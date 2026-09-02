"""Chiusura owner dei correction signal ``proposal_only``.

Il Loop 2g puo' proporre un verdetto, ma non acquisisce autorita' retroattiva
sulla memoria. Questo modulo assegna una sola proposta per scope a voce/UI,
mostra l'eventuale antecedente e applica una decisione soltanto dopo la risposta
esplicita dell'owner. Il link fra versioni resta quello atomico di
``MemoryManager.link_correction``.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from loguru import logger

import config
from core.memory_scope import normalize_scope, scope_of


LEASE_PREFIX = "euri:correction_review:lease:"
OWNER_REVIEW_CONTRACT_VERSION = config.CORRECTION_OWNER_REVIEW_CONTRACT_VERSION
DEFAULT_LEASE_TTL_S = 15 * 60
DEFAULT_REVIEW_BACKOFF_S = 30 * 60

_LATER_RE = re.compile(
    r"\b(?:pi[uù]\s+tardi|dopo|non\s+ora|rimand|da\s+verificare)\b",
    re.IGNORECASE,
)
_UNCERTAIN_RE = re.compile(
    r"\b(?:forse|boh|non\s+(?:lo\s+)?so|non\s+(?:me\s+lo\s+)?ricordo|"
    r"non\s+sono\s+sicur[oa])\b",
    re.IGNORECASE,
)
_DISMISS_RE = re.compile(
    r"\b(?:no|scarta(?:la)?|ignora(?:la)?|lascia\s+perdere|"
    r"solo\s+(?:per\s+)?(?:quella\s+)?conversazione)\b",
    re.IGNORECASE,
)
_SEPARATE_RE = re.compile(
    r"\b(?:separat[ao]|indipendente|nuova\s+memoria|registrala\s+separatamente)\b",
    re.IGNORECASE,
)
_APPLY_RE = re.compile(
    r"\b(?:s[iì]|applica(?:la)?|correggi(?:la)?|collegat[ao]|"
    r"quella\s+memoria|procedi|confermo)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _doc(r, key: str) -> dict:
    try:
        raw = r.json().get(key, "$")
    except Exception:
        return {}
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    return dict(raw) if isinstance(raw, dict) else {}


_SET_FIELDS_LUA = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return 0
end
local keep_durable = false
for i = 1, #ARGV, 2 do
    redis.call('JSON.SET', KEYS[1], '$.' .. ARGV[i], ARGV[i + 1])
    if ARGV[i] == 'status' and ARGV[i + 1] == '"repair_required"' then
        keep_durable = true
    end
end
if keep_durable then
    redis.call('PERSIST', KEYS[1])
end
return 1
"""


def _set_fields(r, key: str, values: dict[str, Any]) -> None:
    args: list[str] = []
    for field, value in values.items():
        args.extend((str(field), json.dumps(value, ensure_ascii=False)))
    try:
        updated = r.eval(_SET_FIELDS_LUA, 1, key, *args)
        if int(updated or 0) != 1:
            raise RuntimeError(f"documento assente: {key}")
    except AttributeError:
        # Client minimali dei test. Il runtime Redis applica tutti i campi in
        # un'unica esecuzione Lua e non degrada mai al percorso sequenziale.
        for field, value in values.items():
            r.json().set(key, f"$.{field}", value)


def _hash_content(content: str) -> str:
    return hashlib.sha256(_text(content).encode("utf-8")).hexdigest()


def _normalise(content: str) -> str:
    return " ".join(_text(content).casefold().split())


def _clip(content: str, limit: int) -> str:
    content = " ".join(_text(content).split())
    if len(content) <= limit:
        return content
    return content[: limit - 1].rstrip() + "…"


def _lease_key(memory_scope: str) -> str:
    scope = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", normalize_scope(memory_scope))
    return f"{LEASE_PREFIX}{scope}"


def _lease_owned(r, review: dict) -> bool:
    try:
        return _text(r.get(review.get("lease_key", ""))) == _text(
            review.get("lease_token")
        )
    except Exception:
        return False


_RELEASE_LEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def release_review_lease(r, review: dict) -> bool:
    key = _text(review.get("lease_key"))
    token = _text(review.get("lease_token"))
    if not key or not token:
        return False
    try:
        return bool(r.eval(_RELEASE_LEASE_LUA, 1, key, token))
    except AttributeError:
        # Fake/minimal client nei test. Il client runtime usa sempre Lua.
        if not _lease_owned(r, review):
            return False
        return bool(r.delete(key))
    except Exception as exc:
        logger.warning("Correction review: rilascio lease fallito ({})", exc)
        return False


