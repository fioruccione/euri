"""Ponte effimero e sanitizzato tra VisualGate e canali testuali locali."""
from __future__ import annotations

import json
import time

import config


_BOOL_FIELDS = (
    "camera_available",
    "recognition_available",
    "gate_active",
    "face_detected",
    "owner_present",
    "demo_mode",
)
_SOCIAL_FEATURES = ("smile", "brow_contraction", "gaze_down")


def publish_visual_presence(
    redis_client,
    snapshot: dict,
    *,
    observed_at: float | None = None,
    ttl_s: int | None = None,
) -> dict:
    """Pubblica solo campi dichiarativi; dati biometrici inattesi sono ignorati."""
    ttl = max(3, int(ttl_s or config.VISUAL_PRESENCE_STATE_TTL_S))
    payload = {
        "schema_version": 1,
        "observed_at": float(time.time() if observed_at is None else observed_at),
        "valid_for_s": ttl,
        **{field: bool(snapshot.get(field, False)) for field in _BOOL_FIELDS},
        "identity": str(snapshot.get("identity") or "")[:80],
    }
    redis_client.set(
        config.VISUAL_PRESENCE_STATE_KEY,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ex=ttl,
    )
    return payload


def read_visual_presence(redis_client, *, now: float | None = None) -> dict | None:
    """Legge uno snapshot solo se valido e fresco; qualunque errore e' fail-silent."""
    try:
        raw = redis_client.get(config.VISUAL_PRESENCE_STATE_KEY)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None
        observed_at = float(payload.get("observed_at") or 0)
        valid_for_s = max(1.0, float(payload.get("valid_for_s") or 0))
        current = time.time() if now is None else float(now)
        if observed_at <= 0 or current - observed_at > valid_for_s:
            return None
        return payload
    except Exception:
        return None


def read_social_perception(redis_client, *, now: float | None = None) -> dict | None:
    """Legge solo stati sociali calibrati, freschi e riferiti all'owner presente."""
    if not getattr(config, "SOCIAL_PERCEPTION_CONTEXT_ENABLED", False):
        return None
    try:
        visual = read_visual_presence(redis_client, now=now)
        if not visual or not visual.get("owner_present"):
            return None

        raw = redis_client.get("euri:social:latest")
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("actor_id") != config.OWNER_ACTOR_ID
            or payload.get("calibrated") is not True
        ):
            return None

        observed_at = float(payload.get("observed_at") or 0)
        current = time.time() if now is None else float(now)
        ttl_s = max(1.0, float(config.SOCIAL_PERCEPTION_LATEST_TTL_S))
        if observed_at <= 0 or current - observed_at > ttl_s:
            return None

        states_in = payload.get("states") or {}
        confidence_in = payload.get("confidences") or {}
        states: dict[str, str] = {}
        confidences: dict[str, float] = {}
        allowed = {
            "smile": {"neutral", "slight", "marked"},
            "brow_contraction": {"neutral", "present"},
            "gaze_down": {"neutral", "present"},
        }
        for feature in _SOCIAL_FEATURES:
            state = str(states_in.get(feature) or "neutral")
            states[feature] = state if state in allowed[feature] else "neutral"
            confidences[feature] = max(
                0.0,
                min(1.0, float(confidence_in.get(feature) or 0.0)),
            )
        return {
            "actor_id": config.OWNER_ACTOR_ID,
            "observed_at": observed_at,
            "states": states,
            "confidences": confidences,
        }
    except Exception:
        return None


