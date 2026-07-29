#!/usr/bin/env python3
"""Regressioni pure: aggregazione shadow, scadenza e promotion explain."""
from __future__ import annotations

import json

from core.memory_utility_shadow import (
    UTILITY_REPORT_KEY,
    UTILITY_REVIEW_PENDING_KEY,
    UTILITY_STATE_KEY,
    aggregate_lineage_events,
    build_memory_utility_report,
    explain_insight_promotion,
    migrate_legacy_utility_shadow_keys,
    run_memory_utility_shadow_maintenance,
    sync_supported_use_metadata,
)
from utils.redis_client import backfill_embeddings


def _event(event_id, sense, kind, *, entity=None, payload=None):
    return event_id, {
        "producer": "response_lineage",
        "experiment_version": "response_lineage_shadow_v1",
        "sense": sense,
        "kind": kind,
        "entity_refs": json.dumps([entity] if entity else []),
        "payload": json.dumps(payload or {}),
        "ts": "1000.0",
    }


class FakeRedis:
    def __init__(self, rows=None):
        self.values = {}
        self.docs = {
            "euri:memory:m1": {
                "id": "m1",
                "source": "user",
                "recalled_count": 3,
                "last_recalled_at": 1000.0,
                "embedding": [0.1],
                "memory_axes": {"subject_status": "explicit"},
            }
        }
        self.zsets = {}
        self.rows = list(rows or [])
        self.events = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def xrange(self, _stream, min="-", max="+"):
        cursor = min.removeprefix("(")
        return [
            row for row in self.rows
            if tuple(map(int, row[0].split("-"))) > tuple(map(int, cursor.split("-")))
        ]

    def xadd(self, stream, fields, **_kwargs):
        self.events.append((stream, fields))
        return "99-0"

    class _Json:
        def __init__(self, outer):
            self.outer = outer

        def get(self, key, path):
            doc = self.outer.docs.get(key)
            if doc is None:
                return None
            if path == "$":
                return [dict(doc)]
            field = path.removeprefix("$.")
            return [doc.get(field)] if field in doc else []

        def set(self, key, path, value):
            field = path.removeprefix("$.")
            self.outer.docs.setdefault(key, {})[field] = value

    def json(self):
        return self._Json(self)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zrem(self, key, member):
        self.zsets.setdefault(key, {}).pop(member, None)


class FakeBackfillRedis:
    def __init__(self):
        self.docs = {
            "euri:memory:m1": {
                "content": "Una memoria valida.",
                "embedding": None,
            }
        }
        self.string_keys = {
            "euri:memory:utility_shadow:state": "{}",
            "euri:memory:utility_shadow:latest": "{}",
        }
        self.json_reads = []

    def scan_iter(self, _pattern):
        return iter([*self.docs, *self.string_keys])

    def type(self, key):
        return "ReJSON-RL" if key in self.docs else "string"

    class _Json:
        def __init__(self, outer):
            self.outer = outer

        def get(self, key, _path):
            self.outer.json_reads.append(key)
            if key not in self.outer.docs:
                raise TypeError("wrong Redis type")
            return [dict(self.outer.docs[key])]

        def set(self, key, path, value):
            assert path == "$.embedding"
            self.outer.docs[key]["embedding"] = value

    def json(self):
        return self._Json(self)


class FakeEmbedder:
    available = True

    class _Vector:
        @staticmethod
        def tolist():
            return [0.1, 0.2]

    def encode(self, _content):
        return self._Vector()


def _rows():
    entity = {"type": "memory", "id": "m1"}
    return [
        _event("1-0", "turn", "started", payload={
            "channel": "silent_chat",
        }),
        _event("2-0", "memory", "recalled", entity=entity, payload={
            "channel": "silent_chat",
            "query_sha256": "q1",
            "retrieval_path": "base_rag",
        }),
        _event("3-0", "turn", "responded", payload={
            "channel": "silent_chat",
        }),
        _event("4-0", "memory", "used_in_response", entity=entity, payload={
            "channel": "silent_chat",
            "status": "supported_not_proven",
        }),
    ]


def test_aggregate_is_private_deduplicated_and_observational():
    state = {
        "totals": {},
        "channels": {},
        "entities": {},
        "processed_lineage_events": 0,
    }
    aggregate_lineage_events(state, _rows())
    assert state["totals"] == {
        "turns_started": 1,
        "recalled_nodes": 1,
        "turns_responded": 1,
        "used_nodes_supported_not_proven": 1,
    }
    assert state["entities"]["memory:m1"]["query_hashes"] == ["q1"]
    serialized = json.dumps(state)
    assert "domanda privata" not in serialized
    assert state["last_processed_stream_id"] == "4-0"


