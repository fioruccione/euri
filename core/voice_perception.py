"""Consapevolezza operativa effimera della pipeline vocale.

Ogni segmento audio termina in un record causale sanitizzato: descrive quali
gate sono stati attraversati e perche' il segmento e' stato inoltrato oppure
fermato, senza conservare audio, trascrizione o embedding biometrici.

Il record vive in tre piani osservativi, nessuno dei quali e' memoria:

* una piccola lista Redis con TTL, condivisa fra i canali locali;
* l'ultima osservazione nel Cognitive Present process-local;
* un evento ``telemetry`` su Pulse, ignorato dal Cognitive Projector.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import config
from core.cognitive_present import EpistemicStatus
from core.pulse import pulse_emit


SCHEMA_VERSION = 1

_ALLOWED_FIELDS = (
    "trace_id",
    "started_at",
    "observed_at",
    "duration_s",
    "speaker_verdict",
    "speaker_similarity",
    "speaker_threshold",
    "speaker_reason",
    "actor_scope",
    "stt_state",
    "transcript_chars",
    "detected_language",
    "has_wake_word",
    "addressed",
    "decision",
    "delivered_to",
)

_DECISION_LABELS = {
    "mobile_session_active": "fermato dal gate mobile",
    "visual_gate_inactive": "fermato per assenza di presenza visiva",
    "voice_enrollment": "usato soltanto per l'enrollment vocale",
    "stt_empty": "STT senza testo",
    "stt_garbage": "testo STT ripetitivo scartato",
    "guest_wake_word_required": "ospite senza wake word",
    "wake_word_absent_no_previous_turn": "wake word assente e nessun turno precedente",
    "wake_word_absent_outside_conversation": "wake word assente fuori dalla finestra conversazionale",
    "accepted_wake_word": "accettato tramite wake word",
    "accepted_conversation_lease": "accettato nella finestra conversazionale",
    "accepted_interpreter_mode": "accettato in modalita' interprete",
    "accepted_dictation_mode": "accettato in modalita' dettatura",
    "accepted_adaptive_followup": "accettato come continuazione semantica",
    "accepted_semantic_bootstrap": "accettato dal bootstrap semantico owner",
    "accepted_activity_window": "accettato nella finestra di attivita'",
    "accepted_other": "accettato dal gate conversazionale",
}

_VOICE_PERCEPTION_QUESTION_RE = re.compile(
    r"\b(?:"
    r"mi\s+hai\s+sentit[oa]|"
    r"hai\s+(?:sentit[oa]|percepito)\b(?!\s+parlare)|"
    r"cosa\s+hai\s+(?:sentito|percepito)|"
    r"perch(?:e'|[eé])\s+non\s+(?:mi\s+)?hai\s+risposto|"
    r"come\s+mai\s+non\s+(?:mi\s+)?hai\s+risposto"
    r")",
    re.IGNORECASE,
)


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _finite_float(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def sanitize_voice_perception(event: dict[str, Any]) -> dict[str, Any]:
    """Whitelist stretta: nessun contenuto audio/testuale puo' attraversarla."""
    clean = {key: event.get(key) for key in _ALLOWED_FIELDS}
    clean["schema_version"] = SCHEMA_VERSION
    clean["trace_id"] = str(clean.get("trace_id") or "")[:96]
    clean["started_at"] = _finite_float(clean.get("started_at"))
    clean["observed_at"] = _finite_float(clean.get("observed_at"), default=time.time())
    clean["duration_s"] = max(0.0, _finite_float(clean.get("duration_s")))
    clean["speaker_verdict"] = str(clean.get("speaker_verdict") or "not_run")[:32]
    similarity = clean.get("speaker_similarity")
    clean["speaker_similarity"] = (
        None if similarity is None else round(_finite_float(similarity), 3)
    )
    threshold = clean.get("speaker_threshold")
    clean["speaker_threshold"] = (
        None if threshold is None else round(_finite_float(threshold), 3)
    )
    clean["speaker_reason"] = str(clean.get("speaker_reason") or "")[:64]
    clean["actor_scope"] = str(clean.get("actor_scope") or "unknown")[:24]
    clean["stt_state"] = str(clean.get("stt_state") or "not_run")[:24]
    clean["transcript_chars"] = max(0, int(clean.get("transcript_chars") or 0))
    clean["detected_language"] = str(clean.get("detected_language") or "")[:16]
    clean["has_wake_word"] = bool(clean.get("has_wake_word", False))
    clean["addressed"] = bool(clean.get("addressed", False))
    clean["decision"] = str(clean.get("decision") or "unknown")[:80]
    clean["delivered_to"] = str(clean.get("delivered_to") or "none")[:32]
    return clean


