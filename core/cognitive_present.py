"""Transient, versioned working state for Euri's immediate present.

The state remains independent from Redis and long-term memory. The voice daemon
feeds it accepted turns, playback boundaries and pending questions; consumers use
versioned snapshots and decision tokens instead of treating stale state as current.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class EpistemicStatus(str, Enum):
    OBSERVED = "observed"
    USER_ASSERTED = "user_asserted"
    SYSTEM_FACT = "system_fact"
    INFERRED = "inferred"
    HYPOTHETICAL = "hypothetical"
    REFUTED = "refuted"


class InteractionPhase(str, Enum):
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"


class InteractionChannel(str, Enum):
    VOICE = "voice"
    MOBILE = "mobile"
    TEXT = "text"
    SYSTEM = "system"


_GROUNDED_STATUSES = frozenset({
    EpistemicStatus.OBSERVED,
    EpistemicStatus.USER_ASSERTED,
    EpistemicStatus.SYSTEM_FACT,
})


def _freeze_value(value: Any) -> Any:
    """Copy structured observations into recursively immutable values."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, Mapping):
        frozen = [(_freeze_value(key), _freeze_value(item)) for key, item in value.items()]
        return tuple(sorted(frozen, key=lambda pair: repr(pair[0])))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze_value(item) for item in value), key=repr))
    raise TypeError(f"unsupported mutable observation value: {type(value).__name__}")


@dataclass(frozen=True)
class Observation:
    key: str
    value: Any
    status: EpistemicStatus
    source: str
    observed_at: float
    ttl_s: float | None = None
    valid_until: float | None = None
    evidence_ref: str = ""

    def is_current(self, now: float) -> bool:
        return self.status is not EpistemicStatus.REFUTED and (
            self.valid_until is None or now <= self.valid_until
        )

    def is_grounded(self, now: float) -> bool:
        return self.status in _GROUNDED_STATUSES and self.is_current(now)

    def semantic_fingerprint(self) -> tuple[Any, ...]:
        return (self.key, self.value, self.status, self.source, self.ttl_s, self.evidence_ref)


@dataclass(frozen=True)
class CognitiveSnapshot:
    version: int
    captured_at: float
    phase: InteractionPhase
    channel: InteractionChannel
    conversation_lease_until: float
    last_user_turn_id: int
    last_user_text: str
    pending_question_id: str
    pending_question_text: str
    focus_until: float
    recent_user_turns: tuple[tuple[int, str, float], ...]
    observations: tuple[Observation, ...]

    def observation(self, key: str) -> Observation | None:
        return next((item for item in self.observations if item.key == key), None)

    def conversation_open(self, now: float | None = None) -> bool:
        at = self.captured_at if now is None else now
        return self.conversation_lease_until > 0 and at <= self.conversation_lease_until

    def focus_open(self, now: float | None = None) -> bool:
        at = self.captured_at if now is None else now
        return self.focus_until > 0 and at <= self.focus_until and bool(self.recent_user_turns)

    def focus_text(self, *, max_chars: int = 1800) -> str:
        """Contesto dei turni utente recenti, senza sintesi o inferenze interne."""
        text = "\n".join(turn[1] for turn in self.recent_user_turns if turn[1]).strip()
        return text[-max_chars:]


@dataclass(frozen=True)
class DecisionToken:
    """State dependency captured before an asynchronous cognitive decision."""

    version: int
    observation_keys: tuple[str, ...]


