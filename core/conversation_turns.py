"""Archivio durevole dei turni conversazionali originali.

I turni non sono memorie cognitive e non partecipano ai loop: sono evidenza
locale, immutabile e indirizzabile. Le memorie passive possono riferirli tramite
``turn_ref`` e il retrieval dual-channel può idratarli senza usare la parafrasi
come substrato di risposta.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass

import config
from loguru import logger
from utils.date_utils import format_datetime_full, from_timestamp
from core.memory_scope import PERSONAL_SCOPE, normalize_scope


TURN_KEY_PREFIX = "euri:turn:"
TURN_SCHEMA_VERSION = 1
TURN_RENDER_VERSION = "absolute-time-auth-channel-v1"
VERBATIM_LIFECYCLE_REPORT_KEY = "euri:verbatim:lifecycle:latest"
VERBATIM_LIFECYCLE_PENDING_KEY = "euri:verbatim:lifecycle:review_pending"
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
    memory_scope: str = PERSONAL_SCOPE

    def render(self) -> str:
        # Il verbatim prova che la frase è stata pronunciata in quel momento,
        # non che descriva ancora lo stato presente. Data assoluta e canale
        # restano accanto alla fonte quando entra nel prompt.
        observed = format_datetime_full(from_timestamp(self.observed_at))
        channel = "canale autenticato" if self.trusted else "canale non autenticato"
        scope = (
            f"; scenario sperimentale {self.memory_scope.removeprefix('experiment_')}"
            if self.memory_scope.startswith("experiment_")
            else ""
        )
        return (
            f"[Turno originale del {observed}; {channel}{scope}] "
            f"{self.speaker}: {self.content}"
        )


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
            "memory_scope": normalize_scope(message.get("memory_scope")),
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
                memory_scope=normalize_scope(doc.get("memory_scope")),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Archivio turni: documento malformato per {turn_ref}")
            return None

    def render(self, turn_ref: str) -> str:
        turn = self.get(turn_ref)
        return turn.render() if turn else ""


def _decode_key(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _json_doc(redis_client, key: str) -> dict:
    try:
        raw = redis_client.json().get(key, "$")
    except Exception:
        return {}
    if not raw:
        return {}
    doc = raw[0] if isinstance(raw, list) else raw
    return dict(doc) if isinstance(doc, dict) else {}


def audit_verbatim_lifecycle(
    redis_client,
    *,
    reference_at: float | None = None,
    grace_days: int | None = None,
) -> dict:
    """Mark-and-sweep audit-only dell'archivio verbatim.

    Marca come raggiungibile ogni turno referenziato da una memoria Redis
    esistente. Lo sweep produce soltanto candidati: non modifica TTL, memorie o
    turni. È deliberatamente conservativo anche verso memorie superseded, che
    restano parte dell'audit storico finché il loro documento esiste.
    """
    now_ts = time.time() if reference_at is None else float(reference_at)
    grace = int(
        getattr(config, "VERBATIM_UNREFERENCED_GRACE_DAYS", 180)
        if grace_days is None else grace_days
    )
    if grace < 1:
        raise ValueError("grace_days deve essere positivo")

    reverse_refs: dict[str, set[str]] = {}
    memory_docs = malformed_memories = 0
    for raw_key in redis_client.scan_iter(match="euri:memory:*"):
        key = _decode_key(raw_key)
        doc = _json_doc(redis_client, key)
        if not doc:
            malformed_memories += 1
            continue
        memory_docs += 1
        memory_id = str(doc.get("id") or key.removeprefix("euri:memory:"))
        temporal = doc.get("temporal_context") or {}
        for raw_ref in temporal.get("source_turn_refs") or []:
            ref = str(raw_ref or "").strip()
            if ref:
                reverse_refs.setdefault(ref, set()).add(memory_id)

    turns = []
    malformed_turns = 0
    for raw_key in redis_client.scan_iter(match=f"{TURN_KEY_PREFIX}*"):
        key = _decode_key(raw_key)
        ref = key.removeprefix(TURN_KEY_PREFIX)
        try:
            turn = ConversationTurnStore(redis_client).get(ref)
        except Exception:
            turn = None
        if turn is None:
            malformed_turns += 1
            continue
        age_days = max(0.0, (now_ts - turn.observed_at) / 86400)
        referenced_by = sorted(reverse_refs.get(ref, set()))
        turns.append(
            {
                "turn_ref": ref,
                "role": turn.role,
                "observed_at": turn.observed_at,
                "age_days": round(age_days, 3),
                "referenced_by": referenced_by,
                "referenced": bool(referenced_by),
            }
        )

    known_refs = {item["turn_ref"] for item in turns}
    missing_source_refs = [
        {
            "turn_ref": ref,
            "referenced_by": sorted(memory_ids),
        }
        for ref, memory_ids in sorted(reverse_refs.items())
        if ref not in known_refs
    ]
    orphan_candidates = [
        item for item in turns
        if not item["referenced"] and item["age_days"] >= grace
    ]
    recent_unreferenced = [
        item for item in turns
        if not item["referenced"] and item["age_days"] < grace
    ]
    referenced = [item for item in turns if item["referenced"]]
    return {
        "schema_version": 1,
        "mode": "audit_only",
        "reference_at": now_ts,
        "grace_days": grace,
        "counts": {
            "turns": len(turns),
            "referenced": len(referenced),
            "recent_unreferenced": len(recent_unreferenced),
            "orphan_candidates": len(orphan_candidates),
            "missing_source_refs": len(missing_source_refs),
            "memory_documents_scanned": memory_docs,
            "malformed_turns": malformed_turns,
            "non_json_memory_keys_skipped": malformed_memories,
        },
        "orphan_candidates": orphan_candidates,
        "missing_source_refs": missing_source_refs,
    }


def _decode_json_value(value) -> dict:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def get_verbatim_lifecycle_pending(redis_client) -> dict:
    """Restituisce l'avviso durevole di revisione, se presente."""
    try:
        return _decode_json_value(redis_client.get(VERBATIM_LIFECYCLE_PENDING_KEY))
    except Exception:
        return {}


