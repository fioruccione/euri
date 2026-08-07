#!/usr/bin/env python3
"""Il timeout Dream deve vivere sul trasporto HTTP, non su un thread non cancellabile."""

from unittest.mock import patch

import httpx

import config
from core.dream_engine import DreamEngine
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


if __name__ == "__main__":
    test_dream_wrapper_uses_bounded_transport_client()
    test_timeout_client_is_created_centrally_with_http_limit()
    print("test_dream_timeout: OK")
