"""Outbox durevole per gli effetti derivati da una memoria canonica."""

from __future__ import annotations

import time

from loguru import logger

from core.memory_attention import update_loop2e_candidate_index
from core.pulse import pulse_emit_once
from utils.obsidian_sync import write_memory


MEMORY_OUTBOX_PENDING = "euri:outbox:memory:pending"
MEMORY_OUTBOX_PREFIX = "euri:outbox:memory:"

_EXTERO_MEMORY_SOURCES = {"user", "teach", "conversation", "episode", "mobile_in"}


def memory_outbox_key(memory_id: str) -> str:
    return f"{MEMORY_OUTBOX_PREFIX}{memory_id}"


def _decode_hash(fields: dict) -> dict[str, str]:
    decoded = {}
    for key, value in (fields or {}).items():
        if isinstance(key, bytes):
            key = key.decode("utf-8", errors="replace")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        decoded[str(key)] = str(value)
    return decoded


def _retry_later(r, event_key: str, error: Exception | str) -> None:
    try:
        attempts = int(r.hincrby(event_key, "attempts", 1))
        r.hset(event_key, mapping={"last_error": str(error)[:500], "last_attempt_at": time.time()})
        delay = min(300, 2 ** min(attempts, 8))
        r.zadd(MEMORY_OUTBOX_PENDING, {event_key: time.time() + delay})
    except Exception:
        pass


def process_memory_outbox_event(r, event_key: str) -> bool:
    """Applica un evento. Ogni effetto e' idempotente, quindi il replay e' sicuro."""
    try:
        event = _decode_hash(r.hgetall(event_key))
        if not event:
            r.zrem(MEMORY_OUTBOX_PENDING, event_key)
            return True

        memory_key = event["memory_key"]
        raw = r.json().get(memory_key, "$")
        if not raw:
            r.zrem(MEMORY_OUTBOX_PENDING, event_key)
            r.delete(event_key)
            logger.warning(f"Memory outbox orfana rimossa: {event_key} -> {memory_key}")
            return True
        doc = raw[0]
        memory_id = str(doc["id"])

        expires_at = doc.get("expires_at")
        if expires_at:
            r.expireat(memory_key, int(float(expires_at)))

        update_loop2e_candidate_index(r, doc, strict=True)

        source = doc.get("source") or ""
        axes = doc.get("memory_axes") or {}
        emitted = pulse_emit_once(
            r,
            event_id=f"memory-saved:{memory_id}",
            sense="memory",
            source="extero" if source in _EXTERO_MEMORY_SOURCES else "intero",
            kind="saved",
            payload={
                "id": memory_id,
                "mem_source": source,
                "memory_kind": doc.get("memory_kind"),
                "domain": doc.get("domain"),
                "asserted_at": doc.get("asserted_at"),
                "event_start": doc.get("event_start"),
                "event_end": doc.get("event_end"),
                "requires_verification": bool(doc.get("requires_verification")),
                "memory_axes": {
                    "subject_status": axes.get("subject_status"),
                    "audit_reasons": axes.get("audit_reasons", []),
                    "fact_types": axes.get("fact_types", []),
                    "temporal_markers": axes.get("temporal_markers", []),
                },
                "safety_flag": doc.get("safety_flag") or [],
            },
            salience=0.55
            if (doc.get("safety_flag") or doc.get("requires_verification"))
            else 0.35,
            marker_key=event_key,
        )
        if not emitted:
            raise RuntimeError("Pulse non disponibile")

        if write_memory(doc) is False:
            raise RuntimeError("Obsidian sync non completato")

        r.zrem(MEMORY_OUTBOX_PENDING, event_key)
        r.delete(event_key)
        return True
    except Exception as exc:
        _retry_later(r, event_key, exc)
        logger.warning(f"Memory outbox retry {event_key}: {exc}")
        return False


def drain_memory_outbox(r, limit: int = 20) -> tuple[int, int]:
    """Processa gli eventi dovuti; ritorna (successi, fallimenti)."""
    try:
        pending = r.zrangebyscore(MEMORY_OUTBOX_PENDING, "-inf", time.time(), start=0, num=limit)
    except Exception as exc:
        logger.warning(f"Memory outbox non leggibile: {exc}")
        return 0, 1

    ok = failed = 0
    for event_key in pending:
        if isinstance(event_key, bytes):
            event_key = event_key.decode("utf-8", errors="replace")
        if process_memory_outbox_event(r, str(event_key)):
            ok += 1
        else:
            failed += 1
    return ok, failed
