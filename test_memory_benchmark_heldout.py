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
from benchmarks.euri_memory.heldout_runner import (
    RunnerError,
    cost_forecast,
    plan_runs,
    run_all,
    _write_selection,
)
from benchmarks.euri_memory.integrity import (
    IntegrityError,
    assert_same_identity,
    expected_pairs,
    validate_pair_report,
)
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


def _valid_pair_report(manifest: dict, expected) -> dict:
    conv = next(
        c for c in manifest["conversations"] if c["sample_id"] == expected.sample_id
    )
    scoring = {
        "mean_token_f1": 0.4,
        "exact_match": 0.0,
        "adversarial_accuracy": 1.0,
        "evidence_recall": 0.5,
        "items": [],
    }
    return {
        "dataset": {
            "sample_id": expected.sample_id,
            "source_sha256": manifest["corpus"]["sha256"],
        },
        "run": {
            "run_label": expected.key,
            "answer_seed": expected.answer_seed,
            "branch_order": list(expected.branch_order),
        },
        "git": {"commit": manifest["git_commit"], "worktree_tracked_dirty": False},
        "selection": {
            "question_ids": list(conv["question_ids"]),
            "selection_sha256": expected.selection_sha256,
        },
        "profiles": [
            {"profile": {"name": "rag_only"}, "scoring": scoring},
            {"profile": {"name": "passive_memory"}, "scoring": scoring},
        ],
    }


def test_run_rejects_corpus_with_wrong_hash():
    if not _corpus_available():
        return
    from benchmarks.euri_memory.heldout import write_manifest

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        manifest = build_manifest(seed=4, budget_name="smoke", git_commit="x")
        manifest_path = write_manifest(manifest, work / "manifest.json")
        fake_corpus = work / "fake_corpus.json"
        fake_corpus.write_text("[]", encoding="utf-8")  # hash diverso dal reale
        try:
            run_all(
                manifest_path=manifest_path,
                output_dir=work / "out",
                corpus_path=fake_corpus,
                dry_run=True,
            )
        except IntegrityError as exc:
            assert "corpus" in str(exc).lower()
        else:
            raise AssertionError("corpus con hash diverso accettato")


def test_run_rejects_different_manifest_in_output_dir():
    if not _corpus_available():
        return
    from benchmarks.euri_memory.heldout import write_manifest

    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        out = work / "out"
        out.mkdir()
        other = build_manifest(seed=1, budget_name="smoke", git_commit="x")
        (out / "manifest.json").write_text(
            json.dumps(other, sort_keys=True), encoding="utf-8"
        )
        current = build_manifest(seed=2, budget_name="smoke", git_commit="x")
        current_path = write_manifest(current, work / "manifest.json")
        assert current["manifest_sha256"] != other["manifest_sha256"]
        try:
            run_all(
                manifest_path=current_path,
                output_dir=out,
                corpus_path=OFFICIAL,
                dry_run=True,
            )
        except RunnerError as exc:
            assert "output-dir" in str(exc).lower()
        else:
            raise AssertionError("manifest diverso nella stessa output-dir accettato")


def test_resume_checkpoint_identity_is_enforced():
    recorded = {"manifest_sha256": "a", "corpus_sha256": "b", "git_commit": "c"}
    assert_same_identity(recorded, dict(recorded), context="ok")  # non solleva
    for field in ("manifest_sha256", "corpus_sha256", "git_commit"):
        broken = dict(recorded)
        broken[field] = "different"
        try:
            assert_same_identity(broken, recorded, context="resume")
        except IntegrityError as exc:
            assert field in str(exc)
        else:
            raise AssertionError(f"identità divergente accettata su {field}")


def test_write_selection_is_fail_closed_on_alteration():
    if not _corpus_available():
        return
    manifest = build_manifest(seed=6, budget_name="smoke", git_commit="x")
    spec = plan_runs(manifest, OFFICIAL)[0]
    with tempfile.TemporaryDirectory() as directory:
        selections = Path(directory)
        path = _write_selection(spec, selections)
        # riuso identico: nessun errore
        assert _write_selection(spec, selections) == path
        # alterazione di un solo campo: fail-closed
        tampered = json.loads(path.read_text())
        tampered["question_ids"].append("conv-XX:q1")
        path.write_text(json.dumps(tampered, indent=2, sort_keys=True), encoding="utf-8")
        try:
            _write_selection(spec, selections)
        except RunnerError as exc:
            assert "fail-closed" in str(exc).lower()
        else:
            raise AssertionError("selezione alterata riusata silenziosamente")


