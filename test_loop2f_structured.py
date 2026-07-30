#!/usr/bin/env python3
"""Regressioni pure della policy structured e fail-closed del Loop 2f."""

from types import SimpleNamespace

from core.dream_engine import DreamEngine
from core.loop2f_policy import (
    POLICY_VERSION,
    audit_basis,
    normalize_assessment,
    relation_from_assessment,
)


def _assessment(**overrides):
    payload = {
        "entity_relation": "same",
        "claim_relation": "same",
        "assertion_kind_a": "current_state",
        "assertion_kind_b": "current_state",
        "mutually_exclusive": "yes",
        "explicit_replacement": "yes",
        "useful_comparison": "no",
    }
    payload.update(overrides)
    return payload


class _FakeEngine(DreamEngine):
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def _ollama_chat(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.raw, Exception):
            raise self.raw
        return SimpleNamespace(message=SimpleNamespace(content=self.raw))


def test_affirmative_evidence_is_required_for_supersession():
    assert relation_from_assessment(_assessment()) == "contradiction"
    for field in (
        "entity_relation",
        "claim_relation",
        "mutually_exclusive",
        "explicit_replacement",
    ):
        value = "unknown"
        assert relation_from_assessment(_assessment(**{field: value})) == "none"


def test_different_assertion_kinds_cannot_supersede():
    target_vs_measurement = _assessment(
        assertion_kind_a="target",
        assertion_kind_b="measurement",
    )
    assert relation_from_assessment(target_vs_measurement) == "none"
    assert relation_from_assessment(
        _assessment(assertion_kind_a="unknown", assertion_kind_b="unknown")
    ) == "none"


def test_comparison_requires_distinct_entities_and_affirmative_utility():
    assert relation_from_assessment(
        _assessment(
            entity_relation="different",
            claim_relation="same",
            explicit_replacement="no",
            useful_comparison="yes",
        )
    ) == "comparison"
    assert relation_from_assessment(
        _assessment(
            entity_relation="unknown",
            explicit_replacement="no",
            useful_comparison="yes",
        )
    ) == "none"


def test_invalid_contract_is_not_coerced():
    payload = _assessment()
    del payload["explicit_replacement"]
    assert normalize_assessment(payload) is None
    assert relation_from_assessment(payload) == "none"
    assert normalize_assessment(
        _assessment(mutually_exclusive=True)
    ) is None


def test_audit_basis_contains_only_enumerative_contract():
    basis = audit_basis(_assessment())
    assert basis == {"policy_version": POLICY_VERSION, **_assessment()}
    assert "reason" not in basis


def test_structured_classifier_uses_json_and_fails_closed():
    valid = _FakeEngine(
        """{"entity_relation":"same","claim_relation":"same",
        "assertion_kind_a":"current_state",
        "assertion_kind_b":"current_state",
        "mutually_exclusive":"yes","explicit_replacement":"yes",
        "useful_comparison":"no"}"""
    )
    result = valid._llm_assess_pair("prima", "dopo")
    assert result["relation"] == "contradiction"
    assert result["contract_ok"] is True
    assert valid.calls[0]["format"] == "json"
    assert valid.calls[0]["think"] is False

    malformed = _FakeEngine("CONTRADIZIONE")
    result = malformed._llm_assess_pair("prima", "dopo")
    assert result["relation"] == "none"
    assert result["contract_ok"] is False
    assert result["assessment"] is None

    failed = _FakeEngine(RuntimeError("modello non disponibile"))
    result = failed._llm_assess_pair("prima", "dopo")
    assert result["relation"] == "none"
    assert result["contract_ok"] is False


def test_legacy_parser_remains_available_for_paired_baseline():
    legacy_typo = _FakeEngine("CONTRADIZIONE")
    assert legacy_typo._llm_classify_pair_legacy("prima", "dopo") == "none"

    legacy_exact = _FakeEngine("CONTRADDIZIONE")
    assert legacy_exact._llm_classify_pair_legacy("prima", "dopo") == "contradiction"


if __name__ == "__main__":
    test_affirmative_evidence_is_required_for_supersession()
    test_different_assertion_kinds_cannot_supersede()
    test_comparison_requires_distinct_entities_and_affirmative_utility()
    test_invalid_contract_is_not_coerced()
    test_audit_basis_contains_only_enumerative_contract()
    test_structured_classifier_uses_json_and_fails_closed()
    test_legacy_parser_remains_available_for_paired_baseline()
    print("test_loop2f_structured: 7/7 OK")
