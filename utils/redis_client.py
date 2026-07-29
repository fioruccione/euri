import redis
from redis.commands.search.field import TextField, TagField, NumericField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from loguru import logger
import config
from core.embedder import DIM


def get_client() -> redis.Redis:
    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )


def init_indexes(r: redis.Redis):
    """Crea/migra gli indici RediSearch se necessario."""
    backfill_memory_scopes(r)
    _ensure_memory_index(r)
    _ensure_note_index(r)
    _create_dream_index(r)
    _create_insight_index(r)
    logger.info("Indici Redis inizializzati")


def flush_and_reinit(r: redis.Redis):
    """Cancella tutti i dati euri:* e ricrea gli indici da zero."""
    # idx:todos resta nella lista di drop per pulire i DB pre-migrazione impegni.
    for idx in ("idx:memories", "idx:todos", "idx:notes", "idx:dreams", "idx:insights"):
        try:
            r.ft(idx).dropindex()
        except Exception:
            pass

    keys = r.keys("euri:*")
    if keys:
        r.delete(*keys)
    logger.info(f"Cancellate {len(keys)} chiavi euri:*")

    _create_memory_index(r)
    _create_note_index(r)
    _create_dream_index(r)
    _create_insight_index(r)
    logger.info("Indici ricreati con schema aggiornato")


def _has_field(r: redis.Redis, index: str, field_name: str) -> bool:
    """True se l'indice contiene già il campo specificato."""
    try:
        info = r.ft(index).info()
        for field in info.get("attributes", []):
            field_flat = [f.lower() if isinstance(f, str) else f for f in field]
            if field_name in field_flat or field_name.encode() in field_flat:
                return True
    except Exception:
        pass
    return False


def _ensure_memory_index(r: redis.Redis):
    """Crea o migra idx:memories includendo ricerca semantica, stato e cronologia."""
    try:
        r.ft("idx:memories").info()
        required = {
            "embedding": "VECTOR",
            "domain": "domain",
            "status": "status",
            "memory_kind": "memory_kind",
            "memory_scope": "memory_scope",
            "asserted_at": "asserted_at",
            "event_start": "event_start",
            "event_end": "event_end",
        }
        missing = [label for field, label in required.items() if not _has_field(r, "idx:memories", field)]
        if missing:
            logger.info(f"Migrazione idx:memories: aggiunta campi {missing}...")
            r.ft("idx:memories").dropindex()
            _create_memory_index(r)
            logger.info("Migrazione idx:memories completata (dati preservati)")
    except Exception:
        _create_memory_index(r)


def _create_memory_index(r: redis.Redis):
    definition = IndexDefinition(prefix=["euri:memory:"], index_type=IndexType.JSON)
    schema = (
        TextField("$.content", as_name="content"),
        TagField("$.category", as_name="category"),
        TagField("$.source", as_name="source"),
        TagField("$.domain", as_name="domain"),          # ← NUOVO: domain gating
        TagField("$.memory_kind", as_name="memory_kind"),
        TagField("$.memory_scope", as_name="memory_scope"),
        NumericField("$.created_at", as_name="created_at", sortable=True),
        NumericField("$.asserted_at", as_name="asserted_at", sortable=True),
        NumericField("$.event_start", as_name="event_start", sortable=True),
        NumericField("$.event_end", as_name="event_end", sortable=True),
        NumericField("$.due_at", as_name="due_at", sortable=True),
        # Impegni assorbiti nel modello memoria: pending/done vive solo sulle
        # memorie-impegno (le altre hanno status null → fuori da @status:{...}).
        TagField("$.status", as_name="status"),
        TagField("$.tags[*]", as_name="tags"),
        VectorField(
            "$.embedding",
            "FLAT",
            {
                "TYPE": "FLOAT32",
                "DIM": DIM,
                "DISTANCE_METRIC": "COSINE",
            },
            as_name="embedding",
        ),
    )
    r.ft("idx:memories").create_index(schema, definition=definition)
    logger.info("Creato indice idx:memories (con VECTOR + domain)")


def backfill_memory_scopes(r: redis.Redis) -> dict:
    """Marca come personali i documenti legacy privi di scope.

    La migrazione è idempotente e non interpreta il contenuto. Viene eseguita
    prima della ricostruzione dell'indice, così il filtro fail-closed non rende
    invisibile la memoria storica.
    """
    memories = turns = notes = skipped = 0
    for pattern, field in (
        ("euri:memory:*", "$.memory_scope"),
        ("euri:turn:*", "$.memory_scope"),
        ("euri:note:*", "$.memory_scope"),
    ):
        for key in r.scan_iter(pattern):
            try:
                raw = r.json().get(key, "$")
                if not raw or not isinstance(raw[0], dict):
                    skipped += 1
                    continue
                if raw[0].get("memory_scope"):
                    continue
                r.json().set(key, field, "personal")
                if pattern.startswith("euri:memory"):
                    memories += 1
                elif pattern.startswith("euri:turn"):
                    turns += 1
                else:
                    notes += 1
            except Exception:
                # Nel namespace memory esistono anche chiavi legacy non-JSON.
                skipped += 1
    if memories or turns or notes:
        logger.info(
            "Backfill scope memoria: {} memorie, {} turni e {} note marcati personal",
            memories,
            turns,
            notes,
        )
    return {
        "memories": memories,
        "turns": turns,
        "notes": notes,
        "skipped": skipped,
    }


