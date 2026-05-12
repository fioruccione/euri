"""
Interfaccia con Ollama.
Interviene per: generazione risposte naturali, comprensione complessa, disambiguazione.
"""
import re
import time
import threading
from loguru import logger
import ollama
import config
from utils.date_utils import now


class Brain:
    def __init__(self):
        self._conversation_history: list[dict] = []
        self._max_history = 10  # ultimi 10 scambi in contesto
        self._next_trusted = False  # impostato da voice_daemon prima di respond()
        self._episodes: list[str] = []       # episodi compressi della sessione corrente
        self._compress_lock = threading.Lock()
        self.history_lock = threading.Lock()  # protegge _conversation_history da accessi concorrenti
        self._episode_callback = None        # fn(summary: str) → salva in Redis; iniettata da voice_daemon

    @staticmethod
    def _clean(text: str) -> str:
        """Rimuove il reasoning interno (Gemma 4 / Qwen 3) dal content del modello."""
        if not text:
            return ""
        # Gemma 4 usa <channel|> come separatore thinking→risposta
        if "<channel|>" in text:
            text = text.split("<channel|>", 1)[-1]
        # Formato alternativo <think>...</think>
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        return text.strip()

    def respond(self, user_text: str, context: str = "") -> str:
        """
        Genera una risposta per voce: breve, diretta, italiana.
        context: informazioni aggiuntive da iniettare (es. risultati ricerca Redis).
        """
        messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]

        dt_line = f"Data e ora corrente: {now().strftime('%A %d %B %Y, ore %H:%M')}"
        ctx_parts = [dt_line]
        if context:
            ctx_parts.append(context)
        messages.append({
            "role": "system",
            "content": "Contesto disponibile:\n" + "\n".join(ctx_parts)
        })

        # Episodi compressi della sessione corrente (max EPISODE_MAX_INJECT)
        if self._episodes:
            ep_text = "\n\n".join(
                f"[Episodio {i+1}] {ep}"
                for i, ep in enumerate(self._episodes[-config.EPISODE_MAX_INJECT:])
            )
            messages.append({"role": "system", "content": f"Episodi conversazione corrente:\n{ep_text}"})

        # Aggiungi storico recente sotto lock — evita race con _compress_episode
        with self.history_lock:
            messages.extend(
                {"role": m["role"], "content": m["content"]}
                for m in self._conversation_history[-self._max_history:]
            )
        messages.append({"role": "user", "content": user_text})

        try:
            _t = time.perf_counter()
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=messages,
                # num_predict alto: Gemma 4 consuma token di thinking prima della risposta;
                # il modello si ferma da solo quando finisce — il cap è solo sicurezza.
                options={"temperature": 0.7, "num_predict": 1500},
                think=False,
            )
            logger.info(f"[TIMING] brain.respond() Ollama: {(time.perf_counter()-_t)*1000:.0f}ms")
            reply = self._clean(response.message.content or "")

            # Aggiorna storico (trusted=True → analizzabile dal passive learner)
            with self.history_lock:
                self._conversation_history.append({"role": "user", "content": user_text, "trusted": self._next_trusted})
                self._conversation_history.append({"role": "assistant", "content": reply, "trusted": self._next_trusted})
                trigger_compress = len(self._conversation_history) >= config.EPISODE_COMPRESSION_THRESHOLD
            self._next_trusted = False  # reset — il prossimo scambio è non-trusted di default

            # Compressione episodica in background se la history è abbastanza lunga
            if trigger_compress:
                threading.Thread(target=self._compress_episode, daemon=True).start()

            return reply

        except Exception as e:
            logger.error(f"Errore Ollama: {e}")
            return "Scusa, ho avuto un problema. Riprova."

    def inject_tool_result(self, user_text: str, result_text: str):
        """Inietta uno scambio tool nella history LLM — visibile ai turn CHAT successivi."""
        with self.history_lock:
            self._conversation_history.append({"role": "user", "content": user_text, "trusted": False})
            self._conversation_history.append({"role": "assistant", "content": result_text, "trusted": False})

    def _compress_episode(self):
        """Comprime i messaggi più vecchi in un episodio — gira in background."""
        with self._compress_lock:
            if len(self._conversation_history) < config.EPISODE_COMPRESSION_THRESHOLD:
                return  # un altro thread ha già compresso
            chunk = self._conversation_history[:config.EPISODE_COMPRESSION_CHUNK]
            lines = []
            for m in chunk:
                role = "Stefano" if m["role"] == "user" else "Euri"
                lines.append(f"{role}: {m['content']}")
            dialogue = "\n".join(lines)
            prompt = (
                "Riassumi questa conversazione in modo conciso ma preciso. "
                "Preserva: nomi propri, numeri, nomi di progetto, decisioni, fatti tecnici. "
                "Scrivi in terza persona. Max 120 parole.\n\n"
                f"{dialogue}\n\nRiassunto:"
            )
            try:
                response = ollama.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.1, "num_predict": 250},
                    think=False,
                )
                summary = self._clean(response.message.content or "")
                if not summary:
                    return
                with self.history_lock:
                    self._conversation_history = self._conversation_history[config.EPISODE_COMPRESSION_CHUNK:]
                self._episodes.append(summary)
                logger.info(f"Episodic compression: {config.EPISODE_COMPRESSION_CHUNK} messaggi → episodio #{len(self._episodes)}")
                if self._episode_callback:
                    self._episode_callback(summary)
            except Exception as e:
                logger.error(f"Episodic compression errore: {e}")

    def confirm_save(self, item_type: str, content: str, due_at_str: str = "") -> str:
        """
        Genera conferma breve per salvataggio (senza passare da Qwen se possibile).
        Risparmia latenza per azioni semplici.
        """
        if item_type == "memory":
            return f"Segnato: {content}"
        elif item_type == "todo":
            content_clean = content.rstrip(".")
            if due_at_str:
                return f"Promemoria: {content_clean}. Scadenza: {due_at_str}."
            return f"Todo salvato: {content_clean}."
        elif item_type == "note":
            return f"Appunto salvato."
        return "Salvato."

    def format_search_results(self, results: list[dict], query: str) -> str:
        """
        Formatta i risultati di ricerca per la risposta vocale.
        Usa LLM solo se ci sono più risultati da sintetizzare.
        """
        if not results:
            return f"Niente in memoria su '{query}'."

        if len(results) == 1:
            return f"Trovato: {results[0]['content']}"

        # Costruisce un riassunto con Qwen
        items_text = "\n".join(f"- {r['content']}" for r in results[:5])
        prompt = (
            f"Stefano ha chiesto: '{query}'\n"
            f"Ho trovato questi ricordi:\n{items_text}\n\n"
            f"Rispondi in modo breve e diretto, come se fossi un collega."
        )
        return self.respond(prompt)

    def format_today_summary(self, todos: list[dict], overdue: list[dict]) -> str:
        """Genera riepilogo mattutino vocale."""
        if not todos and not overdue:
            return "Agenda libera oggi. Niente di programmato."

        lines = []
        if overdue:
            lines.append(f"{len(overdue)} {'cosa scaduta' if len(overdue) == 1 else 'cose scadute'}.")
        if todos:
            lines.append(f"Oggi hai {len(todos)} {'impegno' if len(todos) == 1 else 'impegni'}.")
            for t in todos[:3]:  # max 3 per voce
                due = t.get("_due_at")
                time_str = due.strftime("%H:%M") if due else "senza orario"
                lines.append(f"{time_str}: {t['content']}.")

        return " ".join(lines)

    def generate_status(self, n_todos: int, n_overdue: int, n_memories: int) -> str:
        parts = []
        if n_overdue:
            parts.append(f"{n_overdue} scadut{'o' if n_overdue == 1 else 'i'}")
        if n_todos:
            parts.append(f"{n_todos} todo pendenti")
        parts.append(f"{n_memories} ricordi in memoria")
        return "Stato: " + ", ".join(parts) + "."

    def ask_to_save(self, text: str) -> str:
        """Chiede se salvare qualcosa che suona importante."""
        return "Lo segno o era solo un pensiero ad alta voce?"

    def complete_todo_response(self, content: str) -> str:
        return f"Fatto. '{content}' segnato come completato."

    def probe_question(self, topic: str, accumulated: str, asked_questions: list[str] = None) -> str:
        """Genera una domanda di approfondimento durante la modalità insegnamento."""
        asked_str = ""
        if asked_questions:
            asked_str = "\nDomande già fatte — NON ripetere questi argomenti né varianti simili:\n" + "\n".join(
                f"- {q}" for q in asked_questions
            ) + "\n"
        prompt = (
            f"Stefano ti sta spiegando qualcosa su: {topic}\n"
            f"Quello che ha detto finora:\n{accumulated}\n"
            f"{asked_str}\n"
            f"Fai UNA sola domanda su un aspetto che NON è ancora stato toccato. "
            f"Se non hai più nulla di nuovo da chiedere, rispondi solo con: 'Ho capito tutto, dimmi quando vuoi fermarti.' "
            f"Sii diretto. Max 1 frase."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.7, "num_predict": 500},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore probe_question: {e}")
            return "Dimmi di più."

    def extract_passive_memories(self, conversation: list[dict]) -> list[str]:
        """
        Analizza la conversazione recente ed estrae fatti su Stefano da salvare passivamente.
        Ritorna una lista di fatti (stringhe), vuota se nulla di rilevante.
        """
        if len(conversation) < 4:
            return []

        lines = []
        for msg in conversation:
            role = "Stefano" if msg["role"] == "user" else "Euri"
            lines.append(f"{role}: {msg['content']}")
        dialogue = "\n".join(lines)

        prompt = (
            f"Analizza questa conversazione tra Stefano e il suo assistente.\n\n"
            f"{dialogue}\n\n"
            f"Estrai SOLO i fatti concreti su Stefano che vale la pena ricordare per il futuro:\n"
            f"- Preferenze personali (cibi, orari, abitudini, strumenti che usa)\n"
            f"- Progetti in corso o decisioni prese\n"
            f"- Dati su lavoro, fornitori, clienti, processi, risultati tecnici\n"
            f"- Opinioni forti o posizioni chiare su argomenti specifici\n"
            f"- Scadenze e impegni temporali concreti: materiali attesi, prove pianificate, "
            f"consegne, appuntamenti con fornitori o clienti. Includi sempre la data esatta o "
            f"approssimativa menzionata. Esempio: 'Dopo il 28 maggio il materiale X sarà "
            f"disponibile in azienda per la prova Y.'\n"
            f"- Relazioni causali e strategiche: dipendenze tra domini diversi, piani condizionali "
            f"('se X allora Y'), connessioni concrete tra risultati tecnici, vendite, investimenti, "
            f"decisioni hardware/software. Esempio: 'Se la vendita dei neutri va a buon fine, "
            f"Stefano userà i proventi per aggiornare la GPU della workstation.'\n\n"
            f"IGNORA: conversazione generica, saluti, test del sistema, domande senza risposta concreta, "
            f"informazioni già ovvie (es. 'Stefano usa Euri').\n\n"
            f"Se trovi fatti utili: scrivi una lista numerata, un fatto per riga, max 6.\n"
            f"Se non c'è nulla di concreto da salvare: scrivi solo NOTHING."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1, "num_predict": 2000},
                think=True,
            )
            result = self._clean(response.message.content or "")
            if not result or "NOTHING" in result.upper():
                return []
            # Parsa la lista numerata — estrae il testo dopo "1. ", "2. " ecc.
            import re
            facts = re.findall(r"^\d+\.\s*(.+)$", result, re.MULTILINE)
            return [f.strip() for f in facts if len(f.strip()) > 10]
        except Exception as e:
            logger.error(f"Errore extract_passive_memories: {e}")
            return []

    _REFLECTION_SYSTEM = (
        "Sei un sistema di consolidamento memoria. Leggi le memorie recenti di Stefano "
        "e quelle correlate dal suo archivio, e produci una sintesi breve che identifichi:\n"
        "1. Il tema dominante della sessione\n"
        "2. Eventuali connessioni con attività passate di Stefano\n"
        "3. Un possibile interesse a breve termine che potrebbe emergere\n\n"
        "Regole:\n"
        "- Massimo 3 frasi totali\n"
        "- Tono funzionale, non emotivo\n"
        "- Terza persona su Stefano\n"
        "- Nessun preambolo ('Ecco la sintesi:', ecc.)\n"
        "- Se le memorie sono troppo scollegate, rispondi esattamente: NO_COHERENT_PATTERN"
    )

    def generate_reflection(self, session_memories: list[dict], related_memories: list[dict]) -> str | None:
        """Loop 2a: sintesi silenziosa di memorie recenti + correlate. Ritorna None se no pattern."""
        def _fmt(mems: list[dict]) -> str:
            return "\n".join(
                f"[{m.get('source','?')}] {(m.get('content') or '')[:120]}"
                for m in mems
            )

        user_msg = (
            f"Memorie recenti della sessione:\n{_fmt(session_memories)}\n\n"
            f"Memorie correlate dall'archivio:\n{_fmt(related_memories)}"
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": self._REFLECTION_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                options={"temperature": 0.4, "num_predict": 1000},
                think=True,
            )
            result = self._clean(response.message.content or "")
            if not result or "NO_COHERENT_PATTERN" in result:
                return None
            return result
        except Exception as e:
            logger.error(f"Errore generate_reflection: {e}")
            return None

    def summarize_knowledge(self, accumulated: str) -> str:
        """Sintetizza il contenuto di una sessione di insegnamento per salvarlo in memoria."""
        prompt = (
            f"Stefano ti ha spiegato questo:\n{accumulated}\n\n"
            f"Riassumi in modo chiaro e completo, mantenendo tutti i dettagli tecnici importanti. "
            f"Scrivi come se dovessi spiegarlo a qualcuno in futuro. Max 5 frasi."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 2000},
                think=True,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore summarize_knowledge: {e}")
            return accumulated

    def evaluate_memory_relevance(self, content: str) -> str:
        """
        Death-row gate: valuta se una memoria in scadenza vale ancora la pena conservare.
        Ritorna 'KEEP' o 'DROP'.
        Chiamato solo per memorie passive/reflection mai richiamate vicine alla scadenza.
        """
        prompt = (
            f"Sei il sistema di gestione memoria di Euri, l'assistente personale di Stefano.\n"
            f"Questa memoria sta per scadere perché non è mai stata richiamata in conversazione:\n\n"
            f"\"{content}\"\n\n"
            f"Vale la pena conservarla? Potrebbe essere stagionale, tecnica, o utile in futuro?\n"
            f"Rispondi SOLO con KEEP o DROP."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 10},
                think=False,
            )
            text = self._clean(response.message.content or "").strip().upper()
            return "KEEP" if text.startswith("KEEP") else "DROP"
        except Exception as e:
            logger.debug(f"Errore evaluate_memory_relevance: {e}")
            return "KEEP"  # In caso di errore, conserva per sicurezza

    def probe_same_meaning(self, question: str) -> str:
        """Probe leggero: risponde SI o NO. Usato per dedup semantico."""
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": question}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore probe_same_meaning: {e}")
            return "NO"

    def decide_tool_call(self, user_text: str, tools_description: str) -> str:
        """
        Dato il testo utente e la lista dei tool disponibili,
        ritorna un JSON con {"tool": "nome", "params": {...}} oppure {"tool": null, "params": {}}.
        Chiamata leggera: temperature=0, num_predict=80.
        """
        prompt = (
            f"Sei un dispatcher che sceglie quale tool usare.\n\n"
            f"TOOL DISPONIBILI:\n{tools_description}\n\n"
            f"REGOLE:\n"
            f"1. Rispondi SOLO con un JSON valido, nient'altro.\n"
            f"2. Formato: {{\"tool\": \"nome_tool\", \"params\": {{\"chiave\": \"valore\"}}}}\n"
            f"3. Se nessun tool è appropriato: {{\"tool\": null, \"params\": {{}}}}\n"
            f"4. Non spiegare, non commentare. Solo JSON.\n\n"
            f"Richiesta utente: \"{user_text}\""
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 700},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore decide_tool_call: {e}")
            return '{"tool": null, "params": {}}'

    def translate(self, text: str, target_lang: str) -> str:
        """Traduce il testo nella lingua target. Risponde solo con la traduzione."""
        prompt = (
            f"Traduci il seguente testo in {target_lang}. "
            f"Rispondi SOLO con la traduzione, nessuna spiegazione, nessun commento.\n\n"
            f"{text}"
        )
        try:
            _t = time.perf_counter()
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
            )
            logger.info(f"[TIMING] brain.translate() Ollama: {(time.perf_counter()-_t)*1000:.0f}ms")
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore traduzione: {e}")
            return text

    _VAGUE_QUERY = re.compile(
        r'^(qualcosa\s+di\s+simil[ei]|simil[ei]|altro|approfondisci|approfondiscilo'
        r'|cerca\s+altro|cerca\s+di\s+più|dimmi\s+di\s+più|altro\s+su\s+questo'
        r'|e\s+su\s+questo|come\s+sopra|lo\s+stesso)\b',
        re.IGNORECASE
    )

    def extract_search_query(self, text: str) -> str:
        """
        Estrae la query di ricerca pulita da una frase vocale.
        Se la richiesta è vaga, usa la conversation history per costruire una query contestuale.
        """
        # Rimuovi trigger vocali per vedere cosa resta
        trigger_re = re.compile(
            r'\b(cercami\s+(online|sul\s+web|su\s+internet|in\s+rete)'
            r'|cerca\s+(online|sul\s+web|su\s+internet|in\s+rete)'
            r'|prova\s+a\s+(cercare|guardare)\s+(nel|sul|in)\s+web'
            r'|guarda\s+(online|nel\s+web|sul\s+web)'
            r'|vai\s+online\s+e\s+cerca'
            r'|cosa\s+dice\s+il\s+web\s+(su|di)'
            r'|ricerca\s+(nel|sul)\s+web)\b',
            re.IGNORECASE
        )
        stripped = trigger_re.sub('', text).strip(' ,.-')

        # Se la query è vaga o troppo corta, usa il contesto della conversazione
        is_vague = len(stripped.split()) < 4 or self._VAGUE_QUERY.match(stripped)

        if is_vague and self._conversation_history:
            # Prendi gli ultimi messaggi dell'utente (non le risposte Euri — evita contaminazione)
            user_turns = [m for m in self._conversation_history if m["role"] == "user"][-4:]
            history_str = "\n".join(f"Stefano: {m['content'][:200]}" for m in user_turns)
            prompt = (
                f"Conversazione recente:\n{history_str}\n\n"
                f"Stefano vuole cercare sul web: '{text}'\n"
                f"La richiesta è vaga. Basandoti sul contesto della conversazione, "
                f"formula una query di ricerca web specifica ed efficace. "
                f"Rispondi SOLO con le parole chiave, max 8 parole. Nient'altro."
            )
            logger.debug(f"Query vaga '{stripped}' — uso contesto conversazione")
        else:
            # Includi contesto utente anche nel path non-vago — aiuta a correggere misrecognition STT
            user_turns = [m for m in self._conversation_history if m["role"] == "user"][-3:]
            context_hint = ""
            if user_turns:
                context_hint = (
                    f"Contesto conversazione recente:\n"
                    + "\n".join(f"- {m['content'][:120]}" for m in user_turns)
                    + "\n\n"
                )
            prompt = (
                f"{context_hint}"
                f"L'utente ha detto: '{text}'\n"
                f"Estrai le parole chiave per una ricerca web efficace. "
                f"Rimuovi i trigger ('cercami online', 'guarda nel web' ecc.) e i filler vocali. "
                f"Se una parola sembra un errore di pronuncia o trascrizione, correggila usando il contesto. "
                f"Mantieni termini specifici: nomi di luoghi, nomi propri, termini tecnici. "
                f"Se si parla di meteo/tempo atmosferico, usa sempre 'meteo' o 'previsioni meteo' nella query. "
                f"Rispondi SOLO con le parole chiave, max 6 parole. Nient'altro."
            )

        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 400},
                think=False,
            )
            return self._clean(response.message.content or "").strip('"').strip("'")
        except Exception:
            return stripped or text

    def extract_query_fallback(self, original_query: str) -> str:
        """
        Genera una query alternativa più tecnica/specifica se la prima ha dato risultati scarsi.
        Usa il contesto della conversazione per disambiguare.
        """
        if not self._conversation_history:
            return original_query

        user_turns = [m for m in self._conversation_history if m["role"] == "user"][-4:]
        history_str = "\n".join(f"Stefano: {m['content'][:150]}" for m in user_turns)
        prompt = (
            f"Conversazione recente:\n{history_str}\n\n"
            f"Ho cercato sul web: '{original_query}' ma i risultati erano scarsi o irrilevanti.\n"
            f"Basandoti sul contesto tecnico della conversazione, genera UNA query alternativa "
            f"più specifica e tecnica. Preferisci termini inglesi se l'argomento è tecnico/software. "
            f"Rispondi SOLO con le parole chiave, max 8 parole. Nient'altro."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 400},
                think=False,
            )
            fallback = self._clean(response.message.content or "").strip('"').strip("'")
            logger.debug(f"Query fallback: '{original_query}' → '{fallback}'")
            return fallback if fallback else original_query
        except Exception:
            return original_query

    def summarize_web_results(self, results: list[dict], query: str) -> str:
        """Sintetizza i risultati web in una risposta vocale breve, estraendo i dati."""
        if not results:
            return f"Non ho trovato niente di utile su '{query}'."

        # I risultati arrivano già ordinati per lunghezza body (pagine complete prima)
        # Usa fino a 2 pagine complete + snippet degli altri
        context = ""
        full_pages = [r for r in results if len(r.get("body", "")) > 500][:2]
        snippets   = [r for r in results if len(r.get("body", "")) <= 500]

        for i, r in enumerate(full_pages):
            context += f"FONTE {i+1} ({r['title']}):\n{r['body'][:3000]}\n\n"

        if snippets:
            context += "ALTRI RISULTATI:\n"
            for r in snippets[:3]:
                context += f"- {r['title']}: {r['body'][:200]}\n"

        prompt = (
            f"Stefano ha cercato sul web: '{query}'\n\n"
            f"Ecco i contenuti scaricati:\n{context}\n"
            f"REGOLE DI RISPOSTA:\n"
            f"1. ESTRAI I DATI: Se Stefano chiede un elenco (es. titoli di film, prezzi, nomi), LEGGILI DIRETTAMENTE. Non dire 'Il sito elenca i film', dimmi tu i titoli.\n"
            f"2. Adatta il tono: se cerca dati tecnici o di lavoro, usa numeri e precisione. Se cerca cronaca o svago, vai ai fatti salienti.\n"
            f"3. Sii discorsivo e diretto. Parla per essere letto a voce (Text-to-Speech).\n"
            f"4. ZERO formattazione (niente markdown o asterischi). Zero premesse ('Ho trovato un sito che...'). Vai dritto alla risposta.\n"
            f"5. TEMPERATURE: converti sempre in Celsius. Se trovi Fahrenheit, converti: (F - 32) × 5/9. Non dire mai gradi Fahrenheit."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 1500},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore summarize_web_results: {e}")
            return results[0]["body"][:300] if results else "Nessun risultato."

    def parse_completion_target(self, text: str) -> str:
        """
        Usa LLM per estrarre cosa si è completato da frasi ambigue.
        Es: "ho fatto quella cosa del fornitore" → "fornitore"
        """
        prompt = (
            f"L'utente ha detto: '{text}'\n"
            f"Estrai in 2-5 parole cosa ha completato/fatto. Solo le parole chiave, nient'altro."
        )
        try:
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception:
            return text

    def generate_code(self, task: str, available_files: list[str],
                      input_dir: str, output_dir: str) -> str:
        """
        Chiede a Gemma di generare uno script Python per manipolare file.
        Restituisce il codice puro (senza markdown fences).
        """
        files_list = "\n".join(f"  - {f}" for f in available_files) if available_files else "  (nessun file)"

        prompt = (
            f"Sei un programmatore Python esperto. Genera uno script Python che esegua il task richiesto.\n\n"
            f"REGOLE TASSATIVE:\n"
            f"1. Scrivi SOLO codice Python puro. Nessun commento, nessuna spiegazione, nessun markdown.\n"
            f"2. I file di input sono in: {input_dir}\n"
            f"3. Salva i file di output in: {output_dir}\n"
            f"4. Usa print() per comunicare i risultati (verranno letti a voce all'utente).\n"
            f"5. NON usare input(), GUI, subprocess, o librerie di rete.\n"
            f"6. Librerie disponibili: pandas, numpy, json, csv, pathlib, PIL, matplotlib, PyPDF2, openpyxl, math, re, collections, statistics, odfpy.\n"
            f"7. Gestisci le eccezioni con try/except e stampa messaggi chiari in italiano.\n"
            f"8. Se crei grafici con matplotlib usa plt.savefig() nella cartella output, NON plt.show().\n"
            f"9. Stampa un breve riassunto dei risultati alla fine.\n\n"
            f"FILE DISPONIBILI IN INPUT:\n{files_list}\n\n"
            f"TASK: {task}"
        )
        try:
            _t = time.perf_counter()
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 2000},
                think=False,
            )
            elapsed = (time.perf_counter() - _t) * 1000
            logger.info(f"[TIMING] brain.generate_code() Ollama: {elapsed:.0f}ms")

            raw = self._clean(response.message.content or "")

            # Rimuovi markdown fences se Gemma le ha messe
            if "```python" in raw:
                raw = raw.split("```python", 1)[1]
                if "```" in raw:
                    raw = raw.rsplit("```", 1)[0]
            elif "```" in raw:
                parts = raw.split("```")
                if len(parts) >= 3:
                    raw = parts[1]
                elif len(parts) == 2:
                    raw = parts[1]

            return raw.strip()

        except Exception as e:
            logger.error(f"Errore generate_code: {e}")
            return ""

    def analyze_image(self, image_path: str, question: str = "") -> str:
        """
        Usa Gemma 4 vision per analizzare un'immagine.
        Passa l'immagine direttamente a Ollama tramite il parametro images.
        """
        if not question:
            question = (
                "Descrivi questa immagine in italiano in modo dettagliato. "
                "La descrizione verrà letta a voce, quindi usa frasi complete e naturali. "
                "Non usare elenchi puntati o formattazione markdown."
            )

        try:
            _t = time.perf_counter()
            response = ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{
                    "role": "user",
                    "content": question,
                    "images": [image_path],
                }],
                options={"temperature": 0.3, "num_predict": 500},
                think=False,
            )
            elapsed = (time.perf_counter() - _t) * 1000
            logger.info(f"[TIMING] brain.analyze_image() Ollama: {elapsed:.0f}ms")
            return self._clean(response.message.content or "Impossibile analizzare l'immagine.")
        except Exception as e:
            logger.error(f"Errore analyze_image: {e}")
            return "Non sono riuscito ad analizzare l'immagine."
