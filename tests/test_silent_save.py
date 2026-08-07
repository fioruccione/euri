#!/usr/bin/env python3
"""Regressione del percorso semantico SAVE_MEMORY della Silent Chat."""

from unittest.mock import patch

from core.intent_router import Intent, classify
from core.save_service import save_memory_command
from core.semantic_turn import arbitrate_routable_intent


PROMPT = (
    "In realtà ti avevo detto che ho rifiutato il preventivo dell'officina Jeep "
    "e porterò l'auto da un altro meccanico. Correggi la lacuna e ricordalo."
)
FACT = (
    "Stefano ha rifiutato il preventivo dell'officina Jeep e ha pianificato "
    "di portare l'auto da un altro meccanico."
)


class FakeMemory:
    def __init__(self):
        self.saved = []
        self.superseded = []

    def find_similar_memory(self, _content):
        return {
            "id": "old-passive",
            "content": FACT,
            "source": "passive",
            "passive_support": "owner_asserted",
            "similarity": 0.99,
        }

    def save_memory(self, content, **kwargs):
        self.saved.append((content, kwargs))
        return "new-user"

    def supersede_memory(self, old_id, new_id):
        self.superseded.append((old_id, new_id))
        return True


class FakeBrain:
    def resolve_save_intent(self, text, _history):
        assert text == PROMPT
        return {"mode": "direct", "memory": FACT, "confidence": 0.99}

    def confirm_save(self, _kind, content, _due_at_str=""):
        return f"Memorizzato: {content}"

    def apply_correction_to_memory(self, _existing, correction):
        return correction


class ArtifactBrain(FakeBrain):
    def summarize_artifact_for_memory(self, content, source_name=""):
        assert "NotebookLM locale" in content
        assert source_name == "clipboard"
        return (
            "Assistente Ufficio interroga soltanto i documenti caricati nella "
            "sessione e indica documento e pagina; può commettere errori."
        )


def test_natural_remember_request_executes_explicit_save_with_receipt():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "SAVE_MEMORY",
        "speech_acts": ["INFORM", "CORRECT_FACT", "REQUEST_SAVE"],
    }
    label = arbitrate_routable_intent(
        frame,
        Intent.CHAT,
        allowed={
            "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
            "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "TRANSLATE", "DICTATION",
        },
    )
    assert label == "SAVE_MEMORY"

    memory = FakeMemory()
    with patch("core.save_service.validate_payload", return_value=FACT):
        result = save_memory_command(
            PROMPT,
            memory,
            FakeBrain(),
            recent_history=[{"role": "user", "content": "Contesto precedente"}],
    )

    assert result["saved"] is True
    assert result["reply"].startswith("Ho corretto la memoria:")
    assert memory.saved == [(
        FACT,
        {"source": "user", "idempotent": True, "final_fields": None},
    )]
    assert memory.superseded == [("old-passive", "new-user")]


def test_explicit_correction_and_save_has_deterministic_save_priority():
    prompt = (
        "Correggi e memorizza che Assistente Ufficio risponde esclusivamente "
        "in base ai documenti caricati nella sessione."
    )
    # Regressione live 07/08: prima del fix la UI interrogava i tool prima di
    # SAVE_MEMORY e ingest_documents rubava questo turno.
    intent, _meta = classify(prompt)
    assert intent == Intent.SAVE_MEMORY

    misleading_frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["REQUEST_ACTION", "CORRECT_FACT", "REQUEST_SAVE"],
        "actions": [{
            "effect": "Correggere informazioni sui documenti",
            "target": "documenti",
            "capability_class": "document_ingestion",
            "effect_scope": "write",
            "polarity": "requested",
        }],
    }
    label = arbitrate_routable_intent(
        misleading_frame,
        intent,
        allowed={
            "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
            "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "TRANSLATE", "DICTATION",
        },
    )
    assert label == "SAVE_MEMORY"


def test_incidental_aggiungi_che_does_not_turn_rewrite_into_save():
    prompt = (
        "Non dire che il sistema è automaticamente conforme. Aggiungi che gli altri "
        "adempimenti sono ancora in corso e ora riscrivi il discorso."
    )
    intent, _meta = classify(prompt)
    assert intent == Intent.CHAT


def test_save_clipboard_content_uses_active_artifact_not_instruction_text():
    memory = FakeMemory()
    memory.find_similar_memory = lambda _content: None
    artifact = {
        "source": "clipboard",
        "content": "NotebookLM locale con citazioni verificabili e limiti dichiarati.",
    }

    result = save_memory_command(
        "Memorizza il contenuto della clipboard.",
        memory,
        ArtifactBrain(),
        recent_history=[{"role": "user", "content": "Analizza gli appunti"}],
        active_artifact=artifact,
    )

    assert result["saved"] is True
    assert result["artifact"] is True
    saved_content = memory.saved[0][0]
    assert "documenti caricati" in saved_content
    assert "Memorizza il contenuto" not in saved_content


def test_save_clipboard_does_not_fall_back_to_active_ui_document():
    memory = FakeMemory()
    result = save_memory_command(
        "Memorizza il contenuto degli appunti.",
        memory,
        ArtifactBrain(),
        recent_history=[{"role": "user", "content": "Ho caricato un documento"}],
        active_artifact={
            "source": "/tmp/report.pdf",
            "kind": "document",
            "filename": "report.pdf",
            "content": "NotebookLM locale: questo è il documento UI, non la clipboard.",
        },
    )

    assert result["saved"] is False
    assert result["artifact"] is True
    assert memory.saved == []


if __name__ == "__main__":
    test_natural_remember_request_executes_explicit_save_with_receipt()
    test_explicit_correction_and_save_has_deterministic_save_priority()
    test_incidental_aggiungi_che_does_not_turn_rewrite_into_save()
    test_save_clipboard_content_uses_active_artifact_not_instruction_text()
    test_save_clipboard_does_not_fall_back_to_active_ui_document()
    print("test_silent_save: OK")
