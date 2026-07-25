"""Scoring economico per lo smoke test, distinto dagli scorer ufficiali."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Sequence

from benchmarks.euri_memory.contracts import BenchmarkQuestion, QuestionResult


_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(part for part in _NON_WORD.split(text) if part)


def score_results(
    questions: Sequence[BenchmarkQuestion],
    results: Sequence[QuestionResult],
) -> dict:
    """Calcola solo gold-mention e abstention; non è la metrica LoCoMo."""

    by_id = {result.question_id: result for result in results}
    scored = []
    for question in questions:
        result = by_id.get(question.question_id)
        answer = result.answer if result else None
        if question.expected_answer is None:
            correct = answer is None
            metric = "abstention"
        else:
            expected = _normalize(question.expected_answer)
            actual = _normalize(answer or "")
            correct = bool(expected) and expected in actual
            metric = "gold_mention"
        scored.append(
            {
                "question_id": question.question_id,
                "category": question.category,
                "metric": metric,
                "correct": correct,
            }
        )
    correct_count = sum(1 for item in scored if item["correct"])
    return {
        "name": "phase0_deterministic_not_official",
        "correct": correct_count,
        "total": len(scored),
        "accuracy": correct_count / len(scored) if scored else 0.0,
        "items": scored,
    }
