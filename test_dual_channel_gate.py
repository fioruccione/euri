#!/usr/bin/env python3
"""Regressioni pure del gate selettivo (nessun Redis/LLM)."""
from __future__ import annotations

import numpy as np

from core.dual_channel import compose_dual_channel
from core.dual_channel_gate import (
    SelectiveThresholds,
    compose_selective_presentation,
    evaluate_selective_gate,
)


class FakeEmbedder:
    available = True

    def __init__(self, vectors):
        self.vectors = {
            key: np.asarray(value, dtype=np.float32)
            for key, value in vectors.items()
        }

    def encode(self, text, mode="passage"):
        return self.vectors[(mode, text)]

    def encode_many(self, texts, mode="passage"):
        return np.asarray([self.vectors[(mode, text)] for text in texts])


THRESHOLDS = SelectiveThresholds(
    min_query_source_similarity=0.92,
    min_relevance_margin=-0.01,
    max_source_base_similarity=0.985,
)


def _composition():
    return compose_dual_channel(
        base_context_text="BASE PROTETTA",
        base_slots=1,
        base_turn_ids=[],
        locator_notes=[["conv:1"], ["conv:2"]],
        render_turn=lambda turn: {
            "conv:1": "Stefano: valore corretto",
            "conv:2": "Stefano: dettaglio secondario",
        }[turn],
    )


def test_relevant_incremental_turn_is_promoted_before_protected_base():
    composition = _composition()
    embedder = FakeEmbedder(
        {
            ("query", "domanda"): [1.0, 0.0],
            ("passage", "valore corretto"): [1.0, 0.0],
            ("passage", "dettaglio secondario"): [0.6, 0.8],
            ("passage", "memoria base"): [0.8, 0.6],
        }
    )
    gate = evaluate_selective_gate(
        query="domanda",
        base_nodes=[{"content": "memoria base"}],
        additions=[
            {
                "turn_id": "conv:1",
                "content": "valore corretto",
                "from_note_index": 0,
            },
            {
                "turn_id": "conv:2",
                "content": "dettaglio secondario",
                "from_note_index": 1,
            },
        ],
        locator_nodes=[
            {"id": "passive-1", "position": 1, "retrieval_score": 0.08},
            {"id": "passive-2", "position": 2, "retrieval_score": 0.12},
        ],
        embedder=embedder,
        thresholds=THRESHOLDS,
    )
    assert gate["promoted_turn_ids"] == ["conv:1"]
    assert gate["candidates"][0]["decision"] == "prepend"
    assert gate["candidates"][1]["decision"] == "append"
    assert "weak_query_source" in gate["candidates"][1]["reasons"]

    text, regions = compose_selective_presentation(
        composition, gate["promoted_turn_ids"]
    )
    assert text.index("valore corretto") < text.index("BASE PROTETTA")
    assert text.index("BASE PROTETTA") < text.index("dettaglio secondario")
    assert regions["conv:1"] == "prepend"
    assert regions["conv:2"] == "append"


def test_semantically_redundant_turn_stays_in_append():
    composition = _composition()
    embedder = FakeEmbedder(
        {
            ("query", "domanda"): [1.0, 0.0],
            ("passage", "valore corretto"): [1.0, 0.0],
            ("passage", "memoria equivalente"): [1.0, 0.0],
        }
    )
    gate = evaluate_selective_gate(
        query="domanda",
        base_nodes=[{"content": "memoria equivalente"}],
        additions=[
            {
                "turn_id": "conv:1",
                "content": "valore corretto",
                "from_note_index": 0,
            }
        ],
        locator_nodes=[{"id": "passive-1", "position": 1}],
        embedder=embedder,
        thresholds=THRESHOLDS,
    )
    assert gate["promoted_turn_ids"] == []
    assert "redundant_with_base" in gate["candidates"][0]["reasons"]
    text, regions = compose_selective_presentation(composition, [])
    assert text == composition.final_context_text
    assert regions["conv:1"] == "append"


def test_missing_embedder_fails_closed_to_append():
    gate = evaluate_selective_gate(
        query="domanda",
        base_nodes=[],
        additions=[
            {"turn_id": "conv:1", "content": "valore", "from_note_index": 0}
        ],
        locator_nodes=[],
        embedder=None,
        thresholds=THRESHOLDS,
    )
    assert gate["promoted_turn_ids"] == []
    assert gate["presentation"] == "append"
    assert gate["fallback_reason"] == "embedder_unavailable"


if __name__ == "__main__":
    test_relevant_incremental_turn_is_promoted_before_protected_base()
    test_semantically_redundant_turn_stays_in_append()
    test_missing_embedder_fails_closed_to_append()
    print("test_dual_channel_gate: OK")
