#!/usr/bin/env python3
"""Il realtime Gemma deve usare un solo runner Ollama a contesto stabile."""

from unittest.mock import patch

import config
from core.ollama_client import RealtimeClient


def _client_without_transport() -> RealtimeClient:
    # Evita di costruire socket/httpx: i test esercitano soltanto la policy
    # prima della delega al metodo SDK.
    return object.__new__(RealtimeClient)


def test_realtime_context_is_added_when_missing():
    client = _client_without_transport()
    with patch("ollama.Client.chat", return_value="ok") as send:
        result = client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": "x"}],
            options={"temperature": 0},
        )
    assert result == "ok"
    assert send.call_args.kwargs["options"] == {
        "temperature": 0,
        "num_ctx": config.CHAT_OLLAMA_NUM_CTX,
    }


def test_realtime_context_overrides_divergent_call_site_without_mutation():
    client = _client_without_transport()
    original = {"temperature": 0, "num_ctx": 4096}
    with patch("ollama.Client.chat", return_value="ok") as send:
        client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": "x"}],
            options=original,
        )
    assert original["num_ctx"] == 4096
    assert send.call_args.kwargs["options"]["num_ctx"] == config.CHAT_OLLAMA_NUM_CTX


def test_non_realtime_model_keeps_its_own_context():
    client = _client_without_transport()
    with patch("ollama.Client.chat", return_value="ok") as send:
        client.chat(
            model="modello-esperimento",
            messages=[{"role": "user", "content": "x"}],
            options={"num_ctx": 4096},
        )
    assert send.call_args.kwargs["options"]["num_ctx"] == 4096


if __name__ == "__main__":
    test_realtime_context_is_added_when_missing()
    test_realtime_context_overrides_divergent_call_site_without_mutation()
    test_non_realtime_model_keeps_its_own_context()
    print("test_ollama_context_policy: OK")
