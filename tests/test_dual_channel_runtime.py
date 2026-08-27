#!/usr/bin/env python3
"""Regressioni runtime della memoria dual-channel (nessun LLM/Redis reale)."""
from __future__ import annotations

import numpy as np

import core.domain_gater as domain_gater
from core.brain import Brain
from core.conversation_turns import ConversationTurnStore, make_turn_ref
from core.memory_manager import MemoryManager
from core.rag_context import (
    RagContext,
    apply_knowledge_gap_contract,
    build_dual_channel_context,
    build_runtime_rag_context,
    memory_origin_for_context,
    selective_thinking_decision,
)
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
        self.events = []

    def json(self):
        return self._json

    def xadd(self, stream, fields, **kwargs):
        self.events.append((stream, fields, kwargs))
        return f"1-{len(self.events)}"


class FakeMemory:
    def __init__(self, redis_client, base, passive, embedder=None):
        self.r = redis_client
        self.base = base
        self.passive = passive
        self._embedder = embedder
        self.insight_calls = []

    def get_recent_reflections(self, **_kwargs):
        return []

    def get_recent_memories(self, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_memories(self, _query, **kwargs):
        cache = kwargs.get("query_feature_cache")
        if cache is not None and self._embedder is not None:
            entries = cache.setdefault("entries", {})
            if _query not in entries:
                entries[_query] = {
                    "domain": "chimica polimeri",
                    "vector": self._embedder.encode(_query, mode="query"),
                }
        if "passive" in (kwargs.get("source_exclude") or []):
            return [self.base]
        return [self.passive, self.base]

    def search_notes(self, *_args, **_kwargs):
        return []

    def search_insights(self, *_args, **_kwargs):
        self.insight_calls.append((_args, _kwargs))
        return []


class FakeEmbedder:
    available = True

    def encode(self, text, mode="passage"):
        if mode == "query":
            return np.asarray([1.0, 0.0], dtype=np.float32)
        if "IZOD" in text:
            return np.asarray([1.0, 0.0], dtype=np.float32)
        return np.asarray([0.8, 0.6], dtype=np.float32)

    def encode_many(self, texts, mode="passage"):
        return np.asarray([self.encode(text, mode=mode) for text in texts])


def test_memory_origin_exposes_kind_without_assigning_truth():
    assert memory_origin_for_context({"source": "user"}) == (
        "[ORIGINE: comunicato da Stefano]"
    )
    assert memory_origin_for_context({"source": "web"}) == (
        "[ORIGINE: risultato Web salvato, fonte esterna]"
    )
    assert memory_origin_for_context({"source": "reflection"}) == (
        "[ORIGINE: interpretazione autobiografica di Euri]"
    )
    derived = memory_origin_for_context({"source": "loop2e"})
    assert "consolidamento interno di Euri" in derived
    assert all(
        word not in " ".join(
            [
                memory_origin_for_context({"source": "user"}),
                memory_origin_for_context({"source": "web"}),
                memory_origin_for_context({"source": "reflection"}),
            ]
        ).lower()
        for word in ("vero", "falso", "affidabile")
    )


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
    assert archived.render().startswith("[Turno originale del ")
    assert archived.render().endswith(
        "Stefano: Il modulo rilevato è 1250 MPa."
    )

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
    assert (
        rag.diagnostics["verbatim_render_version"]
        == "absolute-time-auth-channel-v1"
    )
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


def test_selective_runtime_prepends_only_high_confidence_original_turn():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    turn_ref = make_turn_ref("conversation-test", 8)
    store.persist(
        {
            "turn_ref": turn_ref,
            "conversation_id": "conversation-test",
            "seq": 8,
            "role": "user",
            "content": "Il valore IZOD misurato è 3,8.",
            "trusted": True,
            "observed_at": 124.0,
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
        "content": "Il valore IZOD del compound è 3,8.",
        "source": "passive",
        "domain": "chimica polimeri",
        "temporal_context": {"source_turn_refs": [turn_ref]},
    }
    redis.docs["euri:memory:passive-1"] = passive
    memory = FakeMemory(redis, base, passive, embedder=FakeEmbedder())

    rag = build_dual_channel_context(
        "Qual è il valore IZOD del compound?",
        memory,
        store,
        mode="search",
        presentation="selective",
        observe_selective=True,
    )

    assert rag.text.index("Il valore IZOD misurato è 3,8.") < rag.text.index(
        base["content"]
    )
    assert rag.diagnostics["presentation_applied"] == "selective_prepend"
    assert rag.diagnostics["selective_gate"]["promoted_turn_ids"] == [turn_ref]
    hydrated = next(
        node
        for node in rag.nodes
        if node.get("retrieval_path") == "passive_locator_hydrated"
    )
    assert hydrated["prompt_region"] == "prepend"
    assert hydrated["selective_gate_decision"] == "prepend"
    assert selective_thinking_decision(rag) == {
        "enabled": True,
        "reason": "promoted_verbatim",
        "promoted_turn_ids": [turn_ref],
    }


def test_shared_runtime_dispatcher_applies_selective_mode_for_all_channels():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    turn_ref = make_turn_ref("silent-chat-test", 1)
    store.persist(
        {
            "turn_ref": turn_ref,
            "conversation_id": "silent-chat-test",
            "seq": 1,
            "role": "user",
            "content": "Il valore IZOD misurato è 3,8.",
            "trusted": True,
            "observed_at": 125.0,
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
        "content": "Il valore IZOD del compound è 3,8.",
        "source": "passive",
        "domain": "chimica polimeri",
        "temporal_context": {"source_turn_refs": [turn_ref]},
    }
    redis.docs["euri:memory:passive-1"] = passive

    rag = build_runtime_rag_context(
        "Qual è il valore IZOD del compound?",
        FakeMemory(redis, base, passive, embedder=FakeEmbedder()),
        store,
        mode="search",
        recent_history=[],
        dual_mode="selective",
    )

    assert rag.diagnostics["presentation_applied"] == "selective_prepend"
    assert rag.text.index("Il valore IZOD misurato è 3,8.") < rag.text.index(
        base["content"]
    )
    assert selective_thinking_decision(rag)["enabled"] is True


def test_selective_thinking_stays_off_without_promoted_verbatim():
    rag = build_runtime_rag_context(
        "Ciao, come stai?",
        FakeMemory(
            FakeRedis(),
            {
                "id": "base-1",
                "content": "Una memoria ambientale.",
                "source": "user",
                "domain": "generale",
            },
            {
                "id": "passive-1",
                "content": "Nota senza fonte idratabile.",
                "source": "passive",
                "domain": "generale",
                "temporal_context": {},
            },
            embedder=FakeEmbedder(),
        ),
        ConversationTurnStore(FakeRedis()),
        dual_mode="selective",
    )

    decision = selective_thinking_decision(rag)
    assert decision["enabled"] is False
    assert decision["reason"] == "no_promoted_verbatim"


def _evidence_frame(*, dependency="optional", memory_only=False):
    return {
        "status": "interpreted",
        "turn_id": "turn-gap-test",
        "confidence": 0.98,
        "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
        "evidence_request": {
            "dependency": dependency,
            "entities": ["Eurostampi"],
            "premises": [
                "Eurostampi e' proposta dall'utente come termine di confronto"
            ],
            "missing_facts": ["processi e indicatori reali di produttivita'"],
            "acceptable_sources": ["current_user", "web"],
            "memory_only": memory_only,
            "confidence": 0.97,
        },
    }


def test_runtime_gap_emits_pulse_and_never_authorizes_web_automatically():
    redis = FakeRedis()
    memory = type("Memory", (), {"r": redis})()
    rag = RagContext(text="Contesto esistente", ids=[], mode="search")

    result = apply_knowledge_gap_contract(rag, memory, _evidence_frame())

    gap = result.diagnostics["knowledge_gap"]
    assert gap["detected"] is True
    assert gap["uncovered_entities"] == ["Eurostampi"]
    assert "vuoto di conoscenza" in result.text
    assert "non autorizza automaticamente alcuna ricerca Web" in result.text
    assert len(redis.events) == 1
    assert redis.events[0][1]["kind"] == "knowledge_gap_detected"


def test_matching_direct_memory_is_a_candidate_not_a_license_to_extrapolate():
    redis = FakeRedis()
    memory = type("Memory", (), {"r": redis})()
    rag = RagContext(
        text="Una memoria pertinente.",
        ids=["m1"],
        mode="search",
        nodes=[{
            "kind": "memory",
            "id": "m1",
            "content": "Eurostampi costruisce stampi e stampa grandi formati.",
            "entity_mentions": ["Eurostampi"],
            "factual_support_eligible": True,
            "requires_verification": False,
            "epistemic_status": "",
        }],
    )

    result = apply_knowledge_gap_contract(rag, memory, _evidence_frame())

    assert result.diagnostics["knowledge_gap"]["detected"] is False
    assert "esistono candidati fattuali" in result.text
    assert "usa soltanto cio' che il contenuto del nodo sostiene davvero" in result.text
    assert redis.events == []


def test_memory_only_gap_never_offers_user_documents_or_web():
    redis = FakeRedis()
    memory = type("Memory", (), {"r": redis})()
    frame = _evidence_frame(dependency="required", memory_only=True)
    frame["evidence_request"]["acceptable_sources"] = []

    result = apply_knowledge_gap_contract(
        RagContext(text="", ids=[], mode="search"), memory, frame
    )

    assert "senza proporre fonti esterne" in result.text
    assert "fonti semanticamente adatte" not in result.text


def test_dependency_none_does_not_add_contract_or_emit_pulse():
    redis = FakeRedis()
    memory = type("Memory", (), {"r": redis})()
    frame = _evidence_frame(dependency="none")
    frame["evidence_request"].update({
        "entities": [], "acceptable_sources": [], "confidence": 0.97,
    })
    rag = RagContext(text="Solo le premesse bastano.", ids=[], mode="chat")

    result = apply_knowledge_gap_contract(rag, memory, frame)

    assert result.text == "Solo le premesse bastano."
    assert result.diagnostics["knowledge_gap"]["evaluated"] is False
    assert redis.events == []


def test_passive_exclusion_is_a_redis_prefilter_not_a_post_cut_filter():
    assert MemoryManager._source_prefix(None, ["passive"]) == (
        "@memory_scope:{personal} -@source:{passive}"
    )
    assert MemoryManager._source_prefix(["user", "teach"], ["passive"]) == (
        "@memory_scope:{personal} @source:{user|teach} -@source:{passive}"
    )


def test_query_features_are_reused_without_sharing_search_results():
    class EmptySearch:
        docs = []

    class FakeFt:
        def search(self, *_args, **_kwargs):
            return EmptySearch()

    class SearchRedis:
        def ft(self, _index):
            return FakeFt()

    class CountingEmbedder:
        def __init__(self):
            self.calls = 0

        def encode(self, _text, mode="passage"):
            assert mode == "query"
            self.calls += 1
            return np.asarray([1.0, 0.0], dtype=np.float32)

    embedder = CountingEmbedder()
    domain_calls = []
    cache = {}
    original_assign_domain = domain_gater.assign_domain
    try:
        domain_gater.assign_domain = lambda text: domain_calls.append(text) or "automotive"
        first = domain_gater.domain_aware_search(
            "problema sensore Jeep",
            embedder,
            SearchRedis(),
            source_exclude=["passive"],
            query_feature_cache=cache,
        )
        second = domain_gater.domain_aware_search(
            "problema sensore Jeep",
            embedder,
            SearchRedis(),
            query_feature_cache=cache,
        )
    finally:
        domain_gater.assign_domain = original_assign_domain

    assert first == second == []
    assert domain_calls == ["problema sensore Jeep"]
    assert embedder.calls == 1


def test_dual_channel_searches_insights_once_and_reuses_query_vector():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    embedder = FakeEmbedder()
    memory = FakeMemory(
        redis,
        {
            "id": "base-1",
            "content": "La memoria base resta protetta.",
            "source": "user",
            "domain": "chimica polimeri",
        },
        {
            "id": "passive-1",
            "content": "Nota locator senza fonte disponibile.",
            "source": "passive",
            "domain": "chimica polimeri",
        },
        embedder=embedder,
    )

    build_dual_channel_context(
        "Quali dati ricordi sulla prova IZOD?",
        memory,
        store,
        touch=True,
    )

    assert len(memory.insight_calls) == 1
    _args, kwargs = memory.insight_calls[0]
    assert kwargs["touch"] is True
    assert np.array_equal(
        kwargs["query_vector"],
        np.asarray([1.0, 0.0], dtype=np.float32),
    )


if __name__ == "__main__":
    test_brain_persists_stable_turn_refs_and_metadata_reuses_them()
    test_dual_channel_protects_base_and_injects_only_original_turn()
    test_unhydrated_historical_passive_note_is_not_used_as_evidence()
    test_selective_runtime_prepends_only_high_confidence_original_turn()
    test_shared_runtime_dispatcher_applies_selective_mode_for_all_channels()
    test_selective_thinking_stays_off_without_promoted_verbatim()
    test_runtime_gap_emits_pulse_and_never_authorizes_web_automatically()
    test_matching_direct_memory_is_a_candidate_not_a_license_to_extrapolate()
    test_memory_only_gap_never_offers_user_documents_or_web()
    test_dependency_none_does_not_add_contract_or_emit_pulse()
    test_passive_exclusion_is_a_redis_prefilter_not_a_post_cut_filter()
    test_query_features_are_reused_without_sharing_search_results()
    test_dual_channel_searches_insights_once_and_reuses_query_vector()
    print("test_dual_channel_runtime: OK")
