"""A/B DEVELOPMENT: RAG puro vs dual-channel, entrambi strict + thinking.

Isola l'effetto della memoria dopo che prompt-ablation v2 ha mostrato che
Gemma4 beneficia del thinking. Riusa i 129 casi già congelati e ricostruisce
byte-per-byte i contesti RAG e dual dagli artefatti del census. Nessuna nuova
ingestion, retrieval o lettura del Redis personale.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from benchmarks.euri_memory import prompt_ablation_v2 as P
from benchmarks.euri_memory.analysis import cluster_bootstrap_ci, mcnemar_exact
from benchmarks.euri_memory.integrity import (
    IntegrityError,
    assert_corpus_matches,
    assert_head_matches_manifest,
    assert_same_identity,
    assert_worktree_clean,
    run_identity,
    sha256_file,
)


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[1]
AUDIT_ROOT = REPO_ROOT / "audit_output"
EXPERIMENT_ID = "euri_thinking_memory_ab_v1"
SOURCE_CASE_MANIFEST = ROOT / "prompt_ablation_v2_manifest.json"
SOURCE_VALIDATION = "dual_channel_validation_v1_seed396895560"
ARMS = ("rag_think", "dual_think")
NUM_PREDICT = 2000
THINK = True
TEMPERATURE = 0
MAX_CALLS = 258


class ThinkingMemoryABError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _signed(payload: dict) -> dict:
    payload = dict(payload)
    payload["manifest_sha256"] = P.manifest_digest(payload)
    return payload


def verify_manifest(manifest: dict) -> None:
    try:
        P.verify_manifest(manifest)
    except P.AblationError as exc:
        raise ThinkingMemoryABError(str(exc)) from exc


def _source_manifest(path: Path = SOURCE_CASE_MANIFEST) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_manifest(manifest)
    return manifest


def _source_cases(source: dict) -> list[dict]:
    rows = P._cases(source)
    out = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["arm_order"] = list(ARMS if index % 2 == 0 else reversed(ARMS))
        out.append(item)
    return out


def protocol_manifest(source_path: Path = SOURCE_CASE_MANIFEST) -> dict:
    source = _source_manifest(source_path)
    cases = _source_cases(source)
    return _signed({
        "schema_version": 1,
        "experiment": EXPERIMENT_ID,
        "stage": "protocol",
        "kind": "development",
        "source_case_manifest": str(Path(source_path).resolve()),
        "source_case_manifest_sha256": source["manifest_sha256"],
        "source_validation": SOURCE_VALIDATION,
        "cases": len(cases),
        "arms": list(ARMS),
        "prompt_sha256": P.prompt_sha256()["strict"],
        "thinking": THINK,
        "num_predict": NUM_PREDICT,
        "temperature": TEMPERATURE,
        "seed_policy": "seed originale del census, identico nei due arm",
        "order_policy": "alternanza deterministica per caso",
        "primary_contrast": "dual_think_minus_rag_think",
        "decision_rule": {
            "GO": "delta_f1>0, delta_flip>0, >=4/5 conversazioni non-negative, delta_adv>=-0.02",
            "NO_GO": "delta_f1<=0 oppure delta_flip<=0 oppure delta_adv<-0.02",
            "INCONCLUSIVE": "altrimenti",
        },
        "max_calls": MAX_CALLS,
        "note": "LoCoMo interamente aperto: sviluppo causale, non validazione indipendente.",
    })


def build_execution_manifest(
    *,
    protocol: dict,
    source_path: Path,
    git_commit: str,
    corpus_path: Path,
    localization_path: Path,
    validation_runs_dir: Path,
    model: str,
    model_digest: str,
) -> dict:
    verify_manifest(protocol)
    if protocol.get("stage") != "protocol":
        raise ThinkingMemoryABError("serve il protocol manifest")
    if not model or not model_digest or not git_commit:
        raise ThinkingMemoryABError("commit, modello e digest sono obbligatori")
    source = _source_manifest(source_path)
    if source["manifest_sha256"] != protocol["source_case_manifest_sha256"]:
        raise ThinkingMemoryABError("source case-manifest diverso dal protocollo")
    report_sha = {
        path.name: sha256_file(path)
        for path in sorted(Path(validation_runs_dir).glob("*.json"))
    }
    if len(report_sha) != 10:
        raise ThinkingMemoryABError(f"attesi 10 report census, trovati {len(report_sha)}")
    return _signed({
        "schema_version": 1,
        "experiment": EXPERIMENT_ID,
        "stage": "execution",
        "protocol_manifest_sha256": protocol["manifest_sha256"],
        "source_case_manifest_sha256": source["manifest_sha256"],
        "git_commit": git_commit,
        "model": model,
        "model_digest": model_digest,
        "corpus": {
            "path": str(Path(corpus_path).resolve()),
            "sha256": sha256_file(corpus_path),
        },
        "localization": {
            "path": str(Path(localization_path).resolve()),
            "sha256": sha256_file(localization_path),
        },
        "census_report_sha256": report_sha,
        "arms": list(ARMS),
        "thinking": THINK,
        "num_predict": NUM_PREDICT,
        "temperature": TEMPERATURE,
        "cases": _source_cases(source),
        "max_calls": MAX_CALLS,
    })


def identity(manifest: dict) -> dict:
    out = dict(run_identity(manifest))
    out["model"] = manifest.get("model")
    out["model_digest"] = manifest.get("model_digest")
    return out


def reconstruct_pair(report: dict, case, question_id: str) -> dict:
    """Restituisce base e dual byte-esatti con clock congelato al census."""

    from benchmarks.euri_memory.prompt_ablation import _FrozenBaseMemory, _raw_turn_document
    from benchmarks.euri_memory.dual_channel import render_additions_block
    from benchmarks.euri_memory.dual_channel_worker import build_turn_renderer
    from core.rag_context import build_rag_context

    by_arm = {arm["arm"]: arm for arm in report["arms"]}
    rag = {row["question_id"]: row for row in by_arm["rag_only"]["results"]}
    dual = {row["question_id"]: row for row in by_arm["dual_channel"]["results"]}
    turns = {turn.turn_id: turn for turn in case.turns}
    questions = {question.question_id: question for question in case.questions}
    nodes = report.get("base_nodes_by_question", {}).get(question_id)
    if not nodes or question_id not in questions:
        raise ThinkingMemoryABError(f"{question_id}: contesto o domanda assente")
    docs = []
    for node in nodes:
        turn_id = node.get("benchmark_turn_id")
        if not turn_id or turn_id not in turns:
            raise ThinkingMemoryABError(f"{question_id}: nodo base non idratabile ({turn_id})")
        docs.append(_raw_turn_document(case.corpus(), turns[turn_id], []))
    with P.frozen_clock(report["created_at"]) as reference:
        base = build_rag_context(
            questions[question_id].text,
            _FrozenBaseMemory(docs),
            mode="search",
            touch=False,
        ).text
    expected_base = rag[question_id]["metadata"]["base_sha256"]
    if P._sha(base) != expected_base:
        raise ThinkingMemoryABError(f"{question_id}: RAG non byte-esatto")
    composition = dual[question_id]["metadata"]["composition"]
    render_turn = build_turn_renderer(case)
    rendered = [
        render_turn(str(turn_id))
        for turn_id in (composition.get("added_turn_ids") or [])
    ]
    final = base + render_additions_block(rendered)
    if P._sha(final) != composition["final_sha256"]:
        raise ThinkingMemoryABError(f"{question_id}: dual non byte-esatto")
    return {
        "rag": base,
        "dual": final,
        "rag_sha256": expected_base,
        "dual_sha256": composition["final_sha256"],
        "context_reference_at": reference.isoformat(),
        "added_turn_ids": list(composition.get("added_turn_ids") or []),
    }


def _validation_report(validation_root: Path, conv: str, replica: str) -> dict:
    return json.loads(
        (Path(validation_root) / "runs" / f"{conv}__r{replica}.json").read_text(
            encoding="utf-8"
        )
    )


def dry_run(*, protocol: dict, source_path: Path, validation_root: Path) -> dict:
    verify_manifest(protocol)
    source = _source_manifest(source_path)
    if source["manifest_sha256"] != protocol["source_case_manifest_sha256"]:
        raise ThinkingMemoryABError("source case-manifest diverso")
    from benchmarks.euri_memory.prompt_ablation import _load_case

    ok = 0
    different = 0
    failures = []
    per_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in _source_cases(source):
        per_group[(row["conversation"], row["replica"])].append(row)
    for (conv, replica), rows in sorted(per_group.items()):
        report = _validation_report(validation_root, conv, replica)
        case = _load_case(validation_root, conv, P._corpus_path())
        for row in rows:
            try:
                pair = reconstruct_pair(report, case, row["question_id"])
                ok += 1
                different += int(pair["rag_sha256"] != pair["dual_sha256"])
            except ThinkingMemoryABError as exc:
                failures.append({"case_id": row["case_id"], "reason": str(exc)})
    return {
        "experiment": EXPERIMENT_ID,
        "no_model": True,
        "expected": len(_source_cases(source)),
        "byte_exact_pairs": ok,
        "contexts_different": different,
        "failures": failures,
        "byte_exact_ok": not failures and ok == len(_source_cases(source)),
        "max_calls": MAX_CALLS,
    }


def _messages(context: str, question: str, speakers: tuple[str, str]) -> list[dict]:
    return P.build_messages(
        "strict", context_text=context, question=question, speakers=speakers
    )


def _call_arm(
    *,
    arm: str,
    context: str,
    question: str,
    speakers: tuple[str, str],
    case: dict,
    manifest: dict,
    reference_at: str,
    execute: bool,
    chat_fn: Callable | None,
    capture_dir: Path,
) -> dict:
    messages = _messages(context, question, speakers)
    metadata = {
        "arm": arm,
        "case_id": case["case_id"],
        "question_id": case["question_id"],
        "context_sha256": P._sha(context),
        "context_reference_at": reference_at,
        "messages_payload_sha256": P.messages_payload_sha256(messages),
        "system_prompt_sha256": P._sha(P.PROMPT_STRICT),
        "answer_seed": int(case["answer_seed"]),
        "model": manifest["model"],
        "model_digest": manifest["model_digest"],
        "temperature": TEMPERATURE,
        "num_predict": NUM_PREDICT,
        "think": THINK,
    }
    answer = None
    latency = None
    calls = 0
    if execute:
        started = time.perf_counter()
        answer = P._content(chat_fn(
            model=manifest["model"],
            messages=messages,
            options={
                "temperature": TEMPERATURE,
                "num_predict": NUM_PREDICT,
                "seed": int(case["answer_seed"]),
            },
            think=THINK,
        ))
        latency = round(time.perf_counter() - started, 3)
        calls = 1
    Path(capture_dir).mkdir(parents=True, exist_ok=True)
    (Path(capture_dir) / f"{case['case_id']}__{arm}.json").write_text(
        json.dumps({
            "case_id": case["case_id"],
            "arm": arm,
            "context": context,
            "messages": messages,
        }, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "arm": arm,
        "answer": answer,
        "latency_s": latency,
        "calls": calls,
        "metadata": metadata,
    }


def validate_report(
    report: dict,
    case: dict,
    manifest: dict,
    *,
    pair: dict | None = None,
    question: str | None = None,
    speakers: tuple[str, str] | None = None,
) -> list[str]:
    problems = []
    for field in ("case_id", "question_id", "conversation", "replica", "stratum"):
        if report.get(field) != case.get(field):
            problems.append(f"{field} diverso")
    if report.get("arm_order") != case.get("arm_order"):
        problems.append("arm_order diverso")
    if report.get("manifest_sha256") != manifest.get("manifest_sha256"):
        problems.append("manifest_sha256 diverso")
    if report.get("model") != manifest.get("model"):
        problems.append("model diverso")
    if report.get("model_digest") != manifest.get("model_digest"):
        problems.append("model_digest diverso")
    if pair is not None:
        if report.get("rag_sha256") != pair["rag_sha256"]:
            problems.append("rag_sha256 top-level diverso")
        if report.get("dual_sha256") != pair["dual_sha256"]:
            problems.append("dual_sha256 top-level diverso")
        if report.get("context_reference_at") != pair["context_reference_at"]:
            problems.append("context_reference_at top-level diverso")
        if report.get("added_turn_ids") != pair.get("added_turn_ids"):
            problems.append("added_turn_ids diversi")
    else:
        for field in ("rag_sha256", "dual_sha256", "context_reference_at"):
            if not report.get(field):
                problems.append(f"{field} top-level assente")
    arms = report.get("arms") or []
    if sorted(item.get("arm") for item in arms) != sorted(ARMS):
        problems.append("arm non esatti")
        return problems
    by_arm = {item["arm"]: item for item in arms}
    for arm in ARMS:
        item = by_arm[arm]
        meta = item.get("metadata") or {}
        expected_context = pair["rag" if arm == "rag_think" else "dual"] if pair else None
        expected_sha = (
            P._sha(expected_context)
            if expected_context is not None
            else report.get("rag_sha256" if arm == "rag_think" else "dual_sha256")
        )
        checks = {
            "arm": arm,
            "case_id": case["case_id"],
            "question_id": case["question_id"],
            "context_sha256": expected_sha,
            "answer_seed": int(case["answer_seed"]),
            "model": manifest["model"],
            "model_digest": manifest["model_digest"],
            "temperature": TEMPERATURE,
            "num_predict": NUM_PREDICT,
            "think": THINK,
            "system_prompt_sha256": P._sha(P.PROMPT_STRICT),
        }
        if pair:
            checks["context_reference_at"] = pair["context_reference_at"]
        for key, expected in checks.items():
            if meta.get(key) != expected:
                problems.append(f"{arm}/{key}")
        if expected_context is not None and question is not None and speakers is not None:
            expected_messages = _messages(expected_context, question, speakers)
            if meta.get("messages_payload_sha256") != P.messages_payload_sha256(
                expected_messages
            ):
                problems.append(f"{arm}/messages_payload_sha256")
        elif not meta.get("messages_payload_sha256"):
            problems.append(f"{arm}/messages_payload_sha256 assente")
        if item.get("answer") is None:
            problems.append(f"{arm}/answer assente")
        if item.get("calls") != 1:
            problems.append(f"{arm}/calls")
        try:
            if float(item.get("latency_s")) < 0:
                problems.append(f"{arm}/latency")
        except (TypeError, ValueError):
            problems.append(f"{arm}/latency")
    return problems


def _checkpoint(
    path: Path,
    manifest: dict,
    runs_dir: Path,
    validation_root: Path,
) -> set[str]:
    if not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    assert_same_identity(value.get("identity") or {}, identity(manifest), context=EXPERIMENT_ID)
    cases = {case["case_id"]: case for case in manifest["cases"]}
    from benchmarks.euri_memory.prompt_ablation import _load_case

    localized_cache = {}
    census_cache = {}
    done = set()
    for case_id in value.get("done") or []:
        if case_id not in cases or case_id in done:
            raise ThinkingMemoryABError(f"checkpoint non valido: {case_id}")
        report_path = runs_dir / f"{case_id}.json"
        if not report_path.exists():
            raise ThinkingMemoryABError(f"report mancante: {case_id}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        case = cases[case_id]
        key = (case["conversation"], case["replica"])
        if key not in localized_cache:
            localized_cache[key] = _load_case(
                validation_root, case["conversation"], P._corpus_path()
            )
            census_cache[key] = _validation_report(
                validation_root, case["conversation"], case["replica"]
            )
        localized = localized_cache[key]
        pair = reconstruct_pair(census_cache[key], localized, case["question_id"])
        question = {
            item.question_id: item.text for item in localized.questions
        }[case["question_id"]]
        problems = validate_report(
            report,
            case,
            manifest,
            pair=pair,
            question=question,
            speakers=localized.speakers,
        )
        if problems:
            raise ThinkingMemoryABError(f"report {case_id} non valido: {problems}")
        done.add(case_id)
    return done


def run(
    *,
    manifest: dict,
    validation_root: Path,
    output_dir: Path,
    capture_dir: Path,
    execute: bool,
    chat_fn: Callable | None = None,
) -> dict:
    verify_manifest(manifest)
    if manifest.get("stage") != "execution":
        raise ThinkingMemoryABError("serve execution manifest")
    if not execute:
        raise ThinkingMemoryABError("run senza --execute vietato: usare dry-run")
    P.assert_no_personal_redis()
    P.assert_capture_dir_under_audit(capture_dir)
    assert_corpus_matches(manifest, P._corpus_path())
    if sha256_file(manifest["localization"]["path"]) != manifest["localization"]["sha256"]:
        raise ThinkingMemoryABError("localizzazione diversa")
    for name, digest in manifest["census_report_sha256"].items():
        if sha256_file(Path(validation_root) / "runs" / name) != digest:
            raise ThinkingMemoryABError(f"report census diverso: {name}")
    assert_head_matches_manifest(manifest, REPO_ROOT)
    assert_worktree_clean(REPO_ROOT)
    if execute and chat_fn is None:
        from core.ollama_client import chat_client
        chat_fn = lambda **kwargs: chat_client.chat(**kwargs)  # noqa: E731

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        verify_manifest(old)
        if _canonical(old) != _canonical(manifest):
            raise ThinkingMemoryABError("output-dir legata a manifest diverso")
    else:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    runs_dir = output_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    checkpoint_path = output_dir / "checkpoint.json"
    done = _checkpoint(checkpoint_path, manifest, runs_dir, validation_root)

    from benchmarks.euri_memory.prompt_ablation import _load_case

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for case in manifest["cases"]:
        grouped[(case["conversation"], case["replica"])].append(case)
    completed_now = 0
    for (conv, replica), cases in sorted(grouped.items()):
        census = _validation_report(validation_root, conv, replica)
        localized = _load_case(validation_root, conv, P._corpus_path())
        questions = {question.question_id: question for question in localized.questions}
        for case in cases:
            if case["case_id"] in done:
                continue
            pair = reconstruct_pair(census, localized, case["question_id"])
            question = questions[case["question_id"]].text
            records = []
            for arm in case["arm_order"]:
                context = pair["rag" if arm == "rag_think" else "dual"]
                records.append(_call_arm(
                    arm=arm,
                    context=context,
                    question=question,
                    speakers=localized.speakers,
                    case=case,
                    manifest=manifest,
                    reference_at=pair["context_reference_at"],
                    execute=execute,
                    chat_fn=chat_fn,
                    capture_dir=capture_dir,
                ))
            report = {
                "case_id": case["case_id"],
                "question_id": case["question_id"],
                "conversation": case["conversation"],
                "replica": case["replica"],
                "stratum": case["stratum"],
                "arm_order": case["arm_order"],
                "manifest_sha256": manifest["manifest_sha256"],
                "model": manifest["model"],
                "model_digest": manifest["model_digest"],
                "rag_sha256": pair["rag_sha256"],
                "dual_sha256": pair["dual_sha256"],
                "context_reference_at": pair["context_reference_at"],
                "added_turn_ids": pair["added_turn_ids"],
                "arms": records,
            }
            if execute:
                problems = validate_report(
                    report,
                    case,
                    manifest,
                    pair=pair,
                    question=question,
                    speakers=localized.speakers,
                )
                if problems:
                    raise ThinkingMemoryABError(
                        f"{case['case_id']}: report non valido: {problems}"
                    )
            (runs_dir / f"{case['case_id']}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            done.add(case["case_id"])
            completed_now += 1
            checkpoint_path.write_text(
                json.dumps({
                    "identity": identity(manifest),
                    "done": sorted(done),
                }, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
    return {
        "mode": "run" if execute else "prepared_no_model",
        "completed": len(done),
        "completed_now": completed_now,
        "max_calls": MAX_CALLS,
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _verdict(delta_f1: float, delta_flip: float, delta_adv: float, nonnegative: int) -> str:
    if delta_f1 > 0 and delta_flip > 0 and nonnegative >= 4 and delta_adv >= -0.02:
        return "GO"
    if delta_f1 <= 0 or delta_flip <= 0 or delta_adv < -0.02:
        return "NO_GO"
    return "INCONCLUSIVE"


def analyze(
    *,
    manifest: dict,
    validation_root: Path,
    runs_dir: Path,
    gold_lookup: dict,
) -> dict:
    verify_manifest(manifest)
    expected = {case["case_id"]: case for case in manifest["cases"]}
    reports = {}
    for path in sorted(Path(runs_dir).glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        case_id = report.get("case_id")
        if case_id not in expected or case_id in reports:
            raise ThinkingMemoryABError(f"report estraneo o duplicato: {case_id}")
        reports[case_id] = report
    missing = set(expected) - set(reports)
    if missing:
        raise ThinkingMemoryABError(f"report mancanti: {len(missing)}")

    from benchmarks.euri_memory.prompt_ablation import _load_case

    localized_cache = {}
    census_cache = {}
    metrics = defaultdict(lambda: defaultdict(list))
    strata = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    by_conversation_delta = defaultdict(list)
    changed = improved = worsened = 0
    adversarial_b = adversarial_c = 0
    costs = defaultdict(lambda: {"calls": 0, "latency_s": 0.0})
    for case_id, case in expected.items():
        key = (case["conversation"], case["replica"])
        if key not in localized_cache:
            localized_cache[key] = _load_case(
                validation_root, case["conversation"], P._corpus_path()
            )
            census_cache[key] = _validation_report(
                validation_root, case["conversation"], case["replica"]
            )
        localized = localized_cache[key]
        census = census_cache[key]
        pair = reconstruct_pair(census, localized, case["question_id"])
        question = {
            item.question_id: item.text for item in localized.questions
        }[case["question_id"]]
        report = reports[case_id]
        problems = validate_report(
            report,
            case,
            manifest,
            pair=pair,
            question=question,
            speakers=localized.speakers,
        )
        if problems:
            raise ThinkingMemoryABError(f"{case_id}: {problems}")
        answers = {item["arm"]: item["answer"] for item in report["arms"]}
        for item in report["arms"]:
            costs[item["arm"]]["calls"] += int(item["calls"])
            costs[item["arm"]]["latency_s"] += float(item["latency_s"])
        gold = gold_lookup[case["question_id"]]
        if gold["answerable"]:
            f1 = {
                arm: P.token_f1(gold["answer"], answers[arm]) for arm in ARMS
            }
            delta = f1["dual_think"] - f1["rag_think"]
            by_conversation_delta[case["conversation"]].append(delta)
            changed += int(P._norm(answers["rag_think"]) != P._norm(answers["dual_think"]))
            improved += int(delta > 1e-9)
            worsened += int(delta < -1e-9)
            for arm in ARMS:
                values = {
                    "token_f1": f1[arm],
                    "exact_match": P.exact_match(gold["answer"], answers[arm]),
                    "false_abstention": float(P.is_abstention(answers[arm])),
                }
                for name, value in values.items():
                    metrics[arm][name].append(value)
                    strata[case["stratum"]][arm][name].append(value)
        else:
            correct = {
                arm: bool(P.is_abstention(answers[arm])) for arm in ARMS
            }
            adversarial_b += int(correct["rag_think"] and not correct["dual_think"])
            adversarial_c += int(not correct["rag_think"] and correct["dual_think"])
            for arm in ARMS:
                value = float(correct[arm])
                metrics[arm]["adversarial_correct"].append(value)
                strata[case["stratum"]][arm]["adversarial_correct"].append(value)

    global_metrics = {
        arm: {name: _mean(values) for name, values in metrics[arm].items()}
        for arm in ARMS
    }
    per_stratum = {
        stratum: {
            arm: {name: _mean(values) for name, values in arm_metrics.items()}
            for arm, arm_metrics in arms.items()
        }
        for stratum, arms in strata.items()
    }
    conversation_delta = {
        conv: sum(values) / len(values)
        for conv, values in sorted(by_conversation_delta.items())
    }
    delta_f1 = round(
        global_metrics["dual_think"]["token_f1"]
        - global_metrics["rag_think"]["token_f1"],
        4,
    )
    delta_flip = round(
        per_stratum["A_evidence_flip"]["dual_think"]["token_f1"]
        - per_stratum["A_evidence_flip"]["rag_think"]["token_f1"],
        4,
    )
    delta_adv = round(
        global_metrics["dual_think"]["adversarial_correct"]
        - global_metrics["rag_think"]["adversarial_correct"],
        4,
    )
    nonnegative = sum(value >= 0 for value in conversation_delta.values())
    return {
        "experiment": EXPERIMENT_ID,
        "kind": "development",
        "cases": len(reports),
        "manifest_sha256": manifest["manifest_sha256"],
        "global_by_arm": global_metrics,
        "per_stratum_arm": per_stratum,
        "primary_contrast": {
            "delta_token_f1": delta_f1,
            "delta_evidence_flip_f1": delta_flip,
            "delta_adversarial": delta_adv,
            "changed": changed,
            "improved": improved,
            "worsened": worsened,
            "per_conversation_delta_f1": conversation_delta,
            "nonnegative_conversations": nonnegative,
            "cluster_bootstrap": cluster_bootstrap_ci(list(conversation_delta.values())),
            "mcnemar_adversarial": mcnemar_exact(adversarial_b, adversarial_c),
        },
        "verdict": _verdict(delta_f1, delta_flip, delta_adv, nonnegative),
        "cost_by_arm": {
            arm: {
                "calls": value["calls"],
                "latency_s": round(value["latency_s"], 2),
            }
            for arm, value in costs.items()
        },
        "limits": {
            "independent_clusters": len(conversation_delta),
            "underpowered": len(conversation_delta) < 10,
            "locomo_fully_open": True,
            "not_independent_validation": True,
        },
    }


def blind_audit(
    *,
    runs_dir: Path,
    gold_lookup: dict,
    seed: int = 20260728,
) -> dict:
    rng = random.Random(seed)
    rows = []
    key = {}
    for path in sorted(Path(runs_dir).glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        answers = {item["arm"]: item["answer"] for item in report["arms"]}
        if P._norm(answers["rag_think"]) == P._norm(answers["dual_think"]):
            continue
        gold = gold_lookup[report["question_id"]]
        for arm in ARMS:
            code = f"{rng.randrange(16**8):08x}"
            key[code] = {"case_id": report["case_id"], "arm": arm}
            rows.append({
                "code": code,
                "question_id": report["question_id"],
                "replica": report["replica"],
                "question": gold.get("question"),
                "gold": gold.get("answer"),
                "answer": answers[arm],
                "human_label": None,
            })
    rng.shuffle(rows)
    return {"rows": rows, "key": key}


def _write(path: Path, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dry = sub.add_parser("dry-run")
    dry.add_argument("--protocol", type=Path, required=True)
    dry.add_argument("--source", type=Path, default=SOURCE_CASE_MANIFEST)
    dry.add_argument("--validation-root", type=Path, required=True)
    dry.add_argument("--output", type=Path, required=True)
    execution = sub.add_parser("execution-manifest")
    execution.add_argument("--protocol", type=Path, required=True)
    execution.add_argument("--source", type=Path, default=SOURCE_CASE_MANIFEST)
    execution.add_argument("--corpus", type=Path, required=True)
    execution.add_argument("--localization", type=Path, required=True)
    execution.add_argument("--validation-root", type=Path, required=True)
    execution.add_argument("--model", required=True)
    execution.add_argument("--model-digest", required=True)
    execution.add_argument("--output", type=Path, required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--manifest", type=Path, required=True)
    runner.add_argument("--validation-root", type=Path, required=True)
    runner.add_argument("--output-dir", type=Path, required=True)
    runner.add_argument("--capture-dir", type=Path, required=True)
    runner.add_argument("--execute", action="store_true")
    analyzer = sub.add_parser("analyze")
    analyzer.add_argument("--manifest", type=Path, required=True)
    analyzer.add_argument("--validation-root", type=Path, required=True)
    analyzer.add_argument("--runs-dir", type=Path, required=True)
    analyzer.add_argument("--corpus", type=Path, required=True)
    analyzer.add_argument("--localization", type=Path, required=True)
    analyzer.add_argument("--output", type=Path, required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--runs-dir", type=Path, required=True)
    audit.add_argument("--corpus", type=Path, required=True)
    audit.add_argument("--localization", type=Path, required=True)
    audit.add_argument("--rows-output", type=Path, required=True)
    audit.add_argument("--key-output", type=Path, required=True)
    args = parser.parse_args()

    try:
        if args.command == "dry-run":
            protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
            result = dry_run(
                protocol=protocol,
                source_path=args.source,
                validation_root=args.validation_root,
            )
            _write(args.output, result)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["byte_exact_ok"] else 5
        if args.command == "execution-manifest":
            import subprocess

            protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            result = build_execution_manifest(
                protocol=protocol,
                source_path=args.source,
                git_commit=head,
                corpus_path=args.corpus,
                localization_path=args.localization,
                validation_runs_dir=args.validation_root / "runs",
                model=args.model,
                model_digest=args.model_digest,
            )
            _write(args.output, result)
            print(json.dumps({
                "manifest_sha256": result["manifest_sha256"],
                "git_commit": result["git_commit"],
                "cases": len(result["cases"]),
            }, sort_keys=True))
            return 0
        if args.command == "run":
            result = run(
                manifest=json.loads(args.manifest.read_text(encoding="utf-8")),
                validation_root=args.validation_root,
                output_dir=args.output_dir,
                capture_dir=args.capture_dir,
                execute=args.execute,
            )
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "analyze":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            result = analyze(
                manifest=manifest,
                validation_root=args.validation_root,
                runs_dir=args.runs_dir,
                gold_lookup=P.build_gold_lookup(args.localization, args.corpus),
            )
            _write(args.output, result)
            print(json.dumps({
                "verdict": result["verdict"],
                "primary_contrast": result["primary_contrast"],
            }, sort_keys=True))
            return 0
        if args.command == "audit":
            result = blind_audit(
                runs_dir=args.runs_dir,
                gold_lookup=P.build_gold_lookup(args.localization, args.corpus),
            )
            _write(args.rows_output, result["rows"])
            _write(args.key_output, result["key"])
            print(json.dumps({"rows": len(result["rows"])}, sort_keys=True))
            return 0
    except (
        ThinkingMemoryABError,
        P.AblationError,
        IntegrityError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"event": "thinking_memory_ab_error", "detail": str(exc)}))
        return 4
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
