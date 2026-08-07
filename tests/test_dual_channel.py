#!/usr/bin/env python3
"""Regressioni pure della policy dual-channel congelata (nessun LLM/Redis).

Copre le regressioni della correzione 11 e la riproduzione dev con il renderer
definitivo (correzione 12): la policy congelata riproduce esattamente il
risultato analizzato (198 casi, 9 recuperi esclusivi, 0 gold persi, provenance
finale 75,76%), anche col budget calcolato sul rendering reale.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.dual_channel import (
    ADDITIONS_HEADER,
    FROZEN_POLICY,
    compose_dual_channel,
    gold_covered,
)
from benchmarks.euri_memory.dual_channel_worker import (
    build_census,
    hydrate_base_turn_ids,
    hydrate_locator_notes,
    locators_from_nodes,
    structural_dry_run,
)


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"
DEV_RUN = ROOT / "audit_output" / "passive_memory_heldout_v1_seed914917171" / "run"


def _render_short(t):
    return f"S: {t}"


def test_frozen_policy_values():
    assert FROZEN_POLICY["policy_id"] == "dual-channel-q2r1-v1"
    assert FROZEN_POLICY["Q_notes"] == 2
    assert FROZEN_POLICY["R_sources_per_note"] == 1
    assert FROZEN_POLICY["max_additions"] == 2
    assert FROZEN_POLICY["char_budget"] == 2500
    assert FROZEN_POLICY["synthetic_text_in_prompt"] is False
    assert FROZEN_POLICY["base"] == "entire_rag_only_context_protected"


def test_base_more_than_5_nodes_fully_preserved():
    base_ids = [f"D1:{i}" for i in range(1, 8)]  # 7 nodi (query temporale)
    base_text = "\n".join(f"S: turno {t}" for t in base_ids)
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=7,
        base_turn_ids=base_ids,
        locator_notes=[["D9:1"]],
        render_turn=_render_short,
    )
    assert comp.base_slots == 7
    assert comp.final_context_text.startswith(base_text)  # base intera conservata
    assert all(t in comp.final_turn_ids for t in base_ids)
    assert comp.base_chars == len(base_text)


def test_max_two_additions_and_slot_bound():
    base_text = "S: base"
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=7,
        base_turn_ids=["D1:1"],
        locator_notes=[["D2:1"], ["D3:1"], ["D4:1"]],  # Q=2 -> solo prime due note
        render_turn=_render_short,
    )
    assert len(comp.additions) == 2
    assert comp.final_slots <= comp.base_slots + 2


def test_base_bytes_and_sha_equal_across_arms():
    base_text = "OwnerUser: ciao\nAssistant: salve"
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=2,
        base_turn_ids=["D1:1", "D1:2"],
        locator_notes=[["D5:1"]],
        render_turn=_render_short,
    )
    # Braccio A userebbe base_text; braccio B usa comp.final_context_text.
    assert comp.final_context_text[: len(base_text)] == base_text  # byte-per-byte
    assert comp.base_sha256 == hashlib.sha256(base_text.encode()).hexdigest()
    assert comp.final_sha256 == hashlib.sha256(comp.final_context_text.encode()).hexdigest()


def test_budget_computed_on_real_rendering_never_touches_base():
    base_text = "x" * 2450  # base vicina al budget
    long_render = lambda t: "y" * 100  # noqa: E731
    comp = compose_dual_channel(
        base_context_text=base_text,
        base_slots=3,
        base_turn_ids=["D1:1"],
        locator_notes=[["D2:1"]],
        render_turn=long_render,
    )
    # header + sep + 100 caratteri superano 2500 -> scartata per budget
    assert comp.additions == []
    assert comp.discarded_budget == 1
    assert comp.base_chars == 2450  # base mai modificata
    assert comp.final_context_text == base_text
    # verifica che il conto sia sul rendering reale (header incluso)
    assert len(base_text) + len(ADDITIONS_HEADER) + 1 + 100 > FROZEN_POLICY["char_budget"]


def test_no_synthetic_text_in_record():
    comp = compose_dual_channel(
        base_context_text="S: base",
        base_slots=1,
        base_turn_ids=["D1:1"],
        locator_notes=[["D2:1"]],
        render_turn=_render_short,
    )
    rec = comp.to_record()
    for a in rec["additions"]:
        assert set(a) == {"turn_id", "chars", "from_note_index"}
    for c in rec["candidates_considered"]:
        assert "content" not in c and "text" not in c
    assert "base_context_text" not in rec  # solo hash/lunghezze, non il testo


def test_dedup_against_base_and_between_sources():
    comp = compose_dual_channel(
        base_context_text="S: base",
        base_slots=1,
        base_turn_ids=["D1:1"],
        locator_notes=[["D1:1"], ["D2:1"]],  # prima nota duplica la base
        render_turn=_render_short,
    )
    assert comp.duplicates_skipped == 1
    assert comp.added_turn_ids() == ["D2:1"]


def test_locators_match_dev_simulation_selection():
    nodes = [
        {"position": 0, "source": "conversation", "evidence_turn_ids": ["D1:1"]},
        {"position": 1, "source": "passive", "evidence_turn_ids": ["D2:1"]},
        {"position": 2, "source": "conversation", "evidence_turn_ids": ["D3:1"]},
        {"position": 3, "source": "passive", "evidence_turn_ids": ["D4:1", "D4:2"]},
        {"position": 4, "source": "passive", "evidence_turn_ids": ["D5:1"]},
    ]
    # primi due nodi con source=passive, in ordine di posizione; niente ricerca nuova
    assert locators_from_nodes(nodes) == [["D2:1"], ["D4:1", "D4:2"]]


def test_hydration_from_redis_docs_reconstructs_base_and_locators():
    # Blocker 1: RagContext.nodes NON contiene evidence_turn_ids; la risoluzione
    # avviene dai documenti Redis (benchmark_turn_id / benchmark_evidence_turn_ids).
    nodes = [
        {"id": "raw-a", "kind": "memory", "source": "conversation", "position": 0},
        {"id": "pas-a", "kind": "memory", "source": "passive", "position": 1},
        {"id": "raw-b", "kind": "memory", "source": "conversation", "position": 2},
        {"id": "pas-b", "kind": "memory", "source": "passive", "position": 3},
        {"id": "pas-c", "kind": "memory", "source": "passive", "position": 4},
    ]
    # nessun nodo porta evidence_turn_ids: devono essere risolti dai doc
    for n in nodes:
        assert "evidence_turn_ids" not in n
    docs = {
        "raw-a": {"benchmark_turn_id": "D1:1"},
        "raw-b": {"benchmark_turn_id": "D2:5"},
        "pas-a": {"benchmark_evidence_turn_ids": ["D3:2"]},
        "pas-b": {"benchmark_evidence_turn_ids": ["D4:1", "D4:2"]},
        "pas-c": {"benchmark_evidence_turn_ids": ["D5:9"]},
        # fallback: nessun benchmark_evidence, ma source_turn_ids canonici
        "pas-d": {"temporal_context": {"source_turn_ids": ["D6:1"]}},
    }
    load = lambda i: docs.get(i, {})  # noqa: E731
    assert hydrate_base_turn_ids(nodes, load) == ["D1:1", "D2:5"]
    # Q=2: solo i primi due nodi passive, in ordine di posizione
    assert hydrate_locator_notes(nodes, load) == [["D3:2"], ["D4:1", "D4:2"]]
    # fallback ai source_turn_ids canonici quando manca benchmark_evidence
    fallback_nodes = [{"id": "pas-d", "kind": "memory", "source": "passive", "position": 0}]
    assert hydrate_locator_notes(fallback_nodes, load) == [["D6:1"]]


def test_census_includes_adversarial_and_reports_exclusions():
    if not OFFICIAL.is_file():
        return
    census = build_census(OFFICIAL)
    assert census["universe"] == ["conv-41", "conv-44", "conv-48", "conv-49", "conv-50"]
    assert census["totals"]["adversarial"] > 0  # avversariali incluse
    assert census["totals"]["answerable"] > 0
    for conv in census["conversations"]:
        for ex in conv["excluded"]:
            assert ex["reason"] == "evidence_gold_non_nel_corpus"


def test_structural_dry_run_invariants():
    if not OFFICIAL.is_file():
        return
    res = structural_dry_run(source=OFFICIAL)
    assert res["all_invariants_ok"] is True
    assert res["forecast"]["pairs_total"] == 5 * 2


def _dev_available() -> bool:
    return (DEV_RUN / "runs").is_dir() and (DEV_RUN / "localizations").is_dir()


def test_dev_reproduction_with_definitive_renderer():
    if not (_dev_available() and OFFICIAL.is_file()):
        return
    corpus = {c.sample_id: c for c in LoCoMoAdapter().load(OFFICIAL)}
    speaker = {sid: {t.turn_id: t.speaker for t in case.turns} for sid, case in corpus.items()}
    loc = {}
    for p in sorted((DEV_RUN / "localizations").glob("*.it.json")):
        loc[p.name[: -len(".it.json")]] = json.loads(p.read_text()).get("turns", {})

    n = final_hits = exclusive = gold_lost = 0
    for path in sorted((DEV_RUN / "runs").glob("*.json")):
        rep = json.loads(path.read_text())
        sample = rep["dataset"]["sample_id"]
        prof = {pr["profile"]["name"]: pr for pr in rep["profiles"]}
        gold_by_q = {g["question_id"]: g for g in rep["evaluation_gold"]}
        rag = {r["question_id"]: r for r in prof["rag_only"]["results"]}
        pas = {r["question_id"]: r for r in prof["passive_memory"]["results"]}
        for qid, g in gold_by_q.items():
            if g.get("expected_answer") is None or not g.get("evidence_turn_ids"):
                continue
            n += 1
            gold = [str(t) for t in g["evidence_turn_ids"]]
            rn = rag[qid].get("metadata", {}).get("retrieval_nodes", [])
            base_turns = [str(t) for node in sorted(rn, key=lambda x: x.get("position", 0))
                          if node.get("source") == "conversation"
                          for t in (node.get("evidence_turn_ids") or [])]
            base_chars = rag[qid].get("metadata", {}).get("rag_chars", 0)
            pn = pas[qid].get("metadata", {}).get("retrieval_nodes", [])
            notes = locators_from_nodes(pn)

            def render(tid, s=sample):
                return f"{speaker.get(s, {}).get(tid, '?')}: {loc.get(s, {}).get(tid, '')}"

            comp = compose_dual_channel(
                base_context_text="x" * base_chars,
                base_slots=len(rn),
                base_turn_ids=base_turns,
                locator_notes=notes,
                render_turn=render,
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
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_dual_channel: OK")
