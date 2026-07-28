#!/usr/bin/env python3
"""Regressioni pure dell'ablation prompt v2 (nessun modello/Redis/retrieval).

Copre le guardie dell'hardening: case_id canonico, seed per-caso, 7 arm,
ordine controbilanciato, selettore fail-closed, integrità, cattura, audit cieco.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from benchmarks.euri_memory import prompt_ablation_v2 as P


ROOT = Path(__file__).resolve().parent
VAL = ROOT / "audit_output" / "dual_channel_validation_v1_seed396895560"
VAL_RUNS = VAL / "run" / "runs"
LOC = VAL / "localization_it.json"
CORPUS = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"


def _available():
    return VAL_RUNS.is_dir()


def _manifest():
    return P.build_case_manifest(validation_runs_dir=VAL_RUNS)


def test_case_ids_seeds_arms_and_blindness():
    if not _available():
        return
    m = _manifest()
    P.verify_manifest(m)
    cc = m["strata_counts"]
    assert cc["total"] == 129 and cc["distinct_case_ids"] == 129
    assert cc["distinct_question_ids"] == 112  # 17 duplicati fra repliche
    assert cc["total"] - cc["distinct_question_ids"] == 17
    # baseline vs commit sperimentale (punto 7)
    assert m["production_baseline_commit"] == "bac00a0" and "git_commit" not in m
    # 7 arm che isolano think dal budget (punto 3)
    assert m["arms"] == ["A0", "A2", "A1", "B0", "B2", "B1", "C0"]
    assert m["arm_factors"]["A2"] == "strict/no-think/2000"
    assert m["arm_factors"]["A1"] == "strict/think/2000"
    # seed per-caso = census per replica (punto 2)
    seeds = {c["replica"]: c["answer_seed"] for rows in m["strata"].values() for c in rows}
    assert seeds == {"0": 19960177, "1": 1395183426}
    # case_id canonico
    c = m["strata"]["A_evidence_flip"][0]
    assert c["case_id"] == P.case_id(c["conversation"], c["replica"], c["question_id"])
    # cieco
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
    assert _manifest()["manifest_sha256"] == m["manifest_sha256"]  # determinismo


def test_forecast_is_1032():
    if not _available():
        return
    fc = P.forecast(_manifest())
    assert fc["answer_generations"] == 903 and fc["selector_calls_C0"] == 129
    assert fc["total_max_calls"] == 1032 == fc["cap_max_calls"]


def test_counterbalanced_order_is_balanced_permutation():
    if not _available():
        return
    m = _manifest()
    firsts = {}
    for rows in m["strata"].values():
        for c in rows:
            assert sorted(c["arm_order"]) == sorted(P.ARM_NAMES)  # permutazione completa
            firsts[c["arm_order"][0]] = firsts.get(c["arm_order"][0], 0) + 1
    # ogni arm è primo un numero simile di volte (129/7 ≈ 18)
    assert max(firsts.values()) - min(firsts.values()) <= 1


def test_selector_fail_closed():
    ok = P.parse_selector_strict('{"answerable": true, "supporting_fragments": [1, 3]}', 5)
    assert ok == {"answerable": True, "supporting_fragments": [1, 3]}
    # answerable non booleano
    assert P.parse_selector_strict('{"answerable": "yes", "supporting_fragments": []}', 5) is None
    # indice fuori range / duplicato / non intero
    assert P.parse_selector_strict('{"answerable": true, "supporting_fragments": [9]}', 5) is None
    assert P.parse_selector_strict('{"answerable": true, "supporting_fragments": [1, 1]}', 5) is None
    assert P.parse_selector_strict('{"answerable": true, "supporting_fragments": [1.5]}', 5) is None
    # JSON invalido
    assert P.parse_selector_strict("non json", 5) is None


def test_build_messages_has_no_gold_param_and_is_reconstructible():
    ctx = "OwnerUser: uno\nAssistant: due"
    msgs = P.build_messages("strict", context_text=ctx, question="Q?")
    # nessun parametro gold/evidence nella firma
    import inspect
    params = set(inspect.signature(P.build_messages).parameters)
    assert not (params & {"gold", "evidence", "expected_answer", "answer"})
    # ricostruibile dagli hash (payload deterministico)
    assert P.messages_payload_sha256(msgs) == P.messages_payload_sha256(
        P.build_messages("strict", context_text=ctx, question="Q?"))


def test_guards_redis_capture_context():
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
        P.assert_no_personal_redis()
    finally:
        if old is not None:
            os.environ["EURI_REDIS_PORT"] = old
        else:
            os.environ.pop("EURI_REDIS_PORT", None)
    # capture dir deve stare sotto audit_output
    P.assert_capture_dir_under_audit(P.AUDIT_ROOT / "x")
    try:
        P.assert_capture_dir_under_audit(Path("/tmp/altrove"))
    except P.AblationError:
        pass
    else:
        raise AssertionError("capture_dir fuori da audit_output accettata")
    # contesto assente/corrotto
    P.assert_context_matches("c", P._sha("c"))
    for bad in ("", "x"):
        try:
            P.assert_context_matches(bad, P._sha("c"))
        except P.AblationError:
            pass
        else:
            raise AssertionError("contesto invalido accettato")


def test_execution_manifest_binds_baseline_and_sources():
    if not _available():
        return
    m = _manifest()
    ex = P.build_execution_manifest(m, experimental_code_commit="deadbeef", corpus_path=CORPUS,
                                    localization_path=LOC, validation_runs_dir=VAL_RUNS)
    P.verify_manifest(ex)
    assert ex["stage"] == "execution"
    assert ex["case_manifest_sha256"] == m["manifest_sha256"]
    assert ex["production_baseline_commit"] == "bac00a0"
    assert ex["git_commit"] == "deadbeef"  # commit sperimentale, non baseline
    assert len(ex["census_report_sha256"]) == 10
    assert len(ex["cases"]) == 129


def _sign(manifest):
    manifest["manifest_sha256"] = P.manifest_digest(manifest)
    return manifest


def test_checkpoint_revalidation():
    m = _sign({"stage": "execution", "corpus": {"sha256": "c"}, "git_commit": "g",
               "cases": [{"case_id": "x__r0__q1"}]})
    identity = P.run_identity(m)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        runs = out / "runs"; runs.mkdir()
        cp = out / "checkpoint.json"
        # nessun file → done vuoto
        assert P._load_and_revalidate_checkpoint(cp, m, identity, runs)["done"] == []
        # identity incompleta → rifiuto
        cp.write_text(json.dumps({"done": []}))
        try:
            P._load_and_revalidate_checkpoint(cp, m, identity, runs)
        except P.AblationError:
            pass
        else:
            raise AssertionError("checkpoint senza identity accettato")
        # case_id estraneo → rifiuto
        cp.write_text(json.dumps({"identity": identity, "done": ["conv-99__r0__q9"]}))
        try:
            P._load_and_revalidate_checkpoint(cp, m, identity, runs)
        except P.AblationError:
            pass
        else:
            raise AssertionError("case_id estraneo accettato")
        # report mancante → rifiuto
        cp.write_text(json.dumps({"identity": identity, "done": ["x__r0__q1"]}))
        try:
            P._load_and_revalidate_checkpoint(cp, m, identity, runs)
        except P.AblationError:
            pass
        else:
            raise AssertionError("report mancante accettato")


def _synthetic_runs(dirpath, cases):
    """cases: list of (case_id, qid, replica, stratum, {arm: answer})."""
    for cid, qid, rep, stratum, arms in cases:
        (dirpath / f"{cid}.json").write_text(json.dumps({
            "case_id": cid, "question_id": qid, "replica": rep, "stratum": stratum,
            "conversation": cid.split("__")[0], "arm_order": list(P.ARM_NAMES),
            "arms": [{"arm": a, "answer": ans} for a, ans in arms.items()],
        }), encoding="utf-8")


def test_analyze_and_blind_audit_synthetic():
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d) / "runs"; runs.mkdir()
        gold = {"q1": {"answer": "roma", "answerable": True, "question": "dove?", "category": "single_hop"},
                "q2": {"answer": None, "answerable": False, "question": "quando?", "category": "adversarial"}}
        _synthetic_runs(runs, [
            ("conv-41__r0__q1", "q1", "0", "A_evidence_flip",
             {a: ("roma" if a in ("B0", "B1") else "Non lo so.") for a in P.ARM_NAMES}),
            ("conv-41__r0__q2", "q2", "0", "C_adversarial",
             {a: "Non lo so." for a in P.ARM_NAMES}),
        ])
        # execution manifest minimale con quei casi
        ex = _sign({"stage": "execution", "manifest_sha256": None, "corpus": {"sha256": "c"},
                    "git_commit": "g", "cases": [
                        {"case_id": "conv-41__r0__q1", "conversation": "conv-41", "replica": "0", "question_id": "q1"},
                        {"case_id": "conv-41__r0__q2", "conversation": "conv-41", "replica": "0", "question_id": "q2"}]})
        # census report finto per a0_stability (stesso seed → old A0 = dual answer)
        vroot = Path(d) / "vroot"; (vroot / "runs").mkdir(parents=True)
        (vroot / "runs" / "conv-41__r0.json").write_text(json.dumps({
            "dataset": {"sample_id": "conv-41"}, "run": {"run_label": "conv-41__r0", "answer_seed": 19960177},
            "arms": [{"arm": "dual_channel", "results": [
                {"question_id": "q1", "answer": "Non lo so."}, {"question_id": "q2", "answer": "Non lo so."}]}]}))
        rep = P.analyze(output_runs=runs, gold_lookup=gold, validation_root=vroot, execution_manifest=ex)
        # B0/B1 rispondono "roma" → F1 alto; A0 astiene → false abstention
        assert rep["per_stratum_arm"]["A_evidence_flip"]["B0"]["token_f1"] == 1.0
        assert rep["per_stratum_arm"]["A_evidence_flip"]["A0"]["false_abstention"] == 1.0
        # paired vs A0: B0 cambia e migliora
        assert rep["paired_vs_a0"]["B0"]["improved"] == 1
        # a0 stability: fresh A0 astiene = old A0 astiene → identiche
        assert rep["a0_stability"]["same_seed"] is True

        # audit cieco: codici non riconducibili all'arm, chiave separata
        au = P.blind_audit_export(output_runs=runs, gold_lookup=gold)
        assert au["rows"] and all(set(r) >= {"code", "question", "gold", "answer", "replica"} for r in au["rows"])
        for r in au["rows"]:
            assert r["code"] in au["key"]
            assert not any(arm in r["code"] for arm in P.ARM_NAMES)  # non reversibile dal nome
        assert all("arm" not in r for r in au["rows"])  # arm solo nella key


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_prompt_ablation_v2: OK")
