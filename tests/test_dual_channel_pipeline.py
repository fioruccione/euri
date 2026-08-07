#!/usr/bin/env python3
"""Regressioni pure della pipeline dual-channel (nessun Redis/LLM).

Census manifest cieco, manifest finale legato, validazione report↔manifest e
analisi clusterizzata N=5 su report sintetici.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.euri_memory import dual_channel_pipeline as P
from benchmarks.euri_memory.dual_channel import POLICY_ID
from benchmarks.euri_memory.heldout import verify_manifest
from benchmarks.euri_memory.heldout_localization import build_selected_localization


_SCORER = "locomo_reduced_deterministic_v1_not_official"


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"


def _available() -> bool:
    return OFFICIAL.is_file()


def _stub_localization(manifest: dict) -> dict:
    return build_selected_localization(
        corpus_path=OFFICIAL,
        selection_manifest=manifest,
        translate_fn=lambda text, kind: "IT: " + text,
        model="stub",
        model_version="v0",
    )


def _valid_report(manifest: dict, expected) -> dict:
    conv = next(c for c in manifest["conversations"] if c["sample_id"] == expected.sample_id)
    qids = list(conv["question_ids"])
    base_sha = {q: f"sha-{q}" for q in qids}

    def scoring(f1):
        return {
            "name": _SCORER,
            "mean_token_f1": f1,
            "exact_match": 0.0,
            "adversarial_accuracy": 1.0,
            "evidence_recall": 0.6,
            "items": [{"question_id": q, "category": "single_hop", "answerable": True,
                       "evidence_hit": True, "exact_match": False, "correct": True,
                       "token_f1": f1} for q in qids],
        }

    return {
        "policy_id": POLICY_ID,
        "dataset": {"sample_id": expected.sample_id, "source_sha256": manifest["corpus"]["sha256"]},
        "run": {"run_label": expected.key, "answer_seed": expected.answer_seed,
                "generation_order": list(expected.generation_order)},
        "models": {"answer_seed": expected.answer_seed},
        "git": {"commit": manifest["git_commit"], "worktree_tracked_dirty": False},
        "selection": {"question_ids": qids, "selection_sha256": expected.selection_sha256},
        "binding": {
            "manifest_sha256": manifest["manifest_sha256"],
            "selection_manifest_sha256": manifest.get("selection_manifest_sha256"),
            "localization_sha256": (manifest.get("localization") or {}).get("localization_sha256"),
            "localization_id": (manifest.get("localization") or {}).get("localization_id"),
            "language": "it",
        },
        "gold_boundary": {"retrieval_used_only_prompts": True, "generation_used_only_prompts": True,
                          "scorer_runs_after_generation": True},
        "base_nodes_by_question": {q: [] for q in qids},
        "locator_nodes_by_question": {q: [] for q in qids},
        "arms": [
            {"arm": "rag_only",
             "results": [{"question_id": q, "metadata": {"base_sha256": base_sha[q]}} for q in qids],
             "scoring": scoring(0.30)},
            {"arm": "dual_channel",
             "results": [{"question_id": q, "metadata": {"composition": {"policy_id": POLICY_ID, "base_sha256": base_sha[q]}}} for q in qids],
             "scoring": scoring(0.34)},
        ],
    }


def test_census_manifest_deterministic_and_blind():
    if not _available():
        return
    a = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    b = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    assert a["manifest_sha256"] == b["manifest_sha256"]
    verify_manifest(a)
    assert [c["sample_id"] for c in a["conversations"]] == ["conv-41", "conv-44", "conv-48", "conv-49", "conv-50"]
    assert a["n_independent"] == 5
    banned = {"answer", "expected_answer", "question", "text", "adversarial_answer"}

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                assert k not in banned, k
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(a)


def test_final_manifest_binds_and_validates_reports():
    if not _available():
        return
    census = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    loc = _stub_localization(census)
    final = P.build_dual_final_manifest(census, loc, corpus_path=OFFICIAL)
    verify_manifest(final)
    assert final["stage"] == "final" and final["language"] == "it"
    assert final["selection_manifest_sha256"] == census["manifest_sha256"]
    expected = P.expected_dual_pairs(final)
    exp = expected[sorted(expected)[0]]
    good = _valid_report(final, exp)
    assert P.validate_dual_report(good, final, exp) == []
    # ogni alterazione è rilevata
    for mutate in (
        lambda r: r["run"].__setitem__("answer_seed", exp.answer_seed + 1),
        lambda r: r["run"].__setitem__("generation_order", list(reversed(exp.generation_order))),
        lambda r: r["binding"].__setitem__("localization_sha256", "x"),
        lambda r: r["binding"].__setitem__("language", "en"),
        lambda r: r["selection"]["question_ids"].append("conv-99:q9"),
        lambda r: r["arms"].append({"arm": "extra", "scoring": r["arms"][0]["scoring"]}),
        lambda r: r["arms"][0]["scoring"]["items"].pop(),
    ):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        assert P.validate_dual_report(bad, final, exp), mutate


def test_analysis_clustered_on_synthetic_reports(tmp_path=None):
    if not _available():
        return
    import tempfile

    census = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    loc = _stub_localization(census)
    final = P.build_dual_final_manifest(census, loc, corpus_path=OFFICIAL)
    expected = P.expected_dual_pairs(final)
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(final), encoding="utf-8")
        runs = work / "runs"
        runs.mkdir()
        for key, exp in expected.items():
            runs.joinpath(f"{key}.json").write_text(json.dumps(_valid_report(final, exp)), encoding="utf-8")
        report = P.analyze(runs, manifest_path)
        assert report["n_conversations"] == 5
        assert report["is_partial_run"] is False
        assert report["direction"] == "dual_channel_minus_rag_only"
        # dual F1 sempre +0.04 -> delta positivo su ogni conversazione
        f1 = report["primary_metrics"]["mean_token_f1"]["per_conversation_delta"]
        assert all(abs(v - 0.04) < 1e-9 for v in f1.values())
        assert set(report["f1_delta_per_conversation"]) == set(report["conversations"])
        assert report["power"]["underpowered"] is True  # N=5 < 10
        # verdetto congelato: F1>0, 5/5 non-negative, adv delta 0, gold_lost 0 -> GO
        assert report["gold_lost"]["count"] == 0 and report["gold_lost"]["invariant_holds"] is True
        assert "adversarial_correct" in report["secondary_mcnemar_exact"]
        assert report["verdict"]["value"] == "GO"


def test_verdict_no_go_on_gold_lost():
    if not _available():
        return
    import tempfile

    census = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    loc = _stub_localization(census)
    final = P.build_dual_final_manifest(census, loc, corpus_path=OFFICIAL)
    expected = P.expected_dual_pairs(final)
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(final), encoding="utf-8")
        runs = work / "runs"
        runs.mkdir()
        for i, (key, exp) in enumerate(expected.items()):
            rep = _valid_report(final, exp)
            if i == 0:  # un item: RAG recupera l'evidence, il dual la perde -> gold_lost
                rep["arms"][1]["scoring"]["items"][0]["evidence_hit"] = False
            runs.joinpath(f"{key}.json").write_text(json.dumps(rep), encoding="utf-8")
        report = P.analyze(runs, manifest_path)
        assert report["gold_lost"]["count"] >= 1
        assert report["gold_lost"]["invariant_holds"] is False
        assert report["verdict"]["value"] == "NO-GO"


def test_cli_dual_manifest_requires_seed_and_records_head():
    if not _available():
        return
    import subprocess
    import sys
    import tempfile

    # senza --seed -> errore
    proc = subprocess.run(
        [sys.executable, "-m", "benchmarks.euri_memory.cli", "dual-manifest",
         "--source", str(OFFICIAL), "--output", "/tmp/should-not-write.json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0 and "seed" in (proc.stderr + proc.stdout).lower()

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "census.json"
        run = subprocess.run(
            [sys.executable, "-m", "benchmarks.euri_memory.cli", "dual-manifest",
             "--seed", "3", "--source", str(OFFICIAL), "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stderr
        manifest = json.loads(out.read_text())
        assert manifest["git_commit"] == head and head
        assert manifest["seed"] == 3


def test_analysis_rejects_foreign_report():
    if not _available():
        return
    import tempfile

    census = P.build_census_manifest(seed=7, corpus_path=OFFICIAL, git_commit="fixed")
    loc = _stub_localization(census)
    final = P.build_dual_final_manifest(census, loc, corpus_path=OFFICIAL)
    expected = P.expected_dual_pairs(final)
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        manifest_path = work / "manifest.json"
        manifest_path.write_text(json.dumps(final), encoding="utf-8")
        runs = work / "runs"
        runs.mkdir()
        foreign = _valid_report(final, expected[sorted(expected)[0]])
        foreign["run"]["run_label"] = "conv-99__r0"
        runs.joinpath("conv-99__r0.json").write_text(json.dumps(foreign), encoding="utf-8")
        try:
            P.analyze(runs, manifest_path)
        except Exception as exc:  # noqa: BLE001
            assert "estranei" in str(exc)
        else:
            raise AssertionError("report estraneo accettato")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_dual_channel_pipeline: OK")
