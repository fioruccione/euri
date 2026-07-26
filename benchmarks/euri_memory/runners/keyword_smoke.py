"""Runner deterministico di cablaggio: nessun LLM, nessun accesso esterno."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

from benchmarks.euri_memory.contracts import (
    BenchmarkCase,
    ConversationCorpus,
    MemoryProfile,
    QuestionPrompt,
    QuestionResult,
)
from benchmarks.euri_memory.runtime import IsolatedRuntime
from benchmarks.euri_memory.scorers.deterministic import score_results


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text) if len(token) > 2}


class KeywordQuestionRunner:
    """Baseline minima usata per provare ingestione, recall, trace e scoring.

    Non rappresenta ancora il profilo ``rag_only`` di Euri. Il suo unico scopo è
    rendere testabile tutta la tubazione prima di coinvolgere embedder o LLM.
    """

    def __init__(self, runtime: IsolatedRuntime, *, top_k: int = 3):
        self.runtime = runtime
        self.top_k = top_k

    def ingest(self, corpus: ConversationCorpus) -> int:
        client = self.runtime.client
        written = 0
        for order, turn in enumerate(corpus.turns):
            key = self._turn_key(corpus.sample_id, turn.turn_id)
            doc = {
                "dataset": corpus.dataset,
                "sample_id": corpus.sample_id,
                "turn_id": turn.turn_id,
                "speaker": turn.speaker,
                "speaker_role": turn.speaker_role,
                "text": turn.text,
                "session_id": turn.session_id,
                "session_timestamp": turn.session_timestamp,
                "order": order,
                "metadata": dict(turn.metadata),
            }
            client.json().set(key, "$", doc)
            self._trace(
                "ingest",
                {
                    "sample_id": corpus.sample_id,
                    "turn_id": turn.turn_id,
                    "order": order,
                },
            )
            written += 1
        return written

    def run(
        self,
        sample_id: str,
        questions: Iterable[QuestionPrompt],
        profile: MemoryProfile,
    ) -> Sequence[QuestionResult]:
        docs = self._load_turns(sample_id)
        results = []
        for question in questions:
            started = time.perf_counter()
            query_tokens = _tokens(question.text)
            ranked = []
            for doc in docs:
                searchable = " ".join(
                    (
                        str(doc.get("speaker", "")),
                        str(doc.get("text", "")),
                        str(doc.get("session_timestamp", "")),
                    )
                )
                overlap = len(query_tokens & _tokens(searchable))
                ranked.append((overlap, int(doc["order"]), doc))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            recalled = [item for item in ranked[: self.top_k] if item[0] > 0]
            answer = None
            if recalled:
                answer = "\n".join(
                    self._render_turn(item[2])
                    for item in recalled
                )
            latency_ms = (time.perf_counter() - started) * 1000
            recalled_ids = tuple(str(item[2]["turn_id"]) for item in recalled)
            result = QuestionResult(
                question_id=question.question_id,
                answer=answer,
                recalled_turn_ids=recalled_ids,
                latency_ms=latency_ms,
                metadata={
                    "runner": "keyword_smoke",
                    "profile": profile.name,
                    "overlap_scores": [item[0] for item in recalled],
                },
            )
            self._trace(
                "question",
                {
                    "sample_id": sample_id,
                    "question_id": question.question_id,
                    "recalled_turn_ids": list(recalled_ids),
                    "answered": answer is not None,
                    "latency_ms": latency_ms,
                },
            )
            results.append(result)
        return tuple(results)

    def _load_turns(self, sample_id: str) -> list[dict]:
        client = self.runtime.client
        docs = []
        for key in client.scan_iter(match=self._turn_key(sample_id, "*")):
            value = client.json().get(key, "$")
            if value:
                docs.append(value[0])
        docs.sort(key=lambda item: int(item["order"]))
        return docs

    def _trace(self, event_type: str, payload: dict) -> None:
        self.runtime.client.xadd(
            "euri:benchmark:trace",
            {
                "event_type": event_type,
                "runtime_id": self.runtime.runtime_id,
                "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        )

    @staticmethod
    def _turn_key(sample_id: str, turn_id: str) -> str:
        return f"euri:benchmark:turn:{sample_id}:{turn_id}"

    @staticmethod
    def _render_turn(doc: dict) -> str:
        timestamp = doc.get("session_timestamp")
        prefix = f"[{timestamp}] " if timestamp else ""
        return f"{prefix}{doc['speaker']}: {doc['text']}"


def run_smoke(
    runtime: IsolatedRuntime,
    cases: Sequence[BenchmarkCase],
    *,
    profile: MemoryProfile,
) -> Path:
    """Esegue una run completa e scrive il report nel runtime temporaneo."""

    runtime.reset()
    runner = KeywordQuestionRunner(runtime)
    case_reports = []
    total_turns = 0
    total_questions = 0
    for case in cases:
        ingested = runner.ingest(case.corpus())
        prompts = tuple(question.prompt() for question in case.questions)
        results = runner.run(case.sample_id, prompts, profile)
        scores = score_results(case.questions, results)
        known_turns = {turn.turn_id for turn in case.turns}
        missing_evidence = sorted(
            {
                evidence
                for question in case.questions
                for evidence in question.evidence_turn_ids
                if evidence not in known_turns
            }
        )
        case_reports.append(
            {
                "sample_id": case.sample_id,
                "turns_ingested": ingested,
                "questions_run": len(results),
                "missing_evidence": missing_evidence,
                "results": [asdict(item) for item in results],
                "scores": scores,
            }
        )
        total_turns += ingested
        total_questions += len(results)

    assert runtime.report_dir is not None
    report = {
        "schema_version": 1,
        "benchmark": "euri_memory_phase0_smoke",
        "dataset": sorted({case.dataset for case in cases}),
        "profile": asdict(profile),
        "runtime": {
            "isolated": True,
            "runtime_id": runtime.runtime_id,
            "redis_host": "127.0.0.1",
            "redis_port": runtime.port,
            "vault": str(runtime.vault_dir),
        },
        "source": {
            "git_commit": _git_commit(),
        },
        "summary": {
            "cases": len(cases),
            "turns_ingested": total_turns,
            "questions_run": total_questions,
            "trace_events": runtime.client.xlen("euri:benchmark:trace"),
        },
        "cases": case_reports,
    }
    output = runtime.report_dir / "phase0_smoke.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return output


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
