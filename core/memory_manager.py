"""
CRUD Redis per memories, todos, notes.
Tutte le operazioni usano Redis JSON + RediSearch.
"""
import re
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
}
# Memorie user/teach/obsidian_vault non hanno TTL automatico — non compaiono qui.


class MemoryManager:
    def __init__(self, r: redis_lib.Redis, embedder=None):
        self.r = r
        self._embedder = embedder  # core.embedder.Embedder — può essere None (fallback keyword)

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

    def search_memories(self, query: str, limit: int = 5, source_filter: list[str] | None = None) -> list[dict]:
        """
        Ricerca Domain-Gated: semantica (KNN filtrato per dominio) + fallback al DB intero.
        Se l'embedder non è disponibile, fallback a keyword-only.
        """
        if self._embedder and self._embedder.available and query != "*":
            # Passa a domain_aware_search (che esegue il two-pass KNN)
            results = domain_aware_search(query, self._embedder, self.r, limit)
            
            # Applica il source_filter (es. in DEMO_MODE tiene solo 'campus')
            if source_filter is not None:
                results = [r for r in results if r.get("source") in source_filter]
                
            return results
            
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
                        # Zona grigia: prova LLM
                        if llm_probe_fn is not None:
                            question = f"Queste due frasi dicono la stessa cosa?\nA: {content}\nB: {cand['content']}"
                            answer = llm_probe_fn(question)
                            same = answer.strip().upper().startswith(("SÌ", "SI", "YES"))
                        else:
                            same = self._llm_is_same_content(content, cand["content"])
                        if same:
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
                    question = f"Queste due frasi dicono la stessa cosa?\nA: {content}\nB: {r['content']}"
                    answer = llm_probe_fn(question)
                    same = answer.strip().upper().startswith(("SÌ", "SI", "YES"))
                else:
                    same = self._llm_is_same_content(content, r["content"])
                if same:
                    logger.info(f"Duplicato memory (Jaccard+LLM): '{content[:50]}' ~ '{r['content'][:50]}'")
                    return True
        return False

    @staticmethod
    def _llm_is_same_content(a: str, b: str) -> bool:
        """Verifica semanticamente se due frasi trasmettono la stessa informazione."""
        try:
            import ollama
            import config
            prompt = (
                f"Queste due frasi trasmettono la stessa informazione?\n"
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
            return result.startswith("SÌ") or result.startswith("SI") or result.startswith("YES")
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

    def search_insights(self, query: str, limit: int = 2) -> list[dict]:
        """KNN search su idx:insights filtrato per status=promoted. Aggiorna recalled_count."""
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
            q = (RQuery("(@status:{promoted})=>[KNN $k @embedding $vec AS vec_score]")
                 .sort_by("vec_score")
                 .paging(0, limit)
                 .return_fields("vec_score")
                 .dialect(2))
            raw_results = raw_r.ft("idx:insights").search(
                q, query_params={"vec": vec.tobytes(), "k": limit * 2}
            )
            docs = []
            for doc in raw_results.docs:
                doc_id = doc.id.decode() if isinstance(doc.id, bytes) else doc.id
                data = self.r.json().get(doc_id, "$")
                if data:
                    item = data[0]
                    docs.append(item)
                    self.r.json().numincrby(doc_id, "$.recalled_count", 1)
            return docs[:limit]
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
