#!/usr/bin/env python3
"""Regressione del percorso semantico SAVE_MEMORY della Silent Chat."""

from unittest.mock import patch

from core.intent_router import Intent
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
    assert result["reply"].startswith("Memorizzato:")
    assert memory.saved == [(
        FACT,
        {"source": "user", "idempotent": True, "final_fields": None},
    )]
    assert memory.superseded == [("old-passive", "new-user")]


if __name__ == "__main__":
    test_natural_remember_request_executes_explicit_save_with_receipt()
    print("test_silent_save: OK")
