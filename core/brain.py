"""
Interfaccia con Ollama.
Interviene per: generazione risposte naturali, comprensione complessa, disambiguazione.
"""
import re
import json
import time
import threading
import uuid
from collections import deque
from loguru import logger
import ollama
from core.ollama_client import chat_client
from core.operational_context import load_operational_context
import config
from utils.date_utils import now, format_datetime_full, from_timestamp
from core.memory_scope import current_scope, normalize_scope


_OWNER_NAME = config.OWNER_DISPLAY_NAME
_ASSISTANT_NAME = config.ASSISTANT_DISPLAY_NAME
_PASSIVE_EXTRACTION_WINDOW_MESSAGES = 12
_PASSIVE_EXTRACTION_OVERLAP_MESSAGES = 4


class Brain:
    def __init__(self):
        self._conversation_history: list[dict] = []
        self._max_history = 10  # ultimi 10 scambi in contesto
        self._history_seq = 0
        self._conversation_id = str(uuid.uuid4())
        self._history_segment_id = 1
        self._last_user_observed_at: float | None = None
        # Coda indipendente dalla history comprimibile: il passive learner usa
        # sequence ID stabili e fa ack dopo l'estrazione.
        self._passive_journal: deque[dict] = deque(maxlen=2048)
        self._episodes: list[dict] = []      # episodi compressi con confini temporali
        self._compress_lock = threading.Lock()
        self.history_lock = threading.Lock()  # protegge _conversation_history da accessi concorrenti
        self._episode_callback = None        # fn(summary, temporal_context) -> salva in Redis
        self._turn_callback = None           # fn(message) -> archivia il turno originale

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
        # Le etichette temporali appartengono al prompt interno. Il modello non
        # dovrebbe copiarle, ma l'output vocale ha bisogno anche di un confine
        # deterministico nel caso in cui imiti il formato dello storico.
        text = re.sub(
            r"^\s*(?:\[\s*tempo interno\s*:[^\]\r\n]*\]\s*)+",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip()

    def _append_history_locked(
        self,
        role: str,
        content: str,
        trusted: bool,
        *,
        observed_at: float | None = None,
        memory_scope: str | None = None,
    ) -> None:
        """Aggiunge un messaggio alla history LLM e al journal passivo."""
        at = time.time() if observed_at is None else float(observed_at)
        memory_scope = normalize_scope(memory_scope or current_scope())
        if role == "user":
            gap_s = getattr(config, "TEMPORAL_EPISODE_GAP_SECONDS", 30 * 60)
            if (
                self._last_user_observed_at is not None
                and at - self._last_user_observed_at > gap_s
            ):
                self._history_segment_id += 1
            self._last_user_observed_at = at
        self._history_seq += 1
        from core.conversation_turns import make_turn_ref
        turn_ref = make_turn_ref(self._conversation_id, self._history_seq)
        message = {
            "seq": self._history_seq,
            "turn_ref": turn_ref,
            "role": role,
            "content": content,
            "trusted": bool(trusted),
            "observed_at": at,
            "conversation_id": self._conversation_id,
            "segment_id": self._history_segment_id,
            "memory_scope": memory_scope,
        }
        self._conversation_history.append(message)
        self._passive_journal.append(dict(message))
        if self._turn_callback:
            try:
                self._turn_callback(dict(message))
            except Exception as exc:
                # Il journal conserva il turno e il passive learner ritenterà la
                # persistenza prima di pubblicare qualsiasi memoria che lo citi.
                logger.error(
                    "Archivio turni: scrittura immediata fallita per {} ({})",
                    turn_ref,
                    exc,
                )

    def passive_messages_after(self, last_seq: int) -> list[dict]:
        """Snapshot dei messaggi non ancora processati, immune alla compressione."""
        with self.history_lock:
            if self._passive_journal and last_seq < self._passive_journal[0]["seq"] - 1:
                logger.warning(
                    "Passive journal: gap rilevato dopo seq {} (primo disponibile {})",
                    last_seq,
                    self._passive_journal[0]["seq"],
                )
            return [dict(m) for m in self._passive_journal if m["seq"] > last_seq]

    def ack_passive_messages(self, through_seq: int) -> None:
        """Rimuove dal journal solo messaggi già analizzati dal passive learner."""
        with self.history_lock:
            while self._passive_journal and self._passive_journal[0]["seq"] <= through_seq:
                self._passive_journal.popleft()

    def respond(
        self,
        user_text: str,
        context: str = "",
        *,
        trusted: bool = False,
        observed_at: float | None = None,
        thinking: bool = False,
        thinking_reason: str = "",
        memory_scope: str | None = None,
    ) -> str:
        """
        Genera una risposta per voce: breve, diretta, italiana.
        context: informazioni aggiuntive da iniettare (es. risultati ricerca Redis).
        """
        from core.temporal_context import (
            temporal_prompt_contract,
            turn_time_label,
        )

        user_observed_at = time.time() if observed_at is None else float(observed_at)
        memory_scope = normalize_scope(memory_scope or current_scope())
        messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
        if memory_scope.startswith("experiment_"):
            label = memory_scope.removeprefix("experiment_").replace("_", " ")
            messages.append({
                "role": "system",
                "content": (
                    "SESSIONE SPERIMENTALE ISOLATA "
                    f"«{label}». Tutti i dati di questa sessione sono materiale "
                    "di prova, non fatti della vita reale dell'utente. Ragiona "
                    "coerentemente dentro lo scenario, ma non presentarli come "
                    "memoria personale e non mescolarli con altri progetti."
                ),
            })

        # Contesto operativo opzionale (EURI_CONTEXT.md): cornice del mondo in cui Euri opera.
        # Fail-open: "" se il file manca → prompt identico a prima.
        op_ctx = load_operational_context()
        if op_ctx:
            messages.append({"role": "system", "content": op_ctx})

        dt_line = f"Data e ora corrente: {format_datetime_full(now())}"
        ctx_parts = [dt_line]
        if context:
            ctx_parts.append(context)
        messages.append({
            "role": "system",
            "content": "Contesto disponibile:\n" + "\n".join(ctx_parts)
        })

        # Episodi compressi della sessione corrente (max EPISODE_MAX_INJECT)
        scoped_episodes = [
            episode for episode in self._episodes
            if normalize_scope(episode.get("memory_scope")) == memory_scope
        ]
        if scoped_episodes:
            ep_text = "\n\n".join(
                f"[Episodio {i+1} | "
                f"da {turn_time_label(ep.get('started_at'), user_observed_at)} "
                f"a {turn_time_label(ep.get('ended_at'), user_observed_at)}] "
                f"{ep.get('summary', '')}"
                for i, ep in enumerate(scoped_episodes[-config.EPISODE_MAX_INJECT:])
            )
            messages.append({"role": "system", "content": f"Episodi conversazione corrente:\n{ep_text}"})

        # Aggiungi storico recente sotto lock — evita race con _compress_episode
        with self.history_lock:
            history = [
                message for message in self._conversation_history
                if normalize_scope(message.get("memory_scope")) == memory_scope
            ][-self._max_history:]
        if history:
            timeline = []
            for index, message in enumerate(history, start=1):
                role = _OWNER_NAME if message["role"] == "user" else _ASSISTANT_NAME
                segment = message.get("segment_id")
                segment_text = f"; segmento {segment}" if segment is not None else ""
                timeline.append(
                    f"Turno storico {index}: {role}; "
                    f"{turn_time_label(message.get('observed_at'), user_observed_at)}"
                    f"{segment_text}"
                )
            messages.append({
                "role": "system",
                "content": temporal_prompt_contract()
                + "\nTimeline dei messaggi storici (solo metadati interni):\n"
                + "\n".join(timeline),
            })
            messages.extend(
                {
                    "role": m["role"],
                    "content": m.get("content", ""),
                }
                for m in history
            )
        messages.append({"role": "user", "content": user_text})

        try:
            _t = time.perf_counter()
            actual_thinking = bool(thinking)
            options = {
                "temperature": 0.7,
                "num_predict": (
                    getattr(config, "RAG_DUAL_THINKING_NUM_PREDICT", 2000)
                    if actual_thinking else 1500
                ),
            }
            try:
                response = chat_client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=messages,
                    options=options,
                    think=actual_thinking,
                )
                reply = self._clean(response.message.content or "")
                if actual_thinking and not reply:
                    raise RuntimeError("risposta vuota con thinking selettivo")
            except Exception as thinking_error:
                if not actual_thinking:
                    raise
                logger.warning(
                    "Thinking selettivo fallito ({}): retry diretto fail-safe",
                    thinking_error,
                )
                actual_thinking = False
                response = chat_client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=messages,
                    options={"temperature": 0.7, "num_predict": 1500},
                    think=False,
                )
                reply = self._clean(response.message.content or "")
            logger.info(
                "[TIMING] brain.respond() Ollama: {:.0f}ms | think={} reason={}",
                (time.perf_counter() - _t) * 1000,
                actual_thinking,
                thinking_reason or ("requested" if thinking else "direct"),
            )

            # La provenienza appartiene a QUESTO turno: parametro locale, non
            # side-channel globale condiviso tra voce e mobile.
            with self.history_lock:
                self._append_history_locked(
                    "user",
                    user_text,
                    trusted,
                    observed_at=user_observed_at,
                    memory_scope=memory_scope,
                )
                self._append_history_locked(
                    "assistant",
                    reply,
                    trusted,
                    observed_at=time.time(),
                    memory_scope=memory_scope,
                )
                scoped_count = sum(
                    1 for message in self._conversation_history
                    if normalize_scope(message.get("memory_scope")) == memory_scope
                )
                trigger_compress = scoped_count >= config.EPISODE_COMPRESSION_THRESHOLD

            # Compressione episodica in background se la history è abbastanza lunga
            if trigger_compress:
                threading.Thread(
                    target=self._compress_episode,
                    args=(memory_scope,),
                    daemon=True,
                ).start()

            return reply

        except Exception as e:
            logger.error(f"Errore Ollama: {e}")
            return "Scusa, ho avuto un problema. Riprova."

    def inject_tool_result(self, user_text: str, result_text: str):
        """Inietta uno scambio tool nella history LLM — visibile ai turn CHAT successivi."""
        user_at = time.time()
        with self.history_lock:
            scope = current_scope()
            self._append_history_locked(
                "user",
                user_text,
                False,
                observed_at=user_at,
                memory_scope=scope,
            )
            self._append_history_locked(
                "assistant",
                result_text,
                False,
                observed_at=time.time(),
                memory_scope=scope,
            )

    def _compress_episode(self, memory_scope: str | None = None):
        """Comprime i messaggi più vecchi in un episodio — gira in background."""
        from core.temporal_context import history_line_for_prompt

        memory_scope = normalize_scope(memory_scope or current_scope())
        with self._compress_lock:
            with self.history_lock:
                scoped = [
                    message for message in self._conversation_history
                    if normalize_scope(message.get("memory_scope")) == memory_scope
                ]
                if len(scoped) < config.EPISODE_COMPRESSION_THRESHOLD:
                    return  # un altro thread ha già compresso
                chunk = scoped[:config.EPISODE_COMPRESSION_CHUNK]
            reference_at = time.time()
            lines = [history_line_for_prompt(m, reference_at=reference_at) for m in chunk]
            dialogue = "\n".join(lines)
            prompt = (
                "Comprimi questa conversazione senza fondere le fonti. Produci esattamente "
                "questi tre blocchi, anche quando uno e' vuoto:\n"
                f"DETTO DA {_OWNER_NAME.upper()}: fatti, decisioni, numeri e preferenze "
                f"affermati da {_OWNER_NAME}.\n"
                f"CONTRIBUTI DI {_ASSISTANT_NAME.upper()}: domande, ipotesi, interpretazioni "
                f"o proposte formulate da {_ASSISTANT_NAME}.\n"
                "FILO APERTO: argomenti incompleti e dettagli ancora mancanti.\n"
                "Preserva nomi propri, numeri, progetti e ordine temporale. Non spostare mai "
                f"una frase di {_ASSISTANT_NAME} nel blocco di {_OWNER_NAME}, neppure se "
                "sembra plausibile o non "
                "viene contestata. Non trasformare un tema proposto in un fatto avvenuto. "
                "Scrivi in terza persona. Max 150 parole.\n\n"
                f"{dialogue}\n\nRiassunto:"
            )
            try:
                response = chat_client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.1, "num_predict": 250},
                    think=False,
                )
                summary = self._clean(response.message.content or "")
                if not summary:
                    return
                with self.history_lock:
                    removed = {int(message["seq"]) for message in chunk}
                    self._conversation_history = [
                        message for message in self._conversation_history
                        if int(message.get("seq") or -1) not in removed
                    ]
                started_at = min(
                    (float(m.get("observed_at")) for m in chunk if m.get("observed_at") is not None),
                    default=reference_at,
                )
                ended_at = max(
                    (float(m.get("observed_at")) for m in chunk if m.get("observed_at") is not None),
                    default=reference_at,
                )
                temporal_context = {
                    "schema_version": 1,
                    "asserted_at": ended_at,
                    "event_start": started_at,
                    "event_end": ended_at,
                    "event_precision": "conversation_interval",
                    "conversation_id": self._conversation_id,
                    "segment_id": chunk[-1].get("segment_id") if chunk else None,
                    "source_turn_ids": [m.get("seq") for m in chunk if m.get("seq") is not None],
                    "source_turn_refs": [
                        m.get("turn_ref") for m in chunk if m.get("turn_ref")
                    ],
                    "memory_scope": memory_scope,
                }
                self._episodes.append({
                    "summary": summary,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "memory_scope": memory_scope,
                })
                logger.info(f"Episodic compression: {config.EPISODE_COMPRESSION_CHUNK} messaggi → episodio #{len(self._episodes)}")
                if self._episode_callback:
                    self._episode_callback(summary, temporal_context)
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
            f"{_OWNER_NAME} ha chiesto: '{query}'\n"
            f"Ho trovato questi ricordi:\n{items_text}\n\n"
            f"Rispondi in modo breve e diretto, come se fossi un collega."
        )
        return self.respond(prompt)

    def format_today_summary(self, todos: list[dict], overdue: list[dict]) -> str:
        """Genera riepilogo mattutino vocale. Gli scaduti vanno NOMINATI, non contati:
        un conteggio senza contenuto non è rispondibile né azionabile ("1 cosa scaduta"
        ripetuto per settimane, caso Poseidon 13/07)."""
        if not todos and not overdue:
            return "Agenda libera oggi. Niente di programmato."

        lines = []
        if overdue:
            lines.append(f"{'Un impegno scaduto' if len(overdue) == 1 else str(len(overdue)) + ' impegni scaduti'}:")
            for t in overdue[:3]:  # max 3 per voce
                due = t.get("_due_at")
                age = ""
                if due:
                    days = max(0, (now().date() - due.date()).days)
                    if days == 0:
                        age = ", da oggi"
                    elif days == 1:
                        age = ", da ieri"
                    else:
                        age = f", da {days} giorni"
                lines.append(f"{t['content']}{age}.")
            if len(overdue) > 3:
                lines.append(f"E {len(overdue) - 3} altri.")
            lines.append("Dimmi se li chiudo o li riprogrammo." if len(overdue) > 1
                         else "Dimmi se lo chiudo o lo riprogrammo.")
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
            f"{_OWNER_NAME} ti sta spiegando qualcosa su: {topic}\n"
            f"Quello che ha detto finora:\n{accumulated}\n"
            f"{asked_str}\n"
            f"Fai UNA sola domanda su un aspetto che NON è ancora stato toccato. "
            f"Se non hai più nulla di nuovo da chiedere, rispondi solo con: 'Ho capito tutto, dimmi quando vuoi fermarti.' "
            f"Sii diretto. Max 1 frase."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.7, "num_predict": 500},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore probe_question: {e}")
            return "Dimmi di più."

    def extract_passive_memories(self, conversation: list[dict]) -> list[dict]:
        """
        Estrae fatti autosufficienti e fili conversazionali specifici ancora aperti.
        Ogni risultato conserva supporto, tipo e turni sorgente per la cronologia.
        """
        windows = self._passive_extraction_windows(conversation)
        if not windows:
            return []

        collected: list[dict] = []
        for window_index, window in enumerate(windows, 1):
            collected.extend(
                self._extract_passive_memories_window(
                    window,
                    window_index=window_index,
                    window_count=len(windows),
                )
            )
        merged = self._merge_exact_passive_items(collected)
        logger.info(
            "Passive extractor aggregate: "
            f"windows={len(windows)} candidates={len(collected)} "
            f"exact_collapsed={len(collected) - len(merged)} returned={len(merged)}"
        )
        return merged

    @staticmethod
    def _passive_extraction_windows(conversation: list[dict]) -> list[list[dict]]:
        """Finestre sovrapposte: il dettaglio locale non compete con una sessione intera."""
        if len(conversation) < 2:
            return []
        size = _PASSIVE_EXTRACTION_WINDOW_MESSAGES
        overlap = _PASSIVE_EXTRACTION_OVERLAP_MESSAGES
        if len(conversation) <= size:
            return [list(conversation)]

        windows: list[list[dict]] = []
        start = 0
        while start < len(conversation):
            end = min(start + size, len(conversation))
            window = list(conversation[start:end])
            if len(window) >= 2:
                windows.append(window)
            if end >= len(conversation):
                break
            start = end - overlap
        return windows

    @classmethod
    def _merge_exact_passive_items(cls, items: list[dict]) -> list[dict]:
        """Collassa soltanto identità testuali prodotte dall'overlap, unendo le fonti."""
        merged: list[dict] = []
        positions: dict[tuple[str, str], int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = " ".join(
                re.findall(
                    r"\w+",
                    str(item.get("content") or "").casefold(),
                    re.UNICODE,
                )
            )
            kind = str(item.get("memory_kind") or "semantic_fact")
            key = (kind, normalized)
            if not normalized or key not in positions:
                positions[key] = len(merged)
                merged.append(dict(item))
                continue

            existing = merged[positions[key]]
            source_ids: list[int] = []
            for value in (
                list(existing.get("source_turn_ids") or [])
                + list(item.get("source_turn_ids") or [])
            ):
                try:
                    turn_id = int(value)
                except (TypeError, ValueError):
                    continue
                if turn_id not in source_ids:
                    source_ids.append(turn_id)
            existing["source_turn_ids"] = source_ids
            if item.get("support") == "strong":
                existing["support"] = "strong"
        return merged

    def _extract_passive_memories_window(
        self,
        conversation: list[dict],
        *,
        window_index: int,
        window_count: int,
    ) -> list[dict]:
        """Estrae un inventario atomico da un singolo blocco conversazionale."""
        # Un singolo scambio user/assistant può già contenere un fatto tecnico
        # utile. I filtri di provenienza, soggetto e validazione restano applicati
        # dopo l'estrazione; non richiediamo quindi due scambi per entrare nel
        # percorso passivo.
        if len(conversation) < 2:
            return []

        owner_name = next(
            (
                str(msg.get("speaker"))
                for msg in conversation
                if msg.get("role") == "user" and msg.get("speaker")
            ),
            _OWNER_NAME,
        )
        assistant_name = next(
            (
                str(msg.get("speaker"))
                for msg in conversation
                if msg.get("role") == "assistant" and msg.get("speaker")
            ),
            _ASSISTANT_NAME,
        )
        lines = []
        local_to_source_turn: dict[int, int] = {}
        for index, msg in enumerate(conversation, 1):
            role = str(
                msg.get("speaker")
                or (_OWNER_NAME if msg["role"] == "user" else _ASSISTANT_NAME)
            )
            try:
                source_turn_id = int(msg.get("seq", index))
            except (TypeError, ValueError):
                source_turn_id = index
            local_to_source_turn[index] = source_turn_id
            observed_at = msg.get("observed_at")
            if observed_at is not None:
                try:
                    turn_time = format_datetime_full(from_timestamp(float(observed_at)))
                except Exception:
                    turn_time = "tempo non registrato"
            else:
                turn_time = "tempo non registrato"
            lines.append(f"[T{index} | {turn_time}] {role}: {msg['content']}")
        dialogue = "\n".join(lines)
        from core.memory_scope import is_experimental, normalize_scope
        memory_scope = normalize_scope(conversation[0].get("memory_scope"))
        scope_contract = (
            "Questa è una SESSIONE SPERIMENTALE ISOLATA: estrai i dati dello "
            "scenario perché devono essere richiamabili soltanto nel suo scope; "
            "non reinterpretarli come fatti personali reali.\n"
            if is_experimental(memory_scope)
            else
            "Questa è memoria PERSONALE: non estrarre esempi, ipotesi, giochi di "
            "ruolo, simulazioni, battute o dati che l'utente presenta esplicitamente "
            "come inventati/test. Una frase concreta dentro uno scenario fittizio "
            "non diventa un fatto reale.\n"
        )

        prompt = (
            f"Analizza questa conversazione tra {owner_name} e il suo assistente "
            f"{assistant_name}.\n\n"
            f"{dialogue}\n\n"
            f"{scope_contract}"
            f"Estrai SOLO elementi che vale la pena ricordare per una conversazione futura.\n"
            f"Distingui due tipi:\n"
            f"- FATTO: informazione concreta, decisione, risultato o preferenza riutilizzabile.\n"
            f"- EPISODIO: argomento specifico introdotto o riaperto che resta incompleto. "
            f"Descrivi chi lo ha introdotto, che cosa e' stato realmente detto e quale dettaglio "
            f"manca. Un EPISODIO non prova che l'evento raccontato sia avvenuto.\n"
            f"Ogni fatto deve nominare esplicitamente il soggetto a cui si riferisce "
            f"(persona, azienda, cliente, prodotto, macchina, progetto, materiale...).\n"
            f"Risolvi il soggetto dal contesto conversazionale solo quando è chiaro; NON "
            f"defaultare mai a {owner_name}. Se il soggetto non è risolvibile con certezza, scarta il fatto.\n\n"
            f"Priorità operativa: se le parole di {owner_name} contengono numeri, quantità, "
            f"risultati preliminari, decisioni di prova o piani concreti (per esempio 'preparo 100 kg', "
            f"'i primi 20 pezzi sono dubbi', 'il cliente richiede una finitura opaca'), estrai questi "
            f"elementi anche se {assistant_name} li ha solo commentati o riformulati. Non richiedere che "
            f"il dato sia già misurato: in quel caso usa DEBOLE e conserva l'incertezza nel testo. "
            f"Non restituire NOTHING solo perché la conversazione è breve o il risultato è preliminare.\n\n"
            f"Fonte epistemica:\n"
            f"- FORTE: il fatto è affermato, corretto o ripreso operativamente da {owner_name}.\n"
            f"- DEBOLE: il fatto viene comunque dalle parole di {owner_name}, ma e' incerto, "
            f"provvisorio o espresso come possibilita'.\n"
            f"- SCARTA SEMPRE: fatti, spiegazioni, inferenze o autocorrezioni formulate da {assistant_name}. "
            f"Il silenzio, il cambio di argomento e la mancata contestazione NON sono conferma. "
            f"Una risposta breve come 'si' o 'esatto' non autorizza a copiare la formulazione di "
            f"{assistant_name} in un FATTO passivo: se il dettaglio non compare nelle parole "
            f"di {owner_name}, scartalo.\n\n"
            f"Memorie aggiuntive: se il fatto aggiunge un nuovo asse a un soggetto già noto, "
            f"formulalo come aggiunta, non come definizione esaustiva. Usa parole come 'anche' "
            f"o 'inoltre' quando servono.\n\n"
            f"Copertura e atomicità (questo è il blocco {window_index}/{window_count}):\n"
            f"- Esamina uno per uno TUTTI i turni di {owner_name} nel blocco. Un dettaglio "
            f"breve ma riutilizzabile non è meno importante di un racconto lungo.\n"
            f"- Ogni riga deve avere un solo predicato informativo principale. Non fondere "
            f"progetto, hobby, salute e relazioni in un profilo riassuntivo.\n"
            f"- Una proprietà aggiunta in un turno successivo — genere, materiale, valore, "
            f"stato, destinazione, preferenza — va in una riga autonoma riferita esplicitamente "
            f"all'oggetto. Esempio: 'La sceneggiatura di Giulia è un dramma romantico.'\n"
            f"- Data, quantità e condizione che qualificano lo stesso evento restano invece "
            f"nella sua riga e citano tutti i turni necessari.\n"
            f"- Non omettere una proprietà solo perché hai già estratto l'esistenza "
            f"dell'oggetto o il suo completamento.\n\n"
            f"Esempi:\n"
            f"- OK: 'Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica.'\n"
            f"- OK: '{owner_name} si occupa anche di architetture agentiche e analisi DSC.'\n"
            f"- OK: '{owner_name} lavora da casa in modalità remota.'\n"
            f"- NO: 'Lavora da casa in modalità remota.'\n"
            f"- NO: 'Ha un collega di nome Leonardo.'\n\n"
            f"Categorie utili:\n"
            f"- Preferenze personali (cibi, orari, abitudini, strumenti che usa)\n"
            f"- Progetti in corso o decisioni prese\n"
            f"- Dati su lavoro, fornitori, clienti, processi, risultati tecnici\n"
            f"- Opinioni forti o posizioni chiare su argomenti specifici\n"
            f"- Scadenze e impegni temporali concreti: materiali attesi, prove pianificate, "
            f"consegne, appuntamenti con fornitori o clienti. Includi sempre la data esatta o "
            f"approssimativa menzionata. Esempio: 'Dopo il 28 maggio il materiale X sarà "
            f"disponibile in azienda per la prova Y.'\n"
            f"Per riferimenti relativi come 'ieri', 'venerdì scorso' o 'questa mattina', "
            f"conserva nel contenuto esattamente l'espressione detta da {owner_name}: NON "
            f"calcolare né inventare una data assoluta. La conversione viene eseguita dopo "
            f"da un resolver temporale deterministico.\n"
            f"- Relazioni causali e strategiche: dipendenze tra domini diversi, piani condizionali "
            f"('se X allora Y'), connessioni concrete tra risultati tecnici, vendite, investimenti, "
            f"decisioni hardware/software. Esempio: 'Se la vendita dei neutri va a buon fine, "
            f"{owner_name} userà i proventi per aggiornare la GPU della workstation.'\n\n"
            f"IGNORA: conversazione generica, saluti, test del sistema senza un tema futuro specifico, "
            f"informazioni già ovvie (es. '{owner_name} usa {assistant_name}'), frasi "
            f"acefale senza soggetto esplicito.\n\n"
            f"Se trovi fatti utili: scrivi una lista numerata, un fatto per riga, "
            f"fino a 10 elementi PER QUESTO BLOCCO.\n"
            f"Ogni riga deve avere questo formato esatto:\n"
            f"1. FORTE: [TIPO=FATTO; TURNI=T12,T13] contenuto\n"
            f"oppure: 1. FORTE: [TIPO=EPISODIO; TURNI=T12,T13] contenuto\n"
            f"TURNI usa soltanto gli identificatori LOCALI T1, T2, ... mostrati "
            f"in questo blocco; non rinumerarli rispetto alla sessione completa.\n"
            f"TURNI deve contenere l'unione di TUTTI i turni necessari a sostenere OGNI "
            f"affermazione della riga. Se una riga combina un risultato detto in T12 e una "
            f"proprietà precisata in T15, scrivi TURNI=12,15: citare solo T12 è errato. "
            f"Non citare turni che condividono soltanto l'argomento. Non copiare "
            f"nel contenuto l'orario tecnico tra parentesi: preserva invece, senza convertirli, "
            f"gli eventuali riferimenti temporali detti da {owner_name}. Per TIPO=FATTO, TURNI deve contenere "
            f"ESCLUSIVAMENTE turni di {owner_name}; i turni di {assistant_name} possono "
            f"comparire solo in un "
            f"TIPO=EPISODIO, che descrive il filo del dialogo e non e' una prova fattuale.\n"
            f"Esempio FATTO: 1. FORTE: [TIPO=FATTO; TURNI=4] {owner_name} si occupa anche di architetture agentiche e analisi DSC.\n"
            f"Esempio EPISODIO: 2. FORTE: [TIPO=EPISODIO; TURNI=7,8] {owner_name} ha riaperto il tema della prova IZOD riferita a quella mattina; non ha ancora fornito valori o risultati.\n"
            f"Se non c'è nulla di concreto da salvare: scrivi solo NOTHING."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1, "num_predict": 2000},
                # Estrazione strutturata: il reasoning esteso di Gemma può
                # consumare il budget senza emettere la lista parsabile.
                # I fatti restano comunque sottoposti a provenienza e verifica.
                think=False,
            )
            result = self._clean(response.message.content or "")
            has_nothing = "NOTHING" in result.upper()
            if not result or has_nothing:
                logger.info(
                    f"Passive extractor block {window_index}/{window_count}: "
                    f"raw_chars={len(result)} nothing={has_nothing} "
                    "numbered=0 parsed=0 acephalous=0 provenance_deferred=0 returned=0"
                )
                return []
            # Parsa la lista numerata — estrae il testo dopo "1. ", "2. " ecc.
            import re
            facts = re.findall(r"^\d+\.\s*(.+)$", result, re.MULTILINE)
            parsed = []
            parse_rejected = 0
            acephalous = 0
            provenance_rejected = 0
            for fact in facts:
                item = self._parse_passive_fact_line(fact)
                if not item:
                    parse_rejected += 1
                    continue
                local_turn_ids = list(item.get("source_turn_ids") or [])
                if local_turn_ids:
                    if any(
                        turn_id not in local_to_source_turn
                        for turn_id in local_turn_ids
                    ):
                        item["source_turn_ids"] = []
                        item["provenance_resolution"] = "deferred"
                    else:
                        item["source_turn_ids"] = [
                            local_to_source_turn[turn_id]
                            for turn_id in local_turn_ids
                        ]
                        item = self._with_anaphoric_source_context(item, conversation)
                if len(item["content"]) <= 10 or self._looks_acephalous_fact(item["content"]):
                    acephalous += 1
                    continue
                if not self._passive_item_has_valid_provenance(item, conversation):
                    provenance_rejected += 1
                    item["provenance_resolution"] = "deferred"
                parsed.append(item)
            logger.info(
                f"Passive extractor block {window_index}/{window_count}: "
                f"raw_chars={len(result)} nothing={has_nothing} numbered={len(facts)} "
                f"parsed_rejected={parse_rejected} acephalous={acephalous} "
                f"provenance_deferred={provenance_rejected} returned={len(parsed)}"
            )
            return parsed
        except Exception as e:
            logger.error(
                f"Errore extract_passive_memories block {window_index}/{window_count}: {e}"
            )
            return []

    _PASSIVE_FACT_SUPPORT_RE = re.compile(
        r"^\s*(?:\[(FORTE|DEBOLE|STRONG|WEAK)\]|(FORTE|DEBOLE|STRONG|WEAK))\s*:\s*(.+)$",
        re.IGNORECASE,
    )
    _PASSIVE_FACT_META_RE = re.compile(
        r"^\s*\[TIPO=(FATTO|EPISODIO);\s*"
        r"(?:TURNI|TURNOS)=(T?\s*\d+(?:\s*,\s*T?\s*\d+)*)\]\s*(.+)$",
        re.IGNORECASE,
    )

    @classmethod
    def _parse_passive_fact_line(cls, line: str) -> dict | None:
        """Parsa una riga dell'estrattore passivo preservando il supporto epistemico."""
        text = (line or "").strip()
        if not text:
            return None
        m = cls._PASSIVE_FACT_SUPPORT_RE.match(text)
        if m:
            label = (m.group(1) or m.group(2) or "").strip().lower()
            content = (m.group(3) or "").strip()
            support = "weak" if label in {"debole", "weak"} else "strong"
        else:
            # Compatibilità se il modello dimentica il prefisso: conserva il fatto,
            # ma trattalo come debole perché manca la prova della fonte.
            content = text
            support = "weak"
        if not content:
            return None
        parsed = {"content": content, "support": support}
        meta = cls._PASSIVE_FACT_META_RE.match(content)
        if meta:
            kind = meta.group(1).lower()
            turn_ids = [
                int(match.group(1))
                for match in re.finditer(
                    r"(?:^|,)\s*T?\s*(\d+)",
                    meta.group(2),
                    flags=re.IGNORECASE,
                )
            ]
            parsed["content"] = meta.group(3).strip()
            parsed["memory_kind"] = "episode" if kind == "episodio" else "semantic_fact"
            parsed["source_turn_ids"] = turn_ids
        elif content.lstrip().upper().startswith("[TIPO="):
            return None
        return parsed

    @staticmethod
    def _passive_item_has_valid_provenance(item: dict, conversation: list[dict]) -> bool:
        """Fail-closed sulla provenienza dei derivati passivi.

        Un fatto persistente deve puntare a uno o piu' turni dell'utente e a nessun
        turno dell'assistente. Gli episodi possono citare entrambi i ruoli per
        conservare il filo, ma restano memorie non fattuali a valle.
        """
        requested: list[int] = []
        for value in item.get("source_turn_ids") or []:
            try:
                requested.append(int(value))
            except (TypeError, ValueError):
                return False
        if not requested:
            return False

        by_id: dict[int, dict] = {}
        for index, message in enumerate(conversation, 1):
            try:
                turn_id = int(message.get("seq", index))
            except (TypeError, ValueError):
                turn_id = index
            by_id[turn_id] = message

        selected = [by_id.get(turn_id) for turn_id in requested]
        if any(message is None for message in selected):
            return False
        if not any(message.get("role") == "user" for message in selected):
            return False
        if item.get("memory_kind") in {"episode", "conversation_anchor"}:
            return True
        return all(message.get("role") == "user" for message in selected)

    _ANAPHORIC_SOURCE_RE = re.compile(
        r"(?:^|[.!?]\s+)(?:"
        r"è|era|sono|erano|sarà|saranno|"
        r"lo|la|li|le|quest[oaie]|quell[oaie]|"
        r"it(?:'s|\s+is|\s+was)|they(?:'re|\s+are|\s+were)"
        r")\b",
        re.IGNORECASE,
    )

    @classmethod
    def _with_anaphoric_source_context(
        cls,
        item: dict,
        conversation: list[dict],
    ) -> dict:
        """Completa la lineage quando un turno utente usa un referente anaforico."""
        if item.get("memory_kind") in {"episode", "conversation_anchor"}:
            return item

        source_ids: set[int] = set()
        indexed: list[tuple[int, dict]] = []
        for index, message in enumerate(conversation, 1):
            try:
                turn_id = int(message.get("seq", index))
            except (TypeError, ValueError):
                turn_id = index
            indexed.append((turn_id, message))
        for value in item.get("source_turn_ids") or []:
            try:
                source_ids.add(int(value))
            except (TypeError, ValueError):
                continue

        for position, (turn_id, message) in enumerate(indexed):
            if turn_id not in source_ids or message.get("role") != "user":
                continue
            content = str(message.get("content") or "").strip()
            if not cls._ANAPHORIC_SOURCE_RE.search(content):
                continue
            for previous_turn_id, previous in reversed(indexed[:position]):
                if previous.get("role") == "user":
                    source_ids.add(previous_turn_id)
                    break

        candidate = dict(item)
        candidate["source_turn_ids"] = [
            turn_id for turn_id, _message in indexed if turn_id in source_ids
        ]
        return candidate

    def audit_passive_memory_provenance(
        self,
        item: dict,
        conversation: list[dict],
    ) -> dict | None:
        """Verifica semanticamente e, se necessario, ripara i turni sorgente.

        Questo controllo gira dopo il gate KEEP/JUNK passivo. Non basta che gli
        ID esistano: ogni affermazione riutilizzabile deve essere sostenuta dai
        turni restituiti dall'auditor. Output non parsabile, fonti invalide o una
        clausola senza supporto fanno scartare il candidato.
        """
        if not isinstance(item, dict):
            return None
        candidate = dict(item)
        declared_turn_ids = list(candidate.get("source_turn_ids") or [])
        candidate = self._with_anaphoric_source_context(candidate, conversation)
        content = str(candidate.get("content") or "").strip()
        if not content:
            return None

        original_turn_ids = declared_turn_ids
        kind = candidate.get("memory_kind") or "semantic_fact"
        is_episode = kind in {"episode", "conversation_anchor"}

        lines: list[str] = []
        for index, message in enumerate(conversation, 1):
            try:
                turn_id = int(message.get("seq", index))
            except (TypeError, ValueError):
                turn_id = index
            role = str(message.get("role") or "")
            if not is_episode and role != "user":
                continue
            role_label = "UTENTE" if role == "user" else "ASSISTENTE"
            speaker = str(
                message.get("speaker")
                or (_OWNER_NAME if role == "user" else _ASSISTANT_NAME)
            )
            lines.append(
                f"[T{turn_id} | {role_label} | PARLANTE={speaker}] "
                f"{str(message.get('content') or '').strip()}"
            )
        if not lines:
            return None

        allowed_roles = (
            "Per un FATTO puoi usare esclusivamente turni UTENTE."
            if not is_episode
            else (
                "Per un EPISODIO puoi usare turni UTENTE e ASSISTENTE, ma gli ID "
                "devono sostenere ciò che è realmente accaduto nel dialogo."
            )
        )
        prompt = (
            "Sei l'auditor di provenienza di una memoria conversazionale.\n"
            "Controlla il contenuto proposizione per proposizione: soggetto, fatto, "
            "relazione, proprietà, elemento di lista, stato, evento, numero, data e "
            "qualificazione. Lo stesso tema non è una prova.\n"
            "Il soggetto esplicito può risolvere un 'io' inequivocabile del parlante. "
            "Il campo PARLANTE stabilisce l'identità di quell'io. "
            "Una data assoluta prodotta dal resolver locale può corrispondere a "
            "un'espressione relativa presente nella fonte; non fare tu nuovi calcoli.\n"
            f"{allowed_roles}\n"
            "Se OGNI affermazione è sostenuta, restituisci tutti e soli i TURNI "
            "necessari, anche aggiungendo quelli dimenticati dall'estrattore. "
            "Se anche una sola affermazione non è sostenuta da alcun turno, usa "
            "UNSUPPORTED. Non correggere e non riscrivere la memoria.\n\n"
            f"TIPO: {'EPISODIO' if is_episode else 'FATTO'}\n"
            f"TURNI DICHIARATI: {','.join(str(value) for value in original_turn_ids)}\n"
            f"MEMORIA: {content}\n\n"
            "TURNI DISPONIBILI:\n"
            + "\n".join(lines)
            + "\n\nRispondi soltanto con JSON valido, senza markdown:\n"
            '{"verdict":"SUPPORTED","source_turn_ids":[12,15]}\n'
            "oppure:\n"
            '{"verdict":"UNSUPPORTED","source_turn_ids":[]}'
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 200},
                think=False,
            )
            raw = self._clean(response.message.content or "")
            verdict = self._extract_json(raw)
        except Exception as exc:
            logger.warning(f"Passive provenance audit fallito: {exc}")
            return None

        if str(verdict.get("verdict") or "").strip().upper() != "SUPPORTED":
            logger.info("Passive provenance audit: candidato non sostenuto")
            return None

        audited_turn_ids: list[int] = []
        raw_turn_ids = (
            verdict.get("source_turn_ids")
            if "source_turn_ids" in verdict
            else verdict.get("turn_ids", verdict.get("TURNI"))
        )
        if isinstance(raw_turn_ids, str):
            values = [
                match.group(1)
                for match in re.finditer(
                    r"(?:^|[\s,;])T?\s*(\d+)(?=$|[\s,;])",
                    raw_turn_ids,
                    flags=re.IGNORECASE,
                )
            ]
        elif isinstance(raw_turn_ids, int) and not isinstance(raw_turn_ids, bool):
            values = [raw_turn_ids]
        elif isinstance(raw_turn_ids, (list, tuple)):
            values = list(raw_turn_ids)
        else:
            logger.info(
                "Passive provenance audit: formato TURNI non valido "
                f"({type(raw_turn_ids).__name__})"
            )
            return None
        for value in values:
            if isinstance(value, bool):
                return None
            match = re.fullmatch(r"T?\s*(\d+)", str(value).strip(), re.IGNORECASE)
            if not match:
                logger.info("Passive provenance audit: ID turno non parsabile")
                return None
            turn_id = int(match.group(1))
            if turn_id not in audited_turn_ids:
                audited_turn_ids.append(turn_id)
        if not audited_turn_ids:
            logger.info("Passive provenance audit: nessun TURNI restituito")
            return None

        candidate["source_turn_ids"] = audited_turn_ids
        candidate = self._with_anaphoric_source_context(candidate, conversation)
        audited_turn_ids = list(candidate.get("source_turn_ids") or [])
        if not self._passive_item_has_valid_provenance(candidate, conversation):
            logger.info("Passive provenance audit: TURNI restituiti non validi")
            return None

        repaired = audited_turn_ids != original_turn_ids
        candidate["provenance_audit"] = {
            "schema_version": 1,
            "status": "supported",
            "original_source_turn_ids": original_turn_ids,
            "source_turn_ids": audited_turn_ids,
            "repaired": repaired,
        }
        if repaired:
            logger.info(
                "Passive provenance audit: TURNI riparati "
                f"{original_turn_ids} → {audited_turn_ids}"
            )
        return candidate

    _ACEPHALOUS_FACT_RE = re.compile(
        r"^\s*(?:ha|aveva|avrà|lavora|lavorava|opera|gestisce|gestiva|collabora|"
        r"collaborava|si\s+occupa|supervisiona|sta\s+\w+|deve|vuole|preferisce|"
        r"è\s+(?:arrivat[oa]|coinvolt[oa]|responsabile|nuov[oa]|autonom[oa]))\b",
        re.IGNORECASE,
    )

    @classmethod
    def _looks_acephalous_fact(cls, fact: str) -> bool:
        """Filtro conservativo: scarta fatti che iniziano con un predicato senza soggetto."""
        return bool(cls._ACEPHALOUS_FACT_RE.search(fact or ""))

    _REFLECTION_SYSTEM = (
        f"Sei un sistema di consolidamento memoria. Leggi le memorie recenti di {_OWNER_NAME} "
        "e quelle correlate dal suo archivio, e produci una sintesi breve che identifichi:\n"
        "1. Il tema dominante della sessione\n"
        f"2. Eventuali connessioni con attività passate di {_OWNER_NAME}\n"
        "3. Un possibile interesse a breve termine che potrebbe emergere\n\n"
        "Regole:\n"
        "- Massimo 3 frasi totali\n"
        "- Tono funzionale, non emotivo\n"
        f"- Terza persona su {_OWNER_NAME}\n"
        f"- Non trasformare possibilita' o collegamenti plausibili in piani, decisioni o fatti di {_OWNER_NAME}\n"
        f"- La terza frase deve iniziare esattamente con 'Ipotesi di {_ASSISTANT_NAME}:' e dichiarare "
        f"  una tua interpretazione, non una intenzione attribuita a {_OWNER_NAME}\n"
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
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": self._REFLECTION_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                # think=True: Gemma 4 consuma molti token in reasoning prima dell'output;
                # Loop 2a gira in idle senza vincoli di latenza, cap alto per non troncare a metà frase.
                options={"temperature": 0.4, "num_predict": 3000},
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
            f"{_OWNER_NAME} ti ha spiegato questo:\n{accumulated}\n\n"
            f"Riassumi in modo chiaro e completo, mantenendo tutti i dettagli tecnici importanti. "
            f"Scrivi come se dovessi spiegarlo a qualcuno in futuro. Max 5 frasi."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 2000},
                think=True,
            )
            return self._clean(response.message.content or "")
        except Exception as e:
            logger.error(f"Errore summarize_knowledge: {e}")
            return accumulated

    def extract_fact_from_exchange(self, owner_turn: str, assistant_turn: str = "") -> str:
        """
        SAVE anaforico ("memorizza questo / queste informazioni"): estrae il FATTO
        memorizzabile emerso in uno scambio — NON riassume la conversazione.
        Fonte primaria: ciò che afferma l'utente; la risposta dell'assistente serve SOLO a
        disambiguare nomi/soggetti/contesto. Esclude meta-commenti (cosa il sistema
        sa/non sa), inviti a caricare documenti, spiegazioni sul salvataggio, preamboli.
        Ritorna il fatto pulito (max 3 frasi), o '' se non c'è un fatto memorizzabile.
        """
        prompt = (
            f"In questo scambio {_OWNER_NAME} vuole fissare in memoria un FATTO.\n\n"
            f"{_OWNER_NAME}: {owner_turn}\n"
            f"{_ASSISTANT_NAME}: {assistant_turn}\n\n"
            f"Estrai SOLO il fatto memorizzabile emerso, in italiano, conciso (max 3 frasi). "
            f"Fonte primaria: ciò che afferma {_OWNER_NAME}; usa la risposta di "
            f"{_ASSISTANT_NAME} solo per "
            f"disambiguare nomi, soggetti o contesto.\n"
            f"NON includere: commenti su cosa il sistema sa o non sa, inviti a caricare "
            f"documenti, spiegazioni sul salvataggio, preamboli (es. 'Ecco un riassunto'). "
            f"Scrivi direttamente il fatto.\n"
            f"Se non c'è un fatto concreto da ricordare, rispondi SOLO con: NESSUN FATTO"
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 2000},
                think=True,
            )
            out = self._clean(response.message.content or "").strip()
            return "" if out.upper().startswith("NESSUN FATTO") else out
        except Exception as e:
            logger.error(f"Errore extract_fact_from_exchange: {e}")
            return ""

    # Header '# Memoria (2026-04-23 11:58:50)' che alcuni contenuti storici portano in
    # testa: NON deve sopravvivere a una fusione, o la memoria fusa eredita una data vecchia
    # (caso Realube 15/06: il merge trascinava il timestamp del 23/04, mis-datando il nodo).
    _MEM_HEADER_RE = re.compile(r'#\s*Memoria\s*\([^)]*\)\s*')

    @classmethod
    def _strip_memory_header(cls, text: str) -> str:
        if not text:
            return text
        return cls._MEM_HEADER_RE.sub('', text).strip()

    def merge_memories(self, existing: str, new: str) -> str:
        """
        Decide se ARRICCHIRE una memoria esistente con una nuova informazione.
        Ritorna una di tre cose:
          - il testo FUSO (B integrato con C, fedele, senza invenzioni) → stesso soggetto, C aggiunge;
          - 'DIVERSO' → C riguarda un soggetto diverso, o non è chiaro che sia lo stesso;
          - 'NESSUNA AGGIUNTA' → C non aggiunge nulla di nuovo.
        Bias esplicito: in dubbio → DIVERSO. Conflare due entità distinte è peggio di un
        doppione (il doppione lo consolida il Loop 2e; la conflazione corrompe il dato).
        Su errore LLM ritorna 'DIVERSO' (il chiamante salva separato: nessuna perdita, nessuna conflazione).
        """
        # Strip degli header '# Memoria (data)' dagli input: il modello non li vede → non li copia.
        existing = self._strip_memory_header(existing)
        new = self._strip_memory_header(new)
        prompt = (
            f"Memoria esistente B:\n{existing}\n\n"
            f"Nuova informazione C:\n{new}\n\n"
            f"Decidi:\n"
            f"- Se C riguarda CHIARAMENTE lo stesso soggetto/entità di B e aggiunge fatti "
            f"concreti non già presenti (varianti, numeri, componenti, processi, date, nomi), "
            f"riscrivi B integrando C: fedele ai fatti, senza inventare nulla, conciso, "
            f"mantenendo tutti i dettagli di B.\n"
            f"- Se C riguarda un soggetto DIVERSO da B, oppure non è chiaro che sia lo stesso "
            f"(es. codici/numeri/nomi che potrebbero essere cose distinte), rispondi SOLO con: DIVERSO\n"
            f"- Se C non aggiunge nulla di nuovo rispetto a B, rispondi SOLO con: NESSUNA AGGIUNTA\n"
            f"In caso di dubbio sul fatto che B e C siano lo stesso soggetto, scegli DIVERSO."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 2000},
                think=True,
            )
            # Rete: strip dell'header anche in uscita (se il modello lo rigenera). Innocuo
            # su 'DIVERSO'/'NESSUNA AGGIUNTA' (non contengono l'header).
            return self._strip_memory_header(self._clean(response.message.content or ""))
        except Exception as e:
            logger.error(f"Errore merge_memories: {e}")
            return "DIVERSO"

    def apply_correction_to_memory(self, existing: str, correction: str) -> str | None:
        """Gemello di merge_memories per la CORREZIONE (canale rilettura→cura, 14/07):
        riscrive la memoria applicando la correzione dell'utente — user > tutto nella
        gerarchia di fiducia. Fedele: cambia SOLO ciò che la correzione tocca, tiene
        il resto, non inventa. Ritorna il testo corretto o None su errore (il chiamante
        allora salva la correzione grezza e supseda la vecchia: mai perdere la parola
        dell'utente)."""
        existing = self._strip_memory_header(existing)
        prompt = (
            f"Memoria salvata B:\n{existing}\n\n"
            f"Correzione di {_OWNER_NAME} C:\n{correction}\n\n"
            f"Riscrivi B applicando C: correggi SOLO ciò che C contraddice o precisa, "
            f"mantieni intatto tutto il resto di B, non aggiungere nulla che non sia "
            f"in B o in C. La parola di {_OWNER_NAME} vince sempre su B. "
            f"Rispondi SOLO col testo riscritto, niente premesse né commenti."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 2000},
                think=False,
            )
            out = self._strip_memory_header(self._clean(response.message.content or ""))
            return out if len(out) >= 20 else None
        except Exception as e:
            logger.error(f"Errore apply_correction_to_memory: {e}")
            return None

    @staticmethod
    def _format_history_for_save(recent_history: list[dict], max_msgs: int = 16) -> str:
        """Formatta gli ultimi scambi (ruolo→nome) per il risolutore SAVE. Vuoto se assente."""
        if not recent_history:
            return ""
        lines = []
        for m in recent_history[-max_msgs:]:
            content = (m.get("content") or "").strip()
            if not content:
                continue
            who = _OWNER_NAME if m.get("role") == "user" else _ASSISTANT_NAME
            lines.append(f"{who}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _extract_json(raw: str) -> dict:
        """Estrae il primo oggetto JSON da un testo (tollera testo attorno). {} se non trovato."""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            pass
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            obj = json.loads(raw[start:end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def resolve_save_intent(self, command: str, recent_history: list[dict]) -> dict:
        """
        Gradino 1 del controllore di memoria — RUOLO svolto dal modello GIÀ CALDO (Gemma
        realtime), prima del risolutore a regex in save_service. Decide cosa significa un
        comando di salvataggio guardando la conversazione recente:
          - "direct"       : il comando contiene già un fatto completo e autosufficiente
                             (es. "memorizza che Giovanna è responsabile qualità").
          - "recent_topic" : il comando rimanda a un SOGGETTO/TEMA discusso poco fa
                             (es. "ricordati il macinato di Seari") senza contenere i
                             dettagli → cattura la SOSTANZA dagli scambi, non l'etichetta.
          - "last_exchange": riferimento anaforico puro ("memorizza questo").
          - "ask"          : non è chiaro cosa salvare.
        Ritorna {"mode","memory","confidence"} oppure {} su errore/parse fallito (→ il
        chiamante fa fallback al comportamento attuale). Vedi [[project_euri_memory_controller]].
        """
        convo = self._format_history_for_save(recent_history)
        if not convo:
            return {}
        prompt = (
            f"{_OWNER_NAME} ha dato a {_ASSISTANT_NAME} un comando di salvataggio in memoria.\n\n"
            f"Comando: \"{command}\"\n\n"
            f"Conversazione recente (dal più vecchio al più recente):\n{convo}\n\n"
            "Decidi cosa salvare e rispondi SOLO con un oggetto JSON, niente altro testo:\n"
            '{"mode": "...", "memory": "...", "confidence": 0.0}\n\n'
            "mode può essere:\n"
            "- \"direct\": il comando contiene GIÀ un fatto completo e autosufficiente. "
            "memory = quel fatto, ripulito.\n"
            "- \"recent_topic\": il comando rimanda a un SOGGETTO o TEMA discusso negli "
            "scambi qui sopra (es. 'ricordati il macinato di Seari'), senza contenere lui "
            "stesso i dettagli. memory = una memoria pulita e densa che cattura la SOSTANZA "
            "di ciò che è stato detto su quel soggetto nella conversazione (NON solo "
            "l'etichetta o il nome).\n"
            "- \"last_exchange\": riferimento puramente anaforico ('memorizza questo', "
            "'segnati quanto detto'). memory = sintesi del fatto emerso nell'ultimo scambio.\n"
            "- \"ask\": non è chiaro cosa salvare. memory = \"\".\n\n"
            "Per memory: italiano, conciso (max 3-4 frasi), SOLO fatti concreti. Fonte "
            f"primaria: ciò che afferma {_OWNER_NAME}; la conversazione serve a recuperare i "
            "dettagli del soggetto. NON includere preamboli, meta-commenti o spiegazioni sul "
            "salvataggio.\n"
            "confidence: da 0 a 1, quanto sei sicuro della scelta di mode e del contenuto."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 3000},
                think=True,
            )
            raw = self._clean(response.message.content or "").strip()
            data = self._extract_json(raw)
            if not data:
                return {}
            return {
                "mode": str(data.get("mode", "")).strip().lower(),
                "memory": str(data.get("memory", "")).strip(),
                "confidence": data.get("confidence", 0.0),
            }
        except Exception as e:
            logger.error(f"Errore resolve_save_intent: {e}")
            return {}

    def classify_retrieval_strategy(self, query: str, recent_history: list[dict] = None) -> dict:
        """
        Gradino 2 del controllore di memoria — RUOLO del modello GIÀ CALDO (Gemma realtime).
        Sceglie la STRATEGIA di retrieval per una domanda; NON genera la risposta. Chiamato
        solo quando una pre-gate cheap sospetta una domanda non-specifica (vedi
        core/retrieval_strategy). Ritorna {"strategy","subject","confidence"}:
          - "specific_search": domanda specifica/fattuale → retrieval attuale, subject "".
          - "wide_recall"    : panoramica/autobiografica/progetti → campione ampio, subject "".
          - "subject_recall" : tutto su un soggetto nominato in modo aperto → subject = nome.
          - "entity_recall"  : nomi/ruoli/chi fa cosa/relazioni tra entità → subject "".
          - "recent_context" : si risolve con la conversazione recente → subject "".
          - "chronological_first": prima occorrenza verbatim di un soggetto.
          - "chronological_last" : ultima occorrenza verbatim di un soggetto.
          - "chronological_timeline": prime e ultime occorrenze per una cronologia.
        {} su errore/parse fallito (→ il chiamante fa fallback a specific_search).
        """
        convo = self._format_history_for_save(recent_history)
        convo_block = f"\nConversazione recente:\n{convo}\n" if convo else ""
        prompt = (
            f"Classifica che TIPO di recupero memoria serve per la domanda di {_OWNER_NAME}. "
            "NON rispondere alla domanda, scegli solo la strategia.\n\n"
            f"Domanda: \"{query}\"{convo_block}\n"
            "Rispondi SOLO con un oggetto JSON, niente altro testo:\n"
            '{"strategy": "...", "subject": "...", "confidence": 0.0}\n\n'
            "strategy può essere:\n"
            "- \"specific_search\": domanda specifica/fattuale (es. 'quanto pesa il Poseidon?', "
            "'quando scade la commessa') → basta il recupero mirato. subject = \"\".\n"
            "- \"wide_recall\": panoramica o autobiografica (es. 'cosa sai di me', 'che "
            "progetti conosci', 'fammi una panoramica') → serve un campione ampio. subject = \"\".\n"
            "- \"subject_recall\": vuole TUTTO su un SOGGETTO nominato in modo aperto (es. "
            "'parlami di Poseidon', 'cosa sai del macinato Seari') → subject = il nome del "
            "soggetto (es. 'Poseidon', 'macinato Seari').\n"
            "- \"entity_recall\": chiede NOMI, RUOLI, chi fa cosa, composizione di un gruppo "
            "o relazioni tra persone/entità, senza un singolo soggetto specifico da cercare "
            "(es. 'chi lavora con noi?', 'quali nomi conosci?', 'che ruoli hanno?') → subject = \"\".\n"
            "- \"recent_context\": si risolve con ciò che vi siete detti POCO FA (es. "
            "'ricapitola', 'cosa stavamo dicendo') → subject = \"\".\n\n"
            "- \"chronological_first\": chiede QUANDO l'utente ha parlato, nominato o "
            "raccontato per la PRIMA VOLTA un soggetto. subject = poche parole distintive "
            "che devono comparire insieme nel turno originale.\n"
            "- \"chronological_last\": stessa richiesta per l'ULTIMA VOLTA. subject = "
            "poche parole distintive.\n"
            "- \"chronological_timeline\": chiede una cronologia delle volte in cui un "
            "soggetto è stato menzionato. subject = poche parole distintive.\n"
            "Le strategie chronological riguardano la DATA DELLA CONVERSAZIONE, non la "
            "data di un evento o una scadenza. 'Quando scade la commessa?' resta "
            "specific_search. Nel subject non inserire Stefano, Euri o parole generiche: "
            "per una persona ambigua usa anche il ruolo se il dialogo lo rende chiaro "
            "(es. 'Leonardo collega'), ma soltanto termini attesi nello stesso turno.\n\n"
            "Distingui bene: 'quanto pesa il Poseidon?' è specific_search (un dato preciso), "
            "'parlami di Poseidon' è subject_recall (tutto sul soggetto), "
            "'quali persone/ruoli conosci?' è entity_recall, "
            "'quando ti ho parlato per la prima volta di Poseidon?' è "
            "chronological_first.\n"
            "confidence: da 0 a 1, quanto sei sicuro."
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.1, "num_predict": 2000},
                think=True,
            )
            raw = self._clean(response.message.content or "").strip()
            data = self._extract_json(raw)
            if not data:
                return {}
            return {
                "strategy": str(data.get("strategy", "")).strip().lower(),
                "subject": str(data.get("subject", "")).strip(),
                "confidence": data.get("confidence", 0.0),
            }
        except Exception as e:
            logger.error(f"Errore classify_retrieval_strategy: {e}")
            return {}

    def classify_chronological_query(
        self,
        query: str,
        recent_history: list[dict] = None,
    ) -> dict:
        """Classificatore rapido per interrogazioni sul diario verbatim.

        Decide soltanto se serve un estremo/una cronologia e quali termini
        congiunti cercare. Non vede Redis, non risponde alla domanda e non
        produce date.
        """
        convo = self._format_history_for_save(recent_history)
        convo_block = f"\nDialogo recente per disambiguare il soggetto:\n{convo}\n" if convo else ""
        prompt = (
            "Classifica se la richiesta riguarda la DATA IN CUI l'utente ha "
            "pronunciato o menzionato qualcosa nella conversazione. Non rispondere "
            "e non inventare date.\n\n"
            f"Richiesta: \"{query}\"{convo_block}\n"
            "kind è uno tra first, last, timeline, none:\n"
            "- first: prima volta in cui l'utente ne ha parlato;\n"
            "- last: ultima volta;\n"
            "- timeline: cronologia delle menzioni;\n"
            "- none: domanda sulla data di un evento, una prova o una scadenza, "
            "non sul momento della conversazione.\n"
            "subject deve contenere solo poche parole distintive che ci si aspetta "
            "insieme nel turno originale. Ometti Stefano ed Euri. Se un nome è "
            "ambiguo e il dialogo chiarisce il ruolo, aggiungi il ruolo, per esempio "
            "\"Leonardo collega\".\n"
            'Rispondi solo JSON: {"kind":"first|last|timeline|none",'
            '"subject":"","confidence":0.0}'
        )
        try:
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                format="json",
                think=False,
            )
            raw = self._clean(response.message.content or "").strip()
            data = self._extract_json(raw)
            if not data:
                return {}
            return {
                "kind": str(data.get("kind", "")).strip().lower(),
                "subject": str(data.get("subject", "")).strip(),
                "confidence": data.get("confidence", 0.0),
            }
        except Exception as e:
            logger.error(f"Errore classify_chronological_query: {e}")
            return {}

    def evaluate_memory_relevance(self, content: str) -> str:
        """
        Death-row gate: valuta se una memoria in scadenza vale ancora la pena conservare.
        Ritorna 'KEEP' o 'DROP'.
        Chiamato solo per memorie passive/reflection mai richiamate vicine alla scadenza.
        """
        prompt = (
            f"Sei il sistema di gestione memoria di {_ASSISTANT_NAME}, l'assistente "
            f"personale di {_OWNER_NAME}.\n"
            f"Questa memoria sta per scadere perché non è mai stata richiamata in conversazione:\n\n"
            f"\"{content}\"\n\n"
            f"Vale la pena conservarla? Potrebbe essere stagionale, tecnica, o utile in futuro?\n"
            f"Rispondi SOLO con KEEP o DROP."
        )
        try:
            response = chat_client.chat(
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
            response = chat_client.chat(
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
            response = chat_client.chat(
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
            response = chat_client.chat(
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
            history_str = "\n".join(f"{_OWNER_NAME}: {m['content'][:200]}" for m in user_turns)
            prompt = (
                f"Conversazione recente:\n{history_str}\n\n"
                f"{_OWNER_NAME} vuole cercare sul web: '{text}'\n"
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
            response = chat_client.chat(
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
        history_str = "\n".join(f"{_OWNER_NAME}: {m['content'][:150]}" for m in user_turns)
        prompt = (
            f"Conversazione recente:\n{history_str}\n\n"
            f"Ho cercato sul web: '{original_query}' ma i risultati erano scarsi o irrilevanti.\n"
            f"Basandoti sul contesto tecnico della conversazione, genera UNA query alternativa "
            f"più specifica e tecnica. Preferisci termini inglesi se l'argomento è tecnico/software. "
            f"Rispondi SOLO con le parole chiave, max 8 parole. Nient'altro."
        )
        try:
            response = chat_client.chat(
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
            f"{_OWNER_NAME} ha cercato sul web: '{query}'\n\n"
            f"Ecco i contenuti scaricati:\n{context}\n"
            f"REGOLE DI RISPOSTA:\n"
            f"1. ESTRAI I DATI: Se {_OWNER_NAME} chiede un elenco (es. titoli di film, prezzi, nomi), LEGGILI DIRETTAMENTE. Non dire 'Il sito elenca i film', dimmi tu i titoli.\n"
            f"2. Adatta il tono: se cerca dati tecnici o di lavoro, usa numeri e precisione. Se cerca cronaca o svago, vai ai fatti salienti.\n"
            f"3. Sii discorsivo e diretto. Parla per essere letto a voce (Text-to-Speech).\n"
            f"4. ZERO formattazione (niente markdown o asterischi). Zero premesse ('Ho trovato un sito che...'). Vai dritto alla risposta.\n"
            f"5. TEMPERATURE: converti sempre in Celsius. Se trovi Fahrenheit, converti: (F - 32) × 5/9. Non dire mai gradi Fahrenheit."
        )
        try:
            response = chat_client.chat(
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
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
            )
            return self._clean(response.message.content or "")
        except Exception:
            return text

    def generate_code(self, task: str, available_files: list[str],
                      input_dir: str, output_dir: str,
                      file_contents: dict[str, str] = None,
                      file_contents_path: str = None) -> str:
        """
        Chiede a Gemma di generare uno script Python per manipolare file.
        Restituisce il codice puro (senza markdown fences).

        file_contents (opzionale): dict {filename: testo_estratto} per i file
        pre-leggibili (PDF/DOCX/PPTX/immagini). Il testo viene iniettato nel
        prompt così Gemma fa analisi su stringhe invece di riaprire i file
        binari (più affidabile: per i PDF/PPTX scansionati e le immagini, il
        fallback Vision di CodeRunner._preextract_files li ha già letti via
        Gemma multimodale).
        File come csv/xlsx/json/txt/md NON sono qui — Gemma li legge da
        disco direttamente nel codice generato.
        """
        files_list = "\n".join(f"  - {f}" for f in available_files) if available_files else "  (nessun file)"

        # Sezione file già estratti: testi pronti, Gemma lavora su stringhe
        file_section = ""
        if file_contents:
            blocks = []
            for fname, text in file_contents.items():
                if text:
                    # Cap a 8000 char per file per non saturare il context
                    snippet = text[:8000] + (" ...[troncato]" if len(text) > 8000 else "")
                    blocks.append(f"=== {fname} ===\n{snippet}")
                else:
                    blocks.append(f"=== {fname} ===\n(testo non estratto — file protetto, vuoto, o non leggibile)")
            # Header: dice a Gemma che FILE_CONTENTS è GIÀ DEFINITO nello scope
            # (CodeRunner fa il prepend del setup block prima dell'esecuzione).
            # Gemma deve SOLO usarlo, senza ridichiararlo, senza riassegnare
            # variabili tipo json_path/contents_path.
            access_hint = (
                f"\nIMPORTANTE — il dict FILE_CONTENTS è GIÀ DEFINITO nello "
                f"scope globale del tuo script (l'ho caricato io prima). "
                f"USALO direttamente: FILE_CONTENTS['nome_del_file.ext'] "
                f"restituisce il testo del file.\n"
                f"NON ridichiarare FILE_CONTENTS, NON aprire file JSON, "
                f"NON definire variabili tipo json_path/contents_path: "
                f"vai dritto alla logica di analisi.\n"
            ) if file_contents_path else ""

            file_section = (
                "\nCONTENUTO DEI FILE PRE-LETTI (PDF/DOCX/PPTX/immagini — "
                "testo già estratto, mostrato qui SOLO per riferimento):\n\n"
                + "\n\n".join(blocks) + "\n"
                + access_hint
            )

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
            f"9. Stampa con print() i VALORI CONCRETI che estrai o calcoli (numeri, proprietà, misure, nomi, totali), non solo un conteggio tipo 'estratte 5 righe'. Quello che NON stampi non esiste per chi ascolta: anche se salvi i dati in un CSV, stampa comunque i valori chiave nel testo, perché l'assistente vocale ricorderà SOLO l'output stampato, non il contenuto del file.\n"
            f"10. Se nel prompt c'è una sezione CONTENUTO DEI FILE PRE-LETTI: il dict FILE_CONTENTS è già caricato nello scope, usalo direttamente (FILE_CONTENTS['nome_file.ext']) senza ridichiararlo né aprire file JSON. CSV/XLSX/JSON/TXT/MD invece vanno letti normalmente da disco.\n\n"
            f"FILE DISPONIBILI IN INPUT:\n{files_list}\n"
            f"{file_section}\n"
            f"TASK: {task}"
        )
        try:
            _t = time.perf_counter()
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                # num_predict 4000: con PDF pre-estratti (cascata Vision) i
                # contenuti finiscono nel prompt E rispecchiati nello script
                # generato come stringa. Cap a 2000 troncava il codice a metà
                # del parsing (osservato sul caso D19 Scheda Tecnica, 28/05).
                options={"temperature": 0.2, "num_predict": 4000},
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
            response = chat_client.chat(
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

    def read_and_extract(self, documents: dict[str, str], question: str = "") -> str:
        """
        Percorso LETTURA (non code-gen): dà a Gemma il testo già estratto dai
        documenti e gli chiede di COMPRENDERLO ed estrarne i dati, invece di
        fargli scrivere un parser. Per un 26B leggere è molto più affidabile che
        programmare un parser corretto al primo colpo (vedi caso 03PPR100: il
        valore era leggibile, ma il parser generato prendeva il numero della
        norma ISO al posto del valore). Generico: nessuna assunzione di layout.
        """
        blocks = []
        for fname, text in documents.items():
            if text and text.strip():
                # cap per file: non saturare il contesto su documenti lunghi
                snippet = text[:8000] + (" ...[troncato]" if len(text) > 8000 else "")
                blocks.append(f"=== {fname} ===\n{snippet}")
        if not blocks:
            return "Non sono riuscito a leggere testo dai documenti nella cartella dati."

        focus = (
            f"\nDomanda specifica dell'utente: {question}\n"
            "Rispondi prima a questa, poi riporta gli altri dati salienti."
            if question and question.strip() else ""
        )
        prompt = (
            "Questi sono i testi ESTRATTI da uno o più documenti reali. "
            "Leggili con attenzione ed estrai i dati che contengono.\n\n"
            "REGOLE:\n"
            "- Il testo del documento è QUI SOTTO: è il tuo input. NON dire che "
            "non puoi accedere a file/cartelle, NON aggiungere premesse o "
            "disclaimer — vai diritto ai dati.\n"
            "- Adàttati al TIPO di documento (scheda tecnica, fattura, lettera, "
            "report, ricetta, tabella... qualunque cosa) ed estrai il suo "
            "contenuto saliente, senza dare per scontato cosa contenga.\n"
            "- Riporta ogni dato concreto con la sua etichetta e l'unità se "
            "presente: valori, misure, importi, date, nomi, quantità "
            "(es. 'IZOD: 6,5 kJ/m²', 'Totale: 1.240 €', 'Scadenza: 30/06').\n"
            "- Riporta SOLO ciò che è scritto nel testo. Se un dato non c'è, "
            "NON inventarlo: dì che non è riportato, oppure omettilo.\n"
            "- Distingui i valori reali dai codici e riferimenti (numeri di "
            "norma o metodo, sigle, ID): il numero di un riferimento NON è un "
            "valore misurato.\n"
            "- Output per voce: frasi naturali, niente markdown, niente tabelle "
            "ASCII. Se elenchi, usa 'Primo... Secondo...'.\n"
            f"{focus}\n\n"
            "DOCUMENTI:\n" + "\n\n".join(blocks)
        )
        try:
            _t = time.perf_counter()
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                # think=False: lettura diretta; num_predict ampio per documenti
                # con molte proprietà (il modello si ferma da solo).
                options={"temperature": 0.2, "num_predict": 1500},
                think=False,
            )
            logger.info(f"[TIMING] brain.read_and_extract() Ollama: {(time.perf_counter()-_t)*1000:.0f}ms")
            return self._clean(response.message.content or "Non sono riuscito a leggere il documento.")
        except Exception as e:
            logger.error(f"Errore read_and_extract: {e}")
            return "Non sono riuscito a leggere il documento."
