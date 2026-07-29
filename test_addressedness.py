#!/usr/bin/env python3
import json

from core.addressedness import classify_adaptive_followup, recent_dialogue_text


class Message:
    def __init__(self, content):
        self.content = content


class Response:
    def __init__(self, payload):
        self.message = Message(json.dumps(payload))


def fake(payload):
    return lambda **_kwargs: Response(payload)


def test_direct_followup_is_accepted_only_above_threshold():
    result = classify_adaptive_followup(
        "In che modo però questo potrebbe aiutarci?",
        "Euri: I segnali possono rendere la conversazione più naturale.",
        chat_fn=fake({
            "addressed": True,
            "relation": "direct_followup",
            "confidence": 0.96,
        }),
        min_confidence=0.90,
    )
    assert result["accepted"] is True

    low = classify_adaptive_followup(
        "Forse funziona.",
        "Euri: I segnali possono rendere la conversazione più naturale.",
        chat_fn=fake({
            "addressed": True,
            "relation": "direct_followup",
            "confidence": 0.71,
        }),
        min_confidence=0.90,
    )
    assert low["accepted"] is False


def test_same_topic_or_ambient_never_passes():
    result = classify_adaptive_followup(
        "Mi sa che carica l'87.",
        "Euri: La workstation sta funzionando.",
        chat_fn=fake({
            "addressed": True,
            "relation": "ambient",
            "confidence": 0.99,
        }),
        min_confidence=0.90,
    )
    assert result["accepted"] is False


def test_invalid_output_fails_closed():
    class Broken:
        message = Message("non-json")

    result = classify_adaptive_followup(
        "E come la trovi?",
        "Euri: Ti ho appena spiegato la proposta.",
        chat_fn=lambda **_kwargs: Broken(),
    )
    assert result["accepted"] is False
    assert result["reason"] == "classifier_error"


def test_recent_dialogue_contains_only_roles_and_text():
    text = recent_dialogue_text([
        {"role": "system", "content": "SEGRETO RAG"},
        {"role": "user", "content": "Domanda"},
        {"role": "assistant", "content": "Risposta"},
    ])
    assert "SEGRETO RAG" not in text
    assert "Domanda" in text
    assert "Risposta" in text


if __name__ == "__main__":
    tests = [globals()[name] for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"test_addressedness: OK ({len(tests)} casi)")
