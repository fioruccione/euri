#!/usr/bin/env python3
"""Regression for atomic idempotency mapping plus RedisJSON commit."""

import json
from datetime import datetime
from unittest.mock import patch

import config
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
        if numkeys == 3:
            memory_key, outbox_key, pending_key = args[:3]
            memory_id, raw_doc, enqueued_at = args[3:]
            if self.fail_commit:
                raise RuntimeError("simulated JSON.SET failure")
            self.docs[memory_key] = json.loads(raw_doc)
            self.hashes[outbox_key] = {
                "memory_key": memory_key,
                "memory_id": memory_id,
                "enqueued_at": enqueued_at,
                "attempts": "0",
            }
            self.zsets.setdefault(pending_key, {})[outbox_key] = float(enqueued_at)
            return memory_id
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


def _save(
    manager,
    content="Stefano usa un journal sequenziale per la history.",
    *,
    final_fields=None,
):
    with (
        patch("core.memory_manager.assign_domain", return_value="test"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        return manager.save_memory(
            content,
            source="user",
            idempotent=True,
            final_fields=final_fields,
        )


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


def test_temporal_context_is_canonical_memory_metadata():
    redis = FakeRedis()
    manager = _manager(redis)
    asserted_at = 1784105760.0
    with (
        patch("core.memory_manager.assign_domain", return_value="chimica polimeri"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        mid = manager.save_memory(
            "Stefano ha riaperto il tema IZOD senza fornire risultati.",
            source="passive",
            idempotent=True,
            memory_kind="conversation_anchor",
            temporal_context={
                "asserted_at": asserted_at,
                "event_start": 1784066400.0,
                "event_end": asserted_at,
                "event_precision": "part_of_day",
                "source_turn_ids": [7, 8],
            },
        )

    doc = redis.docs[f"euri:memory:{mid}"]
    assert doc["memory_kind"] == "conversation_anchor"
    assert doc["asserted_at"] == asserted_at
    assert doc["event_start"] == 1784066400.0
    assert doc["event_end"] == asserted_at
    assert doc["temporal_context"]["source_turn_ids"] == [7, 8]
    assert doc["memory_axes"]["observed_at"] == asserted_at


def test_direct_save_materializes_elliptical_date_before_commit():
    redis = FakeRedis()
    manager = _manager(redis)
    naive = datetime(2026, 8, 18, 18, 14)
    asserted_at = (
        config.TIMEZONE.localize(naive).timestamp()
        if hasattr(config.TIMEZONE, "localize")
        else naive.replace(tzinfo=config.TIMEZONE).timestamp()
    )
    with (
        patch("core.memory_manager.assign_domain", return_value="lavoro"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        mid = manager.save_memory(
            "L'azienda resta ferma fino al 24.",
            source="user",
            idempotent=True,
            temporal_context={"asserted_at": asserted_at},
        )

    doc = redis.docs[f"euri:memory:{mid}"]
    assert doc["content"] == "L'azienda resta ferma fino al 24 agosto 2026."
    assert doc["temporal_context"]["schema_version"] == 2
    assert doc["temporal_context"]["temporal_relation"] == "until"
    assert doc["event_start"] == asserted_at
    end = datetime.fromtimestamp(doc["event_end"], tz=config.TIMEZONE)
    assert end.isoformat().startswith("2026-08-25T00:00:00")


def test_direct_save_preserves_numeric_measurement_range():
    redis = FakeRedis()
    manager = _manager(redis)
    asserted_at = 1788259744.019418
    content = (
        "Nel Progetto UBQ, la percentuale nella formulazione del PP nero "
        "grado 25 è del 7-8%."
    )
    with (
        patch("core.memory_manager.assign_domain", return_value="chimica polimeri"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        mid = manager.save_memory(
            content,
            source="user",
            idempotent=True,
            temporal_context={"asserted_at": asserted_at},
        )

    doc = redis.docs[f"euri:memory:{mid}"]
    assert doc["content"] == content
    assert doc["event_start"] is None
    assert doc["event_end"] is None
    assert doc["temporal_context"]["temporal_expression"] == ""
    assert "dated" not in doc["memory_axes"]["temporal_markers"]
    assert "2026" not in doc["memory_axes"]["entity_mentions"]


def test_final_fields_are_committed_before_outbox_visibility():
    redis = FakeRedis()
    manager = _manager(redis)
    observed = {}

    def observe_outbox(_redis, event_key):
        mid = event_key.rsplit(":", 1)[-1]
        observed.update(redis.docs[f"euri:memory:{mid}"])
        return True

    with (
        patch("core.memory_manager.assign_domain", return_value="auto"),
        patch(
            "core.memory_manager.process_memory_outbox_event",
            side_effect=observe_outbox,
        ),
    ):
        mid = manager.save_memory(
            "Una correzione esterna deve essere pubblicata già completa.",
            source="reaction",
            final_fields={
                "domain": "logica",
                "requires_verification": False,
                "reacted_to": "insight-1",
            },
        )

    assert mid
    assert observed["domain"] == "logica"
    assert observed["requires_verification"] is False
    assert observed["reacted_to"] == "insight-1"


def test_final_fields_cannot_replace_canonical_identity():
    redis = FakeRedis()
    manager = _manager(redis)
    with (
        patch("core.memory_manager.assign_domain", return_value="test"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        try:
            manager.save_memory(
                "Contenuto canonico.",
                final_fields={"content": "Riscrittura tardiva."},
            )
            raise AssertionError("final_fields non deve riscrivere il contenuto")
        except ValueError as exc:
            assert "content" in str(exc)
    assert redis.docs == {}


def test_precommit_guard_can_cancel_stale_background_publication():
    redis = FakeRedis()
    manager = _manager(redis)
    with (
        patch("core.memory_manager.assign_domain", return_value="test"),
        patch("core.memory_manager.process_memory_outbox_event", return_value=True),
    ):
        mid = manager.save_memory(
            "Ipotesi costruita su uno snapshot ormai superato.",
            source="reflection",
            precommit_guard=lambda: False,
        )
    assert mid is None
    assert redis.docs == {}
    assert redis.hashes == {}


if __name__ == "__main__":
    test_failed_commit_leaves_no_phantom_winner()
    test_document_build_failure_never_reserves_winner()
    test_duplicate_returns_only_existing_document()
    test_stale_mapping_is_replaced()
    test_temporal_context_is_canonical_memory_metadata()
    test_direct_save_materializes_elliptical_date_before_commit()
    test_direct_save_preserves_numeric_measurement_range()
    test_final_fields_are_committed_before_outbox_visibility()
    test_final_fields_cannot_replace_canonical_identity()
    test_precommit_guard_can_cancel_stale_background_publication()
    print("test_memory_idempotency: OK")
