#!/usr/bin/env python3
"""Il timeout Dream deve vivere sul trasporto HTTP, non su un thread non cancellabile."""

from unittest.mock import patch

import httpx
from ollama import ChatResponse, Message

import config
from core.dream_engine import DreamActivityInterrupted, DreamEngine
from core.ollama_client import get_dream_client


class _Redis:
    def get(self, _key):
        return None


class _TimeoutClient:
    def chat(self, **_kwargs):
        raise httpx.ReadTimeout("model stalled")


def test_dream_wrapper_uses_bounded_transport_client():
    engine = DreamEngine(_Redis(), embedder=None)
    with patch(
        "core.dream_engine.get_dream_client",
        return_value=_TimeoutClient(),
    ) as factory:
        try:
            engine._ollama_chat(
                model="test",
                messages=[{"role": "user", "content": "x"}],
                _timeout=7,
            )
            raise AssertionError("il timeout di trasporto deve propagarsi")
        except httpx.ReadTimeout:
            pass
    factory.assert_called_once_with(7)


def test_timeout_client_is_created_centrally_with_http_limit():
    get_dream_client.cache_clear()
    sentinel = object()
    with patch("core.ollama_client.ollama.Client", return_value=sentinel) as constructor:
        assert get_dream_client(12) is sentinel
        assert get_dream_client(12) is sentinel
    constructor.assert_called_once_with(
        host=config.DREAM_OLLAMA_HOST,
        timeout=12.0,
    )
    get_dream_client.cache_clear()


class _StreamingClient:
    def __init__(self, engine, *, interrupt=False):
        self.engine = engine
        self.interrupt = interrupt
        self.closed = False
        self.was_active = False

    def chat(self, **kwargs):
        assert kwargs["stream"] is True

        def chunks():
            try:
                yield ChatResponse(
                    model="test",
                    message=Message(role="assistant", thinking="ragiona ", content=""),
                )
                if self.interrupt:
                    self.was_active = self.engine.notify_activity()
                yield ChatResponse(
                    model="test",
                    done=True,
                    done_reason="stop",
                    eval_count=2,
                    message=Message(role="assistant", thinking="bene", content="OK"),
                )
            finally:
                self.closed = True

        return chunks()


def test_dream_stream_is_reassembled_as_the_legacy_chat_response():
    engine = DreamEngine(_Redis(), embedder=None)
    client = _StreamingClient(engine)
    with patch("core.dream_engine.get_dream_client", return_value=client):
        response = engine._ollama_chat(
            model="test",
            messages=[{"role": "user", "content": "x"}],
            _timeout=7,
        )

    assert response.message.thinking == "ragiona bene"
    assert response.message.content == "OK"
    assert response.done_reason == "stop"
    assert client.closed is True
    assert not engine.is_llm_active()


def test_foreground_activity_closes_the_active_dream_stream():
    engine = DreamEngine(_Redis(), embedder=None)
    client = _StreamingClient(engine, interrupt=True)
    with patch("core.dream_engine.get_dream_client", return_value=client):
        try:
            engine._ollama_chat(
                model="test",
                messages=[{"role": "user", "content": "x"}],
                _timeout=7,
                _label="interrupt-test",
            )
            raise AssertionError("la voce deve interrompere lo stream Dream")
        except DreamActivityInterrupted:
            pass

    assert client.was_active is True
    assert client.closed is True
    assert not engine.is_llm_active()


if __name__ == "__main__":
    test_dream_wrapper_uses_bounded_transport_client()
    test_timeout_client_is_created_centrally_with_http_limit()
    test_dream_stream_is_reassembled_as_the_legacy_chat_response()
    test_foreground_activity_closes_the_active_dream_stream()
    print("test_dream_timeout: OK")