def _eligible_signal(
    doc: dict,
    memory_scope: str,
    now_ts: float,
    *,
    include_legacy: bool = False,
) -> bool:
    if doc.get("status") != "proposed":
        return False
    if doc.get("requires_owner_confirmation") is not True:
        return False
    # Il corpus proposal_only precedente a CORR-03 resta evidenza storica, non
    # una coda operativa. Soltanto il 2g aggiornato può concedere eligibility
    # apponendo esplicitamente la versione del contratto.
    if (
        not include_legacy
        and doc.get("owner_review_contract_version")
        != OWNER_REVIEW_CONTRACT_VERSION
    ):
        return False
    if normalize_scope(scope_of(doc)) != normalize_scope(memory_scope):
        return False
    try:
        if float(doc.get("review_after") or 0) > now_ts:
            return False
    except (TypeError, ValueError):
        return False
    return bool(_text(doc.get("correzione_user")).strip())


def _question(correction: str, target: dict | None) -> tuple[str, str]:
    correction_excerpt = _clip(correction, 420)
    if target:
        target_excerpt = _clip(target.get("content", ""), 340)
        return (
            "targeted",
            "Ho una tua correzione rimasta in sospeso: "
            f"«{correction_excerpt}». La memoria che sembra coinvolta dice: "
            f"«{target_excerpt}». Preferisci A, correggere quella memoria; "
            "B, registrare la correzione come memoria separata; oppure più tardi?",
        )
    return (
        "unresolved",
        "Ho una tua correzione rimasta in sospeso, ma non trovo un antecedente "
        f"sicuro: «{correction_excerpt}». Preferisci A, registrarla come memoria "
        "separata; B, considerarla solo una correzione di quella conversazione; "
        "oppure più tardi?",
    )


def _signal_candidate_ids(signal: dict) -> set[str]:
    """Provenienza ammessa: contesto vissuto o candidati prodotti dal 2g."""
    ids: set[str] = set()
    for field in (
        "quarantined_memory_ids",
        "resolution_rag_ctx_ids",
        "rag_ctx_ids",
    ):
        for value in signal.get(field) or []:
            mid = _text(value).replace("euri:memory:", "").strip()
            if mid:
                ids.add(mid)
    for node in signal.get("rag_ctx_nodes") or []:
        if not isinstance(node, dict) or node.get("kind") != "memory":
            continue
        mid = _text(node.get("id")).replace("euri:memory:", "").strip()
        if mid:
            ids.add(mid)
    return ids


def _resolve_target(
    r,
    memory,
    correction: str,
    scope: str,
    signal: dict,
) -> dict | None:
    """Risoluzione bounded, vincolata alla provenienza congelata nel signal."""
    target = None
    try:
        target = memory.find_correction_target(
            correction,
            correction,
            memory_scope=scope,
        )
    except Exception as exc:
        logger.debug("Correction review: resolver target ha ceduto ({})", exc)
    if not target:
        return None
    target_id = _text(target.get("id")).replace("euri:memory:", "")
    # Un KNN corrente può trovare una memoria semanticamente vicina ma mai
    # esposta nel turno contestato (caso organico Bini → confronto presse).
    # L'owner può correggere solo un antecedente la cui provenienza è nel signal.
    if target_id not in _signal_candidate_ids(signal):
        logger.info(
            "Correction review: target {} escluso (fuori provenienza signal {})",
            target_id,
            _text(signal.get("id"))[:8],
        )
        return None
    canonical = _doc(r, f"euri:memory:{target_id}")
    if (
        not canonical
        or canonical.get("superseded_by")
        or normalize_scope(scope_of(canonical)) != scope
    ):
        return None
    return canonical


def _build_review(
    r,
    memory,
    *,
    signal_key: str,
    signal: dict,
    memory_scope: str,
    channel: str,
) -> dict:
    correction = _text(signal.get("correzione_user")).strip()
    target = _resolve_target(r, memory, correction, memory_scope, signal)
    mode, question = _question(correction, target)
    signal_id = _text(signal.get("id")) or signal_key.rsplit(":", 1)[-1]
    return {
        "signal_id": signal_id,
        "question_id": signal_id,
        "signal_key": signal_key,
        "memory_scope": memory_scope,
        "channel": _text(channel),
        "mode": mode,
        "question": question,
        "correction_text": correction,
        "signal_correction_sha256": _hash_content(correction),
        "target_id": _text((target or {}).get("id")),
        "target_content": _text((target or {}).get("content")),
        "target_content_sha256": (
            _hash_content((target or {}).get("content", "")) if target else ""
        ),
    }


