#!/usr/bin/env python3
"""Regressioni pure per la policy di convergenza claim_judge_v2."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import config
import core.dream_engine as de_mod
from core.dream_engine import DreamEngine


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, path):
        doc = self.redis.docs.get(key)
        if doc is None:
            return None
        if path == "$":
            return [dict(doc)]
        field = path.removeprefix("$.")
        return [doc[field]] if field in doc else []

    def set(self, key, path, value):
        field = path.removeprefix("$.")
        self.redis.docs[key][field] = value


class FakeSearch:
    def __init__(self, redis):
        self.redis = redis

    def search(self, _query, query_params=None):
        docs = self.redis.neighbors if query_params is not None else self.redis.candidates
        return SimpleNamespace(docs=docs)


class FakeRedis:
    def __init__(self, candidates=None, neighbors=None, docs=None):
        self.candidates = candidates or []
        self.neighbors = neighbors or []
        self.docs = docs or {}
        self.cache = {}
        self.deleted = []
        self._json = FakeJson(self)

    def get(self, key):
        return self.cache.get(key)

    def setex(self, key, _ttl, value):
        self.cache[key] = value

    def json(self):
        return self._json

    def ft(self, _index):
        return FakeSearch(self)

    def exists(self, key):
        return key in self.docs

    def delete(self, key):
        self.deleted.append(key)
        self.docs.pop(key, None)


def _candidate(key, content, score=None):
    values = {
        "id": key,
        "content": content,
        "embedding": json.dumps([1.0, 0.0]),
        "convergence_count": 1,
    }
    if score is not None:
        values["score"] = score
    return SimpleNamespace(**values)


def _engine_for(subject, neighbors):
    all_docs = [subject, *neighbors]
    docs = {
        doc.id: {
            "id": doc.id,
            "content": doc.content,
            "status": "candidate",
            "convergence_count": 1,
            "source_memory_ids": [],
        }
        for doc in all_docs
    }
    redis = FakeRedis(
        candidates=[subject],
        neighbors=[subject, *neighbors],
        docs=docs,
    )
    engine = DreamEngine(redis, embedder=None)
    engine._ensure_premise_fidelity = lambda *_args, **_kwargs: False
    traces = []
    engine._trace_convergence = lambda *args, **kwargs: traces.append((args, kwargs))
    return engine, redis, traces


def _patch_config(**values):
    old = {name: getattr(config, name) for name in values}
    for name, value in values.items():
        setattr(config, name, value)
    return old


def _restore_config(old):
    for name, value in old.items():
        setattr(config, name, value)


def test_zero_distance_requires_semantic_confirmation():
    subject = _candidate("seed", "claim operativo del seed")
    neighbors = [
        _candidate("n1", "claim estraneo uno", 0.0),
        _candidate("n2", "claim estraneo due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or False

    engine._evaluate_insights()

    assert len(calls) == 2
    assert redis.docs["seed"]["status"] == "candidate"
    assert redis.deleted == []
    args, meta = traces[-1]
    assert args[1] == 1                 # nessuna convergenza aggiunta
    assert args[2] == 2                 # entrambe sarebbero passate nella policy v1
    assert args[4] == "below_threshold"
    assert meta["n_judge_confirmed"] == 0


def test_only_judge_confirmed_neighbors_are_absorbed():
    subject = _candidate(
        "seed",
        "Nel dominio [alfa] succede: viene osservato un segnale operativo. "
        "Nel dominio [beta] succede: viene regolato un parametro di processo. "
        "La connessione operativa non ovvia è: usare il segnale per regolare il parametro.",
    )
    neighbors = [
        _candidate("same1", "stesso meccanismo, formulazione uno", 0.0),
        _candidate("same2", "stesso meccanismo, formulazione due", 0.0),
        _candidate("different", "meccanismo non correlato", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    engine._llm_judge_same_insight = lambda _a, b: not b.startswith("meccanismo non")

    old_pulse, old_write = de_mod.pulse_emit, de_mod.write_insight
    de_mod.pulse_emit = lambda *_args, **_kwargs: None
    de_mod.write_insight = lambda *_args, **_kwargs: None
    try:
        engine._evaluate_insights()
    finally:
        de_mod.pulse_emit, de_mod.write_insight = old_pulse, old_write

    assert redis.docs["seed"]["status"] == "promoted"
    assert set(redis.deleted) == {"same1", "same2"}
    assert "different" in redis.docs
    args, meta = traces[-1]
    assert args[1] == 3
    assert args[4] == "promoted"
    assert meta["n_judge_confirmed"] == 2


def test_pair_cache_is_symmetric_and_avoids_second_model_call():
    redis = FakeRedis()
    engine = DreamEngine(redis, embedder=None)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or True

    first = engine._cached_same_insight_judgement(
        "a", "contenuto a", "b", "contenuto b", allow_model_call=True
    )
    second = engine._cached_same_insight_judgement(
        "b", "contenuto b", "a", "contenuto a", allow_model_call=False
    )

    assert first == (True, True, False)
    assert second == (True, False, True)
    assert len(calls) == 1


def test_judge_accepts_only_exact_same_label():
    engine = DreamEngine(FakeRedis(), embedder=None)
    requests = []

    def response(label):
        return SimpleNamespace(message=SimpleNamespace(content=label))

    engine._ollama_chat = lambda **kwargs: requests.append(kwargs) or response("SAME")
    assert engine._llm_judge_same_insight("a", "b") is True
    assert requests[-1]["think"] is True
    assert requests[-1]["options"]["num_predict"] == 5000
    engine._ollama_chat = lambda **_kwargs: response("RELATED")
    assert engine._llm_judge_same_insight("a", "b") is False
    engine._ollama_chat = lambda **_kwargs: response("SAME perché sono simili")
    assert engine._llm_judge_same_insight("a", "b") is None


def test_budget_exhaustion_is_fail_closed():
    subject = _candidate("seed", "claim seed")
    neighbors = [
        _candidate("n1", "claim uno", 0.0),
        _candidate("n2", "claim due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or False
    old = _patch_config(CONVERGENCE_JUDGE_BUDGET=1)
    try:
        engine._evaluate_insights()
    finally:
        _restore_config(old)

    assert len(calls) == 1
    assert redis.docs["seed"]["status"] == "candidate"
    assert traces[-1][1]["n_judge_deferred"] == 1


def test_bridge_validity_is_observational_and_preserves_hypotheses():
    insight_key = "euri:insight:new"
    redis = FakeRedis(docs={
        insight_key: {
            "id": "new",
            "content": "Nel dominio [a] succede: A. Nel dominio [b] succede: B. "
                       "La connessione operativa non ovvia è: C.",
            "status": "candidate",
            "bridge_measurement_eligible": True,
            "source_memory_ids": ["euri:memory:a", "euri:memory:b"],
        },
        "euri:memory:a": {"content": "La memoria A descrive un parametro."},
        "euri:memory:b": {"content": "La memoria B descrive un risultato."},
    })
    engine = DreamEngine(redis, embedder=None)
    requests = []
    engine._ollama_chat = lambda **kwargs: requests.append(kwargs) or SimpleNamespace(
        message=SimpleNamespace(
            content="BRIDGE: HYPOTHESIS\nNOTE: manca una misura causale diretta"
        )
    )

    assert engine._ensure_bridge_validity(insight_key) is True
    assert redis.docs[insight_key]["bridge_validity"] == "hypothesis"
    assert redis.docs[insight_key]["bridge_validity_score"] == 0.5
    assert redis.docs[insight_key]["status"] == "candidate"
    assert requests[-1]["think"] is True
    assert requests[-1]["options"]["num_predict"] == 5000


def test_bridge_parser_rejects_explanatory_free_text():
    parse = DreamEngine._parse_bridge_validity_response
    assert parse("BRIDGE: SUPPORTED\nNOTE: segue dalle fonti") == (
        "supported", 1.0, "segue dalle fonti"
    )
    assert parse("Secondo me è una buona ipotesi") is None


if __name__ == "__main__":
    test_zero_distance_requires_semantic_confirmation()
    test_only_judge_confirmed_neighbors_are_absorbed()
    test_pair_cache_is_symmetric_and_avoids_second_model_call()
    test_judge_accepts_only_exact_same_label()
    test_budget_exhaustion_is_fail_closed()
    test_bridge_validity_is_observational_and_preserves_hypotheses()
    test_bridge_parser_rejects_explanatory_free_text()
    print("test_convergence_policy: OK")