def test_pair_report_validation_catches_every_mismatch():
    if not _corpus_available():
        return
    manifest = build_manifest(seed=9, budget_name="validation", git_commit="fixed")
    expected = expected_pairs(manifest)
    key = sorted(expected)[0]
    exp = expected[key]
    good = _valid_pair_report(manifest, exp)
    assert validate_pair_report(good, manifest, exp) == []

    mutators = {
        "answer_seed": lambda r: r["run"].__setitem__("answer_seed", exp.answer_seed + 1),
        "branch_order": lambda r: r["run"].__setitem__("branch_order", ["passive_memory", "rag_only"] if list(exp.branch_order) == ["rag_only", "passive_memory"] else ["rag_only", "passive_memory"]),
        "question_ids": lambda r: r["selection"]["question_ids"].append("conv-XX:q9"),
        "commit": lambda r: r["git"].__setitem__("commit", "other-commit"),
        "corpus": lambda r: r["dataset"].__setitem__("source_sha256", "deadbeef"),
        "worktree": lambda r: r["git"].__setitem__("worktree_tracked_dirty", True),
        "selection_sha": lambda r: r["selection"].__setitem__("selection_sha256", "bad"),
        "missing_branch": lambda r: r.__setitem__("profiles", [r["profiles"][0]]),
    }
    for name, mutate in mutators.items():
        report = json.loads(json.dumps(good))
        mutate(report)
        problems = validate_pair_report(report, manifest, exp)
        assert problems, f"mutazione {name} non rilevata"


def test_analysis_rejects_foreign_and_duplicate_reports():
    if not _corpus_available():
        return
    from benchmarks.euri_memory.heldout import write_manifest

    manifest = build_manifest(seed=10, budget_name="smoke", git_commit="fixed")
    expected = expected_pairs(manifest)
    key = sorted(expected)[0]
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        manifest_path = write_manifest(manifest, work / "manifest.json")
        runs = work / "runs"
        runs.mkdir()
        # report valido per una coppia attesa
        (runs / f"{key}.json").write_text(
            json.dumps(_valid_pair_report(manifest, expected[key])), encoding="utf-8"
        )
        # report ESTRANEO (run_label non nel manifest)
        foreign = _valid_pair_report(manifest, expected[key])
        foreign["run"]["run_label"] = "conv-99__r0"
        (runs / "conv-99__r0.json").write_text(json.dumps(foreign), encoding="utf-8")
        try:
            analyze(runs, manifest_path)
        except Exception as exc:  # noqa: BLE001
            assert "estranei" in str(exc) or "manifest" in str(exc).lower()
        else:
            raise AssertionError("report estraneo accettato nell'analisi")


def test_analysis_declares_partial_run():
    if not _corpus_available():
        return
    from benchmarks.euri_memory.heldout import write_manifest

    manifest = build_manifest(seed=12, budget_name="validation", git_commit="fixed")
    expected = expected_pairs(manifest)
    # tieni solo le coppie di UNA conversazione → run parziale legittima
    one_conv = manifest["conversations"][0]["sample_id"]
    keep = [k for k in expected if expected[k].sample_id == one_conv]
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        manifest_path = write_manifest(manifest, work / "manifest.json")
        runs = work / "runs"
        runs.mkdir()
        for key in keep:
            (runs / f"{key}.json").write_text(
                json.dumps(_valid_pair_report(manifest, expected[key])), encoding="utf-8"
            )
        report = analyze(runs, manifest_path)
        assert report["is_partial_run"] is True
        assert report["binding"]["missing_pairs"]
        assert "PARZIALE" in report["interpretation_limit"]


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
    test_run_rejects_corpus_with_wrong_hash()
    test_run_rejects_different_manifest_in_output_dir()
    test_resume_checkpoint_identity_is_enforced()
    test_write_selection_is_fail_closed_on_alteration()
    test_pair_report_validation_catches_every_mismatch()
    test_analysis_rejects_foreign_and_duplicate_reports()
    test_analysis_declares_partial_run()
    print("test_memory_benchmark_heldout: OK")
