"""Contratti neutrali rispetto al dataset e al motore di memoria."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    speaker: str
    speaker_role: str
    text: str
    session_id: str
    session_timestamp: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationSession:
    session_id: str
    timestamp: str | None
    turns: tuple[ConversationTurn, ...]


@dataclass(frozen=True)
class BenchmarkQuestion:
    question_id: str
    text: str
    expected_answer: Any | None
    category: str
    evidence_turn_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def prompt(self) -> "QuestionPrompt":
        return QuestionPrompt(
            question_id=self.question_id,
            text=self.text,
            category=self.category,
        )


@dataclass(frozen=True)
class QuestionPrompt:
    """Vista consegnata al runner: gold ed evidence non sono accessibili."""

    question_id: str
    text: str
    category: str


@dataclass(frozen=True)
class ConversationCorpus:
    dataset: str
    sample_id: str
    speakers: tuple[str, ...]
    sessions: tuple[ConversationSession, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def turns(self) -> tuple[ConversationTurn, ...]:
        return tuple(turn for session in self.sessions for turn in session.turns)


@dataclass(frozen=True)
class BenchmarkCase:
    dataset: str
    sample_id: str
    speakers: tuple[str, ...]
    sessions: tuple[ConversationSession, ...]
    questions: tuple[BenchmarkQuestion, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def turns(self) -> tuple[ConversationTurn, ...]:
        return tuple(turn for session in self.sessions for turn in session.turns)

    def corpus(self) -> ConversationCorpus:
        """Vista di ingestione priva di domande, answer gold ed evidence."""

        return ConversationCorpus(
            dataset=self.dataset,
            sample_id=self.sample_id,
            speakers=self.speakers,
            sessions=self.sessions,
            # Osservazioni, summary ed event annotations possono contenere
            # informazione derivata o gold. Un profilo che le usa dovrà
            # trasformarle esplicitamente in turni, non riceverle di nascosto.
            metadata={},
        )


@dataclass(frozen=True)
class MemoryProfile:
    name: str
    passive_memory: bool = False
    consolidation: bool = False
    reflection: bool = False
    pulse: bool = False
    initiative: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    answer: str | None
    recalled_turn_ids: tuple[str, ...] = ()
    latency_ms: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ConversationAdapter(Protocol):
    dataset_name: str

    def load(self, source: Path) -> Sequence[BenchmarkCase]:
        """Carica un dataset e restituisce casi nel formato canonico."""


class QuestionRunner(Protocol):
    def run(
        self,
        sample_id: str,
        questions: Iterable[QuestionPrompt],
        profile: MemoryProfile,
    ) -> Sequence[QuestionResult]:
        """Interroga il sistema senza ricevere le risposte attese."""
