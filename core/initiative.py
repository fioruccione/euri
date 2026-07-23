"""
Proactive initiative controller.

Trasforma eventi del Pulse in candidate question, ma non genera mai domande con
template: la formulazione resta compito del modello. La parte deterministica qui
fa solo policy/safety: idrata l'oggetto reale, valuta tensione, applica cooldown
e registra l'audit.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

import config
from core.ollama_client import chat_client
from core.pulse import PULSE_STREAM
from core.reaction import _insight_brief
from core.tension import TensionScore, evaluate_tension


INITIATIVE_STREAM = "euri:initiative:candidates"
INITIATIVE_SEEN_KEY = "euri:initiative:seen"
INITIATIVE_LAST_ASK_KEY = "euri:initiative:last_ask_ts"
INITIATIVE_PENDING_ZSET = "euri:initiative:pending"


@dataclass
class InitiativeCandidate:
    event_id: str
    event: dict[str, Any]
    payload: dict[str, Any]
    related_key: str
    related: dict[str, Any]
    score: TensionScore
    eligible: bool
    reason: str
    goal: str = ""


def parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _json_get_one(r, key: str) -> dict[str, Any]:
    if not key:
        return {}
    try:
        obj = r.json().get(key, "$")
    except Exception as e:
        logger.debug(f"Initiative: JSON.GET fallito su {key}: {e}")
        return {}
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    return obj if isinstance(obj, dict) else {}


def _memory_key(memory_id: str) -> str:
    return memory_id if memory_id.startswith("euri:memory:") else f"euri:memory:{memory_id}"


def _insight_key(insight_id: str) -> str:
    return insight_id if insight_id.startswith("euri:insight:") else f"euri:insight:{insight_id}"


def hydrate_related(r, event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Rilegge l'oggetto reale dietro al Pulse.

    Il payload del Pulse è un'istantanea leggera e può essere stale: alcune path
    salvano prima il nodo e poi alzano flag epistemici. Per decidere se parlare,
    l'unica fonte affidabile è il JSON corrente.
    """
    payload = parse_payload(event.get("payload"))
    sense = str(event.get("sense") or "")

    if sense == "insight":
        key = payload.get("key") or payload.get("insight_key")
        insight_id = payload.get("id") or payload.get("insight") or payload.get("insight_id")
        if not key and insight_id:
            key = _insight_key(str(insight_id))
        doc = _json_get_one(r, str(key or ""))
        if not doc and insight_id:
            fallback_key = _insight_key(str(insight_id))
            doc = _json_get_one(r, fallback_key)
            key = fallback_key if doc else key
        return str(key or ""), doc

    if sense in {"memory", "audit", "provenance", "reaction"}:
        key = payload.get("key") or payload.get("memory_key")
        memory_id = (
            payload.get("id")
            or payload.get("memory_id")
            or payload.get("lesson")
            or payload.get("lesson_id")
        )
        if not key and memory_id:
            key = _memory_key(str(memory_id))
        doc = _json_get_one(r, str(key or ""))
        if not doc and memory_id:
            fallback_key = _memory_key(str(memory_id))
            doc = _json_get_one(r, fallback_key)
            key = fallback_key if doc else key
        return str(key or ""), doc

    return "", {}


