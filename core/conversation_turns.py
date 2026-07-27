"""Archivio durevole dei turni conversazionali originali.

I turni non sono memorie cognitive e non partecipano ai loop: sono evidenza
locale, immutabile e indirizzabile. Le memorie passive possono riferirli tramite
``turn_ref`` e il retrieval dual-channel può idratarli senza usare la parafrasi
come substrato di risposta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import config
from loguru import logger


TURN_KEY_PREFIX = "euri:turn:"
TURN_SCHEMA_VERSION = 1
_TURN_REF_RE = re.compile(r"^(?P<conversation>[^:\s]+):(?P<seq>[1-9]\d*)$")


def make_turn_ref(conversation_id: str, seq: int) -> str:
    conversation = str(conversation_id or "").strip()
    turn_seq = int(seq)
    if not conversation or ":" in conversation or turn_seq < 1:
        raise ValueError("conversation_id/seq non validi per turn_ref")
    return f"{conversation}:{turn_seq}"


def turn_key(turn_ref: str) -> str:
    match = _TURN_REF_RE.fullmatch(str(turn_ref or "").strip())
    if not match:
        raise ValueError(f"turn_ref non valido: {turn_ref!r}")
    return (
        f"{TURN_KEY_PREFIX}{match.group('conversation')}:{int(match.group('seq'))}"
    )


@dataclass(frozen=True)
class ArchivedTurn:
    turn_ref: str
    conversation_id: str
    seq: int
    role: str
    speaker: str
    content: str
    trusted: bool
    observed_at: float
    segment_id: int | None

    def render(self) -> str:
        return f"{self.speaker}: {self.content}"


class ConversationTurnStore:
    """Persistenza esatta, idempotente e senza TTL dei turni sorgente."""

    def __init__(self, redis_client):
        self.r = redis_client

    @staticmethod
    def _speaker(role: str) -> str:
        if role == "user":
            return config.OWNER_DISPLAY_NAME
        if role == "assistant":
            return config.ASSISTANT_DISPLAY_NAME
        return role or "?"

    def persist(self, message: dict) -> str:
        ref = str(message.get("turn_ref") or "").strip()
        if not ref:
            ref = make_turn_ref(message["conversation_id"], message["seq"])
        key = turn_key(ref)
        doc = {
            "schema_version": TURN_SCHEMA_VERSION,
            "turn_ref": ref,
            "conversation_id": str(message.get("conversation_id") or ""),
            "seq": int(message.get("seq")),
            "role": str(message.get("role") or ""),
            "speaker": self._speaker(str(message.get("role") or "")),
            "content": str(message.get("content") or ""),
            "trusted": bool(message.get("trusted")),
            "observed_at": float(message.get("observed_at")),
            "segment_id": message.get("segment_id"),
        }
        # Lo stesso ref identifica lo stesso turno: la riscrittura è idempotente.
        self.r.json().set(key, "$", doc)
        return ref

    def persist_many(self, messages: list[dict]) -> int:
        persisted = 0
        for message in messages:
            try:
                self.persist(message)
                persisted += 1
            except Exception as exc:
                logger.error(
                    "Archivio turni: persistenza fallita per {}: {}",
                    message.get("turn_ref") or message.get("seq"),
                    exc,
                )
                raise
        return persisted

    def get(self, turn_ref: str) -> ArchivedTurn | None:
        try:
            raw = self.r.json().get(turn_key(turn_ref), "$")
        except (TypeError, ValueError):
            return None
        except Exception as exc:
            logger.debug(f"Archivio turni: lettura {turn_ref} fallita ({exc})")
            return None
        if not raw:
            return None
        doc = raw[0] if isinstance(raw, list) else raw
        try:
            return ArchivedTurn(
                turn_ref=str(doc["turn_ref"]),
                conversation_id=str(doc["conversation_id"]),
                seq=int(doc["seq"]),
                role=str(doc.get("role") or ""),
                speaker=str(doc.get("speaker") or self._speaker(doc.get("role") or "")),
                content=str(doc.get("content") or ""),
                trusted=bool(doc.get("trusted")),
                observed_at=float(doc["observed_at"]),
                segment_id=(
                    int(doc["segment_id"])
                    if doc.get("segment_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Archivio turni: documento malformato per {turn_ref}")
            return None

    def render(self, turn_ref: str) -> str:
        turn = self.get(turn_ref)
        return turn.render() if turn else ""
