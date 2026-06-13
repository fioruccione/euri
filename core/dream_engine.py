"""
Dream Engine — Loop 2b (Sogni Onirici) e 2c (Insight e Promozione).

Il Dream Engine gira in background quando Euri è in idle (es. la notte).
Simula il processo di consolidamento della memoria umana:
- Prende memorie lontane semanticamente (da domini diversi)
- Cerca isomorfismi e connessioni nascoste (Loop 2b)
- Se trova un'analogia, crea un Insight CANDIDATE
- Se più sogni indipendenti confermano l'Insight, diventa VALIDATED e poi PROMOTED (Loop 2c)
"""
import time
import threading
import uuid
import json
import hashlib
import numpy as np
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from loguru import logger
import ollama
from core.ollama_client import dream_client
from core.operational_context import load_operational_context

import config
from utils.date_utils import now, to_timestamp
from redis.commands.search.query import Query
from utils.obsidian_sync import write_insight
from core.pulse import pulse_emit


class DreamEngine:
    def __init__(self, r, embedder, brain=None, memory=None):
        self._r = r
        self._embedder = embedder
        self._brain = brain        # usato dal Loop 2d (death-row gate)
        self._memory_manager = memory  # usato dal Bridge Synthesis
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        # Loop 2h — Self-Observation: istanziato solo se memory è disponibile
        # (in test isolati può essere None).
        from core.self_observation import SelfObservation
        self._self_observation = SelfObservation(r, memory) if memory else None
        
        # Traccia l'ultimo activity (STT/TTS) globale di Euri
        # Usa time.time() (wall-clock) e non time.monotonic() perché
        # monotonic si resetta quando il PC va in sospensione.
        self._last_activity = time.time()
        self._consolidation_last_run = 0.0  # timestamp ultimo Loop 2e
        
    def start(self):
        if not config.DREAM_ENGINE_ENABLED:
            logger.info("Dream Engine disabilitato da config")
            return
            
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="dream-engine")
            self._thread.start()
            logger.info("Dream Engine avviato (background)")

    def stop(self):
        with self._lock:
            self._running = False
            
    def notify_activity(self):
        """Chiamato da voice_daemon ad ogni STT/TTS per resettare l'idle timer."""
        with self._lock:
            self._last_activity = time.time()
            
    def _is_idle(self) -> bool:
        """Controlla se il sistema è inattivo da sufficienti ore."""
        with self._lock:
            elapsed_hours = (time.time() - self._last_activity) / 3600.0
        return elapsed_hours >= config.DREAM_ENGINE_IDLE_HOURS

    def _ollama_chat(self, **kwargs) -> ollama.ChatResponse:
        """Wrapper con timeout (default 200s) attorno a ollama.chat — evita hang notturni.
        Antepone il contesto operativo (EURI_CONTEXT.md) come messaggio system a tutte le
        chiamate notturne (sogno, sintesi, contraddizioni, plausibilità). Fail-open: se il
        file manca, op_ctx è "" e i messaggi restano invariati."""
        timeout = kwargs.pop("_timeout", 200)
        op_ctx = load_operational_context()
        if op_ctx and kwargs.get("messages"):
            kwargs["messages"] = [{"role": "system", "content": op_ctx}, *kwargs["messages"]]
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(dream_client.chat, **kwargs)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout:
                logger.warning(f"Dream Engine: timeout LLM dopo {timeout}s — ciclo abortito")
                raise

    def _loop(self):
        """Loop principale: controlla l'idle ogni 10 minuti."""
        while self._running:
            # Controllo ogni 10 minuti
            for _ in range(600):
                if not self._running:
                    return
                time.sleep(1)
                
            if self._is_idle():
                self._run_dream_cycle()
                
                # Dopo un ciclo di sogno, aspetta almeno un'altra ora (se ancora idle)
                # o finché non viene interrotto
                for _ in range(3600):
                    if not self._running or not self._is_idle():
                        break
                    time.sleep(1)

    def _run_dream_cycle(self):
        """Esegue un ciclo completo di sogni (Loop 2b) e validazione (Loop 2c)."""
        logger.info("Dream Engine: inizio ciclo onirico")
        pulse_emit(self._r, "dream", "intero", "cycle_start", salience=0.25)
        try:
            # 1. Trova domini unici
            domains = self._get_unique_domains()
            if len(domains) < 2:
                logger.debug("Dream Engine: non ci sono abbastanza domini per sognare")
                return
                
            # 2. Loop 2b: Sogni Onirici
            dream = self._generate_dream(domains)

            # 3. Loop 2c: Valutazione Insight — sempre, non solo se il sogno ha prodotto un candidato.
            # I candidati accumulati nei cicli precedenti vanno valutati indipendentemente.
            self._evaluate_insights()
                
            # 4. Loop 2f: Contradiction resolution — soft-delete valori numerici obsoleti
            self._contradiction_resolution_pass()

            # 4b. Loop 2g: Audit di Coerenza — analizza le correzioni ricevute durante il giorno
            self._audit_corrections_pass()

            # 4c. Plausibility gate — ARCHIVIATO (kill-switch off, codice lasciato in repo).
            # Negative result: 1 vero positivo / 3 falsi positivi su gemme di dominio vere,
            # anche col contesto operativo attivo → non chiamato di default. Vedi changelog.
            if config.PLAUSIBILITY_GATE_ENABLED:
                self._plausibility_gate_pass()

            # 4d. Loop 2h: Self-Observation — narrative di evoluzione dalle coppie superseded.
            # Additivo: NON modifica il Loop 2f (che continua a fare superseded_by), aggiunge
            # solo una voce narrativa in prima persona per ogni evoluzione mai raccontata prima.
            if self._self_observation:
                try:
                    self._self_observation.run()
                except Exception as e:
                    logger.error(f"Loop 2h: errore self-observation pass: {e}")

            # 5. Pulizia Insight scaduti
            self._cleanup_expired_insights()

            # 6. Pulizia Memorie stantie (passive/reflection mai richiamate)
            self._cleanup_stale_memories()

            # 7. Loop 2d: Death-row gate per memorie in scadenza entro 7 giorni
            self._pruning_pass()

            # 8. Loop 2e: Memory Consolidation — max una volta ogni 24h
            if time.time() - self._consolidation_last_run >= 86400:
                self._consolidation_pass()
                self._consolidation_last_run = time.time()

        except Exception as e:
            logger.error(f"Errore ciclo Dream Engine: {e}")

    # ── Loop 2b: Sogni Onirici ─────────────────────────────────────────────

    def _get_unique_domains(self) -> list[str]:
        """Recupera tutti i domini unici dalle memorie (escludendo 'generale')."""
        try:
            # Usa FT.AGGREGATE per raggruppare per dominio
            res = self._r.execute_command(
                "FT.AGGREGATE", "idx:memories", "*",
                "GROUPBY", "1", "@domain"
            )
            domains = []
            # Il formato di ritorno di FT.AGGREGATE è [count, [b'domain', b'valore'], ...]
            for row in res[1:]:
                if isinstance(row, list) and len(row) >= 2:
                    d = row[1].decode('utf-8') if isinstance(row[1], bytes) else str(row[1])
                    if d and d != "generale":
                        domains.append(d)
            return domains
        except Exception as e:
            logger.debug(f"Errore aggregate domini: {e}")
            return []

    def _get_random_memory_from_domain(self, domain: str) -> dict | None:
        """Recupera una memoria casuale da uno specifico dominio."""
        try:
            safe_domain = domain.replace(" ", "\\ ")
            # Prende un campione casuale (Redis stack non ha RANDOM natively in FT.SEARCH, 
            # ma possiamo prendere le prime con sort_by null e limit 10 e poi scegliere)
            q = Query(f"@domain:{{{safe_domain}}}").paging(0, 10).return_fields("id", "content", "embedding", "created_at")
            res = self._r.ft("idx:memories").search(q)
            if not res.docs:
                return None

            import random
            doc = random.choice(res.docs)
            return {
                "id": doc.id,
                "content": doc.content,
                "domain": domain,
                "embedding": getattr(doc, "embedding", None),
                "created_at": getattr(doc, "created_at", None),
            }
        except Exception as e:
            logger.debug(f"Errore fetch memoria da {domain}: {e}")
            return None

    @staticmethod
    def _has_required_structure(text: str) -> bool:
        """
        True se il content rispetta il formato a 3 righe richiesto dal prompt del Loop 2b:
        'Nel dominio [X] succede: ...' / 'Nel dominio [Y] succede: ...' / 'La connessione operativa ... è: ...'.
        Usato sia in generazione (Loop 2b) che in promozione (Loop 2c) — impedisce che insight
        astratti/filosofici accumulino convergenze e vengano promossi senza struttura operativa.
        """
        if not text:
            return False
        low = text.lower()
        return "connessione operativa" in low and "nel dominio" in low

    @staticmethod
    def _memory_age(ts) -> str:
        """Converte un timestamp unix in stringa relativa."""
        if not ts:
            return ""
        try:
            days = (time.time() - float(ts)) / 86400
            if days < 1:   return "oggi"
            if days < 7:   return f"{int(days)} {'giorno' if int(days)==1 else 'giorni'} fa"
            if days < 30:  return f"{int(days/7)} {'settimana' if int(days/7)==1 else 'settimane'} fa"
            if days < 365: return f"{int(days/30)} {'mese' if int(days/30)==1 else 'mesi'} fa"
            return f"{int(days/365)} {'anno' if int(days/365)==1 else 'anni'} fa"
        except Exception:
            return ""

    def _generate_dream(self, domains: list[str]) -> dict | None:
        """Seleziona due memorie da domini diversi e cerca un'analogia."""
        import random
        
        # Sceglie un dominio a caso
        dom_a = random.choice(domains)
        mem_a = self._get_random_memory_from_domain(dom_a)
        if not mem_a or not mem_a.get("embedding"):
            return None
            
        # Per massimizzare la creatività, cerchiamo un dominio B semanticamente DISTANTE
        # (Idealmente qui faremmo una vector search invertita, ma per ora scegliamo random
        # garantendo che sia diverso da A)
        other_domains = [d for d in domains if d != dom_a]
        if not other_domains:
            return None
            
        dom_b = random.choice(other_domains)
        mem_b = self._get_random_memory_from_domain(dom_b)
        if not mem_b:
            return None
            
        logger.info(f"Dream Engine: sogno tra '{dom_a}' e '{dom_b}'")
        
        # Chiedi a Gemma se esiste un isomorfismo
        age_a = self._memory_age(mem_a.get("created_at"))
        age_b = self._memory_age(mem_b.get("created_at"))
        label_a = f"dominio: {dom_a}" + (f", {age_a}" if age_a else "")
        label_b = f"dominio: {dom_b}" + (f", {age_b}" if age_b else "")

        prompt = f"""\
Hai due memorie da domini diversi. Il tuo compito è trovare una connessione operativa non ovvia — qualcosa che non emerge guardando un solo dominio.

Memoria A ({label_a}):
"{mem_a['content']}"

Memoria B ({label_b}):
"{mem_b['content']}"

Se esiste una connessione genuina, rispondi ESATTAMENTE in questo formato (tre righe, niente altro):
Nel dominio [{dom_a}] succede: [descrivi cosa succede concretamente, con i dettagli specifici della memoria A]
Nel dominio [{dom_b}] succede: [descrivi cosa succede concretamente, con i dettagli specifici della memoria B]
La connessione operativa non ovvia è: [effetto pratico verificabile — cosa puoi fare o evitare sapendo entrambe le cose]

REGOLE:
- La terza riga deve descrivere un effetto pratico che si può verificare o applicare, non un principio filosofico.
- Se la connessione che trovi è ovvia (es. "entrambi ottimizzano un processo"), rispondi NESSUN INSIGHT.
- Se non riesci a formulare la terza riga con un effetto concreto, rispondi NESSUN INSIGHT.
- Nessuna frase introduttiva, nessun commento fuori formato."""

        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.6, "num_predict": 4500},
                think=True,
            )
            text = response.message.content or ""
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1]
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            
            status = "discarded"
            insight_content = ""

            if text and "NESSUN INSIGHT" not in text.upper() and self._has_required_structure(text):
                status = "candidate"
                insight_content = text
                logger.info(f"Dream Engine: generato CANDIDATE Insight → {insight_content[:50]}...")
            else:
                logger.debug("Dream Engine: sogno scartato (nessun isomorfismo o formato incompleto)")
                
            # Salva il sogno
            dream_id = str(uuid.uuid4())
            dream_doc = {
                "id": dream_id,
                "content": insight_content if status == "candidate" else "Nessuna analogia trovata",
                "status": status,
                "domain_a": dom_a,
                "domain_b": dom_b,
                "memory_a_id": mem_a["id"],
                "memory_b_id": mem_b["id"],
                "created_at": to_timestamp(now()),
            }
            self._r.json().set(f"euri:dream:{dream_id}", "$", dream_doc)
            # TTL di 7 giorni per i sogni grezzi
            self._r.expire(f"euri:dream:{dream_id}", 86400 * 7)
            
            # Se è un candidato, creiamo anche un entry provvisoria negli insights
            if status == "candidate":
                vec = self._embedder.encode(insight_content, mode="passage")
                insight_id = str(uuid.uuid4())
                insight_doc = {
                    "id": insight_id,
                    "content": insight_content,
                    "status": "candidate",
                    "domain_a": dom_a,
                    "domain_b": dom_b,
                    "created_at": to_timestamp(now()),
                    "recalled_count": 0,
                    "embedding": vec.tolist() if vec is not None else None,
                    "convergence_count": 1
                }
                self._r.json().set(f"euri:insight:{insight_id}", "$", insight_doc)
                
            return dream_doc
            
        except Exception as e:
            logger.error(f"Errore generazione sogno: {e}")
            return None

    # ── Loop 2c: Insight e Promozione ──────────────────────────────────────

    def _llm_judge_same_insight(self, content_a: str, content_b: str) -> bool:
        """
        Zona grigia: chiede a Gemma (con thinking) se due insight esprimono
        lo stesso principio strutturale, anche se formulati diversamente.
        Il vettore MiniLM è superficiale; il judge ragiona sul significato profondo.
        """
        prompt = f"""\
Analizza questi due insight generati da processi di ragionamento indipendenti.

Insight A: "{content_a}"
Insight B: "{content_b}"

Esprimono lo stesso principio strutturale o la stessa analogia profonda,
anche se formulati con parole diverse?

Rispondi SOLO con SÌ o NO."""
        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 1500},
                think=True,
            )
            text = response.message.content or ""
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1]
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            return text.strip().upper().startswith(("SÌ", "SI", "YES"))
        except Exception as e:
            logger.debug(f"Errore LLM judge insight: {e}")
            return False

    def _evaluate_insights(self):
        """Valuta i candidate insights per la promozione (convergenza)."""
        try:
            # Cerca tutti i CANDIDATE
            q = Query("@status:{candidate}").return_fields("id", "content", "embedding", "convergence_count").paging(0, 500)
            res = self._r.ft("idx:insights").search(q)
            
            if not res.docs:
                return
                
            # Per ogni candidato, controlla se ci sono altri candidati molto simili
            # (Convergenza = la stessa intuizione è emersa da sogni indipendenti)
            promoted_count = 0
            
            for doc in res.docs:
                # Potrebbe essere già stato eliminato come duplicato in un'iterazione precedente
                if not self._r.exists(doc.id):
                    continue

                vec_str = getattr(doc, "embedding", None)
                if not vec_str:
                    continue
                    
                import json
                import numpy as np
                try:
                    vec_list = json.loads(vec_str)
                    vec_bytes = np.array(vec_list, dtype=np.float32).tobytes()
                except Exception as e:
                    logger.debug(f"Errore parsing vettore: {e}")
                    continue
                    
                # Cerca simili (senza escludere da query per evitare syntax error, filtriamo in python)
                q_sim = (
                    Query("(@status:{candidate}) => [KNN 4 @embedding $vec AS score]")
                    .sort_by("score")
                    .return_fields("id", "content", "score")
                    .dialect(2)
                )
                res_sim = self._r.ft("idx:insights").search(q_sim, query_params={"vec": vec_bytes})

                # Conta quanti hanno score molto alto (distanza cosine bassa)
                # < 0.15 → convergenza certa (vettori quasi identici)
                # 0.15–0.40 → zona grigia: il vettore MiniLM è superficiale,
                #              chiediamo al LLM se il principio profondo è lo stesso
                stored_cc = self._r.json().get(doc.id, "$.convergence_count")
                convergences = int(stored_cc[0]) if stored_cc else 1
                similar_ids = []

                for sim in res_sim.docs:
                    if sim.id == doc.id:
                        continue  # Salta se stesso
                    score = float(sim.score)
                    if score < 0.15:
                        convergences += 1
                        similar_ids.append(sim.id)
                    elif score < 0.40:
                        sim_content = getattr(sim, "content", None)
                        if sim_content and self._llm_judge_same_insight(doc.content, sim_content):
                            logger.debug(f"Dream Engine: judge LLM ha confermato convergenza (score={score:.2f})")
                            convergences += 1
                            similar_ids.append(sim.id)
                        
                # Se abbiamo abbastanza convergenze, promuoviamo!
                if convergences >= config.DREAM_INSIGHT_MIN_CONVERGENCES:
                    # Gate di formato: un CANDIDATE astratto/filosofico (senza il pattern
                    # "Nel dominio X succede / La connessione operativa è") non viene promosso
                    # anche se ha accumulato convergenze. Il Loop 2b filtra in generazione,
                    # ma seed storici pre-filtro possono ancora raggiungere la soglia: qui
                    # chiudiamo il gap. Lasciamo il candidate in vita (TTL lo gestirà) — non
                    # lo cancelliamo: la convergenza misurata resta un segnale, e se in futuro
                    # il filtro cambia formato la decisione può essere rivalutata.
                    if not self._has_required_structure(doc.content):
                        logger.info(
                            f"Dream Engine: promozione bloccata (formato non operativo) — "
                            f"{doc.id[-8:]} con {convergences} convergenze"
                        )
                        continue

                    # Gate di ri-promozione (V2.19, opzione b): la PRIMA promozione è
                    # libera (il sogno deve poter affiorare per sola convergenza). Ma un
                    # insight già demoto da Gate 1 — cioè invecchiato (14–30 giorni dalla
                    # creazione) e MAI richiamato in conversazione — non torna in vita per
                    # la sola ri-convergenza (il sogno che si auto-resuscita). Per rinascere
                    # deve essere stato validato dall'uso reale (recalled_count > 0).
                    # I candidate non sono nel percorso di richiamo (search_insights filtra
                    # @status:{promoted}), quindi per un demoto questo è di fatto sempre
                    # vero: resta candidate e si spegne pulito al giorno 30 (Gate 3).
                    # Il check su recalled_count è esplicito per documentare il principio
                    # ed essere a prova di futuro. Niente oscillazione demote↔re-promote.
                    stored_dem = self._r.json().get(doc.id, "$.demoted_once")
                    demoted_once = bool(stored_dem[0]) if stored_dem else False
                    stored_rc = self._r.json().get(doc.id, "$.recalled_count")
                    recalled = int(stored_rc[0]) if stored_rc else 0
                    if demoted_once and recalled == 0:
                        logger.info(
                            f"Dream Engine: re-promozione negata (demoto, mai validato "
                            f"dall'uso) — {doc.id[-8:]} con {convergences} convergenze"
                        )
                        continue

                    # Promuovi questo a PROMOTED
                    self._r.json().set(doc.id, "$.status", "promoted")
                    self._r.json().set(doc.id, "$.convergence_count", convergences)
                    self._r.json().set(doc.id, "$.promoted_at", time.time())
                    
                    # Rimuovi i duplicati assorbiti
                    for sid in similar_ids:
                        self._r.delete(sid)
                        
                    logger.success(f"Dream Engine: Insight PROMOSSO! (convergenze: {convergences})")
                    pulse_emit(self._r, "insight", "intero", "promoted",
                               payload={"convergences": convergences}, salience=0.65)
                    promoted_count += 1
                    
                    # Scrivi nel vault di Obsidian
                    try:
                        doc_promoted = self._r.json().get(doc.id, "$")
                        if doc_promoted:
                            write_insight(doc_promoted[0])
                    except Exception as e:
                        logger.debug(f"Errore sync insight su Obsidian: {e}")
                        
        except Exception as e:
            logger.error(f"Errore valutazione insights: {e}")

    def _llm_classify_pair(self, content_a: str, content_b: str) -> str:
        """
        Classifica la relazione tra due memorie simili. Generale, agnostico al dominio
        (ragiona su 'stesso soggetto vs soggetti diversi', non su liste cablate):
          'contradiction' — STESSO soggetto specifico, valori in conflitto che si
                            escludono (es. "MFI lotto X = 6" vs "= 4") → soft-delete.
          'comparison'    — soggetti/entità DIVERSI ma confrontabili (due impianti, due
                            clienti, due strategie, due posizioni): le differenze NON sono
                            errori, sono informazione → niente cancellazione, genera confronto.
          'none'          — non correlate o aspetti complementari.
        """
        prompt = f"""\
Memoria A: "{content_a[:400]}"
Memoria B: "{content_b[:400]}"

Classifica la relazione tra A e B. Rispondi con UNA sola parola:
- CONTRADDIZIONE: parlano dello STESSO soggetto specifico ma con valori in conflitto che si escludono a vicenda (es. "MFI lotto X = 6" vs "MFI lotto X = 4"; "scade il 10" vs "scade il 15").
- CONFRONTO: parlano di soggetti o entità DIVERSI ma confrontabili (es. due macchine diverse, due clienti, due strategie). Valori diversi qui sono NORMALI, non errori.
- NESSUNA: non correlate, o aspetti complementari che non si escludono.

Rispondi SOLO con: CONTRADDIZIONE, CONFRONTO, o NESSUNA."""
        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 12},
                think=False,
                _timeout=60,
            )
            text = (response.message.content or "").strip().upper()
            if "CONTRADD" in text:
                return "contradiction"
            if "CONFRONT" in text:
                return "comparison"
            return "none"
        except Exception as e:
            logger.debug(f"Loop 2f: errore LLM classify pair — {e}")
            return "none"

    def _make_comparison_memory(self, content_a: str, content_b: str, domain: str) -> None:
        """
        Idea di Stefano (01/06): quando il Loop 2f trova entità DIVERSE ma simili
        (due impianti, due clienti…), non sceglie un vincitore — genera una nota di
        CONFRONTO operativa (cosa in comune, in cosa differiscono, quando preferire
        l'una o l'altra). È meta-conoscenza, NON un fatto grezzo: la salvo con
        requires_verification=False così il Loop 2f non la ri-processa (no feedback loop).
        """
        if not self._memory_manager:
            return
        prompt = f"""\
Due voci di memoria descrivono entità DIVERSE ma confrontabili:

A: "{content_a[:500]}"
B: "{content_b[:500]}"

Scrivi un breve CONFRONTO operativo (2-4 frasi): cosa hanno in comune, in cosa
DIFFERISCONO concretamente (valori/limiti), e quando conviene l'una rispetto all'altra.
Non inventare dati non presenti nelle due voci. Rispondi solo col confronto."""
        try:
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 500},
                think=False,
                _timeout=120,
            )
            import re as _re
            text = _re.sub(r"<think>.*?</think>", "", (resp.message.content or ""),
                           flags=_re.DOTALL).strip()
            if not text:
                return
            mid = self._memory_manager.save_memory(
                f"[confronto] {text}", category="conoscenza", source="reflection",
                tags=["confronto", "loop2f", domain],
            )
            if mid:  # meta-conoscenza → fuori dal Loop 2f, niente ri-processamento
                self._r.json().set(f"euri:memory:{mid}", "$.requires_verification", False)
                logger.info(f"Loop 2f: nota di confronto generata {mid[:8]}… (dominio: {domain})")
        except Exception as e:
            logger.debug(f"Loop 2f: errore _make_comparison_memory — {e}")

    def _contradiction_resolution_pass(self):
        """
        Loop 2f — Contradiction resolution.
        Individua coppie di memorie con claims numerici/fattuali in conflitto sullo stesso dominio.
        Mantiene la più recente, soft-delete la più vecchia settando superseded_by=[ID_vincitore].
        Focus su requires_verification=True — già flaggate come contenenti valori numerici/fattuali.
        Max 15 coppie per ciclo; le coppie già analizzate vengono saltate (CHECKED set con TTL 180gg).
        """
        CHECKED_KEY = "euri:loop2f:checked"
        MIN_CONFLICT_SCORE = 0.28  # cosine distance < 0.28 → similarity > 0.72 (stesso argomento)
        MAX_PAIRS_PER_CYCLE = 15
        # web escluso: fonte esterna, citata sempre con cautela, non è in conflitto coi fatti interni.
        # loop2e NON è più escluso: le memorie consolidate sono nel RAG con alta priorità e devono
        # poter essere corrette dalle contraddizioni. Il soft-delete via superseded_by rende il rischio
        # reversibile — il dato sopravvive in Redis anche se la memoria viene esclusa dal retrieval.
        SKIP_SOURCES = {"web"}

        try:
            # 1. Candidati: requires_verification=True, non già superseded
            candidates = []
            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    if not doc.get("requires_verification"):
                        continue
                    if doc.get("superseded_by"):
                        continue
                    if doc.get("source") in SKIP_SOURCES:
                        continue
                    candidates.append(doc)
                except Exception:
                    continue

            if len(candidates) < 2:
                logger.debug("Loop 2f: candidati insufficienti per contradiction check")
                return

            import redis as _redis_mod
            _raw_r = _redis_mod.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT,
                db=config.REDIS_DB, decode_responses=False,
            )

            def _dec(v, default=""):
                if v is None:
                    return default
                return v.decode() if isinstance(v, bytes) else str(v)

            pairs_checked = 0
            resolved = 0
            compared = 0

            for seed in candidates:
                if pairs_checked >= MAX_PAIRS_PER_CYCLE:
                    break
                seed_id = seed.get("id", "")
                if seed.get("superseded_by"):  # potrebbe essere stato superseded in questo ciclo
                    continue
                seed_emb = seed.get("embedding")
                seed_domain = seed.get("domain", "generale")
                if not seed_emb:
                    continue

                # 2. KNN nello stesso dominio
                try:
                    vec_bytes = np.array(seed_emb, dtype=np.float32).tobytes()
                    safe_domain = seed_domain.replace(" ", "\\ ")
                    q = (
                        Query(f"(@domain:{{{safe_domain}}})=>[KNN 6 @embedding $vec AS score]")
                        .sort_by("score")
                        .return_fields("id", "score")
                        .dialect(2)
                    )
                    res = _raw_r.ft("idx:memories").search(q, query_params={"vec": vec_bytes})
                except Exception:
                    continue

                for neighbor in res.docs:
                    n_id = _dec(neighbor.id).replace("euri:memory:", "")
                    if n_id == seed_id:
                        continue

                    score = float(_dec(getattr(neighbor, "score", b"1.0")) or "1.0")
                    if score >= MIN_CONFLICT_SCORE:
                        continue  # troppo distanti semanticamente

                    pair_key = "|".join(sorted([seed_id, n_id]))
                    if self._r.sismember(CHECKED_KEY, pair_key):
                        continue

                    # 3. Carica il neighbor completo
                    n_raw = self._r.json().get(f"euri:memory:{n_id}", "$")
                    if not n_raw:
                        continue
                    n_doc = n_raw[0]
                    if not n_doc.get("requires_verification"):
                        continue
                    if n_doc.get("superseded_by"):
                        continue

                    # 4. Classifica la relazione: contraddizione / confronto / nessuna
                    rel = self._llm_classify_pair(
                        seed.get("content", ""), n_doc.get("content", "")
                    )

                    self._r.sadd(CHECKED_KEY, pair_key)
                    self._r.expire(CHECKED_KEY, 180 * 86400)
                    pairs_checked += 1

                    if rel == "comparison":
                        # Entità DIVERSE ma simili (es. due impianti, due clienti): NON è una
                        # contraddizione → non si cancella nulla. Le differenze sono conoscenza:
                        # genera una nota di confronto (mappa di scelta). Idea di Stefano 01/06.
                        self._make_comparison_memory(
                            seed.get("content", ""), n_doc.get("content", ""),
                            seed.get("domain", "generale"),
                        )
                        compared += 1
                        logger.info(
                            f"Loop 2f: {seed_id[:8]}… ↔ {n_id[:8]}… confronto tra entità "
                            f"distinte (nessun soft-delete)"
                        )
                        continue

                    if rel != "contradiction":
                        continue

                    # 5. Contraddizione vera: soft-delete il più vecchio (created_at minore)
                    seed_ts = float(seed.get("created_at") or 0)
                    n_ts = float(n_doc.get("created_at") or 0)
                    seed_is_older = seed_ts < n_ts
                    if seed_is_older:
                        loser_doc, loser_id, winner_id = seed, seed_id, n_id
                    else:
                        loser_doc, loser_id, winner_id = n_doc, n_id, seed_id

                    # PARAURTI di richiamo (N3): un atomo fattuale MOLTO RICHIAMATO non viene
                    # auto-cancellato via contraddizione — tieni entrambi. Deterministico:
                    # nessun segnale economico (cosine/lunghezza/richiamo/giudizio LLM) separa
                    # in modo affidabile l'assorbimento dannoso da quello legittimo — la
                    # fidelity-probe sbaglia ~metà delle volte sulle distinzioni fini
                    # (contro-caso test_plane_guard). Conservativo: il consolidamento sui
                    # poco-richiamati resta invariato; il fail-safe è "tieni entrambi". Costo:
                    # un valore vecchio molto-usato sopravvive finché una correzione esplicita
                    # non lo soppianta. (Repo N3 / baseline diag_plane_fusion.py)
                    loser_recalled = int(loser_doc.get("recalled_count") or 0)
                    if loser_recalled >= config.LOOP2F_RECALL_GUARD:
                        logger.info(
                            f"Loop 2f: paraurti richiamo — {loser_id[:8]}… "
                            f"(recalled={loser_recalled}) NON soft-deletato via contraddizione, "
                            f"tengo entrambi"
                        )
                        continue  # pair già in CHECKED: non si ripresenta

                    self._r.json().set(f"euri:memory:{loser_id}", "$.superseded_by", winner_id)
                    logger.info(
                        f"Loop 2f: {loser_id[:8]}… superseded by {winner_id[:8]}… "
                        f"(conflitto risolto, tenuto il più recente)"
                    )
                    if seed_is_older:
                        break  # seed è stato superseded, inutile continuare con i suoi vicini
                    resolved += 1

            if resolved or compared:
                logger.info(
                    f"Loop 2f: {resolved} contraddizioni risolte, {compared} confronti generati "
                    f"({pairs_checked} coppie analizzate)"
                )
            else:
                logger.debug(f"Loop 2f: nessuna contraddizione né confronto ({pairs_checked} coppie analizzate)")

        except Exception as e:
            logger.error(f"Errore Loop 2f contradiction resolution: {e}")

    # ── Loop 2g: Audit di Coerenza ────────────────────────────────────────

    def _llm_classify_correction(
        self, prompt_orig: str, risposta_euri: str, correzione: str, ctx_memories: list[str]
    ) -> str:
        """
        LLM-as-judge a 4 vie. PRIMA il gate (è davvero una correzione?), poi il tipo.
        - not_a_correction: non è una correzione (domanda, elaborazione, accordo,
          pensiero ad alta voce, cambio argomento) → niente lesson, signal scartato.
        - bad_memory: l'errore deriva da una memoria iniettata sbagliata o obsoleta.
        - bad_reasoning: le memorie erano OK, l'errore è di ragionamento al volo.
        - ambiguous: è una correzione, ma non è chiaro se memoria o ragionamento.
        Baseline (diag_phantom_corrections.py): senza il gate, ≥53% dei signal erano
        fantasmi e 19/25 prendevano bad_memory/bad_reasoning → lesson spurie.
        """
        ctx_block = "\n".join(f"- {m}" for m in ctx_memories) if ctx_memories else "(nessuna memoria iniettata)"
        prompt = f"""\
Devi analizzare un messaggio che un utente ha rivolto a un assistente (Euri), dopo una risposta dell'assistente.

DOMANDA/CONTESTO ORIGINALE DELL'UTENTE:
"{prompt_orig[:500]}"

MEMORIE INIETTATE NEL CONTESTO DELL'ASSISTENTE (la base su cui ha risposto):
{ctx_block}

RISPOSTA DELL'ASSISTENTE:
"{risposta_euri[:500]}"

MESSAGGIO SUCCESSIVO DELL'UTENTE (da analizzare):
"{correzione[:500]}"

PRIMA stabilisci: questo messaggio è davvero una CORREZIONE di un errore dell'assistente?
È una correzione SOLO se l'utente afferma che l'assistente ha sbagliato un fatto, un ricordo o un ragionamento, e indica (anche implicitamente) la versione giusta.
NON è una correzione se l'utente: fa una domanda o chiede un ricordo ("ti ricordi di X?", "cosa sai di Y?"), aggiunge o elabora informazioni proprie, concorda ("esatto", "sì"), pensa ad alta voce, cambia argomento, scherza, o commenta in generale il comportamento dell'assistente — anche se il messaggio contiene "no", "non è", "in realtà".

Se NON è una correzione → rispondi NOT_A_CORRECTION.
Se È una correzione, classifica l'origine dell'errore:
- BAD_MEMORY: la risposta sbagliata deriva da una memoria iniettata sbagliata, obsoleta, o di un soggetto diverso da quello chiesto.
- BAD_REASONING: le memorie erano corrette e pertinenti, ma l'assistente ha ragionato male o confuso concetti.
- AMBIGUOUS: è una correzione, ma non è chiaro se l'origine sia la memoria o il ragionamento.

Nel dubbio tra NOT_A_CORRECTION e una categoria di correzione, scegli la correzione: è peggio scartare una correzione vera che processarne una falsa.

Rispondi SOLO con UNA parola: NOT_A_CORRECTION, BAD_MEMORY, BAD_REASONING, o AMBIGUOUS."""
        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 800},
                think=False,
                _timeout=90,
            )
            text = (response.message.content or "").strip().upper()
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1].strip()
            import re as _re
            text = _re.sub(r"<THINK>.*?</THINK>", "", text, flags=_re.DOTALL).strip()
            # NOT_A_CORRECTION per primo: è il gate. Default/fallback = ambiguous
            # (conservativo: trattato come correzione, ma senza generare lesson).
            for verdict in ("NOT_A_CORRECTION", "BAD_MEMORY", "BAD_REASONING", "AMBIGUOUS"):
                if verdict in text:
                    return verdict.lower()
            return "ambiguous"
        except Exception as e:
            logger.debug(f"Loop 2g: errore LLM classify — {e}")
            return "ambiguous"

    def _audit_corrections_pass(self):
        """
        Loop 2g — Audit di Coerenza.
        Per ogni correction_signal pending del giorno:
        1. Recupera contenuti delle memorie iniettate.
        2. Chiede al LLM-judge se l'errore è bad_memory o bad_reasoning.
        3. Azioni differenziate:
           - bad_memory: incrementa audit_flag sulle memorie nel rag_ctx (segnale debole, niente azione automatica per ora).
           - bad_reasoning: salva la correzione come passive memory di tipo 'lesson' (nutrimento per il futuro).
           - ambiguous: nessuna azione, solo marca lo status.
        Max 10 correzioni per ciclo per evitare cicli lunghi.
        """
        MAX_PER_CYCLE = 10

        try:
            pending = []
            for key in self._r.scan_iter("euri:correction:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    if doc.get("status") != "pending":
                        continue
                    pending.append((key, doc))
                except Exception:
                    continue

            if not pending:
                logger.debug("Loop 2g: nessuna correzione pending")
                return

            pending.sort(key=lambda x: x[1].get("created_at", 0))
            pending = pending[:MAX_PER_CYCLE]

            counts = {"not_a_correction": 0, "bad_memory": 0, "bad_reasoning": 0, "ambiguous": 0}

            for key, doc in pending:
                # Recupera contenuti delle memorie iniettate
                ctx_memories = []
                for mid in doc.get("rag_ctx_ids", []):
                    if not mid:
                        continue
                    mkey = mid if mid.startswith("euri:memory:") else f"euri:memory:{mid}"
                    try:
                        m = self._r.json().get(mkey, "$")
                        if m and m[0].get("content"):
                            ctx_memories.append(m[0]["content"][:200])
                    except Exception:
                        continue

                verdict = self._llm_classify_correction(
                    doc.get("prompt_original", ""),
                    doc.get("risposta_euri", ""),
                    doc.get("correzione_user", ""),
                    ctx_memories,
                )

                counts[verdict] = counts.get(verdict, 0) + 1

                # Aggiorna lo status del signal. not_a_correction = soft-delete:
                # status "dismissed" (audit preservato, niente azione, il signal
                # evapora col suo TTL 30gg). Nessuna lesson generata: è il fix N1.
                self._r.json().set(key, "$.status", "dismissed" if verdict == "not_a_correction" else "analyzed")
                self._r.json().set(key, "$.verdict", verdict)
                self._r.json().set(key, "$.analyzed_at", time.time())

                # Azioni differenziate (not_a_correction e ambiguous: nessuna azione)
                if verdict == "bad_memory":
                    for mid in doc.get("rag_ctx_ids", []):
                        if not mid:
                            continue
                        mkey = mid if mid.startswith("euri:memory:") else f"euri:memory:{mid}"
                        try:
                            # Incremento atomico (come recalled_count): elimina la race del
                            # read-modify-write. audit_flag non è inizializzato in save_memory,
                            # quindi lo si crea a 0 solo se assente (SET NX, idempotente) e poi
                            # lo si incrementa con NUMINCRBY: due correzioni concorrenti sulla
                            # stessa memoria non perdono più un incremento.
                            self._r.json().set(mkey, "$.audit_flag", 0, nx=True)
                            self._r.json().numincrby(mkey, "$.audit_flag", 1)
                        except Exception:
                            continue

                elif verdict == "bad_reasoning" and self._memory_manager:
                    # La correzione utente diventa una lesson — passive memory.
                    lesson_text = doc.get("correzione_user", "").strip()
                    if lesson_text and len(lesson_text) > 10:
                        try:
                            self._memory_manager.save_memory(
                                content=lesson_text,
                                category="lesson",
                                tags=["lesson", "from_correction"],
                                source="passive",
                            )
                        except Exception as e:
                            logger.debug(f"Loop 2g: errore salvataggio lesson — {e}")

                logger.info(f"Loop 2g: {key[-8:]} → {verdict}")

            logger.info(
                f"Loop 2g: {len(pending)} signal analizzati "
                f"({counts['not_a_correction']} not_a_correction/scartati, {counts['bad_memory']} bad_memory, "
                f"{counts['bad_reasoning']} bad_reasoning, {counts['ambiguous']} ambiguous)"
            )

        except Exception as e:
            logger.error(f"Errore Loop 2g audit corrections: {e}")

    # ── Plausibility Gate: flag-only su fatti tecnici ──────────────────────

    # Soglie di flag per verdetto: 'impossible' richiede alta confidenza; 'suspicious' una
    # soglia più bassa, perché su memorie tecniche stringate il modello resta cauto anche su
    # errori reali (caso 332e18b6: 'bicarbonato di calcio' → suspicious 0.70). Sempre flag-only.
    _PLAUSIBILITY_FLOORS = {"impossible": 0.82, "suspicious": 0.70}

    @classmethod
    def _plausibility_should_flag(cls, verdict: str, confidence) -> bool:
        """True se (verdetto, confidenza) supera la soglia di flag per quel verdetto.
        Unico punto di verità della regola — usato dal pass e dal test, niente drift."""
        floor = cls._PLAUSIBILITY_FLOORS.get((verdict or "").strip().lower())
        try:
            conf = float(confidence)
        except (TypeError, ValueError):
            conf = 0.0
        return floor is not None and conf >= floor

    def _llm_plausibility_check(self, content: str, domain: str) -> dict:
        """
        Chiede al modello notturno già caldo se una memoria tecnica contiene un fatto
        fisicamente/chimicamente/tecnicamente implausibile. Non corregge: produce solo un
        giudizio strutturato che il pass userà come soft flag.
        """
        prompt = f"""\
Valuta la plausibilità tecnica della seguente memoria di Euri.

Dominio: {domain}
Memoria: "{content[:700]}"

Regole:
- Devi segnalare SOLO impossibilità tecniche/chimiche/fisiche chiare o sospetti forti.
- NON correggere conoscenza pratica di Stefano solo perché è insolita o non scolastica.
- Se il dato può essere vero in un contesto industriale, anche se raro, considera PLAUSIBILE.
- Se manca contesto, usa INCERTO.
- Esempio di IMPOSSIBILE: "bicarbonato di calcio" usato come filler minerale solido in compound polimerici.
- Esempio da NON bocciare automaticamente: additivi, blend o scelte di processo insolite ma industrialmente possibili.

Rispondi SOLO con JSON valido:
{{"verdict":"plausible|suspicious|impossible|uncertain","confidence":0.0,"reason":"breve motivo"}}"""
        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
                _timeout=90,
            )
            raw = (response.message.content or "").strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {}
            data = json.loads(raw[start:end + 1])
            if not isinstance(data, dict):
                return {}
            return {
                "verdict": str(data.get("verdict", "")).strip().lower(),
                "confidence": data.get("confidence", 0.0),
                "reason": str(data.get("reason", "")).strip(),
            }
        except Exception as e:
            logger.debug(f"Plausibility gate: errore LLM check — {e}")
            return {}

    def _plausibility_gate_pass(self):
        """
        Plausibility gate — flag-only.

        Cerca poche memorie tecniche/numeriche non ancora controllate e chiede al modello
        notturno se contengono impossibilità oggettive. Non modifica il contenuto, non
        supersede, non cancella: alza plausibility_flag + audit_flag solo ad alta confidenza.

        Questo colma il buco emerso dal caso 332e18b6: Qwen sa che il bicarbonato di calcio
        non è un filler minerale solido, ma nessun loop glielo chiedeva.
        """
        CHECKED_KEY = "euri:plausibility:checked"
        MAX_PER_CYCLE = 8
        SKIP_SOURCES = {"web", "reflection", "conversation"}

        try:
            candidates = []
            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    content = (doc.get("content") or "").strip()
                    if not content:
                        continue
                    if doc.get("superseded_by"):
                        continue
                    if doc.get("source") in SKIP_SOURCES:
                        continue
                    if not doc.get("requires_verification"):
                        continue
                    if doc.get("plausibility_flag"):
                        continue
                    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
                    checked_id = f"{doc.get('id', '')}:{digest}"
                    if self._r.sismember(CHECKED_KEY, checked_id):
                        continue
                    score = (
                        int(doc.get("recalled_count") or 0),
                        float(doc.get("created_at") or 0),
                    )
                    candidates.append((score, key, doc, checked_id))
                except Exception:
                    continue

            if not candidates:
                logger.debug("Plausibility gate: nessun candidato tecnico da controllare")
                return

            # Prima le memorie più richiamate: sono quelle che rischiano di avvelenare il RAG.
            candidates.sort(key=lambda x: x[0], reverse=True)
            candidates = candidates[:MAX_PER_CYCLE]

            checked = 0
            flagged = 0
            for _score, key, doc, checked_id in candidates:
                result = self._llm_plausibility_check(
                    doc.get("content", ""), doc.get("domain", "generale")
                )
                self._r.sadd(CHECKED_KEY, checked_id)
                self._r.expire(CHECKED_KEY, 180 * 86400)
                checked += 1

                verdict = (result.get("verdict") or "").strip().lower()
                try:
                    confidence = float(result.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                reason = (result.get("reason") or "").strip()

                if not self._plausibility_should_flag(verdict, confidence):
                    continue

                flag = {
                    "verdict": verdict,
                    "confidence": confidence,
                    "reason": reason[:500],
                    "checked_at": time.time(),
                    "source": "plausibility_gate",
                }
                self._r.json().set(key, "$.plausibility_flag", flag)
                self._r.json().set(key, "$.audit_flag", 0, nx=True)
                self._r.json().numincrby(key, "$.audit_flag", 1)
                flagged += 1
                logger.info(
                    f"Plausibility gate: {doc.get('id', '')[:8]}… → {verdict} "
                    f"({confidence:.2f}) — {reason[:120]}"
                )

            logger.info(f"Plausibility gate: {checked} controllate, {flagged} flaggate")

        except Exception as e:
            logger.error(f"Errore plausibility gate: {e}")

    def _cleanup_expired_insights(self):
        """
        Gate 1 — demotion (14 giorni senza richiamo): PROMOTED → candidate (seconda chance).
        Gate 2 — evaporazione (30 giorni senza richiamo): elimina definitivamente.
        """
        try:
            ts_now = to_timestamp(now())
            demote_cutoff = ts_now - config.INSIGHT_DEMOTE_DAYS * 86400
            delete_cutoff  = ts_now - config.INSIGHT_TTL_DAYS  * 86400

            # Gate 1: tra DEMOTE_DAYS e TTL_DAYS → torna candidate
            # Esclude insight promossi nelle ultime 24h (promoted_at recente)
            # per evitare il bug promote-then-demote nello stesso ciclo.
            q_demote = Query(
                f"@status:{{promoted}} @recalled_count:[0 0] "
                f"@created_at:[{delete_cutoff} {demote_cutoff}]"
            )
            res_demote = self._r.ft("idx:insights").search(q_demote)
            for doc in res_demote.docs:
                promoted_at_val = self._r.json().get(doc.id, "$.promoted_at")
                promoted_ts = promoted_at_val[0] if promoted_at_val else 0
                if promoted_ts and (ts_now - promoted_ts) < 86400:
                    logger.debug(f"Dream Engine: skip demotion — promosso da meno di 24h ({doc.id[-8:]})")
                    continue
                self._r.json().set(doc.id, "$.status", "candidate")
                self._r.json().set(doc.id, "$.convergence_count", 1)
                # Marca: "hai già avuto i tuoi 14 giorni di vetrina senza essere usato".
                # Il gate in _evaluate_insights impedirà la re-promozione per sola
                # convergenza (opzione b) — torna a vivere solo se la realtà ti richiama.
                self._r.json().set(doc.id, "$.demoted_once", True)
                logger.info(f"Dream Engine: Insight retrocesso a candidate (ID: {doc.id})")
                pulse_emit(self._r, "insight", "intero", "demoted",
                           payload={"id": str(doc.id)[-12:]}, salience=0.5)

            # Gate 2: più vecchio di TTL_DAYS → elimina
            q_delete = Query(
                f"@status:{{promoted}} @recalled_count:[0 0] "
                f"@created_at:[-inf {delete_cutoff}]"
            )
            res_delete = self._r.ft("idx:insights").search(q_delete)
            for doc in res_delete.docs:
                self._r.delete(doc.id)
                logger.info(f"Dream Engine: Insight evaporato (ID: {doc.id})")

            # Gate 3: candidate mai promossi oltre TTL_DAYS → elimina
            q_stale = Query(
                f"@status:{{candidate}} @created_at:[-inf {delete_cutoff}]"
            )
            res_stale = self._r.ft("idx:insights").search(q_stale)
            for doc in res_stale.docs:
                self._r.delete(doc.id)
                logger.info(f"Dream Engine: Candidate scaduto eliminato (ID: {doc.id})")

        except Exception as e:
            logger.error(f"Errore pulizia insights: {e}")

    def _cleanup_stale_memories(self):
        """
        Rete di sicurezza: elimina memorie passive/reflection/conversation mai
        richiamate solo quando hanno SUPERATO la propria expires_at.

        TTL Redis = fonte di verità operativa (Redis cancella da solo alla scadenza);
        questa pulizia interviene solo sugli orfani che hanno perso il TTL Redis ma
        hanno una expires_at già passata. NON usa più created_at: così non contraddice
        il verdetto KEEP del death-row gate (Loop 2d), che estende expires_at nel futuro.
        Le memorie user/teach/obsidian_vault non vengono mai toccate automaticamente.
        recalled_count non è indicizzato in RediSearch — la scan è necessaria.
        """
        _EPHEMERAL_SOURCES = {"passive", "reflection", "conversation"}
        try:
            now_ts = to_timestamp(now())
            evaporated = 0

            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    if doc.get("source") not in _EPHEMERAL_SOURCES:
                        continue
                    if doc.get("recalled_count", 0) > 0:
                        continue
                    # Cancella solo ciò che ha superato la propria expires_at (mirror del
                    # TTL Redis). Niente expires_at o scadenza nel futuro → si conserva:
                    # rispetta il KEEP del Loop 2d e non cancella per età grezza (created_at).
                    exp = doc.get("expires_at")
                    if not exp or exp > now_ts:
                        continue
                    self._r.delete(key)
                    evaporated += 1
                    logger.debug(f"Dream Engine: memoria stantia evaporata ({key})")
                except Exception:
                    continue

            if evaporated:
                logger.info(f"Dream Engine: {evaporated} memorie stantie evaporate")

        except Exception as e:
            logger.error(f"Errore pulizia memorie stantie: {e}")

    def _pruning_pass(self):
        """
        Loop 2d — Death-row gate: memorie in scadenza entro 7 giorni ricevono una
        valutazione LLM prima di essere eliminate.

        Logica:
        - recalled_count >= MEMORY_KEEP_IF_RECALLED → estendi TTL senza chiamare LLM
        - recalled_count == 0 → chiedi al LLM: KEEP (estendi) o DROP (elimina)
        - Errore LLM → conserva per sicurezza
        """
        if not self._brain:
            return
        try:
            from core.memory_manager import _TTL_BY_SOURCE
            from datetime import timedelta
            from utils.date_utils import to_timestamp

            keep_threshold = config.MEMORY_KEEP_IF_RECALLED
            days_ahead = 7
            cutoff_near = to_timestamp(now() + timedelta(days=days_ahead))
            now_ts = to_timestamp(now())

            kept = dropped = extended = 0

            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    exp = doc.get("expires_at")
                    if not exp or not (now_ts < exp <= cutoff_near):
                        continue

                    source = doc.get("source", "")
                    if source not in _TTL_BY_SOURCE:
                        continue  # user/teach/obsidian_vault — mai toccare

                    recalled = doc.get("recalled_count", 0)
                    ttl_days = _TTL_BY_SOURCE[source]

                    if recalled >= keep_threshold:
                        # Estendi di almeno 30 giorni — episodi hanno ttl=7 e
                        # rientrerebbero nella finestra ogni ciclo senza questo floor.
                        extended_ttl = max(ttl_days, 30)
                        new_exp_dt = now() + timedelta(days=extended_ttl)
                        # TTL Redis = verità operativa, expires_at = mirror di audit:
                        # vanno aggiornati insieme o la chiave muore alla vecchia scadenza.
                        self._r.json().set(key, "$.expires_at", to_timestamp(new_exp_dt))
                        self._r.expireat(key, new_exp_dt)
                        extended += 1
                        continue

                    # Death-row: chiedi al LLM
                    verdict = self._brain.evaluate_memory_relevance(doc.get("content", ""))
                    if verdict == "KEEP":
                        new_exp_dt = now() + timedelta(days=ttl_days)
                        # TTL Redis = verità operativa, expires_at = mirror di audit.
                        self._r.json().set(key, "$.expires_at", to_timestamp(new_exp_dt))
                        self._r.expireat(key, new_exp_dt)
                        kept += 1
                        logger.debug(f"Loop 2d: memoria salvata dal giudice LLM ({key[-8:]})")
                    else:
                        self._r.delete(key)
                        dropped += 1
                        logger.info(f"Loop 2d: memoria eliminata dal giudice LLM ({key[-8:]})")

                except Exception:
                    continue

            if kept or dropped or extended:
                logger.info(f"Loop 2d: {extended} estese, {kept} salvate LLM, {dropped} eliminate LLM")

        except Exception as e:
            logger.error(f"Errore Loop 2d pruning: {e}")

    # ── Loop 2e: Memory Consolidation ─────────────────────────────────────────

    def _same_subject_gate(self, cluster: list[dict], domain: str, seed_id: str = "") -> list[dict]:
        """
        GATE prima della sintesi Loop 2e (D incrementale): tiene solo i frammenti che
        parlano dello STESSO soggetto del seed, per non consolidare entità distinte in un
        unico nodo (caso Poseidon↔Gamma: pallet a iniezione fuso con linea di estrusione).
        Filtra l'INPUT della sintesi → anche consolidated_from risulta coerente. Usa il
        modello notturno GIÀ CALDO (dream_client via _ollama_chat): niente secondo modello,
        niente swap. Fail-open: su errore/risposta ambigua ritorna il cluster invariato.

        Il frammento 1 è SEMPRE il seed: il cluster viene riordinato col seed in testa (sort
        stabile), così non si dipende dall'ordine KNN. seed_id vuoto → ordine invariato.
        """
        ordered = sorted(cluster, key=lambda d: d.get("id") != seed_id) if seed_id else list(cluster)
        items = ordered[:5]
        if len(items) < 2:
            return cluster
        import re as _re
        listing = "\n".join(
            f"{i+1}. {(it.get('content') or '').strip()[:200]}" for i, it in enumerate(items)
        )
        prompt = (
            f"Frammenti di memoria nel dominio \"{domain}\":\n{listing}\n\n"
            f"Il frammento 1 fissa il SOGGETTO. Quali frammenti parlano dello STESSO "
            f"soggetto/entità del frammento 1? Escludi quelli su entità diverse, anche se "
            f"correlate (es. un prodotto vs un impianto diverso).\n"
            f"Rispondi SOLO con gli indici da TENERE separati da virgola (includi sempre 1). "
            f"Esempio: 1,3"
        )
        try:
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 20},
                think=False,
                _timeout=60,
            )
            out = _re.sub(r"<think>.*?</think>", "", resp.message.content or "", flags=_re.DOTALL)
            idxs = {int(n) for n in _re.findall(r"\d+", out)}
            keep = [items[i - 1] for i in sorted(idxs) if 1 <= i <= len(items)]
            # Fail-open: risposta vuota o che esclude il seed → non filtrare.
            if not keep or 1 not in idxs:
                return cluster
            if len(keep) < len(items):
                dropped = len(items) - len(keep)
                logger.info(f"Loop 2e gate: {dropped} frammento/i di soggetto diverso esclusi dal consolidamento")
            return keep
        except Exception as e:
            logger.debug(f"Loop 2e same-subject gate fallito (fail-open): {e}")
            return cluster

    def _consolidation_pass(self):
        """
        Loop 2e — Consolidamento semantico delle memorie.
        Raggruppa memorie dello stesso dominio con recalled_count >= 3 e
        requires_verification = False in documenti sintetici più ricchi.
        Ispirato al consolidamento ippocampale durante il sonno: i frammenti
        episodici vengono integrati in nodi di conoscenza semantica stabile.
        Max 3 consolidazioni per ciclo. Gira max una volta ogni 24h.
        """
        if not self._embedder or not self._embedder.available:
            return
        if not self._memory_manager:
            return

        PROCESSED_KEY = "euri:loop2e:processed"
        MIN_RECALLED = 3
        MIN_CLUSTER = 3
        MAX_PER_CYCLE = 3
        SKIP_SOURCES = {"loop2e", "campus", "web", "reflection"}

        try:
            # 1. Raccogli candidati: recalled_count >= 3, no verifica numerica, no loop2e
            candidates = []
            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    d = self._r.json().get(key, "$")
                    if not d:
                        continue
                    doc = d[0]
                    if doc.get("source") in SKIP_SOURCES:
                        continue
                    if doc.get("requires_verification"):
                        continue
                    if doc.get("recalled_count", 0) < MIN_RECALLED:
                        continue
                    candidates.append(doc)
                except Exception:
                    continue

            if not candidates:
                logger.debug("Loop 2e: nessun candidato qualificato")
                return

            # Indice rapido degli ID qualificati — recalled_count non è nel
            # schema RediSearch quindi non possiamo filtrare via KNN return_fields.
            qualified_by_id = {doc.get("id", ""): doc for doc in candidates}

            consolidated = 0

            for seed in candidates:
                if consolidated >= MAX_PER_CYCLE:
                    break

                seed_id = seed.get("id", "")
                seed_domain = seed.get("domain", "generale")
                seed_emb = seed.get("embedding")
                if not seed_emb or seed_domain == "generale":
                    continue

                # 2. Trova memorie simili nello stesso dominio via KNN
                # Usiamo una connessione raw (decode_responses=False) perché
                # passare vec_bytes come query_params fallisce silenziosamente
                # quando il client principale ha decode_responses=True.
                try:
                    vec = self._embedder.encode(seed.get("content", ""), mode="query")
                    if vec is None:
                        continue
                    vec_bytes = vec.astype("float32").tobytes()
                    safe_domain = seed_domain.replace(" ", "\\ ")
                    q = (
                        Query(f"(@domain:{{{safe_domain}}})=>[KNN 6 @embedding $vec AS score]")
                        .sort_by("score")
                        .return_fields("id", "content", "recalled_count", "requires_verification", "source", "domain")
                        .dialect(2)
                    )
                    import redis as _redis_mod
                    _raw_r = _redis_mod.Redis(
                        host=config.REDIS_HOST, port=config.REDIS_PORT,
                        db=config.REDIS_DB, decode_responses=False,
                    )
                    res = _raw_r.ft("idx:memories").search(q, query_params={"vec": vec_bytes})
                except Exception:
                    continue

                def _dec(v, default=""):
                    if v is None:
                        return default
                    return v.decode() if isinstance(v, bytes) else str(v)

                # 3. Filtra: mantieni solo vicini KNN presenti in qualified_by_id
                # (recalled_count non è nel schema RediSearch, filtriamo dall'indice pre-costruito)
                cluster = []
                for doc in res.docs:
                    did = _dec(doc.id).replace("euri:memory:", "")
                    if did in qualified_by_id:
                        qd = qualified_by_id[did]
                        cluster.append({"id": did, "content": qd.get("content", "")})

                if len(cluster) < MIN_CLUSTER:
                    continue

                # 3b. GATE same-subject PRIMA della sintesi: filtra i frammenti che non
                # parlano dello stesso soggetto del seed (anti-conflazione Poseidon↔Gamma).
                # Così fingerprint, consolidated_from e sintesi usano solo i coerenti.
                cluster = self._same_subject_gate(cluster, seed_domain, seed_id)
                if len(cluster) < MIN_CLUSTER:
                    continue

                # 4. Fingerprint cluster — evita ri-consolidare lo stesso gruppo
                cluster_ids = sorted(doc["id"] for doc in cluster[:5])
                fingerprint = "|".join(cluster_ids)
                if self._r.sismember(PROCESSED_KEY, fingerprint):
                    continue

                # 5. Deduplicazione semantica — salta se esiste già un nodo loop2e simile
                if self._loop2e_duplicate_exists(seed_domain, vec):
                    continue

                # 6. Chiedi a Qwen di sintetizzare
                import re as _re
                _TS_PAT = _re.compile(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\b')
                memories_text = "\n".join(
                    f"- {_TS_PAT.sub('', doc['content']).strip()[:300]}"
                    for doc in cluster[:5]
                )
                prompt = f"""Hai queste memorie correlate nel dominio "{seed_domain}":

{memories_text}

Sintetizzale in un unico blocco di conoscenza strutturata.
Regole:
- Mantieni tutti i dati specifici: numeri, nomi propri, valori, misure
- Elimina ripetizioni e ridondanze
- Scrivi in italiano, massimo 5 frasi dense
- Nessuna interpretazione, solo sintesi dei fatti
- Non includere date o timestamp delle memorie sorgente
- Se le memorie si contraddicono su un dato numerico, scrivi "dato non certo"
Rispondi SOLO con la sintesi. Niente intestazioni."""

                try:
                    resp = self._ollama_chat(
                        model=config.DREAM_OLLAMA_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        options={"temperature": 0.2, "num_predict": 600},
                        think=False,
                        _timeout=90,
                    )
                    synthesis = resp.message.content or ""
                    if "<channel|>" in synthesis:
                        synthesis = synthesis.split("<channel|>", 1)[-1]
                    synthesis = _re.sub(r"<think>.*?</think>", "", synthesis, flags=_re.DOTALL).strip()
                except Exception as e:
                    logger.debug(f"Loop 2e: LLM timeout/errore — {e}")
                    continue

                if not synthesis or len(synthesis) < 30:
                    continue

                # 7. Salva memoria consolidata (senza TTL — permanente come memorie utili)
                mid = self._memory_manager.save_memory(
                    content=synthesis,
                    category="consolidato",
                    tags=["consolidated"],
                    source="loop2e",
                    expires_at=None,
                )
                key = f"euri:memory:{mid}"
                self._r.json().set(key, "$.consolidated_from", cluster_ids)
                risk = self._consolidation_source_risk(cluster_ids, qualified_by_id)
                self._r.json().set(key, "$.consolidation_risk", risk)
                if risk["level"] != "ok":
                    self._r.json().set(key, "$.source_audit_flags", risk["audit_flagged"])

                # Eredita requires_verification se almeno una memoria sorgente ce l'ha
                sources_rv = any(
                    qualified_by_id.get(cid, {}).get("requires_verification", False)
                    for cid in cluster_ids
                )
                if sources_rv:
                    self._r.json().set(key, "$.requires_verification", True)

                # 8. Marca cluster come processato (TTL 180 giorni sliding)
                self._r.sadd(PROCESSED_KEY, fingerprint)
                self._r.expire(PROCESSED_KEY, 180 * 86400)

                # 9. Aggiungi wiki-link sorgenti in Obsidian
                self._write_obsidian_sources(mid, seed_domain, cluster_ids)

                consolidated += 1
                logger.info(
                    f"Loop 2e: consolidate {len(cluster)} memorie → {mid[:8]}… "
                    f"(dominio: {seed_domain})"
                )
                pulse_emit(self._r, "consolidation", "intero", "consolidated",
                           payload={"n": len(cluster), "domain": seed_domain}, salience=0.4)

            if consolidated:
                logger.info(f"Loop 2e: {consolidated} consolidazioni completate")
            else:
                logger.debug("Loop 2e: nessuna consolidazione necessaria in questo ciclo")

        except Exception as e:
            logger.error(f"Errore Loop 2e consolidation: {e}")

    def _consolidation_source_risk(self, cluster_ids: list[str], source_docs: dict[str, dict] | None = None) -> dict:
        """Fotografa la fragilità delle fonti usate da un nodo loop2e."""
        source_docs = source_docs or {}
        risk = {
            "level": "ok",
            "total_sources": len(cluster_ids),
            "audit_flagged": [],
            "superseded": [],
            "missing": [],
            "requires_verification": [],
        }

        for cid in cluster_ids:
            doc = source_docs.get(cid)
            if doc is None:
                try:
                    raw = self._r.json().get(f"euri:memory:{cid}", "$")
                    doc = raw[0] if raw else None
                except Exception:
                    doc = None
            if not doc:
                risk["missing"].append(cid)
                continue
            if int(doc.get("audit_flag") or 0) > 0:
                risk["audit_flagged"].append(cid)
            if doc.get("superseded_by"):
                # Lista di id-stringa, identica alla forma calcolata dal backfill in
                # scripts/audit_memory.py: così il campo consolidation_risk persistito è
                # coerente tra loop live e audit, e il backfill non ri-sporca nodi già scritti.
                risk["superseded"].append(cid)
            if doc.get("requires_verification"):
                risk["requires_verification"].append(cid)

        if risk["missing"] or risk["superseded"] or risk["requires_verification"]:
            risk["level"] = "high"
        elif risk["audit_flagged"]:
            risk["level"] = "watch"
        return risk

    def _write_obsidian_sources(self, mid: str, domain: str, cluster_ids: list[str]):
        """Appende i wiki-link alle memorie sorgente nel file Obsidian del nodo consolidato."""
        if not config.OBSIDIAN_SYNC_ENABLED:
            return
        try:
            from pathlib import Path
            from utils.date_utils import from_timestamp

            vault_path = Path(config.OBSIDIAN_VAULT_PATH)

            # Trova il file Obsidian del nodo consolidato
            self_doc = self._r.json().get(f"euri:memory:{mid}", "$")
            if not self_doc:
                return
            dt = from_timestamp(self_doc[0]["created_at"])
            node_file = vault_path / "Memories" / domain / f"Memory_{dt.strftime('%Y%m%d_%H%M%S')}_{mid[:8]}.md"
            if not node_file.exists():
                return

            # Costruisce wiki-link per ogni memoria sorgente
            links = []
            for cid in cluster_ids:
                src = self._r.json().get(f"euri:memory:{cid}", "$")
                if not src:
                    continue
                src = src[0]
                src_dt = from_timestamp(src["created_at"])
                src_domain = src.get("domain", "generale")
                src_name = f"Memory_{src_dt.strftime('%Y%m%d_%H%M%S')}_{cid[:8]}"
                links.append(f"- [[{src_name}]] — {src_domain}")

            if not links:
                return

            existing = node_file.read_text(encoding="utf-8")
            section = "\n\n## Fonti consolidate\n" + "\n".join(links)
            node_file.write_text(existing + section, encoding="utf-8")
            logger.debug(f"Loop 2e: wiki-link aggiunti in Obsidian per {mid[:8]}")
        except Exception as e:
            logger.debug(f"Loop 2e: errore link Obsidian — {e}")

    def _loop2e_duplicate_exists(self, domain: str, vec) -> bool:
        """
        Ritorna True se esiste già un nodo loop2e semanticamente quasi identico
        nello stesso dominio (distanza cosine < 0.15).
        Previene la proliferazione di nodi ridondanti tra cicli notturni.
        """
        try:
            import redis as _redis_mod
            _raw_r = _redis_mod.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT,
                db=config.REDIS_DB, decode_responses=False,
            )
            safe_domain = domain.replace(" ", "\\ ")
            q = (
                Query(f"(@domain:{{{safe_domain}}} @source:{{loop2e}})=>[KNN 3 @embedding $vec AS dup_score]")
                .sort_by("dup_score")
                .return_fields("dup_score")
                .dialect(2)
            )
            res = _raw_r.ft("idx:memories").search(
                q, query_params={"vec": vec.astype("float32").tobytes()}
            )
            for doc in res.docs:
                raw = getattr(doc, "dup_score", b"1.0")
                score = float(raw.decode() if isinstance(raw, bytes) else raw)
                if score < 0.15:
                    return True
            return False
        except Exception:
            return False
