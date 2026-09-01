#!/usr/bin/env python3
"""Regressioni pure per recall/usage lineage in shadow mode."""

import json

from core.rag_context import build_rag_context
from core.response_lineage import finish_response_turn, start_response_turn


class FakeRedis:
    def __init__(self):
        self.events = []

    def xadd(self, stream, fields, **_kwargs):
        event_id = f"{len(self.events) + 1}-0"
        self.events.append((stream, event_id, dict(fields)))
        return event_id


class FakeMemory:
    def get_recent_reflections(self, **_kwargs):
        return [{
            "id": "reflection-1",
            "content": "La dispersione della polvere resta un'ipotesi interna.",
            "source": "reflection",
            "domain": "riciclo",
        }]

    def get_recent_memories(self, **_kwargs):
        return [{
            "id": "memory-1",
            "content": "Il campione UBQ viene provato al 10 percento nel PP nero.",
            "source": "user",
            "domain": "riciclo",
        }]

    def get_pending_todos(self):
        return [{
            "id": "todo-1",
            "content": "Controllare domani le prove meccaniche UBQ.",
        }]

    def search_memories(self, *_args, **_kwargs):
        return []

    def search_notes(self, *_args, **_kwargs):
        return []

    def search_insights(self, *_args, **_kwargs):
        return [{
            "id": "insight-1",
            "content": "La granulometria può contribuire all'effetto opaco.",
            "domain_a": "riciclo",
            "domain_b": "aspetto",
            "status": "promoted",
            "requires_verification": True,
        }]


def test_rag_reports_only_nodes_actually_injected_without_changing_legacy_ids():
    rag = build_rag_context(
        "Cosa sai del campione UBQ?", FakeMemory(), mode="chat"
    )

    assert rag.ids == ["memory-1"]
    assert {
        (node["kind"], node["id"], node["retrieval_path"])
        for node in rag.nodes
    } == {
        ("memory", "reflection-1", "recent_reflection"),
        ("memory", "todo-1", "open_commitment"),
        ("memory", "memory-1", "base_rag"),
        ("insight", "insight-1", "insight_rag"),
    }


def test_turn_separates_recall_from_supported_use_and_keeps_text_private():
    redis = FakeRedis()
    query = "Che cosa pensi del campione?"
    nodes = [
        {
            "kind": "memory",
            "id": "memory-1",
            "content": (
                "Il materiale UBQ al 10 percento migliora l'effetto opaco "
                "e mantiene le proprietà meccaniche."
            ),
            "position": 1,
            "retrieval_path": "base_rag",
            "source": "user",
            "domain": "riciclo",
        },
        {
            "kind": "insight",
            "id": "insight-1",
            "content": "La temperatura della luna determina il colore del campione.",
            "position": 1,
            "retrieval_path": "insight_rag",
        },
    ]
    lineage = start_response_turn(
        redis,
        query=query,
        channel="silent_chat",
        mode="chat",
        nodes=nodes,
    )
    finish_response_turn(
        redis,
        lineage,
        response=(
            "Nel test il 10 percento di UBQ può migliorare l'effetto opaco "
            "mantenendo le proprietà meccaniche."
        ),
    )

    kinds = [
        (fields["sense"], fields["kind"])
        for _stream, _event_id, fields in redis.events
    ]
    assert kinds == [
        ("turn", "started"),
        ("memory", "recalled"),
        ("insight", "recalled"),
        ("turn", "responded"),
        ("memory", "used_in_response"),
    ]
    recalled = redis.events[1][2]
    responded = redis.events[3][2]
    used = redis.events[4][2]
    assert recalled["causation_id"] == redis.events[0][1]
    assert used["causation_id"] == redis.events[3][1]
    assert json.loads(used["payload"])["status"] == "supported_not_proven"
    assert query not in recalled["payload"]
    assert "proprietà meccaniche" not in used["payload"]
    assert json.loads(responded["parent_refs"]) == [
        redis.events[1][1],
        redis.events[2][1],
    ]


def test_query_echo_is_recall_only_not_usage_evidence():
    redis = FakeRedis()
    query = "Il campione si chiama UBQ 10?"
    lineage = start_response_turn(
        redis,
        query=query,
        channel="voice_chat",
        mode="chat",
        nodes=[{
            "kind": "memory",
            "id": "m1",
            "content": "Il campione si chiama UBQ 10.",
            "position": 1,
            "retrieval_path": "base_rag",
        }],
    )
    finish_response_turn(redis, lineage, response="Il campione si chiama UBQ 10.")

    assert [
        fields["kind"] for _stream, _event_id, fields in redis.events
    ] == ["started", "recalled", "responded"]


def test_factual_overview_excludes_tentative_insight_but_keeps_confirmed_one():
    class Memory(FakeMemory):
        def get_recent_reflections(self, **_kwargs):
            return []

        def get_recent_memories(self, **_kwargs):
            return []

        def search_insights(self, *_args, **_kwargs):
            return [
                {
                    "id": "tentative",
                    "content": "Una GPU potrebbe controllare il processo produttivo.",
                    "domain_a": "progetto",
                    "domain_b": "hardware",
                    "status": "promoted",
                    "requires_verification": True,
                    "verification_status": "internally_convergent",
                },
                {
                    "id": "confirmed",
                    "content": "Il campione confermato usa dieci chilogrammi.",
                    "domain_a": "progetto",
                    "domain_b": "test",
                    "status": "promoted",
                    "requires_verification": False,
                    "verification_status": "externally_confirmed",
                    "external_reaction": {"verdict": "CONFERMA"},
                },
            ]

    frame = {
        "status": "interpreted",
        "confidence": 0.98,
        "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
        "memory_retrieval": {
            "needed": True,
            "focus": [{"entity": "Progetto Alfa", "relevance": 0.99}],
            "relation": "panoramica",
            "evidence_goal": "overview",
            "confidence": 0.98,
        },
    }
    rag = build_rag_context(
        "Fammi una panoramica del Progetto Alfa.",
        Memory(),
        mode="search",
        semantic_frame=frame,
    )

    assert "Una GPU potrebbe controllare" not in rag.text
    assert "Il campione confermato" in rag.text
    assert not [node for node in rag.nodes if node["id"] == "tentative"]
    assert [node["id"] for node in rag.nodes if node["kind"] == "insight"] == [
        "confirmed"
    ]
    assert rag.diagnostics["insight_retrieval"]["excluded_tentative_ids"] == [
        "tentative"
    ]


if __name__ == "__main__":
    test_rag_reports_only_nodes_actually_injected_without_changing_legacy_ids()
    test_turn_separates_recall_from_supported_use_and_keeps_text_private()
    test_query_echo_is_recall_only_not_usage_evidence()
    test_factual_overview_excludes_tentative_insight_but_keeps_confirmed_one()
    print("test_response_lineage: OK")
