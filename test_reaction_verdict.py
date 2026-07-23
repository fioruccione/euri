#!/usr/bin/env python3
"""Regression sul verdetto epistemico delle reaction agli insight."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import core.reaction as reaction


class _Msg:
    def __init__(self, content):
        self.content = content


class _Resp:
    def __init__(self, content):
        self.message = _Msg(content)


class _FakeClient:
    def __init__(self, content=None, fail=False):
        self.content = content
        self.fail = fail

    def chat(self, **_kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return _Resp(self.content)


class _FakeJSON:
    def __init__(self):
        self.sets = []

    def set(self, key, path, value):
        self.sets.append((key, path, value))


class _FakeRedis:
    def __init__(self):
        self.j = _FakeJSON()

    def json(self):
        return self.j


class _FakeMemory:
    def __init__(self):
        self.r = _FakeRedis()
        self.failures = []

    def _record_integrity_failure(self, *args):
        self.failures.append(args)


class _CaptureMemory(_FakeMemory):
    def __init__(self):
        super().__init__()
        self.saved_content = ""
        self.saved_kwargs = {}

    def save_memory(self, *_args, **_kwargs):
        if _args:
            self.saved_content = _args[0]
        self.saved_kwargs = dict(_kwargs)
        return "lesson-1"


def _classify_with(output=None, fail=False):
    old = reaction.chat_client
    reaction.chat_client = _FakeClient(output, fail=fail)
    try:
        return reaction.classify_reaction_verdict(
            {"domain_a": "a", "domain_b": "b", "content": "Nel dominio a succede: x"},
            "È una cosa interessante da valutare, non confermata.",
        )
    finally:
        reaction.chat_client = old


def test_da_valutare_is_parsed():
    assert _classify_with("DA_VALUTARE") == "DA_VALUTARE"


def test_classifier_fail_open_is_hypothesis_not_confirmation():
    assert _classify_with(fail=True) == "DA_VALUTARE"


def test_da_valutare_marks_insight_requires_verification():
    memory = _FakeMemory()
    reaction._apply_reaction_verdict(memory, "abc", "DA_VALUTARE")
    assert ("euri:insight:abc", "$.requires_verification", True) in memory.r.j.sets
    assert ("euri:insight:abc", "$.verification_status", "hypothesis_to_test") in memory.r.j.sets


def test_partial_marks_original_insight_as_partially_refuted():
    memory = _FakeMemory()
    reaction._apply_reaction_verdict(memory, "abc", "PARZIALE")
    sets = memory.r.j.sets
    assert ("euri:insight:abc", "$.requires_verification", True) in sets
    assert (
        "euri:insight:abc", "$.verification_status", "partially_refuted_by_user"
    ) in sets
    assert any(
        key == "euri:insight:abc" and path == "$.partially_refuted_by_user_at"
        for key, path, _value in sets
    )


def test_smentita_still_demotes():
    memory = _FakeMemory()
    reaction._apply_reaction_verdict(memory, "abc", "SMENTITA")
    assert ("euri:insight:abc", "$.status", "candidate") in memory.r.j.sets


def test_smentita_sets_demoted_once():
    """La smentita dell'utente è una demozione più forte di quella anagrafica:
    senza demoted_once il gate di ri-promozione non la vede e la sola
    ri-convergenza può resuscitare un insight bocciato (caso pallet/CO2 03/07)."""
    memory = _FakeMemory()
    reaction._apply_reaction_verdict(memory, "abc", "SMENTITA")
    assert ("euri:insight:abc", "$.demoted_once", True) in memory.r.j.sets
    assert any(k == "euri:insight:abc" and p == "$.refuted_by_user_at"
               for k, p, _v in memory.r.j.sets)


def test_capture_propagates_uncertainty_to_reaction_memory():
    memory = _CaptureMemory()
    old_classify = reaction.classify_reaction_verdict
    old_synthesize = reaction.synthesize_lesson
    old_pulse = reaction.pulse_emit
    seen = {}
    reaction.classify_reaction_verdict = lambda *_a, **_k: "DA_VALUTARE"
    def _synthesize(_insight, _reply, verdict):
        seen["verdict"] = verdict
        return "lezione"
    reaction.synthesize_lesson = _synthesize
    reaction.pulse_emit = lambda *_a, **_k: None
    try:
        out = reaction.capture_reaction(
            memory,
            {"id": "abc", "domain_a": "a", "domain_b": "b", "content": "insight"},
            "Potrebbe essere, ma non è legato a questo evento.",
        )
    finally:
        reaction.classify_reaction_verdict = old_classify
        reaction.synthesize_lesson = old_synthesize
        reaction.pulse_emit = old_pulse

    assert seen["verdict"] == "DA_VALUTARE"
    assert out["verdict"] == "DA_VALUTARE"
    fields = memory.saved_kwargs["final_fields"]
    assert fields["reaction_verdict"] == "DA_VALUTARE"
    assert fields["requires_verification"] is True


def test_partial_patch_requires_literal_evidence_from_reaction():
    old = reaction.chat_client
    reply = (
        '{"confirmed_claims":[{"claim":"Il principio generale regge",'
        '"evidence":"come discorso regge"}],'
        '"refuted_claims":[{"claim":"Giuseppe controlla il materiale",'
        '"evidence":"non è Giuseppe che fa questo lavoro"}],'
        '"replacement_claims":['
        '{"claim":"Il laboratorio valuta il materiale",'
        '"evidence":"ci pensa il laboratorio"},'
        '{"claim":"Il back office dirige il laboratorio",'
        '"evidence":"frase mai pronunciata"}]}'
    )
    reaction.chat_client = _FakeClient(reply)
    try:
        patch = reaction.extract_partial_reaction_patch(
            {"content": "insight"},
            "Regge, come discorso regge, però non è Giuseppe che fa questo lavoro; "
            "a vedere se va bene il materiale ci pensa il laboratorio.",
        )
    finally:
        reaction.chat_client = old
    assert [x["claim"] for x in patch["confirmed_claims"]] == [
        "Il principio generale regge"
    ]
    assert [x["claim"] for x in patch["refuted_claims"]] == [
        "Giuseppe controlla il materiale"
    ]
    assert [x["claim"] for x in patch["replacement_claims"]] == [
        "Il laboratorio valuta il materiale"
    ]


def test_capture_partial_uses_extractively_grounded_patch():
    memory = _CaptureMemory()
    patch = {
        "confirmed_claims": [{"claim": "Il principio regge", "evidence": "regge"}],
        "refuted_claims": [{"claim": "Giuseppe valuta il materiale", "evidence": "non Giuseppe"}],
        "replacement_claims": [{"claim": "Il laboratorio valuta il materiale", "evidence": "laboratorio"}],
    }
    old_classify = reaction.classify_reaction_verdict
    old_extract = reaction.extract_partial_reaction_patch
    old_pulse = reaction.pulse_emit
    reaction.classify_reaction_verdict = lambda *_a, **_k: "PARZIALE"
    reaction.extract_partial_reaction_patch = lambda *_a, **_k: patch
    reaction.pulse_emit = lambda *_a, **_k: None
    try:
        out = reaction.capture_reaction(
            memory,
            {"id": "abc", "domain_a": "a", "domain_b": "b", "content": "insight"},
            "Il principio regge ma non è Giuseppe; ci pensa il laboratorio.",
        )
    finally:
        reaction.classify_reaction_verdict = old_classify
        reaction.extract_partial_reaction_patch = old_extract
        reaction.pulse_emit = old_pulse

    assert out["verdict"] == "PARZIALE"
    assert out["reaction_patch"] == patch
    assert "Ha confermato: Il principio regge" in memory.saved_content
    assert "Ha smentito: Giuseppe valuta il materiale" in memory.saved_content
    assert "Il back office intercetta anomalie" not in memory.saved_content
    assert memory.saved_kwargs["final_fields"]["reaction_patch"] == patch
    sets = memory.r.j.sets
    assert ("euri:insight:abc", "$.reaction_patch", patch) in sets


def test_capture_refutation_is_extractive_and_does_not_preserve_insight_claims():
    memory = _CaptureMemory()
    user_correction = (
        "È una forzatura: 03ppr102 è il non conforme dei vari impianti "
        "e viene poi riestruso in percentuali più piccole."
    )
    old_classify = reaction.classify_reaction_verdict
    old_synthesize = reaction.synthesize_lesson
    old_pulse = reaction.pulse_emit
    reaction.classify_reaction_verdict = lambda *_a, **_k: "SMENTITA"

    def _must_not_synthesize(*_args, **_kwargs):
        raise AssertionError("una SMENTITA non deve passare dalla sintesi creativa")

    reaction.synthesize_lesson = _must_not_synthesize
    reaction.pulse_emit = lambda *_a, **_k: None
    try:
        out = reaction.capture_reaction(
            memory,
            {
                "id": "abc",
                "domain_a": "riciclo materiali",
                "domain_b": "misurazione temperatura",
                "content": (
                    "Ogni variazione di 03ppr102 richiede un aggiustamento "
                    "preventivo del setpoint."
                ),
            },
            user_correction,
        )
    finally:
        reaction.classify_reaction_verdict = old_classify
        reaction.synthesize_lesson = old_synthesize
        reaction.pulse_emit = old_pulse

    assert out["verdict"] == "SMENTITA"
    assert user_correction in out["lesson"]
    assert "Nessuna parte dell'insight precedente resta confermata" in out["lesson"]
    assert "aggiustamento preventivo" not in out["lesson"]
    assert "setpoint rimane" not in out["lesson"]
    assert "resta valida" not in out["lesson"]
    fields = memory.saved_kwargs["final_fields"]
    assert fields["reaction_lesson_mode"] == "extractive_refutation"
    assert fields["verification_status"] == "refutation_grounded_by_user"
    assert fields["requires_verification"] is False


def test_confirmation_is_the_external_epistemic_transition():
    memory = _FakeMemory()
    reaction._apply_reaction_verdict(memory, "abc", "CONFERMA")
    sets = memory.r.j.sets
    assert ("euri:insight:abc", "$.requires_verification", False) in sets
    assert (
        "euri:insight:abc",
        "$.verification_status",
        "externally_confirmed_by_owner",
    ) in sets
    assert (
        "euri:insight:abc",
        "$.epistemic_status",
        "externally_confirmed",
    ) in sets


if __name__ == "__main__":
    test_da_valutare_is_parsed()
    test_classifier_fail_open_is_hypothesis_not_confirmation()
    test_da_valutare_marks_insight_requires_verification()
    test_partial_marks_original_insight_as_partially_refuted()
    test_smentita_still_demotes()
    test_smentita_sets_demoted_once()
    test_capture_propagates_uncertainty_to_reaction_memory()
    test_partial_patch_requires_literal_evidence_from_reaction()
    test_capture_partial_uses_extractively_grounded_patch()
    test_capture_refutation_is_extractive_and_does_not_preserve_insight_claims()
    test_confirmation_is_the_external_epistemic_transition()
    print("test_reaction_verdict: OK")
