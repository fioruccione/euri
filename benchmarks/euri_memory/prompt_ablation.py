"""Replay generativo della presentazione dual-channel, senza nuovo retrieval.

Ricostruisce il contesto base byte-per-byte dagli artefatti del census e lo
verifica contro gli SHA-256 registrati. Confronta:

* append_v1: risposta già prodotta dalla policy validata;
* prepend_plain_v1: identico blocco verbatim spostato prima della base;
* evidence_first_v1: verbatim prima della base con contratto epistemico.

Le memorie passive restano locator: il loro testo non viene mai ricostruito né
mostrato al generatore. Il runner salva un checkpoint dopo ogni risposta.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from loguru import logger

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.dual_channel import render_additions_block
from benchmarks.euri_memory.dual_channel_worker import (
    _ANSWER_SYSTEM_IT,
    _user_prompt,
    build_turn_renderer,
)
from benchmarks.euri_memory.live_worker import _raw_turn_document, _response_content
from benchmarks.euri_memory.localization import BenchmarkLocalization
from benchmarks.euri_memory.scorers import score_locomo_reduced
from benchmarks.euri_memory.selection import BenchmarkSelection
from core.rag_context import build_rag_context


EXPERIMENT_ID = "dual-channel-prompt-ablation-v1"
APPEND = "append_v1"
PREPEND = "prepend_plain_v1"
EVIDENCE_FIRST = "evidence_first_v1"
GENERATED_ARMS = (PREPEND, EVIDENCE_FIRST)

EVIDENCE_FIRST_HEADER = """\
[EVIDENZE ORIGINALI RECUPERATE — CONTROLLA PRIMA QUESTO BLOCCO]
I turni seguenti sono trascrizioni originali potenzialmente pertinenti.
Usali soltanto se rispondono direttamente alla domanda.
Se una sintesi differisce dal turno originale, preferisci il turno originale.
Il turno prova che qualcosa è stato detto, non che sia necessariamente vero."""
BASE_HEADER = "[CONTESTO DI MEMORIA DI BASE]"

# La ricostruzione di 1.978 contesti è una verifica strutturale: il log per ogni
# RAG oscurerebbe gli eventi di avanzamento realmente utili del replay.
logger.disable("core.rag_context")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


class _FrozenBaseMemory:
    """Memory facade che riproduce soltanto i nodi registrati nel report."""

    def __init__(self, docs: list[dict]):
        self.docs = docs

    def get_recent_memories(self, **_kwargs):
        return []

    def search_memories_by_timerange(self, *_args, **_kwargs):
        return list(self.docs)

    def search_memories(self, *_args, **_kwargs):
        return list(self.docs)

    def search_notes(self, *_args, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_insights(self, *_args, **_kwargs):
        return []


def _arm_map(report: dict) -> dict[str, dict]:
    return {arm["arm"]: arm for arm in report["arms"]}


def _result_map(arm: dict) -> dict[str, dict]:
    return {row["question_id"]: row for row in arm["results"]}


def _scoring_map(arm: dict) -> dict[str, dict]:
    return {row["question_id"]: row for row in arm["scoring"]["items"]}


def _load_case(root: Path, sample_id: str, source: Path):
    cases = LoCoMoAdapter().load(source)
    case = BenchmarkSelection.load(root / "selections" / f"{sample_id}.json").apply(cases)
    return BenchmarkLocalization.load(
        root / "localizations" / f"{sample_id}.it.json"
    ).apply(case)


def _reconstruct_contexts(
    report: dict,
    case,
) -> dict[str, dict[str, str]]:
    arms = _arm_map(report)
    rag_results = _result_map(arms["rag_only"])
    dual_results = _result_map(arms["dual_channel"])
    turns = {turn.turn_id: turn for turn in case.turns}
    render_turn = build_turn_renderer(case)
    questions = {question.question_id: question for question in case.questions}
    contexts: dict[str, dict[str, str]] = {}

    for question_id in report["selection"]["question_ids"]:
        nodes = report["base_nodes_by_question"][question_id]
        docs = []
        for node in nodes:
            turn_id = node.get("benchmark_turn_id")
            if not turn_id or turn_id not in turns:
                raise RuntimeError(
                    f"{question_id}: nodo base privo di turno localizzato ({turn_id})"
                )
            docs.append(_raw_turn_document(case.corpus(), turns[turn_id], []))

        base = build_rag_context(
            questions[question_id].text,
            _FrozenBaseMemory(docs),
            mode="search",
            touch=False,
        ).text
        expected_base = rag_results[question_id]["metadata"]["base_sha256"]
        if _sha256(base) != expected_base:
            raise RuntimeError(
                f"{question_id}: base non ricostruibile byte-per-byte "
                f"({_sha256(base)} != {expected_base})"
            )

        composition = dual_results[question_id]["metadata"]["composition"]
        added_ids = [str(turn) for turn in composition.get("added_turn_ids") or []]
        rendered = [render_turn(turn_id) for turn_id in added_ids]
        additions = render_additions_block(rendered)
        append = base + additions
        if _sha256(append) != composition["final_sha256"]:
            raise RuntimeError(f"{question_id}: append_v1 diverge dal report")

        if additions:
            prepend = additions.lstrip("\n") + "\n\n" + base
            evidence_first = (
                EVIDENCE_FIRST_HEADER
                + "\n"
                + "\n".join(rendered)
                + "\n\n"
                + BASE_HEADER
                + "\n"
                + base
            )
        else:
            prepend = append
            evidence_first = append
        contexts[question_id] = {
            APPEND: append,
            PREPEND: prepend,
            EVIDENCE_FIRST: evidence_first,
        }
    return contexts


def dry_run(input_root: Path, source: Path) -> dict:
    reports = sorted((input_root / "runs").glob("*.json"))
    reconstructed = additions = 0
    samples = set()
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        sample_id = report["dataset"]["sample_id"]
        case = _load_case(input_root, sample_id, source)
        contexts = _reconstruct_contexts(report, case)
        reconstructed += len(contexts)
        samples.add(sample_id)
        additions += sum(
            contexts[q][APPEND] != contexts[q][PREPEND] for q in contexts
        )
    return {
        "experiment_id": EXPERIMENT_ID,
        "reports": len(reports),
        "samples": sorted(samples),
        "instances": reconstructed,
        "instances_with_additions": additions,
        "new_generations_planned": additions * len(GENERATED_ARMS),
        "all_base_hashes_verified": True,
    }


def _new_checkpoint(
    *,
    source: Path,
    input_root: Path,
    input_report: Path,
    report: dict,
) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "input": {
            "source_sha256": _sha256_file(source),
            "report_sha256": _sha256_file(input_report),
            "manifest_sha256": report["binding"]["manifest_sha256"],
            "run_label": report["run"]["run_label"],
        },
        "models": dict(report["models"]),
        "prompt_sha256": {
            "answer_system_it": _sha256(_ANSWER_SYSTEM_IT),
            "evidence_first_contract": _sha256(EVIDENCE_FIRST_HEADER),
        },
        "arms": {
            APPEND: {},
            PREPEND: {},
            EVIDENCE_FIRST: {},
        },
        "started_at": time.time(),
        "updated_at": time.time(),
        "complete": False,
    }


def _validate_checkpoint(
    checkpoint: dict,
    *,
    source: Path,
    input_report: Path,
    report: dict,
) -> None:
    expected = {
        "source_sha256": _sha256_file(source),
        "report_sha256": _sha256_file(input_report),
        "manifest_sha256": report["binding"]["manifest_sha256"],
        "run_label": report["run"]["run_label"],
    }
    if checkpoint.get("experiment_id") != EXPERIMENT_ID:
        raise RuntimeError("checkpoint di un altro esperimento")
    if checkpoint.get("input") != expected:
        raise RuntimeError("checkpoint non legato agli stessi artefatti")
    if checkpoint.get("prompt_sha256", {}).get(
        "evidence_first_contract"
    ) != _sha256(EVIDENCE_FIRST_HEADER):
        raise RuntimeError("contratto evidence-first divergente")


def run(
    *,
    input_root: Path,
    output_root: Path,
    source: Path,
    max_reports: int | None = None,
    max_questions: int | None = None,
) -> dict:
    import config
    from core.ollama_client import chat_client

    reports = sorted((input_root / "runs").glob("*.json"))
    if max_reports is not None:
        reports = reports[:max_reports]
    total_new = total_reused = 0
    started = time.perf_counter()

    for report_index, input_report in enumerate(reports, 1):
        report = json.loads(input_report.read_text(encoding="utf-8"))
        sample_id = report["dataset"]["sample_id"]
        case = _load_case(input_root, sample_id, source)
        questions = {q.question_id: q for q in case.questions}
        contexts = _reconstruct_contexts(report, case)
        arms = _arm_map(report)
        append_results = _result_map(arms["dual_channel"])
        question_ids = list(report["selection"]["question_ids"])
        if max_questions is not None:
            question_ids = question_ids[:max_questions]

        output = output_root / "runs" / input_report.name
        if output.exists():
            checkpoint = json.loads(output.read_text(encoding="utf-8"))
            _validate_checkpoint(
                checkpoint,
                source=source,
                input_report=input_report,
                report=report,
            )
        else:
            checkpoint = _new_checkpoint(
                source=source,
                input_root=input_root,
                input_report=input_report,
                report=report,
            )

        seed = int(report["run"]["answer_seed"])
        for question_index, question_id in enumerate(question_ids):
            # La baseline append è già stata generata nella run originale.
            if question_id not in checkpoint["arms"][APPEND]:
                checkpoint["arms"][APPEND][question_id] = {
                    "answer": append_results[question_id]["answer"],
                    "reused": True,
                    "context_sha256": _sha256(contexts[question_id][APPEND]),
                }
                total_reused += 1

            variants = (
                GENERATED_ARMS
                if (question_index + report_index) % 2 == 0
                else tuple(reversed(GENERATED_ARMS))
            )
            for arm in variants:
                if question_id in checkpoint["arms"][arm]:
                    continue
                # Nessuna aggiunta: la variante è byte-identica ad append e la
                # risposta già generata è il controfattuale esatto.
                if contexts[question_id][arm] == contexts[question_id][APPEND]:
                    checkpoint["arms"][arm][question_id] = {
                        "answer": append_results[question_id]["answer"],
                        "reused": True,
                        "context_sha256": _sha256(contexts[question_id][arm]),
                    }
                    total_reused += 1
                else:
                    generated_at = time.perf_counter()
                    response = chat_client.chat(
                        model=config.OLLAMA_MODEL,
                        messages=[
                            {"role": "system", "content": _ANSWER_SYSTEM_IT},
                            {
                                "role": "user",
                                "content": _user_prompt(
                                    case,
                                    questions[question_id].prompt(),
                                    contexts[question_id][arm],
                                ),
                            },
                        ],
                        options={
                            "temperature": 0,
                            "num_predict": 160,
                            "seed": seed,
                        },
                        think=False,
                    )
                    checkpoint["arms"][arm][question_id] = {
                        "answer": _response_content(response),
                        "reused": False,
                        "latency_s": round(time.perf_counter() - generated_at, 3),
                        "context_sha256": _sha256(contexts[question_id][arm]),
                    }
                    total_new += 1
                checkpoint["updated_at"] = time.time()
                _atomic_json(output, checkpoint)
                print(
                    json.dumps(
                        {
                            "event": "answer",
                            "run": report["run"]["run_label"],
                            "question_id": question_id,
                            "arm": arm,
                            "new_generations": total_new,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

        checkpoint["complete"] = all(
            len(checkpoint["arms"][arm]) == len(question_ids)
            for arm in (APPEND, PREPEND, EVIDENCE_FIRST)
        )
        checkpoint["completed_question_ids"] = question_ids
        checkpoint["updated_at"] = time.time()
        _atomic_json(output, checkpoint)

    summary = {
        "experiment_id": EXPERIMENT_ID,
        "reports": len(reports),
        "new_generations": total_new,
        "reused_answers": total_reused,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }
    _atomic_json(output_root / "run_summary.json", summary)
    return summary


def _mean(rows: list[float]) -> float:
    return statistics.fmean(rows) if rows else 0.0


def _cluster_ci(per_conversation: dict[str, float], *, seed: int = 8128) -> dict:
    values = list(per_conversation.values())
    rng = random.Random(seed)
    samples = [
        _mean([rng.choice(values) for _ in values])
        for _ in range(10_000)
    ]
    samples.sort()
    return {
        "point_estimate": _mean(values),
        "ci_low": samples[249],
        "ci_high": samples[9749],
        "n_clusters": len(values),
        "resamples": 10_000,
    }


def analyze(
    *,
    input_root: Path,
    output_root: Path,
    source: Path,
) -> dict:
    input_reports = sorted((input_root / "runs").glob("*.json"))
    rows: list[dict] = []
    per_run_scoring: dict[str, dict] = {}

    for input_report in input_reports:
        report = json.loads(input_report.read_text(encoding="utf-8"))
        replay_path = output_root / "runs" / input_report.name
        if not replay_path.exists():
            raise RuntimeError(f"replay mancante: {replay_path}")
        replay = json.loads(replay_path.read_text(encoding="utf-8"))
        _validate_checkpoint(
            replay, source=source, input_report=input_report, report=report
        )
        if not replay.get("complete"):
            raise RuntimeError(f"replay incompleto: {replay_path}")

        sample_id = report["dataset"]["sample_id"]
        case = _load_case(input_root, sample_id, source)
        expected_ids = list(report["selection"]["question_ids"])
        for arm in (APPEND, PREPEND, EVIDENCE_FIRST):
            if set(replay["arms"][arm]) != set(expected_ids):
                raise RuntimeError(f"{input_report.name}: copertura {arm} divergente")

        scored = {}
        for arm in (APPEND, PREPEND, EVIDENCE_FIRST):
            results = [
                {
                    "question_id": question_id,
                    "answer": replay["arms"][arm][question_id]["answer"],
                    "recalled_turn_ids": (),
                    "metadata": {},
                }
                for question_id in expected_ids
            ]
            from benchmarks.euri_memory.contracts import QuestionResult

            scoring = score_locomo_reduced(
                case.questions,
                [QuestionResult(**result) for result in results],
            )
            scored[arm] = {
                item["question_id"]: item for item in scoring["items"]
            }
        per_run_scoring[report["run"]["run_label"]] = scored

        original_arms = _arm_map(report)
        rag_scores = _scoring_map(original_arms["rag_only"])
        dual_scores = _scoring_map(original_arms["dual_channel"])
        for question_id in expected_ids:
            row = {
                "sample_id": sample_id,
                "run_label": report["run"]["run_label"],
                "question_id": question_id,
                "answerable": bool(scored[APPEND][question_id]["answerable"]),
                "category": scored[APPEND][question_id]["category"],
                "evidence_flip": bool(
                    dual_scores[question_id]["evidence_hit"]
                    and not rag_scores[question_id]["evidence_hit"]
                ),
                "answers": {
                    arm: replay["arms"][arm][question_id]["answer"]
                    for arm in (APPEND, PREPEND, EVIDENCE_FIRST)
                },
                "scores": {
                    arm: scored[arm][question_id]
                    for arm in (APPEND, PREPEND, EVIDENCE_FIRST)
                },
            }
            rows.append(row)

    def arm_metrics(arm: str) -> dict:
        answerable = [row for row in rows if row["answerable"]]
        adversarial = [row for row in rows if not row["answerable"]]
        flips = [row for row in answerable if row["evidence_flip"]]
        return {
            "mean_token_f1": _mean(
                [row["scores"][arm]["token_f1"] for row in answerable]
            ),
            "adversarial_accuracy": _mean(
                [float(row["scores"][arm]["correct"]) for row in adversarial]
            ),
            "flip_mean_token_f1": _mean(
                [row["scores"][arm]["token_f1"] for row in flips]
            ),
            "flip_identical_to_append": sum(
                row["answers"][arm] == row["answers"][APPEND] for row in flips
            ),
            "flip_count": len(flips),
        }

    metrics = {
        arm: arm_metrics(arm) for arm in (APPEND, PREPEND, EVIDENCE_FIRST)
    }
    comparisons = {}
    for arm in (PREPEND, EVIDENCE_FIRST):
        answerable = [row for row in rows if row["answerable"]]
        per_conversation = {}
        for sample_id in sorted({row["sample_id"] for row in answerable}):
            sample_rows = [
                row for row in answerable if row["sample_id"] == sample_id
            ]
            per_conversation[sample_id] = _mean(
                [
                    row["scores"][arm]["token_f1"]
                    - row["scores"][APPEND]["token_f1"]
                    for row in sample_rows
                ]
            )
        deltas = [
            row["scores"][arm]["token_f1"]
            - row["scores"][APPEND]["token_f1"]
            for row in answerable
        ]
        comparison = {
            "delta_mean_token_f1": metrics[arm]["mean_token_f1"]
            - metrics[APPEND]["mean_token_f1"],
            "delta_adversarial_accuracy": metrics[arm]["adversarial_accuracy"]
            - metrics[APPEND]["adversarial_accuracy"],
            "delta_flip_mean_token_f1": metrics[arm]["flip_mean_token_f1"]
            - metrics[APPEND]["flip_mean_token_f1"],
            "answerable_improved": sum(delta > 0 for delta in deltas),
            "answerable_worsened": sum(delta < 0 for delta in deltas),
            "answerable_tied": sum(delta == 0 for delta in deltas),
            "per_conversation_delta": per_conversation,
            "cluster_bootstrap": _cluster_ci(per_conversation),
        }
        comparison["dev_gate"] = (
            "GO_DEV"
            if comparison["delta_mean_token_f1"] > 0
            and comparison["delta_flip_mean_token_f1"] > 0
            and comparison["delta_adversarial_accuracy"] >= -0.02
            and sum(delta >= 0 for delta in per_conversation.values()) >= 4
            else "NO_GO_DEV"
        )
        comparisons[arm] = comparison

    result = {
        "experiment_id": EXPERIMENT_ID,
        "interpretation": (
            "development ablation su conversazioni ormai aperte; "
            "non validazione indipendente"
        ),
        "instances": len(rows),
        "answerable": sum(row["answerable"] for row in rows),
        "adversarial": sum(not row["answerable"] for row in rows),
        "metrics": metrics,
        "comparisons_vs_append": comparisons,
    }
    _atomic_json(output_root / "analysis.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path(
            "audit_output/dual_channel_validation_v1_seed396895560/run"
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            "audit_output/dual_channel_prompt_ablation_v1_seed396895560"
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("benchmarks/euri_memory/data/locomo10.json"),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("dry-run")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--max-reports", type=int)
    run_parser.add_argument("--max-questions", type=int)
    sub.add_parser("run-all")
    sub.add_parser("analyze")
    args = parser.parse_args()

    if args.command == "dry-run":
        result = dry_run(args.input_root, args.source)
    elif args.command == "run":
        result = run(
            input_root=args.input_root,
            output_root=args.output_root,
            source=args.source,
            max_reports=args.max_reports,
            max_questions=args.max_questions,
        )
    elif args.command == "run-all":
        run_result = run(
            input_root=args.input_root,
            output_root=args.output_root,
            source=args.source,
        )
        result = {
            "run": run_result,
            "analysis": analyze(
                input_root=args.input_root,
                output_root=args.output_root,
                source=args.source,
            ),
        }
    else:
        result = analyze(
            input_root=args.input_root,
            output_root=args.output_root,
            source=args.source,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
