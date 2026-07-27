#!/usr/bin/env python3
"""Regressioni runtime della memoria dual-channel (nessun LLM/Redis reale)."""
from __future__ import annotations

from core.brain import Brain
from core.conversation_turns import ConversationTurnStore, make_turn_ref
from core.memory_manager import MemoryManager
from core.rag_context import build_dual_channel_context
from core.temporal_context import derive_passive_memory_metadata


class FakeJson:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, path, value):
        assert path == "$"
        self.docs[key] = dict(value)
        return True

    def get(self, key, path):
        assert path == "$"
        value = self.docs.get(key)
        return [dict(value)] if value is not None else None


class FakeRedis:
    def __init__(self):
        self.docs = {}
        self._json = FakeJson(self.docs)

    def json(self):
        return self._json


class FakeMemory:
    def __init__(self, redis_client, base, passive):
        self.r = redis_client
        self.base = base
        self.passive = passive

    def get_recent_reflections(self, **_kwargs):
        return []

    def get_recent_memories(self, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_memories(self, _query, **kwargs):
        if "passive" in (kwargs.get("source_exclude") or []):
            return [self.base]
        return [self.passive, self.base]

    def search_notes(self, *_args, **_kwargs):
        return []

    def search_insights(self, *_args, **_kwargs):
        return []


def test_brain_persists_stable_turn_refs_and_metadata_reuses_them():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    brain = Brain()
    brain._conversation_id = "conversation-test"
    brain._turn_callback = store.persist

    with brain.history_lock:
        brain._append_history_locked(
            "user", "Il modulo rilevato è 1250 MPa.", True, observed_at=123.0
        )

    message = brain.passive_messages_after(0)[0]
    assert message["turn_ref"] == "conversation-test:1"
    archived = store.get(message["turn_ref"])
    assert archived is not None
    assert archived.content == message["content"]
    assert archived.render().startswith("Stefano:")

    metadata = derive_passive_memory_metadata(
        {
            "content": "Il modulo rilevato è 1250 MPa.",
            "source_turn_ids": [1],
            "memory_kind": "semantic_fact",
        },
        [message],
    )
    assert metadata["temporal_context"]["source_turn_refs"] == [
        "conversation-test:1"
    ]


def test_dual_channel_protects_base_and_injects_only_original_turn():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    turn_ref = make_turn_ref("conversation-test", 7)
    store.persist(
        {
            "turn_ref": turn_ref,
            "conversation_id": "conversation-test",
            "seq": 7,
            "role": "user",
            "content": "Il valore IZOD misurato è 3,8.",
            "trusted": True,
            "observed_at": 123.0,
            "segment_id": 1,
        }
    )
    base = {
        "id": "base-1",
        "content": "Il progetto UBQ riguarda una prova sul compound.",
        "source": "user",
        "domain": "chimica polimeri",
    }
    passive = {
        "id": "passive-1",
        "content": "Sintesi volutamente diversa che non deve entrare nel prompt.",
        "source": "passive",
        "domain": "chimica polimeri",
        "temporal_context": {"source_turn_refs": [turn_ref]},
    }
    redis.docs["euri:memory:passive-1"] = passive
    memory = FakeMemory(redis, base, passive)

    rag = build_dual_channel_context(
        "Qual è il valore IZOD del compound?",
        memory,
        store,
        mode="search",
    )

    assert base["content"] in rag.text
    assert "Il valore IZOD misurato è 3,8." in rag.text
    assert passive["content"] not in rag.text
    assert rag.diagnostics["added_turn_ids"] == [turn_ref]
    assert all(node.get("source") != "passive" for node in rag.nodes)
    assert any(
        node.get("retrieval_path") == "passive_locator_hydrated"
        for node in rag.nodes
    )


def test_unhydrated_historical_passive_note_is_not_used_as_evidence():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    base = {
        "id": "base-1",
        "content": "Base protetta.",
        "source": "user",
        "domain": "generale",
    }
    passive = {
        "id": "passive-old",
        "content": "Una vecchia parafrasi senza turno originale.",
        "source": "passive",
        "domain": "generale",
        "temporal_context": {
            "conversation_id": "old-session",
            "source_turn_ids": [3],
        },
    }
    redis.docs["euri:memory:passive-old"] = passive
    rag = build_dual_channel_context(
        "Ricordi la base protetta?",
        FakeMemory(redis, base, passive),
        store,
        mode="search",
    )

    assert "Base protetta." in rag.text
    assert passive["content"] not in rag.text
    assert rag.diagnostics["added_turn_ids"] == []
    assert rag.diagnostics["candidates_considered"][0]["decision"] == (
        "source_unavailable"
    )


def test_passive_exclusion_is_a_redis_prefilter_not_a_post_cut_filter():
    assert MemoryManager._source_prefix(None, ["passive"]) == (
        "-@source:{passive}"
    )
    assert MemoryManager._source_prefix(["user", "teach"], ["passive"]) == (
        "@source:{user|teach} -@source:{passive}"
    )


if __name__ == "__main__":
    test_brain_persists_stable_turn_refs_and_metadata_reuses_them()
    test_dual_channel_protects_base_and_injects_only_original_turn()
    test_unhydrated_historical_passive_note_is_not_used_as_evidence()
    test_passive_exclusion_is_a_redis_prefilter_not_a_post_cut_filter()
    print("test_dual_channel_runtime: OK")
