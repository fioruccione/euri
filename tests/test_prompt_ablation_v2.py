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


ROOT = Path(__file__).resolve().parents[1]
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
    sp = ("Caroline", "Melanie")
    msgs = P.build_messages("strict", context_text=ctx, question="Q?", speakers=sp)
    # nessun parametro gold/evidence nella firma; gli speaker NON sono gold/evidence
    import inspect
    params = set(inspect.signature(P.build_messages).parameters)
    assert not (params & {"gold", "evidence", "expected_answer", "answer"})
    assert "speakers" in params
    # ricostruibile dagli hash (payload deterministico)
    assert P.messages_payload_sha256(msgs) == P.messages_payload_sha256(
        P.build_messages("strict", context_text=ctx, question="Q?", speakers=sp))


def test_a0_user_message_is_byte_identical_to_census_wrapper():
    """Blocker 1: il messaggio user degli arm risposta è byte-per-byte quello di
    dual_channel_worker._user_prompt (stesso wrapper 'Partecipanti: …')."""

    from benchmarks.euri_memory.dual_channel_worker import _user_prompt

    class _Case:
        speakers = ("Caroline", "Melanie")

    class _Prompt:
        text = "Dove sono andati in vacanza?"

    ctx = "Caroline: siamo stati a Roma\nMelanie: bellissimo"
    expected = _user_prompt(_Case(), _Prompt(), ctx)
    for family in ("strict", "balanced"):
        msgs = P.build_messages(family, context_text=ctx, question=_Prompt.text,
                                speakers=_Case.speakers)
        assert msgs[1]["content"] == expected, family
    # anche il payload SHA coincide col wrapper originale ricostruito
    a0_msgs = P.build_messages("strict", context_text=ctx, question=_Prompt.text,
                               speakers=_Case.speakers)
    assert P.messages_payload_sha256(a0_msgs) == P.messages_payload_sha256(
        [{"role": "system", "content": a0_msgs[0]["content"]},
         {"role": "user", "content": expected}])
    # fallback '(nessuna memoria rilevante)' identico quando il contesto è vuoto
    assert P.build_messages("strict", context_text="", question=_Prompt.text,
                            speakers=_Case.speakers)[1]["content"] == \
        _user_prompt(_Case(), _Prompt(), "")
    # C0 riceve coerentemente i partecipanti in entrambi gli stadi
    sel = P.build_messages("two_stage", context_text=ctx, question=_Prompt.text,
                           speakers=_Case.speakers, stage="selector")
    ans = P.build_messages("two_stage", context_text=ctx, question=_Prompt.text,
                           speakers=_Case.speakers, stage="answer", selected_fragments="frag")
    assert sel[1]["content"].startswith("Partecipanti: Caroline e Melanie.")
    assert ans[1]["content"].startswith("Partecipanti: Caroline e Melanie.")


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
                                    localization_path=LOC, validation_runs_dir=VAL_RUNS,
                                    model="gemma4:26b", model_digest="sha256:abc")
    P.verify_manifest(ex)
    assert ex["stage"] == "execution"
    assert ex["case_manifest_sha256"] == m["manifest_sha256"]
    assert ex["production_baseline_commit"] == "bac00a0"
    assert ex["git_commit"] == "deadbeef"  # commit sperimentale, non baseline
    assert len(ex["census_report_sha256"]) == 10
    assert len(ex["cases"]) == 129
    # blocker 2: modello e digest congelati nel manifest ed entrano nell'identità
    assert ex["model"] == "gemma4:26b" and ex["model_digest"] == "sha256:abc"
    ident = P.ablation_identity(ex)
    assert ident["model"] == "gemma4:26b" and ident["model_digest"] == "sha256:abc"
    # model/digest obbligatori
    try:
        P.build_execution_manifest(m, experimental_code_commit="deadbeef", corpus_path=CORPUS,
                                   localization_path=LOC, validation_runs_dir=VAL_RUNS,
                                   model="", model_digest="")
    except P.AblationError:
        pass
    else:
        raise AssertionError("execution manifest senza model/digest accettato")


