"""
Assi strutturali leggeri per le memorie.

Non decidono la verità del contenuto. Annotano proprietà utili a retrieval, audit e
consolidamento: soggetto esplicito/acefalo, entità nominate, temporalità grezza e tipo
probabile del fatto. Tutto domain-agnostic: regole linguistiche e forma del testo.
"""
from __future__ import annotations

import re
from typing import Any

from core.temporal_context import (
    numeric_date_candidate_is_measurement,
    numeric_date_candidate_is_valid,
)


SCHEMA_VERSION = 1

_HEADER_RE = re.compile(r"^\s*#\s*Memoria\s*\([^)]*\)\s*", re.IGNORECASE)

_ACEPHALOUS_RE = re.compile(
    r"^\s*(?:ha|aveva|avrà|lavora|lavorava|opera|gestisce|gestiva|collabora|"
    r"collaborava|si\s+occupa|supervisiona|sta\s+\w+|deve|vuole|preferisce|"
    r"è\s+(?:arrivat[oa]|coinvolt[oa]|responsabile|nuov[oa]|autonom[oa]))\b",
    re.IGNORECASE,
)

_PROPER_ENTITY_RE = re.compile(
    r"\b(?:[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+){0,3}|"
    r"[A-Z]{2,}[A-Z0-9-]*|[A-Z0-9]{2,}\d[A-Z0-9-]*)\b"
)

_ENTITY_STOP = {
    "Il", "La", "Lo", "Le", "Gli", "Un", "Una", "Uno", "Nel", "Nella", "Nello",
    "Nei", "Negli", "Dopo", "Prima", "Oggi", "Ieri", "Domani", "Memoria",
    "Ha", "Aveva", "Avrà", "Lavora", "Lavorava", "Opera", "Gestisce", "Gestiva",
    "Collabora", "Collaborava", "Si", "Sta", "Deve", "Vuole", "Preferisce",
    "Supervisiona",
}

_DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|"
    r"\d{1,2}-\d{1,2}-\d{2,4})\b|"
    r"\b\d{4}-\d{2}-\d{2}\b|"
    r"\b(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\b",
    re.IGNORECASE,
)
_MONTH_NAMES = {
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
}


def _has_date_marker(text: str) -> bool:
    for match in _DATE_RE.finditer(text):
        if match.group(0).casefold() in _MONTH_NAMES:
            return True
        if (
            numeric_date_candidate_is_valid(match.group(0))
            and not numeric_date_candidate_is_measurement(
                text, match.start(), match.end()
            )
        ):
            return True
    return False


_PENDING_RE = re.compile(
    r"\b(?:da\s+validare|in\s+attesa|attende|aspetta|pending|da\s+fare|"
    r"deve\s+essere|dovrà|entro|scade|scadenza)\b",
    re.IGNORECASE,
)

_HISTORICAL_RE = re.compile(r"\b(?:in\s+passato|prima|all['’]inizio|era|aveva|precedentemente)\b", re.IGNORECASE)
_CURRENT_RE = re.compile(
    r"\b(?:ora|adesso|attualmente|oggi|sta|ancora|già|questa\s+mattina|"
    r"stamattina|stamani|stamane|questa\s+sera|stasera|stanotte)\b",
    re.IGNORECASE,
)
_RELATIVE_TIME_RE = re.compile(
    r"\b(?:oggi|ieri|domani|questa\s+mattina|stamattina|stamani|stamane|"
    r"questa\s+sera|stasera|stanotte|poco\s+fa|\d+\s+(?:giorni|ore)\s+fa|"
    r"settimana\s+scorsa)\b",
    re.IGNORECASE,
)

_TECH_RE = re.compile(
    r"\b\d+[.,]?\d*\s*(?:%|g|kg|ml|l|mg|ppm|bar|°[Cc]|rpm|mm|cm|m\b|MPa|KJ/m)\b|"
    r"\b(?:MFI|MFR|modulo|carico|scheda\s+tecnica|test|prova|campion[ei]|codice|materiale)\b",
    re.IGNORECASE,
)
_PERSON_ROLE_RE = re.compile(
    r"\b(?:collega|collaborator\w*|responsabil\w*|team|ruol\w*|si\s+occupa|"
    r"supervisiona|gestisc\w*|lavora|laboratorio|reparto)\b",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(r"\b(?:progetto|piano|roadmap|implementazione|validazione|rilascio)\b", re.IGNORECASE)
_COMMITMENT_RE = re.compile(r"\b(?:deve|dovrà|vuole|intende|ha\s+deciso|farà|porterà|aspetta)\b", re.IGNORECASE)


def _clean(content: str) -> str:
    return " ".join(_HEADER_RE.sub("", content or "").split())


def _entities(text: str) -> list[str]:
    out: list[str] = []
    for m in _PROPER_ENTITY_RE.finditer(text):
        ent = " ".join(m.group(0).split())
        if ent in _ENTITY_STOP or len(ent) < 2:
            continue
        out.append(ent)
    return list(dict.fromkeys(out))[:12]


def _subject_status(text: str, entities: list[str]) -> str:
    if not text:
        return "unknown"
    if _ACEPHALOUS_RE.search(text):
        return "acephalous"
    if entities:
        return "explicit"
    return "unknown"


def analyze_memory_axes(content: str, *, source: str | None = None, created_at: Any = None) -> dict:
    text = _clean(content)
    entities = _entities(text)
    subject_status = _subject_status(text, entities)

    audit_reasons: list[str] = []
    if subject_status == "acephalous":
        audit_reasons.append("acephalous_subject")

    temporal_markers: list[str] = []
    if _has_date_marker(text):
        temporal_markers.append("dated")
    if _PENDING_RE.search(text):
        temporal_markers.append("pending")
    if _HISTORICAL_RE.search(text):
        temporal_markers.append("historical")
    if _CURRENT_RE.search(text):
        temporal_markers.append("current_marker")
    if _RELATIVE_TIME_RE.search(text):
        temporal_markers.append("relative_reference")

    fact_types: list[str] = []
    if _PERSON_ROLE_RE.search(text):
        fact_types.append("person_or_role")
    if _PROJECT_RE.search(text):
        fact_types.append("project")
    if _TECH_RE.search(text):
        fact_types.append("technical")
    if _COMMITMENT_RE.search(text):
        fact_types.append("commitment_or_pending_action")
    if not fact_types:
        fact_types.append("general")

    return {
        "schema_version": SCHEMA_VERSION,
        "subject_status": subject_status,
        "entity_mentions": entities,
        "fact_types": fact_types,
        "temporal_markers": temporal_markers,
        "audit_reasons": audit_reasons,
        "source": source or "",
        "observed_at": created_at,
    }