def _hydrate_tension_nodes(r, ids: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    """Read-only: rilegge i nodi citati da una tensione della mappa del pensiero.
    Scarta quelli ritirati (superseded/consolidati) — se restano <2 vivi la tensione
    si è risolta da sola (anti-nag naturale). has_primary = c'è un nodo DETTO-DA-STEFANO."""
    nodes, has_primary = [], False
    for mid in list(ids)[:8]:
        doc = _json_get_one(r, _memory_key(str(mid)))
        if not doc or doc.get("superseded_by") or doc.get("consolidated_into"):
            continue
        src = doc.get("source")
        if src in {"user", "teach", "obsidian_vault"}:
            has_primary = True
        nodes.append({"id": str(mid)[:8], "source": src, "content": (doc.get("content") or "")[:300]})
    return nodes, has_primary


def build_candidate(r, event_id: str, event: dict[str, Any]) -> InitiativeCandidate:
    payload = parse_payload(event.get("payload"))
    related_key, related = hydrate_related(r, event)
    score = evaluate_tension(event, related=related or None)

    sense = str(event.get("sense") or "")
    kind = str(event.get("kind") or "")
    eligible = False
    reason = "unsupported_event"
    goal = ""

    if sense == "insight" and kind == "promoted":
        if not related:
            reason = "missing_related_insight"
        elif related.get("external_reaction"):
            reason = "already_grounded"
        elif score.tension < getattr(config, "INITIATIVE_MIN_TENSION", 0.25):
            reason = "low_tension"
        else:
            eligible = True
            reason = "promoted_insight_needs_epistemic_check"
            goal = "ask_epistemic_clarification"
    elif sense == "memory" and kind == "saved":
        clarify_reason = _memory_clarify_reason(related)
        if not related:
            reason = "missing_related_memory"
        elif not clarify_reason:
            reason = "memory_clear_or_explicit"
        elif score.tension < getattr(config, "INITIATIVE_MIN_TENSION", 0.25):
            reason = "low_tension"
        else:
            eligible = True
            reason = clarify_reason
            goal = "ask_memory_clarification"
    elif sense == "thought_map" and kind == "tension":
        # Contraddizione tra memorie trovata dal riorganizzatore. È un gap epistemico REALE
        # per costruzione → non si gate sul tension score generico; il filtro è: i nodi in
        # conflitto esistono ancora (≥2 vivi). Se la tua parola ne ha superseduto uno, la
        # tensione sparisce da sola (anti-nag). related idratato qui (hydrate_related non copre).
        nodes, has_primary = _hydrate_tension_nodes(r, payload.get("ids") or [])
        if len(nodes) < 2:
            reason = "tension_resolved_or_gone"
        else:
            related = {"subject": payload.get("subject"),
                       "description": payload.get("description"),
                       "nodes": nodes,
                       "has_stefano_claim": bool(payload.get("has_stefano_claim") or has_primary)}
            eligible = True
            reason = "tension_vs_stefano_claim" if related["has_stefano_claim"] else "tension_derived_only"
            goal = "ask_memory_clarification"

    return InitiativeCandidate(
        event_id=event_id,
        event=dict(event),
        payload=payload,
        related_key=related_key,
        related=related,
        score=score,
        eligible=eligible,
        reason=reason,
        goal=goal,
    )


def _memory_clarify_reason(doc: dict[str, Any]) -> str:
    """True solo per memorie passive la cui incertezza Stefano può risolvere."""
    if not doc:
        return ""
    if doc.get("source") != "passive":
        return ""
    if doc.get("superseded_by") or doc.get("consolidated_into"):
        return ""
    if doc.get("correction_pending"):
        return ""

    axes = doc.get("memory_axes") or {}
    audit_reasons = axes.get("audit_reasons") or []
    if doc.get("passive_support") == "tacit_acceptance":
        return "weak_passive_memory"
    if "acephalous_subject" in audit_reasons:
        return "acephalous_passive_memory"
    if doc.get("requires_verification"):
        return "passive_memory_needs_verification"
    return ""


def was_seen(r, event_id: str) -> bool:
    try:
        return bool(r.sismember(INITIATIVE_SEEN_KEY, event_id))
    except Exception:
        return False


def mark_seen(r, event_id: str) -> None:
    try:
        r.sadd(INITIATIVE_SEEN_KEY, event_id)
        r.expire(INITIATIVE_SEEN_KEY, 7 * 24 * 3600)
    except Exception as e:
        logger.debug(f"Initiative: mark_seen fallito: {e}")


def _pending_key(event_id: str) -> str:
    return f"euri:initiative:pending:{event_id}"


def store_pending(r, candidate: InitiativeCandidate) -> None:
    """Tiene vivo un candidato eleggibile bloccato da presenza/cooldown.

    Serve perché molti insight nascono in idle/notte: non devono parlare alla
    stanza vuota, ma nemmeno sparire prima che Stefano torni.
    """
    try:
        r.set(
            _pending_key(candidate.event_id),
            json.dumps(candidate.event, ensure_ascii=False),
            ex=2 * 24 * 3600,
        )
        r.zadd(INITIATIVE_PENDING_ZSET, {candidate.event_id: time.time()})
        r.expire(INITIATIVE_PENDING_ZSET, 2 * 24 * 3600)
    except Exception as e:
        logger.debug(f"Initiative: store_pending fallito: {e}")


def store_event_pending(r, event_id: str, event: dict[str, Any]) -> None:
    try:
        r.set(
            _pending_key(event_id),
            json.dumps(dict(event), ensure_ascii=False),
            ex=2 * 24 * 3600,
        )
        r.zadd(INITIATIVE_PENDING_ZSET, {event_id: time.time()})
        r.expire(INITIATIVE_PENDING_ZSET, 2 * 24 * 3600)
    except Exception as e:
        logger.debug(f"Initiative: store_event_pending fallito: {e}")


def iter_pending(r, limit: int = 5, min_age_s: float = 0.0) -> list[tuple[str, dict[str, Any]]]:
    try:
        rows = r.zrange(INITIATIVE_PENDING_ZSET, 0, -1, withscores=True)
    except Exception:
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    now_ts = time.time()
    for event_id, score in rows:
        if len(out) >= limit:
            break
        try:
            if min_age_s and now_ts - float(score or 0) < min_age_s:
                continue
        except (TypeError, ValueError):
            pass
        try:
            raw = r.get(_pending_key(event_id))
            if not raw:
                r.zrem(INITIATIVE_PENDING_ZSET, event_id)
                continue
            event = json.loads(raw)
            if isinstance(event, dict):
                out.append((event_id, event))
        except Exception:
            continue
    return out


def clear_pending(r, event_id: str) -> None:
    try:
        r.zrem(INITIATIVE_PENDING_ZSET, event_id)
        r.delete(_pending_key(event_id))
    except Exception:
        pass


def record_candidate(
    r,
    candidate: InitiativeCandidate,
    *,
    decision: str,
    reason: str = "",
    proposal: dict[str, Any] | None = None,
) -> None:
    try:
        fields = {
            "event_id": candidate.event_id,
            "sense": str(candidate.event.get("sense") or ""),
            "kind": str(candidate.event.get("kind") or ""),
            "related_key": candidate.related_key,
            "eligible": "1" if candidate.eligible else "0",
            "candidate_reason": candidate.reason,
            "decision": decision,
            "reason": reason,
            "tension": f"{candidate.score.tension:.3f}",
            "mode": candidate.score.recommended_mode,
            "ts": str(time.time()),
        }
        if proposal:
            fields["proposal"] = json.dumps(proposal, ensure_ascii=False)[:2000]
        r.xadd(INITIATIVE_STREAM, fields, maxlen=50000, approximate=True)
    except Exception as e:
        logger.debug(f"Initiative: record_candidate fallito: {e}")


def parse_question_response(raw: str) -> dict[str, Any]:
    if not raw:
        return {"should_ask": False, "reason": "empty_output"}
    s = raw.strip()
    if s.startswith("```"):
        start = s.find("{")
        end = s.rfind("}")
        s = s[start:end + 1] if start >= 0 and end > start else s.strip("`")
    else:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            s = s[start:end + 1]
    try:
        data = json.loads(s)
    except Exception:
        return {"should_ask": False, "reason": "json_parse_failed"}
    return data if isinstance(data, dict) else {"should_ask": False, "reason": "not_object"}


def _candidate_focus_material(candidate: InitiativeCandidate) -> str:
    sense = str(candidate.event.get("sense") or "")
    if sense == "insight":
        return _insight_brief(candidate.related)
    if sense == "memory":
        return str(candidate.related.get("content") or candidate.payload)[:1400]
    if sense == "thought_map":
        nodes = "\n".join(
            str(node.get("content") or "")[:260]
            for node in candidate.related.get("nodes", [])
        )
        return (
            f"{candidate.related.get('subject') or ''}: "
            f"{candidate.related.get('description') or ''}\n{nodes}"
        )[:1400]
    return str(candidate.related or candidate.payload)[:1400]


def classify_focus_relevance(
    focus_text: str,
    candidate: InitiativeCandidate,
    *,
    chat=None,
    model: str | None = None,
) -> str:
    """EXTENDS soltanto se l'iniziativa prosegue davvero il filo corrente.

    RELATED e UNRELATED non possono interrompere una conversazione attiva. Errori,
    output vuoti o ambigui sono fail-closed a UNRELATED.
    """
    if not focus_text.strip() or not candidate.eligible:
        return "UNRELATED"
    if chat is None:
        chat = chat_client
    material = _candidate_focus_material(candidate)
    prompt = f"""Sei il gate conservativo che decide se un pensiero proattivo di {config.ASSISTANT_DISPLAY_NAME} può inserirsi nella conversazione in corso.

CONVERSAZIONE ATTUALE, composta solo dalle parole recenti dell'utente:
"{focus_text[-1800:]}"

PENSIERO CANDIDATO:
"{material[:1400]}"

Classifica:
- EXTENDS: il pensiero aggiunge un meccanismo, un dato o una domanda direttamente utile al problema specifico di cui l'utente sta parlando adesso.
- RELATED: condivide dominio, parole o un'analogia generale, ma sposterebbe il filo su un altro problema.
- UNRELATED: tratta un argomento diverso.

La sola appartenenza allo stesso settore, parole come controllo/processo/dati, o un'analogia astratta NON bastano per EXTENDS. Nel dubbio scegli RELATED o UNRELATED.
Rispondi SOLO con EXTENDS, RELATED oppure UNRELATED."""
    try:
        response = chat.chat(
            model=model or config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 2500},
            think=True,
        )
        raw = response.message.content or ""
        if "<channel|>" in raw:
            raw = raw.split("<channel|>", 1)[-1]
        label = raw.strip().upper().rstrip(".")
        return label if label in {"EXTENDS", "RELATED", "UNRELATED"} else "UNRELATED"
    except Exception as e:
        logger.warning(f"Initiative focus relevance fallita ({e}) → UNRELATED")
        return "UNRELATED"


