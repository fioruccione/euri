#!/usr/bin/env python3
"""Regression for atomic idempotency mapping plus RedisJSON commit."""

import json
from unittest.mock import patch

from core.memory_manager import MemoryManager


class FakeRedis:
    def __init__(self):
        self.strings = {}
        self.docs = {}
        self.fail_commit = False
        self.expirations = {}
        self.hashes = {}
        self.zsets = {}

    def eval(self, _script, numkeys, *args):
        assert numkeys == 4
        idem_key, memory_key, outbox_key, pending_key = args[:4]
        memory_id, raw_doc, prefix, enqueued_at = args[4:]
        existing = self.strings.get(idem_key)
        if existing and prefix + existing in self.docs:
            return [existing, "0"]
        if self.fail_commit:
            raise RuntimeError("simulated JSON.SET failure")
        self.docs[memory_key] = json.loads(raw_doc)
        self.strings[idem_key] = memory_id
        self.hashes[outbox_key] = {
            "memory_key": memory_key,
            "memory_id": memory_id,
            "enqueued_at": enqueued_at,
            "attempts": "0",
        }
        self.zsets.setdefault(pending_key, {})[outbox_key] = float(enqueued_at)
        return [memory_id, "1"]

    def zrem(self, *_args):
        return 0

    def expireat(self, key, when):
        self.expirations[key] = when
        return True


def _manager(redis):
    return MemoryManager(redis, embedder=None)


def _save(manager, content="Stefano usa un journal sequenziale per la history."):
    with (
        patch("core.memory_manager.assign_domain", return_value="test"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        return manager.save_memory(content, source="user", idempotent=True)


def test_failed_commit_leaves_no_phantom_winner():
    redis = FakeRedis()
    manager = _manager(redis)
    idem_key = manager._idempotency_key(
        "Stefano usa un journal sequenziale per la history.", "user"
    )
    redis.fail_commit = True
    try:
        _save(manager)
        raise AssertionError("commit failure should propagate")
    except RuntimeError as exc:
        assert "JSON.SET" in str(exc)
    assert idem_key not in redis.strings
    assert redis.docs == {}
    assert redis.hashes == {}

    redis.fail_commit = False
    winner = _save(manager)
    assert redis.strings[idem_key] == winner
    assert f"euri:memory:{winner}" in redis.docs
    assert f"euri:outbox:memory:{winner}" in redis.hashes


def test_document_build_failure_never_reserves_winner():
    redis = FakeRedis()
    manager = _manager(redis)
    with (
        patch("core.memory_manager.assign_domain", side_effect=RuntimeError("domain failure")),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        try:
            manager.save_memory("Stefano verifica il commit prima del mapping.", source="user", idempotent=True)
            raise AssertionError("document build failure should propagate")
        except RuntimeError as exc:
            assert "domain failure" in str(exc)
    assert redis.strings == {}
    assert redis.docs == {}
    assert redis.hashes == {}


def test_duplicate_returns_only_existing_document():
    redis = FakeRedis()
    manager = _manager(redis)
    first = _save(manager)
    second = _save(manager)
    assert second == first
    assert list(redis.docs) == [f"euri:memory:{first}"]


def test_stale_mapping_is_replaced():
    redis = FakeRedis()
    manager = _manager(redis)
    content = "Stefano usa un journal sequenziale per la history."
    idem_key = manager._idempotency_key(content, "user")
    redis.strings[idem_key] = "missing-memory"

    winner = _save(manager, content)
    assert winner != "missing-memory"
    assert redis.strings[idem_key] == winner
    assert f"euri:memory:{winner}" in redis.docs


if __name__ == "__main__":
    test_failed_commit_leaves_no_phantom_winner()
    test_document_build_failure_never_reserves_winner()
    test_duplicate_returns_only_existing_document()
    test_stale_mapping_is_replaced()
    print("test_memory_idempotency: OK")