def read_recent_voice_perceptions(redis_client, *, now: float | None = None) -> list[dict]:
    """Legge soltanto record validi e freschi; errori e schema ignoto tacciono."""
    if not getattr(config, "VOICE_PERCEPTION_AWARENESS_ENABLED", True):
        return []
    try:
        raw = redis_client.get(config.VOICE_PERCEPTION_STATE_KEY)
        if not raw:
            return []
        payload = json.loads(_decode(raw))
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return []
        current = time.time() if now is None else float(now)
        ttl_s = max(1.0, float(config.VOICE_PERCEPTION_TTL_S))
        events = []
        for item in payload.get("events") or []:
            if not isinstance(item, dict) or item.get("schema_version") != SCHEMA_VERSION:
                continue
            observed_at = _finite_float(item.get("observed_at"))
            if observed_at > 0 and current - observed_at <= ttl_s:
                events.append(sanitize_voice_perception(item))
        return sorted(events, key=lambda item: item["observed_at"])
    except Exception:
        return []


class VoicePerceptionRecorder:
    """Registra esiti vocali sanitizzati senza influenzarne il comportamento."""

    def __init__(self, redis_client, present=None, *, clock=time.time) -> None:
        self.redis = redis_client
        self.present = present
        self.clock = clock

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        clean = sanitize_voice_perception(event)
        if not getattr(config, "VOICE_PERCEPTION_AWARENESS_ENABLED", True):
            return clean

        try:
            previous = read_recent_voice_perceptions(
                self.redis,
                now=clean["observed_at"],
            )
            previous.append(clean)
            max_events = max(2, int(config.VOICE_PERCEPTION_MAX_EVENTS))
            payload = {
                "schema_version": SCHEMA_VERSION,
                "updated_at": clean["observed_at"],
                "events": previous[-max_events:],
            }
            self.redis.set(
                config.VOICE_PERCEPTION_STATE_KEY,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ex=max(1, int(config.VOICE_PERCEPTION_TTL_S)),
            )
        except Exception:
            pass

        if self.present is not None:
            try:
                self.present.observe(
                    "voice.last_pipeline_outcome",
                    clean,
                    status=EpistemicStatus.SYSTEM_FACT,
                    source="voice_pipeline",
                    observed_at=clean["observed_at"],
                    ttl_s=max(1.0, float(config.VOICE_PERCEPTION_TTL_S)),
                    evidence_ref=clean["trace_id"],
                )
            except Exception:
                pass

        pulse_emit(
            self.redis,
            "voice",
            "extero",
            "segment_decision",
            payload=clean,
            salience=0.1,
            producer="voice_daemon",
            trace_id=clean["trace_id"],
            logical_event_id=clean["trace_id"],
            epistemic_before="audio_segment_detected",
            epistemic_after=clean["decision"],
            duration_ms=clean["duration_s"] * 1000.0,
        )
        return clean


def voice_perception_context(redis_client, *, now: float | None = None) -> str:
    """Rende al Brain solo i recenti segmenti non inoltrati e la causa codificata."""
    current = time.time() if now is None else float(now)
    events = [
        item
        for item in read_recent_voice_perceptions(redis_client, now=current)
        if not item.get("addressed") and item.get("delivered_to") == "none"
    ]
    if not events:
        return ""
    max_context = max(1, int(config.VOICE_PERCEPTION_CONTEXT_MAX_EVENTS))
    lines = []
    for item in events[-max_context:]:
        age_s = max(0, int(round(current - item["observed_at"])))
        identity = item["speaker_verdict"]
        if item.get("speaker_similarity") is not None:
            identity += f" similarity={item['speaker_similarity']:.3f}"
            if item.get("speaker_threshold") is not None:
                identity += f" soglia={item['speaker_threshold']:.3f}"
        stt = item["stt_state"]
        if stt == "text":
            stt = f"testo rilevato ({item['transcript_chars']} caratteri, contenuto non esposto)"
        reason = _DECISION_LABELS.get(item["decision"], item["decision"])
        lines.append(
            f"- trace={item['trace_id']}; {age_s}s fa; audio={item['duration_s']:.1f}s; "
            f"speaker={identity}; actor={item['actor_scope']}; STT={stt}; "
            f"wake_word={str(item['has_wake_word']).lower()}; inoltrato=no; causa={reason}."
        )
    return (
        "=== TRACCE VOCALI OPERATIVE RECENTI (effimere, non memoria) ===\n"
        "Ogni riga e' un segmento distinto e la causa e' prodotta dal codice. "
        "Non collegare segmenti diversi per la sola vicinanza temporale e non "
        "dedurre identita' o contenuto mancanti. Usa queste righe soltanto se "
        "l'utente chiede cosa hai percepito o perche' non hai risposto; altrimenti ignorale.\n"
        + "\n".join(lines)
    )


