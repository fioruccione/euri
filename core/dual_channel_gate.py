"""Gate osservabile per la presentazione selettiva del dual-channel.

La memoria passiva resta un locator. Il gate giudica esclusivamente il turno
originale idratato e lo confronta con la domanda e con la base protetta:

* rilevanza domanda -> turno originale;
* margine rispetto al miglior nodo della base;
* ridondanza semantica turno -> base.

La policy è deliberatamente fail-closed: embedding mancanti o segnali sotto
soglia mantengono la presentazione append validata.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np

from core.dual_channel import DualChannelComposition, render_additions_block


SELECTIVE_POLICY_ID = "dual-channel-selective-prepend-v0"


@dataclass(frozen=True)
class SelectiveThresholds:
    min_query_source_similarity: float = 0.92
    min_relevance_margin: float = -0.01
    max_source_base_similarity: float = 0.985

    def to_record(self) -> dict[str, float]:
        return {
            "min_query_source_similarity": self.min_query_source_similarity,
            "min_relevance_margin": self.min_relevance_margin,
            "max_source_base_similarity": self.max_source_base_similarity,
        }


def _dot(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(left, right))


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def evaluate_selective_gate(
    *,
    query: str,
    base_nodes: Sequence[dict],
    additions: Sequence[dict],
    locator_nodes: Sequence[dict],
    embedder,
    thresholds: SelectiveThresholds,
) -> dict[str, Any]:
    """Valuta quali aggiunte meritano il primo piano, senza toccare memoria."""
    record: dict[str, Any] = {
        "policy_id": SELECTIVE_POLICY_ID,
        "thresholds": thresholds.to_record(),
        "candidates": [],
        "promoted_turn_ids": [],
        "presentation": "append",
    }
    if not additions:
        record["fallback_reason"] = "no_additions"
        return record
    if not embedder or not getattr(embedder, "available", False):
        record["fallback_reason"] = "embedder_unavailable"
        return record

    query_vector = embedder.encode(query, mode="query")
    if query_vector is None:
        record["fallback_reason"] = "query_embedding_unavailable"
        return record

    addition_texts = [str(item.get("content") or "").strip() for item in additions]
    base_texts = [
        str(node.get("content") or "").strip()
        for node in base_nodes
        if str(node.get("content") or "").strip()
    ]
    passage_texts = addition_texts + base_texts
    if hasattr(embedder, "encode_many"):
        passage_vectors = embedder.encode_many(passage_texts, mode="passage")
    else:
        encoded = [embedder.encode(text, mode="passage") for text in passage_texts]
        passage_vectors = (
            None if any(vector is None for vector in encoded) else np.asarray(encoded)
        )
    if passage_vectors is None or len(passage_vectors) != len(passage_texts):
        record["fallback_reason"] = "passage_embedding_unavailable"
        return record

    addition_vectors = passage_vectors[: len(additions)]
    base_vectors = passage_vectors[len(additions):]
    query_base_scores = [_dot(query_vector, vector) for vector in base_vectors]
    query_base_best = max(query_base_scores, default=None)
    record["query_base_best_similarity"] = _round(query_base_best)

    promoted: list[str] = []
    for index, (addition, vector) in enumerate(zip(additions, addition_vectors)):
        query_source = _dot(query_vector, vector)
        source_base_scores = [_dot(vector, base) for base in base_vectors]
        source_base_max = max(source_base_scores, default=None)
        relevance_margin = (
            None if query_base_best is None else query_source - query_base_best
        )
        note_index = int(addition.get("from_note_index") or 0)
        locator = locator_nodes[note_index] if note_index < len(locator_nodes) else {}

        reasons = []
        if query_source < thresholds.min_query_source_similarity:
            reasons.append("weak_query_source")
        if (
            relevance_margin is not None
            and relevance_margin < thresholds.min_relevance_margin
        ):
            reasons.append("below_base_margin")
        if (
            source_base_max is not None
            and source_base_max > thresholds.max_source_base_similarity
        ):
            reasons.append("redundant_with_base")
        promote = not reasons
        turn_id = str(addition.get("turn_id") or "")
        if promote and turn_id:
            promoted.append(turn_id)

        record["candidates"].append(
            {
                "turn_id": turn_id,
                "from_note_index": note_index,
                "locator_memory_id": str(locator.get("id") or ""),
                "locator_rank": (
                    int(locator.get("position") or note_index + 1)
                    if locator else None
                ),
                "locator_distance": _round(locator.get("retrieval_score")),
                "query_source_similarity": _round(query_source),
                "query_base_best_similarity": _round(query_base_best),
                "relevance_margin": _round(relevance_margin),
                "source_base_max_similarity": _round(source_base_max),
                "decision": "prepend" if promote else "append",
                "reasons": reasons,
            }
        )

    record["promoted_turn_ids"] = promoted
    if promoted:
        record["presentation"] = "selective_prepend"
    else:
        record["fallback_reason"] = "no_high_confidence_candidate"
    return record


def compose_selective_presentation(
    composition: DualChannelComposition,
    promoted_turn_ids: Sequence[str],
) -> tuple[str, dict[str, str]]:
    """Porta davanti solo i turni promossi; gli altri restano in append."""
    promoted = set(map(str, promoted_turn_ids))
    front = [
        item["rendered"]
        for item in composition.additions
        if item["turn_id"] in promoted
    ]
    tail = [
        item["rendered"]
        for item in composition.additions
        if item["turn_id"] not in promoted
    ]
    if not front:
        text = composition.final_context_text
        regions = {item["turn_id"]: "append" for item in composition.additions}
    else:
        text = (
            render_additions_block(front).lstrip("\n")
            + "\n\n"
            + composition.base_context_text
            + render_additions_block(tail)
        )
        regions = {
            item["turn_id"]: (
                "prepend" if item["turn_id"] in promoted else "append"
            )
            for item in composition.additions
        }
    return text, {
        **regions,
        "_final_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
