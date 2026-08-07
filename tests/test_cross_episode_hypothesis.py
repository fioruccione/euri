#!/usr/bin/env python3
"""Test puri per Loop 2i: ipotesi trasversali da episodi ripetuti."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.dream_engine import (
    DreamEngine,
    _case_has_causal_hint,
    _counts_as_cross_episode_evidence,
    _ensure_hypothesis_wording,
    _parse_cross_episode_response,
)


def test_causal_hint_is_form_not_domain():
    assert _case_has_causal_hint("La rugosità dipende dal nastro troppo adesivizzato.")
    assert _case_has_causal_hint("Il problema è rientrato cambiando materiale in ingresso.")
    assert not _case_has_causal_hint("Il pallet pesa 10,40 kg ed è alto 140 mm.")


def test_parse_json_with_text_around():
    parsed = _parse_cross_episode_response(
        '```json\n{"should_create": true, "case_numbers": [1, 2], "hypothesis": "x"}\n```'
    )
    assert parsed["should_create"] is True
    assert parsed["case_numbers"] == [1, 2]


def test_hypothesis_wording_never_becomes_fact():
    assert _ensure_hypothesis_wording("Il nastro colloso causa rugosità.").startswith("Ipotesi da verificare:")
    assert _ensure_hypothesis_wording("Potrebbe essere una variabile da controllare.").startswith("Potrebbe")


def test_derived_lessons_do_not_count_as_independent_cases():
    direct = {
        "source": "user",
        "tags": [],
        "content": "La sporcizia negli ugelli causa valori MFI bassi in produzione.",
    }
    derived = {
        "source": "passive",
        "tags": ["lesson", "from_correction"],
        "content": "Ho imparato dalla correzione che la sporcizia negli ugelli altera l'MFI.",
    }
    reflection = {
        "source": "reflection",
        "tags": [],
        "content": "Reflection sullo stesso tema.",
    }

    assert _counts_as_cross_episode_evidence(direct)
    assert not _counts_as_cross_episode_evidence(derived)
    assert not _counts_as_cross_episode_evidence(reflection)


def test_format_cross_episode_insight_is_operational_and_cautious():
    engine = DreamEngine(r=None, embedder=None)
    data = {
        "cause_pattern": "materiale in ingresso con contaminante adesivo",
        "effect_pattern": "rugosità estetica",
        "context": "prodotti rigenerati economici",
        "hypothesis": "questa variabile può essere controllata anche su altri prodotti simili",
    }
    cases = [
        {"id": "m1", "domain": "produzione"},
        {"id": "m2", "domain": "controllo qualità"},
    ]

    content = engine._format_cross_episode_insight(data, cases)

    assert "Nel dominio [produzione] succede:" in content
    assert "Nel dominio [controllo qualità] succede:" in content
    assert "La connessione operativa non ovvia è:" in content
    assert "può essere controllata" in content
    assert "causa certamente" not in content.lower()


if __name__ == "__main__":
    test_causal_hint_is_form_not_domain()
    test_parse_json_with_text_around()
    test_hypothesis_wording_never_becomes_fact()
    test_derived_lessons_do_not_count_as_independent_cases()
    test_format_cross_episode_insight_is_operational_and_cautious()
    print("test_cross_episode_hypothesis: OK")
