"""Regressioni pure per la proiezione semantica del contesto."""
from __future__ import annotations

import config
from core.semantic_context import (
    build_semantic_context,
    classify_memory_state,
    with_memory_clarification_frame,
)
from core.rag_context import (
    RagContext,
    apply_memory_clarification_contract,
    build_rag_context,
    build_runtime_rag_context,
)
from core.semantic_turn import frame_blocks_passive_memory


def _doc(mid: str, content: str, *, source: str = "user", **extra) -> dict:
    doc = {
        "id": mid,
        "content": content,
        "source": source,
        "memory_axes": {
            "entity_mentions": ["ICMA2", "RAS500", "FPP20"],
        },
    }
    doc.update(extra)
    return doc


def test_state_classification_uses_explicit_markers_without_claiming_truth():
    assert classify_memory_state(_doc("a", "Configurazione attuale: RAS500.")) == "attuale"
    assert classify_memory_state(_doc("p", "Modifica proposta: installare FPP20.")) == "proposta/ipotesi"
    assert classify_memory_state(_doc("m", "Configurazione attuale: RAS500. Modifica proposta: FPP20.")) == "attuale + proposta nello stesso ricordo"
    assert classify_memory_state(_doc("h", "Vecchia configurazione precedente.", superseded_by="new")) == "storica/superata"


def test_semantic_context_is_bounded_and_preserves_provenance_and_claim():
    packet = build_semantic_context([
        _doc("current", "Configurazione attuale: bivite -> RAS500 -> pompa a ingranaggi.", requires_verification=False),
        _doc("passive", "Una pompa spinge il fuso verso il filtro.", source="passive"),
        _doc("proposal", "Modifica proposta: FIMIC FPP20 tra bivite e RAS500."),
        _doc("ignored", "Non deve entrare nel pacchetto."),
    ], limit=3)

    assert packet.diagnostics["memory_count"] == 3
    assert "stato=attuale" in packet.text
    assert "stato=non specificato (passiva)" in packet.text
    assert "fonte=estratta passivamente, non verificata" in packet.text
    assert "memory_id=current" in packet.text
    assert "FIMIC FPP20 tra bivite e RAS500" in packet.text
    assert "ignored" not in packet.text


def test_mixed_memory_remains_one_claim_and_is_not_silently_rewritten():
    content = (
        "Configurazione attuale: bivite, RAS500, pompa a ingranaggi. "
        "Modifica proposta: FIMIC FPP20 tra bivite e RAS500."
    )
    packet = build_semantic_context([_doc("mixed", content)], limit=3)

    assert "stato=attuale + proposta nello stesso ricordo" in packet.text
    assert content in packet.text
    assert "non puo' sovrascrivere" in packet.text


def test_empty_context_is_a_noop():
    packet = build_semantic_context([])
    assert packet.text == ""
    assert packet.diagnostics["memory_count"] == 0


