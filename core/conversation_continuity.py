"""Continuita' conversazionale durevole ma temporanea.

L'archivio ``euri:turn:*`` resta la fonte verbatim. Questo modulo mantiene
soltanto un indice TTL degli ultimi turni e ne deriva uno workspace leggibile:
focus, entita' attive e fili conversazionali ancora aperti. Non crea memorie,
non sintetizza fatti e non autorizza azioni.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import config
from core.memory_scope import PERSONAL_SCOPE, normalize_scope


CONTINUITY_SCHEMA_VERSION = 1
CONTINUITY_KEY_PREFIX = "euri:continuity:v1:"
_REQUEST_ACTS = frozenset({
    "ASK",
    "REQUEST_ACTION",
    "REQUEST_MEMORY_SEARCH",
    "REQUEST_SAVE",
    "REQUEST_WEB_SEARCH",
})


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value or "")


def _question_ending(text: str) -> bool:
    """Segnale di superficie, non classificatore semantico o regex lessicale."""
    return str(text or "").rstrip().endswith("?")


@dataclass(frozen=True)
class WorkspaceLoop:
    loop_id: str
    kind: str
    opened_by_turn_ref: str
    text: str
    opened_at: float
    resolution: str = ""
    resolved_by_turn_ref: str = ""

    @property
    def open(self) -> bool:
        return not self.resolution


@dataclass(frozen=True)
class ContinuitySnapshot:
    schema_version: int
    memory_scope: str
    updated_at: float
    expires_at: float
    turns: tuple[dict, ...]
    focus_text: str
    active_entities: tuple[dict, ...]
    open_loops: tuple[WorkspaceLoop, ...]
    recent_resolutions: tuple[WorkspaceLoop, ...]

    def render_for_prompt(self, *, max_chars: int = 5000) -> str:
        """Rende stato temporaneo con un contratto epistemico esplicito."""
        lines = [
            "Continuità temporanea ripristinata dal processo precedente.",
            "È contesto conversazionale con fonti turno, non memoria a lungo termine "
            "né prova che uno stato sia ancora attuale.",
        ]
        if self.active_entities:
            names = [
                str(item.get("canonical_name") or item.get("observed_form") or "").strip()
                for item in self.active_entities
            ]
            names = [name for name in names if name]
            if names:
                lines.append("Entità attive osservate nei turni: " + ", ".join(names))
        if self.open_loops:
            lines.append("Fili ancora aperti:")
            for loop in self.open_loops:
                lines.append(
                    f"- [{loop.kind}; fonte {loop.opened_by_turn_ref}] {loop.text}"
                )
        if self.focus_text:
            lines.append("Ultimo focus espresso dall'utente: " + self.focus_text)
        rendered = "\n".join(lines)
        return rendered[:max_chars]


class ConversationContinuityStore:
    """Indice Redis TTL e proiezione deterministica del presente conversazionale."""

    def __init__(
        self,
        redis_client,
        *,
        ttl_s: int | None = None,
        max_turns: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.r = redis_client
        self.ttl_s = int(
            ttl_s
            if ttl_s is not None
            else getattr(config, "CONVERSATION_CONTINUITY_TTL_SECONDS", 6 * 3600)
        )
        self.max_turns = int(
            max_turns
            if max_turns is not None
            else getattr(config, "CONVERSATION_CONTINUITY_MAX_TURNS", 12)
        )
        if self.ttl_s <= 0 or self.max_turns <= 0:
            raise ValueError("continuity ttl_s and max_turns must be positive")
        self._clock = clock

    @staticmethod
    def _keys(memory_scope: str) -> tuple[str, str]:
        scope = normalize_scope(memory_scope or PERSONAL_SCOPE)
        base = f"{CONTINUITY_KEY_PREFIX}{scope}"
        return f"{base}:meta", f"{base}:turns"

    @staticmethod
    def _pending_key(memory_scope: str) -> str:
        scope = normalize_scope(memory_scope or PERSONAL_SCOPE)
        return f"{CONTINUITY_KEY_PREFIX}{scope}:pending"

    def set_pending(
        self,
        kind: str,
        data: dict,
        memory_scope: str | None = None,
        *,
        timeout_s: int = 300,
    ) -> None:
        """Conserva il filo operativo breve di una domanda già pronunciata."""
        kind = str(kind or "").strip()
        if kind not in {"reaction", "memory_verification"}:
            raise ValueError("tipo pending non supportato")
        now_ts = self._clock()
        ttl = max(1, min(int(timeout_s), self.ttl_s))
        payload = {
            "schema_version": CONTINUITY_SCHEMA_VERSION,
            "kind": kind,
            "memory_scope": normalize_scope(memory_scope or PERSONAL_SCOPE),
            "created_at": now_ts,
            "expires_at": now_ts + ttl,
            "data": json.loads(json.dumps(data, ensure_ascii=False, default=str)),
        }
        self.r.set(
            self._pending_key(payload["memory_scope"]),
            json.dumps(payload, ensure_ascii=False),
            ex=ttl,
        )

    def load_pending(
        self,
        memory_scope: str | None = None,
        *,
        now: float | None = None,
    ) -> dict | None:
        scope = normalize_scope(memory_scope or PERSONAL_SCOPE)
        raw = self.r.get(self._pending_key(scope))
        if not raw:
            return None
        try:
            payload = json.loads(_decode(raw))
            expires_at = float(payload["expires_at"])
            if (
                int(payload.get("schema_version") or 0) != CONTINUITY_SCHEMA_VERSION
                or payload.get("kind") not in {"reaction", "memory_verification"}
                or normalize_scope(payload.get("memory_scope")) != scope
                or not isinstance(payload.get("data"), dict)
                or (self._clock() if now is None else float(now)) >= expires_at
            ):
                return None
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None
        return payload

    def clear_pending(self, memory_scope: str | None = None) -> None:
        self.r.delete(self._pending_key(memory_scope or PERSONAL_SCOPE))

    def record(self, turn_document: dict) -> None:
        """Indicizza un turno gia' archiviato; non duplica il suo contenuto."""
        ref = str(turn_document.get("turn_ref") or "").strip()
        if not ref:
            raise ValueError("turn_ref richiesto per la continuita'")
        scope = normalize_scope(turn_document.get("memory_scope"))
        observed_at = float(turn_document.get("observed_at") or self._clock())
        if self._clock() - observed_at > self.ttl_s:
            # Backfill e replay archivistici non devono diventare "presente".
            return
        meta_key, turns_key = self._keys(scope)
        meta = {
            "schema_version": CONTINUITY_SCHEMA_VERSION,
            "memory_scope": scope,
            "updated_at": observed_at,
            "expires_at": observed_at + self.ttl_s,
        }
        # ZSET e metadati sono solo puntatori temporanei. I documenti originali
        # restano in euri:turn:* e continuano a essere la sola fonte verbatim.
        pipe = self.r.pipeline(transaction=True)
        pipe.zadd(turns_key, {ref: observed_at})
        pipe.zremrangebyrank(turns_key, 0, -(self.max_turns + 1))
        pipe.expire(turns_key, self.ttl_s)
        pipe.set(meta_key, json.dumps(meta, ensure_ascii=False), ex=self.ttl_s)
        pipe.execute()

    def _turn_document(self, turn_ref: str) -> dict | None:
        from core.conversation_turns import turn_key

        raw = self.r.json().get(turn_key(turn_ref), "$")
        if not raw:
            return None
        doc = raw[0] if isinstance(raw, list) else raw
        return dict(doc) if isinstance(doc, dict) else None

    @staticmethod
    def _derive(
        turns: list[dict],
    ) -> tuple[
        str,
        tuple[dict, ...],
        tuple[WorkspaceLoop, ...],
        tuple[WorkspaceLoop, ...],
    ]:
        focus_text = ""
        entities_by_name: dict[str, dict] = {}
        loops: dict[str, WorkspaceLoop] = {}

        for turn in turns:
            role = str(turn.get("role") or "")
            content = str(turn.get("interpreted_content", turn.get("content")) or "").strip()
            ref = str(turn.get("turn_ref") or "")
            at = float(turn.get("observed_at") or 0)
            frame = turn.get("semantic_frame") if isinstance(turn.get("semantic_frame"), dict) else {}

            if role == "user":
                acts = {str(item or "").upper() for item in frame.get("speech_acts") or []}
                has_semantic_substance = bool(
                    frame.get("facts")
                    or frame.get("entities")
                    or frame.get("actions")
                    or str(frame.get("memory_disposition") or "").lower() == "candidate"
                    or acts & _REQUEST_ACTS
                )
                # Conferme, saluti e chiusure no-store non devono spostare il
                # focus dal tema che stanno soltanto accompagnando. Per turni
                # legacy privi di frame manteniamo un fallback conservativo.
                if has_semantic_substance or (not frame and not focus_text):
                    focus_text = content
                for loop_id, loop in list(loops.items()):
                    if loop.open and loop.kind == "assistant_question":
                        loops[loop_id] = WorkspaceLoop(
                            **{
                                **loop.__dict__,
                                "resolution": "user_replied",
                                "resolved_by_turn_ref": ref,
                            }
                        )
                for entity in frame.get("entities") or []:
                    if not isinstance(entity, dict):
                        continue
                    name = str(
                        entity.get("canonical_name") or entity.get("observed_form") or ""
                    ).strip()
                    if not name:
                        continue
                    entities_by_name[name.casefold()] = {
                        "canonical_name": name,
                        "entity_type": str(entity.get("entity_type") or "unknown"),
                        "source_turn_ref": ref,
                        "observed_at": at,
                    }
                if acts & _REQUEST_ACTS:
                    loop_id = f"request:{ref}"
                    loops[loop_id] = WorkspaceLoop(
                        loop_id=loop_id,
                        kind="user_request",
                        opened_by_turn_ref=ref,
                        text=content,
                        opened_at=at,
                    )

            elif role == "assistant":
                for loop_id, loop in list(loops.items()):
                    if loop.open and loop.kind == "user_request":
                        loops[loop_id] = WorkspaceLoop(
                            **{
                                **loop.__dict__,
                                # Una risposta non prova l'esecuzione dell'azione.
                                "resolution": "assistant_replied",
                                "resolved_by_turn_ref": ref,
                            }
                        )
                if _question_ending(content):
                    loop_id = f"question:{ref}"
                    loops[loop_id] = WorkspaceLoop(
                        loop_id=loop_id,
                        kind="assistant_question",
                        opened_by_turn_ref=ref,
                        text=content,
                        opened_at=at,
                    )

        active_entities = tuple(
            sorted(entities_by_name.values(), key=lambda item: item["observed_at"])[-8:]
        )
        open_loops = tuple(loop for loop in loops.values() if loop.open)[-8:]
        recent_resolutions = tuple(loop for loop in loops.values() if not loop.open)[-8:]
        return focus_text, active_entities, open_loops, recent_resolutions

    def load(self, memory_scope: str | None = None, *, now: float | None = None) -> ContinuitySnapshot | None:
        scope = normalize_scope(memory_scope or PERSONAL_SCOPE)
        at = self._clock() if now is None else float(now)
        meta_key, turns_key = self._keys(scope)
        raw_meta = self.r.get(meta_key)
        if not raw_meta:
            return None
        try:
            meta = json.loads(_decode(raw_meta))
            if int(meta.get("schema_version") or 0) != CONTINUITY_SCHEMA_VERSION:
                return None
            updated_at = float(meta["updated_at"])
            expires_at = float(meta["expires_at"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return None
        if at > expires_at or at - updated_at > self.ttl_s:
            return None

        refs = [_decode(value) for value in self.r.zrange(turns_key, 0, -1)]
        turns = []
        for ref in refs[-self.max_turns:]:
            try:
                doc = self._turn_document(ref)
            except Exception:
                doc = None
            if (
                doc
                and normalize_scope(doc.get("memory_scope")) == scope
                and at - float(doc.get("observed_at") or 0) <= self.ttl_s
            ):
                turns.append(doc)
        turns.sort(key=lambda item: (float(item.get("observed_at") or 0), int(item.get("seq") or 0)))
        if not turns:
            return None
        focus_text, active_entities, open_loops, recent_resolutions = self._derive(turns)
        return ContinuitySnapshot(
            schema_version=CONTINUITY_SCHEMA_VERSION,
            memory_scope=scope,
            updated_at=updated_at,
            expires_at=expires_at,
            turns=tuple(turns),
            focus_text=focus_text,
            active_entities=active_entities,
            open_loops=open_loops,
            recent_resolutions=recent_resolutions,
        )
