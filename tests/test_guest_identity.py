"""Regressioni identita' vocale e quarantena delle informazioni ospite."""

import numpy as np

from core.guest_claims import GuestClaimStore, PENDING_QUEUE_KEY
from voice.speaker_auth import SpeakerAuth, SpeakerVerdict


class _Pipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self
        return queue

    def execute(self):
        results = []
        for name, args, kwargs in self.operations:
            results.append(getattr(self.redis, name)(*args, **kwargs))
        return results


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def pipeline(self):
        return _Pipeline(self)

    def set(self, key, value):
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def expire(self, _key, _ttl):
        return True

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def ltrim(self, key, start, end):
        self.lists[key] = self.lists.get(key, [])[start:end + 1]
        return True

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return values[start:] if end == -1 else values[start:end + 1]

    def lrem(self, key, count, value):
        values = self.lists.get(key, [])
        removed = 0
        kept = []
        for item in values:
            if item == value and (count == 0 or removed < count):
                removed += 1
            else:
                kept.append(item)
        self.lists[key] = kept
        return removed


def test_speaker_verdict_does_not_authenticate_missing_evidence():
    auth = SpeakerAuth()
    audio = np.ones(32000, dtype=np.float32)
    assert auth.classify(audio) == SpeakerVerdict.INDETERMINATE
    evidence = auth.last_classification()
    assert evidence["reason"] == "speaker_auth_unavailable"
    assert evidence["similarity"] is None

    auth._enabled = True
    auth._encoder = object()
    auth._voiceprint = np.array([1.0, 0.0], dtype=np.float32)
    assert auth.classify(np.ones(8000, dtype=np.float32)) == SpeakerVerdict.INDETERMINATE
    assert auth.last_classification()["reason"] == "clip_too_short"

    auth._embed = lambda *_args: np.array([1.0, 0.0], dtype=np.float32)
    assert auth.classify(audio) == SpeakerVerdict.VERIFIED
    assert auth.last_classification() == {
        "verdict": "verified",
        "similarity": 1.0,
        "threshold": 0.65,
        "reason": "threshold_comparison",
    }
    auth._embed = lambda *_args: np.array([0.0, 1.0], dtype=np.float32)
    assert auth.classify(audio) == SpeakerVerdict.REJECTED
    assert auth.last_classification()["verdict"] == "rejected"
    assert auth.last_classification()["similarity"] == 0.0


def test_guest_claim_stays_outside_memory_until_settled():
    redis = _FakeRedis()
    store = GuestClaimStore(redis)
    doc = store.add(
        "Il lotto Poseidon utilizzato oggi è 24B17.",
        original_text="Euri, riferisci a Stefano che il lotto è 24B17.",
        observed_at=123.0,
    )

    assert redis.lists[PENDING_QUEUE_KEY] == [doc["id"]]
    assert store.pending(limit=5)[0]["status"] == "pending"
    assert store.pending(limit=5)[0]["speaker_id"] == "unknown"

    # Lo stesso claim non genera una seconda richiesta di conferma.
    duplicate = store.add(
        "Il lotto Poseidon utilizzato oggi è 24B17.",
        original_text="Lo ripeto: lotto 24B17.",
    )
    assert duplicate["id"] == doc["id"]
    assert len(redis.lists[PENDING_QUEUE_KEY]) == 1

    store.settle(
        doc["id"],
        "confirmed",
        reviewed_by="stefano",
        promoted_memory_id="memory-1",
    )
    assert store.pending(limit=5) == []
    settled = store.get(doc["id"])
    assert settled["reviewed_by"] == "stefano"
    assert settled["promoted_memory_id"] == "memory-1"


if __name__ == "__main__":
    test_speaker_verdict_does_not_authenticate_missing_evidence()
    test_guest_claim_stays_outside_memory_until_settled()
    print("test_guest_identity: OK")