def preview_signal_review(
    r,
    memory,
    *,
    signal_key: str,
    memory_scope: str,
    now: float | None = None,
    include_legacy: bool = False,
) -> dict | None:
    """Costruisce una domanda diagnostica senza claim né mutazioni.

    ``include_legacy`` è riservato ai replay espliciti: non influenza mai la
    selezione runtime di voce o Silent Chat.
    """
    now_ts = float(time.time() if now is None else now)
    scope = normalize_scope(memory_scope)
    key = _text(signal_key)
    if not key.startswith("euri:correction:"):
        key = f"euri:correction:{key}"
    signal = _doc(r, key)
    if not _eligible_signal(
        signal,
        scope,
        now_ts,
        include_legacy=include_legacy,
    ):
        return None
    return _build_review(
        r,
        memory,
        signal_key=key,
        signal=signal,
        memory_scope=scope,
        channel="read_only_preview",
    )


def claim_next_review(
    r,
    memory,
    *,
    memory_scope: str,
    channel: str,
    now: float | None = None,
    token: str | None = None,
    lease_ttl_s: int = DEFAULT_LEASE_TTL_S,
) -> dict | None:
    """Assegna oldest-first una proposta allo scope, senza mutare memorie."""
    now_ts = float(time.time() if now is None else now)
    scope = normalize_scope(memory_scope)
    candidates: list[tuple[float, str, dict]] = []
    for raw_key in r.scan_iter("euri:correction:*"):
        key = _text(raw_key)
        doc = _doc(r, key)
        if not _eligible_signal(doc, scope, now_ts):
            continue
        try:
            created_at = float(doc.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0.0
        candidates.append((created_at, key, doc))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))

    lease_key = _lease_key(scope)
    lease_token = token or str(uuid.uuid4())
    try:
        acquired = r.set(
            lease_key,
            lease_token,
            nx=True,
            ex=max(30, int(lease_ttl_s)),
        )
    except Exception as exc:
        logger.warning("Correction review: lease non disponibile ({})", exc)
        return None
    if not acquired:
        return None

    _created_at, signal_key, signal = candidates[0]
    try:
        review = _build_review(
            r,
            memory,
            signal_key=signal_key,
            signal=signal,
            memory_scope=scope,
            channel=channel,
        )
    except Exception as exc:
        logger.warning("Correction review: costruzione domanda fallita ({})", exc)
        release_review_lease(r, {
            "lease_key": lease_key,
            "lease_token": lease_token,
        })
        return None
    review.update({
        "lease_key": lease_key,
        "lease_token": lease_token,
        "lease_expires_at": now_ts + max(30, int(lease_ttl_s)),
    })
    return review


def defer_review(
    r,
    review: dict,
    *,
    reason: str,
    now: float | None = None,
    backoff_s: int = DEFAULT_REVIEW_BACKOFF_S,
) -> bool:
    """Rende durevole un rinvio soltanto se il chiamante possiede la lease."""
    if not _lease_owned(r, review):
        return False
    now_ts = float(time.time() if now is None else now)
    signal_key = _text(review.get("signal_key"))
    signal = _doc(r, signal_key)
    if not _eligible_signal(signal, review.get("memory_scope", "personal"), now_ts):
        release_review_lease(r, review)
        return False
    _settle_signal(
        r,
        signal_key,
        review_after=now_ts + max(60, int(backoff_s)),
        last_review_outcome=_text(reason)[:100] or "deferred",
        last_reviewed_at=now_ts,
    )
    release_review_lease(r, review)
    return True


def classify_review_answer(review: dict, answer: str) -> str:
    """Classifica solo risposte esplicite; l'incertezza non diventa consenso."""
    answer = _text(answer).strip()
    if not answer:
        return "unknown"
    if _LATER_RE.search(answer):
        return "later"
    if _UNCERTAIN_RE.search(answer):
        return "unknown"
    if review.get("stage") == "awaiting_separate_fact":
        if _DISMISS_RE.search(answer):
            return "dismiss"
        return "separate_fact"
    if re.match(r"^\s*b(?:\b|[.,:;])", answer, re.IGNORECASE):
        return "separate" if review.get("mode") == "targeted" else "dismiss"
    if _DISMISS_RE.search(answer):
        return "dismiss"
    if _SEPARATE_RE.search(answer):
        return "separate"
    if re.match(r"^\s*a(?:\b|[.,:;])", answer, re.IGNORECASE):
        return "apply" if review.get("mode") == "targeted" else "separate"
    if _APPLY_RE.search(answer):
        return "apply" if review.get("mode") == "targeted" else "separate"
    return "unknown"


