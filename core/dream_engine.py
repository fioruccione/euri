"""
Dream Engine — cicli cognitivi in idle.

Il Dream Engine gira in background quando Euri è inattiva. Non è più un blocco
"notturno" unico: l'orchestratore separa pass leggeri, sogni creativi e
manutenzione lenta, così correzioni/ipotesi possono maturare durante la giornata
senza far partire sempre consolidamento e cleanup.
"""
import time
import threading
import uuid
import json
import hashlib
import re
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
from core.memory_attention import (
    remove_loop2e_candidate,
    scan_loop2e_candidates,
    update_loop2e_candidate_index,
    zset_loop2e_candidates,
)


CROSS_EPISODE_SEEN_KEY = "euri:cross_episode:seen"
CROSS_EPISODE_LAST_RUN_KEY = "euri:cross_episode:last_run_ts"

_CAUSAL_EPISODE_RE = re.compile(
    r"\b(?:causa|causato|causata|causare|crea|creare|provoca|provocare|"
    r"dipende|dovuto|dovuta|legato|legata|colpa|problema|effetto|"
    r"sembra|rientrato|tornato\s+a\s+posto|migliora|peggiora)\b",
    re.IGNORECASE,
)

_DERIVED_CROSS_EPISODE_TAGS = {"lesson", "from_correction"}


def _case_has_causal_hint(text: str) -> bool:
    """Prefiltro linguistico leggero, non-domain-specific: cerca forma causa/effetto."""
    return bool(text and _CAUSAL_EPISODE_RE.search(text))


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _counts_as_cross_episode_evidence(doc: dict) -> bool:
    """True solo per episodi abbastanza diretti da fondare una generalizzazione.

    Lezioni da correzione, reflection e consolidamenti sono utili come contesto,
    ma non devono contare come un secondo caso indipendente: spesso sono
    metabolizzazioni dello stesso episodio.
    """
    source = doc.get("source") or ""
    if source in {"reflection", "reaction", "insight", "system"}:
        return False
    tags = {str(t) for t in _as_list(doc.get("tags"))}
    if tags & _DERIVED_CROSS_EPISODE_TAGS:
        return False
    if doc.get("consolidated_from") or doc.get("source_memory_ids"):
        return False
    return True


def _parse_cross_episode_response(raw: str) -> dict:
    if not raw:
        return {"should_create": False, "reason": "empty_output"}
    s = raw.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start:end + 1]
    try:
        data = json.loads(s)
    except Exception:
        return {"should_create": False, "reason": "json_parse_failed"}
    return data if isinstance(data, dict) else {"should_create": False, "reason": "not_object"}