def _sign(manifest):
    manifest["manifest_sha256"] = P.manifest_digest(manifest)
    return manifest


def test_checkpoint_revalidation():
    m = _sign({"stage": "execution", "corpus": {"sha256": "c"}, "git_commit": "g",
               "model": "gemma4:26b", "model_digest": "d",
               "cases": [{"case_id": "x__r0__q1"}]})
    identity = P.ablation_identity(m)
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


def _synthetic_runs(dirpath, cases, manifest):
    """cases: list of (case_id, qid, replica, stratum, {arm: answer})."""
    context = "Caroline: contesto sintetico"
    speakers = ("Caroline", "Melanie")
    reference = "2026-07-27T18:00:00+02:00"
    for cid, qid, rep, stratum, arms in cases:
        arm_records = []
        for arm_name, answer in arms.items():
            arm = P.ARM_BY_NAME[arm_name]
            if arm.family == "two_stage":
                messages = P.build_messages(
                    "two_stage", context_text=context, question="domanda?",
                    speakers=speakers, stage="selector",
                )
                metadata = P.call_metadata(
                    arm=arm, stage="selector",
                    context_text=P.numbered_context(context), cid=cid,
                    question_id=qid,
                    localization_sha256=manifest["localization"]["sha256"],
                    messages=messages, num_predict=P.NUM_PREDICT_SELECTOR,
                    seed=19960177, model=manifest["model"],
                    model_digest=manifest["model_digest"],
                    context_reference_at=reference,
                )
                arm_records.append({
                    "arm": arm_name, "selector_metadata": metadata,
                    "answer_metadata": None, "answer": answer,
                    "latency_s": 0.1, "calls": 1,
                })
            else:
                messages = P.build_messages(
                    arm.family, context_text=context, question="domanda?",
                    speakers=speakers,
                )
                metadata = P.call_metadata(
                    arm=arm, stage="single", context_text=context, cid=cid,
                    question_id=qid,
                    localization_sha256=manifest["localization"]["sha256"],
                    messages=messages, num_predict=arm.num_predict,
                    seed=19960177, model=manifest["model"],
                    model_digest=manifest["model_digest"],
                    context_reference_at=reference,
                )
                arm_records.append({
                    "arm": arm_name, "metadata": metadata, "answer": answer,
                    "latency_s": 0.1, "calls": 1,
                })
        (dirpath / f"{cid}.json").write_text(json.dumps({
            "case_id": cid, "question_id": qid, "replica": rep, "stratum": stratum,
            "conversation": cid.split("__")[0], "arm_order": list(P.ARM_NAMES),
            "manifest_sha256": manifest["manifest_sha256"],
            "model": manifest["model"], "model_digest": manifest["model_digest"],
            "context_sha256": P._sha(context),
            "context_reference_at": reference, "speakers": list(speakers),
            "arms": arm_records,
        }), encoding="utf-8")