def _settle_signal(r, signal_key: str, **values: Any) -> None:
    _set_fields(r, signal_key, values)


def resolve_review(
    r,
    memory,
    brain,
    review: dict,
    answer: str,
    *,
    now: float | None = None,
) -> dict:
    """Applica la decisione owner con mark-after-act e target sotto hash."""
    now_ts = float(time.time() if now is None else now)
    if not _lease_owned(r, review):
        return {
            "handled": True,
            "saved": False,
            "action": "lease_lost",
            "reply": "Quella proposta non è più attiva; la rileggerò dallo stato corrente.",
        }
    signal_key = _text(review.get("signal_key"))
    signal = _doc(r, signal_key)
    if not _eligible_signal(signal, review.get("memory_scope", "personal"), now_ts):
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": False,
            "action": "signal_stale",
            "reply": "Quella correzione è già stata chiusa o non è più applicabile.",
        }
    if _hash_content(signal.get("correzione_user", "")) != _text(
        review.get("signal_correction_sha256")
    ):
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": False,
            "action": "signal_changed",
            "reply": (
                "La correzione è cambiata da quando te l'ho mostrata; "
                "non applico nulla e la rileggerò dallo stato corrente."
            ),
        }

    action = classify_review_answer(review, answer)
    if action == "unknown":
        return {
            "handled": True,
            "saved": False,
            "action": "unknown",
            "needs_clarification": True,
            "reply": (
                "Per non interpretare al posto tuo, dimmi A, B oppure più tardi."
            ),
        }
    if action == "later":
        defer_review(
            r,
            review,
            reason="deferred_by_owner",
            now=now_ts,
        )
        return {
            "handled": True,
            "saved": False,
            "action": "later",
            "reply": "Va bene, te la riproporrò più tardi senza modificare nulla.",
        }
    if action == "dismiss":
        _settle_signal(
            r,
            signal_key,
            status="dismissed",
            verdict="owner_declined_memory_change",
            requires_owner_confirmation=False,
            owner_reviewed_at=now_ts,
            owner_review_answer=_text(answer)[:500],
        )
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": False,
            "action": "dismiss",
            "reply": "Ricevuto. Chiudo la proposta senza modificare la memoria.",
        }

    correction = _text(review.get("correction_text")).strip()
    signal_id = _text(review.get("signal_id"))
    if action == "separate":
        review["stage"] = "awaiting_separate_fact"
        return {
            "handled": True,
            "saved": False,
            "action": "request_separate_fact",
            "needs_clarification": True,
            "reply": (
                "Va bene. Per non trasformare il contesto al posto tuo, dimmi "
                "ora in una frase esatta il fatto che devo ricordare; oppure "
                "dimmi di scartare o rimandare."
            ),
        }
    if action == "separate_fact":
        separate_fact = _text(answer).strip()
        if len(separate_fact) < 10:
            return {
                "handled": True,
                "saved": False,
                "action": "separate_fact_too_short",
                "needs_clarification": True,
                "reply": "Mi serve il fatto completo in una frase, senza sottintesi.",
            }
        new_id = memory.save_memory(
            separate_fact,
            source="user",
            idempotent=True,
            final_fields={
                "correction_signal_id": signal_id,
                "correction_relation": "owner_confirmed_without_antecedent",
                "owner_correction_text": correction,
                "verification_status": "owner_confirmed_correction",
                "epistemic_status": "owner_confirmed",
                "owner_reviewed_at": now_ts,
            },
            memory_scope=review.get("memory_scope"),
        )
        if not new_id:
            defer_review(
                r,
                review,
                reason="save_failed",
                now=now_ts,
                backoff_s=5 * 60,
            )
            return {
                "handled": True,
                "saved": False,
                "action": "save_failed",
                "reply": "Non sono riuscita a registrarla; la proposta resta aperta.",
            }
        _settle_signal(
            r,
            signal_key,
            status="resolved",
            verdict="owner_confirmed_separate_memory",
            requires_owner_confirmation=False,
            owner_reviewed_at=now_ts,
            owner_review_answer=_text(answer)[:500],
            resolved_new_memory_id=new_id,
            resolved_at=now_ts,
        )
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": True,
            "separate": True,
            "action": "separate",
            "new_id": new_id,
            "reply": "Fatto. Ho registrato la correzione come memoria indipendente.",
        }

    target_id = _text(review.get("target_id")).replace("euri:memory:", "")
    target_key = f"euri:memory:{target_id}"
    target = _doc(r, target_key)
    stale = (
        not target
        or target.get("superseded_by")
        or normalize_scope(scope_of(target))
        != normalize_scope(review.get("memory_scope"))
        or _hash_content(target.get("content", ""))
        != _text(review.get("target_content_sha256"))
    )
    if stale:
        _settle_signal(
            r,
            signal_key,
            review_after=now_ts + 60,
            last_review_outcome="stale_target",
            last_reviewed_at=now_ts,
        )
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": False,
            "action": "stale",
            "reply": (
                "Nel frattempo quella memoria è cambiata. Non applico nulla e "
                "ricostruirò la proposta dallo stato aggiornato."
            ),
        }

    old_content = _text(target.get("content"))
    try:
        rewritten = _text(
            brain.apply_correction_to_memory(old_content, correction)
        ).strip()
    except Exception as exc:
        logger.warning("Correction review: riscrittura fallita ({})", exc)
        rewritten = ""
    if not rewritten:
        defer_review(
            r,
            review,
            reason="rewrite_failed",
            now=now_ts,
            backoff_s=5 * 60,
        )
        return {
            "handled": True,
            "saved": False,
            "action": "rewrite_failed",
            "reply": "Non sono riuscita a costruire una versione fedele; non modifico nulla.",
        }
    if _normalise(rewritten) == _normalise(old_content):
        _settle_signal(
            r,
            signal_key,
            status="resolved",
            verdict="already_present",
            requires_owner_confirmation=False,
            owner_reviewed_at=now_ts,
            owner_review_answer=_text(answer)[:500],
            resolved_old_memory_id=target_id,
            resolved_at=now_ts,
        )
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": False,
            "action": "already_present",
            "reply": "La versione attiva contiene già la correzione; non creo un doppione.",
        }

    new_id = memory.save_memory(
        rewritten,
        source="user",
        idempotent=False,
        final_fields={
            "correction_of": target_id,
            "correction_relation": "owner_confirmed_fact_correction",
            "correction_pending": True,
            "owner_correction_signal_id": signal_id,
            "owner_correction_text": correction,
            "owner_reviewed_at": now_ts,
        },
        memory_scope=review.get("memory_scope"),
    )
    if not new_id:
        defer_review(
            r,
            review,
            reason="save_failed",
            now=now_ts,
            backoff_s=5 * 60,
        )
        return {
            "handled": True,
            "saved": False,
            "action": "save_failed",
            "reply": "Non sono riuscita a creare la versione corretta; non modifico la precedente.",
        }
    if not memory.link_correction(target_id, new_id):
        _settle_signal(
            r,
            signal_key,
            status="repair_required",
            requires_owner_confirmation=False,
            owner_review_pending_new_memory_id=new_id,
            last_review_outcome="link_failed",
            last_reviewed_at=now_ts,
        )
        release_review_lease(r, review)
        return {
            "handled": True,
            "saved": True,
            "corrected": False,
            "pending": True,
            "action": "link_failed",
            "reply": (
                "Ho conservato la versione corretta in quarantena, ma il collegamento "
                "non è riuscito: la memoria precedente resta invariata."
            ),
        }

    # MemoryManager chiude già signal + coppia di memorie nella stessa Lua.
    # Qui aggiungiamo soltanto l'audit conversazionale: un suo fallimento non
    # può trasformare un commit cognitivo riuscito in un falso insuccesso.
    try:
        _settle_signal(
            r,
            signal_key,
            owner_reviewed_at=now_ts,
            owner_review_answer=_text(answer)[:500],
        )
    except Exception as exc:
        logger.warning("Correction review: audit post-link non scritto ({})", exc)
    release_review_lease(r, review)
    return {
        "handled": True,
        "saved": True,
        "corrected": True,
        "action": "apply",
        "old_id": target_id,
        "new_id": new_id,
        "reply": "Fatto. Ho collegato atomicamente la versione corretta alla precedente.",
    }