class _Memory:
    def __init__(self, docs):
        self.docs = list(docs)
        self.r = None

    def get_recent_reflections(self, **_kwargs):
        return []

    def get_recent_memories(self, **_kwargs):
        return []

    def search_memories(self, *_args, **_kwargs):
        return list(self.docs)

    def search_notes(self, *_args, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_insights(self, *_args, **_kwargs):
        return []


def test_rag_integration_is_opt_in_and_exposes_diagnostics():
    memory = _Memory([_doc(
        "icma2",
        "Configurazione attuale: bivite -> RAS500 -> pompa a ingranaggi.",
    )])
    previous = config.SEMANTIC_CONTEXT_ENABLED
    try:
        config.SEMANTIC_CONTEXT_ENABLED = True
        rag = build_rag_context("ricostruisci ICMA2", memory, mode="search")
        assert "Mappa semantica del contesto" in rag.text
        assert "stato=attuale" in rag.text
        assert rag.diagnostics["semantic_context"]["memory_count"] == 1
    finally:
        config.SEMANTIC_CONTEXT_ENABLED = previous


def _frame(
    *,
    clarification: bool,
    acts: list[str] | None = None,
    confidence: float = 0.96,
) -> dict:
    return {
        "status": "interpreted",
        "confidence": confidence,
        "primary_intent": "CHAT",
        "speech_acts": list(acts or ["ASK"]),
        "requires_clarification": clarification,
        "memory_disposition": "no_store",
        "memory_retrieval": {
            "needed": clarification,
            "focus": [],
            "relation": "riferimento non discriminato" if clarification else "",
            "evidence_goal": "fact" if clarification else "other",
            "confidence": confidence,
        },
        "evidence_request": {
            "dependency": "required" if clarification else "none",
            "entities": [],
            "premises": [],
            "missing_facts": ["configurazione specifica del sistema"] if clarification else [],
            "acceptable_sources": ["current_user"] if clarification else [],
            "memory_only": False,
            "confidence": 0.95,
        },
    }


def _rag() -> RagContext:
    return RagContext(
        text="Memorie narrative disponibili.",
        ids=["current", "proposal"],
        mode="chat",
        nodes=[
            {"kind": "memory", "id": "current", "content": "configurazione attuale"},
            {"kind": "memory", "id": "proposal", "content": "configurazione proposta"},
        ],
    )


def test_memory_clarification_contract_blocks_the_answer_not_the_candidates():
    previous = config.MEMORY_CLARIFICATION_ENABLED
    try:
        config.MEMORY_CLARIFICATION_ENABLED = True
        rag = apply_memory_clarification_contract(_rag(), _frame(clarification=True))
    finally:
        config.MEMORY_CLARIFICATION_ENABLED = previous

    assert "CHIARIMENTO MNEMONICO OBBLIGATORIO" in rag.text
    assert "non rispondere alla domanda sottostante" in rag.text
    assert "massimo due alternative" in rag.text
    assert rag.diagnostics["memory_clarification"]["required"] is True
    assert rag.ids == ["current", "proposal"]


def test_grounded_question_and_operational_turn_do_not_trigger_clarification():
    previous = config.MEMORY_CLARIFICATION_ENABLED
    try:
        config.MEMORY_CLARIFICATION_ENABLED = True
        grounded = apply_memory_clarification_contract(
            _rag(), _frame(clarification=False)
        )
        operational = apply_memory_clarification_contract(
            _rag(), _frame(clarification=True, acts=["ASK", "REQUEST_ACTION"])
        )
    finally:
        config.MEMORY_CLARIFICATION_ENABLED = previous

    assert "CHIARIMENTO MNEMONICO OBBLIGATORIO" not in grounded.text
    assert grounded.diagnostics["memory_clarification"]["reason"] == "not_required_by_frame"
    assert "CHIARIMENTO MNEMONICO OBBLIGATORIO" not in operational.text
    assert operational.diagnostics["memory_clarification"]["reason"] == "operational_turn"


def test_low_global_confidence_requires_structural_convergence():
    previous = config.MEMORY_CLARIFICATION_ENABLED
    coherent = _frame(clarification=True, confidence=0.0)
    incoherent = _frame(clarification=True, confidence=0.0)
    incoherent["memory_retrieval"]["needed"] = False
    try:
        config.MEMORY_CLARIFICATION_ENABLED = True
        accepted = apply_memory_clarification_contract(_rag(), coherent)
        rejected = apply_memory_clarification_contract(_rag(), incoherent)
    finally:
        config.MEMORY_CLARIFICATION_ENABLED = previous

    assert accepted.diagnostics["memory_clarification"]["required"] is True
    assert accepted.diagnostics["memory_clarification"]["confidence_basis"] == "structural_convergence"
    assert rejected.diagnostics["memory_clarification"]["required"] is False
    assert rejected.diagnostics["memory_clarification"]["reason"] == "frame_low_confidence"


def test_clarification_frame_blocks_passive_memory_without_mutating_original():
    frame = _frame(clarification=True)
    rag = _rag()
    rag.diagnostics["memory_clarification"] = {
        "required": True,
        "reason": "referential_ambiguity",
    }

    marked = with_memory_clarification_frame(frame, rag.diagnostics)

    assert "memory_clarification_required" not in frame
    assert marked["memory_clarification_required"] is True
    assert marked["passive_memory_blocked"] is True
    assert frame_blocks_passive_memory(marked) is True


def test_memory_clarification_rollback_is_byte_neutral():
    previous = config.MEMORY_CLARIFICATION_ENABLED
    rag = _rag()
    before = rag.text
    try:
        config.MEMORY_CLARIFICATION_ENABLED = False
        result = apply_memory_clarification_contract(rag, _frame(clarification=True))
    finally:
        config.MEMORY_CLARIFICATION_ENABLED = previous

    assert result.text == before
    assert result.diagnostics["memory_clarification"]["reason"] == "disabled"


def test_runtime_dispatcher_applies_the_same_clarification_contract():
    memory = _Memory([_doc(
        "icma2",
        "Configurazione attuale e modifica proposta della linea.",
    )])
    previous = config.MEMORY_CLARIFICATION_ENABLED
    try:
        config.MEMORY_CLARIFICATION_ENABLED = True
        rag = build_runtime_rag_context(
            "La pompa e' prima o dopo il filtro?",
            memory,
            turn_store=None,
            mode="search",
            dual_mode="off",
            semantic_frame=_frame(clarification=True),
        )
    finally:
        config.MEMORY_CLARIFICATION_ENABLED = previous

    assert rag.diagnostics["memory_clarification"]["required"] is True
    assert "CHIARIMENTO MNEMONICO OBBLIGATORIO" in rag.text


if __name__ == "__main__":
    test_state_classification_uses_explicit_markers_without_claiming_truth()
    test_semantic_context_is_bounded_and_preserves_provenance_and_claim()
    test_mixed_memory_remains_one_claim_and_is_not_silently_rewritten()
    test_empty_context_is_a_noop()
    test_rag_integration_is_opt_in_and_exposes_diagnostics()
    test_memory_clarification_contract_blocks_the_answer_not_the_candidates()
    test_grounded_question_and_operational_turn_do_not_trigger_clarification()
    test_low_global_confidence_requires_structural_convergence()
    test_clarification_frame_blocks_passive_memory_without_mutating_original()
    test_memory_clarification_rollback_is_byte_neutral()
    test_runtime_dispatcher_applies_the_same_clarification_contract()
    print("test_semantic_context: OK")
