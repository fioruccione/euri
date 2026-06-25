#!/usr/bin/env python3
"""Regression per il merge SAVE esplicito su basi epistemicamente deboli."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.save_service import _save_or_merge


class FakeMemory:
    def __init__(self, match):
        self.match = match
        self.saved = []
        self.superseded = []

    def find_similar_memory(self, _content):
        return self.match

    def save_memory(self, content, source="user", idempotent=True, **_kwargs):
        self.saved.append((content, source, idempotent))
        return "new-id"

    def supersede_memory(self, old_id, new_id):
        self.superseded.append((old_id, new_id))
        return True


class FakeBrain:
    def __init__(self, merged=None):
        self.merged = merged
        self.merge_calls = []

    def merge_memories(self, existing, new):
        self.merge_calls.append((existing, new))
        if self.merged is None:
            raise AssertionError("merge_memories non doveva essere chiamato")
        return self.merged

    def confirm_save(self, _item_type, content, _due_at_str=""):
        return f"Segnato: {content}"


def test_explicit_save_does_not_merge_into_weak_passive_memory():
    match = {
        "id": "old-passive",
        "content": "La rugosità è causata da un nastro troppo adesivo e impostazioni macchina non corrette.",
        "similarity": 0.91,
        "source": "passive",
        "passive_support": "tacit_acceptance",
        "requires_verification": True,
    }
    memory = FakeMemory(match)
    brain = FakeBrain()
    content = "L'eccessiva adesività dei nastri può causare rugosità sui prodotti a prescindere dall'impianto usato."

    res = _save_or_merge(content, memory, brain)

    assert res["saved"] is True
    assert res["merged"] is False
    assert res["content"] == content
    assert brain.merge_calls == []
    assert memory.saved == [(content, "user", True)]
    assert memory.superseded == [("old-passive", "new-id")]


def test_explicit_save_still_merges_into_trusted_user_memory():
    match = {
        "id": "old-user",
        "content": "Il Progetto Poseidon riguarda un pallet aperto per sacconi.",
        "similarity": 0.88,
        "source": "user",
        "consolidation_risk": {"level": "ok"},
    }
    merged = "Il Progetto Poseidon riguarda un pallet aperto per sacconi con carichi ancora da validare."
    memory = FakeMemory(match)
    brain = FakeBrain(merged=merged)

    res = _save_or_merge("I carichi del Progetto Poseidon sono ancora da validare.", memory, brain)

    assert res["saved"] is True
    assert res["merged"] is True
    assert res["content"] == merged
    assert brain.merge_calls == [(match["content"], "I carichi del Progetto Poseidon sono ancora da validare.")]
    assert memory.saved == [(merged, "user", True)]
    assert memory.superseded == [("old-user", "new-id")]


def test_explicit_save_does_not_merge_into_correction_pending_memory_even_if_user():
    match = {
        "id": "old-pending",
        "content": "Stefano preferisce fragole e cipolla.",
        "similarity": 0.92,
        "source": "user",
        "correction_pending": True,
    }
    memory = FakeMemory(match)
    brain = FakeBrain()
    content = "La frase su fragole e cipolla era una provocazione, non una preferenza reale."

    res = _save_or_merge(content, memory, brain)

    assert res["saved"] is True
    assert res["merged"] is False
    assert brain.merge_calls == []
    assert memory.saved == [(content, "user", True)]
    assert memory.superseded == [("old-pending", "new-id")]


if __name__ == "__main__":
    test_explicit_save_does_not_merge_into_weak_passive_memory()
    test_explicit_save_still_merges_into_trusted_user_memory()
    test_explicit_save_does_not_merge_into_correction_pending_memory_even_if_user()
    print("test_save_service_merge_guard: OK")