class CognitivePresent:
    """Thread-safe state machine for seconds/minutes, never long-term memory."""

    def __init__(
        self,
        *,
        conversation_window_s: float = 45.0,
        focus_window_s: float = 300.0,
        max_focus_turns: int = 4,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if conversation_window_s <= 0:
            raise ValueError("conversation_window_s must be positive")
        if focus_window_s <= 0:
            raise ValueError("focus_window_s must be positive")
        if max_focus_turns <= 0:
            raise ValueError("max_focus_turns must be positive")
        self._clock = clock
        self._conversation_window_s = float(conversation_window_s)
        self._focus_window_s = float(focus_window_s)
        self._max_focus_turns = int(max_focus_turns)
        self._lock = threading.RLock()
        self._version = 0
        self._phase = InteractionPhase.LISTENING
        self._channel = InteractionChannel.SYSTEM
        self._conversation_lease_until = 0.0
        self._last_user_turn_id = 0
        self._last_user_text = ""
        self._pending_question_id = ""
        self._pending_question_text = ""
        self._focus_until = 0.0
        self._recent_user_turns: list[tuple[int, str, float]] = []
        self._observations: dict[str, Observation] = {}
        self._speech_started_at: float | None = None
        self._speech_opens_conversation = False

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def _bump(self) -> None:
        self._version += 1

    def snapshot(self, *, now: float | None = None) -> CognitiveSnapshot:
        at = self._clock() if now is None else now
        with self._lock:
            return CognitiveSnapshot(
                version=self._version,
                captured_at=at,
                phase=self._phase,
                channel=self._channel,
                conversation_lease_until=self._conversation_lease_until,
                last_user_turn_id=self._last_user_turn_id,
                last_user_text=self._last_user_text,
                pending_question_id=self._pending_question_id,
                pending_question_text=self._pending_question_text,
                focus_until=self._focus_until,
                recent_user_turns=tuple(self._recent_user_turns),
                observations=tuple(sorted(self._observations.values(), key=lambda item: item.key)),
            )

    def observe(
        self,
        key: str,
        value: Any,
        *,
        status: EpistemicStatus,
        source: str,
        observed_at: float | None = None,
        ttl_s: float | None = None,
        evidence_ref: str = "",
    ) -> Observation:
        if not key.strip() or not source.strip():
            raise ValueError("observation key and source are required")
        if ttl_s is not None and ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        at = self._clock() if observed_at is None else observed_at
        item = Observation(
            key=key.strip(),
            value=_freeze_value(value),
            status=EpistemicStatus(status),
            source=source.strip(),
            observed_at=at,
            ttl_s=ttl_s,
            valid_until=None if ttl_s is None else at + ttl_s,
            evidence_ref=evidence_ref,
        )
        with self._lock:
            previous = self._observations.get(item.key)
            if previous is not None and item.observed_at < previous.observed_at:
                return previous
            self._observations[item.key] = item
            # Sensor refreshes at 2 fps must not invalidate a decision when their
            # meaning is unchanged. Expiry is still checked during revalidation.
            if previous is None or previous.semantic_fingerprint() != item.semantic_fingerprint():
                self._bump()
        return item

    def accept_user_turn(
        self,
        text: str,
        *,
        channel: InteractionChannel = InteractionChannel.VOICE,
        at: float | None = None,
    ) -> int:
        if not text.strip():
            raise ValueError("accepted user turn cannot be empty")
        when = self._clock() if at is None else at
        with self._lock:
            self._last_user_turn_id += 1
            self._last_user_text = text.strip()
            self._channel = InteractionChannel(channel)
            self._phase = InteractionPhase.PROCESSING
            self._conversation_lease_until = max(
                self._conversation_lease_until,
                when + self._conversation_window_s,
            )
            self._focus_until = when + self._focus_window_s
            self._recent_user_turns.append(
                (self._last_user_turn_id, self._last_user_text, when)
            )
            self._recent_user_turns = self._recent_user_turns[-self._max_focus_turns:]
            self._bump()
            return self._last_user_turn_id

    def finish_processing(
        self,
        *,
        opens_conversation: bool = False,
        at: float | None = None,
    ) -> None:
        """Return to listening when a turn completes without voice playback."""
        when = self._clock() if at is None else at
        with self._lock:
            if self._phase is not InteractionPhase.PROCESSING:
                raise RuntimeError("finish_processing called outside processing")
            if opens_conversation:
                self._conversation_lease_until = max(
                    self._conversation_lease_until,
                    when + self._conversation_window_s,
                )
            self._phase = InteractionPhase.LISTENING
            self._bump()

    def begin_speech(
        self,
        *,
        channel: InteractionChannel = InteractionChannel.VOICE,
        opens_conversation: bool = True,
        at: float | None = None,
    ) -> None:
        when = self._clock() if at is None else at
        with self._lock:
            if self._phase is InteractionPhase.SPEAKING:
                raise RuntimeError("begin_speech called while already speaking")
            self._channel = InteractionChannel(channel)
            self._phase = InteractionPhase.SPEAKING
            self._speech_started_at = when
            self._speech_opens_conversation = bool(opens_conversation)
            self._bump()

    def finish_speech(self, *, at: float | None = None) -> None:
        when = self._clock() if at is None else at
        with self._lock:
            if self._phase is not InteractionPhase.SPEAKING or self._speech_started_at is None:
                raise RuntimeError("finish_speech called without begin_speech")
            if self._speech_opens_conversation:
                self._conversation_lease_until = max(
                    self._conversation_lease_until,
                    when + self._conversation_window_s,
                )
            self._phase = InteractionPhase.LISTENING
            self._speech_started_at = None
            self._speech_opens_conversation = False
            self._bump()

    def set_pending_question(self, question_id: str, text: str) -> None:
        if not question_id.strip():
            raise ValueError("question_id is required")
        with self._lock:
            new_value = (question_id.strip(), text.strip())
            old_value = (self._pending_question_id, self._pending_question_text)
            if new_value != old_value:
                self._pending_question_id, self._pending_question_text = new_value
                self._bump()

    def clear_pending_question(self, question_id: str | None = None) -> bool:
        with self._lock:
            if question_id and question_id != self._pending_question_id:
                return False
            if not self._pending_question_id:
                return False
            self._pending_question_id = ""
            self._pending_question_text = ""
            self._bump()
            return True

    def issue_decision_token(self, *observation_keys: str) -> DecisionToken:
        with self._lock:
            return DecisionToken(self._version, tuple(sorted(set(observation_keys))))

    def revalidate(
        self,
        token: DecisionToken,
        *,
        require_phase: InteractionPhase | None = None,
        require_grounded: tuple[str, ...] = (),
        now: float | None = None,
    ) -> tuple[bool, str]:
        at = self._clock() if now is None else now
        with self._lock:
            if token.version != self._version:
                return False, "state_version_changed"
            if require_phase is not None and self._phase is not InteractionPhase(require_phase):
                return False, f"phase:{self._phase.value}"
            for key in set(token.observation_keys) | set(require_grounded):
                item = self._observations.get(key)
                if item is None:
                    return False, f"missing_observation:{key}"
                if not item.is_current(at):
                    return False, f"stale_observation:{key}"
                if key in require_grounded and not item.is_grounded(at):
                    return False, f"ungrounded_observation:{key}"
            return True, "current"
