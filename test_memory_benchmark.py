#!/usr/bin/env python3
"""Regressioni del banco prova memoria isolato."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from benchmarks.euri_memory.adapters import LoCoMoAdapter
from benchmarks.euri_memory.contracts import BenchmarkQuestion, QuestionResult
from benchmarks.euri_memory.runtime import IsolatedRuntime, IsolationError
from benchmarks.euri_memory.scorers import score_locomo_reduced
from benchmarks.euri_memory.selection import BenchmarkSelection
from benchmarks.euri_memory.localization import BenchmarkLocalization


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "benchmarks" / "euri_memory" / "fixtures" / "locomo_smoke.json"
OFFICIAL = ROOT / "benchmarks" / "euri_memory" / "data" / "locomo10.json"
REDUCED = (
    ROOT / "benchmarks" / "euri_memory" / "fixtures" / "locomo_reduced_v1.json"
)
REDUCED_V2 = (
    ROOT / "benchmarks" / "euri_memory" / "fixtures" / "locomo_reduced_v2.json"
)
ITALIAN_V2 = (
    ROOT
    / "benchmarks"
    / "euri_memory"
    / "fixtures"
    / "locomo_reduced_v2_it.json"
)


def test_locomo_adapter_preserves_sessions_evidence_and_gold_boundary():
    cases = LoCoMoAdapter().load(FIXTURE)
    assert len(cases) == 1
    case = cases[0]
    assert case.sample_id == "synthetic-isolation-1"
    assert len(case.sessions) == 2
    assert [turn.turn_id for turn in case.turns] == ["D1:1", "D1:2", "D2:1", "D2:2"]
    assert case.turns[1].speaker_role == "speaker_a"
    assert case.questions[0].category == "single_hop"
    assert case.questions[0].evidence_turn_ids == ("D1:2",)
    assert case.questions[0].expected_answer == "ramen"
    prompt = case.questions[0].prompt()
    assert prompt.text == case.questions[0].text
    assert not hasattr(prompt, "expected_answer")
    assert not hasattr(prompt, "evidence_turn_ids")
    corpus = case.corpus()
    assert not hasattr(corpus, "questions")
    assert corpus.metadata == {}


def test_locomo_adapter_marks_unknown_evidence():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw[0]["qa"][0]["evidence"] = ["D404:1"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "broken.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        question = LoCoMoAdapter().load(path)[0].questions[0]
        assert question.evidence_turn_ids == ("D404:1",)
        assert question.metadata["missing_evidence_turn_ids"] == ["D404:1"]


def test_isolation_guards_reject_personal_or_external_targets():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "runtime"
        redis_dir = root / "redis"
        vault_dir = root / "vault"
        report_dir = root / "reports"
        for path in (redis_dir, vault_dir, report_dir):
            path.mkdir(parents=True, exist_ok=True)
        try:
            IsolatedRuntime.validate_settings(
                root=root,
                redis_port=6379,
                redis_dir=redis_dir,
                vault_dir=vault_dir,
                report_dir=report_dir,
            )
        except IsolationError as exc:
            assert "personale" in str(exc)
        else:
            raise AssertionError("porta personale accettata")

        try:
            IsolatedRuntime.validate_settings(
                root=root,
                redis_port=16379,
                redis_dir=redis_dir,
                vault_dir=Path(tempfile.gettempdir()) / "vault-esterno",
                report_dir=report_dir,
            )
        except IsolationError as exc:
            assert "fuori dal runtime" in str(exc)
        else:
            raise AssertionError("Vault esterno accettato")


def test_reduced_selection_is_declared_and_gold_safe():
    if not OFFICIAL.is_file():
        return
    cases = LoCoMoAdapter().load(OFFICIAL)
    selection = BenchmarkSelection.load(REDUCED)
    case = selection.apply(cases)
    assert case.sample_id == "conv-26"
    assert len(case.sessions) == 2
    assert len(case.turns) == 35
    assert len(case.questions) == 8
    assert {question.category for question in case.questions} == {
        "single_hop",
        "temporal",
        "multi_hop",
        "open_domain",
        "adversarial",
    }
    assert all(
        set(question.evidence_turn_ids) <= {turn.turn_id for turn in case.turns}
        for question in case.questions
    )
    assert all(not hasattr(question.prompt(), "expected_answer") for question in case.questions)


def test_reduced_scorer_handles_answers_abstention_and_evidence():
    questions = (
        BenchmarkQuestion(
            question_id="q1",
            text="What food?",
            expected_answer="ramen noodles",
            category="single_hop",
            evidence_turn_ids=("D1:1",),
        ),
        BenchmarkQuestion(
            question_id="q2",
            text="Unsupported?",
            expected_answer=None,
            category="adversarial",
            evidence_turn_ids=("D1:2",),
            metadata={"adversarial_answer": "pizza"},
        ),
    )
    results = (
        QuestionResult(
            question_id="q1",
            answer="ramen noodles",
            recalled_turn_ids=("D1:1",),
        ),
        QuestionResult(
            question_id="q2",
            answer="I don't know.",
            recalled_turn_ids=("D1:2",),
        ),
    )
    scoring = score_locomo_reduced(questions, results)
    assert scoring["mean_token_f1"] == 1.0
    assert scoring["exact_match"] == 1.0
    assert scoring["adversarial_accuracy"] == 1.0
    assert scoring["evidence_recall"] == 1.0


def test_italian_localization_is_complete_and_gold_safe():
    if not OFFICIAL.is_file():
        return
    cases = LoCoMoAdapter().load(OFFICIAL)
    case = BenchmarkSelection.load(REDUCED_V2).apply(cases)
    localized = BenchmarkLocalization.load(ITALIAN_V2).apply(case)
    assert localized.metadata["language"] == "it"
    assert len(localized.turns) == len(case.turns) == 51
    assert len(localized.questions) == len(case.questions) == 8
    assert [turn.turn_id for turn in localized.turns] == [
        turn.turn_id for turn in case.turns
    ]
    assert [
        question.evidence_turn_ids for question in localized.questions
    ] == [question.evidence_turn_ids for question in case.questions]
    assert all(question.text != source.text for question, source in zip(
        localized.questions, case.questions, strict=True
    ))


def test_reduced_scorer_recognizes_italian_abstention():
    question = BenchmarkQuestion(
        question_id="q-it",
        text="Informazione assente?",
        expected_answer=None,
        category="adversarial",
        evidence_turn_ids=("D1:1",),
        metadata={"adversarial_answer": "risposta falsa"},
    )
    result = QuestionResult(
        question_id="q-it",
        answer="Non lo so.",
        recalled_turn_ids=("D1:1",),
    )
    scoring = score_locomo_reduced((question,), (result,))
    assert scoring["adversarial_accuracy"] == 1.0


if __name__ == "__main__":
    test_locomo_adapter_preserves_sessions_evidence_and_gold_boundary()
    test_locomo_adapter_marks_unknown_evidence()
    test_isolation_guards_reject_personal_or_external_targets()
    test_reduced_selection_is_declared_and_gold_safe()
    test_reduced_scorer_handles_answers_abstention_and_evidence()
    test_italian_localization_is_complete_and_gold_safe()
    test_reduced_scorer_recognizes_italian_abstention()
    print("test_memory_benchmark: OK")