def _ensure_hypothesis_wording(text: str) -> str:
    """Rende esplicito che l'output è ipotesi, non fatto operativo acquisito."""
    s = " ".join((text or "").split())
    if not s:
        return ""
    low = s.lower()
    if any(w in low for w in ("ipotesi", "potrebbe", "può", "da verificare", "da valutare")):
        return s
    return f"Ipotesi da verificare: {s}"


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
        boot_ts = time.time()
        self._last_activity = boot_ts
        self._light_last_run = boot_ts
        self._creative_last_run = boot_ts
        # Il clock della manutenzione (cadenza 24h) vive in Redis, non solo in RAM:
        # così NON si azzera a ogni riavvio. Senza, restart frequenti (fase di sviluppo)
        # starvano per sempre la fascia maintenance — 2e/2f/2h/cleanup/pruning — perché
        # il clock riparte dal boot a ogni avvio. Vedi _run_due_idle_cycles.
        self._maintenance_last_run = self._load_maintenance_clock(boot_ts)
        self._consolidation_last_run = 0.0  # timestamp ultimo Loop 2e

    _MAINTENANCE_CLOCK_KEY = "euri:dream:maintenance_last_run"

    def _load_maintenance_clock(self, default_ts: float) -> float:
        """Last-run della manutenzione letto da Redis (default boot_ts se assente)."""
        try:
            raw = self._r.get(self._MAINTENANCE_CLOCK_KEY)
            if raw is not None:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode()
                return float(raw)
        except Exception as e:
            logger.debug(f"Dream Engine: clock manutenzione non letto: {e}")
        return default_ts

    def _persist_maintenance_clock(self, ts: float):
        """Scrive il last-run della manutenzione su Redis, così sopravvive ai restart."""
        try:
            self._r.set(self._MAINTENANCE_CLOCK_KEY, ts)
        except Exception as e:
            logger.debug(f"Dream Engine: clock manutenzione non persistito: {e}")

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
        """Controlla se il sistema è inattivo abbastanza per i cicli offline."""
        with self._lock:
            elapsed = time.time() - self._last_activity
        idle_seconds = float(
            getattr(config, "DREAM_ENGINE_IDLE_SECONDS", 0)
            or getattr(config, "DREAM_ENGINE_IDLE_HOURS", 2) * 3600
        )
        return elapsed >= idle_seconds

    def _ollama_chat(self, **kwargs) -> ollama.ChatResponse:
        """Wrapper con timeout (default 200s) attorno a ollama.chat — evita hang dei cicli idle.
        Antepone il contesto operativo (EURI_CONTEXT.md) come messaggio system a tutte le
        chiamate offline/idle (sogno, sintesi, contraddizioni, plausibilità). Fail-open: se il
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
        """Loop principale: controlla l'idle e lancia i sotto-cicli dovuti."""
        while self._running:
            poll = int(getattr(config, "DREAM_ENGINE_POLL_SECONDS", 300))
            for _ in range(max(1, poll)):
                if not self._running:
                    return
                time.sleep(1)
                
            if self._is_idle():
                self._run_due_idle_cycles()

    def _run_due_idle_cycles(self):
        """Esegue solo i sotto-cicli scaduti mentre Euri è idle."""
        ts = time.time()
        light_due = ts - self._light_last_run >= float(getattr(config, "DREAM_LIGHT_CYCLE_INTERVAL_S", 20 * 60))
        creative_due = ts - self._creative_last_run >= float(getattr(config, "DREAM_CREATIVE_CYCLE_INTERVAL_S", 90 * 60))
        maintenance_due = ts - self._maintenance_last_run >= float(getattr(config, "DREAM_MAINTENANCE_CYCLE_INTERVAL_S", 24 * 3600))

        if not (light_due or creative_due or maintenance_due):
            return

        logger.info(
            "Dream Engine: ciclo idle "
            f"(light={light_due}, creative={creative_due}, maintenance={maintenance_due})"
        )
        pulse_emit(
            self._r, "dream", "intero", "idle_cycle",
            payload={"light": light_due, "creative": creative_due, "maintenance": maintenance_due},
            salience=0.25,
        )

        if creative_due:
            try:
                self._creative_cycle()
                self._creative_last_run = time.time()
            except Exception as e:
                logger.error(f"Errore ciclo creativo Dream Engine: {e}")
        if light_due:
            try:
                self._light_cycle()
                self._light_last_run = time.time()
            except Exception as e:
                logger.error(f"Errore ciclo leggero Dream Engine: {e}")
        if maintenance_due:
            try:
                self._maintenance_cycle()
                self._maintenance_last_run = time.time()
                self._persist_maintenance_clock(self._maintenance_last_run)
            except Exception as e:
                logger.error(f"Errore ciclo manutentivo Dream Engine: {e}")

    def _creative_cycle(self):
        """Sogno cross-domain + promozione insight. Medio-costo, cadenza separata."""
        domains = self._get_unique_domains()
        if len(domains) < 2:
            logger.debug("Dream Engine: non ci sono abbastanza domini per sognare")
            return
        self._generate_dream(domains)
        self._evaluate_insights()

    def _light_cycle(self):
        """Pass leggeri/frequenti: metabolizza feedback e ipotesi senza consolidare."""
        self._evaluate_insights()
        self._audit_corrections_pass()
        if getattr(config, "CROSS_EPISODE_HYPOTHESIS_ENABLED", True):
            self._cross_episode_hypothesis_pass()
        self._provenance_propagation_pass()

    def _maintenance_cycle(self):
        """Manutenzione lenta: pulizia, contraddizioni, consolidamento, self-observation."""
        self._contradiction_resolution_pass()
        if config.PLAUSIBILITY_GATE_ENABLED:
            self._plausibility_gate_pass()
        if self._self_observation:
            try:
                self._self_observation.run()
            except Exception as e:
                logger.error(f"Loop 2h: errore self-observation pass: {e}")
        self._cleanup_expired_insights()
        self._cleanup_stale_memories()
        self._pruning_pass()
        if time.time() - self._consolidation_last_run >= 86400:
            self._consolidation_pass()
            self._consolidation_last_run = time.time()
        self._provenance_propagation_pass()

    def _run_dream_cycle(self):
        """Esegue un ciclo completo forzato (compatibile con force_full_cycle.py)."""
        logger.info("Dream Engine: inizio ciclo cognitivo completo")
        pulse_emit(self._r, "dream", "intero", "cycle_start", salience=0.25)
        try:
            # 1. Loop 2b/2c: sogno creativo + valutazione insight
            self._creative_cycle()
                
            # 2. Loop 2f: Contradiction resolution — soft-delete valori numerici obsoleti
            self._contradiction_resolution_pass()

            # 3. Loop 2g: Audit di Coerenza — analizza le correzioni ricevute
            self._audit_corrections_pass()

            # 4. Loop 2i: ipotesi trasversali da episodi ripetuti — genera domande, non fatti.
            if getattr(config, "CROSS_EPISODE_HYPOTHESIS_ENABLED", True):
                self._cross_episode_hypothesis_pass()

            # 5. Plausibility gate — ARCHIVIATO (kill-switch off, codice lasciato in repo).
            # Negative result: 1 vero positivo / 3 falsi positivi su gemme di dominio vere,
            # anche col contesto operativo attivo → non chiamato di default. Vedi changelog.
            if config.PLAUSIBILITY_GATE_ENABLED:
                self._plausibility_gate_pass()

            # 6. Loop 2h: Self-Observation — narrative di evoluzione dalle coppie superseded.
            # Additivo: NON modifica il Loop 2f (che continua a fare superseded_by), aggiunge
            # solo una voce narrativa in prima persona per ogni evoluzione mai raccontata prima.
            if self._self_observation:
                try:
                    self._self_observation.run()
                except Exception as e:
                    logger.error(f"Loop 2h: errore self-observation pass: {e}")

            # 7. Pulizia Insight scaduti
            self._cleanup_expired_insights()

            # 8. Pulizia Memorie stantie (passive/reflection mai richiamate)
            self._cleanup_stale_memories()

            # 9. Loop 2d: Death-row gate per memorie in scadenza entro 7 giorni
            self._pruning_pass()

            # 10. Loop 2e: Memory Consolidation — max una volta ogni 24h
            if time.time() - self._consolidation_last_run >= 86400:
                self._consolidation_pass()
                self._consolidation_last_run = time.time()

            # 11. Propagazione di provenienza (invariante A): in coda, così le supersessioni
            # appena fatte dal 2f e i nodi appena consolidati dal 2e sono valutati nello
            # stesso ciclo.
            self._provenance_propagation_pass()

        except Exception as e:
            logger.error(f"Errore ciclo Dream Engine: {e}")

    def _provenance_propagation_pass(self):
        """
        Invariante A — propagazione di provenienza. Un nodo derivato (`consolidated_from`)
        la cui fondamenta è caduta — genitori superseded/spariti/da-verificare — viene
        tenuto SOSPETTO: `provenance_stale=True` (down-rank nel retrieval, vedi
        memory_manager.search_memories) + `requires_verification=True` (Euri si copre).
        Fail-safe: si SEGNALA, non si cancella; si auto-guarisce se il rischio torna ok.
        Ricalcola il rischio DAL VIVO ogni ciclo → propaga le supersessioni (2f/correzioni)
        ai discendenti, anche quando la base è marcita DOPO la nascita del nodo.
        """
        if not getattr(config, "PROVENANCE_PROPAGATION_ENABLED", True):
            return
        staled = healed = 0
        for key in self._r.scan_iter("euri:memory:*"):
            try:
                doc = self._r.json().get(key, "$")[0]
            except Exception:
                continue
            parents = doc.get("consolidated_from")
            if not parents or doc.get("superseded_by"):
                continue
            try:
                risk = self._consolidation_source_risk(parents)
                is_high = risk["level"] == "high"
                was_stale = bool(doc.get("provenance_stale"))
                if is_high and not was_stale:
                    self._r.json().set(key, "$.provenance_stale", True)
                    self._r.json().set(key, "$.consolidation_risk", risk)
                    # requires_verification è MULTI-CAUSA (lo accende anche il contenuto
                    # spec-heavy alla nascita). Lo settiamo SOLO se era spento, e marchiamo
                    # che siamo stati NOI (rv_by_provenance) → così la guarigione potrà
                    # azzerarlo senza cancellare una verifica dovuta ad altro (fix review F2).
                    if not doc.get("requires_verification"):
                        # mark-after-act (Codex #5): scrivi PRIMA il marcatore rv_by_provenance,
                        # POI requires_verification. Se il secondo write fallisce la memoria non è
                        # flaggata (safe); l'ordine inverso lasciava requires_verification acceso
                        # SENZA marcatore → la guarigione (riga sotto) non poteva più spegnerlo.
                        self._r.json().set(key, "$.rv_by_provenance", True)
                        self._r.json().set(key, "$.requires_verification", True)
                        remove_loop2e_candidate(self._r, doc.get("id", ""))
                    staled += 1
                elif not is_high and was_stale:
                    self._r.json().set(key, "$.provenance_stale", False)
                    self._r.json().set(key, "$.consolidation_risk", risk)
                    # Guarigione vera: azzera requires_verification SOLO se l'avevamo acceso
                    # noi per la provenienza; se era acceso per conto suo, lo lasciamo.
                    if doc.get("rv_by_provenance"):
                        self._r.json().set(key, "$.requires_verification", False)
                        self._r.json().set(key, "$.rv_by_provenance", False)
                        healed_doc = dict(doc)
                        healed_doc["requires_verification"] = False
                        update_loop2e_candidate_index(self._r, healed_doc)
                    healed += 1
            except Exception as e:
                logger.debug(f"provenance pass fallito per {key}: {e}")
        if staled or healed:
            logger.info(f"Provenienza: {staled} nodi marcati sospetti, {healed} risanati")
            pulse_emit(self._r, "provenance", "intero", "propagated",
                       payload={"staled": staled, "healed": healed}, salience=0.55)

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

    def _integrity_failure(self, kind: str, key: str, err) -> None:
        """Path di scrittura dei loop idle (mark-after-act): un fallimento di scrittura NON
        deve sparire in debug. Delega a MemoryManager._record_integrity_failure (WARNING + stream
        euri:integrity:failures) quando disponibile; altrimenti almeno un WARNING."""
        if self._memory_manager:
            self._memory_manager._record_integrity_failure(kind, key, err)
        else:
            logger.warning(f"INTEGRITÀ (dream) '{kind}' su {key}: {err}")

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

        # Esperimento dream_trace (continuità 2b): residuo di STRATEGIA del ciclo
        # precedente, iniettato come sezione marcata. Serve a non ripercorrere i TIPI
        # di ponte già trovati deboli — mai a continuarli. A flag spento: sezione
        # vuota, prompt bit-identico all'attuale.
        trace_txt = None
        if getattr(config, "DREAM_TRACE_ENABLED", False):
            try:
                trace_txt = self._r.get("euri:dream_trace:latest")
            except Exception:
                trace_txt = None
        trace_section = ""
        if trace_txt:
            trace_section = (
                "\n[TRACCIA DEL CICLO PRECEDENTE — strategie di connessione già tentate e trovate deboli:\n"
                f"{trace_txt}\n"
                "Serve solo a NON ripercorrere: se la connessione che stai per proporre ricade in una di "
                "queste strategie deboli, cambia tipo di ponte o rispondi NESSUN INSIGHT.]\n"
            )

        prompt = f"""\
Hai due memorie da domini diversi. Il tuo compito è trovare una connessione operativa non ovvia — qualcosa che non emerge guardando un solo dominio.

Memoria A ({label_a}):
"{mem_a['content']}"

Memoria B ({label_b}):
"{mem_b['content']}"
{trace_section}
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
            import re
            # Il CoT va colto PRIMA dello strip: è la materia prima del residuo di
            # esplorazione. A seconda della versione ollama vive in message.thinking
            # oppure inline nel blocco <think> del content.
            raw_cot = ""
            if getattr(config, "DREAM_TRACE_ENABLED", False):
                raw_cot = getattr(response.message, "thinking", "") or ""
                if not raw_cot:
                    m = re.search(r"<think>(.*?)</think>", text, flags=re.DOTALL)
                    raw_cot = m.group(1) if m else ""
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1]
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
                    "convergence_count": 1,
                    # Provenienza: i nodi VISSUTI da cui questo insight è nato (le due memorie
                    # del sogno). Persistere qui rende l'insight groundabile seguendo gli archi,
                    # invece di ri-recuperare per similarità (muro multi-hop, 22/06). Gli ID sono
                    # già in mano e sul dream doc; mancavano solo qui. Lista per estendersi in
                    # convergenza (union dei fratelli assorbiti — vedi promozione).
                    "source_memory_ids": [mem_a["id"], mem_b["id"]],
                }
                if getattr(config, "DREAM_TRACE_ENABLED", False):
                    # Braccio sperimentale: candidate nato CON residuo iniettato (True)
                    # o senza (False: primo ciclo, o residuo scaduto). A flag spento il
                    # campo non esiste → nessuna differenza col comportamento attuale.
                    insight_doc["trace_injected"] = bool(trace_txt)
                self._r.json().set(f"euri:insight:{insight_id}", "$", insight_doc)

            # Il residuo si distilla ANCHE dai sogni scartati: "perché era debole" è
            # proprio l'informazione che il ciclo dopo deve avere. Fail-open, dopo il
            # salvataggio del sogno: un fallimento qui non tocca il ciclo.
            if getattr(config, "DREAM_TRACE_ENABLED", False) and raw_cot:
                self._update_dream_trace(raw_cot, dom_a, dom_b)

            return dream_doc
            
        except Exception as e:
            logger.error(f"Errore generazione sogno: {e}")
            return None

    def _update_dream_trace(self, cot: str, dom_a: str, dom_b: str) -> None:
        """Esperimento continuità 2b: distilla dal CoT appena scartato un residuo di
        ESPLORAZIONE (max 5 righe) e lo persiste con TTL su euri:dream_trace:latest.

        Il residuo vive al livello della STRATEGIA ("che tipo di ponte ho provato e
        perché era debole"), non della coppia di domini: con ~145 domini e pairing
        random la coppia non si ripete quasi mai, un residuo per-coppia sarebbe inerte.
        Povero di proposito: troppo ricco → il ciclo dopo converge invece di esplorare
        (ruminazione). think=False: il thinking consumerebbe num_predict e tornerebbe
        vuoto (failure noto, caso synthesize_lesson). Il modello del sogno è già caldo
        in VRAM → chiamata economica. NON è una memoria: niente embedding, niente
        dominio, mai nel retrieval. Fail-open: non rompe mai il sogno."""
        try:
            if not cot or len(cot.strip()) < 80:
                return
            # NB 13/07 sera: la v1 di questo prompt aveva 3 etichette d'esempio → il
            # modello le pappagallava a ogni ciclo, e la traccia iniettata nel sogno
            # rientrava dal CoT nel residuo (eco a punto fisso, quasi-verbatim tra
            # cicli su domini diversi). Ora: niente esempi, ignorare esplicitamente
            # la traccia precedente, e "NIENTE DA SEGNALARE" se non c'è esplorazione
            # vera (meglio nessun residuo di un residuo finto).
            prompt = (
                f"Hai appena cercato una connessione tra i domini '{dom_a}' e '{dom_b}'. "
                "Questo è il tuo ragionamento grezzo:\n\n"
                f"{cot[:6000]}\n\n"
                "Riassumi in MASSIMO 5 righe i tentativi di collegamento che HAI FATTO in "
                "QUESTO ragionamento e perché non reggevano. Regole:\n"
                "- il TIPO di ponte descrivilo con parole tue, prese dal ragionamento vero — "
                "niente etichette generiche di repertorio;\n"
                "- se nel ragionamento compare una [TRACCIA DEL CICLO PRECEDENTE], IGNORALA: "
                "non riassumerla, non riusarne etichette o frasi;\n"
                "- niente contenuti specifici dei due domini, niente insight finale;\n"
                "- una riga per tentativo: 'ho provato <tipo di ponte>: debole perché <ragione>'.\n"
                "Se in questo ragionamento non hai esplorato strade alternative reali, "
                "rispondi solo: NIENTE DA SEGNALARE."
            )
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 400},
                think=False,
            )
            text = resp.message.content or ""
            if "<channel|>" in text:
                text = text.split("<channel|>", 1)[-1]
            import re as _re
            text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
            if "NIENTE DA SEGNALARE" in text.upper():
                return  # nessuna esplorazione vera → non sovrascrivere (il TTL smaltisce)
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:5]
            residue = "\n".join(lines)
            if residue:
                self._r.setex("euri:dream_trace:latest",
                              getattr(config, "DREAM_TRACE_TTL_S", 48 * 3600), residue)
                logger.info(f"Dream trace aggiornata ({len(lines)} righe)")
        except Exception as e:
            logger.debug(f"dream_trace non aggiornata (non-critico): {e}")

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

    def _trace_convergence(self, doc, convergences, n_certain, neighbor_trace, outcome):
        """Instrumentazione ADDITIVA (read-only sulla decisione): registra la convergenza
        AL MOMENTO DELLA DECISIONE su euri:convergence:trace, per correlarla OFFLINE col
        recall futuro — test convergenza↔uso su dati NON selezionati (promossi E scartati),
        che il pool dei soli promossi non permette (selection bias).
        Non altera nessuna decisione. Fail-safe: non rompe mai il ciclo. Disattivabile via
        config.CONVERGENCE_TRACE_ENABLED. neighbor_trace = [(id, score, content[:400])] per
        ricalcolare offline qualsiasi metrica (es. claim-embedding + soglia relativa)."""
        if not getattr(config, "CONVERGENCE_TRACE_ENABLED", True):
            return
        try:
            # below_threshold è ri-valutato ogni ciclo per lo stesso candidate → dedup per
            # (seed, convergences) con marcatore TTL: una entry per LIVELLO di convergenza
            # raggiunto (traiettoria), non a ogni ciclo. Le decisioni TERMINALI
            # (promoted/denied_*) si loggano SEMPRE — sono l'evento raro e prezioso per la
            # correlazione convergenza↔uso, che il rumore below_threshold non deve sfrattare.
            if outcome == "below_threshold":
                seen_key = f"euri:convergence:seen:{doc.id}:{convergences}"
                if not self._r.set(seen_key, "1", nx=True, ex=7 * 86400):
                    return
            import json as _json
            g = lambda p, d=None: (self._r.json().get(doc.id, p) or [d])[0]
            self._r.xadd("euri:convergence:trace", {
                "ts": repr(time.time()),
                "seed_id": str(doc.id),
                "domain": f"{g('$.domain_a')}×{g('$.domain_b')}",
                "created_at": repr(g("$.created_at")),
                "demoted_once": "1" if g("$.demoted_once", False) else "0",
                "recalled_count_at_decision": str(g("$.recalled_count", 0) or 0),
                "convergences": str(convergences),
                "n_certain": str(n_certain),
                "outcome": outcome,
                "seed_content": (getattr(doc, "content", "") or "")[:600],
                "neighbors": _json.dumps(neighbor_trace, ensure_ascii=False)[:4000],
                # Braccio esperimento dream_trace: "1"/"0" se il candidate è nato
                # con/senza residuo iniettato; "" per i candidate pre-esperimento.
                # È il join che permette l'audit baseline/trattamento SULLA trace,
                # senza log paralleli.
                "trace_injected": {True: "1", False: "0"}.get(g("$.trace_injected"), ""),
            }, maxlen=50000, approximate=True)
        except Exception as e:
            logger.debug(f"trace convergence fallito (non-critico): {e}")

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
                neighbor_trace = []   # (id, score, content[:400]) — instrumentazione offline
                n_certain = 0         # vicini auto-contati (score<0.15), la parte anisotropia-sensibile

                for sim in res_sim.docs:
                    if sim.id == doc.id:
                        continue  # Salta se stesso
                    score = float(sim.score)
                    sim_content = getattr(sim, "content", None)
                    neighbor_trace.append((str(sim.id), round(score, 4), (sim_content or "")[:400]))
                    if score < 0.15:
                        convergences += 1
                        n_certain += 1
                        similar_ids.append(sim.id)
                    elif score < 0.40:
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
                        self._trace_convergence(doc, convergences, n_certain, neighbor_trace, "denied_format")
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
                        pulse_emit(self._r, "insight", "intero", "repromotion_denied",
                                   payload={"id": str(doc.id)[-12:], "convergences": convergences},
                                   salience=0.45)
                        self._trace_convergence(doc, convergences, n_certain, neighbor_trace, "denied_repromotion")
                        continue

                    # Provenienza cumulativa: prima di cancellare i candidate assorbiti,
                    # unisci i loro nodi sorgente a quelli del candidate promosso. I vecchi
                    # insight pre-patch possono non avere il campo: in quel caso l'union è
                    # parziale ma resta corretta per i dati disponibili.
                    source_memory_ids = []
                    for iid in [doc.id, *similar_ids]:
                        try:
                            raw_ids = self._r.json().get(iid, "$.source_memory_ids") or []
                            for mid in (raw_ids[0] if raw_ids else []):
                                if mid:
                                    source_memory_ids.append(mid)
                        except Exception:
                            continue

                    # Promuovi questo a PROMOTED
                    self._r.json().set(doc.id, "$.status", "promoted")
                    self._r.json().set(doc.id, "$.convergence_count", convergences)
                    if source_memory_ids:
                        self._r.json().set(doc.id, "$.source_memory_ids", list(dict.fromkeys(source_memory_ids)))
                    self._r.json().set(doc.id, "$.promoted_at", time.time())
                    
                    # Rimuovi i duplicati assorbiti
                    for sid in similar_ids:
                        self._r.delete(sid)
                        
                    logger.success(f"Dream Engine: Insight PROMOSSO! (convergenze: {convergences})")
                    pulse_emit(self._r, "insight", "intero", "promoted",
                               payload={
                                   "id": str(doc.id).replace("euri:insight:", ""),
                                   "key": str(doc.id),
                                   "convergences": convergences,
                               },
                               salience=0.65)
                    self._trace_convergence(doc, convergences, n_certain, neighbor_trace, "promoted")
                    promoted_count += 1
                    
                    # Scrivi nel vault di Obsidian
                    try:
                        doc_promoted = self._r.json().get(doc.id, "$")
                        if doc_promoted:
                            write_insight(doc_promoted[0])
                    except Exception as e:
                        logger.debug(f"Errore sync insight su Obsidian: {e}")

                else:
                    # Convergenza sotto soglia: nessuna promozione (il ramo più comune).
                    self._trace_convergence(doc, convergences, n_certain, neighbor_trace, "below_threshold")

        except Exception as e:
            logger.error(f"Errore valutazione insights: {e}")

    # ── Loop 2i: Ipotesi trasversali da episodi ripetuti ───────────────────

    def _cross_episode_recently_ran(self) -> bool:
        try:
            raw = self._r.get(CROSS_EPISODE_LAST_RUN_KEY)
            last = float(raw or 0)
        except Exception:
            return False
        min_s = float(getattr(config, "CROSS_EPISODE_MIN_INTERVAL_S", 12 * 3600))
        return bool(last and time.time() - last < min_s)

    def _mark_cross_episode_run(self) -> None:
        try:
            self._r.set(CROSS_EPISODE_LAST_RUN_KEY, f"{time.time():.3f}", ex=7 * 86400)
        except Exception:
            pass

    def _collect_cross_episode_cases(self) -> list[dict]:
        """Raccoglie memorie operative con forma causa→effetto, senza hardcode di dominio."""
        limit = int(getattr(config, "CROSS_EPISODE_MAX_MEMORIES", 24))
        oversample = max(limit * 4, 50)
        cases: list[dict] = []
        try:
            q = (
                Query("*")
                .sort_by("created_at", asc=False)
                .paging(0, oversample)
                .return_fields("id", "content", "source", "domain", "created_at")
            )
            res = self._r.ft("idx:memories").search(q)
        except Exception as e:
            logger.debug(f"Loop 2i: ricerca memorie fallita: {e}")
            return cases

        for row in res.docs:
            if len(cases) >= limit:
                break
            key = row.id
            try:
                raw = self._r.json().get(key, "$")
                doc = raw[0] if raw else {}
            except Exception:
                doc = {}
            content = (doc.get("content") or getattr(row, "content", "") or "").strip()
            if not content or not _case_has_causal_hint(content):
                continue
            if doc.get("superseded_by") or doc.get("consolidated_into"):
                continue
            if doc.get("provenance_stale") or int(doc.get("audit_flag") or 0) > 0:
                continue
            source = doc.get("source") or getattr(row, "source", "")
            if source in {"web", "mobile", "mobile_in"}:
                continue
            if not _counts_as_cross_episode_evidence({**doc, "source": source}):
                continue
            # Le memorie passive deboli sono buone per chiedere conferma, non per
            # fondare una generalizzazione trasversale.
            if source == "passive" and (doc.get("passive_support") or doc.get("requires_verification")):
                continue
            cases.append({
                "id": (doc.get("id") or str(key).replace("euri:memory:", "")),
                "key": str(key),
                "content": content[:500],
                "source": source,
                "domain": doc.get("domain") or getattr(row, "domain", "") or "generale",
                "created_at": doc.get("created_at") or getattr(row, "created_at", None),
            })
        return cases

    def _llm_cross_episode_hypothesis(self, cases: list[dict]) -> dict:
        lines = []
        for i, c in enumerate(cases, 1):
            age = self._memory_age(c.get("created_at"))
            meta = f"fonte={c.get('source','?')}, dominio={c.get('domain','?')}"
            if age:
                meta += f", {age}"
            lines.append(f"CASO {i} ({meta})\n{c['content']}")
        prompt = f"""\
Analizza questi episodi operativi di memoria. Cerca SOLO pattern trasversali causa_sospetta→effetto che compaiono in almeno {getattr(config, "CROSS_EPISODE_MIN_CASES", 2)} CASI distinti.

Regole:
- Non cercare una bella analogia: cerca un'ipotesi pratica da verificare.
- Non trasformare una coincidenza in verità. Se il pattern è debole, should_create=false.
- Non generalizzare universalmente. Formula come "può/potrebbe essere una variabile da controllare", non come fatto certo.
- Usa solo i casi qui sotto. Niente conoscenza del mondo, niente dominio hardcoded.
- Se i casi sono duplicati dello stesso episodio o dicono tutti la stessa cosa ripetuta, should_create=false.

EPISODI:
{chr(10).join(lines)}

Rispondi SOLO JSON valido:
{{
  "should_create": true/false,
  "case_numbers": [1, 2],
  "cause_pattern": "causa o variabile comune, breve",
  "effect_pattern": "effetto ricorrente, breve",
  "context": "contesto operativo dove vale la pena controllare",
  "hypothesis": "ipotesi in italiano, cauta, da verificare",
  "why": "motivo breve"
}}"""
        try:
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 900},
                format="json",
                think=False,
            )
            return _parse_cross_episode_response(resp.message.content or "")
        except Exception as e:
            logger.debug(f"Loop 2i: LLM fallito: {e}")
            return {"should_create": False, "reason": f"llm_error:{str(e)[:80]}"}

    def _format_cross_episode_insight(self, data: dict, selected: list[dict]) -> str:
        domains = list(dict.fromkeys(c.get("domain") or "generale" for c in selected))
        dom_a = domains[0] if domains else "episodi operativi"
        dom_b = domains[1] if len(domains) > 1 else "ipotesi trasversale"
        cause = " ".join(str(data.get("cause_pattern") or "variabile ricorrente").split())
        effect = " ".join(str(data.get("effect_pattern") or "effetto ricorrente").split())
        context = " ".join(str(data.get("context") or "casi simili futuri").split())
        hypothesis = _ensure_hypothesis_wording(str(data.get("hypothesis") or ""))
        if not hypothesis:
            hypothesis = f"Ipotesi da verificare: {cause} potrebbe essere una variabile da controllare quando compare {effect}."
        return (
            f"Nel dominio [{dom_a}] succede: più episodi collegano {cause} a {effect}.\n"
            f"Nel dominio [{dom_b}] succede: il contesto operativo ricorrente è {context}.\n"
            f"La connessione operativa non ovvia è: {hypothesis}"
        )

    def _cross_episode_hypothesis_pass(self) -> None:
        if self._cross_episode_recently_ran():
            return
        cases = self._collect_cross_episode_cases()
        min_cases = int(getattr(config, "CROSS_EPISODE_MIN_CASES", 2))
        if len(cases) < min_cases:
            return
        self._mark_cross_episode_run()

        data = self._llm_cross_episode_hypothesis(cases)
        if not data.get("should_create"):
            logger.debug(f"Loop 2i: nessuna ipotesi trasversale ({data.get('reason') or data.get('why') or 'no'})")
            return

        selected: list[dict] = []
        for n in data.get("case_numbers") or []:
            try:
                idx = int(n) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(cases):
                selected.append(cases[idx])
        # Dedup preservando ordine
        selected = list({c["id"]: c for c in selected}.values())
        if len(selected) < min_cases:
            logger.debug("Loop 2i: output scartato, source insufficienti")
            return

        source_ids = [c["id"] for c in selected]
        fp = hashlib.sha1("|".join(sorted(source_ids)).encode()).hexdigest()
        try:
            if self._r.sismember(CROSS_EPISODE_SEEN_KEY, fp):
                logger.debug("Loop 2i: ipotesi già emersa per queste fonti")
                return
        except Exception:
            pass

        content = self._format_cross_episode_insight(data, selected)
        if not self._has_required_structure(content):
            logger.debug("Loop 2i: ipotesi scartata, formato insight non valido")
            return
        vec = self._embedder.encode(content, mode="passage") if self._embedder else None
        insight_id = str(uuid.uuid4())
        domains = list(dict.fromkeys(c.get("domain") or "generale" for c in selected))
        now_ts = to_timestamp(now())
        insight_doc = {
            "id": insight_id,
            "content": content,
            "status": "promoted",
            "domain_a": domains[0] if domains else "episodi operativi",
            "domain_b": domains[1] if len(domains) > 1 else "ipotesi trasversale",
            "created_at": now_ts,
            "promoted_at": now_ts,
            "recalled_count": 0,
            "embedding": vec.tolist() if vec is not None else None,
            "convergence_count": len(selected),
            "source_memory_ids": source_ids,
            "requires_verification": True,
            "verification_status": "hypothesis_to_test",
            "hypothesis_kind": "cross_episode_pattern",
        }
        key = f"euri:insight:{insight_id}"
        try:
            self._r.json().set(key, "$", insight_doc)
            self._r.sadd(CROSS_EPISODE_SEEN_KEY, fp)
            self._r.expire(CROSS_EPISODE_SEEN_KEY, 180 * 86400)
        except Exception as e:
            self._integrity_failure("loop2i-create-insight", key, e)
            return

        logger.success(f"Loop 2i: ipotesi trasversale PROMOSSA → {insight_id[:8]}…")
        pulse_emit(self._r, "insight", "intero", "promoted",
                   payload={"id": insight_id, "key": key, "convergences": len(selected)},
                   salience=0.68)
        try:
            write_insight(insight_doc)
        except Exception as e:
            logger.debug(f"Loop 2i: sync insight su Obsidian fallita: {e}")

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

    @staticmethod
    def _loop2f_is_comparison_doc(doc: dict) -> bool:
        """True se il nodo e' una nota meta-comparativa generata dal Loop 2f."""
        if not doc:
            return False
        content = (doc.get("content") or "").strip().lower()
        if content.startswith("[confronto]"):
            return True
        tags = doc.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tagset = {str(t).lower() for t in tags}
        return "loop2f" in tagset and "confronto" in tagset

    @staticmethod
    def _loop2f_consolidation_risk_level(doc: dict) -> str:
        cr = (doc or {}).get("consolidation_risk") or {}
        if isinstance(cr, list):
            cr = cr[0] if cr and isinstance(cr[0], dict) else {}
        if isinstance(cr, dict):
            return str(cr.get("level") or "ok").lower()
        return "ok"

    @classmethod
    def _loop2f_source_allowed(cls, doc: dict) -> bool:
        """
        Fonti ammissibili come input primario del Loop 2f.

        Le note [confronto] sono meta-conoscenza: possono orientare il RAG, ma non
        devono generare confronti di confronti. I consolidati ad alto rischio restano
        correggibili dal 2g/retrieval, ma non vanno usati per produrre nuova
        metariflessione.
        """
        if cls._loop2f_is_comparison_doc(doc):
            return False
        if cls._loop2f_consolidation_risk_level(doc) == "high":
            return False
        return True

    @classmethod
    def _loop2f_candidate_allowed(cls, doc: dict, *, skip_sources: set[str] | None = None) -> bool:
        """Gate completo per memorie che possono essere lavorate dal Loop 2f."""
        if not cls._loop2f_source_allowed(doc):
            return False
        if doc.get("correction_pending"):
            return False
        if not doc.get("requires_verification"):
            return False
        if doc.get("superseded_by"):
            return False
        if skip_sources and doc.get("source") in skip_sources:
            return False
        return True

    def _make_comparison_memory(
        self,
        content_a: str,
        content_b: str,
        domain: str,
        *,
        source_ids: list[str] | None = None,
        requires_verification: bool = False,
    ) -> None:
        """
        Idea di Stefano (01/06): quando il Loop 2f trova entità DIVERSE ma simili
        (due impianti, due clienti…), non sceglie un vincitore — genera una nota di
        CONFRONTO operativa (cosa in comune, in cosa differiscono, quando preferire
        l'una o l'altra). È meta-conoscenza, NON un fatto grezzo: resta fuori dal
        Loop 2f tramite prefisso [confronto], ma eredita la fragilità epistemica dei
        parent se nasce da memorie già `requires_verification`.
        """
        if not self._memory_manager:
            return
        prompt = f"""\
Due voci di memoria descrivono entità DIVERSE ma confrontabili:

A: "{content_a[:500]}"
B: "{content_b[:500]}"

Scrivi un breve CONFRONTO operativo (2-4 frasi): cosa hanno in comune, in cosa
DIFFERISCONO concretamente (valori/limiti), e quando conviene l'una rispetto all'altra.
Non inventare dati non presenti nelle due voci.
Se una fonte e' incerta o richiede verifica, NON usare parole come "validato",
"definitivo", "certo" o "pronto all'uso" a meno che siano scritte esplicitamente
nella voce. In quel caso formula il confronto come ipotesi operativa da verificare.

Fonte incerta o da verificare: {"SI" if requires_verification else "NO"}

Rispondi solo col confronto."""
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
            if mid:
                key = f"euri:memory:{mid}"
                remove_loop2e_candidate(self._r, mid)
                if source_ids:
                    self._r.json().set(key, "$.source_memory_ids", list(dict.fromkeys(source_ids)))
                if requires_verification:
                    self._r.json().set(key, "$.requires_verification", True)
                    self._r.json().set(key, "$.consolidation_risk", {
                        "level": "watch",
                        "reason": "loop2f_comparison_from_unverified_parent",
                        "source_ids": list(dict.fromkeys(source_ids or [])),
                    })
                else:
                    self._r.json().set(key, "$.requires_verification", False)
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
                    if not self._loop2f_candidate_allowed(doc, skip_sources=SKIP_SOURCES):
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
                    if not self._loop2f_candidate_allowed(n_doc, skip_sources=SKIP_SOURCES):
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
                            source_ids=[seed_id, n_id],
                            requires_verification=bool(
                                seed.get("requires_verification") or n_doc.get("requires_verification")
                            ),
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

                    try:
                        self._r.json().set(f"euri:memory:{loser_id}", "$.superseded_by", winner_id)
                        remove_loop2e_candidate(self._r, loser_id)
                    except Exception as e:
                        # mark-after-act (Codex #1): la coppia è già in CHECKED (marcata a monte),
                        # ma il soft-delete è fallito → la contraddizione resta VIVA. Dis-marca così
                        # si riprova, invece di seppellirla 180gg con la contraddizione attiva.
                        self._r.srem(CHECKED_KEY, pair_key)
                        self._integrity_failure("loop2f-supersede", f"euri:memory:{loser_id}", e)
                        continue
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
È correzione anche se l'utente dice che un fatto appena trattato o memorizzato era uno scherzo/provocazione/non vero: sta rettificando lo stato epistemico del fatto.
NON è una correzione se l'utente: fa una domanda o chiede un ricordo ("ti ricordi di X?", "cosa sai di Y?"), aggiunge o elabora informazioni proprie, concorda ("esatto", "sì"), pensa ad alta voce, cambia argomento, scherza senza rettificare un fatto, o commenta in generale il comportamento dell'assistente — anche se il messaggio contiene "no", "non è", "in realtà".

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

    def _synthesize_lesson_from_correction(self, prompt_orig: str, risposta_euri: str, correzione: str) -> str | None:
        """Loop 2g: distilla la correzione di Stefano in una LEZIONE (il principio), come la
        reaction-loop — non archivia il testo grezzo. Gira in idle → modello del sogno (Qwen),
        think=False per affidabilità. Ritorna None se vuota (l'audit usa il grezzo come fallback)."""
        msg = (
            f"A una domanda di Stefano — «{(prompt_orig or '')[:300]}» — avevi risposto:\n"
            f"«{(risposta_euri or '')[:500]}»\n\n"
            f"Stefano ti ha CORRETTO:\n«{(correzione or '')[:500]}»\n\n"
            f"Scrivi in prima persona la LEZIONE che ne ricavi: il principio concreto da non "
            f"sbagliare più, e dove ti eri sbagliata. NON un riassunto della correzione, NON un "
            f"ringraziamento — il punto che porti a casa e che potresti dover riaffrontare. Max 3 frasi."
        )
        try:
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": msg}],
                options={"temperature": 0.4, "num_predict": 2500},
                think=False,
            )
            out = (resp.message.content or "")
            if "<channel|>" in out:
                out = out.split("<channel|>", 1)[-1]
            return out.strip() or None
        except Exception as e:
            logger.debug(f"Loop 2g: sintesi lezione fallita — {e}")
            return None

    def _correction_target_ids(self, doc: dict, ctx_memories: list[str]) -> list[str]:
        """
        Se la correzione nomina un soggetto, prova a colpire il nodo giusto invece di
        limitarsi al contesto del turno. Fallback: usa il RAG context del turno.
        """
        if not self._memory_manager:
            return list(dict.fromkeys(doc.get("rag_ctx_ids", []) or []))

        correction_text = doc.get("correzione_user", "") or ""
        prompt_original = doc.get("prompt_original", "") or ""
        candidate_ids: list[str] = []
        scored: list[tuple[int, float, str]] = []
        seen: set[str] = set()

        # Scansione completa ma cheap: il Loop 2g gira in idle, su max 10 signal.
        # Prima ordinazione = overlap con la correzione; così un nodo che contiene
        # "Giada" + i dettagli sbagliati ("Leonardo", "team", "casa") vince su
        # un frammento corretto ma solo parzialmente allineato.
        for key in self._r.scan_iter("euri:memory:*"):
            try:
                raw = self._r.json().get(key, "$")
                if not raw:
                    continue
                doc_candidate = raw[0]
            except Exception:
                continue
            if doc_candidate.get("superseded_by") or doc_candidate.get("source") == "web":
                continue
            mid = doc_candidate.get("id") or str(key).rsplit(":", 1)[-1]
            if not mid or mid in seen:
                continue
            seen.add(mid)
            content = doc_candidate.get("content") or ""
            overlap = self._memory_manager.correction_overlap_score(correction_text, content)
            if overlap <= 0:
                continue
            created_at = float(doc_candidate.get("created_at") or 0)
            suspicion = 0
            if doc_candidate.get("provenance_stale"):
                suspicion += 2
            if doc_candidate.get("requires_verification"):
                suspicion += 1
            if int(doc_candidate.get("audit_flag") or 0) > 0:
                suspicion += 1
            if doc_candidate.get("source") == "loop2e":
                suspicion += 1
            scored.append((overlap, suspicion, created_at, mid))

        if not scored and prompt_original:
            correction_text = prompt_original
            for key in self._r.scan_iter("euri:memory:*"):
                try:
                    raw = self._r.json().get(key, "$")
                    if not raw:
                        continue
                    doc_candidate = raw[0]
                except Exception:
                    continue
                if doc_candidate.get("superseded_by") or doc_candidate.get("source") == "web":
                    continue
                mid = doc_candidate.get("id") or str(key).rsplit(":", 1)[-1]
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                content = doc_candidate.get("content") or ""
                overlap = self._memory_manager.correction_overlap_score(correction_text, content)
                if overlap <= 0:
                    continue
                created_at = float(doc_candidate.get("created_at") or 0)
                suspicion = 0
                if doc_candidate.get("provenance_stale"):
                    suspicion += 2
                if doc_candidate.get("requires_verification"):
                    suspicion += 1
                if int(doc_candidate.get("audit_flag") or 0) > 0:
                    suspicion += 1
                if doc_candidate.get("source") == "loop2e":
                    suspicion += 1
                scored.append((overlap, suspicion, created_at, mid))

        if not scored:
            return list(dict.fromkeys(doc.get("rag_ctx_ids", []) or []))

        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        best_overlap = scored[0][0]
        best_suspicion = scored[0][1]
        return [
            mid
            for overlap, suspicion, _created_at, mid in scored
            if overlap == best_overlap and suspicion == best_suspicion
        ][:3]

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

                # mark-after-act (Codex #2): lo status si scrive DOPO che l'effetto (audit_flag /
                # lesson) è andato. not_a_correction = soft-delete (status "dismissed", audit
                # preservato, evapora col TTL 30gg). Un effetto fallito NON deve marcare il signal
                # 'analyzed' → sennò la correzione è persa senza retry.
                effect_ok = True

                # Azioni differenziate (not_a_correction e ambiguous: nessuna azione)
                if verdict == "bad_memory":
                    target_ids = self._correction_target_ids(doc, ctx_memories)
                    if not target_ids:
                        target_ids = [mid for mid in (doc.get("rag_ctx_ids", []) or []) if mid]
                    for mid in target_ids:
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
                            self._r.json().set(mkey, "$.requires_verification", True)
                            _af = self._r.json().numincrby(mkey, "$.audit_flag", 1)
                            remove_loop2e_candidate(self._r, mid)
                            # Pulse afferente (Fase 1): memoria marcata sospetta = evento interno
                            # da osservare (intero → watch, non ask). audit_flag nel payload per lo
                            # scorer. Fail-open (pulse_emit non solleva mai).
                            pulse_emit(self._r, "audit", "intero", "flagged",
                                       payload={"memory_id": mid,
                                                "audit_flag": (_af[0] if isinstance(_af, list) and _af else _af)},
                                       salience=0.6)
                        except Exception as e:
                            effect_ok = False
                            self._integrity_failure("loop2g-audit_flag", mkey, e)
                            continue
                    logger.info(
                        f"Loop 2g: target subject-memory ids = {', '.join(t[-8:] for t in target_ids[:4])}"
                    )

                elif verdict == "bad_reasoning" and self._memory_manager:
                    # COME la reaction-loop: distilla la correzione in una LEZIONE (il principio
                    # da non sbagliare più), non archiviare il testo grezzo dello sfogo ("ti boccio,
                    # secondo me viene 15") che non è richiamabile come regola. Fallback al grezzo
                    # se la sintesi tace (fail-safe: non si perde mai la correzione).
                    raw = doc.get("correzione_user", "").strip()
                    if raw and len(raw) > 10:
                        lesson_text = self._synthesize_lesson_from_correction(
                            doc.get("prompt_original", ""), doc.get("risposta_euri", ""), raw
                        ) or raw
                        try:
                            self._memory_manager.save_memory(
                                content=lesson_text,
                                category="lesson",
                                tags=["lesson", "from_correction"],
                                source="passive",
                            )
                        except Exception as e:
                            effect_ok = False
                            self._integrity_failure("loop2g-lesson", key, e)

                # Ora marca processato — SOLO se l'effetto è andato (o non c'era). Se fallito, il
                # signal resta 'pending' → riprovato al prossimo ciclo, niente correzione persa.
                if effect_ok:
                    self._settle_correction_quarantine(doc, verdict)
                    self._r.json().set(key, "$.status", "dismissed" if verdict == "not_a_correction" else "analyzed")
                    self._r.json().set(key, "$.verdict", verdict)
                    self._r.json().set(key, "$.analyzed_at", time.time())
                logger.info(f"Loop 2g: {key[-8:]} → {verdict}" + ("" if effect_ok else " (effetto fallito → pending, retry)"))

            logger.info(
                f"Loop 2g: {len(pending)} signal analizzati "
                f"({counts['not_a_correction']} not_a_correction/scartati, {counts['bad_memory']} bad_memory, "
                f"{counts['bad_reasoning']} bad_reasoning, {counts['ambiguous']} ambiguous)"
            )

        except Exception as e:
            logger.error(f"Errore Loop 2g audit corrections: {e}")

    def _settle_correction_quarantine(self, doc: dict, verdict: str) -> None:
        """Chiude la quarantena immediata aperta al capture del correction signal.

        Il capture demuove subito il nodo più probabile per evitare richiamo forte
        nella stessa sessione. Qui il giudizio 2g decide se ripristinare o lasciare
        prudente: bad_memory/ambiguous mantengono requires_verification, mentre
        not_a_correction/bad_reasoning ripristinano il valore precedente.
        """
        sid = doc.get("id")
        try:
            from core.memory_manager import MemoryManager
            deterministic_retraction = bool(doc.get("quarantined_memory_ids")) and MemoryManager._is_immediate_quarantine_correction(
                doc.get("correzione_user", "") or ""
            )
        except Exception:
            deterministic_retraction = False
        for mid in doc.get("quarantined_memory_ids") or []:
            if not mid:
                continue
            mkey = mid if str(mid).startswith("euri:memory:") else f"euri:memory:{mid}"
            try:
                raw = self._r.json().get(mkey, "$")
                mem = raw[0] if raw else {}
                if mem.get("correction_signal_id") != sid:
                    continue
                prev = bool(mem.get("correction_pending_prev_requires_verification"))
                mem["correction_pending"] = False
                self._r.json().set(mkey, "$.correction_pending", False)
                can_restore = (
                    verdict in {"not_a_correction", "bad_reasoning"}
                    and int(mem.get("audit_flag") or 0) <= 0
                    and not deterministic_retraction
                )
                if can_restore:
                    mem["requires_verification"] = prev
                    self._r.json().set(mkey, "$.requires_verification", prev)
                update_loop2e_candidate_index(self._r, mem)
            except Exception as e:
                self._integrity_failure("loop2g-settle-quarantine", mkey, e)

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
        Chiede al modello offline/idle già caldo se una memoria tecnica contiene un fatto
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
        offline/idle se contengono impossibilità oggettive. Non modifica il contenuto, non
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
                remove_loop2e_candidate(self._r, doc.get("id", ""))
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
        modello offline/idle GIÀ CALDO (dream_client via _ollama_chat): niente secondo modello,
        niente swap. Fail-closed: su errore/risposta ambigua ritorna solo il seed, così
        il consolidamento si ferma se non resta un cluster minimo.

        Il frammento 1 è SEMPRE il seed: il cluster viene riordinato col seed in testa (sort
        stabile), così non si dipende dall'ordine KNN. seed_id vuoto → ordine invariato.
        """
        self._last_same_subject_gate_parse_failed = False
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
            f"Il frammento 1 fissa il SOGGETTO/ENTITÀ del cluster. Classifica ogni frammento:\n"
            f"- SAME: parla chiaramente dello stesso soggetto/entità del frammento 1.\n"
            f"- DIFFERENT: parla chiaramente di un altro soggetto/entità.\n"
            f"- UNKNOWN: il soggetto non è esplicito o non è risolvibile con certezza dal testo.\n\n"
            f"Regole dure:\n"
            f"- Un frammento senza soggetto esplicito NON eredita il soggetto del frammento 1.\n"
            f"- Se dovresti tirare a indovinare, classifica UNKNOWN.\n"
            f"- UNKNOWN va escluso dalla consolidazione.\n\n"
            f"Rispondi SOLO con un oggetto JSON su una riga, dove le chiavi sono gli indici "
            f"e i valori sono SAME, DIFFERENT o UNKNOWN. Esempio: {{\"1\":\"SAME\",\"2\":\"UNKNOWN\",\"3\":\"DIFFERENT\"}}"
        )
        try:
            resp = self._ollama_chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 300},
                think=False,
                format="json",
                _timeout=60,
            )
            out = _re.sub(r"<think>.*?</think>", "", resp.message.content or "", flags=_re.DOTALL)
            i, j = out.find("{"), out.rfind("}")
            if i < 0 or j <= i:
                self._last_same_subject_gate_parse_failed = True
                logger.info("Loop 2e gate: output non parsabile → fail-closed seed-only")
                return items[:1]
            try:
                verdicts = json.loads(out[i:j + 1])
            except Exception:
                self._last_same_subject_gate_parse_failed = True
                logger.info("Loop 2e gate: JSON invalido → fail-closed seed-only")
                return items[:1]
            if not isinstance(verdicts, dict):
                self._last_same_subject_gate_parse_failed = True
                logger.info("Loop 2e gate: JSON non oggetto → fail-closed seed-only")
                return items[:1]
            keep = []
            for idx, item in enumerate(items, 1):
                label = str(verdicts.get(str(idx)) or verdicts.get(idx) or "").strip().upper()
                if idx == 1:
                    if label != "SAME":
                        logger.info("Loop 2e gate: seed non SAME → fail-closed seed-only")
                        return items[:1]
                    keep.append(item)
                elif label == "SAME":
                    keep.append(item)
            if not keep:
                return items[:1]
            if len(keep) < len(items):
                dropped = len(items) - len(keep)
                logger.info(f"Loop 2e gate: {dropped} frammento/i di soggetto diverso esclusi dal consolidamento")
            return keep
        except Exception as e:
            self._last_same_subject_gate_parse_failed = True
            logger.debug(f"Loop 2e same-subject gate fallito (fail-closed): {e}")
            return items[:1]

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
        # Recency gate: il consolidamento vuole memorie ATTIVE, non fossili. recalled_count è
        # un contatore MONOTÒNO (mai decade) → satura con l'età e smette di discriminare
        # (misurato 19/06: i ≥6 hanno ~33gg di età e ultimo richiamo ~19gg fa; 75% delle
        # memorie a ≥3). Stessa trappola dell'anisotropia: soglia ASSOLUTA su quantità che
        # cresce = no-op col tempo. Cura di radice: NON forzare decay sul contatore (serve la
        # semantica "è mai servita?" al pruning Loop 2d), ma instradare QUESTO consumatore al
        # segnale di recency (last_recalled_at, già scritto su touch=True). Finestra 30gg:
        # accomoda le fasi di test di produzione LUNGHE di Stefano (se ne parla oggi, la prova
        # parte tra settimane → la memoria si riattiva e RIENTRA nel pool al momento giusto).
        try:
            # 1. Raccogli candidati: prima prova l'indice leggero ordinato, poi fallback allo
            # scan canonico. Il JSON resta la fonte di verità: zset_loop2e_candidates rilegge e
            # ri-valida ogni ID, rimuovendo gli stale. Se l'indice è assente/vuoto, comportamento
            # identico al pre-ZSET (scan completo).
            candidates, used_index = zset_loop2e_candidates(self._r)
            if not used_index:
                candidates = scan_loop2e_candidates(self._r)
            logger.debug(
                f"Loop 2e: candidati da {'ZSET' if used_index else 'SCAN'} = {len(candidates)}"
            )

            if not candidates:
                logger.debug("Loop 2e: nessun candidato qualificato")
                return

            # Indice rapido degli ID qualificati — recalled_count non è nel
            # schema RediSearch quindi non possiamo filtrare via KNN return_fields.
            qualified_by_id = {doc.get("id", ""): doc for doc in candidates}

            consolidated = 0
            gate_parse_failures = 0
            MAX_GATE_PARSE_FAILURES = 3
            gate_attempts = 0
            MAX_GATE_ATTEMPTS = 30

            for seed in candidates:
                if consolidated >= MAX_PER_CYCLE:
                    break
                if gate_attempts >= MAX_GATE_ATTEMPTS:
                    logger.info(
                        f"Loop 2e gate: {gate_attempts} tentativi raggiunti → "
                        "stop consolidamento per questo ciclo"
                    )
                    break
                if gate_parse_failures >= MAX_GATE_PARSE_FAILURES:
                    logger.info(
                        f"Loop 2e gate: {gate_parse_failures} parse-fail consecutivi → "
                        "stop consolidamento per questo ciclo"
                    )
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
                gate_attempts += 1
                cluster = self._same_subject_gate(cluster, seed_domain, seed_id)
                if getattr(self, "_last_same_subject_gate_parse_failed", False):
                    gate_parse_failures += 1
                else:
                    gate_parse_failures = 0
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
- Non attribuire a un soggetto attributi provenienti da un frammento con soggetto implicito o non chiaro
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
                # Opzione A: marca i frammenti come "spesi" — restano richiamabili ma non
                # rientrano in future consolidazioni (no riuso, no duplicazione nodo-per-nodo).
                # Reversibile: togliere consolidated_into li ri-ammette.
                for cid in cluster_ids:
                    try:
                        self._r.json().set(f"euri:memory:{cid}", "$.consolidated_into", mid)
                        remove_loop2e_candidate(self._r, cid)
                    except Exception as e:
                        # mark-after-act (Codex #3): se il frammento non viene marcato "speso",
                        # rientra in future consolidazioni → duplicato (il meccanismo che teneva
                        # vivo il Leonardo). Non più silenzioso: tracciato.
                        self._integrity_failure("loop2e-consolidated_into", f"euri:memory:{cid}", e)
                risk = self._consolidation_source_risk(cluster_ids, qualified_by_id)
                self._r.json().set(key, "$.consolidation_risk", risk)
                if risk["level"] != "ok":
                    self._r.json().set(key, "$.source_audit_flags", risk["audit_flagged"])
                    self._r.json().set(key, "$.requires_verification", True)

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
        Previene la proliferazione di nodi ridondanti tra cicli idle.
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
