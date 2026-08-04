#!/usr/bin/env python3
"""Regressioni pure per il pruning budgetato e durevole del Loop 2d."""

import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import config
import core.brain as brain_module
import core.dream_engine as dream_engine_module
from core.brain import Brain
from core.dream_engine import DreamEngine
from core.memory_manager import MemoryManager


FIXED_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, _path="$"):
        doc = self.redis.docs.get(str(key))
        return [doc] if doc is not None else None

    def set(self, key, path, value):
        key = str(key)
        if path == "$":
            self.redis.docs[key] = deepcopy(value)
        else:
            field = path.removeprefix("$.")
            self.redis.docs[key][field] = deepcopy(value)
        return True

    def numincrby(self, key, path, amount):
        field = path.removeprefix("$.")
        doc = self.redis.docs[str(key)]
        doc[field] = float(doc.get(field) or 0) + amount
        return doc[field]


class FakeRedis:
    def __init__(self, docs):
        self.docs = {key: deepcopy(doc) for key, doc in docs.items()}
        self.expiries = {}
        self.deleted = []
        self._json = FakeJson(self)

    def json(self):
        return self._json

    def pipeline(self, transaction=True):
        return FakePipeline(self, transaction=transaction)

    def get(self, _key):
        return None

    def scan_iter(self, pattern):
        prefix = pattern.rstrip("*")
        return iter(sorted(key for key in self.docs if key.startswith(prefix)))

    def expireat(self, key, when):
        if isinstance(when, datetime):
            when = when.timestamp()
        self.expiries[str(key)] = int(when)
        return True

    def delete(self, key):
        key = str(key)
        self.docs.pop(key, None)
        self.deleted.append(key)
        return 1

    def zadd(self, *_args, **_kwargs):
        return 1

    def zrem(self, *_args, **_kwargs):
        return 1


class FakePipeline:
    def __init__(self, redis, transaction=True):
        self.redis = redis
        self.transaction = transaction
        self.commands = []

    def json(self):
        return self

    def set(self, key, path, value):
        self.commands.append(("json_set", key, path, deepcopy(value)))
        return self

    def expireat(self, key, when):
        self.commands.append(("expireat", key, when))
        return self

    def execute(self):
        for command in self.commands:
            if command[0] == "json_set":
                _, key, path, value = command
                self.redis.json().set(key, path, value)
            else:
                _, key, when = command
                self.redis.expireat(key, when)
        return [True] * len(self.commands)


class FakeBrain:
    def __init__(self, verdict="KEEP"):
        self.verdict = verdict
        self.calls = []

    def evaluate_memory_relevance(self, doc):
        self.calls.append(deepcopy(doc))
        return self.verdict


_MISSING = object()


def _patch_config(**values):
    old = {}
    for name, value in values.items():
        old[name] = getattr(config, name, _MISSING)
        setattr(config, name, value)
    return old


def _restore_config(old):
    for name, value in old.items():
        if value is _MISSING:
            delattr(config, name)
        else:
            setattr(config, name, value)


def _memory(mid, *, source="passive", expires_in_days=1, recalled=0, **extra):
    doc = {
        "id": mid,
        "content": f"memoria {mid}",
        "source": source,
        "recalled_count": recalled,
        "expires_at": (FIXED_NOW + timedelta(days=expires_in_days)).timestamp(),
    }
    doc.update(extra)
    return doc