def generate_question(candidate: InitiativeCandidate, *, focus_text: str = "") -> dict[str, Any]:
    """Chiede al modello se formulare una domanda e con quali parole.

    Le regole qui sono il contratto epistemico; la frase resta generata dal LLM.
    """
    if not candidate.eligible:
        return {"should_ask": False, "reason": candidate.reason}

    event = candidate.event
    related = candidate.related
    kind = f"{event.get('sense')}/{event.get('kind')}"

    if str(event.get("sense") or "") == "insight":
        event_text = _insight_brief(related)
        event_label = "insight promosso dal Dream Engine"
        specific_contract = (
            "- Per un insight/sogno, chiedi se è analogia utile, forzatura, fatto operativo o da ignorare.\n"
            "- Non presentarlo come procedura già vera."
        )
    elif str(event.get("sense") or "") == "memory":
        event_text = str(related.get("content") or candidate.payload)[:1200]
        event_label = "memoria passiva incerta"
        specific_contract = (
            "- Per una memoria passiva incerta, chiedi conferma/correzione del fatto specifico.\n"
            "- La domanda deve aiutare a fissare o correggere la memoria, non commentarla.\n"
            f"- Se il fatto è banale, troppo generico o non vale interrompere "
            f"{config.OWNER_DISPLAY_NAME}, should_ask=false."
        )
    elif str(event.get("sense") or "") == "thought_map":
        subj = related.get("subject") or "una cosa"
        conflitto = "\n".join(f"  - [{n.get('source')}] {n.get('content')}" for n in related.get("nodes", []))
        event_text = f"Tensione su «{subj}»: {related.get('description','')}\nNote in conflitto:\n{conflitto}"[:1400]
        event_label = "contraddizione tra memorie (trovata dal riorganizzatore)"
        specific_contract = (
            "- È una contraddizione REALE tra note di memoria sullo stesso soggetto.\n"
            f"- Chiedi a {config.OWNER_DISPLAY_NAME} QUALE versione è corretta, o di "
            f"confermare il dato, nominando il soggetto in modo naturale.\n"
            "- NON dire tu quale è giusta; NON elencare id o citare 'note': parla come a voce.\n"
            "- should_ask=false se la contraddizione è banale o non risolvibile con una frase."
        )
    else:
        event_text = str(related.get("content") or candidate.payload)[:1200]
        event_label = kind
        specific_contract = (
            f"- Chiedi solo se la risposta di {config.OWNER_DISPLAY_NAME} "
            "cambierebbe davvero la memoria."
        )

    focus_contract = ""
    if focus_text.strip():
        focus_contract = (
            "\nLa domanda entra dentro una conversazione già attiva. Deve nominare in modo "
            "naturale il legame concreto col filo corrente e aggiungerlo, non cambiare argomento.\n"
            f"FILO CORRENTE (parole dell'utente): {focus_text[-1200:]}\n"
        )

    prompt = f"""Sei il controllore proattivo di {config.ASSISTANT_DISPLAY_NAME}. Non stai rispondendo a {config.OWNER_DISPLAY_NAME}: devi decidere se {config.ASSISTANT_DISPLAY_NAME} deve fare UNA domanda breve adesso.

Contratto:
- Parla solo se la domanda serve a trasformare un evento interno in apprendimento da {config.OWNER_DISPLAY_NAME}.
- La domanda deve essere in italiano naturale, adatta alla voce, massimo 2 frasi.
- Se la domanda sarebbe ridondante, troppo astratta o non ancorata all'evento, should_ask=false.
- Niente markdown, niente elenco, niente formule burocratiche.
{specific_contract}
{focus_contract}

EVENTO: {event_label}
TENSIONE: {candidate.score.tension:.2f}
MOTIVO SISTEMA: {candidate.reason}

CONTENUTO:
{event_text[:1400]}

Rispondi SOLO JSON valido con questi campi:
{{"should_ask": true/false, "question": "testo domanda o stringa vuota", "why": "motivo breve"}}"""

    try:
        res = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 220},
            format="json",
            think=False,
        )
        raw = res.message.content or ""
    except Exception as e:
        return {"should_ask": False, "reason": f"llm_error:{str(e)[:80]}"}

    data = parse_question_response(raw)
    question = str(data.get("question") or "").strip()
    should_ask = bool(data.get("should_ask")) and bool(question)
    if len(question) > 360:
        question = question[:357].rstrip() + "..."
    return {
        "should_ask": should_ask,
        "question": question if should_ask else "",
        "why": str(data.get("why") or data.get("reason") or "")[:300],
    }
