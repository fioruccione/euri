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
    _ensure_memory_index(r)
    _create_note_index(r)
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
    """Crea o migra idx:memories per includere VECTOR, domain e status (impegni)."""
    try:
        r.ft("idx:memories").info()
        # Controlla se ha già VECTOR, domain e status
        has_vector = _has_field(r, "idx:memories", "embedding")
        has_domain = _has_field(r, "idx:memories", "domain")
        has_status = _has_field(r, "idx:memories", "status")
        if not has_vector or not has_domain or not has_status:
            missing = []
            if not has_vector:
                missing.append("VECTOR")
            if not has_domain:
                missing.append("domain")
            if not has_status:
                missing.append("status")
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
        NumericField("$.created_at", as_name="created_at", sortable=True),
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


def _create_note_index(r: redis.Redis):
    try:
        r.ft("idx:notes").info()
    except Exception:
        definition = IndexDefinition(prefix=["euri:note:"], index_type=IndexType.JSON)
        schema = (
            TextField("$.content", as_name="content"),
            TagField("$.category", as_name="category"),
            TagField("$.source", as_name="source"),
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

    keys = r.keys("euri:memory:*")
    updated = 0
    for key in keys:
        try:
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

    if updated:
        logger.info(f"Backfill embedding: {updated} memorie aggiornate")
    return updated
