"""Policy dual-channel CONGELATA (primaria) per la validazione A/B.

Architettura: **intero contesto prodotto dal ramo rag_only, integralmente
protetto** + memorie passive usate SOLO come locator. Il testo sintetico delle
note NON entra mai nel prompt: le note servono unicamente a risolvere i loro
source_turn_ids in turni verbatim (con speaker), idratati sopra la base.

Parametri congelati (dual-channel-q2r1-v1):
- base = contesto rag_only intero, protetto: mai rimosso o troncato (il RAG può
  produrre più di 5 nodi nelle query temporali; il compositore non tronca);
- Q = 2 note passive considerate, nell'ordine di retrieval registrato;
- R = 1 source turn verbatim per nota;
- dedup rispetto alla base e fra i sorgenti idratati;
- max 2 aggiunte (quindi final_slots <= base_slots + 2);
- budget finale 2.500 caratteri: limita SOLO le aggiunte, calcolato sul RENDERING
  reale (intestazione, speaker, separatori, newline); non modifica mai la base;
- category-agnostic: il risultato single-hop è diagnostico, non un gate.

Deterministico, nessun LLM. Riusabile dall'harness A/B e riproducibile offline.
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

# Renderer deterministico delle aggiunte. La base non viene mai toccata.
ADDITIONS_HEADER = "\n\n[Turni verbatim aggiuntivi dal canale passivo]"
ADDITION_SEP = "\n"


def render_additions_block(rendered_turns: Sequence[str]) -> str:
    """Blocco delle aggiunte, o stringa vuota se non ce ne sono."""

    if not rendered_turns:
        return ""
    return ADDITIONS_HEADER + "".join(ADDITION_SEP + r for r in rendered_turns)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class DualChannelComposition:
    base_slots: int
    base_turn_ids: list[str]
    base_context_text: str
    additions: list[dict]              # {turn_id, rendered, chars, from_note_index}
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
    candidates_considered: list[dict]  # strumentazione: ogni source e la sua decisione

    def added_turn_ids(self) -> list[str]:
        return [a["turn_id"] for a in self.additions]

    def to_record(self) -> dict:
        # Il testo della base non viene copiato nel record (solo hash e lunghezze).
        return {
            "policy_id": POLICY_ID,
            "base_slots": self.base_slots,
            "base_turn_ids": list(self.base_turn_ids),
            "base_sha256": self.base_sha256,
            "base_chars": self.base_chars,
            "additions": [
                {k: a[k] for k in ("turn_id", "chars", "from_note_index")}
                for a in self.additions
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
    """Compone il contesto dual-channel secondo la policy congelata.

    - ``base_context_text``: il contesto rag_only INTERO (protetto, mai troncato).
    - ``base_slots``: numero di nodi della base (può essere > 5).
    - ``base_turn_ids``: turni della base, per il dedup.
    - ``locator_notes``: source_turn_ids reali delle note passive, nell'ordine di
      retrieval (una lista per nota). Il TESTO delle note non è usato.
    - ``render_turn``: rende un turno verbatim con speaker ("Speaker: testo"),
      senza separatore iniziale. Il budget è calcolato sul rendering reale.
    """

    base_ids = list(dict.fromkeys(str(t) for t in base_turn_ids))
    base_set = set(base_ids)
    added: list[dict] = []
    added_render: list[str] = []
    dup = slot = budg = 0
    candidates: list[dict] = []
    budget = policy["char_budget"]

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
                rendered = render_turn(src)
                prospective = base_context_text + render_additions_block(added_render + [rendered])
                if budget is not None and len(prospective) > budget:
                    budg += 1
                    record["decision"] = "discarded_budget"
                else:
                    added.append(
                        {
                            "turn_id": src,
                            "rendered": rendered,
                            "chars": len(rendered),
                            "from_note_index": note_index,
                        }
                    )
                    added_render.append(rendered)
                    record["decision"] = "added"
            candidates.append(record)

    final_context_text = base_context_text + render_additions_block(added_render)
    final_ids = base_ids + [a["turn_id"] for a in added]
    return DualChannelComposition(
        base_slots=base_slots,
        base_turn_ids=base_ids,
        base_context_text=base_context_text,
        additions=added,
        duplicates_skipped=dup,
        discarded_slot_cap=slot,
        discarded_budget=budg,
        final_turn_ids=final_ids,
        final_context_text=final_context_text,
        base_chars=len(base_context_text),
        final_chars=len(final_context_text),
        final_slots=base_slots + len(added),
        base_sha256=_sha256(base_context_text),
        final_sha256=_sha256(final_context_text),
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
