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
