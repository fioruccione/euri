#!/usr/bin/env python3
"""Regressioni causali e idempotenti del Loop 2h."""

from core.self_observation import SelfObservation


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, path="$"):
        doc = self.redis.docs.get(key)
        if doc is None:
            return None
        if path == "$":
            return [dict(doc)]
        field = path.removeprefix("$.")
        return [doc[field]] if field in doc else []


class FakeRedis:
    def __init__(self):
        self.docs = {
            "euri:memory:loser": {
                "id": "loser",
                "content": "prima",
                "domain": "test",
                "superseded_by": "winner",
            },
            "euri:memory:winner": {
                "id": "winner",
                "content": "ora",
                "domain": "test",
            },
        }
        # Simula la duplicazione ammessa dal contratto Redis SCAN.
        self.scan_keys = [
            "euri:memory:loser",
            "euri:memory:loser",
            "euri:memory:winner",
        ]
        self.narrated = set()
        self.streams = []
        self.expirations = []
        self._json = FakeJson(self)

    def scan_iter(self, _pattern):
        yield from self.scan_keys

    def json(self):
        return self._json

    def sismember(self, _key, value):
        return value in self.narrated

    def sadd(self, _key, value):
        self.narrated.add(value)

    def expire(self, key, ttl):
        self.expirations.append((key, ttl))

    def xadd(self, key, fields, **kwargs):
        self.streams.append((key, fields, kwargs))
        return "1-0"


class FakeMemory:
    def __init__(self, *, publish=True):
        self.publish = publish
        self.calls = []
        self.dedup_calls = []

    def save_memory(self, **kwargs):
        self.calls.append(kwargs)
        guard = kwargs.get("precommit_guard")
        if not self.publish or (guard is not None and not guard()):
            return None
        return "reflection-id"

    def supersede_duplicate_reflections(self, *args):
        self.dedup_calls.append(args)


def _observation(*, publish=True):
    redis = FakeRedis()
    memory = FakeMemory(publish=publish)
    observation = SelfObservation(redis, memory)
    observation._generate_narrative = lambda _grouped: "riflessione"
    return observation, redis, memory


def test_scan_duplicates_produce_one_causal_pair():
    observation, redis, memory = _observation()

    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": "reflection-id"}
    fields = memory.calls[0]["final_fields"]
    assert fields["requires_verification"] is True
    assert fields["epistemic_status"] == "internal_self_observation"
    assert fields["source_memory_ids"] == ["loser", "winner"]
    assert fields["self_observation_pairs"] == [{
        "loser_id": "loser",
        "winner_id": "winner",
        "pair_key": "loser|winner",
    }]
    assert redis.narrated == {"loser|winner"}
    assert len(redis.streams) == 1


def test_failed_or_stale_publication_does_not_consume_pairs():
    observation, redis, memory = _observation()

    result = observation.run(precommit_guard=lambda: False)

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert len(memory.calls) == 1
    assert redis.narrated == set()
    assert redis.streams == []


def test_changed_supersession_invalidates_precommit():
    observation, redis, memory = _observation()

    def change_world(_grouped):
        redis.docs["euri:memory:loser"]["superseded_by"] = "someone-else"
        return "riflessione stale"

    observation._generate_narrative = change_world
    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert len(memory.calls) == 1
    assert redis.narrated == set()


if __name__ == "__main__":
    test_scan_duplicates_produce_one_causal_pair()
    test_failed_or_stale_publication_does_not_consume_pairs()
    test_changed_supersession_invalidates_precommit()
    print("test_self_observation: 3/3 OK")
