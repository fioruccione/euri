"""Policy dual-channel CONGELATA (primaria) per la validazione A/B.

Architettura: base RAG grezza PROTETTA + memorie passive usate SOLO come locator.
Il testo sintetico delle note NON entra mai nel prompt: le note servono unicamente
a risolvere i loro source_turn_ids in turni verbatim, idratati sopra la base.

Parametri congelati (dual-channel-q2r1-v1):
- base = top-5 rag_only, protetta: mai rimossa o troncata;
- Q = 2 note passive considerate, nell'ordine registrato;
- R = 1 source turn verbatim per nota;
- dedup rispetto alla base e fra i sorgenti idratati;
- max 2 aggiunte (max 7 slot);
- budget finale 2.500 caratteri: limita SOLO le aggiunte, mai la base;
- category-agnostic: il risultato single-hop è diagnostico, non un gate.

Deterministico, nessun LLM. Riusabile dall'harness A/B e riproducibile offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


POLICY_ID = "dual-channel-q2r1-v1"

FROZEN_POLICY: dict[str, Any] = {
    "policy_id": POLICY_ID,
    "base": "top5_rag_only_protected",
    "Q_notes": 2,
    "R_sources_per_note": 1,
    "max_additions": 2,
    "char_budget": 2500,
    "protect_base": True,
    "synthetic_text_in_prompt": False,
    "category_agnostic": True,
}


@dataclass
class DualChannelComposition:
    base_turn_ids: list[str]
    additions: list[dict]              # {turn_id, chars, from_note_index}
    duplicates_skipped: int
    discarded_slot_cap: int
    discarded_budget: int
    final_turn_ids: list[str]
    base_chars: int
    final_chars: int
    final_slots: int
    candidates_considered: list[dict]  # strumentazione: ogni source e la sua decisione

    def added_turn_ids(self) -> list[str]:
        return [a["turn_id"] for a in self.additions]

    def to_record(self) -> dict:
        return {
            "policy_id": POLICY_ID,
            "base_turn_ids": list(self.base_turn_ids),
            "additions": list(self.additions),
            "added_turn_ids": self.added_turn_ids(),
            "duplicates_skipped": self.duplicates_skipped,
            "discarded_slot_cap": self.discarded_slot_cap,
            "discarded_budget": self.discarded_budget,
            "final_turn_ids": list(self.final_turn_ids),
            "base_chars": self.base_chars,
            "final_chars": self.final_chars,
            "final_slots": self.final_slots,
            "candidates_considered": list(self.candidates_considered),
        }


def compose_dual_channel(
    *,
    base_turn_ids: Sequence[str],
    base_chars: int,
    locator_notes: Sequence[Sequence[str]],
    turn_chars: Callable[[str], int],
    policy: dict = FROZEN_POLICY,
) -> DualChannelComposition:
    """Compone il contesto dual-channel secondo la policy congelata.

    - ``base_turn_ids``: i turni della base rag_only protetta (ordine di posizione).
    - ``locator_notes``: source_turn_ids reali delle note passive, nell'ordine
      registrato (una lista per nota). Il TESTO delle note non è usato.
    - ``turn_chars``: lunghezza in caratteri del turno verbatim (per il budget).
    """

    base = list(dict.fromkeys(str(t) for t in base_turn_ids))
    base_set = set(base)
    added: list[dict] = []
    added_chars = 0
    dup = slot = budg = 0
    candidates: list[dict] = []

    for note_index, note in enumerate(list(locator_notes)[: policy["Q_notes"]]):
        for src in [str(t) for t in note][: policy["R_sources_per_note"]]:
            record = {"note_index": note_index, "source_turn_id": src}
            if src in base_set or src in {a["turn_id"] for a in added}:
                dup += 1
                record["decision"] = "duplicate"
            elif len(added) >= policy["max_additions"]:
                slot += 1
                record["decision"] = "discarded_slot_cap"
            else:
                c = int(turn_chars(src))
                budget = policy["char_budget"]
                if budget is not None and base_chars + added_chars + c > budget:
                    budg += 1
                    record["decision"] = "discarded_budget"
                else:
                    added.append({"turn_id": src, "chars": c, "from_note_index": note_index})
                    added_chars += c
                    record["decision"] = "added"
            candidates.append(record)

    final_ids = base + [a["turn_id"] for a in added]
    return DualChannelComposition(
        base_turn_ids=base,
        additions=added,
        duplicates_skipped=dup,
        discarded_slot_cap=slot,
        discarded_budget=budg,
        final_turn_ids=final_ids,
        base_chars=base_chars,
        final_chars=base_chars + added_chars,
        final_slots=len(final_ids),
        candidates_considered=candidates,
    )


def gold_covered(final_turn_ids: Sequence[str], gold_turn_ids: Sequence[str]) -> bool:
    return bool(set(map(str, final_turn_ids)) & set(map(str, gold_turn_ids)))


# Universo della validazione dual-channel: le conversazioni LoCoMo MAI usate.
# Escluse: conv-26/42 (sviluppo passive) e conv-30/43/47 (held-out seed914917171,
# ormai development set aperto per l'analisi eviction/policy).
DUAL_CHANNEL_EXCLUDED_SAMPLE_IDS = frozenset(
    {"conv-26", "conv-42", "conv-30", "conv-43", "conv-47"}
)


def untouched_universe(all_sample_ids: Sequence[str]) -> list[str]:
    """Le conversazioni ancora mai toccate, ordinate. Attese: 5."""

    return sorted(s for s in all_sample_ids if s not in DUAL_CHANNEL_EXCLUDED_SAMPLE_IDS)
