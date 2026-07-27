#!/usr/bin/env python3
"""Regressioni pure della pipeline dual-channel (nessun Redis/LLM).

Census manifest cieco, manifest finale legato, validazione report↔manifest e
analisi clusterizzata N=5 su report sintetici.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.euri_memory import dual_channel_pipeline as P
from benchmarks.euri_memory.heldout import verify_manifest
from benchmarks.euri_memory.heldout_localization import build_selected_localization


ROOT = Path(__file__).resolve().parent
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

    def scoring(f1):
        return {
            "mean_token_f1": f1,
            "exact_match": 0.0,
            "adversarial_accuracy": 1.0,
            "evidence_recall": 0.6,
            "items": [{"question_id": q, "answerable": True, "evidence_hit": True,
                       "exact_match": False, "correct": True, "token_f1": f1} for q in qids],
        }

    return {
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
            "language": "it",
        },
        "arms": [
            {"arm": "rag_only", "scoring": scoring(0.30)},
            {"arm": "dual_channel", "scoring": scoring(0.34)},
        ],
    }


def test_census_manifest_deterministic_and_blind():
    if not _available():
        return
    a = P.build_census_manifest(corpus_path=OFFICIAL, git_commit="fixed")
    b = P.build_census_manifest(corpus_path=OFFICIAL, git_commit="fixed")
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
    census = P.build_census_manifest(corpus_path=OFFICIAL, git_commit="fixed")
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

    census = P.build_census_manifest(corpus_path=OFFICIAL, git_commit="fixed")
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
        assert report["power"]["underpowered"] is True  # N=5 < 10


def test_analysis_rejects_foreign_report():
    if not _available():
        return
    import tempfile

    census = P.build_census_manifest(corpus_path=OFFICIAL, git_commit="fixed")
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