def _run_pruning(redis, brain, *, monotonic_values=None, **config_overrides):
    old_now = dream_engine_module.now
    old_time = dream_engine_module.time
    values = {
        "MEMORY_KEEP_IF_RECALLED": 3,
        "MEMORY_PRUNING_MAX_LLM_CALLS_PER_CYCLE": 16,
        "MEMORY_PRUNING_LLM_TIME_BUDGET_S": 60,
        "MEMORY_PRUNING_KEEP_MIN_DAYS": 30,
        "MEMORY_PRUNING_REVIEW_LEASE_MIN_DAYS": 30,
        "DREAM_MAINTENANCE_CYCLE_INTERVAL_S": 86400,
    }
    values.update(config_overrides)
    old_config = _patch_config(**values)
    try:
        dream_engine_module.now = lambda: FIXED_NOW
        if monotonic_values is not None:
            values_iter = iter(monotonic_values)
            dream_engine_module.time = SimpleNamespace(
                time=old_time.time,
                monotonic=lambda: next(values_iter),
            )
        DreamEngine(redis, embedder=None, brain=brain)._pruning_pass()
    finally:
        dream_engine_module.now = old_now
        dream_engine_module.time = old_time
        _restore_config(old_config)


def test_llm_budget_defers_overflow_with_durable_lease():
    docs = {
        f"euri:memory:m{i}": _memory(f"m{i}", expires_in_days=i + 1)
        for i in range(5)
    }
    redis = FakeRedis(docs)
    brain = FakeBrain("KEEP")

    _run_pruning(redis, brain, MEMORY_PRUNING_MAX_LLM_CALLS_PER_CYCLE=2)

    assert [doc["id"] for doc in brain.calls] == ["m0", "m1"]
    floor = (FIXED_NOW + timedelta(days=30)).timestamp()
    for index in range(2):
        doc = redis.docs[f"euri:memory:m{index}"]
        assert doc["pruning_review_pending"] is False
        assert doc["pruning_last_verdict"] == "KEEP"
        assert doc["expires_at"] >= floor
    for index in range(2, 5):
        doc = redis.docs[f"euri:memory:m{index}"]
        assert doc["pruning_review_pending"] is True
        assert doc["pruning_review_after"] > FIXED_NOW.timestamp()
        assert doc["pruning_original_expires_at"] == docs[
            f"euri:memory:m{index}"
        ]["expires_at"]
        assert doc["expires_at"] >= floor


def test_pending_backlog_is_processed_outside_near_expiry_window():
    key = "euri:memory:pending"
    original_expiry = (FIXED_NOW + timedelta(days=1)).timestamp()
    redis = FakeRedis(
        {
            key: _memory(
                "pending",
                expires_in_days=30,
                pruning_review_pending=True,
                pruning_review_after=(FIXED_NOW - timedelta(seconds=1)).timestamp(),
                pruning_original_expires_at=original_expiry,
            )
        }
    )
    brain = FakeBrain("DROP")

    _run_pruning(redis, brain)

    assert [doc["id"] for doc in brain.calls] == ["pending"]
    assert key not in redis.docs
    assert redis.deleted == [key]


def test_time_budget_defers_after_completed_call():
    docs = {
        f"euri:memory:t{i}": _memory(f"t{i}", expires_in_days=i + 1)
        for i in range(3)
    }
    redis = FakeRedis(docs)
    brain = FakeBrain("KEEP")

    _run_pruning(
        redis,
        brain,
        MEMORY_PRUNING_LLM_TIME_BUDGET_S=1,
        monotonic_values=[0.0, 0.0, 2.0, 2.0],
    )

    assert [doc["id"] for doc in brain.calls] == ["t0"]
    assert redis.docs["euri:memory:t1"]["pruning_review_pending"] is True
    assert redis.docs["euri:memory:t2"]["pruning_review_pending"] is True


def test_episode_keep_uses_floor_and_judge_receives_real_metadata():
    key = "euri:memory:episode"
    redis = FakeRedis(
        {
            key: _memory(
                "episode",
                source="episode",
                recalled=1,
                supported_use_count=2,
            )
        }
    )
    brain = FakeBrain("KEEP")

    _run_pruning(redis, brain)

    assert len(brain.calls) == 1
    assert brain.calls[0]["recalled_count"] == 1
    assert brain.calls[0]["supported_use_count"] == 2
    assert redis.docs[key]["expires_at"] >= (
        FIXED_NOW + timedelta(days=30)
    ).timestamp()
    assert redis.docs[key]["pruning_last_recalled_count"] == 1


