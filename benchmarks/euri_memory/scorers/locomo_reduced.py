"""Metriche trasparenti per la prima LoCoMo A/B; nessun judge LLM."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Sequence

from benchmarks.euri_memory.contracts import BenchmarkQuestion, QuestionResult


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_ABSTENTION_RE = re.compile(
    r"\b(?:i\s+(?:do\s+not|don't)\s+know|not\s+(?:enough\s+)?information|"
    r"not\s+(?:stated|mentioned|provided|available)|cannot\s+(?:tell|determine|answer)|"
    r"insufficient\s+(?:information|context)|unknown|"
    r"non\s+(?:lo|la)\s+so|non\s+ci\s+sono\s+(?:abbastanza\s+)?informazioni|"
    r"non\s+(?:è|e)\s+(?:indicato|menzionato|specificato|disponibile)|"
    r"informazioni\s+insufficienti|contesto\s+insufficiente|sconosciuto)\b",
    re.IGNORECASE,
)


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(_TOKEN_RE.findall(text))


def _token_f1(expected: Any, actual: Any) -> float:
    gold = _normalize(expected).split()
    predicted = _normalize(actual).split()
    if not gold or not predicted:
        return float(gold == predicted)
    common = Counter(gold) & Counter(predicted)
    overlap = sum(common.values())
    if not overlap:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(gold)
    return 2 * precision * recall / (precision + recall)


def _is_abstention(answer: str | None) -> bool:
    return not _normalize(answer) or bool(_ABSTENTION_RE.search(answer or ""))


def score_locomo_reduced(
    questions: Sequence[BenchmarkQuestion],
    results: Sequence[QuestionResult],
) -> dict:
    by_id = {result.question_id: result for result in results}
    items = []
    by_category: dict[str, list[dict]] = defaultdict(list)
    for question in questions:
        result = by_id.get(question.question_id)
        answer = result.answer if result else None
        recalled = set(result.recalled_turn_ids if result else ())
        evidence = set(question.evidence_turn_ids)
        evidence_hit = bool(evidence & recalled) if evidence else None
        if question.expected_answer is None:
            false_answer = question.metadata.get("adversarial_answer")
            false_leak = (
                bool(_normalize(false_answer))
                and _normalize(false_answer) in _normalize(answer)
            )
            abstained = _is_abstention(answer)
            item = {
                "question_id": question.question_id,
                "category": question.category,
                "answerable": False,
                "abstained": abstained,
                "false_answer_leak": false_leak,
                "correct": abstained and not false_leak,
                "evidence_hit": evidence_hit,
                "token_f1": None,
                "exact_match": None,
            }
        else:
            expected = _normalize(question.expected_answer)
            actual = _normalize(answer)
            item = {
                "question_id": question.question_id,
                "category": question.category,
                "answerable": True,
                "abstained": _is_abstention(answer),
                "false_answer_leak": None,
                "correct": expected == actual,
                "evidence_hit": evidence_hit,
                "token_f1": _token_f1(question.expected_answer, answer),
                "exact_match": expected == actual,
            }
        items.append(item)
        by_category[question.category].append(item)

    answerable = [item for item in items if item["answerable"]]
    adversarial = [item for item in items if not item["answerable"]]
    evidence_scored = [item for item in items if item["evidence_hit"] is not None]
    categories = {}
    for category, category_items in sorted(by_category.items()):
        f1_values = [
            item["token_f1"]
            for item in category_items
            if item["token_f1"] is not None
        ]
        category_evidence = [
            item for item in category_items if item["evidence_hit"] is not None
        ]
        categories[category] = {
            "count": len(category_items),
            "mean_token_f1": sum(f1_values) / len(f1_values) if f1_values else None,
            "evidence_recall": (
                sum(bool(item["evidence_hit"]) for item in category_evidence)
                / len(category_evidence)
                if category_evidence else None
            ),
        }
    return {
        "name": "locomo_reduced_deterministic_v1_not_official",
        "answerable_count": len(answerable),
        "mean_token_f1": (
            sum(item["token_f1"] for item in answerable) / len(answerable)
            if answerable else 0.0
        ),
        "exact_match": (
            sum(bool(item["exact_match"]) for item in answerable) / len(answerable)
            if answerable else 0.0
        ),
        "adversarial_count": len(adversarial),
        "adversarial_accuracy": (
            sum(bool(item["correct"]) for item in adversarial) / len(adversarial)
            if adversarial else 0.0
        ),
        "evidence_recall": (
            sum(bool(item["evidence_hit"]) for item in evidence_scored)
            / len(evidence_scored)
            if evidence_scored else 0.0
        ),
        "categories": categories,
        "items": items,
    }
