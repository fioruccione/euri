#!/usr/bin/env python3
"""Test puri del confronto RAG-thinking vs dual-thinking."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from benchmarks.euri_memory import thinking_memory_ab_v1 as T


ROOT = Path(__file__).resolve().parent
VALIDATION = ROOT / "audit_output" / "dual_channel_validation_v1_seed396895560"
SOURCE = ROOT / "benchmarks" / "euri_memory" / "prompt_ablation_v2_manifest.json"
SOURCE_REPO_PATH = Path("benchmarks/euri_memory/prompt_ablation_v2_manifest.json")


def _protocol_without_checkout_location(manifest: dict) -> dict:
    """Confronta il contratto congelato senza firmare la directory del runner.

    Il protocollo storico contiene il path assoluto della workstation originale.
    Quel campo documenta una posizione, non un parametro sperimentale: su un
    checkout GitHub Actions cambia necessariamente pur puntando allo stesso file,
    già vincolato da ``source_case_manifest_sha256``. Entrambi i manifest restano
    verificati con la propria firma prima di questa normalizzazione comparativa.
    """
    comparable = dict(manifest)
    comparable.pop("manifest_sha256", None)
    source_path = Path(str(comparable["source_case_manifest"]))
    expected_parts = SOURCE_REPO_PATH.parts
    assert source_path.parts[-len(expected_parts):] == expected_parts
    comparable["source_case_manifest"] = SOURCE_REPO_PATH.as_posix()
    return comparable


def test_protocol_is_signed_and_two_arms():
    protocol = T.protocol_manifest(SOURCE)
    T.verify_manifest(protocol)
    frozen = json.loads(
        (ROOT / "benchmarks" / "euri_memory" /
         "thinking_memory_ab_v1_protocol.json").read_text(encoding="utf-8")
    )
    T.verify_manifest(frozen)
    assert Path(protocol["source_case_manifest"]).resolve() == SOURCE.resolve()
    assert _protocol_without_checkout_location(protocol) == (
        _protocol_without_checkout_location(frozen)
    )
    # La firma dell'artefatto storico e dei risultati già prodotti non cambia.
    assert frozen["manifest_sha256"] == (
        "a44be8c257ec7c3dbe2defd36a1f05bd5f1042bc81dedfc9127d53f81efd9c25"
    )
    assert protocol["cases"] == 129
    assert protocol["arms"] == ["rag_think", "dual_think"]
    assert protocol["thinking"] is True
    assert protocol["num_predict"] == 2000
    assert protocol["max_calls"] == 258


def test_order_is_counterbalanced_and_seed_preserved():
    source = T._source_manifest(SOURCE)
    cases = T._source_cases(source)
    assert len(cases) == 129
    first = [case["arm_order"][0] for case in cases]
    assert abs(first.count("rag_think") - first.count("dual_think")) == 1
    assert {case["answer_seed"] for case in cases} == {19960177, 1395183426}


def test_dry_run_129_pairs_byte_exact():
    if not (VALIDATION / "run" / "runs").is_dir():
        return
    result = T.dry_run(
        protocol=T.protocol_manifest(SOURCE),
        source_path=SOURCE,
        validation_root=VALIDATION / "run",
    )
    assert result["byte_exact_ok"] is True
    assert result["byte_exact_pairs"] == 129
    assert result["failures"] == []


def test_fake_calls_differ_only_in_context():
    protocol = T.protocol_manifest(SOURCE)
    manifest = T._signed({
        "experiment": T.EXPERIMENT_ID,
        "stage": "execution",
        "manifest_sha256": None,
        "model": "gemma4:26b",
        "model_digest": "digest",
        "localization": {"sha256": "loc"},
        "cases": [],
    })
    case = {
        "case_id": "conv-41__r0__q1",
        "question_id": "conv-41:q1",
        "conversation": "conv-41",
        "replica": "0",
        "stratum": "A_evidence_flip",
        "answer_seed": 19960177,
        "arm_order": ["rag_think", "dual_think"],
    }
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"message": {"content": "Roma"}}

    T.AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=T.AUDIT_ROOT) as directory:
        records = []
        for arm, context in (
            ("rag_think", "Caroline: base"),
            ("dual_think", "Caroline: base\nStefano: aggiunta"),
        ):
            records.append(T._call_arm(
                arm=arm,
                context=context,
                question="Dove?",
                speakers=("Caroline", "Melanie"),
                case=case,
                manifest=manifest,
                reference_at="2026-07-27T10:00:00+02:00",
                execute=True,
                chat_fn=fake_chat,
                capture_dir=Path(directory),
            ))
        assert len(calls) == 2
        assert calls[0]["think"] is True and calls[1]["think"] is True
        assert calls[0]["options"] == calls[1]["options"]
        assert calls[0]["messages"][0] == calls[1]["messages"][0]
        assert calls[0]["messages"][1] != calls[1]["messages"][1]
        pair = {
            "rag": "Caroline: base",
            "dual": "Caroline: base\nStefano: aggiunta",
            "rag_sha256": T.P._sha("Caroline: base"),
            "dual_sha256": T.P._sha("Caroline: base\nStefano: aggiunta"),
            "context_reference_at": "2026-07-27T10:00:00+02:00",
            "added_turn_ids": ["D1:1"],
        }
        report = {
            **{key: case[key] for key in (
                "case_id", "question_id", "conversation", "replica", "stratum",
                "arm_order",
            )},
            "manifest_sha256": manifest["manifest_sha256"],
            "model": manifest["model"],
            "model_digest": manifest["model_digest"],
            "rag_sha256": pair["rag_sha256"],
            "dual_sha256": pair["dual_sha256"],
            "context_reference_at": pair["context_reference_at"],
            "added_turn_ids": pair["added_turn_ids"],
            "arms": records,
        }
        assert T.validate_report(
            report,
            case,
            manifest,
            pair=pair,
            question="Dove?",
            speakers=("Caroline", "Melanie"),
        ) == []


def test_verdict_is_frozen():
    assert T._verdict(0.01, 0.02, 0.0, 4) == "GO"
    assert T._verdict(0.0, 0.02, 0.0, 5) == "NO_GO"
    assert T._verdict(0.01, -0.01, 0.0, 5) == "NO_GO"
    assert T._verdict(0.01, 0.02, -0.03, 5) == "NO_GO"
    assert T._verdict(0.01, 0.02, 0.0, 3) == "INCONCLUSIVE"


def test_fake_end_to_end_129_cases_and_analysis():
    """Esegue l'intera pipeline su tutti i casi con un backend finto.

    Nessun modello o Redis: verifica checkpoint, report, resume-validation e
    analisi prima del primo run reale.
    """

    if not (VALIDATION / "run" / "runs").is_dir():
        return
    corpus = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"
    localization = VALIDATION / "localization_it.json"
    protocol = json.loads(
        (ROOT / "benchmarks" / "euri_memory" /
         "thinking_memory_ab_v1_protocol.json").read_text(encoding="utf-8")
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout.strip()
    manifest = T.build_execution_manifest(
        protocol=protocol,
        source_path=SOURCE,
        git_commit=head,
        corpus_path=corpus,
        localization_path=localization,
        validation_runs_dir=VALIDATION / "run" / "runs",
        model="gemma4:26b",
        model_digest="digest-test",
    )
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"message": {"content": "Non lo so."}}

    T.AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="thinking-memory-ab-fake-", dir=T.AUDIT_ROOT
    ) as directory:
        root = Path(directory)
        original_clean = T.assert_worktree_clean
        try:
            # Il test stesso è un file tracciato ancora dirty prima del commit;
            # tutte le altre guardie reali restano attive.
            T.assert_worktree_clean = lambda _root: None
            result = T.run(
                manifest=manifest,
                validation_root=VALIDATION / "run",
                output_dir=root / "run",
                capture_dir=root / "capture",
                execute=True,
                chat_fn=fake_chat,
            )
        finally:
            T.assert_worktree_clean = original_clean
        assert result["completed"] == 129
        assert len(calls) == 258
        analysis = T.analyze(
            manifest=manifest,
            validation_root=VALIDATION / "run",
            runs_dir=root / "run" / "runs",
            gold_lookup=T.P.build_gold_lookup(localization, corpus),
        )
        assert analysis["cases"] == 129
        assert analysis["verdict"] == "NO_GO"  # entrambi identici → delta 0
        assert analysis["cost_by_arm"]["rag_think"]["calls"] == 129
        assert analysis["cost_by_arm"]["dual_think"]["calls"] == 129


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_thinking_memory_ab_v1: OK")
