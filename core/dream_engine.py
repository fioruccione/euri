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
import numpy as np
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from loguru import logger
import ollama

import config
from utils.date_utils import now, to_timestamp
from redis.commands.search.query import Query
from utils.obsidian_sync import write_insight


class DreamEngine:
    def __init__(self, r, embedder, brain=None, memory=None):
        self._r = r
        self._embedder = embedder
        self._brain = brain        # usato dal Loop 2d (death-row gate)
        self._memory_manager = memory  # usato dal Bridge Synthesis
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        
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
        """Wrapper con timeout (default 150s) attorno a ollama.chat — evita hang notturni."""
        timeout = kwargs.pop("_timeout", 150)
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(ollama.chat, **kwargs)
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
                
            # 4. Pulizia Insight scaduti
            self._cleanup_expired_insights()

            # 5. Pulizia Memorie stantie (passive/reflection mai richiamate)
            self._cleanup_stale_memories()

            # 6. Loop 2d: Death-row gate per memorie in scadenza entro 7 giorni
            self._pruning_pass()

            # 7. Loop 2e: Memory Consolidation — max una volta ogni 24h
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
            
            has_structure = (
                "connessione operativa" in text.lower()
                and "nel dominio" in text.lower()
            )
            if text and "NESSUN INSIGHT" not in text.upper() and has_structure:
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
            q = Query("@status:{candidate}").return_fields("id", "content", "embedding", "convergence_count").paging(0, 100)
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
                    # Promuovi questo a PROMOTED
                    self._r.json().set(doc.id, "$.status", "promoted")
                    self._r.json().set(doc.id, "$.convergence_count", convergences)
                    
                    # Rimuovi i duplicati assorbiti
                    for sid in similar_ids:
                        self._r.delete(sid)
                        
                    logger.success(f"Dream Engine: Insight PROMOSSO! (convergenze: {convergences})")
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
            q_demote = Query(
                f"@status:{{promoted}} @recalled_count:[0 0] "
                f"@created_at:[{delete_cutoff} {demote_cutoff}]"
            )
            res_demote = self._r.ft("idx:insights").search(q_demote)
            for doc in res_demote.docs:
                self._r.json().set(doc.id, "$.status", "candidate")
                self._r.json().set(doc.id, "$.convergence_count", 1)
                logger.info(f"Dream Engine: Insight retrocesso a candidate (ID: {doc.id})")

            # Gate 2: più vecchio di TTL_DAYS → elimina
            q_delete = Query(
                f"@status:{{promoted}} @recalled_count:[0 0] "
                f"@created_at:[-inf {delete_cutoff}]"
            )
            res_delete = self._r.ft("idx:insights").search(q_delete)
            for doc in res_delete.docs:
                self._r.delete(doc.id)
                logger.info(f"Dream Engine: Insight evaporato (ID: {doc.id})")

        except Exception as e:
            logger.error(f"Errore pulizia insights: {e}")

    def _cleanup_stale_memories(self):
        """
        Elimina memorie passive/reflection mai richiamate dopo MEMORY_TTL_PASSIVE_DAYS.
        Le memorie user/teach/obsidian_vault non vengono mai toccate automaticamente:
        sono state salvate con intenzione esplicita e non hanno data di scadenza.
        recalled_count non è indicizzato in RediSearch — la scan è necessaria.
        """
        _EPHEMERAL_SOURCES = {"passive", "reflection", "conversation"}
        try:
            ttl_sec = config.MEMORY_TTL_PASSIVE_DAYS * 86400
            cutoff = to_timestamp(now()) - ttl_sec
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
                    if doc.get("created_at", 0) > cutoff:
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
                        new_exp = to_timestamp(now() + timedelta(days=extended_ttl))
                        self._r.json().set(key, "$.expires_at", new_exp)
                        extended += 1
                        continue

                    # Death-row: chiedi al LLM
                    verdict = self._brain.evaluate_memory_relevance(doc.get("content", ""))
                    if verdict == "KEEP":
                        new_exp = to_timestamp(now() + timedelta(days=ttl_days))
                        self._r.json().set(key, "$.expires_at", new_exp)
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

                # Eredita requires_verification se almeno una memoria sorgente ce l'ha
                sources_rv = any(
                    qualified_by_id.get(cid, {}).get("requires_verification", False)
                    for cid in cluster_ids
                )
                if sources_rv:
                    self._r.json().set(key, "$.requires_verification", True)

                # 8. Marca cluster come processato
                self._r.sadd(PROCESSED_KEY, fingerprint)

                # 9. Aggiungi wiki-link sorgenti in Obsidian
                self._write_obsidian_sources(mid, seed_domain, cluster_ids)

                consolidated += 1
                logger.info(
                    f"Loop 2e: consolidate {len(cluster)} memorie → {mid[:8]}… "
                    f"(dominio: {seed_domain})"
                )

            if consolidated:
                logger.info(f"Loop 2e: {consolidated} consolidazioni completate")
            else:
                logger.debug("Loop 2e: nessuna consolidazione necessaria in questo ciclo")

        except Exception as e:
            logger.error(f"Errore Loop 2e consolidation: {e}")

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
