#!/usr/bin/env python3
"""A/B esplorativo K=5 vs K=10 sulle risposte LoCoMo italiane.

Il corpus vive in un Redis effimero. I due bracci condividono query embedding,
modello, prompt di sistema, temperatura e seed; cambia soltanto il numero di
memorie inserite nel contesto. L'ordine dei bracci e' alternato per domanda.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config
from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.contracts import QuestionResult
from benchmarks.euri_memory.live_worker import (
    _ANSWER_SYSTEM_IT,
    _ingest_raw_turns,
    _response_content,
    _response_metric,
)
from benchmarks.euri_memory.localization import BenchmarkLocalization
from benchmarks.euri_memory.runtime import IsolatedRuntime
from benchmarks.euri_memory.scorers import score_locomo_reduced
from benchmarks.euri_memory.selection import BenchmarkSelection
from core.embedder import Embedder
from core.memory_manager import MemoryManager
from core.ollama_client import chat_client
from utils.redis_client import _create_memory_index


LOCOMO = ROOT / "benchmarks/euri_memory/data/locomo10.json"
SELECTION = ROOT / "benchmarks/euri_memory/fixtures/locomo_reduced_v2.json"
LOCALIZATION = ROOT / "benchmarks/euri_memory/fixtures/locomo_reduced_v2_it.json"
ARMS = ("k5", "k10")
K_BY_ARM = {"k5": 5, "k10": 10}
ANSWER_SEED = 20260808
NUM_PREDICT = 160


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _query_cache(embedder: Embedder, query: str) -> dict[str, Any]:
    vector = embedder.encode(query, mode="query")
    if vector is None:
        raise RuntimeError(f"embedding query fallito: {query}")
    return {
        "entries": {
            query: {
                # Tutti i turni della fixture hanno questo dominio. Fissarlo
                # isola K dalla variabilita' del classificatore di dominio.
                "domain": "conversation",
                "vector": vector,
            }
        },
        "hits": 0,
    }


def _retrieve(
    memory: MemoryManager,
    query: str,
    k: int,
    cache: dict[str, Any],
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    docs = memory.search_memories(
        query,
        limit=k,
        touch=False,
        query_feature_cache=cache,
    )
    recalled = tuple(dict.fromkeys(
        str(doc.get("benchmark_turn_id"))
        for doc in docs
        if doc.get("benchmark_turn_id")
    ))
    return docs, recalled


def _render_context(docs: list[dict[str, Any]]) -> str:
    if not docs:
        return "(nessuna memoria pertinente trovata)"
    # Nessun ID gold o benchmark entra nel prompt.
    return "\n".join(
        f"- [{doc.get('domain') or 'generale'}] {doc.get('content') or ''}"
        for doc in docs
    )


def _answer(
    *,
    model: str,
    speakers: tuple[str, str],
    question: str,
    context: str,
) -> tuple[str, dict[str, Any]]:
    user_prompt = (
        f"Partecipanti alla conversazione: {speakers[0]} e {speakers[1]}.\n\n"
        f"Contesto di memoria:\n{context}\n\n"
        f"Domanda: {question}"
    )
    started = time.perf_counter()
    response = chat_client.chat(
        model=model,
        messages=[
            {"role": "system", "content": _ANSWER_SYSTEM_IT},
            {"role": "user", "content": user_prompt},
        ],
        options={
            "temperature": 0,
            "num_predict": NUM_PREDICT,
            "seed": ANSWER_SEED,
        },
        think=False,
    )
    return _response_content(response), {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "prompt_chars": len(_ANSWER_SYSTEM_IT) + len(user_prompt),
        "prompt_eval_count": _response_metric(response, "prompt_eval_count"),
        "eval_count": _response_metric(response, "eval_count"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cases = LoCoMoAdapter().load(LOCOMO)
    case = BenchmarkSelection.load(SELECTION).apply(cases)
    case = BenchmarkLocalization.load(LOCALIZATION).apply(case)

    embedder = Embedder()
    embedder.load()
    arm_results: dict[str, list[QuestionResult]] = {arm: [] for arm in ARMS}
    records: list[dict[str, Any]] = []
    started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="euri-depth-answer-ab-") as parent:
        with IsolatedRuntime(base_dir=Path(parent)) as runtime:
            client = runtime.client
            _create_memory_index(client)
            ingest = _ingest_raw_turns(client, embedder, case.corpus())
            memory = MemoryManager(client, embedder)

            for index, question in enumerate(case.questions):
                cache = _query_cache(embedder, question.text)
                prepared: dict[str, dict[str, Any]] = {}
                for arm in ARMS:
                    docs, recalled = _retrieve(
                        memory,
                        question.text,
                        K_BY_ARM[arm],
                        cache,
                    )
                    prepared[arm] = {
                        "docs": docs,
                        "recalled": recalled,
                        "context": _render_context(docs),
                    }

                order = ARMS if index % 2 == 0 else tuple(reversed(ARMS))
                answers: dict[str, dict[str, Any]] = {}
                for arm in order:
                    answer, generation = _answer(
                        model=config.OLLAMA_MODEL,
                        speakers=case.speakers,
                        question=question.text,
                        context=prepared[arm]["context"],
                    )
                    recalled = prepared[arm]["recalled"]
                    arm_results[arm].append(QuestionResult(
                        question_id=question.question_id,
                        answer=answer,
                        recalled_turn_ids=recalled,
                        latency_ms=generation["elapsed_ms"],
                        metadata={
                            "k": K_BY_ARM[arm],
                            "context_chars": len(prepared[arm]["context"]),
                            **generation,
                        },
                    ))
                    answers[arm] = {
                        "answer": answer,
                        "recalled_turn_ids": list(recalled),
                        "context_chars": len(prepared[arm]["context"]),
                        "retrieval_ids": [
                            str(doc.get("id") or "")
                            for doc in prepared[arm]["docs"]
                        ],
                        **generation,
                    }
                    print(json.dumps({
                        "event": "arm_complete",
                        "question_id": question.question_id,
                        "arm": arm,
                        "elapsed_ms": generation["elapsed_ms"],
                    }), flush=True)

                records.append({
                    "question_id": question.question_id,
                    "category": question.category,
                    "branch_order": list(order),
                    "arms": answers,
                })

    scoring = {
        arm: score_locomo_reduced(case.questions, results)
        for arm, results in arm_results.items()
    }
    metrics = (
        "mean_token_f1",
        "exact_match",
        "adversarial_accuracy",
        "evidence_recall",
    )
    report = {
        "schema": "euri_retrieval_depth_answer_ab_v1",
        "created_at": time.time(),
        "exploratory": True,
        "protocol": {
            "arms": K_BY_ARM,
            "branch_order": "alternato per domanda",
            "temperature": 0,
            "answer_seed": ANSWER_SEED,
            "num_predict": NUM_PREDICT,
            "model": config.OLLAMA_MODEL,
            "query_domain": "conversation fisso",
            "touch": False,
            "only_difference": "numero di memorie nel contesto",
        },
        "dataset": {
            "source_sha256": _sha256(LOCOMO),
            "selection_sha256": _sha256(SELECTION),
            "localization_sha256": _sha256(LOCALIZATION),
            "turns": len(case.turns),
            "questions": len(case.questions),
            "turns_ingested": ingest["turns"],
        },
        "scoring": scoring,
        "delta_k10_minus_k5": {
            metric: scoring["k10"][metric] - scoring["k5"][metric]
            for metric in metrics
        },
        "generation_totals": {
            arm: {
                "calls": len(results),
                "elapsed_ms": round(sum(result.latency_ms for result in results), 3),
                "prompt_chars": sum(
                    int(result.metadata.get("prompt_chars") or 0) for result in results
                ),
                "prompt_eval_count": sum(
                    int(result.metadata.get("prompt_eval_count") or 0) for result in results
                ),
                "eval_count": sum(
                    int(result.metadata.get("eval_count") or 0) for result in results
                ),
            }
            for arm, results in arm_results.items()
        },
        "questions": records,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
