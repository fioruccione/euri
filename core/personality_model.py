"""Proiezione identitaria emergente, ricostruibile dai turni canonici.

Questo modulo non e' una seconda memoria e non addestra il modello. Conserva una
vista materializzata di pattern comportamentali sostenuti dal verbatim owner:

* ``assistant``: come Euri tende a ragionare e comunicare, secondo feedback esterno;
* ``interlocutor``: preferenze operative contestuali dell'interlocutore verificato;
* ``relationship``: dinamiche che emergono soltanto nella loro interazione.

Il modello Dream propone. Il codice valida citazioni, provenienza e indipendenza
dei supporti; soltanto i tratti ``stable`` vengono resi al prompt realtime. Le
risposte di Euri non sono mai evidenza sufficiente del suo stesso carattere.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

import config
from core.memory_scope import PERSONAL_SCOPE, normalize_scope


SCHEMA_VERSION = 1
SUBJECTS = frozenset({"assistant", "interlocutor", "relationship"})
RELATIONS = frozenset({"new", "supports", "contradicts"})
STRENGTHS = frozenset({"declared", "feedback", "pattern"})
PROJECTION_PREFIX = "euri:personality:projection:"
LOCK_PREFIX = "euri:personality:lock:"
ATTEMPT_PREFIX = "euri:personality:last_attempt:"
CLAIM_MAX_CHARS = 320
CLAIM_PROMPT_MAX_CHARS = 240


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _json_doc(redis_client, key: str) -> dict:
    try:
        raw = redis_client.json().get(key, "$")
    except Exception:
        return {}
    if not raw:
        return {}
    doc = raw[0] if isinstance(raw, list) else raw
    return dict(doc) if isinstance(doc, dict) else {}


def _normalized_quote(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(text or "").casefold(), flags=re.UNICODE))


def _clean_model_text(text: str) -> str:
    if not text:
        return ""
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()


def _parse_json_object(raw: str) -> dict | None:
    cleaned = _clean_model_text(raw)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def empty_projection(actor_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "actor_id": str(actor_id),
        "revision": 0,
        "checkpoint_observed_at": 0.0,
        "last_model_run_at": 0.0,
        "updated_at": 0.0,
        "traits": [],
    }


@dataclass(frozen=True)
class PersonalityBatch:
    actor_id: str
    projection: dict
    turns: tuple[dict, ...]
    new_owner_refs: frozenset[str]
    checkpoint_before: float
    processed_through: float


@dataclass(frozen=True)
class PersonalityUpdate:
    status: str
    proposals: int = 0
    accepted: int = 0
    stable: int = 0
    revision: int = 0


def _evidence_from_proposal(
    proposal: dict,
    turns_by_ref: dict[str, dict],
    *,
    new_owner_refs: frozenset[str],
) -> list[dict]:
    """Accetta soltanto citazioni contigue di turni owner autenticati.

    Almeno una fonte deve essere nuova rispetto al checkpoint: il modello non puo'
    mantenere vivo un tratto ricitando per sempre lo stesso vecchio turno.
    """
    raw_items = proposal.get("evidence")
    if not isinstance(raw_items, list):
        return []
    accepted: list[dict] = []
    seen: set[str] = set()
    for item in raw_items[:6]:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("turn_ref") or "").strip()
        quote = str(item.get("quote") or "").strip()
        turn = turns_by_ref.get(ref)
        if (
            not ref
            or ref in seen
            or turn is None
            or turn.get("role") != "user"
            or not turn.get("trusted")
            or normalize_scope(turn.get("memory_scope")) != PERSONAL_SCOPE
        ):
            continue
        quote_norm = _normalized_quote(quote)
        content_norm = _normalized_quote(turn.get("content") or "")
        if len(quote_norm) < 8 or quote_norm not in content_norm:
            continue
        seen.add(ref)
        accepted.append({
            "turn_ref": ref,
            "quote": quote[:500],
            "conversation_id": str(turn.get("conversation_id") or ""),
            "segment_id": turn.get("segment_id"),
            "observed_at": float(turn.get("observed_at") or 0.0),
        })
    if not any(item["turn_ref"] in new_owner_refs for item in accepted):
        return []
    return accepted


def validate_proposals(
    payload: dict,
    *,
    projection: dict,
    turns: tuple[dict, ...],
    new_owner_refs: frozenset[str],
) -> list[dict]:
    """Valida la proposta onirica senza giudicarne semanticamente il contenuto."""
    raw = payload.get("proposals")
    if not isinstance(raw, list):
        return []
    turns_by_ref = {
        str(turn.get("turn_ref") or ""): turn
        for turn in turns
        if turn.get("turn_ref")
    }
    traits_by_id = {
        str(trait.get("id") or ""): trait
        for trait in projection.get("traits", [])
        if isinstance(trait, dict) and trait.get("id")
    }
    valid: list[dict] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip().casefold()
        relation = str(item.get("relation") or "new").strip().casefold()
        strength = str(item.get("strength") or "pattern").strip().casefold()
        claim = " ".join(str(item.get("claim") or "").split())
        scope = " ".join(str(item.get("scope") or "generale").split())[:120]
        trait_id = str(item.get("trait_id") or "").strip()
        if (
            subject not in SUBJECTS
            or relation not in RELATIONS
            or strength not in STRENGTHS
            or len(claim) < 12
            # Un tratto identitario non puo' essere mozzato silenziosamente:
            # cambierebbe il significato di una vista che entra nel realtime.
            # Le proposte troppo lunghe vengono respinte e potranno essere
            # riformulate dal modello in un consolidamento successivo.
            or len(claim) > CLAIM_MAX_CHARS
            or not scope
        ):
            continue
        if relation != "new":
            target = traits_by_id.get(trait_id)
            if target is None or target.get("subject") != subject:
                continue
        evidence = _evidence_from_proposal(
            item, turns_by_ref, new_owner_refs=new_owner_refs
        )
        if not evidence:
            continue
        valid.append({
            "subject": subject,
            "relation": relation,
            "strength": strength,
            "claim": claim,
            "scope": scope,
            "trait_id": trait_id,
            "evidence": evidence,
        })
    return valid


def _merge_evidence(current: list[dict], incoming: list[dict]) -> list[dict]:
    by_ref = {
        str(item.get("turn_ref") or ""): dict(item)
        for item in current
        if isinstance(item, dict) and item.get("turn_ref")
    }
    for item in incoming:
        by_ref.setdefault(str(item["turn_ref"]), dict(item))
    return sorted(
        by_ref.values(),
        key=lambda item: (float(item.get("observed_at") or 0), item.get("turn_ref", "")),
    )[-20:]


def _support_metrics(trait: dict) -> tuple[int, int]:
    support = list(trait.get("support") or [])
    refs = {str(item.get("turn_ref") or "") for item in support if item.get("turn_ref")}
    contexts = {
        (
            str(item.get("conversation_id") or ""),
            item.get("segment_id"),
        )
        for item in support
        if item.get("conversation_id")
    }
    return len(refs), len(contexts)


def _refresh_trait_state(trait: dict, *, now_ts: float) -> None:
    refs, conversations = _support_metrics(trait)
    counters = len({
        str(item.get("turn_ref") or "")
        for item in trait.get("counter_evidence", [])
        if item.get("turn_ref")
    })
    if trait.get("status") == "contested":
        trait["confidence"] = min(float(trait.get("confidence") or 0.5), 0.35)
        return
    strength = trait.get("origin_strength")
    if strength == "declared" and refs >= 1:
        trait["status"] = "stable"
        trait["confidence"] = min(0.97, 0.84 + 0.03 * min(refs, 4))
    elif strength == "feedback" and refs >= 2 and conversations >= 2:
        trait["status"] = "stable"
        trait["confidence"] = min(0.91, 0.60 + 0.08 * refs + 0.05 * conversations)
    elif refs >= 3 and conversations >= 2:
        trait["status"] = "stable"
        trait["confidence"] = min(0.94, 0.58 + 0.06 * refs + 0.05 * conversations)
    else:
        trait["status"] = "candidate"
        trait["confidence"] = min(0.74, 0.38 + 0.07 * refs + 0.04 * conversations)
    if counters:
        trait["confidence"] = max(0.0, float(trait["confidence"]) - 0.12 * counters)
    trait["updated_at"] = now_ts


def evolve_projection(
    projection: dict,
    proposals: list[dict],
    *,
    processed_through: float,
    now_ts: float,
) -> dict:
    """Applica proposte validate mantenendo la vista append/revision-friendly."""
    updated = json.loads(json.dumps(projection, ensure_ascii=False))
    updated.setdefault("traits", [])
    traits_by_id = {
        str(trait.get("id") or ""): trait
        for trait in updated["traits"]
        if isinstance(trait, dict) and trait.get("id")
    }
    normalized_claims = {
        (trait.get("subject"), trait.get("scope"), _normalized_quote(trait.get("claim", ""))): trait
        for trait in traits_by_id.values()
    }
    for proposal in proposals:
        relation = proposal["relation"]
        if relation == "new":
            dedup_key = (
                proposal["subject"], proposal["scope"], _normalized_quote(proposal["claim"])
            )
            trait = normalized_claims.get(dedup_key)
            if trait is None:
                trait = {
                    "id": str(uuid.uuid4()),
                    "subject": proposal["subject"],
                    "scope": proposal["scope"],
                    "claim": proposal["claim"],
                    "status": "candidate",
                    "origin_strength": proposal["strength"],
                    "support": [],
                    "counter_evidence": [],
                    "created_at": now_ts,
                    "updated_at": now_ts,
                    "last_supported_at": 0.0,
                    "confidence": 0.0,
                }
                updated["traits"].append(trait)
                traits_by_id[trait["id"]] = trait
                normalized_claims[dedup_key] = trait
            relation = "supports"
        else:
            trait = traits_by_id[proposal["trait_id"]]

        if relation == "supports":
            trait["support"] = _merge_evidence(
                list(trait.get("support") or []), proposal["evidence"]
            )
            strength_priority = {"pattern": 0, "feedback": 1, "declared": 2}
            if strength_priority.get(proposal["strength"], 0) > strength_priority.get(
                trait.get("origin_strength"), 0
            ):
                trait["origin_strength"] = proposal["strength"]
            trait["last_supported_at"] = max(
                [float(item.get("observed_at") or 0) for item in trait["support"]] or [now_ts]
            )
        elif relation == "contradicts":
            trait["counter_evidence"] = _merge_evidence(
                list(trait.get("counter_evidence") or []), proposal["evidence"]
            )
            counter_refs = {item["turn_ref"] for item in trait["counter_evidence"]}
            counter_contexts = {
                (item.get("conversation_id"), item.get("segment_id"))
                for item in trait["counter_evidence"]
                if item.get("conversation_id")
            }
            if (
                proposal["strength"] == "declared"
                or (len(counter_refs) >= 2 and len(counter_contexts) >= 2)
            ):
                trait["status"] = "contested"
                trait["contested_at"] = now_ts
        _refresh_trait_state(trait, now_ts=now_ts)

    updated["schema_version"] = SCHEMA_VERSION
    updated["checkpoint_observed_at"] = max(
        float(updated.get("checkpoint_observed_at") or 0), float(processed_through)
    )
    updated["last_model_run_at"] = now_ts
    updated["updated_at"] = now_ts
    updated["revision"] = int(updated.get("revision") or 0) + 1
    return updated


def render_projection(
    projection: dict,
    *,
    actor_id: str,
    reference_at: float | None = None,
    max_chars: int | None = None,
) -> str:
    """Rende solo tratti stabili e freschi; candidati e contestati restano invisibili."""
    if str(projection.get("actor_id") or "") != str(actor_id):
        return ""
    now_ts = time.time() if reference_at is None else float(reference_at)
    stale_days = int(getattr(config, "PERSONALITY_PATTERN_STALE_DAYS", 180))
    stale_after = stale_days * 86400
    active = []
    for trait in projection.get("traits", []):
        if not isinstance(trait, dict) or trait.get("status") != "stable":
            continue
        if (
            trait.get("origin_strength") != "declared"
            and now_ts - float(trait.get("last_supported_at") or 0) > stale_after
        ):
            continue
        active.append(trait)
    if not active:
        return ""
    limit = int(getattr(config, "PERSONALITY_MAX_ACTIVE_TRAITS", 9))
    active.sort(key=lambda item: (
        item.get("origin_strength") == "declared",
        float(item.get("confidence") or 0),
        float(item.get("last_supported_at") or 0),
    ), reverse=True)
    active = active[:max(1, limit)]
    labels = {
        "assistant": f"Pattern appresi su {config.ASSISTANT_DISPLAY_NAME}",
        "interlocutor": f"Pattern appresi sull'interlocutore verificato {config.OWNER_DISPLAY_NAME}",
        "relationship": "Pattern appresi nella relazione",
    }
    sections = []
    for subject in ("assistant", "interlocutor", "relationship"):
        rows = [
            f"- [{trait.get('scope', 'generale')}] {trait.get('claim', '').strip()}"
            for trait in active if trait.get("subject") == subject
        ]
        if rows:
            sections.append(labels[subject] + ":\n" + "\n".join(rows))
    if not sections:
        return ""
    header = (
        "[PROIEZIONE IDENTITARIA APPRESA — vista derivata da turni verificati, non "
        "memoria fattuale e non diagnosi. Usala tacitamente per calibrare il modo di "
        "interagire; non recitarla spontaneamente. Il turno presente e le correzioni "
        "esplicite prevalgono sempre.]"
    )
    rendered = header + "\n" + "\n\n".join(sections)
    budget = int(max_chars or getattr(config, "PERSONALITY_CONTEXT_MAX_CHARS", 2800))
    return rendered[:max(200, budget)].rstrip()


class PersonalityModel:
    """Store e consolidatore lento del modello identitario owner-scoped."""

    def __init__(self, redis_client):
        self.r = redis_client

    @staticmethod
    def projection_key(actor_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(actor_id or "unknown"))
        return f"{PROJECTION_PREFIX}{safe}"

    def load(self, actor_id: str) -> dict:
        doc = _json_doc(self.r, self.projection_key(actor_id))
        if not doc or int(doc.get("schema_version") or 0) != SCHEMA_VERSION:
            return empty_projection(actor_id)
        if str(doc.get("actor_id") or "") != str(actor_id):
            return empty_projection(actor_id)
        return doc

    def render_context(self, actor_id: str) -> str:
        return render_projection(self.load(actor_id), actor_id=actor_id)

    def _turn_documents(self) -> list[dict]:
        turns = []
        try:
            keys = self.r.scan_iter(match="euri:turn:*")
            for raw_key in keys:
                doc = _json_doc(self.r, _decode(raw_key))
                if (
                    doc
                    and normalize_scope(doc.get("memory_scope")) == PERSONAL_SCOPE
                    and doc.get("role") in {"user", "assistant"}
                    and doc.get("turn_ref")
                ):
                    turns.append(doc)
        except Exception as exc:
            logger.debug(f"Modello identitario: archivio turni non disponibile ({exc})")
            return []
        turns.sort(key=lambda item: (
            float(item.get("observed_at") or 0), str(item.get("turn_ref") or "")
        ))
        return turns

    def prepare_if_due(
        self,
        actor_id: str,
        *,
        reference_at: float | None = None,
    ) -> PersonalityBatch | None:
        now_ts = time.time() if reference_at is None else float(reference_at)
        projection = self.load(actor_id)
        try:
            raw_attempt = self.r.get(f"{ATTEMPT_PREFIX}{actor_id}")
            last_attempt = float(_decode(raw_attempt)) if raw_attempt is not None else 0.0
        except Exception:
            last_attempt = 0.0
        last_success = float(projection.get("last_model_run_at") or 0)
        success_interval = float(
            getattr(config, "PERSONALITY_UPDATE_INTERVAL_S", 6 * 3600)
        )
        retry_interval = float(
            getattr(config, "PERSONALITY_RETRY_INTERVAL_S", 20 * 60)
        )
        if last_success and now_ts - last_success < success_interval:
            return None
        # Un output non valido non deve martellare il modello ogni cinque minuti,
        # ma nemmeno assumere l'intervallo lungo riservato a un consolidamento
        # riuscito. last_model_run_at avanza soltanto al commit della proiezione.
        if (
            last_attempt > last_success
            and now_ts - last_attempt < retry_interval
        ):
            return None
        turns = self._turn_documents()
        checkpoint = float(projection.get("checkpoint_observed_at") or 0)
        new_owner = [
            turn for turn in turns
            if turn.get("role") == "user"
            and turn.get("trusted")
            and float(turn.get("observed_at") or 0) > checkpoint
        ]
        minimum = int(getattr(config, "PERSONALITY_MIN_NEW_OWNER_TURNS", 8))
        if len(new_owner) < minimum:
            return None
        max_turns = int(getattr(config, "PERSONALITY_ANALYSIS_MAX_TURNS", 60))
        if checkpoint <= 0:
            # Bootstrap sul presente recente: non trasforma anni di archivio legacy
            # in una biografia istantanea. Le evoluzioni successive sono invece
            # consumate in ordine e nessun turno nuovo viene saltato.
            window_list = turns[-max(12, max_turns):]
        else:
            first_new_index = next(
                index for index, turn in enumerate(turns)
                if float(turn.get("observed_at") or 0) > checkpoint
            )
            context_start = max(0, first_new_index - 6)
            window_list = turns[
                context_start:first_new_index + max(12, max_turns)
            ]
        window = tuple(window_list)
        included_new_owner = [
            turn for turn in window
            if turn.get("role") == "user"
            and turn.get("trusted")
            and float(turn.get("observed_at") or 0) > checkpoint
        ]
        if len(included_new_owner) < minimum:
            return None
        new_refs = frozenset(str(turn["turn_ref"]) for turn in included_new_owner)
        return PersonalityBatch(
            actor_id=str(actor_id),
            projection=projection,
            turns=window,
            new_owner_refs=new_refs,
            checkpoint_before=checkpoint,
            processed_through=max(
                float(turn.get("observed_at") or 0) for turn in included_new_owner
            ),
        )

    @staticmethod
    def _prompt(batch: PersonalityBatch) -> str:
        existing = [
            {
                "id": trait.get("id"),
                "subject": trait.get("subject"),
                "scope": trait.get("scope"),
                "claim": trait.get("claim"),
                "status": trait.get("status"),
            }
            for trait in batch.projection.get("traits", [])
            if isinstance(trait, dict)
        ]
        lines = []
        for turn in batch.turns:
            role = config.OWNER_DISPLAY_NAME if turn.get("role") == "user" else config.ASSISTANT_DISPLAY_NAME
            marker = " NUOVO" if str(turn.get("turn_ref")) in batch.new_owner_refs else ""
            content_limit = 1400 if turn.get("role") == "user" else 900
            lines.append(
                f"[{turn.get('turn_ref')} | {role}{marker}] "
                f"{str(turn.get('content') or '')[:content_limit]}"
            )
        return (
            "Analizza una relazione conversazionale nel tempo. Non devi scrivere una "
            "personalita' piacevole: devi trovare soltanto pattern comportamentali "
            "sostenuti dalle parole dell'interlocutore autenticato. Distingui:\n"
            "- assistant: feedback esterno su come Euri ragiona/comunica;\n"
            "- interlocutor: preferenze operative contestuali, non diagnosi psicologiche;\n"
            "- relationship: dinamiche che esistono nel loro modo di collaborare.\n\n"
            "Puoi essere creativo nel NOTARE un pattern, ma non nella prova. Non usare "
            "mai una risposta di Euri come evidenza. Scarta fatti di progetto, umori "
            "momentanei, adulazione, categorie sensibili e generalizzazioni assolute. "
            "Un pattern osservato va ristretto al suo ambito.\n\n"
            "Per ogni proposta cita da 1 a 6 frammenti CONTIGUI e letterali di turni "
            f"di {config.OWNER_DISPLAY_NAME}; almeno uno deve avere il marcatore NUOVO. "
            "strength=declared solo per una preferenza, identita' conversazionale o "
            "regola di interazione dichiarata direttamente; strength=feedback per una "
            "valutazione/correzione su Euri; altrimenti pattern. Un complimento isolato "
            "non e' un tratto. "
            "Se la proposta coincide con un tratto esistente usa relation=supports e "
            "il suo trait_id; se lo smentisce usa contradicts.\n\n"
            "Rispondi SOLO con JSON valido nel formato:\n"
            '{"proposals":[{"subject":"assistant|interlocutor|relationship",'
            '"scope":"ambito concreto","claim":"pattern prudente",'
            '"relation":"new|supports|contradicts","trait_id":"id oppure vuoto",'
            '"strength":"declared|feedback|pattern","evidence":'
            '[{"turn_ref":"...","quote":"citazione esatta"}]}]}\n\n'
            f"Il claim deve essere una sola frase completa di massimo "
            f"{CLAIM_PROMPT_MAX_CHARS} caratteri. Se non riesci a formularlo "
            "entro il limite, non proporlo.\n\n"
            f"Tratti esistenti:\n{json.dumps(existing, ensure_ascii=False)}\n\n"
            "Turni:\n" + "\n".join(lines)
        )

    def update(
        self,
        batch: PersonalityBatch,
        *,
        model_call: Callable[..., Any],
        precommit_guard: Callable[[], bool] | None = None,
        reference_at: float | None = None,
    ) -> PersonalityUpdate:
        now_ts = time.time() if reference_at is None else float(reference_at)
        lock_key = f"{LOCK_PREFIX}{batch.actor_id}"
        token = str(uuid.uuid4())
        try:
            acquired = self.r.set(lock_key, token, nx=True, ex=15 * 60)
        except Exception as exc:
            logger.debug(f"Modello identitario: lock non disponibile ({exc})")
            return PersonalityUpdate(status="lock_error")
        if not acquired:
            return PersonalityUpdate(status="busy")
        try:
            self.r.set(f"{ATTEMPT_PREFIX}{batch.actor_id}", now_ts)
            response = model_call(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": self._prompt(batch)}],
                options={
                    "temperature": 0.35,
                    "num_predict": int(
                        getattr(config, "PERSONALITY_MODEL_NUM_PREDICT", 5000)
                    ),
                },
                format="json",
                # Questo e' un consolidatore strutturato, non il REM grezzo:
                # Con think=True il modello Dream puo' consumare l'intero budget nel
                # reasoning senza produrre content. La liberta' semantica resta
                # nel prompt/temperature; la veglia riceve direttamente JSON.
                think=False,
                _timeout=int(getattr(config, "PERSONALITY_MODEL_TIMEOUT_S", 240)),
            )
            message = getattr(response, "message", None)
            raw = getattr(message, "content", "") or ""
            payload = _parse_json_object(raw)
            if payload is None:
                thinking = getattr(message, "thinking", "") or ""
                logger.warning(
                    "Modello identitario: output non valido "
                    "(content_chars={} thinking_chars={} done_reason={} head={!r})",
                    len(raw),
                    len(thinking),
                    str(getattr(response, "done_reason", "") or "unknown"),
                    _clean_model_text(raw)[:240],
                )
                return PersonalityUpdate(status="invalid_model_output")
            proposals = validate_proposals(
                payload,
                projection=batch.projection,
                turns=batch.turns,
                new_owner_refs=batch.new_owner_refs,
            )
            if precommit_guard is not None and not precommit_guard():
                return PersonalityUpdate(status="stale_snapshot", proposals=len(payload.get("proposals") or []))
            evolved = evolve_projection(
                batch.projection,
                proposals,
                processed_through=batch.processed_through,
                now_ts=now_ts,
            )
            self.r.json().set(self.projection_key(batch.actor_id), "$", evolved)
            stable = sum(
                1 for trait in evolved.get("traits", []) if trait.get("status") == "stable"
            )
            return PersonalityUpdate(
                status="updated",
                proposals=len(payload.get("proposals") or []),
                accepted=len(proposals),
                stable=stable,
                revision=int(evolved.get("revision") or 0),
            )
        except Exception as exc:
            logger.warning(f"Modello identitario: aggiornamento fallito ({exc})")
            return PersonalityUpdate(status="error")
        finally:
            try:
                current = self.r.get(lock_key)
                if current is not None and _decode(current) == token:
                    self.r.delete(lock_key)
            except Exception:
                pass
