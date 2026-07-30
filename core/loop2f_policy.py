"""Policy pura e fail-closed per le decisioni distruttive del Loop 2f.

Il modello descrive assi semantici separati. Questa policy decide; non completa
campi mancanti e non converte una confidenza vaga in autorizzazione.
"""

from __future__ import annotations

from typing import Any


POLICY_VERSION = "loop2f-structured-affirmative-v2"

TRINARY = frozenset({"yes", "no", "unknown"})
ENTITY_RELATIONS = frozenset({"same", "different", "unknown"})
CLAIM_RELATIONS = frozenset({"same", "different", "unknown"})
ASSERTION_KINDS = frozenset(
    {
        "current_state",
        "measurement",
        "target",
        "requirement",
        "prediction",
        "preference",
        "other",
        "unknown",
    }
)
SUPERSEDABLE_KINDS = ASSERTION_KINDS - {"other", "unknown"}

REQUIRED_FIELDS = frozenset(
    {
        "entity_relation",
        "claim_relation",
        "assertion_kind_a",
        "assertion_kind_b",
        "mutually_exclusive",
        "explicit_replacement",
        "useful_comparison",
    }
)


def normalize_assessment(payload: Any) -> dict[str, str] | None:
    """Valida il contratto senza coercizioni semantiche."""

    if not isinstance(payload, dict) or not REQUIRED_FIELDS.issubset(payload):
        return None
    normalized = {
        field: str(payload.get(field) or "").strip().lower()
        for field in REQUIRED_FIELDS
    }
    if normalized["entity_relation"] not in ENTITY_RELATIONS:
        return None
    if normalized["claim_relation"] not in CLAIM_RELATIONS:
        return None
    if normalized["assertion_kind_a"] not in ASSERTION_KINDS:
        return None
    if normalized["assertion_kind_b"] not in ASSERTION_KINDS:
        return None
    for field in ("mutually_exclusive", "explicit_replacement", "useful_comparison"):
        if normalized[field] not in TRINARY:
            return None
    return normalized


def relation_from_assessment(payload: Any) -> str:
    """Restituisce contradiction/comparison/none con autorità asimmetrica."""

    assessment = normalize_assessment(payload)
    if assessment is None:
        return "none"

    kind_a = assessment["assertion_kind_a"]
    kind_b = assessment["assertion_kind_b"]
    affirmative_supersession = (
        assessment["entity_relation"] == "same"
        and assessment["claim_relation"] == "same"
        and kind_a == kind_b
        and kind_a in SUPERSEDABLE_KINDS
        and assessment["mutually_exclusive"] == "yes"
        and assessment["explicit_replacement"] == "yes"
    )
    if affirmative_supersession:
        return "contradiction"

    if (
        assessment["entity_relation"] == "different"
        and assessment["useful_comparison"] == "yes"
    ):
        return "comparison"
    return "none"


def audit_basis(payload: Any) -> dict[str, str] | None:
    """Metadati enumerativi persistibili, senza testo o ragionamento del modello."""

    assessment = normalize_assessment(payload)
    if assessment is None:
        return None
    return {"policy_version": POLICY_VERSION, **assessment}
