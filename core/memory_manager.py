"""
CRUD Redis per memories, todos, notes.
Tutte le operazioni usano Redis JSON + RediSearch.
"""
import re
import json
import time
import uuid
import hashlib
from datetime import datetime
from loguru import logger

import redis as redis_lib
from redis.commands.search.query import Query

import config
from utils.date_utils import now, to_timestamp, from_timestamp, format_datetime
from core.domain_gater import assign_domain, domain_aware_search, neighbor_domains
from core.memory_attention import remove_loop2e_candidate, update_loop2e_candidate_index
from core.memory_axes import analyze_memory_axes
from core.memory_risk import rank_memories_epistemically
from core.memory_outbox import (
    MEMORY_OUTBOX_PENDING,
    memory_outbox_key,
    process_memory_outbox_event,
)
from core.pulse import pulse_emit
from core.memory_scope import (
    PERSONAL_SCOPE,
    current_scope,
    normalize_scope,
    scope_clause,
)

# Sorgenti di memoria che nascono da un'INTERAZIONE col mondo (→ polo extero del Pulse);
# le altre (passive/loop2e/reflection/reaction…) sono elaborazione interna di Euri (→ intero).
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
# Il passive learner può tollerare un doppione, non la perdita di un fatto.
# Prima del giudice LLM, tutti i marker informativi della nuova frase
# devono essere già presenti nel candidato.
PASSIVE_DEDUP_MIN_CLAIM_COVERAGE = 1.0


# Mapping idempotente e documento diventano visibili nella stessa operazione.
# JSON.SET precede SET: se la scrittura canonica fallisce, non resta un winner
# fantasma. Gli effetti derivati (TTL, ZSET, Pulse, Obsidian) restano replayabili.
_IDEMPOTENT_MEMORY_COMMIT_LUA = """
local existing = redis.call('GET', KEYS[1])
if existing then
    local existing_key = ARGV[3] .. existing
    if redis.call('EXISTS', existing_key) == 1 then
        return {existing, '0'}
    end
end
redis.call('JSON.SET', KEYS[2], '$', ARGV[2])
redis.call('SET', KEYS[1], ARGV[1], 'EX', 120)
redis.call(
    'HSET', KEYS[3],
    'memory_key', KEYS[2], 'memory_id', ARGV[1],
    'enqueued_at', ARGV[4], 'attempts', '0'
)
redis.call('ZADD', KEYS[4], ARGV[4], KEYS[3])
return {ARGV[1], '1'}
"""

