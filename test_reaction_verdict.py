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


if __name__ == "__main__":
    test_da_valutare_is_parsed()
    test_classifier_fail_open_is_hypothesis_not_confirmation()
    test_da_valutare_marks_insight_requires_verification()
    test_smentita_still_demotes()
    test_smentita_sets_demoted_once()
    print("test_reaction_verdict: OK")
