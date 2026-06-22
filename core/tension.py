"""
System tension — scorer agnostico per eventi afferenti.

Questo modulo NON agisce e NON parla. Converte un evento Pulse, più eventuale
oggetto collegato (memoria/todo/insight), in una misura leggibile di attenzione:
quanto l'evento merita di essere guardato.

Principio: la tensione è domain-agnostic. Non conosce Lucy Plast, polimeri,
clienti o progetti; guarda solo forma dell'evento, fonte, rischio, tempo,
ricorrenza, affidabilità e possibilità di azione.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, asdict
from typing import Any


TENSION_STREAM = "euri:tension"
_MAXLEN = 50000

_SOURCE_RELIABILITY = {
    "user": 0.95,
    "teach": 0.95,
    "reaction": 0.90,
    "obsidian_vault": 0.85,
    "episode": 0.70,
    "conversation": 0.68,
    "passive": 0.62,
    "loop2e": 0.58,
    "reflection": 0.55,
    "web": 0.35,
    "mobile": 0.35,
    "mobile_in": 0.35,
}

_RISK_BY_CONSOLIDATION = {
    "ok": 0.0,
    "watch": 0.45,
    "high": 0.85,
}


@dataclass(frozen=True)
class TensionScore:
    tension: float
    novelty: float
    risk: float
    urgency: float
    impact: float
    source_reliability: float
    recurrence: float
    deviation: float
    needs_verification: bool
    actionability: float
    recommended_mode: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(x: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return lo
    if math.isnan(v):
        return lo
    return max(lo, min(hi, v))


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _event_get(event: dict[str, Any], key: str, default: Any = None) -> Any:
    return event.get(key, default)


def _source_reliability(event: dict[str, Any], related: dict[str, Any]) -> float:
    src = (related.get("source") or "").strip()
    if src in _SOURCE_RELIABILITY:
        return _SOURCE_RELIABILITY[src]
    pulse_source = (_event_get(event, "source", "") or "").strip()
    if pulse_source == "extero":
        return 0.70
    if pulse_source == "intero":
        return 0.60
    return 0.55


def _urgency(event: dict[str, Any], related: dict[str, Any], payload: dict[str, Any], now_ts: float) -> float:
    if "minutes_left" in payload:
        try:
            minutes = float(payload["minutes_left"])
        except (TypeError, ValueError):
            minutes = None
        if minutes is not None:
            if minutes <= 0:
                return 1.0
            if minutes <= 15:
                return 0.85
            if minutes <= 30:
                return 0.65
            if minutes <= 60:
                return 0.45
            return 0.20

    due_at = related.get("due_at") or payload.get("due_at")
    if due_at:
        try:
            seconds_left = float(due_at) - now_ts
        except (TypeError, ValueError):
            seconds_left = None
        if seconds_left is not None:
            if seconds_left <= 0:
                return 1.0
            hours = seconds_left / 3600.0
            if hours <= 1:
                return 0.75
            if hours <= 6:
                return 0.45
            if hours <= 24:
                return 0.25
    return 0.0


def _risk(event: dict[str, Any], related: dict[str, Any], payload: dict[str, Any]) -> tuple[float, bool, list[str]]:
    reasons: list[str] = []
    vals: list[float] = []

    if related.get("requires_verification") or payload.get("requires_verification"):
        vals.append(0.45)
        reasons.append("requires_verification")

    safety = related.get("safety_flag") or payload.get("safety_flag") or []
    if safety:
        vals.append(0.90)
        reasons.append("safety_flag")

    try:
        audit_flag = int(related.get("audit_flag") or payload.get("audit_flag") or 0)
    except (TypeError, ValueError):
        audit_flag = 0
    if audit_flag > 0:
        vals.append(min(0.95, 0.35 + 0.15 * audit_flag))
        reasons.append("audit_flag")

    if related.get("provenance_stale") or payload.get("provenance_stale"):
        vals.append(0.80)
        reasons.append("provenance_stale")

    cr = related.get("consolidation_risk") or payload.get("consolidation_risk") or {}
    if isinstance(cr, dict):
        level = str(cr.get("level") or "ok").lower()
        rv = _RISK_BY_CONSOLIDATION.get(level, 0.0)
        if rv:
            vals.append(rv)
            reasons.append(f"consolidation_risk={level}")

    sense = str(_event_get(event, "sense", ""))
    kind = str(_event_get(event, "kind", ""))
    if sense == "integrity" or "integrity" in kind:
        vals.append(0.90)
        reasons.append("integrity_failure")
    if sense == "provenance" and kind == "propagated":
        staled = payload.get("staled", 0)
        try:
            if int(staled) > 0:
                vals.append(0.70)
                reasons.append("provenance_propagated")
        except (TypeError, ValueError):
            pass
    if sense == "correction":
        vals.append(0.55)
        reasons.append("correction_signal")

    risk = max(vals) if vals else 0.0
    needs_verification = risk >= 0.45 or bool(related.get("requires_verification"))
    return _clamp(risk), needs_verification, reasons


def _recurrence(recurrence_count: int) -> float:
    if recurrence_count <= 0:
        return 0.0
    if recurrence_count == 1:
        return 0.25
    if recurrence_count <= 3:
        return 0.55
    return 0.85


def _novelty(event: dict[str, Any], payload: dict[str, Any], recurrence: float) -> float:
    sense = str(_event_get(event, "sense", ""))
    kind = str(_event_get(event, "kind", ""))
    if payload.get("novelty") is not None:
        return _clamp(payload.get("novelty"))
    base = 0.35
    if sense in {"vault", "reaction"}:
        base = 0.55
    if sense == "insight" and kind == "promoted":
        base = 0.70
    if sense in {"integrity", "provenance"}:
        base = 0.65
    return _clamp(base * (1.0 - 0.35 * recurrence))


def _deviation(event: dict[str, Any], related: dict[str, Any], payload: dict[str, Any], risk: float) -> float:
    sense = str(_event_get(event, "sense", ""))
    kind = str(_event_get(event, "kind", ""))
    if payload.get("deviation") is not None:
        return _clamp(payload.get("deviation"))
    if sense in {"integrity", "provenance"}:
        return max(0.60, risk)
    if related.get("domain_outlier") or payload.get("domain_outlier"):
        return 0.70
    if "denied" in kind or "demoted" in kind:
        return 0.45
    return 0.15 if risk == 0 else min(0.55, risk)


def _actionability(event: dict[str, Any], related: dict[str, Any], payload: dict[str, Any],
                   urgency: float, needs_verification: bool) -> float:
    sense = str(_event_get(event, "sense", ""))
    kind = str(_event_get(event, "kind", ""))
    if sense == "clock":
        return 0.90
    if sense == "presence":
        return 0.65
    if sense == "reaction":
        return 0.70
    if sense == "vault":
        return 0.45
    if sense == "integrity":
        return 0.60
    if sense == "insight" and kind == "promoted":
        return 0.45
    if needs_verification:
        return 0.55
    if urgency > 0:
        return 0.65
    if related.get("status") == "pending":
        return 0.55
    return 0.25


def _impact(event: dict[str, Any], related: dict[str, Any], payload: dict[str, Any],
            risk: float, urgency: float, actionability: float) -> float:
    if payload.get("impact") is not None:
        return _clamp(payload.get("impact"))
    sense = str(_event_get(event, "sense", ""))
    if sense == "clock":
        return max(0.60, urgency)
    if sense == "reaction":
        return 0.65
    if sense == "integrity":
        return 0.80
    if sense == "insight":
        return 0.55
    return _clamp(0.25 + 0.35 * risk + 0.25 * urgency + 0.15 * actionability)


def _mode(tension: float, needs_verification: bool, actionability: float,
          urgency: float, risk: float, event_source: str) -> str:
    # Gli eventi interni possono richiedere verifica epistemica, ma non devono
    # trasformarsi automaticamente in "ask-the-user". Prima devono essere
    # consumati da un pass interno o promossi da una policy esplicita.
    intero = event_source == "intero"
    if tension >= 0.92 and actionability >= 0.80:
        return "act"
    if urgency >= 0.85 and actionability >= 0.75:
        return "notify"
    if risk >= 0.85 and actionability >= 0.55 and not intero:
        return "notify"
    if tension >= 0.72:
        return "notify"
    if needs_verification and intero:
        return "watch" if tension >= 0.22 else "log"
    if needs_verification and actionability >= 0.45 and (risk >= 0.45 or tension >= 0.52):
        return "ask"
    if tension >= 0.42:
        return "watch"
    if tension >= 0.22:
        return "log"
    return "ignore"


def evaluate_tension(
    event: dict[str, Any],
    related: dict[str, Any] | None = None,
    *,
    recurrence_count: int = 0,
    now_ts: float | None = None,
) -> TensionScore:
    """
    Valuta un evento Pulse in modo agnostico.

    `event` può essere il dict fields di Redis Stream (`payload` JSON string) oppure
    un dict già normalizzato. `related` è opzionale: memoria/todo/insight collegato.
    """
    related = related or {}
    now_ts = time.time() if now_ts is None else float(now_ts)
    payload = _parse_payload(event.get("payload"))

    salience = _clamp(event.get("salience", 0.5))
    recurrence = _recurrence(int(recurrence_count or 0))
    reliability = _source_reliability(event, related)
    urgency = _urgency(event, related, payload, now_ts)
    risk, needs_verification, risk_reasons = _risk(event, related, payload)
    novelty = _novelty(event, payload, recurrence)
    deviation = _deviation(event, related, payload, risk)
    actionability = _actionability(event, related, payload, urgency, needs_verification)
    impact = _impact(event, related, payload, risk, urgency, actionability)

    # Affidabilità bassa aumenta cautela, ma non deve gonfiare ogni evento esterno innocuo.
    unreliability_risk = (1.0 - reliability) * 0.35 if risk > 0 or needs_verification else 0.0

    tension = (
        0.14 * salience +
        0.13 * novelty +
        0.20 * max(risk, unreliability_risk) +
        0.18 * urgency +
        0.13 * impact +
        0.08 * recurrence +
        0.08 * deviation +
        0.06 * actionability
    )
    tension = _clamp(tension)
    mode = _mode(
        tension, needs_verification, actionability, urgency, risk,
        str(event.get("source") or ""),
    )

    reason_bits = []
    if risk_reasons:
        reason_bits.append(",".join(risk_reasons))
    if urgency >= 0.65:
        reason_bits.append("time_urgent")
    if recurrence >= 0.55:
        reason_bits.append("recurring")
    if novelty >= 0.65:
        reason_bits.append("novel")
    reason = "; ".join(reason_bits) or "low_pressure"

    return TensionScore(
        tension=round(tension, 3),
        novelty=round(novelty, 3),
        risk=round(risk, 3),
        urgency=round(urgency, 3),
        impact=round(impact, 3),
        source_reliability=round(reliability, 3),
        recurrence=round(recurrence, 3),
        deviation=round(deviation, 3),
        needs_verification=bool(needs_verification),
        actionability=round(actionability, 3),
        recommended_mode=mode,
        reason=reason,
    )


def tension_emit(r, event_id: str, event: dict[str, Any], score: TensionScore) -> None:
    """Scrive lo score su `euri:tension`. Fail-open: non solleva mai."""
    if r is None:
        return
    try:
        r.xadd(
            TENSION_STREAM,
            {
                "event_id": str(event_id),
                "sense": str(event.get("sense", "")),
                "kind": str(event.get("kind", "")),
                "score": json.dumps(score.to_dict(), ensure_ascii=False),
                "tension": f"{score.tension:.3f}",
                "mode": score.recommended_mode,
                "ts": f"{time.time():.3f}",
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception:
        pass
