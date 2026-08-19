"""Confine conversazionale per Loop 2k.

Questo modulo non interpreta parole e non lancia modelli: conserva gli stati
effimeri di proposta/lavoro/consegna e traduce un risultato dell'arena in una
risposta breve. Le chiavi non ricevono embedding e non appartengono al RAG.
"""
from __future__ import annotations

import json
from typing import Any


def _decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def pending_key(actor_id: str) -> str:
    return f"euri:ideation:pending:{actor_id}"


def active_key(actor_id: str) -> str:
    return f"euri:ideation:active:{actor_id}"


def delivery_key(actor_id: str) -> str:
    return f"euri:ideation:delivery:{actor_id}"


def job_queue_key(actor_id: str) -> str:
    return f"euri:ideation:jobs:{actor_id}"


def ui_stream_key(actor_id: str) -> str:
    return f"euri:ideation:ui_out:{actor_id}"


def load_json(redis_client, key: str) -> dict:
    try:
        value = json.loads(_decode(redis_client.get(key)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def store_json(redis_client, key: str, value: dict, *, ttl_s: int) -> None:
    redis_client.set(key, json.dumps(value, ensure_ascii=False), ex=max(1, int(ttl_s)))


def enqueue_delivery(redis_client, key: str, value: dict, *, ttl_s: int) -> None:
    redis_client.rpush(key, json.dumps(value, ensure_ascii=False))
    redis_client.expire(key, max(1, int(ttl_s)))


def peek_delivery(redis_client, key: str) -> dict:
    try:
        value = json.loads(_decode(redis_client.lindex(key, 0)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def enqueue_job(redis_client, key: str, value: dict, *, ttl_s: int) -> None:
    redis_client.rpush(key, json.dumps(value, ensure_ascii=False))
    redis_client.expire(key, max(1, int(ttl_s)))


def pop_job(redis_client, key: str) -> dict:
    try:
        value = json.loads(_decode(redis_client.lpop(key)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def semantic_pending_decision(
    frame: dict | None, *, minimum_confidence: float = 0.72
) -> str | None:
    """Legge una conferma o un rifiuto dal frame, senza lessico parallelo."""
    if not isinstance(frame, dict) or frame.get("status") != "interpreted":
        return None
    if frame.get("requires_clarification"):
        return None
    if frame.get("addressed_to_assistant") is not True:
        return None
    try:
        confidence = float(frame.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return None
    if confidence < float(minimum_confidence):
        return None
    acts = {str(item or "").upper() for item in frame.get("speech_acts") or []}
    if "CONFIRM" in acts and "REJECT" not in acts:
        return "confirm"
    if "REJECT" in acts and "CONFIRM" not in acts:
        return "reject"
    return None


def format_result(result) -> str:
    """Presenta il verdetto come ipotesi interna, non come verita' acquisita."""
    status = str(getattr(result, "status", "") or "")
    candidates = list(getattr(result, "candidates", []) or [])
    by_id = {str(getattr(item, "id", "")): item for item in candidates}
    top_ids = list(getattr(result, "top_candidate_ids", []) or [])

    if status == "completed" and len(top_ids) == 1:
        candidate = by_id.get(str(top_ids[0]))
        if candidate is not None:
            proposal = " ".join(str(getattr(candidate, "proposal", "") or "").split())
            test = " ".join(
                str(getattr(candidate, "falsification_test", "") or "").split()
            )
            risks = [
                " ".join(str(item or "").split())
                for item in (getattr(candidate, "risks", []) or [])
                if str(item or "").strip()
            ]
            reply = (
                "Ho finito il confronto. La proposta che ha retto meglio e': "
                f"{proposal[:700]}"
            )
            if test:
                reply += f" La prova piu' utile per smentirla sarebbe: {test[:360]}"
            if risks:
                reply += f" Il rischio principale resta: {risks[0][:260]}"
            return reply + " La tratto come ipotesi interna, non come fatto acquisito."

    if status == "contested":
        proposals = []
        for candidate_id in top_ids[:3]:
            candidate = by_id.get(str(candidate_id))
            proposal = " ".join(
                str(getattr(candidate, "proposal", "") or "").split()
            ) if candidate is not None else ""
            if proposal:
                proposals.append(proposal[:320])
        detail = "; oppure: ".join(proposals)
        suffix = f" Le alternative rimaste sono: {detail}." if detail else ""
        return (
            "Ho finito il confronto, ma non c'e' un vincitore netto."
            f"{suffix} Non forzo una conclusione: serve un dato o un test discriminante."
        )

    if status == "insufficient_candidates":
        return (
            "Ho finito, ma le alternative non sono rimaste abbastanza distinte o fedeli "
            "alle premesse per sostenere un vero confronto. Preferisco non inventare un vincitore."
        )
    if status == "insufficient_evaluations":
        return (
            "Ho generato alternative utili, ma i confronti non hanno prodotto valutazioni "
            "abbastanza affidabili. Il risultato resta aperto e non lo trasformo in una risposta certa."
        )
    return "Il confronto non ha prodotto un risultato utilizzabile; non ne ricavo una conclusione."
