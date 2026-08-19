#!/usr/bin/env python3
"""Regressioni pure del nucleo identitario emergente."""
from __future__ import annotations

import json
from types import SimpleNamespace
from fnmatch import fnmatch

import core.brain as brain_module
from core.brain import Brain
from core.personality_model import (
    empty_projection,
    evolve_projection,
    PersonalityModel,
    render_projection,
    validate_proposals,
)


ACTOR = "stefano"
NOW = 2_000_000_000.0


def _turn(ref: str, text: str, *, conversation: str, observed_at: float, role: str = "user"):
    return {
        "turn_ref": ref,
        "conversation_id": conversation,
        "segment_id": 1,
        "role": role,
        "content": text,
        "trusted": True,
        "observed_at": observed_at,
        "memory_scope": "personal",
    }


def _proposal(*, evidence, strength="pattern", relation="new", trait_id=""):
    return {
        "subject": "relationship",
        "scope": "modifiche architetturali",
        "claim": "Prima di modificare l'architettura, la relazione privilegia il ragionamento condiviso.",
        "relation": relation,
        "trait_id": trait_id,
        "strength": strength,
        "evidence": evidence,
    }


def test_only_owner_verbatim_is_evidence():
    owner = _turn(
        "c1:1", "Prima di fare modifiche ragioniamo sul meccanismo.",
        conversation="c1", observed_at=NOW - 30,
    )
    assistant = _turn(
        "c1:2", "Tu preferisci sempre ragionare prima.",
        conversation="c1", observed_at=NOW - 20, role="assistant",
    )
    payload = {"proposals": [_proposal(evidence=[
        {"turn_ref": "c1:2", "quote": "preferisci sempre ragionare prima"},
        {"turn_ref": "c1:1", "quote": "Prima di fare modifiche ragioniamo"},
    ])]}
    valid = validate_proposals(
        payload,
        projection=empty_projection(ACTOR),
        turns=(owner, assistant),
        new_owner_refs=frozenset({"c1:1"}),
    )
    assert len(valid) == 1
    assert [item["turn_ref"] for item in valid[0]["evidence"]] == ["c1:1"]


def test_old_evidence_cannot_self_reinforce():
    owner = _turn(
        "c1:1", "Prima di fare modifiche ragioniamo sul meccanismo.",
        conversation="c1", observed_at=NOW - 30,
    )
    payload = {"proposals": [_proposal(evidence=[
        {"turn_ref": "c1:1", "quote": "Prima di fare modifiche ragioniamo"},
    ])]}
    assert validate_proposals(
        payload,
        projection=empty_projection(ACTOR),
        turns=(owner,),
        new_owner_refs=frozenset({"c9:9"}),
    ) == []


def test_oversized_claim_is_rejected_instead_of_truncated():
    owner = _turn(
        "c1:1", "Prima di fare modifiche ragioniamo sul meccanismo.",
        conversation="c1", observed_at=NOW - 30,
    )
    proposal = _proposal(evidence=[{
        "turn_ref": "c1:1",
        "quote": "Prima di fare modifiche ragioniamo sul meccanismo",
    }])
    proposal["claim"] = "x" * 321
    assert validate_proposals(
        {"proposals": [proposal]},
        projection=empty_projection(ACTOR),
        turns=(owner,),
        new_owner_refs=frozenset({"c1:1"}),
    ) == []


def test_explicit_trait_is_stable_but_actor_scoped():
    proposal = _proposal(
        strength="declared",
        evidence=[{
            "turn_ref": "c1:1", "quote": "Usa il femminile quando parli di te",
            "conversation_id": "c1", "segment_id": 1, "observed_at": NOW - 10,
        }],
    )
    evolved = evolve_projection(
        empty_projection(ACTOR), [proposal], processed_through=NOW - 10, now_ts=NOW
    )
    assert evolved["traits"][0]["status"] == "stable"
    assert "ragionamento condiviso" in render_projection(
        evolved, actor_id=ACTOR, reference_at=NOW
    )
    assert render_projection(evolved, actor_id="ospite", reference_at=NOW) == ""


def test_pattern_requires_independent_conversations():
    first = _proposal(evidence=[
        {
            "turn_ref": "c1:1", "quote": "Prima ragioniamo sul meccanismo",
            "conversation_id": "c1", "segment_id": 1, "observed_at": NOW - 50,
        },
        {
            "turn_ref": "c1:3", "quote": "Aspetta, prima capiamo il principio",
            "conversation_id": "c1", "segment_id": 2, "observed_at": NOW - 40,
        },
    ])
    projection = evolve_projection(
        empty_projection(ACTOR), [first], processed_through=NOW - 40, now_ts=NOW - 30
    )
    trait = projection["traits"][0]
    assert trait["status"] == "candidate"
    support = _proposal(
        relation="supports",
        trait_id=trait["id"],
        evidence=[{
            "turn_ref": "c1:7", "quote": "Prima discutiamo la logica e poi procedi",
            "conversation_id": "c1", "segment_id": 3, "observed_at": NOW - 5,
        }],
    )
    projection = evolve_projection(
        projection, [support], processed_through=NOW - 5, now_ts=NOW
    )
    assert projection["traits"][0]["status"] == "stable"


def test_single_feedback_does_not_become_personality():
    feedback = _proposal(
        strength="feedback",
        evidence=[{
            "turn_ref": "c1:9", "quote": "Questa analisi e davvero molto bella",
            "conversation_id": "c1", "segment_id": 1, "observed_at": NOW - 5,
        }],
    )
    projection = evolve_projection(
        empty_projection(ACTOR), [feedback], processed_through=NOW - 5, now_ts=NOW
    )
    assert projection["traits"][0]["status"] == "candidate"
    assert render_projection(projection, actor_id=ACTOR, reference_at=NOW) == ""