def is_voice_perception_question(text: str) -> bool:
    """Riconosce soltanto domande sul recente ascolto operativo di Euri."""
    return bool(_VOICE_PERCEPTION_QUESTION_RE.search(str(text or "")))


def voice_perception_answer(
    text: str,
    redis_client,
    *,
    now: float | None = None,
) -> str:
    """Risposta causale deterministica; non ricostruisce contenuto o identita'."""
    if not is_voice_perception_question(text):
        return ""
    current = time.time() if now is None else float(now)
    events = [
        item
        for item in read_recent_voice_perceptions(redis_client, now=current)
        if not item.get("addressed") and item.get("delivered_to") == "none"
    ]
    if not events:
        return (
            "Non ho una traccia operativa recente di un segmento vocale fermato "
            "prima del ragionamento."
        )

    item = events[-1]
    decision = item.get("decision")
    speaker = item.get("speaker_verdict")
    if speaker == "verified":
        speaker_fact = "SpeakerAuth aveva verificato la voce."
    elif speaker == "rejected":
        similarity = item.get("speaker_similarity")
        threshold = item.get("speaker_threshold")
        if similarity is not None and threshold is not None:
            speaker_fact = (
                "SpeakerAuth non aveva verificato la voce "
                f"({similarity:.3f}, soglia {threshold:.3f})."
            )
        else:
            speaker_fact = "SpeakerAuth non aveva verificato la voce."
    else:
        speaker_fact = "SpeakerAuth non era stato eseguito."

    if decision in {
        "wake_word_absent_no_previous_turn",
        "wake_word_absent_outside_conversation",
    }:
        detail = (
            "Ho rilevato e trascritto un segmento audio, ma non l'ho inoltrato "
            "al ragionamento perche' mancava la wake word e la finestra "
            "conversazionale non consentiva il seguito."
        )
    elif decision == "guest_wake_word_required":
        detail = (
            "Ho rilevato e trascritto un segmento audio, ma non l'ho inoltrato "
            "perche' una voce non verificata deve usare la wake word."
        )
    elif decision == "stt_empty":
        detail = (
            "Ho rilevato un segmento audio, ma lo STT non ha prodotto testo e "
            "quindi nulla e' stato inoltrato al ragionamento."
        )
    elif decision == "stt_garbage":
        detail = (
            "Ho rilevato un segmento audio, ma lo STT ha prodotto una ripetizione "
            "classificata come rumore e non l'ha inoltrata al ragionamento."
        )
    elif decision == "visual_gate_inactive":
        detail = (
            "Ho rilevato audio, ma il gate visivo non rilevava una presenza e il "
            "segmento e' stato fermato prima dello STT."
        )
    elif decision == "mobile_session_active":
        detail = (
            "Ho rilevato audio, ma una sessione mobile era gia' attiva e il "
            "segmento e' stato fermato prima dello STT."
        )
    else:
        reason = _DECISION_LABELS.get(str(decision), str(decision or "causa ignota"))
        detail = (
            "Ho una traccia di un segmento non inoltrato al ragionamento. "
            f"La causa registrata dal codice e': {reason}."
        )

    privacy = (
        " La traccia conserva la causa, non il contenuto della trascrizione."
        if item.get("stt_state") == "text"
        else ""
    )
    return f"{detail} {speaker_fact}{privacy}".strip()


def with_voice_perception_context(
    base_context: str,
    redis_client,
    *,
    now: float | None = None,
) -> str:
    return "\n\n".join(
        part
        for part in (
            str(base_context or ""),
            voice_perception_context(redis_client, now=now),
        )
        if part
    )