def test_recalled_pending_memory_is_extended_without_llm():
    key = "euri:memory:recalled"
    redis = FakeRedis(
        {
            key: _memory(
                "recalled",
                source="episode",
                expires_in_days=30,
                recalled=3,
                pruning_review_pending=True,
                pruning_review_after=(FIXED_NOW + timedelta(days=1)).timestamp(),
                pruning_original_expires_at=(FIXED_NOW + timedelta(days=1)).timestamp(),
            )
        }
    )
    brain = FakeBrain("DROP")

    _run_pruning(redis, brain)

    assert brain.calls == []
    assert redis.docs[key]["pruning_review_pending"] is False
    assert redis.docs[key]["pruning_last_verdict"] == "EXTEND_RECALLED"
    assert redis.docs[key]["expires_at"] >= (
        FIXED_NOW + timedelta(days=30)
    ).timestamp()


def test_touch_does_not_shorten_pending_review_lease():
    key = "euri:memory:touched"
    lease_expiry = (FIXED_NOW + timedelta(days=30)).timestamp()
    doc = _memory(
        "touched",
        source="episode",
        expires_in_days=30,
        pruning_review_pending=True,
    )
    redis = FakeRedis({key: doc})
    manager = MemoryManager.__new__(MemoryManager)
    manager.r = redis
    old_now = dream_engine_module.now
    import core.memory_manager as memory_manager_module

    old_manager_now = memory_manager_module.now
    try:
        dream_engine_module.now = lambda: FIXED_NOW
        memory_manager_module.now = lambda: FIXED_NOW
        manager._touch_memories([doc])
    finally:
        dream_engine_module.now = old_now
        memory_manager_module.now = old_manager_now

    assert redis.docs[key]["expires_at"] == lease_expiry
    assert redis.expiries[key] == int(lease_expiry)
    assert redis.docs[key]["recalled_count"] == 1


def test_relevance_prompt_is_informed_and_ambiguous_output_keeps():
    prompts = []
    old_chat_client = brain_module.chat_client

    class FakeChatClient:
        def chat(self, **kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return SimpleNamespace(message=SimpleNamespace(content="MAYBE"))

    try:
        brain_module.chat_client = FakeChatClient()
        brain = Brain.__new__(Brain)
        verdict = brain.evaluate_memory_relevance(
            {
                "content": "Un episodio tecnico utile",
                "source": "episode",
                "memory_kind": "conversation_episode",
                "recalled_count": 2,
                "supported_use_count": 4,
            }
        )
    finally:
        brain_module.chat_client = old_chat_client

    assert verdict == "KEEP"
    assert "sorgente=episode" in prompts[0]
    assert "richiami_cognitivi=2" in prompts[0]
    assert "usi_sostenuti_non_provati=4" in prompts[0]
    assert "non è mai stata richiamata" not in prompts[0]
    assert Brain._parse_memory_relevance_verdict("DROP") == "DROP"
    assert Brain._parse_memory_relevance_verdict("DROP perche' vecchia") == "KEEP"


def test_ambiguous_brain_verdict_cannot_delete():
    key = "euri:memory:ambiguous"
    redis = FakeRedis({key: _memory("ambiguous")})
    brain = FakeBrain("MAYBE")

    _run_pruning(redis, brain)

    assert key in redis.docs
    assert redis.docs[key]["pruning_last_verdict"] == "KEEP"
    assert redis.deleted == []


if __name__ == "__main__":
    test_llm_budget_defers_overflow_with_durable_lease()
    test_pending_backlog_is_processed_outside_near_expiry_window()
    test_time_budget_defers_after_completed_call()
    test_episode_keep_uses_floor_and_judge_receives_real_metadata()
    test_recalled_pending_memory_is_extended_without_llm()
    test_touch_does_not_shorten_pending_review_lease()
    test_relevance_prompt_is_informed_and_ambiguous_output_keeps()
    test_ambiguous_brain_verdict_cannot_delete()
    print("test_loop2d_pruning: OK")
