#!/usr/bin/env python3
"""Regressioni pure per envelope Pulse v2 e projector osservazionale."""

import json

from core.cognitive_projector import (
    COGNITIVE_PROJECTOR_GROUP,
    COGNITIVE_PROJECTOR_STATE,
    COGNITIVE_STREAM,
    consume_projector_batch,
    ensure_projector_group,
    project_cognitive_event,
)
from core.pulse import (
    COGNITIVE_EVENT,
    PULSE_STREAM,
    cognitive_emit,
    pulse_emit,
    pulse_emit_once,
)


class FakePulseRedis:
    def __init__(self):
        self.events = []
        self.eval_args = None

    def xadd(self, stream, fields, **kwargs):
        event_id = f"{len(self.events) + 1}-0"
        self.events.append((stream, event_id, dict(fields), kwargs))
        return event_id

    def eval(self, *args):
        self.eval_args = args
        return 1


class FakeProjectorRedis:
    def __init__(self):
        self.events = {}
        self.hashes = {}
        self.acks = []
        self.group_created = False
        self.pending_batches = []
        self.new_batches = []

    def xgroup_create(self, stream, group, id, mkstream):
        assert stream == PULSE_STREAM
        assert group == COGNITIVE_PROJECTOR_GROUP
        assert id == "0-0"
        assert mkstream is True
        if self.group_created:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.group_created = True

    def xadd(self, stream, fields, *, id, **_kwargs):
        target = self.events.setdefault(stream, {})
        if id in target:
            raise RuntimeError(
                "The ID specified in XADD is equal or smaller than the target stream top item"
            )
        target[id] = dict(fields)
        return id

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def xreadgroup(self, **kwargs):
        stream_id = next(iter(kwargs["streams"].values()))
        batches = self.pending_batches if stream_id == "0" else self.new_batches
        return batches.pop(0) if batches else []

    def xack(self, stream, group, event_id):
        self.acks.append((stream, group, event_id))


def _cognitive_event(**overrides):
    event = {
        "schema_version": "2",
        "event_class": COGNITIVE_EVENT,
        "sense": "dream",
        "source": "intero",
        "kind": "candidate_created",
        "producer": "loop2b",
        "trace_id": "dream:trace-1",
        "causation_id": "10-0",
        "logical_event_id": "insight:i1",
        "entity_refs": '[{"type":"insight","id":"i1"}]',
        "parent_refs": '["m1","m2"]',
        "epistemic_before": "seed_pair_selected",
        "epistemic_after": "internally_emergent",
        "experiment_version": "",
        "duration_ms": "1200.000",
        "payload": '{"insight_id":"i1"}',
        "salience": "0.450",
        "ts": "100.000",
    }
    event.update(overrides)
    return event


def test_legacy_emit_is_telemetry_but_uses_v2_envelope():
    redis = FakePulseRedis()
    event_id = pulse_emit(
        redis,
        "presence",
        "extero",
        "arrival",
        payload={"actor": "owner"},
    )

    assert event_id == "1-0"
    _stream, _id, fields, _kwargs = redis.events[0]
    assert fields["schema_version"] == "2"
    assert fields["event_class"] == "telemetry"
    assert json.loads(fields["entity_refs"]) == []


def test_cognitive_emit_carries_lineage_and_returns_stream_id():
    redis = FakePulseRedis()
    event_id = cognitive_emit(
        redis,
        "dream",
        "intero",
        "seed_selected",
        producer="loop2b",
        trace_id="dream:t1",
        causation_id="9-0",
        logical_event_id="dream:t1:seed",
        entity_refs=[{"type": "memory", "id": "m1"}],
        parent_refs=["m1"],
        epistemic_before="eligible",
        epistemic_after="selected",
    )

    assert event_id == "1-0"
    fields = redis.events[0][2]
    assert fields["event_class"] == COGNITIVE_EVENT
    assert fields["trace_id"] == "dream:t1"
    assert fields["causation_id"] == "9-0"
    assert json.loads(fields["entity_refs"]) == [{"type": "memory", "id": "m1"}]
    assert json.loads(fields["parent_refs"]) == ["m1"]


def test_emit_once_preserves_dedup_and_adds_cognitive_envelope():
    redis = FakePulseRedis()
    assert pulse_emit_once(
        redis,
        "memory-saved:m1",
        "memory",
        "extero",
        "saved",
        event_class=COGNITIVE_EVENT,
        producer="memory_outbox",
        trace_id="memory:m1",
        entity_refs=[{"type": "memory", "id": "m1"}],
    )

    args = redis.eval_args
    assert args[2] == PULSE_STREAM
    assert args[11] == "2"
    assert args[12] == COGNITIVE_EVENT
    assert args[13] == "memory_outbox"
    assert args[14] == "memory:m1"
    assert args[16] == "memory-saved:m1"


def test_projection_is_idempotent_and_never_writes_semantic_objects():
    redis = FakeProjectorRedis()
    event = _cognitive_event()

    assert project_cognitive_event(redis, "11-0", event) == "projected"
    assert project_cognitive_event(redis, "11-0", event) == "duplicate"

    assert list(redis.events) == [COGNITIVE_STREAM]
    assert list(redis.events[COGNITIVE_STREAM]) == ["11-0"]
    assert set(redis.hashes) == {COGNITIVE_PROJECTOR_STATE}
    assert not any(key.startswith("euri:memory:") for key in redis.events)
    assert not any(key.startswith("euri:insight:") for key in redis.events)


def test_durable_consumer_recovers_pending_and_acks_ignored_telemetry():
    redis = FakeProjectorRedis()
    ensure_projector_group(redis)
    ensure_projector_group(redis)  # BUSYGROUP è idempotente

    redis.pending_batches.append(
        [(PULSE_STREAM, [("20-0", _cognitive_event())])]
    )
    redis.new_batches.append(
        [(
            PULSE_STREAM,
            [("21-0", _cognitive_event(event_class="telemetry"))],
        )]
    )

    assert consume_projector_batch(redis, pending=True) == (1, 1, 0)
    assert consume_projector_batch(redis, pending=False) == (1, 0, 1)
    assert redis.acks == [
        (PULSE_STREAM, COGNITIVE_PROJECTOR_GROUP, "20-0"),
        (PULSE_STREAM, COGNITIVE_PROJECTOR_GROUP, "21-0"),
    ]


if __name__ == "__main__":
    test_legacy_emit_is_telemetry_but_uses_v2_envelope()
    test_cognitive_emit_carries_lineage_and_returns_stream_id()
    test_emit_once_preserves_dedup_and_adds_cognitive_envelope()
    test_projection_is_idempotent_and_never_writes_semantic_objects()
    test_durable_consumer_recovers_pending_and_acks_ignored_telemetry()
    print("test_cognitive_pulse: OK")
