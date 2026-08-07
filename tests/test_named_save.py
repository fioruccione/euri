#!/usr/bin/env python3
"""Regression del comando nominato: contenuto dalla conversazione, nome come metadato."""

from core.intent_router import Intent, classify
from core.save_service import extract_named_save, save_memory_command


class FakeMemory:
    def __init__(self):
        self.saved = []

    def find_similar_memory(self, _content):
        return None

    def save_memory(self, content, **kwargs):
        self.saved.append((content, kwargs))
        return "new-id"


class FakeBrain:
    def resolve_save_intent(self, *_args):
        return {}

    def extract_fact_from_exchange(self, user, assistant):
        return f"Fatto emerso: {user}"

    def confirm_save(self, _kind, content, _due_at_str=""):
        return f"Memorizzato: {content}"


def test_named_command_is_save_memory_and_extracts_title():
    text = "questi informazioni con il nome Compuand UBQ 2026"
    intent, _meta = classify(text)
    assert intent == Intent.SAVE_MEMORY
    assert extract_named_save(text) == (
        "questi informazioni", "Compuand UBQ 2026"
    )
    assert extract_named_save(
        "Memorizza questo con il nome Compound UBQ 2026 Risultati Finali."
    ) == ("Memorizza questo", "Compound UBQ 2026 Risultati Finali")


def test_named_command_saves_previous_fact_and_title_metadata():
    memory = FakeMemory()
    result = save_memory_command(
        "questi informazioni con il nome Compuand UBQ 2026",
        memory,
        FakeBrain(),
        prev_user_text="Il campione UBQ è stato provato in trafila.",
        prev_assistant_text="Attendo le prove meccaniche.",
        fresh=True,
        recent_history=[],
    )
    assert result["saved"] is True
    assert result["memory_title"] == "Compuand UBQ 2026"
    assert memory.saved[0][0] == "Fatto emerso: Il campione UBQ è stato provato in trafila."
    assert memory.saved[0][1]["final_fields"] == {
        "memory_title": "Compuand UBQ 2026"
    }


if __name__ == "__main__":
    test_named_command_is_save_memory_and_extracts_title()
    test_named_command_saves_previous_fact_and_title_metadata()
    print("test_named_save: OK")
