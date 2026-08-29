"""Risoluzione effimera del soggetto per il recupero mnemonico.

Il frame semantico resta immutato. Questo modulo collega un follow-up mnemonico
all'ultimo turno owner nominalmente grounded nello stesso scope/segmento e
restituisce soltanto una vista read-only usata dal RAG nello stesso turno.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import config
from core.memory_scope import current_scope, normalize_scope, scope_of
from core.semantic_turn import trusted_memory_retrieval_plan


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _surface_supported_entity(entity: dict, message: dict, floor: float) -> dict | None:
    observed = str(entity.get("observed_form") or "").strip()
    canonical = str(entity.get("canonical_name") or observed).strip()
    observed_key = _key(observed)
    canonical_key = _key(canonical)
    if not observed_key or not canonical_key or not any(ch.isalpha() for ch in canonical):
        return None

    frame = message.get("semantic_frame") or {}
    try:
        confidence = float(entity.get("confidence", frame.get("confidence", 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < floor:
        return None

    raw_surface = _key(
        str(message.get("raw_content") or "")
        or str(message.get("content") or "")
    )
    interpreted_surface = _key(message.get("content") or "")
    observed_present = bool(observed_key and observed_key in raw_surface)
    canonical_present = bool(canonical_key and canonical_key in interpreted_surface)
    if not observed_present and not canonical_present:
        return None

    status = str(entity.get("status") or "mentioned").strip().lower()
    nominal_match = (
        observed_key == canonical_key
        or SequenceMatcher(None, observed_key, canonical_key).ratio() >= 0.72
        or status == "explicit_correction"
    )
    # Una coreferenza come ``loro -> Gio Style`` puo' essere utile al modello
    # nel turno, ma non diventa da sola un'ancora che apre memoria durevole.
    if not nominal_match:
        return None
    return {
        "entity": canonical[:160],
        "entity_type": str(entity.get("entity_type") or "other")[:64],
        "relevance": max(0.0, min(1.0, confidence)),
        "source_turn_ref": str(message.get("turn_ref") or ""),
    }


def _latest_contextual_focus(
    recent_history: list[dict] | None,
    *,
    memory_scope: str,
    floor: float,
) -> list[dict]:
    scoped_owner = [
        item for item in (recent_history or [])
        if item.get("role") == "user" and scope_of(item) == memory_scope
    ]
    if not scoped_owner:
        return []
    latest_segment = scoped_owner[-1].get("segment_id")
    for message in reversed(scoped_owner):
        if latest_segment is not None and message.get("segment_id") != latest_segment:
            continue
        frame = message.get("semantic_frame")
        if not isinstance(frame, dict) or frame.get("status") != "interpreted":
            continue
        try:
            frame_confidence = float(frame.get("confidence") or 0.0)
        except (TypeError, ValueError):
            frame_confidence = 0.0
        if frame_confidence < floor:
            continue
        if not (message.get("trusted") is True or frame.get("accepted_owner_turn") is True):
            continue
        anchors: list[dict] = []
        seen: set[str] = set()
        for entity in frame.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            anchor = _surface_supported_entity(entity, message, floor)
            key = _key((anchor or {}).get("entity") or "")
            if not anchor or not key or key in seen:
                continue
            seen.add(key)
            anchors.append(anchor)
            if len(anchors) >= 4:
                break
        if anchors:
            return anchors
    return []


def _append_missing_entities(text: str, anchors: list[dict]) -> str:
    text_key = _key(text)
    missing = [
        str(item.get("entity") or "").strip()
        for item in anchors
        if str(item.get("entity") or "").strip()
        and _key(item.get("entity") or "") not in text_key
    ]
    if not missing:
        return text
    return f"{text}\nEntita' attive del filo: {', '.join(missing)}"


@dataclass(frozen=True)
class RetrievalResolution:
    raw_query: str
    effective_query: str
    semantic_plan: dict | None
    contextual_focus: tuple[dict, ...] = field(default_factory=tuple)
    reason: str = "legacy"

    def diagnostics(self) -> dict:
        return {
            "enabled": self.reason != "disabled",
            "reason": self.reason,
            "query_changed": self.effective_query != self.raw_query,
            "raw_query": self.raw_query,
            "effective_query": self.effective_query,
            "contextual_focus": [dict(item) for item in self.contextual_focus],
            "source_turn_refs": list(dict.fromkeys(
                str(item.get("source_turn_ref") or "")
                for item in self.contextual_focus
                if item.get("source_turn_ref")
            )),
        }


def resolve_retrieval_context(
    text: str,
    semantic_frame: dict | None,
    recent_history: list[dict] | None,
    *,
    memory_scope: str | None = None,
    minimum_confidence: float | None = None,
) -> RetrievalResolution:
    """Costruisce il piano effettivo senza mutare input o stato persistente."""
    raw_query = str(text or "")
    if not getattr(config, "SYSTEMIC_RETRIEVAL_ENABLED", True):
        return RetrievalResolution(raw_query, raw_query, None, reason="disabled")

    floor = float(
        minimum_confidence
        if minimum_confidence is not None
        else getattr(config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72)
    )
    plan = trusted_memory_retrieval_plan(semantic_frame)
    if plan is not None and not plan.get("needed"):
        return RetrievalResolution(raw_query, raw_query, plan, reason="semantic_no_retrieval")

    frame_trusted = False
    acts: set[str] = set()
    if isinstance(semantic_frame, dict) and semantic_frame.get("status") == "interpreted":
        try:
            frame_trusted = float(semantic_frame.get("confidence") or 0.0) >= floor
        except (TypeError, ValueError):
            frame_trusted = False
        if frame_trusted:
            acts = {str(item or "").upper() for item in semantic_frame.get("speech_acts") or []}

    durable_requested = bool(
        (plan is not None and plan.get("needed"))
        or (frame_trusted and "REQUEST_MEMORY_SEARCH" in acts)
    )
    if not durable_requested:
        return RetrievalResolution(raw_query, raw_query, plan, reason="legacy")

    current_focus = [dict(item) for item in (plan or {}).get("focus") or []]
    if current_focus:
        effective = _append_missing_entities(raw_query, current_focus)
        return RetrievalResolution(
            raw_query,
            effective,
            plan,
            reason="current_frame_focus" if effective != raw_query else "current_query_grounded",
        )

    scope = normalize_scope(
        memory_scope
        or (semantic_frame or {}).get("memory_scope")
        or current_scope()
    )
    inherited = _latest_contextual_focus(
        recent_history,
        memory_scope=scope,
        floor=floor,
    )
    if not inherited:
        return RetrievalResolution(raw_query, raw_query, plan, reason="no_grounded_context_focus")

    effective_plan = {
        "needed": True,
        "focus": [
            {
                "entity": item["entity"],
                "role": "focus",
                "relevance": item["relevance"],
                "context_source_turn_ref": item.get("source_turn_ref") or "",
            }
            for item in inherited
        ],
        "relation": str((plan or {}).get("relation") or "continuita' contestuale bounded")[:240],
        "evidence_goal": str((plan or {}).get("evidence_goal") or "continuity"),
        "confidence": min(
            [float((semantic_frame or {}).get("confidence") or floor)]
            + [float(item["relevance"]) for item in inherited]
        ),
        "resolution_source": "recent_owner_turn",
    }
    return RetrievalResolution(
        raw_query,
        _append_missing_entities(raw_query, inherited),
        effective_plan,
        contextual_focus=tuple(inherited),
        reason="inherited_recent_owner_focus",
    )
