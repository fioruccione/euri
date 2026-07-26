"""Localizzazioni versionate applicate dopo la selezione del caso."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from benchmarks.euri_memory.contracts import (
    BenchmarkCase,
    BenchmarkQuestion,
    ConversationSession,
    ConversationTurn,
)


class LocalizationError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkLocalization:
    localization_id: str
    selection_id: str
    language: str
    source_language: str
    turns: Mapping[str, str]
    questions: Mapping[str, Mapping[str, Any]]

    @classmethod
    def load(cls, path: Path) -> "BenchmarkLocalization":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalizationError(f"localizzazione non leggibile {path}: {exc}") from exc
        required = (
            "localization_id",
            "selection_id",
            "language",
            "source_language",
            "turns",
            "questions",
        )
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise LocalizationError(
                f"campi localizzazione mancanti: {', '.join(missing)}"
            )
        if not isinstance(raw["turns"], dict) or not isinstance(raw["questions"], dict):
            raise LocalizationError("turns e questions devono essere oggetti")
        return cls(
            localization_id=str(raw["localization_id"]),
            selection_id=str(raw["selection_id"]),
            language=str(raw["language"]),
            source_language=str(raw["source_language"]),
            turns={str(key): str(value) for key, value in raw["turns"].items()},
            questions={
                str(key): dict(value) for key, value in raw["questions"].items()
            },
        )

    def apply(self, case: BenchmarkCase) -> BenchmarkCase:
        case_selection = str(case.metadata.get("selection_id") or "")
        if case_selection != self.selection_id:
            raise LocalizationError(
                f"localizzazione per {self.selection_id}, caso {case_selection}"
            )
        expected_turns = {turn.turn_id for turn in case.turns}
        expected_questions = {question.question_id for question in case.questions}
        missing_turns = sorted(expected_turns - set(self.turns))
        missing_questions = sorted(expected_questions - set(self.questions))
        if missing_turns or missing_questions:
            raise LocalizationError(
                "localizzazione incompleta; "
                f"turni={missing_turns}, domande={missing_questions}"
            )

        sessions = tuple(
            ConversationSession(
                session_id=session.session_id,
                timestamp=session.timestamp,
                turns=tuple(self._localize_turn(turn) for turn in session.turns),
            )
            for session in case.sessions
        )
        questions = tuple(self._localize_question(question) for question in case.questions)
        return BenchmarkCase(
            dataset=case.dataset,
            sample_id=case.sample_id,
            speakers=case.speakers,
            sessions=sessions,
            questions=questions,
            metadata={
                **case.metadata,
                "language": self.language,
                "source_language": self.source_language,
                "localization_id": self.localization_id,
            },
        )

    def _localize_turn(self, turn: ConversationTurn) -> ConversationTurn:
        text = self.turns.get(turn.turn_id, "").strip()
        if not text:
            raise LocalizationError(f"traduzione vuota per {turn.turn_id}")
        return replace(turn, text=text)

    def _localize_question(self, question: BenchmarkQuestion) -> BenchmarkQuestion:
        item = self.questions[question.question_id]
        text = str(item.get("text") or "").strip()
        if not text:
            raise LocalizationError(
                f"testo domanda vuoto per {question.question_id}"
            )
        if "answer" not in item:
            raise LocalizationError(
                f"risposta localizzata mancante per {question.question_id}"
            )
        answer = item["answer"]
        if question.expected_answer is None and answer is not None:
            raise LocalizationError(
                f"domanda avversariale resa answerable: {question.question_id}"
            )
        if question.expected_answer is not None and not str(answer or "").strip():
            raise LocalizationError(
                f"risposta vuota per domanda answerable {question.question_id}"
            )
        metadata = dict(question.metadata)
        if "adversarial_answer" in metadata:
            translated_false = str(item.get("adversarial_answer") or "").strip()
            if not translated_false:
                raise LocalizationError(
                    f"risposta avversariale mancante per {question.question_id}"
                )
            metadata["adversarial_answer"] = translated_false
        return replace(
            question,
            text=text,
            expected_answer=answer,
            metadata=metadata,
        )
