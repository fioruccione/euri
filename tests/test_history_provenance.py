#!/usr/bin/env python3
"""Regression for per-turn provenance and compression-safe passive journal."""

from unittest.mock import patch

from core.brain import Brain


class _Message:
    def __init__(self, content):
        self.content = content


class _Response:
    def __init__(self, content):
        self.message = _Message(content)


def test_trusted_is_local_to_one_turn():
    brain = Brain()
    with patch("core.brain.chat_client.chat", side_effect=[_Response("r1"), _Response("r2")]):
        brain.respond("voice", trusted=True)
        brain.respond("mobile")

    rows = brain.passive_messages_after(0)
    assert [row["seq"] for row in rows] == [1, 2, 3, 4]
    assert [row["trusted"] for row in rows] == [True, True, False, False]
    assert not hasattr(brain, "_next_trusted")


def test_compression_does_not_invalidate_passive_journal():
    brain = Brain()
    for idx in range(20):
        brain.inject_tool_result(f"u{idx}", f"a{idx}")

    before = brain.passive_messages_after(0)
    assert len(before) == 40

    # Simulate the same truncation performed by episodic compression.
    with brain.history_lock:
        brain._conversation_history = brain._conversation_history[20:]

    after = brain.passive_messages_after(0)
    assert len(after) == 40
    assert [row["seq"] for row in after] == list(range(1, 41))

    brain.ack_passive_messages(30)
    remaining = brain.passive_messages_after(30)
    assert [row["seq"] for row in remaining] == list(range(31, 41))

    brain.inject_tool_result("new-user", "new-assistant")
    assert [row["seq"] for row in brain.passive_messages_after(40)] == [41, 42]


def test_selective_thinking_retries_direct_on_failure():
    brain = Brain()
    with patch(
        "core.brain.chat_client.chat",
        side_effect=[RuntimeError("thinking unavailable"), _Response("fallback")],
    ) as chat:
        reply = brain.respond(
            "Ricordi il valore?",
            thinking=True,
            thinking_reason="promoted_verbatim",
        )

    assert reply == "fallback"
    assert chat.call_count == 2
    assert chat.call_args_list[0].kwargs["think"] is True
    assert chat.call_args_list[0].kwargs["options"]["num_predict"] == 2000
    assert chat.call_args_list[1].kwargs["think"] is False
    assert chat.call_args_list[1].kwargs["options"]["num_predict"] == 1500


def test_history_is_presented_as_autobiography_not_uniform_evidence():
    brain = Brain()
    brain.inject_tool_result(
        "Eurostampi costruisce stampi e fa prove di stampaggio.",
        "Avevo interpretato Eurostampi come produzione massiva e standardizzata.",
    )

    with patch(
        "core.brain.chat_client.chat", return_value=_Response("risposta")
    ) as chat:
        brain.respond("Che cosa ricordi di Eurostampi?")

    messages = chat.call_args.kwargs["messages"]
    contract = next(
        message["content"]
        for message in messages
        if message["role"] == "system"
        and message["content"].startswith("Contratto autobiografico")
    )
    assert "non provano da soli un fatto sul mondo" in contract
    assert "continuita', personalita' e autocritica" in contract
    assert "interpretazione precedente non confermata" in contract
    assert any(
        message["role"] == "assistant"
        and "produzione massiva e standardizzata" in message["content"]
        for message in messages
    )
    assert any(
        message["role"] == "user"
        and "costruisce stampi" in message["content"]
        for message in messages
    )


if __name__ == "__main__":
    test_trusted_is_local_to_one_turn()
    test_compression_does_not_invalidate_passive_journal()
    test_selective_thinking_retries_direct_on_failure()
    test_history_is_presented_as_autobiography_not_uniform_evidence()
    print("test_history_provenance: OK")
