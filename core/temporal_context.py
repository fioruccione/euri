"""Temporal grounding for conversation turns and derived memories.

Exact timestamps stay machine-facing.  Prompts receive qualitative distance and
episode boundaries so the model can order events without narrating the clock.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Any

import config
from loguru import logger
from utils.date_utils import from_timestamp
from utils.temporal import extract_temporal_range


TEMPORAL_SCHEMA_VERSION = 2

_MONTHS_IT = (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)
_MONTHS_IT_PATTERN = "|".join(_MONTHS_IT)

# Una data ellittica non viene interpretata da sola: serve una relazione
# temporale esplicita. Questo evita di trasformare qualsiasi numero ("il 24")
# in una data, ma consente al resolver calendariale di completare espressioni
# come "fino al 24" usando il timestamp del turno come ancora.
_RELATIONAL_DAY_PATTERNS = (
    (
        "until",
        re.compile(
            rf"\b(?P<prefix>(?:fino|sino)\s+(?:a|al|alla)\s+(?:giorno\s+)?)"
            rf"(?P<day>\d{{1,2}})(?:\s+(?P<month>{_MONTHS_IT_PATTERN}))?"
            r"(?:\s+(?P<year>\d{4}))?\b(?![/-]\d)",
            re.IGNORECASE,
        ),
    ),
    (
        "deadline",
        re.compile(
            rf"\b(?P<prefix>entro\s+(?:il\s+|giorno\s+)?)"
            rf"(?P<day>\d{{1,2}})(?:\s+(?P<month>{_MONTHS_IT_PATTERN}))?"
            r"(?:\s+(?P<year>\d{4}))?\b(?![/-]\d)",
            re.IGNORECASE,
        ),
    ),
    (
        "since",
        re.compile(
            rf"\b(?P<prefix>(?:a\s+partire\s+)?(?:dal|dalla)\s+(?:giorno\s+)?)"
            rf"(?P<day>\d{{1,2}})(?:\s+(?P<month>{_MONTHS_IT_PATTERN}))?"
            r"(?:\s+(?P<year>\d{4}))?\b(?![/-]\d)",
            re.IGNORECASE,
        ),
    ),
)

_NUMERIC_DATE_PATTERN = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"\d{1,2}-\d{1,2}-\d{2,4})"
)

_TEMPORAL_EXPRESSION_RE = re.compile(
    r"\b(?:questa\s+mattina|stamattina|stamani|stamane|questa\s+sera|stasera|"
    r"stanotte|oggi|ieri|l['’]altro\s+ieri|\d+\s+giorni\s+fa|"
    r"(?:\d+|un['’]?|una|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"
    r"\s*(?:ora|ore)\s+fa|"
    rf"poco\s+fa|{_NUMERIC_DATE_PATTERN}|"
    r"(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)"
    r"(?:\s+scors[oa])?|"
    r"\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
    r"settembre|ottobre|novembre|dicembre))\b",
    re.IGNORECASE,
)
_NUMERIC_DATE_RE = re.compile(
    rf"\b{_NUMERIC_DATE_PATTERN}\b"
)

# Una forma numerica simile a una data può essere in realtà una misura o un
# intervallo tecnico (``7-8%``, ``MFI 6/8``, ``7/8 kg``). In questi casi il
# resolver deve fallire chiuso: è preferibile non assegnare un event-time che
# trasformare il dato sorgente e creare una falsa data autorevole.
_NUMERIC_MEASUREMENT_PREFIX_RE = re.compile(
    r"(?:\bMFI|\bMFR|\bindice\s+di\s+fluidit[aà]|\bpercentuale|"
    r"\bconcentrazione|\bdosaggio|\bdose|\brange|\bintervallo|"
    r"\brapporto|\bvalore|\bgrad[oi])\s*(?:target\s*)?"
    r"(?:di|del|tra|da|pari\s+a|[èe]'?|[=:])?\s*$",
    re.IGNORECASE,
)
_NUMERIC_MEASUREMENT_SUFFIX_RE = re.compile(
    r"^\s*(?:[%‰]|°\s*[CFK]|"
    r"(?:percento|percentual[ei]?|punt[io]\s+percentual[ei]?|"
    r"mg|mcg|µg|g|kg|q|t|ml|cl|dl|l|mm|cm|km|m|"
    r"ms|second[oi]|minut[oi]|ore?|giorn[oi]|settimane?|mesi|anni|"
    r"ppm|ppb|bar|psi|pa|kpa|mpa|rpm|hz|khz|mhz|"
    r"v|kv|a|ma|w|kw|mw|j|kj|euro|euro/\w+)\b)",
    re.IGNORECASE,
)


def numeric_date_candidate_is_measurement(
    text: str,
    start: int,
    end: int,
) -> bool:
    """True se una forma numerica data-like appartiene a una misura tecnica."""
    value = str(text or "")
    prefix = value[max(0, start - 48):start]
    suffix = value[end:]
    return bool(
        _NUMERIC_MEASUREMENT_PREFIX_RE.search(prefix)
        or _NUMERIC_MEASUREMENT_SUFFIX_RE.match(suffix)
    )


def numeric_date_candidate_is_valid(expression: str) -> bool:
    """Valida la sola forma calendariale, usando il 2000 per date senza anno."""
    value = str(expression or "").strip()
    for pattern, order in (
        (r"(\d{4})-(\d{1,2})-(\d{1,2})", "ymd"),
        (r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", "dmy"),
        (r"(\d{1,2})-(\d{1,2})-(\d{2,4})", "dmy"),
    ):
        match = re.fullmatch(pattern, value)
        if not match:
            continue
        first, second, third = match.groups()
        if order == "ymd":
            year, month, day = int(first), int(second), int(third)
        else:
            day, month = int(first), int(second)
            year = int(third) if third else 2000
            if year < 100:
                year += 2000
        try:
            datetime(year, month, day)
        except ValueError:
            return False
        return True
    return False


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
        "Nei ricordi, il tempo dell'evento e il momento in cui e' stato affermato sono "
        "distinti: non presentare come attuale un intervallo il cui termine e' trascorso. "
        "Un turno di ore prima non e' 'poco fa'. Se il nuovo turno e' ellittico (per esempio "
        "un valore, 'quello' o 'il risultato') e segue un tema aperto nello stesso segmento, "
        "collegalo a quel tema e chiedi soltanto gli attributi davvero mancanti. Un numero "
        "senza unita', metodo o riferimento puo' essere collegato e ricordato, ma non basta "
        "per dichiararlo tecnicamente coerente, normale o anomalo."
    )


def temporal_prompt_contract_legacy_v1() -> str:
    """Contratto congelato per i replay firmati anteriori al 18/08/2026."""
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
    value = str(text or "")
    matches = [
        match.group(0)
        for match in _TEMPORAL_EXPRESSION_RE.finditer(value)
        if not (
            _NUMERIC_DATE_RE.fullmatch(match.group(0))
            and (
                not numeric_date_candidate_is_valid(match.group(0))
                or numeric_date_candidate_is_measurement(
                    value, match.start(), match.end()
                )
            )
        )
    ]
    if not matches:
        return ""
    # Il testo sorgente può contenere metadiscorso recente ("poco fa") e la
    # data dell'evento ("lunedì 3 agosto"). Vince l'espressione più precisa,
    # non quella incontrata per prima.
    rank = {"unspecified": 0, "relative_day": 1, "part_of_day": 2, "explicit_day": 3}
    return max(matches, key=lambda value: rank[_event_precision(value)])


def _event_precision(expression: str) -> str:
    low = (expression or "").lower()
    if "matt" in low or "staman" in low or "sera" in low or "notte" in low:
        return "part_of_day"
    if re.search(r"(?:\d{1,2}\s+[a-zà-öø-ÿ]+|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2})", low):
        return "explicit_day"
    if low:
        return "relative_day"
    return "unspecified"


def _calendar_candidate(
    reference: datetime,
    *,
    day: int,
    direction: str,
) -> datetime | None:
    """Trova il giorno di calendario valido più vicino nella direzione richiesta."""
    if day < 1 or day > 31:
        return None
    base = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    step = 1 if direction == "future" else -1
    year, month = base.year, base.month
    for _ in range(14):
        try:
            candidate = base.replace(year=year, month=month, day=day)
        except ValueError:
            candidate = None
        if candidate is not None:
            if direction == "future" and candidate.date() >= base.date():
                return candidate
            if direction == "past" and candidate.date() <= base.date():
                return candidate
        month += step
        if month == 13:
            month, year = 1, year + 1
        elif month == 0:
            month, year = 12, year - 1
    return None


def _resolve_relational_day(text: str, asserted_at: float) -> dict | None:
    """Risolvi una data con giorno ellittico soltanto se la relazione è esplicita."""
    reference = datetime.fromtimestamp(asserted_at, tz=config.TIMEZONE)
    for relation, pattern in _RELATIONAL_DAY_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue

        # Evita quantità come "fino a 10 giorni" o "entro 24 ore".
        tail = (text or "")[match.end():]
        if not match.group("month") and re.match(
            r"\s*(?:ore?|giorni?|settimane?|mesi?|anni?|%|kg|g|mm|cm|m\b)",
            tail,
            flags=re.IGNORECASE,
        ):
            continue

        day = int(match.group("day"))
        month_name = (match.group("month") or "").casefold()
        year_text = match.group("year")
        inferred = not bool(month_name) or not bool(year_text)
        if month_name:
            month = _MONTHS_IT.index(month_name) + 1
            year = int(year_text) if year_text else reference.year
            try:
                target = reference.replace(
                    year=year,
                    month=month,
                    day=day,
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                continue
        else:
            direction = "past" if relation == "since" else "future"
            target = _calendar_candidate(reference, day=day, direction=direction)
            if target is None:
                continue

        day_end = target + timedelta(days=1)
        if relation == "until":
            # Se il termine era già passato, non costruiamo un intervallo
            # invertito: senza una data iniziale esplicita conserviamo almeno
            # il giorno terminale come collocazione storica.
            event_start = (
                asserted_at if asserted_at < day_end.timestamp() else target.timestamp()
            )
            event_end = day_end.timestamp()
            canonical_prefix = "fino al "
        elif relation == "since":
            event_start, event_end = target.timestamp(), None
            canonical_prefix = "dal "
        else:
            event_start, event_end = target.timestamp(), day_end.timestamp()
            canonical_prefix = "entro il "
        canonical_expression = (
            f"{canonical_prefix}{target.day} {_MONTHS_IT[target.month - 1]} {target.year}"
        )
        return {
            "temporal_expression": match.group(0),
            "canonical_temporal_expression": canonical_expression,
            "temporal_relation": relation,
            "event_precision": "calendar_day_inferred" if inferred else "explicit_day",
            "event_start": event_start,
            "event_end": event_end,
            "event_target_start": target.timestamp(),
            "event_target_end": day_end.timestamp(),
            "resolved_from_asserted_at": inferred,
        }
    return None


def _relation_for_expression(text: str, expression: str) -> str:
    """Ricava la relazione grammaticale che introduce una data già completa."""
    if not expression:
        return "none"
    start = (text or "").casefold().find(expression.casefold())
    if start < 0:
        return "point"
    prefix = (text or "")[:start]
    if re.search(r"\b(?:fino|sino)\s+(?:a|al|alla)\s*$", prefix, re.IGNORECASE):
        return "until"
    if re.search(r"\bentro\s+(?:il\s+)?$", prefix, re.IGNORECASE):
        return "deadline"
    if re.search(
        r"\b(?:a\s+partire\s+)?(?:dal|dalla)\s*$",
        prefix,
        re.IGNORECASE,
    ):
        return "since"
    return "point"


def resolve_text_event_time(text: str, *, asserted_at: float) -> dict:
    """Resolve a temporal phrase in text against the moment it was asserted."""
    relational = _resolve_relational_day(text, asserted_at)
    if relational is not None:
        return relational
    expression = _temporal_expression(text)
    event_range = None
    if expression:
        reference_dt = datetime.fromtimestamp(asserted_at, tz=config.TIMEZONE)
        event_range = extract_temporal_range(expression, reference_dt)
    relation = _relation_for_expression(text, expression) if event_range else "none"
    event_start = event_range[0] if event_range else None
    event_end = event_range[1] if event_range else None
    if event_range and relation == "until":
        event_start = asserted_at if asserted_at < event_end else event_range[0]
    elif event_range and relation == "since":
        event_end = None
    canonical_expression = ""
    resolved_from_asserted_at = False
    if event_range and expression:
        event_dt = from_timestamp(event_range[0])
        if event_dt is not None and re.fullmatch(
            r"\d{1,2}/\d{1,2}", expression.strip()
        ):
            canonical_expression = event_dt.strftime("%d/%m/%Y")
            resolved_from_asserted_at = True
        elif event_dt is not None and re.fullmatch(
            rf"\d{{1,2}}\s+(?:{_MONTHS_IT_PATTERN})",
            expression.strip(),
            flags=re.IGNORECASE,
        ):
            canonical_expression = (
                f"{event_dt.day} {_MONTHS_IT[event_dt.month - 1]} {event_dt.year}"
            )
            resolved_from_asserted_at = True
    return {
        "temporal_expression": expression,
        "canonical_temporal_expression": canonical_expression,
        "temporal_relation": relation,
        "event_precision": _event_precision(expression),
        "event_start": event_start,
        "event_end": event_end,
        "event_target_start": event_range[0] if event_range else None,
        "event_target_end": event_range[1] if event_range else None,
        "resolved_from_asserted_at": resolved_from_asserted_at,
    }


def materialize_temporal_expression(text: str, resolved: dict) -> str:
    """Completa nel derivato una data ellittica già risolta e tracciata."""
    value = str(text or "")
    expression = str(resolved.get("temporal_expression") or "").strip()
    canonical = str(
        resolved.get("canonical_temporal_expression") or ""
    ).strip()
    if (
        not expression
        or not canonical
        or resolved.get("resolved_from_asserted_at") is not True
    ):
        return value
    start = value.casefold().find(expression.casefold())
    if start < 0:
        return value
    return value[:start] + canonical + value[start + len(expression):]


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
    canonical_content = materialize_temporal_expression(content, content_event_time)
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
    temporal = memory.get("temporal_context") or {}
    relation = str(temporal.get("temporal_relation") or "")
    target_start = _as_timestamp(temporal.get("event_target_start"))
    target_end = _as_timestamp(temporal.get("event_target_end"))
    if event_start is not None:
        start_dt = from_timestamp(event_start)
        end_dt = from_timestamp(event_end)
        target_start_dt = from_timestamp(target_start)
        target_end_dt = from_timestamp(target_end)
        precision = temporal.get("event_precision")
        if relation == "until" and target_end_dt is not None:
            inclusive = target_end_dt - timedelta(microseconds=1)
            state = "termine futuro" if ref < target_end else "termine trascorso"
            event = f"valido fino al {inclusive.strftime('%d/%m/%Y')} incluso; {state}"
        elif relation == "since" and target_start_dt is not None:
            event = f"valido dal {target_start_dt.strftime('%d/%m/%Y')}"
        elif relation == "deadline" and target_start_dt is not None:
            state = "termine futuro" if target_end is not None and ref < target_end else "termine trascorso"
            event = f"scadenza {target_start_dt.strftime('%d/%m/%Y')}; {state}"
        elif start_dt is not None and end_dt is not None and precision == "conversation_interval":
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
        if asserted_at:
            asserted_dt = from_timestamp(asserted_at)
            asserted_relative = qualitative_distance(asserted_at, ref)
            asserted_absolute = (
                asserted_dt.strftime("%d/%m/%Y %H:%M")
                if asserted_dt is not None else "tempo non registrato"
            )
            return (
                f"evento: {event}; riferito {asserted_relative}; "
                f"affermato il {asserted_absolute}"
            )
        return f"evento: {event}"

    anchor = asserted_at or created_at
    if anchor is None:
        return "tempo non registrato"
    anchor_dt = from_timestamp(anchor)
    if anchor_dt is None:
        return qualitative_distance(anchor, ref)
    source = "affermato" if asserted_at is not None else "salvato"
    return (
        f"{source} il {anchor_dt.strftime('%d/%m/%Y %H:%M')}; "
        f"{qualitative_distance(anchor, ref)}"
    )


def memory_time_label_legacy_v1(memory: dict, *, reference_at: Any = None) -> str:
    """Renderer congelato per replay creati prima del contratto temporale v2."""
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
