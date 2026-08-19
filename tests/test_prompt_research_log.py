import json

import core.prompt_research_log as research_log
from core.prompt_research_log import PromptCapture, analyze_transport_body


def test_transport_analysis_finds_identity_and_each_rag_block():
    identity = "[IDENTITA] tratto stabile owner-scoped"
    rag = "Ricordi rilevanti:\n- memoria uno\n\nConnessioni:\n- insight due"
    messages = [
        {"role": "system", "content": "sistema"},
        {"role": "system", "content": identity},
        {"role": "system", "content": "Contesto disponibile:\n" + rag},
        {"role": "user", "content": "domanda"},
    ]
    body = json.dumps(
        {
            "model": "gemma4:26b",
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.7},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    result = analyze_transport_body(
        body,
        identity_block=identity,
        rag_context=rag,
    )

    assert result["identity_block"]["present"] is True
    assert result["identity_block"]["location"]["message_index"] == 1
    assert result["rag_context"]["present"] is True
    assert len(result["rag_context"]["blocks"]) == 2
    assert result["rag_context"]["blocks"][1]["label"] == "Connessioni:"
    assert result["identity_block"]["token_offset_from_start"] is None
    assert result["compiled_prompt"]["status"] == "unavailable_ollama_renderer_internal"


def test_transport_analysis_does_not_claim_absent_identity():
    body = json.dumps({
        "model": "gemma4:26b",
        "messages": [{"role": "user", "content": "ciao"}],
        "stream": False,
    }).encode("utf-8")

    result = analyze_transport_body(body, identity_block="tratto non inviato")

    assert result["identity_block"]["present"] is False
    assert result["identity_block"]["location"] is None


def test_completion_without_captured_transport_is_not_persisted():
    capture = PromptCapture(client=object())

    def must_not_be_called():
        raise AssertionError("writer richiesto senza trasporto")

    original = research_log._writer
    research_log._writer = must_not_be_called
    try:
        capture.complete({}, latency_ms=1.0)
        capture.fail(RuntimeError("nessun trasporto"))
    finally:
        research_log._writer = original


if __name__ == "__main__":
    test_transport_analysis_finds_identity_and_each_rag_block()
    test_transport_analysis_does_not_claim_absent_identity()
    test_completion_without_captured_transport_is_not_persisted()
    print("test_prompt_research_log: OK")
