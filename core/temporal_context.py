"""Temporal grounding for conversation turns and derived memories.

Exact timestamps stay machine-facing.  Prompts receive qualitative distance and
episode boundaries so the model can order events without narrating the clock.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import config
from loguru import logger
from utils.date_utils import from_timestamp
from utils.temporal import extract_temporal_range


TEMPORAL_SCHEMA_VERSION = 1

_TEMPORAL_EXPRESSION_RE = re.compile(
    r"\b(?:questa\s+mattina|stamattina|stamani|stamane|questa\s+sera|stasera|"
    r"stanotte|oggi|ieri|l['’]altro\s+ieri|\d+\s+giorni\s+fa|"
    r"(?:\d+|un['’]?|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"
    r"\s*(?:ora|ore)\s+fa|"
    r"poco\s+fa|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
    r"(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)"
    r"(?:\s+scors[oa])?|"
    r"\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
    r"settembre|ottobre|novembre|dicembre))\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b"
)


def _as_timestamp(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def qualitative_distance(observed_at: Any, reference_at: Any = None) -> str:
    """Human-scale distance used only as internal prompt metadata."""
    ts = _as_timestamp(observed_at)
    ref = _as_timestamp(reference_at) or time.time()
    if ts is None:
        return "tempo non registrato"
    delta = max(0.0, ref - ts)
    if delta < 90:
        return "adesso"
    if delta < 15 * 60:
        return f"circa {max(2, round(delta / 60))} minuti fa"
    if delta < 90 * 60:
        return "circa un'ora fa"
    if delta < 12 * 3600:
        return f"circa {round(delta / 3600)} ore fa"

    dt = from_timestamp(ts)
    ref_dt = from_timestamp(ref)
    if dt is None or ref_dt is None:
        return "tempo non registrato"
    if dt.date() == ref_dt.date():
        return "prima oggi"
    if (ref_dt.date() - dt.date()).days == 1:
        return "ieri"
    days = max(1, (ref_dt.date() - dt.date()).days)
    if days < 7:
        return f"{days} giorni fa"
    if days < 35:
        weeks = max(1, round(days / 7))
        return f"{weeks} {'settimana' if weeks == 1 else 'settimane'} fa"
    months = max(1, round(days / 30))
    return f"{months} {'mese' if months == 1 else 'mesi'} fa"


def turn_time_label(observed_at: Any, reference_at: Any = None) -> str:
    ts = _as_timestamp(observed_at)
    ref = _as_timestamp(reference_at) or time.time()
    if ts is None:
        return "tempo non registrato"
    dt = from_timestamp(ts)
    ref_dt = from_timestamp(ref)
    if dt is None or ref_dt is None:
        return qualitative_distance(ts, ref)
    if dt.date() == ref_dt.date():
        absolute = f"oggi {dt.strftime('%H:%M')}"
    elif (ref_dt.date() - dt.date()).days == 1:
        absolute = f"ieri {dt.strftime('%H:%M')}"
    else:
        absolute = dt.strftime("%d/%m/%Y %H:%M")
    return f"{absolute}; {qualitative_distance(ts, ref)}"


def history_content_for_prompt(message: dict, *, reference_at: Any = None) -> str:
    """Annotate one chat message without changing its semantic content."""
    label = turn_time_label(message.get("observed_at"), reference_at)
    segment = message.get("segment_id")
    segment_label = f"; segmento {segment}" if segment is not None else ""
    return f"[tempo interno: {label}{segment_label}] {message.get('content', '')}"


def history_line_for_prompt(message: dict, *, reference_at: Any = None) -> str:
    who = (
        config.OWNER_DISPLAY_NAME
        if message.get("role") == "user"
        else config.ASSISTANT_DISPLAY_NAME
    )
    return f"[{turn_time_label(message.get('observed_at'), reference_at)}] {who}: {message.get('content', '')}"


def temporal_prompt_contract() -> str:
    return (
        "Le etichette 'tempo interno' sono metadati cognitivi: usale per ordinare i fatti, "
        "risolvere riferimenti e distinguere continuita' da riapertura. Non copiarle mai "
        "nella risposta e non recitare date o orari salvo che siano utili o richiesti. "
        "Un turno di ore prima non e' 'poco fa'. Se il nuovo turno e' ellittico (per esempio "
        "un valore, 'quello' o 'il risultato') e segue un tema aperto nello stesso segmento, "
        "collegalo a quel tema e chiedi soltanto gli attributi davvero mancanti. Un numero "
        "senza unita', metodo o riferimento puo' essere collegato e ricordato, ma non basta "
        "per dichiararlo tecnicamente coerente, normale o anomalo."
    )


def _temporal_expression(text: str) -> str:
    match = _TEMPORAL_EXPRESSION_RE.search(text or "")
    return match.group(0) if match else ""


def _event_precision(expression: str) -> str:
    low = (expression or "").lower()
    if "matt" in low or "staman" in low or "sera" in low or "notte" in low:
        return "part_of_day"
    if re.search(r"(?:\d{1,2}\s+[a-zà-öø-ÿ]+|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2})", low):
        return "explicit_day"
    if low:
        return "relative_day"
    return "unspecified"


def resolve_text_event_time(text: str, *, asserted_at: float) -> dict:
    """Resolve a temporal phrase in text against the moment it was asserted."""
    expression = _temporal_expression(text)
    event_range = None
    if expression:
        reference_dt = datetime.fromtimestamp(asserted_at, tz=config.TIMEZONE)
        event_range = extract_temporal_range(expression, reference_dt)
    return {
        "temporal_expression": expression,
        "event_precision": _event_precision(expression),
        "event_start": event_range[0] if event_range else None,
        "event_end": event_range[1] if event_range else None,
    }


def derive_passive_memory_metadata(
    fact_item: dict,
    conversation: list[dict],
    *,
    fallback_at: float | None = None,
) -> dict:
    """Build provenance and event-time metadata from the turns supporting a fact."""
    indexed: list[tuple[int, dict]] = []
    for index, message in enumerate(conversation, 1):
        turn_id = message.get("seq")
        try:
            turn_id = int(turn_id) if turn_id is not None else index
        except (TypeError, ValueError):
            turn_id = index
        indexed.append((turn_id, message))

    requested = []
    for value in fact_item.get("source_turn_ids") or []:
        try:
            requested.append(int(value))
        except (TypeError, ValueError):
            continue
    requested_set = set(requested)
    selected = [(tid, msg) for tid, msg in indexed if tid in requested_set]
    if not selected:
        selected = [(tid, msg) for tid, msg in indexed if msg.get("role") == "user"] or indexed

    observed = [
        _as_timestamp(msg.get("observed_at"))
        for _, msg in selected
        if msg.get("role") == "user"
    ]
    observed = [value for value in observed if value is not None]
    if not observed:
        observed = [_as_timestamp(msg.get("observed_at")) for _, msg in selected]
        observed = [value for value in observed if value is not None]
    asserted_at = max(observed) if observed else (fallback_at or time.time())

    source_text = " ".join(
        str(msg.get("content") or "") for _, msg in selected if msg.get("role") == "user"
    )
    content = str(fact_item.get("content") or "").strip()

    # La fonte conversazionale è canonica per il tempo. Il modello estrattore può
    # riassumere il fatto, ma non può sostituire una data relativa pronunciata
    # dall'utente con un proprio calcolo. Se la fonte non contiene alcun riferimento
    # temporale, ricadiamo sul contenuto estratto per compatibilità.
    source_event_time = resolve_text_event_time(source_text, asserted_at=asserted_at)
    content_event_time = resolve_text_event_time(content, asserted_at=asserted_at)
    event_time = (
        source_event_time
        if source_event_time.get("event_start") is not None
        else content_event_time
    )

    # Rete deterministica contro normalizzazioni LLM errate: se il contenuto
    # estratto materializza una data numerica diversa da quella risolta dalla
    # fonte, correggiamo soltanto quell'espressione. Il resto del fatto resta
    # invariato e l'originale relativo rimane tracciato nel temporal_context.
    canonical_content = content
    content_date_match = _NUMERIC_DATE_RE.search(content)
    content_date_corrected = False
    original_content_date = content_date_match.group(0) if content_date_match else ""
    canonical_event_start = _as_timestamp(event_time.get("event_start"))
    if content_date_match and canonical_event_start is not None:
        content_date_event = resolve_text_event_time(
            original_content_date,
            asserted_at=asserted_at,
        )
        content_date_start = _as_timestamp(content_date_event.get("event_start"))
        canonical_dt = from_timestamp(canonical_event_start)
        content_dt = from_timestamp(content_date_start)
        if (
            canonical_dt is not None
            and content_dt is not None
            and canonical_dt.date() != content_dt.date()
        ):
            canonical_date = canonical_dt.strftime("%d/%m/%Y")
            canonical_content = (
                content[: content_date_match.start()]
                + canonical_date
                + content[content_date_match.end() :]
            )
            content_date_corrected = True
            logger.warning(
                "Passive temporal guard: data estratta corretta "
                f"{original_content_date} → {canonical_date}"
            )

    latest = selected[-1][1] if selected else {}
    temporal_context = {
        "schema_version": TEMPORAL_SCHEMA_VERSION,
        "asserted_at": asserted_at,
        "source_turn_ids": [tid for tid, _ in selected],
        "source_turn_refs": [
            str(msg.get("turn_ref"))
            for _, msg in selected
            if msg.get("turn_ref")
        ],
        "conversation_id": latest.get("conversation_id") or "",
        "segment_id": latest.get("segment_id"),
        "source_temporal_expression": source_event_time.get("temporal_expression") or "",
        "content_temporal_expression": content_event_time.get("temporal_expression") or "",
        "content_date_corrected": content_date_corrected,
        "content_original_date": original_content_date if content_date_corrected else "",
        **event_time,
    }
    kind = fact_item.get("memory_kind") or "semantic_fact"
    if kind == "episode":
        kind = "conversation_anchor"
    return {
        "memory_kind": kind,
        "temporal_context": temporal_context,
        "canonical_content": canonical_content,
    }


def memory_time_label(memory: dict, *, reference_at: Any = None) -> str:
    """Prefer event time, then assertion time, then storage time for RAG labels."""
    ref = _as_timestamp(reference_at) or time.time()
    event_start = _as_timestamp(memory.get("event_start"))
    event_end = _as_timestamp(memory.get("event_end"))
    asserted_at = _as_timestamp(memory.get("asserted_at"))
    created_at = _as_timestamp(memory.get("created_at"))
    if event_start is not None:
        start_dt = from_timestamp(event_start)
        end_dt = from_timestamp(event_end)
        precision = (memory.get("temporal_context") or {}).get("event_precision")
        if start_dt is not None and end_dt is not None and precision == "conversation_interval":
            event = (
                f"conversazione del {start_dt.strftime('%d/%m/%Y')} "
                f"tra {start_dt.strftime('%H:%M')} e {end_dt.strftime('%H:%M')}"
            )
        elif start_dt is not None and precision == "part_of_day":
            period = "mattina" if start_dt.hour < 12 else "sera"
            event = _period_of_day_label(start_dt, period)
        elif start_dt is not None:
            event = start_dt.strftime("%d/%m/%Y")
        else:
            event = qualitative_distance(event_start, ref)
        asserted = qualitative_distance(asserted_at, ref) if asserted_at else ""
        return f"evento: {event}; riferito {asserted}" if asserted else f"evento: {event}"
    return qualitative_distance(asserted_at or created_at, ref)


def _period_of_day_label(dt: datetime, period: str) -> str:
    """Small Italian formatter kept separate for deterministic tests."""
    return f"{period} del {dt.strftime('%d/%m/%Y')}"
