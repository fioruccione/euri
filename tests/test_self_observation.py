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

    def set(self, key, path, value):
        field = path.removeprefix("$.")
        self.redis.docs[key][field] = value


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
        self.non_evolution = set()
        self.streams = []
        self.expirations = []
        self.zset_removed = []
        self._json = FakeJson(self)

    def scan_iter(self, _pattern):
        yield from self.scan_keys

    def json(self):
        return self._json

    def sismember(self, key, value):
        target = (
            self.non_evolution
            if key == SelfObservation.NON_EVOLUTION_KEY
            else self.narrated
        )
        return value in target

    def sadd(self, key, value):
        target = (
            self.non_evolution
            if key == SelfObservation.NON_EVOLUTION_KEY
            else self.narrated
        )
        target.add(value)

    def expire(self, key, ttl):
        self.expirations.append((key, ttl))

    def xadd(self, key, fields, **kwargs):
        self.streams.append((key, fields, kwargs))
        return "1-0"

    def zadd(self, _key, _mapping):
        return 1

    def zrem(self, key, value):
        self.zset_removed.append((key, value))
        return 1


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
    observation._classify_pair_relation = lambda _pair: ("same", "")
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
        "relation_audit": {},
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


def test_related_entities_emit_comparison_pulse_without_reflection():
    observation, redis, memory = _observation()
    observation._classify_pair_relation = lambda _pair: (
        "related",
        "UBQ assomiglia a Poseidon per l'aumento del modulo, ma resta un materiale diverso.",
    )

    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert memory.calls == []
    assert redis.narrated == set()
    assert redis.non_evolution == {"loser|winner"}
    assert redis.docs["euri:memory:loser"]["superseded_by"] is None
    assert (
        redis.docs["euri:memory:loser"]["supersession_reversed"]["reason"]
        == "semantic_relation_related"
    )
    assert (
        redis.docs["euri:memory:loser"]["supersession_reversed"]["committed"]
        is True
    )
    assert len(redis.streams) == 2
    kinds = [fields["kind"] for _, fields, _ in redis.streams]
    assert kinds == ["supersession_reversed", "comparison_noted"]
    assert "related_not_same" in redis.streams[1][1]["payload"]


def test_different_entities_are_not_narrated_or_emitted():
    observation, redis, memory = _observation()
    observation._classify_pair_relation = lambda _pair: ("different", "")

    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert memory.calls == []
    assert redis.non_evolution == {"loser|winner"}
    assert redis.docs["euri:memory:loser"]["superseded_by"] is None
    assert len(redis.streams) == 1
    assert redis.streams[0][1]["kind"] == "supersession_reversed"


def test_unknown_identity_is_fail_closed_but_retriable():
    observation, redis, memory = _observation()
    observation._classify_pair_relation = lambda _pair: ("unknown", "")

    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert memory.calls == []
    assert redis.non_evolution == set()
    assert redis.narrated == set()
    assert redis.streams == []
    deferred = redis.docs["euri:memory:loser"]["loop2h_identity_deferred"]
    assert deferred["winner_id"] == "winner"
    assert deferred["attempts"] == 1
    assert deferred["retry_delay_days"] == 1
    assert deferred["next_retry_at"] > deferred["last_attempt_at"]

    # Il rinvio non consuma la coppia, ma impedisce che occupi ogni ciclo.
    second = observation.run()
    assert second == {"pairs_found": 0, "reflection_id": None}
    assert redis.non_evolution == set()


def test_unknown_retry_backoff_is_bound_and_specific_to_current_arc():
    observation, redis, memory = _observation()
    pair = {
        "pair_key": "loser|winner",
        "loser": redis.docs["euri:memory:loser"],
        "winner": redis.docs["euri:memory:winner"],
    }
    for expected_attempts, expected_days in (
        (1, 1),
        (2, 2),
        (3, 4),
        (4, 8),
        (5, 16),
        (6, 30),
        (7, 30),
    ):
        assert observation._defer_unknown_pair(pair)
        deferred = pair["loser"]["loop2h_identity_deferred"]
        assert deferred["attempts"] == expected_attempts
        assert deferred["retry_delay_days"] == expected_days

    pair["winner"] = {"id": "new-winner", "content": "nuovo"}
    assert observation._defer_unknown_pair(pair)
    deferred = pair["loser"]["loop2h_identity_deferred"]
    assert deferred["winner_id"] == "new-winner"
    assert deferred["attempts"] == 1
    assert deferred["retry_delay_days"] == 1


def _decision(**overrides):
    decision = {
        "identity": "SAME",
        "basis": "EXPLICIT",
        "claim_subject_a": "server Orion",
        "claim_subject_b": "server Orion",
        "subject_specificity_a": "SPECIFIC",
        "subject_specificity_b": "SPECIFIC",
        "entity_type_a": "MACHINE",
        "entity_type_b": "MACHINE",
        "related_if_distinct": "NOT_APPLICABLE",
        "note": "",
    }
    decision.update(overrides)
    return decision


def test_evidenced_identity_policy_accepts_only_verifiable_explicit_same():
    old = "Il server Orion usa attualmente l'indirizzo 10.0.0.18."
    new = "Dopo la riconfigurazione, il server Orion usa 10.0.0.27."

    relation, note, audit = SelfObservation._apply_relation_policy(
        _decision(), old, new
    )

    assert (relation, note) == ("same", "")
    assert audit["accepted_relation"] == "same"
    assert audit["reason"] == "explicit_same_identity"

    for changed, reason in (
        ({"basis": "INFERRED"}, "identity_not_explicit"),
        ({"subject_specificity_a": "GENERIC"}, "referent_not_specific"),
        ({"claim_subject_b": "server inventato"}, "unverifiable_source_excerpt"),
        ({"entity_type_b": "PROJECT"}, "same_with_type_mismatch"),
    ):
        relation, note, audit = SelfObservation._apply_relation_policy(
            _decision(**changed), old, new
        )
        assert (relation, note) == ("unknown", "")
        assert audit["reason"] == reason