def test_analyze_and_blind_audit_synthetic():
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d) / "runs"; runs.mkdir()
        gold = {"q1": {"answer": "roma", "answerable": True, "question": "dove?", "category": "single_hop"},
                "q2": {"answer": None, "answerable": False, "question": "quando?", "category": "adversarial"}}
        # execution manifest minimale con quei casi
        ex = _sign({"stage": "execution", "manifest_sha256": None, "corpus": {"sha256": "c"},
                    "git_commit": "g", "localization": {"sha256": "L"},
                    "model": "gemma4:26b", "model_digest": "digest-test",
                    "cases": [
                        {"case_id": "conv-41__r0__q1", "conversation": "conv-41", "replica": "0",
                         "question_id": "q1", "stratum": "A_evidence_flip", "answer_seed": 19960177,
                         "arm_order": list(P.ARM_NAMES)},
                        {"case_id": "conv-41__r0__q2", "conversation": "conv-41", "replica": "0",
                         "question_id": "q2", "stratum": "C_adversarial", "answer_seed": 19960177,
                         "arm_order": list(P.ARM_NAMES)}]})
        _synthetic_runs(runs, [
            ("conv-41__r0__q1", "q1", "0", "A_evidence_flip",
             {a: ("roma" if a in ("B0", "B1") else "Non lo so.") for a in P.ARM_NAMES}),
            ("conv-41__r0__q2", "q2", "0", "C_adversarial",
             {a: "Non lo so." for a in P.ARM_NAMES}),
        ], ex)
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
        # contrasti preregistrati presenti (punto 4)
        assert set(rep["preregistered_contrasts"]) >= {
            "budget_A2_minus_A0", "thinking_A1_minus_A2", "prompt_B0_minus_A0", "two_stage_C0_minus_A0"}
        assert rep["preregistered_contrasts"]["prompt_B0_minus_A0"]["delta_token_f1"] == 1.0  # B0=roma, A0=astiene
        assert "global_by_arm" in rep and "cost_by_arm" in rep and rep["cases"] == 2
        # a0 stability: contesto+seed bloccati, delta astensione calcolato (punto 5)
        assert rep["a0_stability"]["context_and_seed_frozen"] is True
        assert rep["a0_stability"]["mean_delta_abstention_fresh_minus_old"] is not None

        # audit cieco: codici non riconducibili all'arm, chiave separata
        au = P.blind_audit_export(output_runs=runs, gold_lookup=gold)
        assert au["rows"] and all(set(r) >= {"code", "question", "gold", "answer", "replica"} for r in au["rows"])
        for r in au["rows"]:
            assert r["code"] in au["key"]
            assert not any(arm in r["code"] for arm in P.ARM_NAMES)  # non reversibile dal nome
        assert all("arm" not in r for r in au["rows"])  # arm solo nella key


def test_selector_answerable_false_requires_empty_fragments():
    # answerable=false con frammenti → fail-closed (punto 6)
    assert P.parse_selector_strict('{"answerable": false, "supporting_fragments": [2]}', 5) is None
    assert P.parse_selector_strict('{"answerable": false, "supporting_fragments": []}', 5) == {
        "answerable": False, "supporting_fragments": []}


def test_validate_case_report_catches_mismatches():
    case = {"case_id": "conv-41__r0__q1", "question_id": "conv-41:q1", "conversation": "conv-41",
            "replica": "0", "stratum": "A_evidence_flip", "answer_seed": 19960177,
            "arm_order": list(P.ARM_NAMES)}
    ex = _sign({
        "stage": "execution", "corpus": {"sha256": "c"}, "git_commit": "g",
        "localization": {"sha256": "L"}, "model": "gemma4:26b",
        "model_digest": "d", "cases": [case],
    })
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d)
        _synthetic_runs(
            runs,
            [("conv-41__r0__q1", "conv-41:q1", "0", "A_evidence_flip",
              {a: "x" for a in P.ARM_NAMES})],
            ex,
        )
        good = json.loads((runs / "conv-41__r0__q1.json").read_text())
        assert P.validate_case_report(good, case, ex) == []
        for mut in (
            lambda r: r.__setitem__("question_id", "conv-41:q9"),
            lambda r: r.__setitem__("arm_order", list(reversed(P.ARM_NAMES))),
            lambda r: r["arms"][0]["metadata"].__setitem__("answer_seed", 1),
            lambda r: r["arms"][0]["metadata"].__setitem__("localization_sha256", "X"),
            lambda r: r["arms"][0]["metadata"].__setitem__("num_predict", 999),
            lambda r: r["arms"][0]["metadata"].__setitem__("model_digest", "altro"),
            lambda r: r["arms"][0].__setitem__("answer", None),
            lambda r: r["arms"].pop(),
        ):
            import json as _j
            bad = _j.loads(_j.dumps(good))
            mut(bad)
            assert P.validate_case_report(bad, case, ex), mut


