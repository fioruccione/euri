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
    def __init__(self, r, embedder, brain=None):
        self._r = r
        self._embedder = embedder
        self._brain = brain  # usato dal Loop 2d (death-row gate)
        self._running = False
        self._thread = None
        self._lock = threading.Lock()
        
        # Traccia l'ultimo activity (STT/TTS) globale di Euri
        # Usa time.time() (wall-clock) e non time.monotonic() perché
        # monotonic si resetta quando il PC va in sospensione.
        self._last_activity = time.time()
        
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
            if dream and dream.get("status") == "candidate":
                # 3. Loop 2c: Valutazione Insight
                self._evaluate_insights()
                
            # 4. Pulizia Insight scaduti
            self._cleanup_expired_insights()

            # 5. Pulizia Memorie stantie (passive/reflection mai richiamate)
            self._cleanup_stale_memories()

            # 6. Loop 2d: Death-row gate per memorie in scadenza entro 7 giorni
            self._pruning_pass()

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
Sei un motore cognitivo analogico. Il tuo compito è trovare isomorfismi strutturali tra due memorie di domini distinti.

PROCESSO:
1. Astrai ogni memoria alla sua struttura logica essenziale, ignorando i dettagli di dominio.
2. Cerca se le due strutture condividono la stessa dinamica sottostante: stesso vincolo, stesso meccanismo causale, stessa legge emergente.
3. Se le memorie sono temporalmente distanti, considera anche se una rappresenta un'evoluzione o una risposta all'altra.
4. Formula il principio generale che li governa entrambi.

PREFERISCI analogie non ovvie — evita connessioni banali del tipo "entrambi sono processi". Cerca il meccanismo profondo, non la somiglianza superficiale.

Memoria A ({label_a}):
"{mem_a['content']}"

Memoria B ({label_b}):
"{mem_b['content']}"

Se proprio non esiste nessuna connessione sensata, rispondi SOLO: "NESSUN INSIGHT".
Altrimenti formula l'insight come principio generale in UNA sola frase concisa."""

        try:
            response = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.6, "num_predict": 2000},
                think=True,
            )
            text = response.message.content or ""
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1]
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            
            status = "discarded"
            insight_content = ""
            
            if text and "NESSUN INSIGHT" not in text.upper() and len(text) > 15:
                status = "candidate"
                insight_content = text
                logger.info(f"Dream Engine: generato CANDIDATE Insight → {insight_content[:50]}...")
            else:
                logger.debug("Dream Engine: sogno scartato (nessun isomorfismo)")
                
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
                vec = self._embedder.encode(insight_content)
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
                convergences = int(getattr(doc, "convergence_count", 1))
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
        """Gli insight non utilizzati (PROMOTED ma con recalled_count=0) evaporano dopo TTL."""
        try:
            ttl_sec = config.INSIGHT_TTL_DAYS * 86400
            cutoff = to_timestamp(now()) - ttl_sec

            q = Query(f"@status:{{promoted}} @recalled_count:[0 0] @created_at:[-inf {cutoff}]")
            res = self._r.ft("idx:insights").search(q)

            for doc in res.docs:
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
                        # Abbastanza richiamate — estendi senza LLM
                        new_exp = to_timestamp(now() + timedelta(days=ttl_days))
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
