"""
CRUD Redis per memories, todos, notes.
Tutte le operazioni usano Redis JSON + RediSearch.
"""
import re
import json
import time
import uuid
from datetime import datetime
from loguru import logger

import redis as redis_lib
from redis.commands.search.query import Query

import config
from utils.date_utils import now, to_timestamp, from_timestamp, format_datetime
from core.domain_gater import assign_domain, domain_aware_search, neighbor_domains
from utils.obsidian_sync import write_memory


_TTL_BY_SOURCE: dict[str, int] = {
    "passive":      90,
    "reflection":   90,
    "conversation": 90,
    "episode":       7,
    "web":          60,   # ricerche web — info può invecchiare
}
# Memorie user/teach/obsidian_vault non hanno TTL automatico — non compaiono qui.

# Reflection dedup latest-wins: distanza cosine <= 0.10 equivale a similarita' >= 0.90.
REFLECTION_DEDUP_MAX_DIST = 0.10


class MemoryManager:
    def __init__(self, r: redis_lib.Redis, embedder=None):
        self.r = r
        self._embedder = embedder  # core.embedder.Embedder — può essere None (fallback keyword)
        # Cache "active domains" per Filtro del Risveglio (TTL 5 min).
        # Tuple (set[str], timestamp). None = mai computato.
        self._active_domains_cache: tuple[set[str], float] | None = None

    # ──────────────────────────────────────────
    # MEMORIES (ricordi a lungo termine)
    # ──────────────────────────────────────────

    def save_memory(self, content: str, category: str = "personale", tags: list[str] = None, source: str = "user", expires_at: datetime | None = None) -> str | None:
        # Memory Guard: scansione anti-poisoning sull'ingest. Da fonte non fidata
        # (web/mobile_in) un contenuto con injection/esfiltrazione viene rifiutato
        # (ritorna None); da fonte fidata si salva ma marcato in safety_flag.
        from core.memory_guard import evaluate
        guard = evaluate(content, source)
        if guard["reject"]:
            return None

        mid = str(uuid.uuid4())
        key = f"euri:memory:{mid}"
        ts = now()

        # Auto-assegna expires_at in base alla source (finestra scorrevole)
        if expires_at is None:
            ttl_days = _TTL_BY_SOURCE.get(source)
            if ttl_days:
                from datetime import timedelta
                expires_at = ts + timedelta(days=ttl_days)

        # Embedding semantico
        embedding = None
        if self._embedder and self._embedder.available:
            vec = self._embedder.encode(content, mode="passage")
            if vec is not None:
                embedding = vec.tolist()

        # Contesto temporale arricchito
        hour = ts.hour
        if hour < 12:
            time_of_day = "mattina"
        elif hour < 18:
            time_of_day = "pomeriggio"
        else:
            time_of_day = "sera"
        _DAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        context_meta = {
            "day_of_week": _DAYS_IT[ts.weekday()],
            "time_of_day": time_of_day,
            "session_type": source,
        }

        # Domain assignment auto-scoperto via LLM, disambiguato dai vicini semantici (P1).
        # I suggerimenti vengono dalla memoria stessa di Euri (KNN), mai da liste cablate:
        # su un DB vuoto non ci sono vicini e si ricade nel comportamento base.
        hint_domains = None
        if embedding is not None:
            try:
                vec_bytes = vec.astype("float32").tobytes()
                hint_domains = neighbor_domains(vec_bytes, self.r, k=8)
            except Exception as e:
                logger.debug(f"P1 neighbor_domains fallito: {e}")
        domain_label = assign_domain(content, hint_domains=hint_domains)

        # Flag dati numerici non verificati (dosaggi, percentuali, misure)
        import re as _re
        _NUM_PAT = _re.compile(
            r'\b\d+[.,]?\d*\s*(%|g|kg|ml|l|mg|ppm|bar|°[Cc]|rpm|mm|cm|m\b)|'
            r'\b\d+[.,]\d+\b|'
            r'\b(grado|gradi)\s+\d+\b',
            _re.IGNORECASE
        )
        requires_verification = bool(_NUM_PAT.search(content))

        doc = {
            "id": mid,
            "content": content,
            "category": category,
            "source": source,
            "domain": domain_label,
            "requires_verification": requires_verification,
            "created_at": to_timestamp(ts),
            "due_at": None,
            "expires_at": to_timestamp(expires_at) if expires_at else None,
            "recalled_count": 0,
            "last_recalled_at": None,
            "tags": tags or [],
            "embedding": embedding,
            "context_meta": context_meta,
            "safety_flag": guard["safety_flag"],  # [] se pulito; categorie se contenuto sospetto da fonte fidata
        }
        self.r.json().set(key, "$", doc)
        if expires_at:
            # expireat è separato dal JSON.SET (Codex #1): se fallisce, la memoria resterebbe
            # SENZA TTL (vive per sempre) mentre expires_at dice il contrario. Tracciato, non
            # ingoiato; e NON facciamo crashare il save (meglio una memoria senza TTL che persa).
            try:
                self.r.expireat(key, expires_at)
            except Exception as e:
                self._record_integrity_failure("expireat-save", key, e)
        logger.info(f"Memory salvata: {mid}")

        # Sincronizza verso Obsidian Vault (se abilitato)
        try:
            write_memory(doc)
        except Exception as e:
            logger.debug(f"Obsidian sync memory error: {e}")

        return mid

    # Identificatori specifici: acronimi (MFI, DCP), codici con trattino (TEST-ALPHA, PPR-738P), decimali (4.7, 0.35)
    _IDENTIFIER_RE = re.compile(
        r'\b[A-Z]{2,}(?:-[A-Z0-9]+)*\b|\b\d+[.,]\d+\b'
    )

    def search_memories(
        self,
        query: str,
        limit: int = 5,
        source_filter: list[str] | None = None,
        touch: bool = True,
    ) -> list[dict]:
        """
        Ricerca a tre livelli:
        1. Identifier-first: acronimi, codici, numeri decimali → keyword search diretta, risultati in cima
        2. Domain-gated KNN: ricerca semantica nel dominio assegnato alla query
        3. Hybrid fill: _search_hybrid riempie eventuali slot rimasti
        Garantisce che fatti specifici (MFI lotto, concentrazioni, codici progetto) non vengano
        sepolti da memorie semanticamente centrali già consolidate nello stesso dominio.

        touch=True rinforza solo i risultati restituiti: recalled_count, last_recalled_at
        e TTL scorrevole. Usa touch=False per audit, UI diagnostica e test read-only.
        """
        if self._embedder and self._embedder.available and query != "*":
            merged: list[dict] = []
            seen_uuids: set[str] = set()

            # Livello 1 — identifier-first keyword search
            identifiers = self._IDENTIFIER_RE.findall(query)
            if identifiers:
                id_query = " | ".join(identifiers)
                id_results = self._search_keyword(id_query, limit, source_filter=source_filter, touch=False)
                for r in id_results:
                    uid = r.get("id", "")
                    if uid not in seen_uuids:
                        merged.append(r)
                        seen_uuids.add(uid)

            # Livello 2 — domain-gated semantic
            semantic = domain_aware_search(query, self._embedder, self.r, limit)
            semantic = [r for r in semantic if not r.get("superseded_by")]
            if source_filter is not None:
                semantic = [r for r in semantic if r.get("source") in source_filter]
            for r in semantic:
                uid = r["id"].replace("euri:memory:", "")
                if uid not in seen_uuids:
                    merged.append(r)
                    seen_uuids.add(uid)

            # Livello 3 — hybrid fill se ancora sotto il limite
            if len(merged) < limit:
                hybrid = self._search_hybrid(query, limit, source_filter=source_filter, touch=False)
                for r in hybrid:
                    uid = r.get("id", "")
                    if uid not in seen_uuids and len(merged) < limit:
                        merged.append(r)
                        seen_uuids.add(uid)

            logger.debug(
                f"Search 3-livelli: {len(merged)} risultati "
                f"(id:{len(identifiers)} token, semantic:{len(semantic)}, fill)"
            )

            # Invariante A — down-rank di provenienza (centralizzato in _demote_provenance_stale).
            merged = self._demote_provenance_stale(merged)

            results = merged[:limit]
            if touch:
                self._touch_memories(results)
            return results

        return self._search_keyword(query, limit, source_filter=source_filter, touch=touch)

    @staticmethod
    def _sanitize_query(text: str) -> str:
        """Rimuove caratteri speciali RediSearch da input utente grezzo."""
        clean = re.sub(r'[^\w\sàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]', ' ', text)
        return ' '.join(clean.split()) or "*"

    @staticmethod
    def _source_prefix(source_filter: list[str] | None) -> str:
        """Restituisce il prefisso RediSearch per filtrare per source, o stringa vuota."""
        if not source_filter:
            return ""
        escaped = "|".join(source_filter)
        return f"@source:{{{escaped}}} "

    def _search_keyword(
        self,
        query: str,
        limit: int,
        source_filter: list[str] | None = None,
        touch: bool = True,
    ) -> list[dict]:
        try:
            prefix = self._source_prefix(source_filter).strip()
            safe_query = query if query == "*" else self._sanitize_query(query)

            # Se abbiamo sia un filtro source che una query testuale, li isoliamo con parentesi
            # altrimenti operatori testuali come '|' (OR) rompono l'AST di RediSearch.
            if prefix and safe_query != "*":
                full_query = f"({prefix}) ({safe_query})"
            elif prefix and safe_query == "*":
                full_query = prefix  # Se query è "*", usiamo solo il filtro tag
            else:
                full_query = safe_query
                
            q = Query(full_query).paging(0, limit).sort_by("created_at", asc=False)
            results = self.r.ft("idx:memories").search(q)
            return self._hydrate(results.docs, touch=touch)
        except Exception as e:
            logger.error(f"Errore ricerca keyword memories: {e}")
            return []
        
    def _search_semantic(self, query: str, limit: int, source_filter: list[str] | None = None) -> list[dict]:
        """KNN search tramite embedding — restituisce docs ordinati per distanza coseno."""
        vec = self._embedder.encode(query, mode="query")
        if vec is None:
            return []
        try:
            from redis.commands.search.query import Query as RQuery
            # Con decode_responses=True il client non accetta bytes come query_params
            # — usiamo una connessione raw per il solo comando FT.SEARCH vettoriale
            import redis as _redis
            import config
            raw_r = _redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                decode_responses=False,
            )
            prefilter = self._source_prefix(source_filter).strip() or "*"
            q = (RQuery(f"({prefilter})=>[KNN {limit * 2} @embedding $vec AS vec_score]")
                 .sort_by("vec_score")
                 .paging(0, limit * 2)
                 .return_fields("vec_score")
                 .dialect(2))
            raw_results = raw_r.ft("idx:memories").search(q, query_params={"vec": vec.tobytes()})

            # Estrai doc IDs e score
            id_score = {}
            for doc in raw_results.docs:
                doc_id = doc.id.decode() if isinstance(doc.id, bytes) else doc.id
                score_raw = getattr(doc, "vec_score", b"1.0")
                score = float(score_raw.decode() if isinstance(score_raw, bytes) else score_raw)
                id_score[doc_id] = score

            # Idrata i documenti usando il client principale
            docs = []
            for doc_id, score in sorted(id_score.items(), key=lambda x: x[1]):
                data = self.r.json().get(doc_id, "$")
                if data:
                    item = data[0]
                    if item.get("superseded_by"):  # soft-deleted da Loop 2f
                        continue
                    item["_created_at"] = from_timestamp(item.get("created_at"))
                    item["_vec_score"] = score
                    docs.append(item)
            return docs[:limit]
        except Exception as e:
            logger.error(f"Errore ricerca semantica: {e}")
            return []

    def _search_hybrid(
        self,
        query: str,
        limit: int,
        source_filter: list[str] | None = None,
        touch: bool = True,
    ) -> list[dict]:
        """
        Merge ricerca semantica + keyword.
        Priorità: doc trovati da entrambi > solo semantici > solo keyword.
        """
        kw_list = self._safe_keywords(query)
        kw_query = " | ".join(kw_list[:6]) if kw_list else None

        sem_docs = self._search_semantic(query, limit, source_filter=source_filter)
        kw_docs = self._search_keyword(kw_query, limit, source_filter=source_filter, touch=False) if kw_query else []

        sem_ids = {d["id"] for d in sem_docs}
        kw_ids = {d["id"] for d in kw_docs}
        both_ids = sem_ids & kw_ids

        merged: list[dict] = []
        seen: set[str] = set()

        # Prima: trovati da entrambi (più rilevanti), ordinati per score semantico
        for d in sem_docs:
            if d["id"] in both_ids and d["id"] not in seen:
                merged.append(d)
                seen.add(d["id"])

        # Poi: solo semantici
        for d in sem_docs:
            if d["id"] not in seen:
                merged.append(d)
                seen.add(d["id"])

        # Infine: solo keyword (possibile recupero di memorie senza embedding)
        for d in kw_docs:
            if d["id"] not in seen:
                merged.append(d)
                seen.add(d["id"])

        results = merged[:limit]
        if touch:
            self._touch_memories(results)
        return results

    def _hydrate(self, raw_docs, touch: bool = True) -> list[dict]:
        """Carica i documenti completi da Redis JSON; opzionalmente rinforza i richiami."""
        docs = []
        for doc in raw_docs:
            data = self.r.json().get(doc.id, "$")
            if data:
                item = data[0]
                if item.get("superseded_by"):  # soft-deleted da Loop 2f — escludi dalla ricerca
                    continue
                item["_created_at"] = from_timestamp(item.get("created_at"))
                docs.append(item)
        if touch:
            self._touch_memories(docs)
        return docs

    def _demote_provenance_stale(self, results: list[dict]) -> list[dict]:
        """
        Invariante A — down-rank di provenienza, CENTRALIZZATO. Sposta in fondo i nodi
        la cui fondamenta è caduta (`provenance_stale`, propagato dal Dream Engine) —
        demozione, non esclusione (fail-safe). Applicato a TUTTI i path che iniettano
        contesto (semantico, recency, temporale), non solo alla ricerca semantica: senza,
        un nodo marcio poteva ancora affiorare per pura recenza prima delle ancore pulite
        (gap trovato in review). Legge il flag solo per i candidati finali; sort stabile.
        """
        if not results:
            return results
        def _stale(rec):
            if "provenance_stale" in rec:
                return bool(rec.get("provenance_stale"))
            mid = rec.get("id", "")
            k = mid if str(mid).startswith("euri:memory:") else f"euri:memory:{mid}"
            try:
                v = self.r.json().get(k, "$.provenance_stale")
                return bool(v and v[0])
            except Exception:
                return False
        results.sort(key=lambda rec: 1 if _stale(rec) else 0)
        return results

    def _touch_memories(self, memories: list[dict]):
        """Rinforza memorie realmente usate in retrieval cognitivo."""
        if not memories:
            return
        ts_now = to_timestamp(now())
        for item in memories:
            mid = item.get("id")
            if not mid:
                continue
            key = mid if str(mid).startswith("euri:memory:") else f"euri:memory:{mid}"
            try:
                self.r.json().numincrby(key, "$.recalled_count", 1)
                self.r.json().set(key, "$.last_recalled_at", ts_now)
                ttl_days = _TTL_BY_SOURCE.get(item.get("source", ""))
                if ttl_days:
                    from datetime import timedelta
                    new_exp_dt = now() + timedelta(days=ttl_days)
                    self.r.json().set(key, "$.expires_at", to_timestamp(new_exp_dt))
                    self.r.expireat(key, new_exp_dt)
            except Exception as e:
                logger.debug(f"Touch memory fallito per {key}: {e}")

    def get_expiring_memories(self, days_ahead: int = 7) -> list[dict]:
        """Restituisce memorie con expires_at entro days_ahead giorni (escluse user/teach/obsidian)."""
        from datetime import timedelta
        cutoff = to_timestamp(now() + timedelta(days=days_ahead))
        now_ts = to_timestamp(now())
        results = []
        for key in self.r.scan_iter("euri:memory:*"):
            try:
                d = self.r.json().get(key, "$")
                if not d:
                    continue
                doc = d[0]
                exp = doc.get("expires_at")
                if exp and now_ts < exp <= cutoff:
                    doc["_key"] = key
                    results.append(doc)
            except Exception:
                continue
        return results

    def get_recent_memories(
        self,
        limit: int = 10,
        source_filter: list[str] | None = None,
        touch: bool = True,
    ) -> list[dict]:
        # Down-rank di provenienza anche sulla recency (gap di review F3): la base
        # iniettata da _build_context per pura recenza non deve far affiorare un nodo
        # marcio prima delle ancore pulite.
        return self._demote_provenance_stale(
            self._search_keyword("*", limit=limit, source_filter=source_filter, touch=touch)
        )

    def search_memories_by_timerange(
        self,
        ts_start: float,
        ts_end: float,
        limit: int = 5,
        touch: bool = True,
    ) -> list[dict]:
        """Recupera memorie in un range temporale tramite filtro numerico su created_at."""
        try:
            q = (Query(f"@created_at:[{ts_start} {ts_end}]")
                 .sort_by("created_at", asc=False)
                 .paging(0, limit))
            results = self.r.ft("idx:memories").search(q)
            return self._demote_provenance_stale(self._hydrate(results.docs, touch=touch))
        except Exception as e:
            logger.error(f"Errore ricerca temporale: {e}")
            return []

    def get_recent_reflections(self, limit: int = 2, touch: bool = True) -> list[dict]:
        """Restituisce le reflection più recenti generate da Loop 2a."""
        return self._search_keyword("*", limit=limit, source_filter=["reflection"], touch=touch)

    # ──────────────────────────────────────────
    # TODOS (promemoria con scadenza)
    # ──────────────────────────────────────────

    def save_todo(self, content: str, due_at: datetime = None, priority: str = "media", tags: list[str] = None) -> str:
        tid = str(uuid.uuid4())
        key = f"euri:todo:{tid}"
        ts = now()
        doc = {
            "id": tid,
            "content": content,
            "created_at": to_timestamp(ts),
            "due_at": to_timestamp(due_at),
            "completed_at": None,
            "priority": priority,
            "status": "pending",
            "reminded_count": 0,
            "last_reminded_at": None,
            "tags": tags or [],
        }
        self.r.json().set(key, "$", doc)
        logger.info(f"Todo salvato: {tid} — scadenza: {format_datetime(due_at)}")
        return tid

    def get_todos_today(self) -> list[dict]:
        t = now()
        start = t.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = t.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        return self._query_todos(f"@due_at:[{start} {end}] @status:{{pending}}")

    def get_overdue_todos(self) -> list[dict]:
        ts = now().timestamp()
        return self._query_todos(f"@due_at:[-inf ({ts}] @status:{{pending}}")

    def get_pending_todos(self) -> list[dict]:
        return self._query_todos("@status:{pending}")

    def get_due_todos_now(self, window_seconds: int = 60) -> list[dict]:
        """Todo in scadenza entro i prossimi window_seconds (default: entro questo minuto)."""
        ts_now = now().timestamp()
        ts_end = ts_now + window_seconds
        return self._query_todos(
            f"@due_at:[{ts_now - window_seconds} {ts_end}] @status:{{pending}} @reminded_count:[0 0]"
        )

    def mark_reminded(self, todo_id: str):
        key = f"euri:todo:{todo_id}"
        self.r.json().numincrby(key, "$.reminded_count", 1)
        self.r.json().set(key, "$.last_reminded_at", to_timestamp(now()))

    def complete_todo(self, todo_id: str) -> bool:
        key = f"euri:todo:{todo_id}"
        if not self.r.exists(key):
            return False
        self.r.json().set(key, "$.status", "done")
        self.r.json().set(key, "$.completed_at", to_timestamp(now()))
        logger.info(f"Todo completato: {todo_id}")
        return True

    def find_todo_by_content(self, query: str) -> list[dict]:
        return self._query_todos(self._sanitize_query(query), limit=3)

    @staticmethod
    def _safe_keywords(content: str) -> list[str]:
        """Estrae parole chiave sicure per RediSearch — rimuove stop words e caratteri speciali."""
        _STOP = {"di", "il", "la", "lo", "le", "gli", "un", "una", "uno", "e", "è",
                 "a", "da", "in", "su", "per", "con", "che", "ho", "devo", "fare",
                 "del", "della", "dei", "degli", "al", "alla", "ai", "agli",
                 # parole che collidono con la sintassi RediSearch
                 "todo", "note", "tag", "and", "or", "not",
                 # nomi propri universali — compaiono in quasi ogni memoria e non discriminano
                 "stefano", "euri"}
        import re
        words = re.findall(r"[a-zA-ZàèéìòùÀÈÉÌÒÙ]{4,}", content.lower())
        return [w for w in words if w not in _STOP]

    def is_duplicate_todo(self, content: str) -> bool:
        """True se esiste già un todo pendente con contenuto simile creato oggi."""
        words = self._safe_keywords(content)
        if not words:
            return False
        t = now()
        start = t.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = t.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        keyword_query = " | ".join(words[:5])
        results = self._query_todos(
            f"({keyword_query}) @status:{{pending}} @created_at:[{start} {end}]",
            limit=1
        )
        if results:
            logger.info(f"Duplicato todo: '{content[:50]}' ~ '{results[0]['content'][:50]}'")
            return True
        return False

    def is_duplicate_memory(self, content: str, llm_probe_fn=None) -> bool:
        """
        True se esiste già una memoria semanticamente equivalente.

        Logica a 3 livelli (con embedding se disponibile, altrimenti Jaccard):
          Cosine ≥ 0.92  → duplicato certo (skip LLM probe)
          Cosine 0.70-0.92 → zona grigia → Jaccard + LLM probe
          Cosine < 0.70  → contenuto diverso

        Fallback senza embedder: Jaccard keyword a 3 livelli.
        """
        # ── Fast path: cosine similarity con embedding ──
        if self._embedder and self._embedder.available:
            import numpy as np
            vec_new = self._embedder.encode(content)
            if vec_new is not None:
                candidates = self._search_semantic(content, limit=3)
                for cand in candidates:
                    score = cand.get("_vec_score", 1.0)  # COSINE distance (0=identico, 1=opposto)
                    similarity = 1.0 - score
                    if similarity >= 0.92:
                        logger.info(f"Duplicato memory (cosine={similarity:.2f}): '{content[:50]}' ~ '{cand['content'][:50]}'")
                        return True
                    elif similarity >= 0.70:
                        # Zona grigia: chiedi se A aggiunge fatti nuovi rispetto a B
                        if llm_probe_fn is not None:
                            question = (
                                f"La frase A contiene informazioni concrete non già presenti in B? "
                                f"(numeri diversi, componenti diversi, processi diversi, date, misure specifiche)\n"
                                f"A: {content}\nB: {cand['content']}\n"
                                f"Rispondi SÌ se A aggiunge qualcosa di nuovo, NO se A non aggiunge nulla di nuovo rispetto a B."
                            )
                            answer = llm_probe_fn(question)
                            adds_new = answer.strip().upper().startswith(("SÌ", "SI", "YES"))
                        else:
                            adds_new = not self._llm_is_same_content(content, cand["content"])
                        if not adds_new:
                            logger.info(f"Duplicato memory (cosine={similarity:.2f}+LLM): '{content[:50]}' ~ '{cand['content'][:50]}'")
                            return True
                return False  # nessun duplicato trovato via embedding

        # ── Fallback: Jaccard keyword ──
        words = self._safe_keywords(content)
        if len(words) < 2:
            return False
        keyword_query = " | ".join(words[:5])
        results = self._search_keyword(keyword_query, limit=3, touch=False)
        if not results:
            return False
        content_kws = set(words)
        for r in results:
            candidate_kws = set(self._safe_keywords(r["content"]))
            if not candidate_kws:
                continue
            overlap = content_kws & candidate_kws
            min_kws = min(len(content_kws), len(candidate_kws))
            if min_kws == 0:
                continue
            ratio = len(overlap) / min_kws
            if ratio >= 0.5:
                logger.info(f"Duplicato memory (Jaccard≥50%): '{content[:50]}' ~ '{r['content'][:50]}'")
                return True
            elif ratio >= 0.2:
                if llm_probe_fn is not None:
                    question = (
                        f"La frase A contiene informazioni concrete non già presenti in B? "
                        f"(numeri diversi, componenti diversi, processi diversi, date, misure specifiche)\n"
                        f"A: {content}\nB: {r['content']}\n"
                        f"Rispondi SÌ se A aggiunge qualcosa di nuovo, NO se A non aggiunge nulla di nuovo rispetto a B."
                    )
                    answer = llm_probe_fn(question)
                    adds_new = answer.strip().upper().startswith(("SÌ", "SI", "YES"))
                else:
                    adds_new = not self._llm_is_same_content(content, r["content"])
                if not adds_new:
                    logger.info(f"Duplicato memory (Jaccard+LLM): '{content[:50]}' ~ '{r['content'][:50]}'")
                    return True
        return False

    def find_similar_memory(self, content: str) -> dict | None:
        """
        Memoria esistente più simile a `content`, per il merge costruttivo del SAVE
        esplicito (core/save_service). Ritorna {'id', 'content', 'similarity'} o None.
        Usa l'embedding; senza embedder non fa nulla (None → il chiamante salva nuovo).
        similarity = 1 - distanza coseno (1.0 = identico).
        """
        if not (self._embedder and self._embedder.available):
            return None
        candidates = self._search_semantic(content, limit=1)  # già esclude i superseded
        if not candidates:
            return None
        cand = candidates[0]
        return {
            "id": cand.get("id"),
            "content": cand.get("content", ""),
            "similarity": 1.0 - cand.get("_vec_score", 1.0),
        }

    def _record_integrity_failure(self, kind: str, key: str, err) -> None:
        """Path di SCRITTURA della memoria: un fallimento NON deve sparire in DEBUG. Lo rende
        RUMOROSO (WARNING) e TRACCIATO (stream euri:integrity:failures, cap 1000) — così
        sappiamo QUANDO la memoria non ha fatto ciò che credeva di aver fatto. È il fratello
        tecnico del 'say≠do': una scrittura fallita e ingoiata è corruzione invisibile, e
        l'integrità della memoria è l'intera tesi. Fail-safe: se anche il tracking salta, il
        WARNING è già uscito (non incateniamo i fallimenti)."""
        logger.warning(f"INTEGRITÀ memoria: scrittura '{kind}' fallita su {key}: {err}")
        try:
            self.r.xadd(
                "euri:integrity:failures",
                {"kind": kind, "key": str(key), "err": str(err)[:300], "ts": to_timestamp(now())},
                maxlen=1000, approximate=True,
            )
        except Exception:
            pass

    def supersede_memory(self, old_id: str, new_id: str) -> bool:
        """Soft-delete della memoria vecchia puntando alla nuova (convenzione Loop 2f:
        superseded_by = id stringa). Il retrieval esclude già i superseded.

        Ritorna True se riuscito. Il fallimento NON è più silenzioso (era logger.debug →
        invisibile): lascerebbe vecchia E nuova entrambe attive — merge parziale (Codex #3).
        Un retry copre il blip Redis transitorio; poi traccia via _record_integrity_failure."""
        key = old_id if str(old_id).startswith("euri:memory:") else f"euri:memory:{old_id}"
        for attempt in (1, 2):
            try:
                self.r.json().set(key, "$.superseded_by", new_id)
                return True
            except Exception as e:
                if attempt == 2:
                    self._record_integrity_failure("supersede", key, e)
        return False

    def supersede_duplicate_reflections(
        self,
        new_id: str,
        domain: str,
        content: str,
        *,
        max_supersede: int = 10,
    ) -> int:
        """
        Dedup latest-wins per reflection generate dai loop 2a/2h.

        Dopo aver salvato una nuova reflection, cerca reflection quasi-identiche nello
        stesso dominio (cosine distance <= REFLECTION_DEDUP_MAX_DIST) e marca le vecchie
        con superseded_by=new_id. Soft-delete reversibile: niente delete, niente touch,
        niente modifiche a contenuto/recalled_count/TTL.

        Fail-open: qualsiasi errore ritorna 0, lasciando la nuova reflection salvata.
        Le note di confronto 2f ("[confronto] ...") sono esplicitamente escluse.
        """
        if not new_id or not domain or not content:
            return 0
        if content.strip().lower().startswith("[confronto]"):
            return 0
        if not (self._embedder and self._embedder.available):
            return 0

        try:
            vec = self._embedder.encode(content, mode="query")
            if vec is None:
                return 0

            raw_r = redis_lib.Redis(
                host=config.REDIS_HOST, port=config.REDIS_PORT,
                db=config.REDIS_DB, decode_responses=False,
            )
            safe_domain = domain.replace(" ", "\\ ")
            k = max(max_supersede + 5, 12)
            q = (
                Query(
                    f"(@domain:{{{safe_domain}}} @source:{{reflection}})"
                    f"=>[KNN {k} @embedding $vec AS dup_score]"
                )
                .sort_by("dup_score")
                .return_fields("id", "dup_score")
                .dialect(2)
            )
            res = raw_r.ft("idx:memories").search(
                q, query_params={"vec": vec.astype("float32").tobytes()}
            )

            superseded = 0
            for doc in res.docs:
                raw_id = doc.id.decode() if isinstance(doc.id, bytes) else str(doc.id)
                old_id = raw_id.replace("euri:memory:", "")
                if old_id == new_id:
                    continue

                raw_score = getattr(doc, "dup_score", b"1.0")
                dist = float(raw_score.decode() if isinstance(raw_score, bytes) else raw_score)
                if dist > REFLECTION_DEDUP_MAX_DIST:
                    continue

                old_key = f"euri:memory:{old_id}"
                old_raw = self.r.json().get(old_key, "$")
                if not old_raw:
                    continue
                old_doc = old_raw[0]
                if old_doc.get("superseded_by"):
                    continue
                if old_doc.get("source") != "reflection":
                    continue
                if (old_doc.get("content") or "").strip().lower().startswith("[confronto]"):
                    continue

                self.supersede_memory(old_id, new_id)
                superseded += 1
                if superseded >= max_supersede:
                    break

            if superseded:
                logger.info(
                    f"Reflection dedup (latest-wins): {superseded} soppiantate "
                    f"(dominio {domain})"
                )
            return superseded
        except Exception as e:
            logger.debug(f"Reflection dedup latest-wins fallito: {e}")
            return 0

    @staticmethod
    def _llm_is_same_content(a: str, b: str) -> bool:
        """True se A non aggiunge informazioni concrete rispetto a B (= è un duplicato)."""
        try:
            from core.ollama_client import chat_client
            import config
            prompt = (
                f"La frase A contiene informazioni concrete non già presenti in B? "
                f"(numeri diversi, componenti diversi, processi diversi, date, misure specifiche)\n"
                f"A: {a}\n"
                f"B: {b}\n"
                f"Rispondi solo SÌ o NO."
            )
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 5},
                think=False,
            )
            result = (response.message.content or "").strip().upper()
            adds_new = result.startswith("SÌ") or result.startswith("SI") or result.startswith("YES")
            return not adds_new  # True = duplicato (non aggiunge nulla)
        except Exception:
            return False  # In caso di errore, lascia passare

    def get_passive_memory_stats(self) -> dict:
        """Statistiche sulle memorie passive per l'audit."""
        all_memories = self.get_recent_memories(limit=500, touch=False)
        passive = [m for m in all_memories if m.get("source") == "passive"]
        user_saved = [m for m in all_memories if m.get("source") == "user"]
        teach = [m for m in all_memories if m.get("source") == "teach"]
        return {
            "total": len(all_memories),
            "passive": passive,
            "user_saved": len(user_saved),
            "teach": len(teach),
        }

    def _query_todos(self, query: str, limit: int = 20) -> list[dict]:
        try:
            q = Query(query).paging(0, limit).sort_by("due_at", asc=True)
            results = self.r.ft("idx:todos").search(q)
            docs = []
            for doc in results.docs:
                data = self.r.json().get(doc.id, "$")
                if data:
                    item = data[0]
                    item["_due_at"] = from_timestamp(item.get("due_at"))
                    item["_created_at"] = from_timestamp(item.get("created_at"))
                    docs.append(item)
            return docs
        except Exception as e:
            logger.error(f"Errore query todos: {e}")
            return []

    # ──────────────────────────────────────────
    # NOTES (appunti per categoria)
    # ──────────────────────────────────────────

    def save_note(self, content: str, category: str = "personale", tags: list[str] = None, source: str = "user") -> str:
        nid = str(uuid.uuid4())
        key = f"euri:note:{nid}"
        doc = {
            "id": nid,
            "content": content,
            "category": category,
            "source": source,
            "created_at": to_timestamp(now()),
            "tags": tags or [],
        }
        self.r.json().set(key, "$", doc)
        logger.info(f"Nota salvata: {nid}")
        return nid

    def _active_domains(self, days: int = 30) -> set[str]:
        """
        Domini con almeno una memoria "curata da Stefano" negli ultimi `days` giorni.
        Sorgenti operative = config.INSIGHT_ACTIVE_SOURCES (default teach/user/reflection).
        passive e conversation escluse: sono spugne ambient che catturano ogni nome
        di passaggio, neutralizzando il filtro. teach/user = scelta esplicita;
        reflection = sintesi consolidata (Loop 2a).
        Cache 5 min — usato dal Filtro del Risveglio in search_insights.
        """
        import config
        OPERATIONAL = config.INSIGHT_ACTIVE_SOURCES
        CACHE_TTL = 300  # 5 minuti
        now_ts = time.time()
        if self._active_domains_cache and (now_ts - self._active_domains_cache[1]) < CACHE_TTL:
            return self._active_domains_cache[0]

        cutoff = now_ts - days * 86400
        domains: set[str] = set()
        for key in self.r.scan_iter("euri:memory:*"):
            try:
                data = self.r.json().get(key, "$")
                if not data:
                    continue
                doc = data[0]
                if doc.get("source") not in OPERATIONAL:
                    continue
                ts = doc.get("created_at", 0)
                if not ts or ts < cutoff:
                    continue
                if doc.get("superseded_by"):
                    continue
                dom = doc.get("domain")
                if dom:
                    domains.add(dom)
            except Exception:
                continue

        self._active_domains_cache = (domains, now_ts)
        return domains

    def search_insights(self, query: str, limit: int = 2) -> list[dict]:
        """
        KNN search su idx:insights filtrato per status=promoted, con Filtro del Risveglio.
        Gli insight i cui due domini non sono apparsi nelle memorie operative degli
        ultimi INSIGHT_ACTIVE_DAYS giorni ricevono una penalty moltiplicativa sulla
        cosine distance — non vengono soppressi, solo deprioritizzati nel ranking.
        Il sogno (Loop 2b) resta libero: il filtro opera solo qui, al recupero.
        Recalled_count viene incrementato solo per gli insight effettivamente restituiti.
        """
        if not self._embedder or not self._embedder.available:
            return []
        vec = self._embedder.encode(query, mode="query")
        if vec is None:
            return []
        try:
            import config
            from redis.commands.search.query import Query as RQuery
            import redis as _redis
            raw_r = _redis.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                db=config.REDIS_DB,
                decode_responses=False,
            )
            # Oversample: chiedi più candidati per avere margine al re-rank
            oversample = max(limit * config.INSIGHT_OVERSAMPLE_FACTOR, 6)
            q = (RQuery("(@status:{promoted})=>[KNN $k @embedding $vec AS vec_score]")
                 .sort_by("vec_score")
                 .paging(0, oversample)
                 .return_fields("vec_score")
                 .dialect(2))
            raw_results = raw_r.ft("idx:insights").search(
                q, query_params={"vec": vec.tobytes(), "k": oversample}
            )

            # Carica doc completi + vec_score; nessun side-effect su recalled_count qui
            candidates: list[tuple[float, dict, str]] = []
            for doc in raw_results.docs:
                doc_id = doc.id.decode() if isinstance(doc.id, bytes) else doc.id
                vs_raw = getattr(doc, "vec_score", b"1.0")
                try:
                    vec_score = float(vs_raw.decode() if isinstance(vs_raw, bytes) else vs_raw)
                except (ValueError, AttributeError):
                    vec_score = 1.0
                data = self.r.json().get(doc_id, "$")
                if not data:
                    continue
                candidates.append((vec_score, data[0], doc_id))

            # Filtro del Risveglio: penalty se entrambi i domini sono "freddi"
            active = self._active_domains(days=config.INSIGHT_ACTIVE_DAYS)
            penalty = float(config.INSIGHT_ARCHIVE_PENALTY)

            def _adjusted(triple):
                vs, item, _ = triple
                dom_a = item.get("domain_a", "")
                dom_b = item.get("domain_b", "")
                archive = (dom_a not in active) and (dom_b not in active)
                return vs * (penalty if archive else 1.0)

            candidates.sort(key=_adjusted)
            survivors = candidates[:limit]

            # Incrementa recalled_count solo sui sopravvissuti
            docs = []
            for _vs, item, doc_id in survivors:
                self.r.json().numincrby(doc_id, "$.recalled_count", 1)
                docs.append(item)
            return docs
        except Exception as e:
            logger.error(f"Errore ricerca insights: {e}")
            return []

    def search_notes(self, query: str, category: str = None, limit: int = 5) -> list[dict]:
        safe = self._sanitize_query(query)
        q_str = safe
        if category:
            q_str = f"@category:{{{category}}} {safe}"
        try:
            q = Query(q_str).paging(0, limit).sort_by("created_at", asc=False)
            results = self.r.ft("idx:notes").search(q)
            docs = []
            for doc in results.docs:
                data = self.r.json().get(doc.id, "$")
                if data:
                    item = data[0]
                    item["_created_at"] = from_timestamp(item.get("created_at"))
                    docs.append(item)
            return docs
        except Exception as e:
            logger.error(f"Errore ricerca notes: {e}")
            return []

    # ──────────────────────────────────────────
    # META / STATO
    # ──────────────────────────────────────────

    def is_silent_mode(self) -> bool:
        val = self.r.get("euri:meta:silent_until")
        if not val:
            return False
        try:
            silent_until = float(val)
            return now().timestamp() < silent_until
        except ValueError:
            return False

    def set_silent_mode(self, until: datetime):
        self.r.set("euri:meta:silent_until", until.timestamp())
        logger.info(f"Modalità silenziosa attiva fino a {format_datetime(until)}")

    def clear_silent_mode(self):
        self.r.delete("euri:meta:silent_until")

    # Cap del ring buffer giornaliero. Storico osservato: media ~90 turni/giorno,
    # massimo ~200 (sessioni vocali dense). 500 dà 2.5× margine sul peggior caso.
    _CONVERSATION_RING_CAP = 500

    def log_conversation(self, role: str, text: str):
        date_key = now().strftime("%Y-%m-%d")
        key = f"euri:conversation:{date_key}"
        entry = f"[{now().strftime('%H:%M:%S')}] {role}: {text}"
        # ARRING (Redis 8.8): ring buffer nativo capped, sostituisce rpush+ltrim.
        # Espulsione FIFO automatica oltre il cap, O(1) per insert.
        self.r.execute_command("ARRING", key, self._CONVERSATION_RING_CAP, entry)
        self.r.expire(key, 60 * 60 * 24 * 30)  # 30 giorni

    def get_today_conversation(self) -> list[str]:
        date_key = now().strftime("%Y-%m-%d")
        key = f"euri:conversation:{date_key}"
        if not self.r.exists(key):
            return []
        # Retrocompatibilità: chiavi pre-V2.16 sono LIST (rpush). Lette finché expire.
        if self.r.type(key) == "list":
            return self.r.lrange(key, 0, -1)
        # ARLASTITEMS restituisce in ordine cronologico (vecchio→nuovo).
        # ARGETRANGE non va usato qui: dopo wraparound mostra il ring fisico, non FIFO.
        return self.r.execute_command("ARLASTITEMS", key, self._CONVERSATION_RING_CAP)

    # ──────────────────────────────────────────
    # AUDIT DI COERENZA — Correction signals
    # ──────────────────────────────────────────

    # Pattern di correzione: solo segnali forti, falsi positivi vengono filtrati
    # dal Loop 2g (classificati come "ambiguous").
    _CORRECTION_PATTERNS = [
        re.compile(p, re.IGNORECASE) for p in [
            r'\bhai\s+(fatto\s+)?confusione\b',
            r'\bstai\s+(miscelando|confondendo|sbagliando)\b',
            r'\bnon\s+(è\s+(corretto|esatto|cos[iì])|hai\s+capito)\b',
            r'\bti\s+sbagli\b',
            r'\bhai\s+sbagliato\b',
            r'\bcorre(g)?gimi\b',
            r'\bno\s*,?\s+(non|sbagli|stai|hai|in\s+realt[aà])\b',
            r'\bnon\s+era\b.*\bma\s+(era|erano)\b',
            # Estensione 17 maggio: correzioni di tipo architetturale/fattuale
            # che la prima formulazione strict aveva mancato (caso reale: "il
            # Context Ingestion Layer non esiste, l'hai inventato").
            r'\bnon\s+esiste\b',
            r'\bhai\s+inventato\b',
            r'\bnon\s+c[’\']\s*[èe]\s+ancora\b',
            # Estensione 18 maggio: correzioni di tipo "assenza di recall"
            # (l'entità c'era ma il retrieval non l'ha pescata). Caso reale:
            # "Lucy Plast è l'azienda dove stai lavorando, ci guardavamo ieri
            # sera" — correzione semantica chiara ma fuori da entrambe le
            # categorie precedenti (attributiva, referenziale). Apre il terzo
            # tipo: correzione di assenza.
            r'\bne\s+abbiamo\s+(parlato|discusso|gi[àa]\s+parlato)\b',
            r'\bci\s+(guardavamo|guardammo|vedevamo|eravamo\s+detti)\b',
            r'\bricordi\s+(che|di|quando)\b',
            r'\bte\s+l[’\']?\s*(avevo|ho)\s+(detto|gi[àa]\s+detto)\b',
            r'\bce\s+l[’\']?\s*avevi\s+(detto|gi[àa])\b',
            # Estensione 18 maggio (sera): pattern di correzione esplicita più
            # diretti, osservati in conversazioni reali con turni tipo "qui ti
            # correggo, perché in realtà il DSC l'abbiamo fatto su 03 PPR 043"
            # (turno 8 sessione 12:15, non catturato dalle 16 regex precedenti).
            r'\bti\s+correggo\b',
            r'\bin\s+realt[àa]\b',
            # Estensione 26 maggio: il sostantivo "correzione/correzioni" come
            # apertura esplicita ("Due correzioni. La prima è che..."). Sessione
            # 15:16 26-mag, doppia correzione (fattuale + comportamentale) non
            # intercettata perché il blocco precedente copriva solo il verbo.
            r'\bcorrezion[ei]\b',
            r'\bmi\s+correggo\b',
        ]
    ]

    # Secondo livello — marcatori "soft": da soli sarebbero deboli (possono essere
    # benigni, "guarda che bello"), ma diventano segnale di correzione QUANDO
    # contraddicono qualcosa che Euri ha appena detto (overlap di token salienti
    # con l'ultimo turno). Catturano le correzioni implicite/maldette ("ma dai,
    # Leonardo è un collega") che le regex forti mancavano. La precisione fine la
    # fa comunque il giudice LLM del Loop 2g (un falso positivo → "ambiguous").
    _SOFT_CORRECTION_MARKERS = [
        re.compile(p, re.IGNORECASE) for p in [
            r'\bguarda\s+che\b', r'\bma\s+no\b', r'\bma\s+dai\b', r'\binvece\b',
            r'\bsemmai\b', r'\bcasomai\b', r'\bveramente\b', r'\bmica\b',
            r'\bpiuttosto\b', r'\bocchio\b', r'\battenz?(ione|to)\b',
            r'\bnon\s+è\b', r'\bnon\s+sono\b', r'\bnon\s+era[no]?\b',
        ]
    ]
    # Marcatori "forti soft": esprimono già di per sé correzione o disappunto,
    # bastano senza overlap (rari in frasi benigne).
    _STRONG_SOFT_MARKERS = [
        re.compile(p, re.IGNORECASE) for p in [
            r'\bmi\s+offendo\b', r'\bnon\s+confondere\b', r'\bnon\s+scambiare\b',
            r'\bti\s+ricordo\s+che\b', r'\bnon\s+mi\s+chiamo\b',
            r'\bnon\s+sono\s+io\b', r'\bguarda\s+che\s+ti\s+sbagli\b',
        ]
    ]
    # Token "salienti" = nomi propri (maiuscoli), codici alfanumerici, numeri:
    # le cose specifiche su cui una correzione fa leva.
    _SALIENT_RE = re.compile(r'\b([A-ZÀ-Ü][\wÀ-ü]{2,}|[A-Z0-9_]{3,}|\d+(?:[.,]\d+)?)\b')
    # Parole comuni maiuscole a inizio frase: non sono "salienti" (evitano falsi overlap).
    _SALIENT_STOP = {
        "non", "però", "quindi", "perché", "perche", "anche", "come", "cosa",
        "questo", "questa", "quello", "quella", "sono", "era", "erano", "sei",
        "hai", "ecco", "allora", "forse", "magari", "euri", "scusa", "senti",
    }

    @classmethod
    def _salient_tokens(cls, text: str) -> set[str]:
        return {
            m.group(1).lower() for m in cls._SALIENT_RE.finditer(text or "")
        } - cls._SALIENT_STOP

    def detect_correction(self, text: str, last_euri_turn: str | None = None) -> bool:
        """True se il prompt utente assomiglia a una correzione di un turno precedente.

        Due livelli: (1) pattern forti — bastano da soli; (2) marcatori soft —
        valgono solo se l'utente contraddice qualcosa di specifico che Euri ha
        appena detto (overlap di token salienti con l'ultimo turno di Euri).
        """
        if not text:
            return False
        # Livello 1 — segnali forti espliciti
        if any(p.search(text) for p in self._CORRECTION_PATTERNS):
            return True
        # Livello 2a — soft "forti": correzione/disappunto già nel marcatore
        if any(p.search(text) for p in self._STRONG_SOFT_MARKERS):
            return True
        # Livello 2b — soft + contraddice qualcosa appena detto da Euri
        if any(p.search(text) for p in self._SOFT_CORRECTION_MARKERS):
            if last_euri_turn is None:
                try:
                    last_euri_turn = self.get_last_euri_turn()
                except Exception:
                    last_euri_turn = ""
            if last_euri_turn and (self._salient_tokens(text) & self._salient_tokens(last_euri_turn)):
                return True
        return False

    def set_last_rag_ctx(self, ids: list[str]) -> None:
        """
        Memorizza gli ID delle memorie iniettate nel turno corrente.
        Usato dal Loop 2g per ricostruire il contesto del turno che ha generato l'errore.
        TTL 1h: oltre quel limite, la correzione non è più associabile in modo affidabile.
        """
        key = "euri:last_rag_ctx"
        if not ids:
            self.r.delete(key)
            return
        # SET atomico con TTL nello stesso comando: elimina la finestra delete→rpush in
        # cui un lettore vedeva la lista vuota, e la chiave orfana senza TTL se il processo
        # moriva tra rpush ed expire. Lista serializzata in JSON (gli id non hanno virgole).
        self.r.set(key, json.dumps(ids), ex=3600)

    def get_last_rag_ctx(self) -> list[str]:
        """Recupera gli ID del RAG context del turno precedente (può essere vuoto)."""
        try:
            raw = self.r.get("euri:last_rag_ctx")
        except Exception:
            # Chiave residua nel vecchio formato lista → WRONGTYPE su GET: il prossimo
            # set_last_rag_ctx la sovrascrive col nuovo formato. Best-effort: [].
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []

    def get_last_euri_turn(self) -> str:
        """Ultimo turno di Euri nella conversazione di oggi (stringa vuota se assente)."""
        convs = self.get_today_conversation()
        for entry in reversed(convs):
            if "] Euri: " in entry:
                return entry.split("] Euri: ", 1)[1]
        return ""

    def save_correction_signal(
        self,
        prompt_originale: str,
        risposta_euri: str,
        correzione_user: str,
        rag_ctx_ids: list[str],
    ) -> str:
        """
        Salva un correction signal per analisi notturna del Loop 2g.
        TTL 30gg: oltre quel limite il signal non analizzato evapora.
        """
        sid = str(uuid.uuid4())
        key = f"euri:correction:{sid}"
        doc = {
            "id": sid,
            "prompt_original": prompt_originale,
            "risposta_euri": risposta_euri,
            "correzione_user": correzione_user,
            "rag_ctx_ids": rag_ctx_ids or [],
            "status": "pending",
            "verdict": None,
            "created_at": to_timestamp(now()),
            "analyzed_at": None,
        }
        self.r.json().set(key, "$", doc)
        self.r.expire(key, 30 * 86400)
        logger.info(f"Correction signal salvato: {sid[:8]} — '{correzione_user[:60]}'")
        return sid