def test_all_arms_end_to_end_with_fake_chat():
    """Mini E2E senza modello: costruzione prompt, 7 arm, C0 a due stadi,
    cattura e validazione byte-esatta del report completo.
    """

    context = "Caroline: siamo stati a Roma\nMelanie: è stato bellissimo"
    question = "Dove sono andati?"
    speakers = ("Caroline", "Melanie")
    reference = "2026-07-27T18:00:00+02:00"
    case = {
        "case_id": "conv-41__r0__q1",
        "question_id": "conv-41:q1",
        "conversation": "conv-41",
        "replica": "0",
        "stratum": "A_evidence_flip",
        "answer_seed": 19960177,
        "arm_order": list(P.ARM_NAMES),
    }
    execution = _sign({
        "stage": "execution",
        "corpus": {"sha256": "corpus-test"},
        "git_commit": "commit-test",
        "localization": {"sha256": "localization-test"},
        "model": "gemma4:26b",
        "model_digest": "digest-test",
        "cases": [case],
    })
    calls = []

    def fake_chat(**kwargs):
        calls.append(kwargs)
        if kwargs.get("format") == "json":
            content = '{"answerable": true, "supporting_fragments": [1]}'
        else:
            content = "Roma"
        return {"message": {"content": content}}

    P.AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="prompt-ablation-v2-fake-", dir=P.AUDIT_ROOT) as d:
        capture_dir = Path(d)
        arms = [
            P._run_arm(
                P.ARM_BY_NAME[name],
                context,
                question,
                case["case_id"],
                case["question_id"],
                case["answer_seed"],
                execution["localization"]["sha256"],
                True,
                fake_chat,
                execution["model"],
                execution["model_digest"],
                capture_dir,
                case["replica"],
                reference,
                speakers,
            )
            for name in P.ARM_NAMES
        ]
        report = {
            "case_id": case["case_id"],
            "question_id": case["question_id"],
            "conversation": case["conversation"],
            "replica": case["replica"],
            "stratum": case["stratum"],
            "arm_order": case["arm_order"],
            "manifest_sha256": execution["manifest_sha256"],
            "model": execution["model"],
            "model_digest": execution["model_digest"],
            "context_sha256": P._sha(context),
            "context_reference_at": reference,
            "speakers": list(speakers),
            "arms": arms,
        }
        assert P.validate_case_report(
            report,
            case,
            execution,
            dual_context_text=context,
            expected_context_reference_at=reference,
            question_text=question,
            speakers=speakers,
        ) == []
        assert len(calls) == 8  # sei arm diretti + due chiamate per C0
        c0 = next(a for a in arms if a["arm"] == "C0")
        assert c0["calls"] == 2 and c0["answer"] == "Roma"
        assert c0["selected_indices"] == [1]
        assert len(list(capture_dir.glob("*.json"))) == 7

        # Anche il percorso C0 invalido deve fermarsi al selettore e astenersi.
        def malformed_selector(**_kwargs):
            return {"message": {"content": "non-json"}}

        failed_closed = P._run_arm(
            P.ARM_BY_NAME["C0"],
            context,
            question,
            case["case_id"],
            case["question_id"],
            case["answer_seed"],
            execution["localization"]["sha256"],
            True,
            malformed_selector,
            execution["model"],
            execution["model_digest"],
            capture_dir,
            case["replica"],
            reference,
            speakers,
        )
        assert failed_closed["answer"] == "Non lo so."
        assert failed_closed["calls"] == 1
        assert failed_closed["answer_metadata"] is None


def test_frozen_clock_regression():
    """Clock corrente: alcuni contesti divergono; clock census: byte-esatti."""
    if not _available():
        return
    try:
        from benchmarks.euri_memory.prompt_ablation import _load_case  # noqa: F401
    except Exception:
        return
    import json as _j
    conv, rep, qid = "conv-49", "0", "conv-49:q33"
    report = _j.loads((VAL_RUNS / f"{conv}__r{rep}.json").read_text())
    try:
        case = _load_case(VAL / "run", conv, CORPUS)
    except Exception:
        return
    # clock CENSUS congelato → byte-esatto
    text, ref = P.reconstruct_one(report, case, qid, freeze=True)
    assert text and ref  # context_reference_at registrato
    # clock CORRENTE → diverge (questo caso è uno dei 3 noti)
    try:
        P.reconstruct_one(report, case, qid, freeze=False)
    except P.AblationError:
        pass
    else:
        raise AssertionError("con clock corrente conv-49:q33 dovrebbe divergere")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("test_prompt_ablation_v2: OK")
