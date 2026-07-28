"""Lineage osservazionale tra turno, recall e risposta.

Questo modulo non decide cosa recuperare e non modifica il prompt. Registra:

* quali nodi sono entrati davvero nel contesto del modello;
* il confine del turno e della risposta;
* una attribuzione conservativa di uso quando la risposta contiene evidenza
  lessicale distintiva del nodo.

`used_in_response` significa quindi "uso sostenuto dall'osservazione v1", non
verità del contenuto, attenzione interna provata o conferma esterna.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from core.pulse import cognitive_emit


LINEAGE_EXPERIMENT = "response_lineage_shadow_v1"
_PRODUCER = "response_lineage"
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOP = {
    "alla", "alle", "anche", "ancora", "avere", "come", "con", "cosa",
    "dalla", "dalle", "della", "delle", "degli", "dello", "dentro", "dire",
    "dopo", "dove", "essere", "fare", "fatto", "forse", "fuori", "gli",
    "hai", "hanno", "ieri", "inoltre", "invece", "loro", "mentre", "molto",
    "nella", "nelle", "negli", "nello", "non", "oggi", "ogni", "per", "perché",
    "pero", "però", "più", "prima", "può", "quale", "quella", "quelle",
    "quelli", "quello", "questa", "queste", "questi", "questo", "senza",
    "sono", "sotto", "sopra", "stato", "sulla", "sulle", "tale", "tra",
    "tutto", "una", "uno", "verso", "the", "and", "that", "this", "with",
    "from", "into", "have", "has", "was", "were",
}


@dataclass
class TurnLineage:
    turn_id: str
    trace_id: str
    channel: str
    mode: str
    query: str
    nodes: list[dict]
    started_event_id: str = ""
    recalled_event_ids: dict[tuple[str, str], str] = field(default_factory=dict)


def _event_id(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normal(text))


def _entity(node: dict) -> dict:
    return {"type": node["kind"], "id": node["id"]}


def _dedup_nodes(nodes: list[dict] | None) -> list[dict]:
    clean: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in nodes or []:
        kind = str(raw.get("kind") or "memory")
        node_id = str(raw.get("id") or "")
        for prefix in ("euri:memory:", "euri:insight:", "euri:note:"):
            node_id = node_id.removeprefix(prefix)
        content = str(raw.get("content") or "").strip()
        key = (kind, node_id)
        if not node_id or not content or key in seen:
            continue
        seen.add(key)
        node = dict(raw)
        node.update({"kind": kind, "id": node_id, "content": content})
        clean.append(node)
    return clean


def load_augmented_memory_nodes(memory, ids: list[str], *, start_position: int = 1) -> list[dict]:
    """Risoluzione read-only degli ID aggiunti dal retrieval strategico."""
    nodes: list[dict] = []
    for position, raw_id in enumerate(ids or [], start_position):
        memory_id = str(raw_id or "").removeprefix("euri:memory:")
        if not memory_id:
            continue
        try:
            data = memory.r.json().get(f"euri:memory:{memory_id}", "$")
            doc = data[0] if data else {}
        except Exception:
            continue
        content = str(doc.get("content") or "").strip()
        if not content:
            continue
        nodes.append({
            "kind": "memory",
            "id": memory_id,
            "content": content,
            "position": position,
            "retrieval_path": "strategy_augment",
            "source": str(doc.get("source") or ""),
            "domain": str(doc.get("domain") or ""),
        })
    return nodes


def start_response_turn(
    r,
    *,
    query: str,
    channel: str,
    mode: str,
    nodes: list[dict] | None,
) -> TurnLineage:
    """Apre una trace e registra ogni nodo realmente inserito nel prompt."""
    turn_id = str(uuid.uuid4())
    trace_id = f"turn:{turn_id}"
    clean_nodes = _dedup_nodes(nodes)
    lineage = TurnLineage(turn_id, trace_id, channel, mode, query, clean_nodes)
    try:
        started = cognitive_emit(
            r,
            "turn",
            "extero",
            "started",
            payload={
                "channel": channel,
                "mode": mode,
                "query_sha256": _digest(query),
                "query_chars": len(query or ""),
                "recalled_nodes": len(clean_nodes),
            },
            salience=0.3,
            producer=_PRODUCER,
            trace_id=trace_id,
            logical_event_id=f"{trace_id}:started",
            entity_refs=[{"type": "turn", "id": turn_id}],
            epistemic_before="user_turn_observed",
            epistemic_after="response_context_building",
            experiment_version=LINEAGE_EXPERIMENT,
        )
        lineage.started_event_id = _event_id(started)

        for node in clean_nodes:
            kind = node["kind"] if node["kind"] in {"memory", "insight"} else "memory"
            recalled = cognitive_emit(
                r,
                kind,
                "intero",
                "recalled",
                payload={
                    "turn_id": turn_id,
                    "channel": channel,
                    "mode": mode,
                    "position": int(node.get("position") or 0),
                    "retrieval_path": str(node.get("retrieval_path") or ""),
                    "retrieval_score": node.get("retrieval_score"),
                    "source": str(node.get("source") or ""),
                    "domain": str(node.get("domain") or ""),
                    "prompt_region": str(node.get("prompt_region") or ""),
                    "selective_gate_decision": str(
                        node.get("selective_gate_decision") or ""
                    ),
                    "query_source_similarity": node.get(
                        "query_source_similarity"
                    ),
                    "relevance_margin": node.get("relevance_margin"),
                    "source_base_max_similarity": node.get(
                        "source_base_max_similarity"
                    ),
                    "locator_memory_id": str(
                        node.get("locator_memory_id") or ""
                    ),
                    "query_sha256": _digest(query),
                },
                salience=0.25,
                producer=_PRODUCER,
                trace_id=trace_id,
                causation_id=lineage.started_event_id,
                logical_event_id=f"{trace_id}:{kind}:recalled:{node['id']}",
                entity_refs=[_entity(node)],
                parent_refs=[{"type": "turn", "id": turn_id}],
                epistemic_before="retrieved_candidate",
                epistemic_after="injected_in_response_prompt",
                experiment_version=LINEAGE_EXPERIMENT,
            )
            lineage.recalled_event_ids[(kind, node["id"])] = _event_id(recalled)
    except Exception:
        # Il tracciamento è deliberatamente fail-open.
        pass
    return lineage


def _usage_evidence(node_content: str, query: str, response: str) -> dict | None:
    query_tokens = set(_tokens(query))
    response_tokens = _tokens(response)
    response_set = set(response_tokens)
    content_tokens = _tokens(node_content)

    informative = [
        token for token in content_tokens
        if len(token) >= 4 and token not in _STOP and token not in query_tokens
    ]
    informative_set = set(informative)
    matches = informative_set & response_set

    raw_identifiers = {
        token.casefold()
        for token in re.findall(r"\b[\w.-]+\b", node_content or "")
        if (
            any(char.isdigit() for char in token)
            or (len(token) >= 2 and token.isupper())
        )
    }
    query_identifiers = {
        token.casefold() for token in re.findall(r"\b[\w.-]+\b", query or "")
    }
    response_identifiers = {
        token.casefold() for token in re.findall(r"\b[\w.-]+\b", response or "")
    }
    identifier_matches = (
        raw_identifiers - query_identifiers
    ) & response_identifiers

    informative_sequence = [
        token for token in content_tokens
        if len(token) >= 4 and token not in _STOP
    ]
    response_sequence = [
        token for token in response_tokens
        if len(token) >= 4 and token not in _STOP
    ]
    content_bigrams = set(zip(informative_sequence, informative_sequence[1:]))
    response_bigrams = set(zip(response_sequence, response_sequence[1:]))
    query_bigrams = set(zip(_tokens(query), _tokens(query)[1:]))
    bigram_matches = (content_bigrams - query_bigrams) & response_bigrams

    coverage = len(matches) / max(1, min(len(informative_set), 12))
    supported = (
        bool(identifier_matches)
        or (len(bigram_matches) >= 1 and len(matches) >= 3)
        or (len(matches) >= 4 and coverage >= 0.25)
    )
    if not supported:
        return None
    score = min(
        1.0,
        0.16 * len(matches)
        + 0.28 * len(bigram_matches)
        + 0.62 * len(identifier_matches),
    )
    return {
        "method": "distinctive_lexical_overlap_v1",
        "status": "supported_not_proven",
        "score": round(score, 3),
        "matched_terms": len(matches),
        "matched_bigrams": len(bigram_matches),
        "matched_identifiers": len(identifier_matches),
    }


def finish_response_turn(
    r,
    lineage: TurnLineage | None,
    *,
    response: str,
    outcome: str = "delivered",
    attribute_usage: bool = True,
) -> None:
    """Chiude il turno e, dopo la risposta finale, emette le attribuzioni supportate."""
    if lineage is None:
        return
    try:
        responded = cognitive_emit(
            r,
            "turn",
            "intero",
            "responded",
            payload={
                "turn_id": lineage.turn_id,
                "channel": lineage.channel,
                "mode": lineage.mode,
                "outcome": outcome,
                "response_sha256": _digest(response),
                "response_chars": len(response or ""),
            },
            salience=0.3,
            producer=_PRODUCER,
            trace_id=lineage.trace_id,
            causation_id=lineage.started_event_id,
            logical_event_id=f"{lineage.trace_id}:responded",
            entity_refs=[{"type": "turn", "id": lineage.turn_id}],
            parent_refs=[
                event_id for event_id in lineage.recalled_event_ids.values() if event_id
            ],
            epistemic_before="response_context_built",
            epistemic_after=f"response_{outcome}",
            experiment_version=LINEAGE_EXPERIMENT,
        )
        responded_id = _event_id(responded)
        if not attribute_usage or outcome != "delivered":
            return

        for node in lineage.nodes:
            evidence = _usage_evidence(
                node["content"], lineage.query, response
            )
            if evidence is None:
                continue
            kind = node["kind"] if node["kind"] in {"memory", "insight"} else "memory"
            recalled_id = lineage.recalled_event_ids.get((kind, node["id"]), "")
            cognitive_emit(
                r,
                kind,
                "intero",
                "used_in_response",
                payload={
                    "turn_id": lineage.turn_id,
                    "channel": lineage.channel,
                    "mode": lineage.mode,
                    **evidence,
                },
                salience=0.35,
                producer=_PRODUCER,
                trace_id=lineage.trace_id,
                causation_id=responded_id,
                logical_event_id=(
                    f"{lineage.trace_id}:{kind}:used_in_response:{node['id']}"
                ),
                entity_refs=[_entity(node)],
                parent_refs=[event for event in (recalled_id, responded_id) if event],
                epistemic_before="recalled_not_equivalent_to_used",
                epistemic_after="use_evidence_detected",
                experiment_version=LINEAGE_EXPERIMENT,
            )
    except Exception:
        pass
