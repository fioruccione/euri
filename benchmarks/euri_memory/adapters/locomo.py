"""Adapter per il formato ufficiale LoCoMo ACL 2024."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.euri_memory.contracts import (
    BenchmarkCase,
    BenchmarkQuestion,
    ConversationSession,
    ConversationTurn,
)


_SESSION_RE = re.compile(r"^session_(\d+)$")
_EVIDENCE_ID_RE = re.compile(r"D\d+:\d+")
_CATEGORY_NAMES = {
    1: "single_hop",
    2: "temporal",
    3: "multi_hop",
    4: "open_domain",
    5: "adversarial",
}


class LoCoMoFormatError(ValueError):
    pass


class LoCoMoAdapter:
    dataset_name = "locomo"

    def load(self, source: Path) -> Sequence[BenchmarkCase]:
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoCoMoFormatError(f"impossibile leggere {source}: {exc}") from exc
        if not isinstance(raw, list):
            raise LoCoMoFormatError("la radice LoCoMo deve essere una lista")
        return tuple(self._convert_sample(item, index) for index, item in enumerate(raw))

    def _convert_sample(self, raw: Any, index: int) -> BenchmarkCase:
        if not isinstance(raw, Mapping):
            raise LoCoMoFormatError(f"sample {index}: oggetto JSON atteso")
        sample_id = str(raw.get("sample_id") or f"sample-{index + 1}")
        conversation = raw.get("conversation")
        if not isinstance(conversation, Mapping):
            raise LoCoMoFormatError(f"{sample_id}: conversation mancante")

        speaker_a = self._required_text(conversation, "speaker_a", sample_id)
        speaker_b = self._required_text(conversation, "speaker_b", sample_id)
        role_by_speaker = {
            speaker_a: "speaker_a",
            speaker_b: "speaker_b",
        }

        numbered_sessions = []
        for key, value in conversation.items():
            match = _SESSION_RE.match(str(key))
            if match:
                numbered_sessions.append((int(match.group(1)), str(key), value))
        numbered_sessions.sort()
        if not numbered_sessions:
            raise LoCoMoFormatError(f"{sample_id}: nessuna sessione")

        seen_turns: set[str] = set()
        sessions = []
        for number, session_id, raw_turns in numbered_sessions:
            if not isinstance(raw_turns, list):
                raise LoCoMoFormatError(f"{sample_id}/{session_id}: lista turni attesa")
            timestamp_value = conversation.get(f"session_{number}_date_time")
            timestamp = str(timestamp_value) if timestamp_value is not None else None
            turns = []
            for turn_index, raw_turn in enumerate(raw_turns):
                if not isinstance(raw_turn, Mapping):
                    raise LoCoMoFormatError(
                        f"{sample_id}/{session_id}/{turn_index}: turno non valido"
                    )
                turn_id = self._required_text(raw_turn, "dia_id", sample_id)
                if turn_id in seen_turns:
                    raise LoCoMoFormatError(f"{sample_id}: dia_id duplicato {turn_id}")
                seen_turns.add(turn_id)
                speaker = self._required_text(raw_turn, "speaker", sample_id)
                text = self._required_text(raw_turn, "text", sample_id)
                metadata = {
                    key: value
                    for key, value in raw_turn.items()
                    if key not in {"dia_id", "speaker", "text"}
                }
                turns.append(
                    ConversationTurn(
                        turn_id=turn_id,
                        speaker=speaker,
                        speaker_role=role_by_speaker.get(speaker, "other"),
                        text=text,
                        session_id=session_id,
                        session_timestamp=timestamp,
                        metadata=metadata,
                    )
                )
            sessions.append(
                ConversationSession(
                    session_id=session_id,
                    timestamp=timestamp,
                    turns=tuple(turns),
                )
            )

        raw_questions = raw.get("qa")
        if not isinstance(raw_questions, list):
            raise LoCoMoFormatError(f"{sample_id}: qa deve essere una lista")
        questions = tuple(
            self._convert_question(sample_id, item, question_index, seen_turns)
            for question_index, item in enumerate(raw_questions)
        )
        return BenchmarkCase(
            dataset=self.dataset_name,
            sample_id=sample_id,
            speakers=(speaker_a, speaker_b),
            sessions=tuple(sessions),
            questions=questions,
            metadata={
                key: value
                for key, value in raw.items()
                if key not in {"sample_id", "conversation", "qa"}
            },
        )

    def _convert_question(
        self,
        sample_id: str,
        raw: Any,
        index: int,
        known_turns: set[str],
    ) -> BenchmarkQuestion:
        if not isinstance(raw, Mapping):
            raise LoCoMoFormatError(f"{sample_id}/qa/{index}: oggetto atteso")
        text = self._required_text(raw, "question", sample_id)
        raw_category = raw.get("category")
        try:
            category_number = int(raw_category)
        except (TypeError, ValueError):
            category_number = 0
        category = _CATEGORY_NAMES.get(category_number, f"category_{raw_category}")
        evidence_raw = raw.get("evidence") or []
        if not isinstance(evidence_raw, list):
            raise LoCoMoFormatError(f"{sample_id}/qa/{index}: evidence non è una lista")
        evidence_items = []
        for item in evidence_raw:
            value = str(item)
            # Nel rilascio ufficiale esistono celle come "D8:6; D9:17":
            # semanticamente sono due riferimenti, benché occupino un solo item.
            extracted = _EVIDENCE_ID_RE.findall(value)
            evidence_items.extend(extracted or [value])
        evidence = tuple(evidence_items)
        unknown_evidence = sorted(set(evidence) - known_turns)
        metadata = {
            key: value
            for key, value in raw.items()
            if key not in {"question", "answer", "category", "evidence"}
        }
        return BenchmarkQuestion(
            question_id=f"{sample_id}:q{index + 1}",
            text=text,
            expected_answer=raw.get("answer"),
            category=category,
            evidence_turn_ids=evidence,
            metadata={
                "category_id": category_number,
                "missing_evidence_turn_ids": unknown_evidence,
                **metadata,
            },
        )

    @staticmethod
    def _required_text(raw: Mapping[str, Any], key: str, sample_id: str) -> str:
        value = raw.get(key)
        if value is None or not str(value).strip():
            raise LoCoMoFormatError(f"{sample_id}: campo testuale {key!r} mancante")
        return str(value).strip()