def run_verbatim_lifecycle_maintenance(
    redis_client,
    *,
    reference_at: float | None = None,
    grace_days: int | None = None,
    emit_pulse: bool = True,
) -> dict:
    """Esegue e persiste l'audit, senza modificare né cancellare i turni.

    Un problema crea un avviso durevole. L'avviso viene rimosso soltanto quando
    un audit successivo torna pulito; il Pulse è emesso solo quando cambia
    l'insieme dei problemi, per non produrre lo stesso evento ogni giorno.
    """
    report = audit_verbatim_lifecycle(
        redis_client,
        reference_at=reference_at,
        grace_days=grace_days,
    )
    counts = report["counts"]
    needs_review = bool(
        counts["orphan_candidates"]
        or counts["missing_source_refs"]
        or counts["malformed_turns"]
    )
    report["review_required"] = needs_review
    report["persisted_at"] = time.time()
    redis_client.set(
        VERBATIM_LIFECYCLE_REPORT_KEY,
        json.dumps(report, ensure_ascii=False, sort_keys=True),
    )

    if not needs_review:
        redis_client.delete(VERBATIM_LIFECYCLE_PENDING_KEY)
        logger.info(
            "Lifecycle verbatim: audit automatico pulito — {} turni, "
            "{} referenziati, {} recenti non referenziati",
            counts["turns"],
            counts["referenced"],
            counts["recent_unreferenced"],
        )
        return report

    fingerprint_payload = {
        "orphan_candidates": [
            item["turn_ref"] for item in report["orphan_candidates"]
        ],
        "missing_source_refs": [
            item["turn_ref"] for item in report["missing_source_refs"]
        ],
        "malformed_turns": counts["malformed_turns"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    previous = get_verbatim_lifecycle_pending(redis_client)
    pending = {
        "schema_version": 1,
        "status": "review_pending",
        "fingerprint": fingerprint,
        "first_detected_at": (
            previous.get("first_detected_at")
            if previous.get("fingerprint") == fingerprint
            else report["reference_at"]
        ),
        "last_detected_at": report["reference_at"],
        "grace_days": report["grace_days"],
        "counts": {
            "orphan_candidates": counts["orphan_candidates"],
            "missing_source_refs": counts["missing_source_refs"],
            "malformed_turns": counts["malformed_turns"],
        },
        "report_key": VERBATIM_LIFECYCLE_REPORT_KEY,
        "automatic_deletion": False,
    }
    redis_client.set(
        VERBATIM_LIFECYCLE_PENDING_KEY,
        json.dumps(pending, ensure_ascii=False, sort_keys=True),
    )
    logger.warning(
        "Lifecycle verbatim: REVISIONE PENDENTE — {} candidati orfani, "
        "{} riferimenti mancanti, {} turni malformati; nessuna cancellazione",
        counts["orphan_candidates"],
        counts["missing_source_refs"],
        counts["malformed_turns"],
    )

    if emit_pulse and previous.get("fingerprint") != fingerprint:
        from core.pulse import pulse_emit

        pulse_emit(
            redis_client,
            "memory",
            "intero",
            "verbatim_lifecycle_review_needed",
            payload=pending,
            salience=0.75,
            producer="conversation_turns.lifecycle",
            logical_event_id=f"verbatim-lifecycle:{fingerprint}",
            experiment_version="verbatim-lifecycle-v1",
        )
    return report
