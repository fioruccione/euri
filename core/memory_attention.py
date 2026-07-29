"""
Indici leggeri di attenzione per le memorie.

Questi indici sono derivati: il documento RedisJSON resta la fonte di verità.
Se un indice manca o va fuori sync, i consumatori devono poter ricadere sullo
scan completo. Lo scopo è separare il ranking/candidatura dal payload pesante.
"""
from __future__ import annotations

import time
import hashlib
from typing import Any

from loguru import logger


LOOP2E_ZSET = "euri:idx:loop2e:candidates"
LOOP2E_MIN_RECALLED = 3
LOOP2E_RECENCY_WINDOW_S = 30 * 86400
LOOP2E_SKIP_SOURCES = {"loop2e", "campus", "web", "reflection"}
NON_FACTUAL_MEMORY_KINDS = {"conversation_anchor", "conversation_episode"}


def memory_key(memory_id: str) -> str:
    mid = str(memory_id or "")
    return mid if mid.startswith("euri:memory:") else f"euri:memory:{mid}"


def bare_memory_id(memory_id: str) -> str:
    return str(memory_id or "").rsplit(":", 1)[-1]


def is_loop2e_candidate(doc: dict[str, Any], *, now_ts: float | None = None) -> bool:
    """Replica il gate preliminare del Loop 2e, senza conoscere il dominio specifico."""
    if not doc:
        return False
    from core.memory_scope import PERSONAL_SCOPE, scope_of
    if scope_of(doc) != PERSONAL_SCOPE:
        return False
    if doc.get("source") in LOOP2E_SKIP_SOURCES:
        return False
    if doc.get("memory_kind") in NON_FACTUAL_MEMORY_KINDS:
        return False
    if doc.get("superseded_by") or doc.get("consolidated_into"):
        return False
    if doc.get("correction_pending"):
        return False
    if doc.get("requires_verification"):
        return False
    axes = doc.get("memory_axes") or {}
    if axes.get("subject_status") == "acephalous":
        return False
    try:
        if int(doc.get("audit_flag") or 0) > 0:
            return False
    except (TypeError, ValueError):
        return False
    try:
        if int(doc.get("recalled_count") or 0) < LOOP2E_MIN_RECALLED:
            return False
    except (TypeError, ValueError):
        return False
    lr = doc.get("last_recalled_at")
    if not lr:
        return False
    now_ts = time.time() if now_ts is None else now_ts
    try:
        if now_ts - float(lr) > LOOP2E_RECENCY_WINDOW_S:
            return False
    except (TypeError, ValueError):
        return False
    if not doc.get("embedding"):
        return False
    return True


def loop2e_attention_score(doc: dict[str, Any]) -> float:
    """
    Score ordinato per utilità di consolidamento.

    Un uso sostenuto nella risposta rinforza l'attenzione, ma non modifica
    eleggibilità, verità o gate: vale come pochi richiami aggiuntivi e ha un cap.
    Il contatore recall resta necessario per entrare nel pool.
    """
    import config

    try:
        rc = min(int(doc.get("recalled_count") or 0), 20)
    except (TypeError, ValueError):
        rc = 0
    try:
        supported = min(
            int(doc.get("supported_use_count") or 0),
            int(getattr(config, "MEMORY_ATTENTION_SUPPORTED_USE_CAP", 5)),
        )
    except (TypeError, ValueError):
        supported = 0
    try:
        supported_weight = float(
            getattr(config, "MEMORY_ATTENTION_SUPPORTED_USE_WEIGHT", 2.0)
        )
    except (TypeError, ValueError):
        supported_weight = 2.0
    try:
        lr = float(doc.get("last_recalled_at") or doc.get("created_at") or 0.0)
    except (TypeError, ValueError):
        lr = 0.0
    mid = bare_memory_id(doc.get("id", ""))
    tie = int(hashlib.sha1(mid.encode("utf-8")).hexdigest()[:6], 16) / 100_000_000
    attention = rc + supported * max(0.0, supported_weight)
    return attention * 10_000_000_000.0 + lr + tie


def update_loop2e_candidate_index(r, doc: dict[str, Any], *, strict: bool = False) -> None:
    """Aggiorna lo ZSET; fail-open per default, solleva se il caller deve ritentare."""
    if r is None or not doc:
        return
    mid = bare_memory_id(doc.get("id", ""))
    if not mid:
        return
    try:
        if is_loop2e_candidate(doc):
            r.zadd(LOOP2E_ZSET, {mid: loop2e_attention_score(doc)})
        else:
            r.zrem(LOOP2E_ZSET, mid)
    except Exception as e:
        if strict:
            raise
        logger.debug(f"Loop2e attention index update fallito per {mid[:8]}: {e}")


def remove_loop2e_candidate(r, memory_id: str) -> None:
    """Rimuove una memoria dall'indice derivato. Fail-open."""
    if r is None or not memory_id:
        return
    try:
        r.zrem(LOOP2E_ZSET, bare_memory_id(memory_id))
    except Exception as e:
        logger.debug(f"Loop2e attention index remove fallito per {str(memory_id)[:8]}: {e}")


def scan_loop2e_candidates(r, *, now_ts: float | None = None) -> list[dict[str, Any]]:
    """Baseline canonica: scan completo dei JSON, usata anche per test/drift."""
    docs: list[dict[str, Any]] = []
    now_ts = time.time() if now_ts is None else now_ts
    for key in r.scan_iter("euri:memory:*"):
        try:
            raw = r.json().get(key, "$")
            if raw and is_loop2e_candidate(raw[0], now_ts=now_ts):
                docs.append(raw[0])
        except Exception:
            continue
    docs.sort(key=loop2e_attention_score, reverse=True)
    return docs


def zset_loop2e_candidates(
    r,
    *,
    max_ids: int = 2000,
    now_ts: float | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Ritorna candidati da ZSET e un flag `used_index`.
    Se lo ZSET è assente/vuoto, `used_index=False` e il chiamante deve usare lo scan.
    Ogni documento viene revalidato contro il JSON: l'indice non è fonte di verità.
    """
    try:
        if r.zcard(LOOP2E_ZSET) <= 0:
            return [], False
        ids = r.zrevrange(LOOP2E_ZSET, 0, max_ids - 1)
    except Exception as e:
        logger.debug(f"Loop2e attention index read fallito: {e}")
        return [], False

    docs: list[dict[str, Any]] = []
    now_ts = time.time() if now_ts is None else now_ts
    for mid in ids:
        if isinstance(mid, bytes):
            mid = mid.decode("utf-8", errors="ignore")
        try:
            raw = r.json().get(memory_key(mid), "$")
            if not raw:
                remove_loop2e_candidate(r, mid)
                continue
            doc = raw[0]
            if not is_loop2e_candidate(doc, now_ts=now_ts):
                remove_loop2e_candidate(r, mid)
                continue
            docs.append(doc)
        except Exception:
            continue
    return docs, True


def rebuild_loop2e_candidate_index(r) -> int:
    """Ricostruisce lo ZSET dallo scan canonico. Usato da script/diagnostica."""
    docs = scan_loop2e_candidates(r)
    pipe = r.pipeline()
    pipe.delete(LOOP2E_ZSET)
    for doc in docs:
        mid = bare_memory_id(doc.get("id", ""))
        if mid:
            pipe.zadd(LOOP2E_ZSET, {mid: loop2e_attention_score(doc)})
    pipe.execute()
    return len(docs)
