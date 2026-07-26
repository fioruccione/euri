"""Proiezione osservazionale e replayabile degli eventi cognitivi del Pulse.

Il projector è deliberatamente privo di effetti cognitivi: non scrive memorie,
non modifica insight e non esegue azioni. Filtra l'unico stream afferente e crea
una timeline più piccola in cui gli archi causali possono essere misurati.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from core.pulse import COGNITIVE_EVENT, PULSE_STREAM


COGNITIVE_STREAM = "euri:cognitive:events"
COGNITIVE_PROJECTOR_GROUP = "euri:cognitive:projector:v1"
COGNITIVE_PROJECTOR_CONSUMER = "euri-runtime"
COGNITIVE_PROJECTOR_STATE = "euri:cognitive:projector:state"
_MAXLEN = 20000


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def normalize_event(event: dict[Any, Any]) -> dict[str, str]:
    return {_text(key): _text(value) for key, value in (event or {}).items()}


def ensure_projector_group(r) -> None:
    """Crea il cursore durevole dall'inizio della storia disponibile."""
    try:
        r.xgroup_create(
            PULSE_STREAM,
            COGNITIVE_PROJECTOR_GROUP,
            id="0-0",
            mkstream=True,
        )
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _is_duplicate_xadd(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "equal or smaller" in message
        or "id specified in xadd is equal" in message
        or "the id specified in xadd" in message and "smaller" in message
    )


def _projection_fields(source_event_id: str, event: dict[str, str]) -> dict[str, str]:
    fields = {
        "source_event_id": source_event_id,
        "schema_version": event.get("schema_version", ""),
        "event_class": event.get("event_class", ""),
        "sense": event.get("sense", ""),
        "source": event.get("source", ""),
        "kind": event.get("kind", ""),
        "producer": event.get("producer", ""),
        "trace_id": event.get("trace_id", ""),
        "causation_id": event.get("causation_id", ""),
        "logical_event_id": event.get("logical_event_id", ""),
        "entity_refs": event.get("entity_refs", "[]"),
        "parent_refs": event.get("parent_refs", "[]"),
        "epistemic_before": event.get("epistemic_before", ""),
        "epistemic_after": event.get("epistemic_after", ""),
        "experiment_version": event.get("experiment_version", ""),
        "duration_ms": event.get("duration_ms", ""),
        "payload": event.get("payload", "{}"),
        "salience": event.get("salience", ""),
        "ts": event.get("ts", ""),
    }
    return fields


def _record_state(r, source_event_id: str, event: dict[str, str], outcome: str) -> None:
    """Snapshot operativo idempotente; la timeline resta la fonte primaria."""
    try:
        r.hset(
            COGNITIVE_PROJECTOR_STATE,
            mapping={
                "last_source_event_id": source_event_id,
                "last_trace_id": event.get("trace_id", ""),
                "last_event_type": f"{event.get('sense', '')}/{event.get('kind', '')}",
                "last_event_ts": event.get("ts", ""),
                "last_outcome": outcome,
            },
        )
    except Exception as exc:
        logger.debug(f"Cognitive Projector: stato non aggiornato ({exc})")


def project_cognitive_event(r, source_event_id: str, raw_event: dict) -> str:
    """Proietta un evento; ritorna ``projected``, ``duplicate`` o ``ignored``.

    Riusa lo stream ID sorgente come ID destinazione. Redis rende così il replay
    naturalmente idempotente: lo stesso evento non può essere copiato due volte.
    """
    event = normalize_event(raw_event)
    if event.get("event_class") != COGNITIVE_EVENT:
        return "ignored"

    outcome = "projected"
    try:
        r.xadd(
            COGNITIVE_STREAM,
            _projection_fields(source_event_id, event),
            id=source_event_id,
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:
        if not _is_duplicate_xadd(exc):
            raise
        outcome = "duplicate"

    _record_state(r, source_event_id, event, outcome)
    return outcome


def consume_projector_batch(
    r,
    *,
    pending: bool,
    count: int = 100,
    block_ms: int = 2000,
) -> tuple[int, int, int]:
    """Consuma e ACKa un batch: (letti, proiettati, ignorati).

    Con ``pending=True`` recupera prima gli eventi consegnati al consumer stabile
    ma non ACKati prima di un crash. Gli errori di proiezione non vengono ACKati.
    """
    stream_id = "0" if pending else ">"
    kwargs = {
        "groupname": COGNITIVE_PROJECTOR_GROUP,
        "consumername": COGNITIVE_PROJECTOR_CONSUMER,
        "streams": {PULSE_STREAM: stream_id},
        "count": max(1, int(count)),
    }
    if not pending:
        kwargs["block"] = max(1, int(block_ms))

    streams = r.xreadgroup(**kwargs)
    read = projected = ignored = 0
    for _stream, entries in streams or []:
        for event_id, event in entries:
            event_id = _text(event_id)
            read += 1
            try:
                outcome = project_cognitive_event(r, event_id, event)
            except Exception as exc:
                logger.warning(
                    f"Cognitive Projector: evento {event_id} non proiettato ({exc})"
                )
                continue
            if outcome == "ignored":
                ignored += 1
            else:
                projected += 1
            r.xack(PULSE_STREAM, COGNITIVE_PROJECTOR_GROUP, event_id)
    return read, projected, ignored
