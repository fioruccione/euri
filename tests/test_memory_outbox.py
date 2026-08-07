#!/usr/bin/env python3
"""Regressioni per replay e idempotenza degli effetti memoria."""

import time
from unittest.mock import patch

from core.memory_outbox import (
    MEMORY_OUTBOX_PENDING,
    memory_outbox_key,
    process_memory_outbox_event,
)
from core.memory_attention import LOOP2E_ZSET
from core.pulse import pulse_emit


class FakeJSON:
    def __init__(self, docs):
        self.docs = docs

    def get(self, key, _path="$"):
        doc = self.docs.get(key)
        return [doc] if doc is not None else None


class FakeRedis:
    def __init__(self, doc):
        mid = doc["id"]
        memory_key = f"euri:memory:{mid}"
        event_key = memory_outbox_key(mid)
        self.docs = {memory_key: doc}
        self.hashes = {
            event_key: {
                "memory_key": memory_key,
                "memory_id": mid,
                "attempts": "0",
            }
        }
        self.zsets = {MEMORY_OUTBOX_PENDING: {event_key: time.time()}}
        self.expirations = {}
        self.xevents = []

    def json(self):
        return FakeJSON(self.docs)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hincrby(self, key, field, amount):
        value = int(self.hashes[key].get(field, 0)) + amount
        self.hashes[key][field] = str(value)
        return value

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def expireat(self, key, when):
        self.expirations[key] = when

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, member):
        self.zsets.setdefault(key, {}).pop(member, None)

    def delete(self, key):
        self.hashes.pop(key, None)

    def xadd(self, stream, fields, **kwargs):
        self.xevents.append((stream, fields, kwargs))


def _doc():
    return {
        "id": "m1",
        "content": "Memoria durevole.",
        "source": "user",
        "domain": "test",
        "created_at": time.time(),
        "expires_at": time.time() + 3600,
        "requires_verification": False,
        "memory_axes": {},
        "safety_flag": [],
        "recalled_count": 0,
    }


def test_partial_failure_keeps_event_and_replay_finishes_once():
    redis = FakeRedis(_doc())
    event_key = memory_outbox_key("m1")
    emitted = set()

    def emit_once(_r, event_id, **_kwargs):
        emitted.add(event_id)
        return True

    with (
        patch("core.memory_outbox.pulse_emit_once", side_effect=emit_once),
        patch("core.memory_outbox.write_memory", side_effect=[False, True]) as write,
    ):
        assert process_memory_outbox_event(redis, event_key) is False
        assert event_key in redis.hashes
        assert process_memory_outbox_event(redis, event_key) is True

    assert emitted == {"memory-saved:m1"}
    assert write.call_count == 2
    assert event_key not in redis.hashes
    assert event_key not in redis.zsets[MEMORY_OUTBOX_PENDING]
    assert "euri:memory:m1" in redis.expirations


def test_regular_pulse_emit_contract_is_unchanged():
    redis = FakeRedis(_doc())

    pulse_emit(redis, "memory", "extero", "saved", payload={"id": "m1"})

    assert len(redis.xevents) == 1
    assert redis.xevents[0][1]["sense"] == "memory"


def test_attention_index_failure_keeps_outbox_pending():
    redis = FakeRedis(_doc())
    event_key = memory_outbox_key("m1")
    original_zrem = redis.zrem

    def fail_attention(key, member):
        if key == LOOP2E_ZSET:
            raise RuntimeError("attention unavailable")
        return original_zrem(key, member)

    redis.zrem = fail_attention
    with (
        patch("core.memory_outbox.pulse_emit_once") as pulse,
        patch("core.memory_outbox.write_memory") as write,
    ):
        assert process_memory_outbox_event(redis, event_key) is False

    assert event_key in redis.hashes
    assert event_key in redis.zsets[MEMORY_OUTBOX_PENDING]
    pulse.assert_not_called()
    write.assert_not_called()


if __name__ == "__main__":
    test_partial_failure_keeps_event_and_replay_finishes_once()
    test_regular_pulse_emit_contract_is_unchanged()
    test_attention_index_failure_keeps_outbox_pending()
    print("test_memory_outbox: OK")