def visual_presence_context(redis_client, *, now: float | None = None) -> str:
    """Contesto operativo per l'LLM; descrive evidenza, non concede autorita'."""
    assistant = config.ASSISTANT_DISPLAY_NAME
    owner = config.OWNER_DISPLAY_NAME
    owner_id = config.OWNER_ACTOR_ID
    state = read_visual_presence(redis_client, now=now)
    heading = "=== STATO VISIVO OPERATIVO (effimero, non memoria) ==="

    if state is None:
        enabled = "configurato" if config.FACE_AUTH_ENABLED else "disabilitato"
        return (
            f"{heading}\nVisualGate e riconoscimento facciale locale risultano {enabled}, "
            "ma questo canale non dispone ora di uno snapshot fresco. Non negare "
            "l'esistenza del sensore; dichiara soltanto che il suo stato corrente non e' disponibile."
        )
    if state.get("demo_mode"):
        return (
            f"{heading}\nVisualGate e' in modalita' demo: non costituisce osservazione "
            "biometrica della persona davanti allo schermo."
        )
    if not state.get("camera_available"):
        return (
            f"{heading}\nVisualGate e' previsto dall'architettura di {assistant}, ma la "
            "webcam non risulta attualmente disponibile."
        )
    if state.get("owner_present") and state.get("identity") == owner_id:
        return (
            f"{heading}\nVisualGate rileva ora un volto e riconosce il proprietario "
            f"configurato {owner} (actor_id={owner_id}). Nell'installazione locale "
            f"mono-utente e' forte evidenza che {owner} sia davanti allo schermo; "
            "non e' una prova crittografica di chi sta digitando e non sostituisce "
            "le conferme richieste per azioni sensibili."
        )
    if state.get("face_detected"):
        identity = str(state.get("identity") or "").strip()
        detail = f" come actor_id={identity}" if identity else ", senza identita' verificata"
        return f"{heading}\nVisualGate rileva ora un volto{detail}. Non attribuire la chat al proprietario."
    return (
        f"{heading}\nLa webcam e' disponibile, ma VisualGate non rileva ora un volto "
        "fresco davanti allo schermo."
    )


def social_perception_context(redis_client, *, now: float | None = None) -> str:
    """Descrive movimenti visibili; vieta al prompt di trasformarli in emozioni."""
    state = read_social_perception(redis_client, now=now)
    if state is None:
        return ""

    states = state["states"]
    confidence = state["confidences"]
    observations: list[str] = []

    smile = states["smile"]
    if confidence["smile"] >= 0.6:
        observations.append(
            {
                "neutral": "nessun sorriso stabile rilevato",
                "slight": "sorriso lieve stabilizzato",
                "marked": "sorriso marcato stabilizzato",
            }[smile]
        )
    brow = states["brow_contraction"]
    if confidence["brow_contraction"] >= 0.6:
        observations.append(
            "contrazione visibile delle sopracciglia"
            if brow == "present"
            else "nessuna contrazione stabile delle sopracciglia"
        )
    gaze = states["gaze_down"]
    if confidence["gaze_down"] >= 0.6:
        observations.append(
            "sguardo stabilizzato verso il basso rispetto alla telecamera"
            if gaze == "present"
            else "nessuno sguardo verso il basso stabilizzato"
        )
    if not observations:
        return ""

    owner = config.OWNER_DISPLAY_NAME
    return (
        "=== OSSERVAZIONI SOCIALI VISIVE (effimere, descrittive) ===\n"
        f"Sul volto riconosciuto di {owner}: " + "; ".join(observations) + ".\n"
        "Sono movimenti visibili, NON emozioni, intenzioni, sincerita' o approvazione. "
        "Lo sguardo verso il basso puo' indicare schermo, tastiera o lavoro. "
        "Usa questi segnali solo per adattare leggermente tono o brevita' quando sono "
        "coerenti con le parole dell'utente; altrimenti ignorali. Non citarli "
        "spontaneamente, non salvarli come fatti e non usarli per autorizzare azioni."
    )


def with_visual_context(
    base_context: str,
    redis_client,
    *,
    now: float | None = None,
) -> str:
    """Aggiunge presenza e segnali sociali effimeri senza alterare la memoria."""
    base = str(base_context or "")
    return "\n\n".join(
        part
        for part in (
            base,
            visual_presence_context(redis_client, now=now),
            social_perception_context(redis_client, now=now),
        )
        if part
    )
