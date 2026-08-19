#!/usr/bin/env python3
"""Regressioni per l'architettura onirica REM divergente -> risveglio lucido."""

from types import SimpleNamespace

import numpy as np

import config
from core.dream_engine import DREAM_REM_WAKE_VERSION, DreamEngine


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def set(self, key, path, value):
        if path == "$":
            self.redis.docs[key] = value
            return
        self.redis.docs.setdefault(key, {})[path.removeprefix("$.")] = value


class FakeRedis:
    def __init__(self):
        self.docs = {}
        self.expired = []
        self._json = FakeJson(self)

    def json(self):
        return self._json

    def expire(self, key, ttl):
        self.expired.append((key, ttl))


class FakeEmbedder:
    def encode(self, _content, mode=None):
        return np.array([1.0, 0.0])


def _engine():
    engine = DreamEngine.__new__(DreamEngine)
    engine._r = FakeRedis()
    engine._embedder = FakeEmbedder()
    return engine


def _memories():
    return (
        {
            "id": "mem-a",
            "content": "La workstation P620 separa i carichi fra GPU e CPU.",
            "created_at": 1000.0,
            "dream_seed_context": {
                "status": "unavailable",
                "source_turn_refs": [],
                "context_turn_refs": [],
            },
        },
        {
            "id": "mem-b",
            "content": "Il materiale riciclato varia fra un lotto e il successivo.",
            "created_at": 1001.0,
            "dream_seed_context": {
                "status": "unavailable",
                "source_turn_refs": [],
                "context_turn_refs": [],
            },
        },
    )


def _reply(content, thinking=""):
    return SimpleNamespace(message=SimpleNamespace(content=content, thinking=thinking))


def test_rem_is_free_but_only_wake_can_create_an_insight():
    engine = _engine()
    mem_a, mem_b = _memories()
    raw_dream = (
        "La P620 diventa un polmone a due camere. Una GPU mastica lotti neri, "
        "l'altra sogna granuli trasparenti; ogni variazione di viscosita' cambia "
        "la direzione del vento dentro il case."
    )
    wake = (
        "Nel dominio [hardware] succede: la P620 separa i carichi fra GPU e CPU.\n"
        "Nel dominio [materiali] succede: il riciclato varia fra lotti.\n"
        "La connessione operativa non ovvia è: isolare i carichi di analisi per "
        "confrontare i lotti senza introdurre contesa nella misura."
    )
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return _reply(raw_dream, "associazioni libere") if len(calls) == 1 else _reply(wake)

    engine._ollama_chat = chat
    result = engine._generate_rem_wake_dream(
        "hardware", mem_a, "materiali", mem_b
    )

    assert len(calls) == 2
    assert "fase REM divergente" in calls[0]["messages"][0]["content"]
    assert "Puoi violare" in calls[0]["messages"][0]["content"]
    assert calls[0]["options"]["temperature"] > 0.6
    assert calls[0]["think"] is config.DREAM_REM_THINK
    assert calls[0]["options"]["num_predict"] == config.DREAM_REM_NUM_PREDICT
    wake_prompt = calls[1]["messages"][0]["content"]
    assert raw_dream in wake_prompt
    assert "non e' una memoria" in wake_prompt
    assert "rispondi ESATTAMENTE" in wake_prompt
    assert calls[1]["think"] is config.DREAM_WAKE_THINK
    assert calls[1]["options"]["num_predict"] == config.DREAM_WAKE_NUM_PREDICT

    dreams = [doc for key, doc in engine._r.docs.items() if key.startswith("euri:dream:")]
    insights = [doc for key, doc in engine._r.docs.items() if key.startswith("euri:insight:")]
    assert len(dreams) == 2
    assert len(insights) == 1

    rem = next(doc for doc in dreams if doc.get("stage") == "rem_divergent")
    lucid = next(doc for doc in dreams if doc.get("stage") == "wake_interpretation")
    insight = insights[0]
    assert rem["content"] == raw_dream
    assert rem["status"] == "raw"
    assert rem["eligible_for_insight"] is False
    assert rem["eligible_for_rag"] is False
    assert rem["eligible_for_memory"] is False
    assert "embedding" not in rem
    assert rem["interpretation_status"] == "candidate"
    assert rem["wake_dream_id"] == lucid["id"]
    assert rem["wake_insight_id"] == insight["id"]
    assert lucid["rem_dream_id"] == rem["id"]
    assert insight["rem_dream_id"] == rem["id"]
    assert insight["origin_stage"] == "wake_interpretation"
    assert insight["architecture_version"] == DREAM_REM_WAKE_VERSION
    assert result["id"] == lucid["id"]


def test_wake_can_discard_without_erasing_the_raw_dream():
    engine = _engine()
    mem_a, mem_b = _memories()
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _reply("Una vite diventa una corrente marina dentro un granulo.")
        return _reply("NESSUN INSIGHT")

    engine._ollama_chat = chat
    result = engine._generate_rem_wake_dream(
        "hardware", mem_a, "materiali", mem_b
    )

    assert result["stage"] == "wake_interpretation"
    assert result["status"] == "discarded"
    dreams = [doc for key, doc in engine._r.docs.items() if key.startswith("euri:dream:")]
    insights = [key for key in engine._r.docs if key.startswith("euri:insight:")]
    rem = next(doc for doc in dreams if doc.get("stage") == "rem_divergent")
    assert len(dreams) == 2
    assert insights == []
    assert rem["content"].startswith("Una vite")
    assert rem["interpretation_status"] == "discarded"
    assert "wake_insight_id" not in rem


def test_empty_rem_never_runs_the_wake_or_creates_an_insight():
    engine = _engine()
    mem_a, mem_b = _memories()
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        return _reply("<think>solo ragionamento nascosto</think>")

    engine._ollama_chat = chat
    result = engine._generate_rem_wake_dream(
        "hardware", mem_a, "materiali", mem_b
    )

    assert len(calls) == 1
    assert result["stage"] == "rem_divergent"
    assert result["status"] == "discarded"
    assert result["interpretation_status"] == "not_generated"
    assert not any(key.startswith("euri:insight:") for key in engine._r.docs)


if __name__ == "__main__":
    test_rem_is_free_but_only_wake_can_create_an_insight()
    test_wake_can_discard_without_erasing_the_raw_dream()
    test_empty_rem_never_runs_the_wake_or_creates_an_insight()
    print("test_dream_rem_wake: OK")
