"""Regressioni pure per il gate epistemico dei semi Dream (Loop 2b)."""

from types import SimpleNamespace

from core.dream_engine import (
    DreamEngine,
    dream_seed_rejection_reason,
    is_dream_seed_eligible,
)


def _clean(**overrides):
    doc = {
        "id": "good",
        "content": "Stefano ha misurato MFI e IZOD sul campione.",
        "source": "passive",
        "memory_kind": "semantic_fact",
        "embedding": [0.1, 0.2],
        "created_at": 123.0,
    }
    doc.update(overrides)
    return doc


def test_pure_gate():
    assert is_dream_seed_eligible(_clean())
    assert is_dream_seed_eligible(_clean(source="user"))
    assert is_dream_seed_eligible(_clean(source="teach"))

    rejected = [
        (_clean(source="reflection"), "derived_source"),
        (_clean(source="reaction"), "derived_source"),
        (_clean(source="loop2e"), "derived_source"),
        (_clean(memory_kind="conversation_anchor"), "non_factual_kind"),
        (_clean(memory_kind="conversation_episode"), "non_factual_kind"),
        (_clean(tags=["lesson"]), "derived_tag"),
        (_clean(superseded_by="new"), "superseded"),
        (_clean(consolidated_into="new"), "superseded"),
        (_clean(correction_pending=True), "correction_pending"),
        (_clean(requires_verification=True), "requires_verification"),
        (_clean(provenance_stale=True), "requires_verification"),
        (_clean(safety_flag="prompt_injection"), "safety_flag"),
        (_clean(audit_flag=1), "audit_flag"),
        (_clean(consolidation_risk={"level": "watch"}), "consolidation_risk"),
        (_clean(memory_axes={"subject_status": "acephalous"}), "acephalous"),
        (_clean(passive_support="tacit_acceptance"), "tacit_acceptance"),
        (_clean(embedding=None), "incomplete"),
    ]
    for doc, expected in rejected:
        assert dream_seed_rejection_reason(doc) == expected, (doc, expected)


class _Json:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key):
        return self.docs.get(key)


class _Ft:
    def __init__(self, keys):
        self.keys = keys
        self.query = None

    def search(self, query):
        self.query = str(query.query_string())
        return SimpleNamespace(docs=[SimpleNamespace(id=key) for key in self.keys])


class _Redis:
    def __init__(self, docs):
        self._json = _Json(docs)
        self._ft = _Ft(list(docs))

    def json(self):
        return self._json

    def ft(self, _index):
        return self._ft


def test_fetch_revalidates_json():
    docs = {
        "euri:memory:derived": _clean(source="reflection"),
        "euri:memory:superseded": _clean(superseded_by="winner"),
        "euri:memory:good": _clean(id="good"),
    }
    engine = DreamEngine.__new__(DreamEngine)
    engine._r = _Redis(docs)

    seed = engine._get_random_memory_from_domain("chimica polimeri")
    assert seed is not None
    assert seed["id"] == "euri:memory:good"
    assert seed["content"] == docs["euri:memory:good"]["content"]
    assert "@source:" in engine._r._ft.query


def test_fetch_prefers_a_seed_with_verbatim_provenance():
    docs = {
        "euri:memory:legacy": _clean(id="legacy"),
        "euri:memory:grounded": _clean(
            id="grounded",
            temporal_context={"source_turn_refs": ["conv:7"]},
        ),
    }
    engine = DreamEngine.__new__(DreamEngine)
    engine._r = _Redis(docs)

    seed = engine._get_random_memory_from_domain("chimica polimeri")

    assert seed is not None
    assert seed["id"] == "euri:memory:grounded"
    assert seed["temporal_context"]["source_turn_refs"] == ["conv:7"]


def test_pick_seed_skips_empty_domains():
    engine = DreamEngine.__new__(DreamEngine)
    docs = {"vuoto": None, "fragile": None, "buono": _clean()}
    engine._get_random_memory_from_domain = lambda domain: docs[domain]

    picked = engine._pick_dream_seed(["vuoto", "fragile", "buono"], max_attempts=3)
    assert picked is not None and picked[0] == "buono"
    assert engine._pick_dream_seed(["buono"], exclude={"buono"}) is None


def test_pick_seed_prefers_provenance_across_sampled_domains():
    engine = DreamEngine.__new__(DreamEngine)
    docs = {
        "legacy": _clean(),
        "grounded": _clean(
            temporal_context={"source_turn_refs": ["conv:3"]},
        ),
    }
    engine._get_random_memory_from_domain = lambda domain: docs[domain]

    picked = engine._pick_dream_seed(["legacy", "grounded"], max_attempts=2)

    assert picked is not None and picked[0] == "grounded"


if __name__ == "__main__":
    test_pure_gate()
    test_fetch_revalidates_json()
    test_fetch_prefers_a_seed_with_verbatim_provenance()
    test_pick_seed_skips_empty_domains()
    test_pick_seed_prefers_provenance_across_sampled_domains()
    print("test_dream_seed_gate: OK")
