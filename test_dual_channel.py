#!/usr/bin/env python3
"""Regressioni pure della policy dual-channel congelata (nessun LLM/Redis).

Include una riproduzione sul development set: la policy congelata deve
riprodurre esattamente il risultato analizzato offline (provenance finale
75,76%, 9 recuperi esclusivi, 0 gold persi) alla config Q=2/R=1/budget=2500.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.euri_memory.dual_channel import (
    FROZEN_POLICY,
    compose_dual_channel,
    gold_covered,
)


ROOT = Path(__file__).resolve().parent
DEV_RUN = ROOT / "audit_output" / "passive_memory_heldout_v1_seed914917171" / "run"


def _fixed_chars(_turn):  # ogni turno costa 100 caratteri
    return 100


def test_frozen_policy_values():
    assert FROZEN_POLICY["policy_id"] == "dual-channel-q2r1-v1"
    assert FROZEN_POLICY["Q_notes"] == 2
    assert FROZEN_POLICY["R_sources_per_note"] == 1
    assert FROZEN_POLICY["max_additions"] == 2
    assert FROZEN_POLICY["char_budget"] == 2500
    assert FROZEN_POLICY["synthetic_text_in_prompt"] is False


def test_base_is_protected_and_no_synthetic_text():
    comp = compose_dual_channel(
        base_turn_ids=["D1:1", "D1:2"],
        base_chars=300,
        locator_notes=[["D5:1"], ["D6:1"]],
        turn_chars=_fixed_chars,
    )
    # base sempre presente e per prima
    assert comp.final_turn_ids[:2] == ["D1:1", "D1:2"]
    assert comp.base_chars == 300  # base mai troncata
    # nessun testo sintetico: le aggiunte portano solo turn_id/chars
    for a in comp.additions:
        assert set(a) == {"turn_id", "chars", "from_note_index"}
    for c in comp.candidates_considered:
        assert "content" not in c and "text" not in c


def test_dedup_against_base_and_between_sources():
    comp = compose_dual_channel(
        base_turn_ids=["D1:1"],
        base_chars=100,
        locator_notes=[["D1:1"], ["D2:1"]],  # prima nota duplica la base
        turn_chars=_fixed_chars,
    )
    assert comp.duplicates_skipped == 1
    assert comp.added_turn_ids() == ["D2:1"]


def test_slot_cap_limits_to_two_additions():
    comp = compose_dual_channel(
        base_turn_ids=["D1:1"],
        base_chars=100,
        locator_notes=[["D2:1"], ["D3:1"]],  # Q=2, ciascuna 1 source -> 2 aggiunte
        turn_chars=_fixed_chars,
    )
    assert len(comp.additions) == 2
    # una terza nota non verrebbe comunque considerata (Q=2)
    comp3 = compose_dual_channel(
        base_turn_ids=["D1:1"],
        base_chars=100,
        locator_notes=[["D2:1"], ["D3:1"], ["D4:1"]],
        turn_chars=_fixed_chars,
    )
    assert len(comp3.additions) == 2
    assert comp3.discarded_slot_cap == 0  # la 3ª nota è fuori quota Q, non scartata per slot


def test_budget_limits_only_additions_never_base():
    # base già oltre il budget: nessuna aggiunta, base intatta
    comp = compose_dual_channel(
        base_turn_ids=["D1:1", "D1:2"],
        base_chars=2600,
        locator_notes=[["D2:1"]],
        turn_chars=_fixed_chars,
    )
    assert comp.additions == []
    assert comp.discarded_budget == 1
    assert comp.base_chars == 2600
    assert comp.final_turn_ids == ["D1:1", "D1:2"]


def test_r_limits_sources_per_note():
    comp = compose_dual_channel(
        base_turn_ids=["D1:1"],
        base_chars=100,
        locator_notes=[["D2:1", "D2:2"]],  # R=1 -> solo il primo
        turn_chars=_fixed_chars,
    )
    assert comp.added_turn_ids() == ["D2:1"]


def _dev_available() -> bool:
    return (DEV_RUN / "runs").is_dir() and (DEV_RUN / "localizations").is_dir()


def test_dev_set_reproduction_matches_analysis():
    if not _dev_available():
        return
    loc = {}
    for p in sorted((DEV_RUN / "localizations").glob("*.it.json")):
        loc[p.name[: -len(".it.json")]] = json.loads(p.read_text()).get("turns", {})

    n = 0
    final_hits = 0
    exclusive = 0
    gold_lost = 0
    for path in sorted((DEV_RUN / "runs").glob("*.json")):
        rep = json.loads(path.read_text())
        prof = {pr["profile"]["name"]: pr for pr in rep["profiles"]}
        sample = rep["dataset"]["sample_id"]
        gold_by_q = {g["question_id"]: g for g in rep["evaluation_gold"]}
        rag = {r["question_id"]: r for r in prof["rag_only"]["results"]}
        pas = {r["question_id"]: r for r in prof["passive_memory"]["results"]}
        for qid, g in gold_by_q.items():
            if g.get("expected_answer") is None or not g.get("evidence_turn_ids"):
                continue
            n += 1
            gold = [str(t) for t in g["evidence_turn_ids"]]
            rnodes = rag[qid].get("metadata", {}).get("retrieval_nodes", [])
            base_turns = [str(t) for node in sorted(rnodes, key=lambda x: x.get("position", 0))
                          if node.get("source") == "conversation"
                          for t in (node.get("evidence_turn_ids") or [])]
            base_chars = rag[qid].get("metadata", {}).get("rag_chars", 0)
            pnodes = pas[qid].get("metadata", {}).get("retrieval_nodes", [])
            notes = [[str(t) for t in (node.get("evidence_turn_ids") or [])]
                     for node in sorted(pnodes, key=lambda x: x.get("position", 0))
                     if node.get("source") == "passive"]
            comp = compose_dual_channel(
                base_turn_ids=base_turns,
                base_chars=base_chars,
                locator_notes=notes,
                turn_chars=lambda t, s=sample: len(loc.get(s, {}).get(t, "")),
            )
            base_cov = gold_covered(comp.base_turn_ids, gold)
            final_cov = gold_covered(comp.final_turn_ids, gold)
            if final_cov:
                final_hits += 1
            if final_cov and not base_cov:
                exclusive += 1
            if base_cov and not final_cov:
                gold_lost += 1

    assert n == 198, n
    assert gold_lost == 0
    assert exclusive == 9, exclusive
    assert round(final_hits / n, 4) == 0.7576, final_hits / n


if __name__ == "__main__":
    test_frozen_policy_values()
    test_base_is_protected_and_no_synthetic_text()
    test_dedup_against_base_and_between_sources()
    test_slot_cap_limits_to_two_additions()
    test_budget_limits_only_additions_never_base()
    test_r_limits_sources_per_note()
    test_dev_set_reproduction_matches_analysis()
    print("test_dual_channel: OK")
