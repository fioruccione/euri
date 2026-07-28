#!/usr/bin/env python3
"""Regressioni temporali e lifecycle audit-only dei turni verbatim."""
from __future__ import annotations

from core.conversation_turns import (
    ConversationTurnStore,
    audit_verbatim_lifecycle,
)


class FakeJson:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, path, value):
        assert path == "$"
        self.docs[key] = dict(value)

    def get(self, key, path):
        assert path == "$"
        doc = self.docs.get(key)
        return [dict(doc)] if isinstance(doc, dict) else None


class FakeRedis:
    def __init__(self):
        self.docs = {}
        self._json = FakeJson(self.docs)

    def json(self):
        return self._json

    def scan_iter(self, match):
        prefix = match.removesuffix("*")
        return iter(sorted(key for key in self.docs if key.startswith(prefix)))


def _turn(ref, observed_at, *, trusted=True):
    conversation_id, seq = ref.rsplit(":", 1)
    return {
        "turn_ref": ref,
        "conversation_id": conversation_id,
        "seq": int(seq),
        "role": "user",
        "content": "Martedì proverò il materiale.",
        "trusted": trusted,
        "observed_at": observed_at,
        "segment_id": 1,
    }


def test_render_keeps_absolute_time_and_channel_with_verbatim():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    store.persist(_turn("conv:1", 1785222000.0))

    rendered = store.render("conv:1")
    assert "martedì 28 luglio 2026, ore 09:00" in rendered
    assert "canale autenticato" in rendered
    assert rendered.endswith("Stefano: Martedì proverò il materiale.")


def test_lifecycle_marks_referenced_and_only_old_unreferenced_as_orphan():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    reference_at = 200 * 86400.0
    store.persist(_turn("conv:1", 0.0))
    store.persist(_turn("conv:2", 10 * 86400.0))
    store.persist(_turn("conv:3", 190 * 86400.0, trusted=False))
    redis.docs["euri:memory:m1"] = {
        "id": "m1",
        "temporal_context": {"source_turn_refs": ["conv:1", "missing:9"]},
    }

    report = audit_verbatim_lifecycle(
        redis,
        reference_at=reference_at,
        grace_days=180,
    )

    assert report["mode"] == "audit_only"
    assert report["counts"]["turns"] == 3
    assert report["counts"]["referenced"] == 1
    assert report["counts"]["orphan_candidates"] == 1
    assert report["counts"]["recent_unreferenced"] == 1
    assert report["orphan_candidates"][0]["turn_ref"] == "conv:2"
    assert report["missing_source_refs"] == [
        {"turn_ref": "missing:9", "referenced_by": ["m1"]}
    ]
    assert len(redis.docs) == 4


if __name__ == "__main__":
    test_render_keeps_absolute_time_and_channel_with_verbatim()
    test_lifecycle_marks_referenced_and_only_old_unreferenced_as_orphan()
    print("test_verbatim_lifecycle: OK")