def _ensure_note_index(r: redis.Redis):
    """Crea o migra idx:notes includendo il confine di memoria."""
    try:
        r.ft("idx:notes").info()
        if not _has_field(r, "idx:notes", "memory_scope"):
            logger.info("Migrazione idx:notes: aggiunta campo memory_scope...")
            r.ft("idx:notes").dropindex()
            _create_note_index(r)
            logger.info("Migrazione idx:notes completata (dati preservati)")
    except Exception:
        _create_note_index(r)


def _create_note_index(r: redis.Redis):
    try:
        r.ft("idx:notes").info()
    except Exception:
        definition = IndexDefinition(prefix=["euri:note:"], index_type=IndexType.JSON)
        schema = (
            TextField("$.content", as_name="content"),
            TagField("$.category", as_name="category"),
            TagField("$.source", as_name="source"),
            TagField("$.memory_scope", as_name="memory_scope"),
            NumericField("$.created_at", as_name="created_at", sortable=True),
            TagField("$.tags[*]", as_name="tags"),
        )
        r.ft("idx:notes").create_index(schema, definition=definition)
        logger.info("Creato indice idx:notes")


def _create_dream_index(r: redis.Redis):
    """Indice per i sogni del Dream Engine (Loop 2b)."""
    try:
        r.ft("idx:dreams").info()
    except Exception:
        definition = IndexDefinition(prefix=["euri:dream:"], index_type=IndexType.JSON)
        schema = (
            TextField("$.content", as_name="content"),
            TagField("$.status", as_name="status"),          # candidate | discarded
            TagField("$.domain_a", as_name="domain_a"),
            TagField("$.domain_b", as_name="domain_b"),
            NumericField("$.created_at", as_name="created_at", sortable=True),
        )
        r.ft("idx:dreams").create_index(schema, definition=definition)
        logger.info("Creato indice idx:dreams")


def _create_insight_index(r: redis.Redis):
    """Indice per gli Insight promossi (Loop 2c)."""
    try:
        r.ft("idx:insights").info()
    except Exception:
        definition = IndexDefinition(prefix=["euri:insight:"], index_type=IndexType.JSON)
        schema = (
            TextField("$.content", as_name="content"),
            TagField("$.status", as_name="status"),          # candidate | validated | promoted
            TagField("$.domain_a", as_name="domain_a"),
            TagField("$.domain_b", as_name="domain_b"),
            NumericField("$.created_at", as_name="created_at", sortable=True),
            NumericField("$.recalled_count", as_name="recalled_count", sortable=True),
            VectorField(
                "$.embedding",
                "FLAT",
                {
                    "TYPE": "FLOAT32",
                    "DIM": DIM,
                    "DISTANCE_METRIC": "COSINE",
                },
                as_name="embedding",
            ),
        )
        r.ft("idx:insights").create_index(schema, definition=definition)
        logger.info("Creato indice idx:insights")


def backfill_embeddings(r: redis.Redis, embedder) -> int:
    """
    Aggiunge il campo embedding alle memorie esistenti che ne sono prive.
    Ritorna il numero di documenti aggiornati.
    """
    if not embedder.available:
        return 0

    updated = 0
    skipped_non_json = 0
    for key in r.scan_iter("euri:memory:*"):
        try:
            key_type = r.type(key)
            if isinstance(key_type, bytes):
                key_type = key_type.decode("utf-8", errors="replace")
            if str(key_type or "").lower() not in {"rejson-rl", "json"}:
                skipped_non_json += 1
                continue
            doc = r.json().get(key, "$")
            if not doc:
                continue
            item = doc[0]
            if item.get("embedding"):
                continue  # già presente
            content = item.get("content", "")
            if not content:
                continue
            vec = embedder.encode(content)
            if vec is None:
                continue
            r.json().set(key, "$.embedding", vec.tolist())
            updated += 1
        except Exception as e:
            logger.error(f"Errore backfill {key}: {e}")

    if skipped_non_json:
        logger.debug(
            "Backfill embedding: {} chiavi non RedisJSON ignorate",
            skipped_non_json,
        )
    if updated:
        logger.info(f"Backfill embedding: {updated} memorie aggiornate")
    return updated