def test_review_matures_by_data_or_by_max_wait_without_policy_change():
    state = {
        "observation_started_at": 0.0,
        "totals": {
            "turns_responded": 100,
            "recalled_nodes": 0,
            "used_nodes_supported_not_proven": 0,
        },
        "channels": {},
        "entities": {},
    }
    report = build_memory_utility_report(
        state,
        reference_at=14 * 86400.0,
        min_days=14,
        min_responded_turns=100,
        max_days=30,
    )
    assert report["review_due"] is True
    assert report["review_reason"] == "minimum_window_and_data_reached"
    assert report["automatic_policy_change"] is False

    state["totals"]["turns_responded"] = 2
    forced = build_memory_utility_report(
        state,
        reference_at=30 * 86400.0,
        min_days=14,
        min_responded_turns=100,
        max_days=30,
    )
    assert forced["review_due"] is True
    assert forced["review_reason"] == "maximum_wait_reached"


def test_supported_use_is_materialized_idempotently_for_attention_only():
    redis = FakeRedis()
    state = {
        "entities": {
            "memory:m1": {
                "kind": "memory",
                "id": "m1",
                "used_supported_not_proven": 3,
                "last_used_at": 1200.0,
            }
        }
    }
    first = sync_supported_use_metadata(redis, state)
    second = sync_supported_use_metadata(redis, state)
    assert first["updated_memories"] == 1
    assert second["updated_memories"] == 0
    assert redis.docs["euri:memory:m1"]["supported_use_count"] == 3
    assert redis.docs["euri:memory:m1"]["last_supported_use_at"] == 1200.0
    assert redis.docs["euri:memory:m1"]["supported_use_signal"][
        "attention_only"
    ] is True


def test_daily_maintenance_persists_and_does_not_duplicate_reminder():
    redis = FakeRedis(_rows())

    # Prima passata: apre la finestra, ma non è ancora matura.
    first = run_memory_utility_shadow_maintenance(
        redis,
        reference_at=0.0,
        min_days=1,
        min_responded_turns=1,
        max_days=2,
    )
    assert first["review_due"] is False
    assert UTILITY_STATE_KEY in redis.values
    assert UTILITY_REPORT_KEY in redis.values

    second = run_memory_utility_shadow_maintenance(
        redis,
        reference_at=86400.0,
        min_days=1,
        min_responded_turns=1,
        max_days=2,
    )
    assert second["review_due"] is True
    assert UTILITY_REVIEW_PENDING_KEY in redis.values
    assert len(redis.events) == 1

    run_memory_utility_shadow_maintenance(
        redis,
        reference_at=2 * 86400.0,
        min_days=1,
        min_responded_turns=1,
        max_days=2,
    )
    assert len(redis.events) == 1


def test_legacy_utility_state_is_copied_without_deleting_rollback():
    redis = FakeRedis()
    legacy_state = "euri:memory:utility_shadow:state"
    legacy_report = "euri:memory:utility_shadow:latest"
    redis.values[legacy_state] = json.dumps({"schema_version": 1})
    redis.values[legacy_report] = json.dumps({"old": True})

    result = migrate_legacy_utility_shadow_keys(redis)

    assert len(result["migrated"]) == 2
    assert redis.values[UTILITY_STATE_KEY] == redis.values[legacy_state]
    assert redis.values[UTILITY_REPORT_KEY] == redis.values[legacy_report]
    assert legacy_state in redis.values
    assert legacy_report in redis.values


def test_embedding_backfill_ignores_non_json_keys_in_memory_namespace():
    redis = FakeBackfillRedis()

    assert backfill_embeddings(redis, FakeEmbedder()) == 1
    assert redis.json_reads == ["euri:memory:m1"]
    assert redis.docs["euri:memory:m1"]["embedding"] == [0.1, 0.2]


def test_promotion_explain_uses_existing_signals_without_deciding_again():
    result = explain_insight_promotion(
        {
            "id": "i1",
            "status": "candidate",
            "convergence_count": 2,
            "premise_fidelity": 1.0,
            "bridge_validity": "supported",
            "recalled_count": 4,
        },
        latest_trace={
            "convergences": "2",
            "outcome": "below_threshold",
            "n_judge_confirmed": "1",
        },
    )
    assert result["decision"] == "NOT_PROMOTED"
    assert result["decisive_reason"] == "below_threshold"
    assert result["signals"]["convergences"] == 2
    assert result["automatic_policy_change"] is False


if __name__ == "__main__":
    test_aggregate_is_private_deduplicated_and_observational()
    test_review_matures_by_data_or_by_max_wait_without_policy_change()
    test_supported_use_is_materialized_idempotently_for_attention_only()
    test_daily_maintenance_persists_and_does_not_duplicate_reminder()
    test_legacy_utility_state_is_copied_without_deleting_rollback()
    test_embedding_backfill_ignores_non_json_keys_in_memory_namespace()
    test_promotion_explain_uses_existing_signals_without_deciding_again()
    print("test_memory_utility_shadow: OK")
