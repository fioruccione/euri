"""Compositore dual-channel validato per la memoria passiva.

La base RAG resta integralmente protetta. Le note passive sono soltanto locator:
il loro testo sintetico non entra nel prompt; vengono aggiunti al massimo due
turni sorgente originali, se disponibili nell'archivio durevole.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Sequence


POLICY_ID = "dual-channel-q2r1-v1"
FROZEN_POLICY: dict[str, Any] = {
    "policy_id": POLICY_ID,
    "base": "entire_rag_only_context_protected",
    "Q_notes": 2,
    "R_sources_per_note": 1,
    "max_additions": 2,
    "char_budget": 2500,
    "protect_base": True,
    "synthetic_text_in_prompt": False,
    "category_agnostic": True,
}

ADDITIONS_HEADER = "\n\n[Turni verbatim aggiuntivi dal canale passivo]"
ADDITION_SEP = "\n"


def render_additions_block(rendered_turns: Sequence[str]) -> str:
    if not rendered_turns:
        return ""
    return ADDITIONS_HEADER + "".join(ADDITION_SEP + row for row in rendered_turns)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class DualChannelComposition:
    base_slots: int
    base_turn_ids: list[str]
    base_context_text: str
    additions: list[dict]
    duplicates_skipped: int
    discarded_slot_cap: int
    discarded_budget: int
    final_turn_ids: list[str]
    final_context_text: str
    base_chars: int
    final_chars: int
    final_slots: int
    base_sha256: str
    final_sha256: str
    candidates_considered: list[dict]

    def added_turn_ids(self) -> list[str]:
        return [item["turn_id"] for item in self.additions]

    def to_record(self) -> dict:
        return {
            "policy_id": POLICY_ID,
            "base_slots": self.base_slots,
            "base_turn_ids": list(self.base_turn_ids),
            "base_sha256": self.base_sha256,
            "base_chars": self.base_chars,
            "additions": [
                {key: item[key] for key in ("turn_id", "chars", "from_note_index")}
                for item in self.additions
            ],
            "added_turn_ids": self.added_turn_ids(),
            "duplicates_skipped": self.duplicates_skipped,
            "discarded_slot_cap": self.discarded_slot_cap,
            "discarded_budget": self.discarded_budget,
            "final_turn_ids": list(self.final_turn_ids),
            "final_sha256": self.final_sha256,
            "final_chars": self.final_chars,
            "final_slots": self.final_slots,
            "candidates_considered": list(self.candidates_considered),
        }


def compose_dual_channel(
    *,
    base_context_text: str,
    base_slots: int,
    base_turn_ids: Sequence[str],
    locator_notes: Sequence[Sequence[str]],
    render_turn: Callable[[str], str],
    policy: dict = FROZEN_POLICY,
) -> DualChannelComposition:
    base_ids = list(dict.fromkeys(str(turn) for turn in base_turn_ids))
    base_set = set(base_ids)
    added: list[dict] = []
    rendered_additions: list[str] = []
    duplicates = discarded_slot = discarded_budget = 0
    candidates: list[dict] = []

    for note_index, note in enumerate(list(locator_notes)[: policy["Q_notes"]]):
        for source in [str(turn) for turn in note][: policy["R_sources_per_note"]]:
            record = {"note_index": note_index, "source_turn_id": source}
            if source in base_set or source in {item["turn_id"] for item in added}:
                duplicates += 1
                record["decision"] = "duplicate"
            elif len(added) >= policy["max_additions"]:
                discarded_slot += 1
                record["decision"] = "discarded_slot_cap"
            else:
                rendered = render_turn(source)
                if not rendered:
                    record["decision"] = "source_unavailable"
                else:
                    prospective = (
                        base_context_text
                        + render_additions_block(rendered_additions + [rendered])
                    )
                    budget = policy["char_budget"]
                    if budget is not None and len(prospective) > budget:
                        discarded_budget += 1
                        record["decision"] = "discarded_budget"
                    else:
                        added.append(
                            {
                                "turn_id": source,
                                "rendered": rendered,
                                "chars": len(rendered),
                                "from_note_index": note_index,
                            }
                        )
                        rendered_additions.append(rendered)
                        record["decision"] = "added"
            candidates.append(record)

    final_text = base_context_text + render_additions_block(rendered_additions)
    final_ids = base_ids + [item["turn_id"] for item in added]
    return DualChannelComposition(
        base_slots=base_slots,
        base_turn_ids=base_ids,
        base_context_text=base_context_text,
        additions=added,
        duplicates_skipped=duplicates,
        discarded_slot_cap=discarded_slot,
        discarded_budget=discarded_budget,
        final_turn_ids=final_ids,
        final_context_text=final_text,
        base_chars=len(base_context_text),
        final_chars=len(final_text),
        final_slots=base_slots + len(added),
        base_sha256=_sha256(base_context_text),
        final_sha256=_sha256(final_text),
        candidates_considered=candidates,
    )


def gold_covered(final_turn_ids: Sequence[str], gold_turn_ids: Sequence[str]) -> bool:
    return bool(set(map(str, final_turn_ids)) & set(map(str, gold_turn_ids)))
