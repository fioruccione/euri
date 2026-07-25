"""Selezioni ridotte, dichiarate e validate prima di una run costosa."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.euri_memory.contracts import BenchmarkCase, ConversationSession


class SelectionError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkSelection:
    selection_id: str
    dataset: str
    sample_id: str
    session_ids: tuple[str, ...]
    question_ids: tuple[str, ...]
    speaker_mapping: Mapping[str, str]
    metadata: Mapping[str, Any]

    @classmethod
    def load(cls, path: Path) -> "BenchmarkSelection":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SelectionError(f"selezione non leggibile {path}: {exc}") from exc
        required = ("selection_id", "dataset", "sample_id", "session_ids", "question_ids")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise SelectionError(f"campi selezione mancanti: {', '.join(missing)}")
        if len(set(raw["session_ids"])) != len(raw["session_ids"]):
            raise SelectionError("session_ids contiene duplicati")
        if len(set(raw["question_ids"])) != len(raw["question_ids"]):
            raise SelectionError("question_ids contiene duplicati")
        return cls(
            selection_id=str(raw["selection_id"]),
            dataset=str(raw["dataset"]),
            sample_id=str(raw["sample_id"]),
            session_ids=tuple(str(item) for item in raw["session_ids"]),
            question_ids=tuple(str(item) for item in raw["question_ids"]),
            speaker_mapping=dict(raw.get("speaker_mapping") or {}),
            metadata={
                key: value
                for key, value in raw.items()
                if key not in {
                    "selection_id",
                    "dataset",
                    "sample_id",
                    "session_ids",
                    "question_ids",
                    "speaker_mapping",
                }
            },
        )

    def apply(self, cases: Sequence[BenchmarkCase]) -> BenchmarkCase:
        matches = [
            case
            for case in cases
            if case.dataset == self.dataset and case.sample_id == self.sample_id
        ]
        if len(matches) != 1:
            raise SelectionError(
                f"atteso un solo caso {self.dataset}/{self.sample_id}, trovati {len(matches)}"
            )
        case = matches[0]
        sessions_by_id = {session.session_id: session for session in case.sessions}
        questions_by_id = {question.question_id: question for question in case.questions}
        missing_sessions = [item for item in self.session_ids if item not in sessions_by_id]
        missing_questions = [item for item in self.question_ids if item not in questions_by_id]
        if missing_sessions or missing_questions:
            raise SelectionError(
                f"selezione non risolta; sessioni={missing_sessions}, domande={missing_questions}"
            )
        sessions: tuple[ConversationSession, ...] = tuple(
            sessions_by_id[item] for item in self.session_ids
        )
        questions = tuple(questions_by_id[item] for item in self.question_ids)
        known_turns = {turn.turn_id for session in sessions for turn in session.turns}
        outside = {
            question.question_id: sorted(set(question.evidence_turn_ids) - known_turns)
            for question in questions
            if set(question.evidence_turn_ids) - known_turns
        }
        if outside:
            raise SelectionError(f"gold evidence fuori dalle sessioni selezionate: {outside}")
        return BenchmarkCase(
            dataset=case.dataset,
            sample_id=case.sample_id,
            speakers=case.speakers,
            sessions=sessions,
            questions=questions,
            metadata={
                "selection_id": self.selection_id,
                "speaker_mapping": dict(self.speaker_mapping),
            },
        )
