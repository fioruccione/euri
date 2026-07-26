#!/usr/bin/env python3
"""Regressioni pure della valutazione held-out della memoria passiva.

Nessun Redis, nessun Ollama, nessun hardware: selettore, forecast strutturale e
analisi appaiata sono verificabili in isolamento. Il dry-run del runner (che
avvia un Redis effimero) sta nel test di integrazione.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.analysis import (
    analyze,
    cluster_bootstrap_ci,
    mcnemar_exact,
)
from benchmarks.euri_memory.heldout import (
    BUDGETS,
    GUARD_EXCLUDED_SAMPLE_IDS,
    build_manifest,
    dev_excluded_sample_ids,
    manifest_digest,
    verify_manifest,
)
from benchmarks.euri_memory.heldout_runner import cost_forecast, plan_runs
from benchmarks.euri_memory.selection import BenchmarkSelection


ROOT = Path(__file__).resolve().parent
OFFICIAL = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"
_CATEGORIES = ("single_hop", "temporal", "multi_hop", "open_domain", "adversarial")


def _corpus_available() -> bool:
    return OFFICIAL.is_file()


def _available_by_conversation() -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for case in LoCoMoAdapter().load(OFFICIAL):
        known = {turn.turn_id for turn in case.turns}
        hist: dict[str, int] = defaultdict(int)
        for question in case.questions:
            # Coerente col selettore: escludi le domande con evidence gold assente.
            if set(question.evidence_turn_ids) - known:
                continue
            hist[question.category] += 1
        counts[case.sample_id] = dict(hist)
    return counts


def test_seed_is_mandatory_without_default():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.euri_memory.cli",
            "heldout-select",
            "--budget",
            "smoke",
            "--output",
            "/tmp/should-not-be-written.json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "seed" in (proc.stderr + proc.stdout).lower()


def test_manifest_is_deterministic_and_seed_sensitive():
    if not _corpus_available():
        return
    a = build_manifest(seed=7, budget_name="validation", git_commit="fixed")
    b = build_manifest(seed=7, budget_name="validation", git_commit="fixed")
    assert a["manifest_sha256"] == b["manifest_sha256"]
    assert a["conversations"] == b["conversations"]
    assert a["replicas"] == b["replicas"]
    c = build_manifest(seed=8, budget_name="validation", git_commit="fixed")
    assert c["manifest_sha256"] != a["manifest_sha256"]


def test_dev_selections_are_never_in_heldout():
    if not _corpus_available():
        return
    excluded = dev_excluded_sample_ids()
    assert GUARD_EXCLUDED_SAMPLE_IDS <= excluded
    assert {"conv-26", "conv-42"} <= excluded
    for seed in range(60):
        manifest = build_manifest(seed=seed, budget_name="extended", git_commit="x")
        chosen = {c["sample_id"] for c in manifest["conversations"]}
        assert not (chosen & excluded)


def test_stratification_matches_availability():
    if not _corpus_available():
        return
    available = _available_by_conversation()
    per_category = BUDGETS["validation"].per_category
    manifest = build_manifest(seed=3, budget_name="validation", git_commit="x")
    for conversation in manifest["conversations"]:
        sample_id = conversation["sample_id"]
        for category in _CATEGORIES:
            got = conversation["category_histogram"][category]
            expected = min(per_category, available[sample_id].get(category, 0))
            assert got == expected, (sample_id, category, got, expected)


def test_replicas_pairing_and_answer_seeds():
    if not _corpus_available():
        return
    manifest = build_manifest(seed=11, budget_name="validation", git_commit="x")
    replicas = manifest["replicas"]
    assert len(replicas) == BUDGETS["validation"].num_replicas
    seeds = [r["answer_seed"] for r in replicas]
    assert len(set(seeds)) == len(seeds)  # distinti fra repliche
    for replica in replicas:
        assert sorted(replica["branch_order"]) == ["passive_memory", "rag_only"]
    # con 3 repliche l'ordine dei bracci deve alternare: entrambi gli inizi
    starts = {tuple(r["branch_order"])[0] for r in replicas}
    assert starts == {"rag_only", "passive_memory"}


def test_evidence_is_covered_by_ingested_sessions():
    if not _corpus_available():
        return
    cases = list(LoCoMoAdapter().load(OFFICIAL))
    manifest = build_manifest(seed=5, budget_name="validation", git_commit="x")
    for conversation in manifest["conversations"]:
        selection = BenchmarkSelection(
            selection_id="probe",
            dataset="locomo",
            sample_id=conversation["sample_id"],
            session_ids=tuple(conversation["session_ids"]),
            question_ids=tuple(conversation["question_ids"]),
            speaker_mapping={},
            metadata={},
        )
        # Non solleva: tutta l'evidence gold cade dentro le sessioni ingerite.
        selection.apply(cases)


def test_manifest_immutability_and_blindness():
    if not _corpus_available():
        return
    manifest = build_manifest(seed=1, budget_name="smoke", git_commit="x")
    verify_manifest(manifest)  # non solleva
    tampered = json.loads(json.dumps(manifest))
    tampered["conversations"][0]["question_ids"].append("conv-99:q999")
    try:
        verify_manifest(tampered)
    except Exception as exc:  # noqa: BLE001
        assert "manifest" in str(exc).lower()
    else:
        raise AssertionError("manifest alterato accettato")

    forbidden_keys = {"answer", "expected_answer", "question", "text", "adversarial_answer"}

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden_keys, key
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(manifest)


def test_forecast_is_structural_only():
    if not _corpus_available():
        return
    manifest = build_manifest(seed=2, budget_name="validation", git_commit="x")
    forecast = cost_forecast(manifest, OFFICIAL)
    assert forecast["pairs_total"] == len(manifest["conversations"]) * len(
        manifest["replicas"]
    )
    assert forecast["exact"]["total_extraction_windows"] > 0
    assert forecast["estimated_llm_calls"]["low"] <= forecast["estimated_llm_calls"]["high"]
    # specifica dei run pianificati coerente col manifest
    specs = plan_runs(manifest, OFFICIAL)
    assert len(specs) == forecast["pairs_total"]


def test_mcnemar_exact_matches_known_values():
    assert mcnemar_exact(0, 0)["p_value"] == 1.0
    # b=c=1: coppie discordanti simmetriche → p=1.0
    assert mcnemar_exact(1, 1)["p_value"] == 1.0
    # b=6,c=0: 2 * 0.5**6 = 0.03125
    assert abs(mcnemar_exact(6, 0)["p_value"] - 0.03125) < 1e-9


def test_cluster_bootstrap_is_deterministic_and_clusters_by_conversation():
    values = [0.10, -0.02, 0.30]
    first = cluster_bootstrap_ci(values)
    second = cluster_bootstrap_ci(values)
    assert first == second
    assert first["n_clusters"] == 3
    assert first["unit"] == "conversation"
    assert abs(first["point_estimate"] - sum(values) / 3) < 1e-9
    assert first["ci_low"] <= first["point_estimate"] <= first["ci_high"]


def _synthetic_report(sample_id: str, run_label: str, *, base: dict, treat: dict) -> dict:
    def profile(name: str, scoring: dict) -> dict:
        return {
            "profile": {"name": name},
            "scoring": scoring,
            "llm": {"calls": scoring.get("_calls", 10), "eval_count": 100, "prompt_eval_count": 200},
            "elapsed_ms": 1000.0,
            "ingest": {"passive": scoring.get("_passive")},
            "database_before_questions": {"memories": 0},
            "database_after_questions": {"memories": scoring.get("_saved", 0)},
        }

    return {
        "dataset": {"sample_id": sample_id},
        "run": {"run_label": run_label},
        "profiles": [profile("rag_only", base), profile("passive_memory", treat)],
    }


def test_analysis_reports_per_conversation_deltas_and_power():
    def scoring(f1, adv, ev, items, **extra):
        return {
            "mean_token_f1": f1,
            "exact_match": 0.0,
            "adversarial_accuracy": adv,
            "evidence_recall": ev,
            "items": items,
            **extra,
        }

    def items(evidence_hits, adversarial_correct):
        rows = []
        for i, hit in enumerate(evidence_hits):
            rows.append(
                {
                    "question_id": f"q{i}",
                    "answerable": True,
                    "evidence_hit": hit,
                    "exact_match": False,
                    "correct": True,
                    "token_f1": 0.5,
                }
            )
        for j, correct in enumerate(adversarial_correct):
            rows.append(
                {
                    "question_id": f"adv{j}",
                    "answerable": False,
                    "evidence_hit": None,
                    "exact_match": None,
                    "correct": correct,
                    "token_f1": None,
                }
            )
        return rows

    with tempfile.TemporaryDirectory() as directory:
        runs = Path(directory)
        # conv-A: passive migliora nettamente; conv-B,-C: neutre/leggere.
        (runs / "conv-A__r0.json").write_text(
            json.dumps(
                _synthetic_report(
                    "conv-A",
                    "r0",
                    base=scoring(0.30, 1.0, 0.50, items([True, False], [True]), _calls=10),
                    treat=scoring(
                        0.50, 1.0, 0.90, items([True, True], [True]),
                        _calls=40, _passive={"extracted": 8, "saved": 6, "duplicates": 1,
                                             "saved_memories": [{"source_turn_ids": ["D1:1"]}]},
                        _saved=6,
                    ),
                )
            ),
            encoding="utf-8",
        )
        (runs / "conv-B__r0.json").write_text(
            json.dumps(
                _synthetic_report(
                    "conv-B",
                    "r0",
                    base=scoring(0.40, 1.0, 0.60, items([True], []), _calls=10),
                    treat=scoring(0.41, 1.0, 0.60, items([True], []), _calls=35,
                                  _passive={"extracted": 3, "saved": 3, "saved_memories": []}, _saved=3),
                )
            ),
            encoding="utf-8",
        )
        (runs / "conv-C__r0.json").write_text(
            json.dumps(
                _synthetic_report(
                    "conv-C",
                    "r0",
                    base=scoring(0.35, 1.0, 0.55, items([False], []), _calls=10),
                    treat=scoring(0.34, 1.0, 0.55, items([False], []), _calls=30,
                                  _passive={"extracted": 2, "saved": 2, "saved_memories": []}, _saved=2),
                )
            ),
            encoding="utf-8",
        )
        report = analyze(runs)
        assert report["n_conversations"] == 3
        assert report["independent_unit"] == "conversation"
        # delta per singola conversazione visibile: conv-A domina
        f1_deltas = report["primary_metrics"]["mean_token_f1"]["per_conversation_delta"]
        assert f1_deltas["conv-A"] > 0.15
        assert abs(f1_deltas["conv-B"]) < 0.05
        # con 3 cluster è sempre sotto-potenziato
        assert report["power"]["underpowered"] is True
        # costo: passive costa più chiamate del rag
        assert report["cost_and_fragmentation"]["extra_llm_calls_passive_vs_rag"] > 0


if __name__ == "__main__":
    test_seed_is_mandatory_without_default()
    test_manifest_is_deterministic_and_seed_sensitive()
    test_dev_selections_are_never_in_heldout()
    test_stratification_matches_availability()
    test_replicas_pairing_and_answer_seeds()
    test_evidence_is_covered_by_ingested_sessions()
    test_manifest_immutability_and_blindness()
    test_forecast_is_structural_only()
    test_mcnemar_exact_matches_known_values()
    test_cluster_bootstrap_is_deterministic_and_clusters_by_conversation()
    test_analysis_reports_per_conversation_deltas_and_power()
    print("test_memory_benchmark_heldout: OK")
