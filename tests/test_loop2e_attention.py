#!/usr/bin/env python3
"""
Contro-casi dell'indice derivato Loop 2e.

Lo ZSET non deve diventare ground truth: una memoria entra solo se supera il gate
canonico; appena diventa sospetta/spesa/superseded deve uscire.
"""
import time

import config
from core.memory_attention import (
    LOOP2E_ZSET,
    is_loop2e_candidate,
    loop2e_attention_score,
    remove_loop2e_candidate,
    supported_use_attention_bonus,
    update_loop2e_candidate_index,
)


class _FakeRedis:
    def __init__(self):
        self.zsets = {}

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, member):
        self.zsets.setdefault(key, {}).pop(member, None)


def _doc(**overrides):
    base = {
        "id": "mem-1",
        "source": "passive",
        "requires_verification": False,
        "recalled_count": 3,
        "last_recalled_at": time.time(),
        "created_at": time.time(),
        "embedding": [0.1, 0.2],
        "memory_axes": {"subject_status": "explicit"},
    }
    base.update(overrides)
    return base


def test_candidate_enters_index():
    r = _FakeRedis()
    doc = _doc()
    assert is_loop2e_candidate(doc)

    update_loop2e_candidate_index(r, doc)

    assert "mem-1" in r.zsets[LOOP2E_ZSET]


def test_requires_verification_exits_index():
    r = _FakeRedis()
    doc = _doc()
    update_loop2e_candidate_index(r, doc)

    doc["requires_verification"] = True
    update_loop2e_candidate_index(r, doc)

    assert "mem-1" not in r.zsets[LOOP2E_ZSET]


def test_correction_pending_exits_index():
    r = _FakeRedis()
    doc = _doc()
    update_loop2e_candidate_index(r, doc)

    doc["correction_pending"] = True
    update_loop2e_candidate_index(r, doc)

    assert "mem-1" not in r.zsets[LOOP2E_ZSET]


def test_consolidated_or_superseded_exits_index():
    r = _FakeRedis()
    doc = _doc()
    update_loop2e_candidate_index(r, doc)

    doc["consolidated_into"] = "new-node"
    update_loop2e_candidate_index(r, doc)
    assert "mem-1" not in r.zsets[LOOP2E_ZSET]

    doc = _doc()
    update_loop2e_candidate_index(r, doc)
    doc["superseded_by"] = "winner"
    update_loop2e_candidate_index(r, doc)
    assert "mem-1" not in r.zsets[LOOP2E_ZSET]


def test_acephalous_or_untouched_does_not_enter():
    r = _FakeRedis()

    update_loop2e_candidate_index(r, _doc(memory_axes={"subject_status": "acephalous"}))
    update_loop2e_candidate_index(r, _doc(id="mem-2", recalled_count=2))
    update_loop2e_candidate_index(r, _doc(id="mem-3", last_recalled_at=None))

    assert r.zsets.get(LOOP2E_ZSET, {}) == {}


def test_conversation_context_never_becomes_consolidation_evidence():
    for kind in ("conversation_anchor", "conversation_episode"):
        r = _FakeRedis()
        update_loop2e_candidate_index(r, _doc(memory_kind=kind))
        assert r.zsets.get(LOOP2E_ZSET, {}) == {}


def test_explicit_remove_is_idempotent():
    r = _FakeRedis()
    update_loop2e_candidate_index(r, _doc())

    remove_loop2e_candidate(r, "euri:memory:mem-1")
    remove_loop2e_candidate(r, "mem-1")

    assert "mem-1" not in r.zsets[LOOP2E_ZSET]


def test_supported_use_is_bounded_attention_not_eligibility_or_truth():
    unused = _doc(id="unused", recalled_count=5, supported_use_count=0)
    useful = _doc(
        id="useful",
        recalled_count=5,
        supported_use_count=2,
        supported_use_observed_recalled_count=3,
    )
    assert loop2e_attention_score(useful) > loop2e_attention_score(unused)

    # L'uso non apre il gate se mancano i tre richiami canonici.
    assert not is_loop2e_candidate(
        _doc(id="not-eligible", recalled_count=2, supported_use_count=99)
    )
    # A parita' di usi, una memoria selettiva riceve piu' attenzione di una
    # memoria quasi sempre esposta ma raramente riconoscibile nella risposta.
    selective = _doc(
        supported_use_count=8,
        supported_use_observed_recalled_count=8,
    )
    overexposed = _doc(
        supported_use_count=8,
        supported_use_observed_recalled_count=80,
    )
    assert supported_use_attention_bonus(selective) > supported_use_attention_bonus(
        overexposed
    )

    # Nessun denominatore osservato: durante la migrazione il comportamento e'
    # fail-safe e non inventa un rapporto favorevole.
    assert supported_use_attention_bonus(
        _doc(supported_use_count=5)
    ) == 0.0

    # Anche con uso perfettamente selettivo il rinforzo resta sotto il vecchio
    # massimo cap*peso a causa del prior conservativo.
    assert 0 < supported_use_attention_bonus(selective) < 10


def test_absolute_count_policy_remains_an_explicit_rollback():
    old_policy = config.MEMORY_ATTENTION_POLICY
    config.MEMORY_ATTENTION_POLICY = "absolute_count_v1"
    try:
        assert supported_use_attention_bonus(
            _doc(supported_use_count=500)
        ) == 10.0
    finally:
        config.MEMORY_ATTENTION_POLICY = old_policy


if __name__ == "__main__":
    test_candidate_enters_index()
    test_requires_verification_exits_index()
    test_correction_pending_exits_index()
    test_consolidated_or_superseded_exits_index()
    test_acephalous_or_untouched_does_not_enter()
    test_conversation_context_never_becomes_consolidation_evidence()
    test_explicit_remove_is_idempotent()
    test_supported_use_is_bounded_attention_not_eligibility_or_truth()
    test_absolute_count_policy_remains_an_explicit_rollback()
    print("test_loop2e_attention: OK")
