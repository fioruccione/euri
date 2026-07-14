#!/usr/bin/env python3
"""Regressioni pure per il ranking epistemico dei candidati di memoria."""

from core.memory_risk import memory_epistemic_rank, rank_memories_epistemically
from core.temporal_recall import prioritize_window


def _memory(mid: str, source: str = "user", **flags) -> dict:
    return {"id": mid, "source": source, **flags}


def test_clean_results_preserve_relevance_order():
    results = [_memory("first"), _memory("second"), _memory("third")]

    assert [item["id"] for item in rank_memories_epistemically(results)] == [
        "first",
        "second",
        "third",
    ]


def test_severe_risk_is_demoted_but_remains_recoverable():
    results = [
        _memory("stale", provenance_stale=True),
        _memory("clean-1"),
        _memory("clean-2"),
        _memory("clean-3"),
        _memory("clean-4"),
    ]

    ranked = rank_memories_epistemically(results)

    assert ranked[0]["id"] == "clean-1"
    assert [item["id"] for item in ranked].index("stale") >= 3
    assert {item["id"] for item in ranked} == {item["id"] for item in results}


def test_correction_pending_and_superseded_are_not_context_evidence():
    results = [
        _memory("pending", correction_pending=True),
        _memory("superseded", superseded_by="replacement"),
        _memory("usable"),
    ]

    assert [item["id"] for item in rank_memories_epistemically(results)] == ["usable"]


def test_source_penalty_is_bounded_and_risk_flags_dominate_it():
    assert memory_epistemic_rank(_memory("direct", source="user")) == 0
    assert memory_epistemic_rank(_memory("derived", source="reflection")) == 25
    assert memory_epistemic_rank(
        _memory("fragile", source="user", consolidation_risk={"level": "high"})
    ) == 80


def test_limit_is_applied_after_epistemic_ranking():
    results = [
        _memory("risky", safety_flag=True),
        _memory("clean-1"),
        _memory("clean-2"),
    ]

    assert [item["id"] for item in rank_memories_epistemically(results, limit=2)] == [
        "clean-1",
        "clean-2",
    ]


def test_temporal_recall_cannot_reintroduce_quarantined_memory():
    window = [
        _memory("pending", source="user", correction_pending=True),
        _memory("derived", source="loop2e"),
    ]

    assert [item["id"] for item in prioritize_window(window)] == ["derived"]


if __name__ == "__main__":
    tests = [globals()[name] for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"test_epistemic_ranking: OK ({len(tests)} casi)")