def test_explicit_contradiction_removes_trait_from_context():
    stable = _proposal(
        strength="declared",
        evidence=[{
            "turn_ref": "c1:1", "quote": "Preferisco discutere prima",
            "conversation_id": "c1", "segment_id": 1, "observed_at": NOW - 20,
        }],
    )
    projection = evolve_projection(
        empty_projection(ACTOR), [stable], processed_through=NOW - 20, now_ts=NOW - 10
    )
    trait = projection["traits"][0]
    contradiction = _proposal(
        relation="contradicts",
        trait_id=trait["id"],
        strength="declared",
        evidence=[{
            "turn_ref": "c2:1", "quote": "Non voglio piu discutere prima",
            "conversation_id": "c2", "segment_id": 1, "observed_at": NOW - 2,
        }],
    )
    projection = evolve_projection(
        projection, [contradiction], processed_through=NOW - 2, now_ts=NOW
    )
    assert projection["traits"][0]["status"] == "contested"
    assert render_projection(projection, actor_id=ACTOR, reference_at=NOW) == ""


def test_brain_injects_projection_only_with_actor():
    captured = []
    original = brain_module.chat_client.chat

    def fake_chat(**kwargs):
        captured.append(kwargs["messages"])
        return SimpleNamespace(message=SimpleNamespace(content="ok"))

    try:
        brain_module.chat_client.chat = fake_chat
        brain = Brain()
        calls = []
        brain._personality_context_callback = lambda actor: calls.append(actor) or "[IDENTITA APPRESA]"
        assert brain.respond("ciao", actor_id=ACTOR) == "ok"
        assert calls == [ACTOR]
        assert any(message["content"] == "[IDENTITA APPRESA]" for message in captured[0])

        captured.clear()
        calls.clear()
        brain = Brain()
        brain._personality_context_callback = lambda actor: calls.append(actor) or "LEAK"
        assert brain.respond("ciao") == "ok"
        assert calls == []
        assert all(message["content"] != "LEAK" for message in captured[0])
    finally:
        brain_module.chat_client.chat = original


class _FakeJson:
    def __init__(self, owner):
        self.owner = owner

    def get(self, key, _path="$"):
        value = self.owner.docs.get(str(key))
        return [value] if value is not None else None

    def set(self, key, _path, value):
        self.owner.docs[str(key)] = value
        return True


class _FakeRedis:
    def __init__(self, docs):
        self.docs = dict(docs)
        self.values = {}
        self._json = _FakeJson(self)

    def json(self):
        return self._json

    def scan_iter(self, match="*"):
        return iter(key for key in self.docs if fnmatch(key, match))

    def get(self, key):
        return self.values.get(str(key))

    def set(self, key, value, nx=False, ex=None):
        key = str(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(str(key), None)


def _bootstrap_store():
    docs = {}
    for index in range(1, 9):
        owner = _turn(
            f"boot:{index * 2 - 1}",
            f"Turno {index}: prima discutiamo la logica e poi procediamo.",
            conversation="boot",
            observed_at=NOW - 100 + index * 2,
        )
        assistant = _turn(
            f"boot:{index * 2}", "Risposta di Euri.",
            conversation="boot", observed_at=NOW - 99 + index * 2, role="assistant",
        )
        docs[f"euri:turn:{owner['turn_ref']}"] = owner
        docs[f"euri:turn:{assistant['turn_ref']}"] = assistant
    redis = _FakeRedis(docs)
    model = PersonalityModel(redis)
    batch = model.prepare_if_due(ACTOR, reference_at=NOW)
    assert batch is not None
    return redis, model, batch


def test_store_bootstraps_from_turns_and_commits_validated_view():
    _redis, model, batch = _bootstrap_store()
    call_kwargs = {}

    def fake_model_call(**kwargs):
        call_kwargs.update(kwargs)
        payload = {"proposals": [_proposal(
            strength="declared",
            evidence=[{
                "turn_ref": "boot:15",
                "quote": "prima discutiamo la logica e poi procediamo",
            }],
        )]}
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))

    result = model.update(batch, model_call=fake_model_call, reference_at=NOW)
    assert result.status == "updated"
    assert result.accepted == 1
    assert result.stable == 1
    assert "ragionamento condiviso" in model.render_context(ACTOR)
    assert call_kwargs["format"] == "json"
    assert call_kwargs["think"] is False
    assert call_kwargs["options"]["num_predict"] >= 3000


def test_invalid_output_uses_short_retry_without_partial_projection():
    redis, model, batch = _bootstrap_store()

    def invalid_model_call(**_kwargs):
        return SimpleNamespace(
            message=SimpleNamespace(content="", thinking="ragionamento troncato"),
            done_reason="length",
        )

    result = model.update(batch, model_call=invalid_model_call, reference_at=NOW)
    assert result.status == "invalid_model_output"
    assert PersonalityModel.projection_key(ACTOR) not in redis.docs
    assert model.prepare_if_due(ACTOR, reference_at=NOW + 10 * 60) is None
    assert model.prepare_if_due(ACTOR, reference_at=NOW + 21 * 60) is not None


def main():
    test_only_owner_verbatim_is_evidence()
    test_old_evidence_cannot_self_reinforce()
    test_oversized_claim_is_rejected_instead_of_truncated()
    test_explicit_trait_is_stable_but_actor_scoped()
    test_pattern_requires_independent_conversations()
    test_single_feedback_does_not_become_personality()
    test_explicit_contradiction_removes_trait_from_context()
    test_brain_injects_projection_only_with_actor()
    test_store_bootstraps_from_turns_and_commits_validated_view()
    test_invalid_output_uses_short_retry_without_partial_projection()
    print("test_personality_model: 10/10 OK")


if __name__ == "__main__":
    main()
