#!/usr/bin/env python3
import json
import time

import config
from core.visual_presence import (
    publish_visual_presence,
    read_visual_presence,
    visual_presence_context,
)
from voice.visual_gate import VisualGate


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.last_ex = None

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.last_ex = ex

    def get(self, key):
        return self.values.get(key)


class EnabledAuth:
    @staticmethod
    def is_enabled():
        return True


def test_visual_gate_snapshot_requires_fresh_face_and_identity():
    gate = VisualGate(face_auth=EnabledAuth())
    gate._running = True
    gate._detector_kind = "yunet"
    gate._gate_active = True
    gate._last_seen = 100.0
    gate._identity = config.OWNER_ACTOR_ID
    gate._last_identity_positive_ts = 100.0

    fresh = gate.operational_snapshot(now=100.5)
    assert fresh["face_detected"] is True
    assert fresh["owner_present"] is True
    assert fresh["identity"] == config.OWNER_ACTOR_ID
    assert "similarity" not in fresh

    stale = gate.operational_snapshot(now=120.0)
    assert stale["face_detected"] is False
    assert stale["owner_present"] is False
    assert stale["identity"] is None


def test_bridge_whitelists_payload_and_expires_defensively():
    redis = FakeRedis()
    payload = publish_visual_presence(
        redis,
        {
            "camera_available": True,
            "recognition_available": True,
            "gate_active": True,
            "face_detected": True,
            "identity": config.OWNER_ACTOR_ID,
            "owner_present": True,
            "similarity": 0.99,
            "embedding": [1, 2, 3],
        },
        observed_at=1000.0,
        ttl_s=8,
    )
    assert redis.last_ex == 8
    assert "similarity" not in payload
    assert "embedding" not in payload
    assert read_visual_presence(redis, now=1007.9) is not None
    assert read_visual_presence(redis, now=1008.1) is None


def test_silent_chat_context_treats_owner_presence_as_evidence_not_authority():
    redis = FakeRedis()
    publish_visual_presence(
        redis,
        {
            "camera_available": True,
            "recognition_available": True,
            "gate_active": True,
            "face_detected": True,
            "identity": config.OWNER_ACTOR_ID,
            "owner_present": True,
        },
        observed_at=time.time(),
        ttl_s=8,
    )
    context = visual_presence_context(redis)
    assert config.OWNER_DISPLAY_NAME in context
    assert "forte evidenza" in context
    assert "non e' una prova crittografica" in context
    assert "azioni sensibili" in context


def test_missing_snapshot_does_not_deny_visual_capability():
    context = visual_presence_context(FakeRedis())
    assert "non dispone ora di uno snapshot fresco" in context
    assert "Non negare l'esistenza del sensore" in context


if __name__ == "__main__":
    tests = [globals()[name] for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"test_visual_presence_bridge: OK ({len(tests)} casi)")
