#!/usr/bin/env python3
"""Regressioni pure dell'ablation prompt v2 (nessun modello, Redis o retrieval)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from benchmarks.euri_memory import prompt_ablation_v2 as P


ROOT = Path(__file__).resolve().parent
VAL_RUNS = ROOT / "audit_output" / "dual_channel_validation_v1_seed396895560" / "run" / "runs"


def _available():
    return VAL_RUNS.is_dir()


def _manifest():
    return P.build_case_manifest(
        validation_runs_dir=VAL_RUNS, git_commit="fixed",
        corpus_sha256="corpus", localization_sha256="loc",
    )


def test_prompt_sha_frozen_and_stable():
    a, b = P.prompt_sha256(), P.prompt_sha256()
    assert a == b and set(a) == {"strict", "balanced", "two_stage_selector", "two_stage_answer"}
    # cinque arm esatti
    assert [x.name for x in P.ARMS] == ["A0", "A1", "B0", "B1", "C0"]


def test_forecast_is_774():
    if not _available():
        return
    fc = P.forecast(_manifest())
    assert fc["answer_generations_five_arms"] == 645
    assert fc["selector_calls_C0"] == 129
    assert fc["total_max_calls"] == 774 == fc["cap_max_calls"]


def test_manifest_strata_and_blindness():
    if not _available():
        return
    m = _manifest()
    P.verify_manifest(m)
    assert m["strata_counts"] == {"A_evidence_flip": 43, "B_answerable_control": 43,
                                  "C_adversarial": 43, "total": 129}
    banned = {"answer", "expected_answer", "adversarial_answer", "gold"}

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                assert k not in banned, k
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(m)
    # determinismo
    assert _manifest()["manifest_sha256"] == m["manifest_sha256"]


def test_no_gold_in_model_messages():
    # build_messages non accetta gold; su un contesto SENZA il gold, il gold non c'è
    ctx = "OwnerUser: parliamo del più\nAssistant: certo"
    gold = "risposta segreta 42"
    for fam in ("strict", "balanced"):
        msgs = P.build_messages(fam, context_text=ctx, question="Qual è la risposta?")
        assert P.messages_forbidden_hits(msgs, [gold, "D3:2"]) == []
    # two-stage selettore: contesto numerato, nessun gold/evidence
    sel = P.build_messages("two_stage", context_text=ctx, question="Q?", stage="selector")
    assert P.messages_forbidden_hits(sel, [gold, "D3:2"]) == []


def test_shared_context_sha_across_arms():
    ctx = "riga uno\nriga due\nriga tre"
    shas = set()
    for arm in P.ARMS:
        if arm.family == "two_stage":
            continue
        msgs = P.build_messages(arm.family, context_text=ctx, question="Q?")
        meta = P.call_metadata(arm=arm, stage="single", context_text=ctx, question_id="q",
                               localization_sha256="loc", messages=msgs,
                               num_predict=160, model="m", model_digest=None)
        shas.add(meta["context_sha256"])
    assert len(shas) == 1  # base byte-per-byte identica fra gli arm


def test_two_stage_indices_are_reconstructible_and_validated():
    ctx = "alpha\nbeta\ngamma"
    sel, valid = P.select_by_indices(ctx, [1, 3, 99, -2])  # 99 e -2 fuori range → scartati
    assert valid == [1, 3]
    assert sel == "alpha\ngamma"
    parsed = P.parse_selector('{"answerable": true, "supporting_fragments": [2]}')
    assert parsed == {"answerable": True, "supporting_fragments": [2]}
    # JSON assente → conservativo
    assert P.parse_selector("boh")["answerable"] is False


def test_no_personal_redis_guard():
    old = os.environ.get("EURI_REDIS_PORT")
    try:
        os.environ["EURI_REDIS_PORT"] = "6379"
        try:
            P.assert_no_personal_redis()
        except P.AblationError:
            pass
        else:
            raise AssertionError("Redis personale accettato")
        os.environ.pop("EURI_REDIS_PORT", None)
        P.assert_no_personal_redis()  # assente → ok (l'ablation non usa Redis)
    finally:
        if old is not None:
            os.environ["EURI_REDIS_PORT"] = old
        else:
            os.environ.pop("EURI_REDIS_PORT", None)


def test_absent_or_corrupt_context_blocks():
    good = "contesto"
    P.assert_context_matches(good, P._sha(good))  # ok
    for bad in ("", "contesto alterato"):
        try:
            P.assert_context_matches(bad, P._sha(good))
        except P.AblationError:
            pass
        else:
            raise AssertionError(f"contesto {'assente' if not bad else 'corrotto'} accettato")


def test_output_dir_divergent_manifest_fails_closed():
    if not _available():
        return
    m = _manifest()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        other = dict(m)
        other["manifest_sha256"] = "different"
        (out / "manifest.json").write_text(json.dumps(other), encoding="utf-8")
        try:
            P.run_ablation(manifest=m, validation_root=VAL_RUNS.parent, output_dir=out,
                           capture_dir=out / "cap", execute=False)
        except P.AblationError as exc:
            assert "output-dir" in str(exc)
        else:
            raise AssertionError("manifest divergente accettato nell'output-dir")


def test_a0_regenerated_not_reused():
    # A0 è un arm generato fresco; niente riuso della vecchia risposta come baseline
    assert P.REUSE_PREVIOUS_A0 is False
    assert "A0" in [a.name for a in P.ARMS]
    a0 = next(a for a in P.ARMS if a.name == "A0")
    assert a0.answer_calls == 1 and a0.family == "strict" and a0.think is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_prompt_ablation_v2: OK")
