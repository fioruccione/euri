"""
CRUD Redis per memories, todos, notes.
Tutte le operazioni usano Redis JSON + RediSearch.
"""
import re
import time
import uuid
from datetime import datetime
from loguru import logger

import redis as redis_lib
from redis.commands.search.query import Query

from utils.date_utils import now, to_timestamp, from_timestamp, format_datetime
from core.domain_gater import assign_domain, domain_aware_search
from utils.obsidian_sync import write_memory


_TTL_BY_SOURCE: dict[str, int] = {
    "passive":      90,
    "reflection":   90,
    "conversation": 90,
    "episode":       7,
    "web":          60,   # ricerche web — info può invecchiare
}
# Memorie user/teach/obsidian_vault non hanno TTL automatico — non compaiono qui.


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

    def save_memory(self, content: str, category: str = "personale", tags: list[str] = None, source: str = "user", expires_at: datetime | None = None) -> str:
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

        # Domain assignment auto-scoperto via LLM
        domain_label = assign_domain(content)

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
        }
        self.r.json().set(key, "$", doc)
        if expires_at:
            self.r.expireat(key, expires_at)
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

    def search_memories(self, query: str, limit: int = 5, source_filter: list[str] | None = None) -> list[dict]:
        """
        Ricerca a tre livelli:
        1. Identifier-first: acronimi, codici, numeri decimali → keyword search diretta, risultati in cima
        2. Domain-gated KNN: ricerca semantica nel dominio assegnato alla query
        3. Hybrid fill: _search_hybrid riempie eventuali slot rimasti
        Garantisce che fatti specifici (MFI lotto, concentrazioni, codici progetto) non vengano
        sepolti da memorie semanticamente centrali già consolidate nello stesso dominio.
        """
        if self._embedder and self._embedder.available and query != "*":
            merged: list[dict] = []
            seen_uuids: set[str] = set()

            # Livello 1 — identifier-first keyword search
            identifiers = self._IDENTIFIER_RE.findall(query)
            if identifiers:
                id_query = " | ".join(identifiers)
                id_results = self._search_keyword(id_query, limit, source_filter=source_filter)
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
                hybrid = self._search_hybrid(query, limit, source_filter=source_filter)
                for r in hybrid:
                    uid = r.get("id", "")
                    if uid not in seen_uuids and len(merged) < limit:
                        merged.append(r)
                        seen_uuids.add(uid)

            logger.debug(
                f"Search 3-livelli: {len(merged)} risultati "
                f"(id:{len(identifiers)} token, semantic:{len(semantic)}, fill)"
            )
            return merged[:limit]

        return self._search_keyword(query, limit, source_filter=source_filter)

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

    def _search_keyword(self, query: str, limit: int, source_filter: list[str] | None = None) -> list[dict]:
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
            return self._hydrate(results.docs)
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

    def _search_hybrid(self, query: str, limit: int, source_filter: list[str] | None = None) -> list[dict]:
        """
        Merge ricerca semantica + keyword.
        Priorità: doc trovati da entrambi > solo semantici > solo keyword.
        """
        kw_list = self._safe_keywords(query)
        kw_query = " | ".join(kw_list[:6]) if kw_list else None

        sem_docs = self._search_semantic(query, limit, source_filter=source_filter)
        kw_docs = self._search_keyword(kw_query, limit, source_filter=source_filter) if kw_query else []

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

        # Aggiorna contatori richiami e resetta finestra scorrevole TTL
        for item in merged[:limit]:
            key = f"euri:memory:{item['id']}"
            self.r.json().numincrby(key, "$.recalled_count", 1)
            ts_now = to_timestamp(now())
            self.r.json().set(key, "$.last_recalled_at", ts_now)
            ttl_days = _TTL_BY_SOURCE.get(item.get("source", ""))
            if ttl_days:
                from datetime import timedelta
                new_exp_dt = now() + timedelta(days=ttl_days)
                self.r.json().set(key, "$.expires_at", to_timestamp(new_exp_dt))
                self.r.expireat(key, new_exp_dt)

        return merged[:limit]

    def _hydrate(self, raw_docs) -> list[dict]:
        """Carica i documenti completi da Redis JSON e aggiorna i contatori."""
        docs = []
        for doc in raw_docs:
            data = self.r.json().get(doc.id, "$")
            if data:
                item = data[0]
                if item.get("superseded_by"):  # soft-deleted da Loop 2f — escludi dalla ricerca
                    continue
                item["_created_at"] = from_timestamp(item.get("created_at"))
                docs.append(item)
                self.r.json().numincrby(doc.id, "$.recalled_count", 1)
                ts_now = to_timestamp(now())
                self.r.json().set(doc.id, "$.last_recalled_at", ts_now)
                ttl_days = _TTL_BY_SOURCE.get(item.get("source", ""))
                if ttl_days:
                    from datetime import timedelta
                    new_exp_dt = now() + timedelta(days=ttl_days)
                    self.r.json().set(doc.id, "$.expires_at", to_timestamp(new_exp_dt))
                    self.r.expireat(doc.id, new_exp_dt)
        return docs

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

    def get_recent_memories(self, limit: int = 10, source_filter: list[str] | None = None) -> list[dict]:
        return self._search_keyword("*", limit=limit, source_filter=source_filter)

    def search_memories_by_timerange(self, ts_start: float, ts_end: float, limit: int = 5) -> list[dict]:
        """Recupera memorie in un range temporale tramite filtro numerico su created_at."""
        try:
            q = (Query(f"@created_at:[{ts_start} {ts_end}]")
                 .sort_by("created_at", asc=False)
                 .paging(0, limit))
            results = self.r.ft("idx:memories").search(q)
            return self._hydrate(results.docs)
        except Exception as e:
            logger.error(f"Errore ricerca temporale: {e}")
            return []

    def get_recent_reflections(self, limit: int = 2) -> list[dict]:
        """Restituisce le reflection più recenti generate da Loop 2a."""
        return self._search_keyword("*", limit=limit, source_filter=["reflection"])

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
        results = self._search_keyword(keyword_query, limit=3)
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

    @staticmethod
    def _llm_is_same_content(a: str, b: str) -> bool:
        """True se A non aggiunge informazioni concrete rispetto a B (= è un duplicato)."""
        try:
            import ollama
            import config
            prompt = (
                f"La frase A contiene informazioni concrete non già presenti in B? "
                f"(numeri diversi, componenti diversi, processi diversi, date, misure specifiche)\n"
                f"A: {a}\n"
                f"B: {b}\n"
                f"Rispondi solo SÌ o NO."
            )
            response = ollama.chat(
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
        all_memories = self.get_recent_memories(limit=500)
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

    def log_conversation(self, role: str, text: str):
        date_key = now().strftime("%Y-%m-%d")
        key = f"euri:conversation:{date_key}"
        entry = f"[{now().strftime('%H:%M:%S')}] {role}: {text}"
        self.r.rpush(key, entry)
        self.r.expire(key, 60 * 60 * 24 * 30)  # 30 giorni

    def get_today_conversation(self) -> list[str]:
        date_key = now().strftime("%Y-%m-%d")
        return self.r.lrange(f"euri:conversation:{date_key}", 0, -1)

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
        ]
    ]

    @classmethod
    def detect_correction(cls, text: str) -> bool:
        """True se il prompt utente assomiglia a una correzione di un turno precedente."""
        if not text:
            return False
        return any(p.search(text) for p in cls._CORRECTION_PATTERNS)

    def set_last_rag_ctx(self, ids: list[str]) -> None:
        """
        Memorizza gli ID delle memorie iniettate nel turno corrente.
        Usato dal Loop 2g per ricostruire il contesto del turno che ha generato l'errore.
        TTL 1h: oltre quel limite, la correzione non è più associabile in modo affidabile.
        """
        key = "euri:last_rag_ctx"
        self.r.delete(key)
        if ids:
            self.r.rpush(key, *ids)
            self.r.expire(key, 3600)

    def get_last_rag_ctx(self) -> list[str]:
        """Recupera gli ID del RAG context del turno precedente (può essere vuoto)."""
        return self.r.lrange("euri:last_rag_ctx", 0, -1) or []

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