_MEMORY_COMMIT_LUA = """
redis.call('JSON.SET', KEYS[1], '$', ARGV[2])
redis.call(
    'HSET', KEYS[2],
    'memory_key', KEYS[1], 'memory_id', ARGV[1],
    'enqueued_at', ARGV[3], 'attempts', '0'
)
redis.call('ZADD', KEYS[3], ARGV[3], KEYS[2])
return ARGV[1]
"""


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

    @staticmethod
    def _idempotency_key(
        content: str,
        source: str,
        memory_scope: str = PERSONAL_SCOPE,
    ) -> str | None:
        normalized = " ".join((content or "").lower().split())
        if not normalized:
            return None
        digest = hashlib.sha1(normalized.encode()).hexdigest()
        return f"euri:idem:save:{normalize_scope(memory_scope)}:{source}:{digest}"

    def _commit_idempotent_memory(
        self,
        idem_key: str,
        memory_key: str,
        memory_id: str,
        doc: dict,
    ) -> tuple[str, bool]:
        """Commit atomico RedisJSON + mapping; ritorna (winner_id, created)."""
        result = self.r.eval(
            _IDEMPOTENT_MEMORY_COMMIT_LUA,
            4,
            idem_key,
            memory_key,
            memory_outbox_key(memory_id),
            MEMORY_OUTBOX_PENDING,
            memory_id,
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
            "euri:memory:",
            f"{time.time():.6f}",
        )
        winner, created = result
        if isinstance(winner, bytes):
            winner = winner.decode("utf-8", errors="replace")
        if isinstance(created, bytes):
            created = created.decode("utf-8", errors="replace")
        return str(winner), str(created) == "1"

    def _commit_memory(self, memory_key: str, memory_id: str, doc: dict) -> None:
        """Crea memoria canonica e record outbox nella stessa operazione Redis."""
        self.r.eval(
            _MEMORY_COMMIT_LUA,
            3,
            memory_key,
            memory_outbox_key(memory_id),
            MEMORY_OUTBOX_PENDING,
            memory_id,
            json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
            f"{time.time():.6f}",
        )

    def save_memory(
        self,
        content: str,
        category: str = "personale",
        tags: list[str] = None,
        source: str = "user",
        expires_at: datetime | None = None,
        idempotent: bool = False,
        due_at: datetime | None = None,
        status: str | None = None,
        memory_kind: str | None = None,
        temporal_context: dict | None = None,
        final_fields: dict | None = None,
        precommit_guard=None,
        memory_scope: str | None = None,
    ) -> str | None:
        """Costruisce e pubblica una memoria canonica già completa.

        ``final_fields`` contiene metadati conosciuti dal chiamante (provenienza,
        dominio forzato, fragilità epistemica, ecc.) che devono essere visibili
        nella STESSA versione letta da outbox, Pulse, Obsidian e indici derivati.
        Non va usato per riscrivere identità, contenuto, fonte o embedding canonici.
        ``precommit_guard`` consente ai loop lunghi di annullare la pubblicazione
        se il mondo è cambiato durante embedding/classificazione.
        """
        # Memory Guard: scansione anti-poisoning sull'ingest. Da fonte non fidata
        # (web/mobile_in) un contenuto con injection/esfiltrazione viene rifiutato
        # (ritorna None); da fonte fidata si salva ma marcato in safety_flag.
        from core.memory_guard import evaluate
        guard = evaluate(content, source)
        if guard["reject"]:
            return None

        mid = str(uuid.uuid4())
        key = f"euri:memory:{mid}"

        # Idempotency cross-processo — OPT-IN (idempotent=True).
        # Daemon vocale, UI e passive-inline possono salvare lo STESSO contenuto concorrentemente
        # (check-then-save TOCTOU). Il mapping su (source, contenuto normalizzato) viene
        # committato atomicamente col RedisJSON solo DOPO aver costruito il documento.
        # Finestra breve = copre la race, non la storia.
        # ⚠️ OPT-IN di proposito (Codex round 3 bis): ritornare un id ESISTENTE rompe i chiamanti
        # che post-mutano il nuovo id (reaction scrive reacted_to/tags, loop2e consolidated_*,
        # obsidian forza il domain) → gli scriverebbero metadati di una memoria nuova sul nodo del
        # vincitore. Abilitato SOLO dove la race è reale e le eventuali post-mutazioni sono
        # conservative anche su un nodo identico esistente: passive learner e save esplicito.
        # Fail-mode sicuro: spento = resta il dedup best-effort.
        ts = now()
        memory_scope = normalize_scope(memory_scope or current_scope())
        idem_key = (
            self._idempotency_key(content, source, memory_scope)
            if idempotent
            else None
        )
        temporal_context = dict(temporal_context or {})

        def _float_or_none(value):
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        asserted_at = _float_or_none(temporal_context.get("asserted_at")) or to_timestamp(ts)
        event_start = _float_or_none(temporal_context.get("event_start"))
        event_end = _float_or_none(temporal_context.get("event_end"))
        if event_start is None and event_end is None:
            from core.temporal_context import resolve_text_event_time
            inferred_time = resolve_text_event_time(content, asserted_at=asserted_at)
            for field, value in inferred_time.items():
                temporal_context.setdefault(field, value)
            event_start = _float_or_none(temporal_context.get("event_start"))
            event_end = _float_or_none(temporal_context.get("event_end"))
        temporal_context["schema_version"] = int(temporal_context.get("schema_version") or 1)
        temporal_context["asserted_at"] = asserted_at
        temporal_context["event_start"] = event_start
        temporal_context["event_end"] = event_end
        kind_by_source = {
            "episode": "conversation_episode",
            "reflection": "reflection",
            "loop2e": "derived_consolidation",
            "reaction": "reaction_lesson",
        }
        memory_kind = memory_kind or kind_by_source.get(source, "semantic_fact")

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
        asserted_dt = from_timestamp(asserted_at) or ts
        hour = asserted_dt.hour
        if hour < 12:
            time_of_day = "mattina"
        elif hour < 18:
            time_of_day = "pomeriggio"
        else:
            time_of_day = "sera"
        _DAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
        context_meta = {
            "day_of_week": _DAYS_IT[asserted_dt.weekday()],
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
                hint_domains = neighbor_domains(
                    vec_bytes, self.r, k=8, memory_scope=memory_scope
                )
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
        memory_axes = analyze_memory_axes(content, source=source, created_at=asserted_at)
        if "acephalous_subject" in memory_axes.get("audit_reasons", []):
            requires_verification = True

        doc = {
            "id": mid,
            "content": content,
            "category": category,
            "source": source,
            "memory_kind": memory_kind,
            "memory_scope": memory_scope,
            "domain": domain_label,
            "requires_verification": requires_verification,
            "created_at": to_timestamp(ts),
            "asserted_at": asserted_at,
            "event_start": event_start,
            "event_end": event_end,
            "due_at": to_timestamp(due_at),
            "expires_at": to_timestamp(expires_at) if expires_at else None,
            "recalled_count": 0,
            "last_recalled_at": None,
            "tags": tags or [],
            "embedding": embedding,
            "context_meta": context_meta,
            "temporal_context": temporal_context,
            "memory_axes": memory_axes,
            "safety_flag": guard["safety_flag"],  # [] se pulito; categorie se contenuto sospetto da fonte fidata
        }
        # Impegno (todo assorbito): una memoria con scadenza e stato pending/done.
        # Le memorie normali restano senza status → invisibili a @status:{pending}.
        if status:
            doc["status"] = status
            doc["reminded_count"] = 0
            doc["last_reminded_at"] = None
            doc["completed_at"] = None
        if final_fields:
            immutable = {
                "id", "content", "source", "embedding", "memory_scope",
            }
            forbidden = immutable.intersection(final_fields)
            if forbidden:
                raise ValueError(
                    "save_memory final_fields non può sovrascrivere campi canonici: "
                    + ", ".join(sorted(forbidden))
                )
            doc.update(dict(final_fields))
        if precommit_guard is not None:
            try:
                if not bool(precommit_guard()):
                    logger.info(
                        f"Memory publication annullata dal precommit guard: {mid}"
                    )
                    return None
            except Exception as exc:
                logger.warning(f"Memory precommit guard fallito: {exc}")
                return None
        if idem_key:
            winner_id, created = self._commit_idempotent_memory(idem_key, key, mid, doc)
            if not created:
                logger.debug(
                    f"save_memory: idempotency skip — documento già committato (winner={winner_id})"
                )
                process_memory_outbox_event(self.r, memory_outbox_key(winner_id))
                return winner_id
        else:
            self._commit_memory(key, mid, doc)
        logger.info(f"Memory salvata: {mid}")

        # Fast path: conserva la latenza attuale. Se un effetto fallisce, il record
        # resta nell'outbox e verra' ripreso dal worker invece di essere perso.
        process_memory_outbox_event(self.r, memory_outbox_key(mid))

        return mid

    # Riconoscimento DOMAIN-AGNOSTIC di identificatori per la keyword-search (nessuna lista di
    # materiali/codici cablata, nessun boost di dominio: conta la FORMA, non il contenuto):
    #  - compositi multi-token con almeno una cifra: "03 PPR 738P", "T REX 001", "ROSA DAM 12"
    #  - token singolo che mischia lettere E cifre: "03PPR738P", "738P", "043T"
    #  - acronimi (MFI, DCP), codici con trattino (PPR-738P), decimali (4.7)
    # I nomi propri in maiuscolo SENZA cifra NON sono codici ("Mario Rossi" → niente).
    # Bug 20/06: la vecchia regex da "03 PPR 738P" estraeva solo "PPR" → l'identifier-first
    # cercava TUTTI i prodotti "PPR" e soffocava la semantica buona.
    _COMPOSITE_ID_RE = re.compile(r'\b[A-Z0-9]+(?:\s+[A-Z0-9]+){1,3}\b')
    _SINGLE_ALNUM_RE = re.compile(r'\b(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,}\b')
    _ACR_DEC_RE = re.compile(r'\b[A-Z]{2,}(?:-[A-Z0-9]+)*\b|\b\d+[.,]\d+\b')

    def _extract_identifiers(self, query: str) -> list[str]:
        """Identificatori per la keyword-search, dal più specifico. Vedi note su _COMPOSITE_ID_RE."""
        ids: list[str] = []
        for m in self._COMPOSITE_ID_RE.findall(query):          # compositi multi-token: cifra E lettera
            # ≥2 token, almeno una cifra E almeno una lettera: i codici hanno lettere
            # (PPR/REX/DAM), le date pure-numeriche no ("20 06 2026" → al path temporale, non qui).
            if len(m.split()) >= 2 and any(c.isdigit() for c in m) and any(c.isalpha() for c in m):
                ids.append(m)
        for m in self._ACR_DEC_RE.findall(query):               # acronimi / trattino / decimali
            if not any(m in c for c in ids):
                ids.append(m)
        for m in self._SINGLE_ALNUM_RE.findall(query):          # token singolo lettere+cifre
            if not any(m in c for c in ids):
                ids.append(m)
        return ids

    def search_memories(
        self,
        query: str,
        limit: int = 5,
        source_filter: list[str] | None = None,
        touch: bool = True,
        *,
        source_exclude: list[str] | None = None,
        memory_scope: str | None = None,
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
        memory_scope = normalize_scope(memory_scope or current_scope())
        if self._embedder and self._embedder.available and query != "*":
            merged: list[dict] = []
            seen_uuids: set[str] = set()

            # Livello 1 — identifier-first keyword search, CAPPATA.
            # Un identificatore generico/ambiguo può matchare molti nodi: senza cap
            # monopolizzerebbe gli slot e soffocherebbe la semantica (bug 20/06). Limitiamo
            # il contributo identifier-first a ~1/3 degli slot, lasciando la maggioranza al
            # livello semantico. I codici specifici restano comunque IN CIMA (precedenza),
            # solo senza occupare tutto.
            identifiers = self._extract_identifiers(query)
            if identifiers:
                id_cap = max(1, limit // 3)
                id_query = " | ".join(identifiers)
                id_results = self._rank_epistemically(
                    self._search_keyword(
                        id_query,
                        max(id_cap * 4, 8),
                        source_filter=source_filter,
                        source_exclude=source_exclude,
                        memory_scope=memory_scope,
                        touch=False,
                    ),
                    limit=id_cap,
                )
                for r in id_results[:id_cap]:
                    uid = r.get("id", "")
                    if uid not in seen_uuids:
                        merged.append(r)
                        seen_uuids.add(uid)

            # Livello 2 — domain-gated semantic
            semantic = domain_aware_search(
                query,
                self._embedder,
                self.r,
                limit,
                source_filter=source_filter,
                source_exclude=source_exclude,
                memory_scope=memory_scope,
            )
            semantic = [r for r in semantic if not r.get("superseded_by")]
            if source_filter is not None:
                semantic = [r for r in semantic if r.get("source") in source_filter]
            if source_exclude is not None:
                semantic = [r for r in semantic if r.get("source") not in source_exclude]
            for r in semantic:
                uid = r["id"].replace("euri:memory:", "")
                if uid not in seen_uuids:
                    merged.append(r)
                    seen_uuids.add(uid)

            # Livello 3 — hybrid fill se ancora sotto il limite
            if len(merged) < limit:
                hybrid = self._search_hybrid(
                    query,
                    limit,
                    source_filter=source_filter,
                    source_exclude=source_exclude,
                    memory_scope=memory_scope,
                    touch=False,
                )
                for r in hybrid:
                    uid = r.get("id", "")
                    if uid not in seen_uuids and len(merged) < limit:
                        merged.append(r)
                        seen_uuids.add(uid)

            logger.debug(
                f"Search 3-livelli: {len(merged)} risultati "
                f"(id:{len(identifiers)} token, semantic:{len(semantic)}, fill)"
            )

            # Ranking prudente centralizzato: pertinenza, affidabilita' della fonte
            # e flag epistemici concorrono prima del taglio finale.
            results = self._rank_epistemically(merged, limit=limit)
            if touch:
                self._touch_memories(results)
            return results

        if query == "*":
            candidates = self._search_keyword(
                query,
                max(limit * 4, limit),
                source_filter=source_filter,
                source_exclude=source_exclude,
                memory_scope=memory_scope,
                touch=False,
            )
            results = self._rank_epistemically(candidates, limit=limit)
            if touch:
                self._touch_memories(results)
            return results
        kw_list = self._safe_keywords(query)
        kw_query = " | ".join(kw_list[:6]) if kw_list else query
        candidates = self._search_keyword(
            kw_query,
            max(limit * 4, limit),
            source_filter=source_filter,
            source_exclude=source_exclude,
            memory_scope=memory_scope,
            touch=False,
        )
        results = self._rank_epistemically(candidates, limit=limit)
        if touch:
            self._touch_memories(results)
        return results

    @staticmethod
    def _sanitize_query(text: str) -> str:
        """Rimuove caratteri speciali RediSearch da input utente grezzo."""
        clean = re.sub(r'[^\w\sàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]', ' ', text)
        return ' '.join(clean.split()) or "*"

    @classmethod
    def _sanitize_query_or(cls, text: str) -> str:
        """Sanitizza una query preservando OR espliciti costruiti dal codice (`a | b`)."""
        if "|" not in (text or ""):
            return cls._sanitize_query(text)
        parts = [cls._sanitize_query(p) for p in text.split("|")]
        parts = [p for p in parts if p and p != "*"]
        return " | ".join(parts) if parts else "*"

    @staticmethod
    def _source_prefix(
        source_filter: list[str] | None,
        source_exclude: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> str:
        """Prefiltro RediSearch inclusivo/esclusivo per la source."""
        clauses = [scope_clause(memory_scope)]
        if source_filter:
            clauses.append("@source:{" + "|".join(source_filter) + "}")
        if source_exclude:
            clauses.extend(f"-@source:{{{source}}}" for source in source_exclude)
        return " ".join(clauses)

    def _search_keyword(
        self,
        query: str,
        limit: int,
        source_filter: list[str] | None = None,
        source_exclude: list[str] | None = None,
        touch: bool = True,
        memory_scope: str | None = None,
    ) -> list[dict]:
        try:
            prefix = self._source_prefix(
                source_filter, source_exclude, memory_scope
            ).strip()
            safe_query = query if query == "*" else self._sanitize_query_or(query)

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
            return self._hydrate(
                results.docs, touch=touch, memory_scope=memory_scope
            )
        except Exception as e:
            logger.error(f"Errore ricerca keyword memories: {e}")
            return []
        
    def _search_semantic(
        self,
        query: str,
        limit: int,
        source_filter: list[str] | None = None,
        source_exclude: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> list[dict]:
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
            prefilter = self._source_prefix(
                source_filter, source_exclude, memory_scope
            ).strip() or "*"
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
                    if normalize_scope(item.get("memory_scope")) != normalize_scope(
                        memory_scope or current_scope()
                    ):
                        continue
                    item["_created_at"] = from_timestamp(item.get("created_at"))
                    item["_vec_score"] = score
                    docs.append(item)
            return self._rank_epistemically(docs, limit=limit)
        except Exception as e:
            logger.error(f"Errore ricerca semantica: {e}")
            return []

    def _search_hybrid(
        self,
        query: str,
        limit: int,
        source_filter: list[str] | None = None,
        source_exclude: list[str] | None = None,
        touch: bool = True,
        memory_scope: str | None = None,
    ) -> list[dict]:
        """
        Merge ricerca semantica + keyword.
        Priorità: doc trovati da entrambi > solo semantici > solo keyword.
        """
        kw_list = self._safe_keywords(query)
        kw_query = " | ".join(kw_list[:6]) if kw_list else None

        sem_docs = self._search_semantic(
            query,
            limit,
            source_filter=source_filter,
            source_exclude=source_exclude,
            memory_scope=memory_scope,
        )
        kw_docs = (
            self._search_keyword(
                kw_query,
                limit,
                source_filter=source_filter,
                source_exclude=source_exclude,
                touch=False,
                memory_scope=memory_scope,
            )
            if kw_query
            else []
        )

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

        results = self._rank_epistemically(merged, limit=limit)
        if touch:
            self._touch_memories(results)
        return results

    def _hydrate(
        self,
        raw_docs,
        touch: bool = True,
        memory_scope: str | None = None,
    ) -> list[dict]:
        """Carica i documenti completi da Redis JSON; opzionalmente rinforza i richiami."""
        expected_scope = normalize_scope(memory_scope or current_scope())
        docs = []
        for doc in raw_docs:
            data = self.r.json().get(doc.id, "$")
            if data:
                item = data[0]
                if item.get("superseded_by"):  # soft-deleted da Loop 2f — escludi dalla ricerca
                    continue
                if normalize_scope(item.get("memory_scope")) != expected_scope:
                    continue
                item["_created_at"] = from_timestamp(item.get("created_at"))
                docs.append(item)
        if touch:
            self._touch_memories(docs)
        return docs

    @staticmethod
    def _rank_epistemically(results: list[dict], limit: int | None = None) -> list[dict]:
        """
        Applica lo stesso ordinamento prudente a semantica, keyword e recency.
        Il pool deve essere gia' ordinato per pertinenza: il reranker lo corregge,
        non sostituisce il segnale prodotto dal motore di ricerca.
        """
        return rank_memories_epistemically(results, limit=limit)

    def _demote_provenance_stale(self, results: list[dict]) -> list[dict]:
        """Compatibilita' interna: il vecchio helper ora usa il ranking completo."""
        return self._rank_epistemically(results)

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
                indexed = dict(item)
                indexed["recalled_count"] = int(indexed.get("recalled_count") or 0) + 1
                indexed["last_recalled_at"] = ts_now
                ttl_days = _TTL_BY_SOURCE.get(item.get("source", ""))
                if ttl_days:
                    from datetime import timedelta
                    new_exp_dt = now() + timedelta(days=ttl_days)
                    self.r.json().set(key, "$.expires_at", to_timestamp(new_exp_dt))
                    indexed["expires_at"] = to_timestamp(new_exp_dt)
                    self.r.expireat(key, new_exp_dt)
                update_loop2e_candidate_index(self.r, indexed)
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
        *,
        source_exclude: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> list[dict]:
        memory_scope = normalize_scope(memory_scope or current_scope())
        candidates = self._search_keyword(
            "*",
            limit=max(limit * 4, limit),
            source_filter=source_filter,
            source_exclude=source_exclude,
            touch=False,
            memory_scope=memory_scope,
        )
        results = self._rank_epistemically(candidates, limit=limit)
        if touch:
            self._touch_memories(results)
        return results

    def search_memories_by_timerange(
        self,
        ts_start: float,
        ts_end: float,
        limit: int = 5,
        touch: bool = True,
        *,
        source_exclude: list[str] | None = None,
        memory_scope: str | None = None,
    ) -> list[dict]:
        """Recupera memorie per tempo dell'evento, dell'affermazione o del salvataggio."""
        try:
            temporal_query = (
                f"((@event_start:[-inf {ts_end}] @event_end:[{ts_start} +inf]) | "
                f"@asserted_at:[{ts_start} {ts_end}] | "
                f"@created_at:[{ts_start} {ts_end}])"
            )
            memory_scope = normalize_scope(memory_scope or current_scope())
            source_clause = self._source_prefix(
                None, source_exclude, memory_scope
            )
            if source_clause:
                temporal_query = f"({source_clause}) ({temporal_query})"
            q = (Query(temporal_query)
                 .sort_by("created_at", asc=False)
                 .paging(0, max(limit * 4, limit)))
            results = self.r.ft("idx:memories").search(q)
            memories = self._rank_epistemically(
                self._hydrate(
                    results.docs, touch=False, memory_scope=memory_scope
                ),
                limit=limit,
            )
            if touch:
                self._touch_memories(memories)
            return memories
        except Exception as e:
            logger.error(f"Errore ricerca temporale: {e}")
            return []

    def get_recent_reflections(
        self,
        limit: int = 2,
        touch: bool = True,
        *,
        memory_scope: str | None = None,
    ) -> list[dict]:
        """Restituisce le reflection più recenti generate da Loop 2a."""
        candidates = self._search_keyword(
            "*",
            limit=max(limit * 4, limit),
            source_filter=["reflection"],
            touch=False,
            memory_scope=memory_scope,
        )
        results = self._rank_epistemically(candidates, limit=limit)
        if touch:
            self._touch_memories(results)
        return results

    # ──────────────────────────────────────────
    # IMPEGNI (todo assorbiti nel modello memoria: memorie con due_at + status)
    # ──────────────────────────────────────────

    def save_todo(self, content: str, due_at: datetime = None, tags: list[str] = None) -> str | None:
        """Un impegno è una memoria di prima classe con scadenza e stato pending/done:
        passa dall'hardened path di save_memory (guard, axes, embedding, pulse, vault)
        e il piano conversazionale la vede come qualsiasi altro ricordo."""
        mid = self.save_memory(content, category="impegno", tags=tags, source="user",
                               due_at=due_at, status="pending")
        if mid:
            logger.info(f"Impegno salvato: {mid} — scadenza: {format_datetime(due_at)}")
        return mid

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

    def mark_reminded(self, todo_id: str):
        key = f"euri:memory:{todo_id}"
        self.r.json().numincrby(key, "$.reminded_count", 1)
        self.r.json().set(key, "$.last_reminded_at", to_timestamp(now()))

    def complete_todo(self, todo_id: str) -> bool:
        key = f"euri:memory:{todo_id}"
        if not self.r.exists(key):
            return False
        self.r.json().set(key, "$.status", "done")
        self.r.json().set(key, "$.completed_at", to_timestamp(now()))
        logger.info(f"Impegno completato: {todo_id}")
        return True

    def suspend_todo(self, todo_id: str) -> bool:
        """Mantiene l'impegno aperto ma rimuove la scadenza e i relativi trigger."""
        key = f"euri:memory:{todo_id}"
        if not self.r.exists(key):
            return False
        self.r.json().set(key, "$.status", "pending")
        self.r.json().set(key, "$.due_at", None)
        self.r.json().set(key, "$.suspended_at", to_timestamp(now()))
        self.r.json().set(key, "$.reminded_count", 0)
        self.r.json().set(key, "$.last_reminded_at", None)
        try:
            self.r.srem("euri:pulse:clock_emitted", todo_id)
        except Exception:
            pass
        logger.info(f"Impegno sospeso senza scadenza: {todo_id}")
        return True

    def reschedule_todo(self, todo_id: str, new_due: datetime) -> bool:
        """Sposta la scadenza di un impegno e riarma consegna e clock afferente:
        una scadenza nuova è un evento nuovo — va riannunciata (marcatore
        euri:pulse:clock_emitted rimosso) e riconsegnata (reminded_count azzerato)."""
        key = f"euri:memory:{todo_id}"
        if not self.r.exists(key):
            return False
        self.r.json().set(key, "$.due_at", to_timestamp(new_due))
        self.r.json().set(key, "$.suspended_at", None)
        self.r.json().set(key, "$.reminded_count", 0)
        self.r.json().set(key, "$.last_reminded_at", None)
        try:
            self.r.srem("euri:pulse:clock_emitted", todo_id)
        except Exception:
            pass  # fail-open: al peggio il clock afferente non ri-emette, la consegna va comunque
        logger.info(f"Impegno riprogrammato: {todo_id} → {format_datetime(new_due)}")
        return True

    def find_todo_by_content(self, query: str) -> list[dict]:
        # _sanitize_query_or preserva gli OR espliciti ("a | b") costruiti dai chiamanti:
        # una frase intera sanitizzata diventa AND di tutti i token e non trova mai nulla.
        return self._query_todos(f"({self._sanitize_query_or(query)}) @status:{{pending}}", limit=3)

    @staticmethod
    def _safe_keywords(content: str) -> list[str]:
        """Estrae parole chiave sicure per RediSearch — rimuove stop words e caratteri speciali."""
        _STOP = {"di", "il", "la", "lo", "le", "gli", "un", "una", "uno", "e", "è",
                 "a", "da", "in", "su", "per", "con", "che", "ho", "devo", "fare",
                 "del", "della", "dei", "degli", "al", "alla", "ai", "agli",
                 # parole che collidono con la sintassi RediSearch
                 "todo", "note", "tag", "and", "or", "not",
                 # i nomi del profilo compaiono in quasi ogni memoria e non discriminano
                 }
        _STOP.update(
            token.lower()
            for name in (
                config.OWNER_DISPLAY_NAME,
                config.OWNER_ACTOR_ID,
                config.ASSISTANT_DISPLAY_NAME,
            )
            for token in name.split()
            if token
        )
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

    def is_duplicate_memory(
        self,
        content: str,
        llm_probe_fn=None,
        *,
        memory_scope: str | None = None,
    ) -> bool:
        """
        True se esiste già una memoria semanticamente equivalente.

        La cosine individua soltanto candidati, non decide mai da sola. Una
        memoria viene eliminata soltanto per identità testuale normalizzata,
        oppure quando il candidato copre tutti i marker informativi di A
        e il giudice restituisce esattamente ``DUPLICATO``. Ambiguità ed errori
        conservano A: un doppione è recuperabile, un fatto perso no.
        """
        # ── Fast path: cosine similarity con embedding ──
        if self._embedder and self._embedder.available:
            vec_new = self._embedder.encode(content)
            if vec_new is not None:
                candidates = self._search_semantic(
                    content, limit=3, memory_scope=memory_scope
                )
                for cand in candidates:
                    score = cand.get("_vec_score", 1.0)  # COSINE distance (0=identico, 1=opposto)
                    similarity = 1.0 - score
                    candidate_content = str(cand.get("content") or "")
                    normalized_content = self._normalized_claim_text(content)
                    if normalized_content and normalized_content == (
                        self._normalized_claim_text(candidate_content)
                    ):
                        logger.info(
                            "Duplicato memory (identità testuale): "
                            f"'{content[:50]}' ~ '{candidate_content[:50]}'"
                        )
                        return True
                    if similarity < 0.70:
                        continue
                    if not self._dedup_subjects_compatible(content, candidate_content):
                        logger.debug(
                            "Dedup memory: soggetti espliciti diversi, candidato escluso"
                        )
                        continue
                    coverage = self._dedup_claim_coverage(content, candidate_content)
                    if coverage < PASSIVE_DEDUP_MIN_CLAIM_COVERAGE:
                        logger.debug(
                            "Dedup memory: candidato semanticamente vicino ma "
                            f"incompleto (cosine={similarity:.2f}, coverage={coverage:.2f})"
                        )
                        continue
                    if self._probe_duplicate_verdict(
                        content,
                        candidate_content,
                        llm_probe_fn=llm_probe_fn,
                    ):
                        logger.info(
                            "Duplicato memory "
                            f"(cosine={similarity:.2f}, coverage={coverage:.2f}+LLM): "
                            f"'{content[:50]}' ~ '{candidate_content[:50]}'"
                        )
                        return True
                return False  # nessun duplicato trovato via embedding

        # ── Fallback: Jaccard keyword ──
        words = self._safe_keywords(content)
        if len(words) < 2:
            return False
        keyword_query = " | ".join(words[:5])
        results = self._search_keyword(
            keyword_query,
            limit=3,
            touch=False,
            memory_scope=memory_scope,
        )
        if not results:
            return False
        content_kws = set(words)
        for r in results:
            candidate_content = str(r.get("content") or "")
            normalized_content = self._normalized_claim_text(content)
            if normalized_content and normalized_content == (
                self._normalized_claim_text(candidate_content)
            ):
                logger.info(
                    "Duplicato memory (identità testuale fallback): "
                    f"'{content[:50]}' ~ '{candidate_content[:50]}'"
                )
                return True
            candidate_kws = set(self._safe_keywords(candidate_content))
            if not candidate_kws:
                continue
            overlap = content_kws & candidate_kws
            min_kws = min(len(content_kws), len(candidate_kws))
            if min_kws == 0:
                continue
            ratio = len(overlap) / min_kws
            if ratio < 0.2:
                continue
            if not self._dedup_subjects_compatible(content, candidate_content):
                continue
            coverage = self._dedup_claim_coverage(content, candidate_content)
            if coverage < PASSIVE_DEDUP_MIN_CLAIM_COVERAGE:
                continue
            if self._probe_duplicate_verdict(
                content,
                candidate_content,
                llm_probe_fn=llm_probe_fn,
            ):
                logger.info(
                    "Duplicato memory "
                    f"(Jaccard={ratio:.2f}, coverage={coverage:.2f}+LLM): "
                    f"'{content[:50]}' ~ '{candidate_content[:50]}'"
                )
                return True
        return False

    @staticmethod
    def _normalized_claim_text(content: str) -> str:
        """Identità testuale robusta a maiuscole, punteggiatura e spazi."""
        return " ".join(re.findall(r"\w+", (content or "").casefold(), re.UNICODE))

    @classmethod
    def _dedup_claim_tokens(cls, content: str) -> set[str]:
        """Marker conservativi della proposizione, non una semantica completa."""
        filler = {
            "anche",
            "inoltre",
            "davvero",
            "proprio",
            "sempre",
            "molto",
            "circa",
        }
        tokens = set(cls._safe_keywords(content)) - filler
        tokens.update(
            re.findall(
                r"\b(?:\d{4}-\d{1,2}-\d{1,2}|"
                r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
                r"\d+(?:[.,]\d+)?)\b",
                (content or "").casefold(),
            )
        )
        tokens.update(
            re.findall(
                r"\b(?:non|mai|senza|nessun[oa]?|né|ne)\b",
                (content or "").casefold(),
            )
        )
        return tokens

    @staticmethod
    def _dedup_subjects_compatible(a: str, b: str) -> bool:
        """Soggetti espliciti e disgiunti non possono essere duplicati."""
        axes_a = analyze_memory_axes(a, source="passive")
        axes_b = analyze_memory_axes(b, source="passive")
        entities_a = {
            str(item).casefold() for item in axes_a.get("entity_mentions") or []
        }
        entities_b = {
            str(item).casefold() for item in axes_b.get("entity_mentions") or []
        }
        return not entities_a or not entities_b or bool(entities_a & entities_b)

    @classmethod
    def _dedup_claim_coverage(cls, new_content: str, candidate_content: str) -> float:
        """Quota dei marker di A già presenti in B; A è il fatto da salvare."""
        new_tokens = cls._dedup_claim_tokens(new_content)
        if not new_tokens:
            return 0.0
        candidate_tokens = cls._dedup_claim_tokens(candidate_content)
        return len(new_tokens & candidate_tokens) / len(new_tokens)

    @staticmethod
    def _duplicate_probe_prompt(a: str, b: str) -> str:
        return (
            "Classifica la frase A rispetto alla memoria B.\n"
            "Rispondi DUPLICATO soltanto se B contiene già TUTTE le affermazioni "
            "riutilizzabili presenti in A.\n"
            "Rispondi AGGIUNGE se A introduce anche un solo fatto, preferenza, "
            "relazione, proprietà, elemento di una lista, stato, evento, oggetto, "
            "data, numero o qualificazione non presente in B. Stesso soggetto o "
            "stesso argomento NON significa duplicato. Nel dubbio rispondi AGGIUNGE.\n"
            "Esempio: 'Giulia ama scrivere' e 'Giulia ama scrivere e stare con gli "
            "amici' → AGGIUNGE.\n"
            "Esempio: 'Marco ama i film' e 'Marco preferisce drammi e commedie "
            "romantiche' → AGGIUNGE.\n"
            f"A: {a}\n"
            f"B: {b}\n"
            "Rispondi con una sola parola: DUPLICATO oppure AGGIUNGE."
        )

    @staticmethod
    def _answer_means_duplicate(answer: str) -> bool:
        """Fail-open: soltanto un verdetto esplicito e inequivoco elimina A."""
        normalized = (answer or "").strip().upper().strip(" .,:;!?")
        return normalized == "DUPLICATO"

    @classmethod
    def _probe_duplicate_verdict(
        cls,
        a: str,
        b: str,
        *,
        llm_probe_fn=None,
    ) -> bool:
        if llm_probe_fn is None:
            return cls._llm_is_same_content(a, b)
        try:
            answer = llm_probe_fn(cls._duplicate_probe_prompt(a, b))
        except Exception:
            return False
        return cls._answer_means_duplicate(answer)

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
            "source": cand.get("source"),
            "requires_verification": cand.get("requires_verification"),
            "passive_support": cand.get("passive_support"),
            "memory_axes": cand.get("memory_axes") or {},
            "provenance_stale": cand.get("provenance_stale"),
            "consolidation_risk": cand.get("consolidation_risk") or {},
            "audit_flag": cand.get("audit_flag"),
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
        # Pulse afferente (Fase 1): un fallimento d'integrità è alto-segnale (interno → intero).
        # Lo scorer di tensione lo legge da sense=="integrity". Fail-open, separato dal tracking.
        pulse_emit(self.r, "integrity", "intero", "failure",
                   payload={"kind": kind, "key": str(key), "err": str(err)[:300]},
                   salience=0.9)

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
                remove_loop2e_candidate(self.r, old_id)
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
                    f"({scope_clause()} @domain:{{{safe_domain}}} "
                    f"@source:{{reflection}})"
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

                # Conta solo se il soft-delete è andato davvero (Codex round 3 #4): supersede_memory
                # ora ritorna bool e traccia il fallimento; ignorarlo gonfiava il conteggio (falsa
                # coerenza interna — il chiamante credeva di aver deduplicato).
                if self.supersede_memory(old_id, new_id):
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

    @classmethod
    def _llm_is_same_content(cls, a: str, b: str) -> bool:
        """True soltanto su verdetto DUPLICATO esplicito; errore = conserva A."""
        try:
            from core.ollama_client import chat_client
            import config
            response = chat_client.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": cls._duplicate_probe_prompt(a, b)}],
                options={"temperature": 0, "num_predict": 12},
                think=False,
            )
            return cls._answer_means_duplicate(response.message.content or "")
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

    def _query_todos(
        self,
        query: str,
        limit: int = 20,
        *,
        memory_scope: str | None = None,
    ) -> list[dict]:
        """Query sugli impegni: memorie con status pending/done in idx:memories."""
        try:
            scoped_query = f"({scope_clause(memory_scope)}) ({query})"
            q = Query(scoped_query).paging(0, limit).sort_by("due_at", asc=True)
            results = self.r.ft("idx:memories").search(q)
            docs = []
            for doc in results.docs:
                data = self.r.json().get(doc.id, "$")
                if data:
                    item = data[0]
                    if normalize_scope(item.get("memory_scope")) != normalize_scope(
                        memory_scope or current_scope()
                    ):
                        continue
                    item.pop("embedding", None)  # inutile ai consumatori, pesante nei log
                    item["_due_at"] = from_timestamp(item.get("due_at"))
                    item["_created_at"] = from_timestamp(item.get("created_at"))
                    docs.append(item)
            return docs
        except Exception as e:
            logger.error(f"Errore query impegni: {e}")
            return []

    # ──────────────────────────────────────────
    # NOTES (appunti per categoria)
    # ──────────────────────────────────────────

    def save_note(
        self,
        content: str,
        category: str = "personale",
        tags: list[str] = None,
        source: str = "user",
        *,
        memory_scope: str | None = None,
    ) -> str:
        nid = str(uuid.uuid4())
        key = f"euri:note:{nid}"
        memory_scope = normalize_scope(memory_scope or current_scope())
        doc = {
            "id": nid,
            "content": content,
            "category": category,
            "source": source,
            "created_at": to_timestamp(now()),
            "tags": tags or [],
            "memory_scope": memory_scope,
        }
        self.r.json().set(key, "$", doc)
        logger.info(f"Nota salvata: {nid}")
        return nid

    def _active_domains(self, days: int = 30) -> set[str]:
        """
        Domini con almeno una memoria curata dal proprietario negli ultimi `days` giorni.
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
                if normalize_scope(doc.get("memory_scope")) != PERSONAL_SCOPE:
                    continue
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
        if current_scope() != PERSONAL_SCOPE:
            return []
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

    def search_notes(
        self,
        query: str,
        category: str = None,
        limit: int = 5,
        *,
        memory_scope: str | None = None,
    ) -> list[dict]:
        safe = self._sanitize_query(query)
        expected_scope = normalize_scope(memory_scope or current_scope())
        q_str = f"({scope_clause(expected_scope)}) ({safe})"
        if category:
            q_str = (
                f"({scope_clause(expected_scope)}) "
                f"(@category:{{{category}}}) ({safe})"
            )
        try:
            q = Query(q_str).paging(0, limit).sort_by("created_at", asc=False)
            results = self.r.ft("idx:notes").search(q)
            docs = []
            for doc in results.docs:
                data = self.r.json().get(doc.id, "$")
                if data:
                    item = data[0]
                    if normalize_scope(item.get("memory_scope")) != expected_scope:
                        continue
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
        # Compatibilita' con i chiamanti storici mentre il runtime viene reso
        # portabile: nel dato persistito finiscono i nomi del profilo, non costanti
        # dell'installazione originale.
        role = {
            "Stefano": config.OWNER_DISPLAY_NAME,
            "Euri": config.ASSISTANT_DISPLAY_NAME,
        }.get(role, role)
        date_key = now().strftime("%Y-%m-%d")
        memory_scope = current_scope()
        key = (
            f"euri:conversation:{date_key}"
            if memory_scope == PERSONAL_SCOPE
            else f"euri:conversation:{memory_scope}:{date_key}"
        )
        entry = f"[{now().strftime('%H:%M:%S')}] {role}: {text}"
        # ARRING (Redis 8.8): ring buffer nativo capped, sostituisce rpush+ltrim.
        # Espulsione FIFO automatica oltre il cap, O(1) per insert.
        self.r.execute_command("ARRING", key, self._CONVERSATION_RING_CAP, entry)
        self.r.expire(key, 60 * 60 * 24 * 30)  # 30 giorni

    def get_today_conversation(
        self,
        *,
        memory_scope: str | None = None,
    ) -> list[str]:
        date_key = now().strftime("%Y-%m-%d")
        memory_scope = normalize_scope(memory_scope or current_scope())
        key = (
            f"euri:conversation:{date_key}"
            if memory_scope == PERSONAL_SCOPE
            else f"euri:conversation:{memory_scope}:{date_key}"
        )
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
            # Correzioni pragmatiche: l'utente chiarisce che il turno precedente era
            # scherzo/provocazione, quindi una memoria estratta letteralmente va
            # contestata come fatto (non è dominio-specifico).
            r'\bstavo\s+scherzando\b',
            r'\bera\s+(?:uno\s+scherzo|una\s+provocazione)\b',
            r'\bti\s+prendevo\s+in\s+giro\b',
            r'\bnon\s+(?:ho|avevo)\s+davvero\b',
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
    _CORRECTION_TARGET_STOP = {
        "aggiungo", "aggiunta", "aggiunto", "memoria", "memorie", "memorie",
        "collegamenti", "collegamento", "sbagliato", "sbagliata", "sbagliati",
        "sbagliate", "correzione", "correzioni", "solo", "niente", "altro",
        "nuova", "nuovo", "collaboratrice", "collaboratore", "apprendimento",
        "laboratorio", "parte", "detto", "detta", "detti", "dette", "quello",
        "quella", "quelli", "quelle", "dove", "qui", "lì", "li", "da", "del",
        "della", "dei", "degli", "delle", "nel", "nella", "nelle", "sul",
        "sulla", "sui", "sulle", "per", "con", "che", "hai", "fatto", "fatta",
        "fatti", "fatte", "tue", "tuoi", "tua", "tuo", "questa", "questo",
        "questa", "questioni", "invece", "anche", "non", "che", "come", "cosa",
        "oppure", "ovvero", "team",
        # Meta-lessico pragmatico: descrive il fatto che l'utente stava testando
        # o scherzando, non identifica il fatto eventualmente ritirato. Senza
        # questo filtro una frase come "stavo scherzando, volevo vedere se avevi
        # capito il termine" può sovrapporsi accidentalmente a una memoria RAG.
        "stavo", "scherzando", "scherzo", "provocazione", "prendevo", "giro",
        "davvero", "volevo", "vedere", "capito", "capire", "ancora",
        "significava", "significa", "termine", "concetto", "metodologia",
        "test", "provare", "prova",
    }
    _IMMEDIATE_QUARANTINE_EXPLICIT_RE = [
        re.compile(p, re.IGNORECASE) for p in [
            r"\bnon\s+[èe]\s+vero\b",
            r"\bti\s+correggo\b",
            r"\bno\s*,?\s+ti\s+correggo\b",
            r"\bno\s*,?\s+.*\bcorreggo\b",
            r"\bti\s+sbagli\b",
            r"\bhai\s+sbagliato\b",
            r"\bhai\s+inventato\b",
        ]
    ]
    _IMMEDIATE_QUARANTINE_PRAGMATIC_RE = [
        re.compile(p, re.IGNORECASE) for p in [
            # Una vera ritrattazione del proprio fatto, non una descrizione del
            # tono ("stavo scherzando") o dell'intento ("era una provocazione").
            # Questi ultimi restano correction signal per il Loop 2g, ma non
            # hanno autorità sufficiente a mutare subito una memoria canonica.
            r"\bnon\s+(ho|avevo)\s+davvero\b",
        ]
    ]
    _META_JOKE_RE = [
        re.compile(p, re.IGNORECASE) for p in [
            r"\bstavo\s+scherzando\b",
            r"\bera\s+(?:uno\s+scherzo|una\s+provocazione)\b",
            r"\bti\s+prendevo\s+in\s+giro\b",
        ]
    ]

    @classmethod
    def _salient_tokens(cls, text: str) -> set[str]:
        return {
            m.group(1).lower() for m in cls._SALIENT_RE.finditer(text or "")
        } - cls._SALIENT_STOP

    @classmethod
    def correction_target_tokens(cls, text: str) -> list[str]:
        """
        Estrae i token utili a identificare il soggetto/bersaglio di una correzione.

        Mantiene i termini specifici in ordine di apparizione e scarta il gergo
        meta-correttivo e i riempitivi. Usata dal Loop 2g per passare da
        correzione "contesto-based" a correzione "subject-targeted".
        """
        tokens: list[str] = []
        for token in re.findall(r"\b[\wÀ-ü]{4,}\b", text or ""):
            low = token.lower()
            if low in cls._CORRECTION_TARGET_STOP:
                continue
            tokens.append(low)
        return list(dict.fromkeys(tokens))

    @classmethod
    def correction_overlap_score(cls, reference: str, content: str) -> int:
        """Quanti token utili della correzione compaiono nel contenuto."""
        ref = set(cls.correction_target_tokens(reference))
        cont = {
            m.group(0).lower()
            for m in re.finditer(r"\b[\wÀ-ü]{4,}\b", content or "")
        }
        return len(ref & cont)

    @classmethod
    def _is_immediate_quarantine_correction(cls, text: str) -> bool:
        """True solo per correzioni esplicite abbastanza forti da demuovere subito."""
        if not text:
            return False
        if any(p.search(text) for p in cls._IMMEDIATE_QUARANTINE_EXPLICIT_RE):
            return True
        if not any(p.search(text) for p in cls._IMMEDIATE_QUARANTINE_PRAGMATIC_RE):
            return False
        # Una ritrattazione pragmatica ha autorità immediata soltanto quando
        # contiene una formula fattuale esplicita ("non ho davvero...") e almeno
        # due token sostanziali con cui identificare il bersaglio. Gli altri
        # marcatori restano segnali non mutanti per il Loop 2g.
        return len(cls.correction_target_tokens(text)) >= 2

    @classmethod
    def _is_meta_joke_audit_only(cls, text: str) -> bool:
        """Uno scherzo descritto come tale non autorizza mutazioni differite.

        Se nello stesso turno compare anche una formula fattuale esplicita
        (`ti correggo`, `non ho davvero`, ...), il normale circuito correttivo
        resta disponibile. Altrimenti conserviamo soltanto una traccia chiusa:
        nessun Pulse e nessun giudice notturno può promuoverla a correzione.
        """
        if not text or not any(p.search(text) for p in cls._META_JOKE_RE):
            return False
        if any(p.search(text) for p in cls._IMMEDIATE_QUARANTINE_EXPLICIT_RE):
            return False
        if any(p.search(text) for p in cls._IMMEDIATE_QUARANTINE_PRAGMATIC_RE):
            return False
        return True

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

    @staticmethod
    def _last_rag_ctx_key(memory_scope: str | None = None) -> str:
        scope = normalize_scope(memory_scope or current_scope())
        return (
            "euri:last_rag_ctx"
            if scope == PERSONAL_SCOPE
            else f"euri:last_rag_ctx:{scope}"
        )

    def set_last_rag_ctx(
        self,
        ids: list[str],
        *,
        memory_scope: str | None = None,
    ) -> None:
        """
        Memorizza gli ID delle memorie iniettate nel turno corrente.
        Usato dal Loop 2g per ricostruire il contesto del turno che ha generato l'errore.
        TTL 1h: oltre quel limite, la correzione non è più associabile in modo affidabile.
        """
        key = self._last_rag_ctx_key(memory_scope)
        if not ids:
            self.r.delete(key)
            return
        # SET atomico con TTL nello stesso comando: elimina la finestra delete→rpush in
        # cui un lettore vedeva la lista vuota, e la chiave orfana senza TTL se il processo
        # moriva tra rpush ed expire. Lista serializzata in JSON (gli id non hanno virgole).
        self.r.set(key, json.dumps(ids), ex=3600)

    def get_last_rag_ctx(self, *, memory_scope: str | None = None) -> list[str]:
        """Recupera gli ID del RAG context del turno precedente (può essere vuoto)."""
        key = self._last_rag_ctx_key(memory_scope)
        try:
            raw = self.r.get(key)
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

    def _quarantine_correction_targets(
        self,
        signal_id: str,
        correzione_user: str,
        rag_ctx_ids: list[str],
        *,
        created_at: float,
        memory_scope: str | None = None,
    ) -> list[str]:
        """Demote immediato e reversibile per il bersaglio evidente di una correzione.

        Non sostituisce il Loop 2g: qui non decidiamo se la memoria sia "cattiva".
        Evitiamo solo che una memoria appena corretta resti richiamabile come fatto
        forte nella stessa sessione. La selezione è volutamente conservativa:
        correzione forte + overlap lessicale sul contenuto del nodo.
        """
        if not self._is_immediate_quarantine_correction(correzione_user):
            return []

        expected_scope = normalize_scope(memory_scope or current_scope())
        scored: list[tuple[int, str, dict]] = []
        for mid in rag_ctx_ids or []:
            if not mid:
                continue
            mkey = mid if str(mid).startswith("euri:memory:") else f"euri:memory:{mid}"
            try:
                raw = self.r.json().get(mkey, "$")
                doc = raw[0] if raw else {}
            except Exception:
                continue
            if normalize_scope(doc.get("memory_scope")) != expected_scope:
                continue
            content = doc.get("content") or ""
            score = self.correction_overlap_score(correzione_user, content)
            if score >= 2:
                scored.append((score, str(mid).replace("euri:memory:", ""), doc))

        if not scored:
            return []

        max_score = max(score for score, _, _ in scored)
        targets = [(mid, doc) for score, mid, doc in scored if score == max_score][:3]
        quarantined: list[str] = []
        for mid, doc in targets:
            mkey = f"euri:memory:{mid}"
            try:
                self.r.json().set(mkey, "$.correction_pending", True)
                self.r.json().set(mkey, "$.correction_signal_id", signal_id)
                self.r.json().set(mkey, "$.correction_pending_at", created_at)
                self.r.json().set(
                    mkey,
                    "$.correction_pending_prev_requires_verification",
                    bool(doc.get("requires_verification")),
                )
                self.r.json().set(mkey, "$.requires_verification", True)
                remove_loop2e_candidate(self.r, mid)
                quarantined.append(mid)
            except Exception as e:
                logger.debug(f"Correction quarantine fallita su {mid[:8]}: {e}")
        return quarantined

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
        created_at = to_timestamp(now())
        memory_scope = current_scope()
        audit_only = self._is_meta_joke_audit_only(correzione_user)
        explicit_correction = self._is_immediate_quarantine_correction(correzione_user)
        mutation_policy = (
            "audit_only"
            if audit_only
            else "explicit_correction"
            if explicit_correction
            else "proposal_only"
        )
        doc = {
            "id": sid,
            "prompt_original": prompt_originale,
            "risposta_euri": risposta_euri,
            "correzione_user": correzione_user,
            "rag_ctx_ids": rag_ctx_ids or [],
            "quarantined_memory_ids": [],
            "status": "dismissed" if audit_only else "pending",
            "verdict": "not_a_correction" if audit_only else None,
            "mutation_policy": mutation_policy,
            "memory_scope": memory_scope,
            "dismiss_reason": "pragmatic_meta_signal" if audit_only else None,
            "created_at": created_at,
            "analyzed_at": created_at if audit_only else None,
        }
        self.r.json().set(key, "$", doc)
        self.r.expire(key, 30 * 86400)
        quarantined = (
            []
            if audit_only
            else self._quarantine_correction_targets(
                sid,
                correzione_user,
                rag_ctx_ids or [],
                created_at=created_at,
                memory_scope=memory_scope,
            )
        )
        if quarantined:
            self.r.json().set(key, "$.quarantined_memory_ids", quarantined)
            logger.info(
                "Correction quarantine: "
                + ", ".join(mid[:8] for mid in quarantined)
                + f" → requires_verification pending ({sid[:8]})"
            )
        if audit_only:
            logger.info(
                f"Correction observation audit-only: {sid[:8]} — "
                f"'{correzione_user[:60]}'"
            )
            return sid
        logger.info(f"Correction signal salvato: {sid[:8]} — '{correzione_user[:60]}'")
        # Pulse afferente (Fase 1): una correzione viene dal mondo (→ extero). Il Loop 2g la
        # consuma di notte; qui la rendiamo percepibile anche al polso. Fail-open.
        pulse_emit(self.r, "correction", "extero", "signal",
                   payload={"id": sid, "correction": correzione_user[:200]},
                   salience=0.6)
        return sid