def test_evidenced_identity_policy_rejects_ambiguous_generic_identity():
    old = "Il lotto pilota ha dato MFI 5."
    new = "Il lotto pilota ha dato MFI 8."

    relation, note, audit = SelfObservation._apply_relation_policy(
        _decision(
            claim_subject_a="lotto pilota",
            claim_subject_b="lotto pilota",
            subject_specificity_a="GENERIC",
            subject_specificity_b="GENERIC",
            entity_type_a="BATCH",
            entity_type_b="BATCH",
        ),
        old,
        new,
    )

    assert (relation, note) == ("unknown", "")
    assert audit["reason"] == "referent_not_specific"


def test_evidenced_identity_policy_repairs_only_positive_distinct_identity():
    old = "Il compound UBQ U-17 mostra un modulo di 1.250 MPa."
    new = "Il compound Altosele A-9 mostra un modulo di 1.400 MPa."
    relation, note, audit = SelfObservation._apply_relation_policy(
        _decision(
            identity="DISTINCT",
            claim_subject_a="UBQ U-17",
            claim_subject_b="Altosele A-9",
            entity_type_a="MATERIAL",
            entity_type_b="MATERIAL",
            related_if_distinct="YES",
            note="Materiali distinti confrontabili sul modulo.",
        ),
        old,
        new,
    )

    assert relation == "related"
    assert note == "Materiali distinti confrontabili sul modulo."
    assert audit["reason"] == "explicit_distinct_related"

    relation, note, audit = SelfObservation._apply_relation_policy(
        _decision(
            identity="DISTINCT",
            claim_subject_a="Aurora",
            claim_subject_b="Aurora",
            entity_type_a="PROJECT",
            entity_type_b="PROJECT",
            related_if_distinct="NO",
        ),
        "Aurora usa il modulo di controllo versione 3.",
        "Aurora ha cambiato ufficio.",
    )
    assert (relation, note) == ("unknown", "")
    assert audit["reason"] == "indistinguishable_referents"


def test_relation_audit_is_persisted_on_reversal():
    observation, redis, memory = _observation()
    relation_audit = {
        "contract_version": "loop2h-evidenced-identity-v1",
        "accepted_relation": "different",
        "reason": "explicit_distinct_unrelated",
    }

    def classify(pair):
        pair["loop2h_relation_audit"] = relation_audit
        return "different", ""

    observation._classify_pair_relation = classify
    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert (
        redis.docs["euri:memory:loser"]["supersession_reversed"][
            "relation_audit"
        ]
        == relation_audit
    )
    payload = redis.streams[0][1]["payload"]
    assert "loop2h-evidenced-identity-v1" in payload


def test_failed_reversal_does_not_consume_relation():
    observation, redis, memory = _observation()
    observation._classify_pair_relation = lambda _pair: (
        "related",
        "Somiglianza utile ma identità distinta.",
    )
    observation._reverse_false_supersession = lambda *_args: False

    result = observation.run()

    assert result == {"pairs_found": 1, "reflection_id": None}
    assert memory.calls == []
    assert redis.non_evolution == set()
    assert redis.docs["euri:memory:loser"]["superseded_by"] == "winner"
    assert redis.streams == []


def test_loop2h_reflection_cannot_feed_self_observation_again():
    observation, redis, memory = _observation()
    redis.docs["euri:memory:loser"].update({
        "source": "reflection",
        "tags": ["self_observation", "loop2h", "evolution"],
        "self_observation_pairs": [{
            "loser_id": "older",
            "winner_id": "newer",
        }],
    })

    result = observation.run()

    assert result == {"pairs_found": 0, "reflection_id": None}
    assert memory.calls == []
    assert redis.narrated == set()
    assert redis.streams == []


def test_rejected_cross_entity_evolution_is_not_renarrated():
    observation, redis, memory = _observation()
    redis.docs["euri:memory:loser"].update({
        "source": "passive",
        "verification_status": "rejected_cross_entity_evolution",
        "epistemic_status": "cross_entity_conflation",
    })

    result = observation.run()

    assert result == {"pairs_found": 0, "reflection_id": None}
    assert memory.calls == []
    assert redis.narrated == set()
    assert redis.streams == []


if __name__ == "__main__":
    test_scan_duplicates_produce_one_causal_pair()
    test_failed_or_stale_publication_does_not_consume_pairs()
    test_changed_supersession_invalidates_precommit()
    test_related_entities_emit_comparison_pulse_without_reflection()
    test_different_entities_are_not_narrated_or_emitted()
    test_unknown_identity_is_fail_closed_but_retriable()
    test_unknown_retry_backoff_is_bound_and_specific_to_current_arc()
    test_evidenced_identity_policy_accepts_only_verifiable_explicit_same()
    test_evidenced_identity_policy_rejects_ambiguous_generic_identity()
    test_evidenced_identity_policy_repairs_only_positive_distinct_identity()
    test_relation_audit_is_persisted_on_reversal()
    test_failed_reversal_does_not_consume_relation()
    test_loop2h_reflection_cannot_feed_self_observation_again()
    test_rejected_cross_entity_evolution_is_not_renarrated()
    print("test_self_observation: 14/14 OK")
