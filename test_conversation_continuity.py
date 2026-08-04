#!/usr/bin/env python3
"""Regressioni per capsule temporanea, workspace e reidratazione."""
import sys

from core.brain import Brain
from core.conversation_continuity import ConversationContinuityStore


class FakeJSON:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, _path):
        doc = self.redis.docs.get(key)
        return [doc] if doc is not None else None


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def zadd(self, *args, **kwargs):
        self.ops.append(("zadd", args, kwargs)); return self

    def zremrangebyrank(self, *args, **kwargs):
        self.ops.append(("zremrangebyrank", args, kwargs)); return self

    def expire(self, *args, **kwargs):
        self.ops.append(("expire", args, kwargs)); return self

    def set(self, *args, **kwargs):
        self.ops.append(("set", args, kwargs)); return self

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.redis, name)(*args, **kwargs)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.zsets = {}
        self.docs = {}

    def pipeline(self, transaction=True):
        return FakePipeline(self)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyrank(self, key, start, stop):
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        end = len(ordered) + stop if stop < 0 else stop
        if end < start:
            return 0
        for member, _score in ordered[start:end + 1]:
            self.zsets[key].pop(member, None)
        return 0

    def zrange(self, key, start, stop):
        ordered = sorted(self.zsets.get(key, {}).items(), key=lambda item: (item[1], item[0]))
        values = [member for member, _score in ordered]
        return values[start:] if stop == -1 else values[start:stop + 1]

    def expire(self, key, ttl):
        return True

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)

    def json(self):
        return FakeJSON(self)


def _turn(ref, seq, role, content, at, *, frame=None, scope="personal"):
    conversation_id = ref.split(":", 1)[0]
    return {
        "schema_version": 1,
        "turn_ref": ref,
        "conversation_id": conversation_id,
        "seq": seq,
        "role": role,
        "content": content,
        "interpreted_content": content,
        "trusted": True,
        "observed_at": at,
        "segment_id": 1,
        "memory_scope": scope,
        "semantic_frame": frame or {},
    }


def test_workspace_derives_focus_entities_and_open_question_without_inference():
    redis = FakeRedis()
    store = ConversationContinuityStore(redis, ttl_s=600, clock=lambda: 130.0)
    user = _turn(
        "old:1", 1, "user", "Ieri abbiamo provato il materiale da Gio Style.", 100.0,
        frame={
            "speech_acts": ["INFORM"],
            "entities": [{"canonical_name": "Gio Style", "entity_type": "organization"}],
        },
    )
    assistant = _turn("old:2", 2, "assistant", "Vuoi riprendere da quella prova?", 110.0)
    for doc in (user, assistant):
        redis.docs[f"euri:turn:{doc['turn_ref']}"] = doc
        store.record(doc)

    snapshot = store.load("personal", now=130.0)
    assert snapshot is not None
    assert snapshot.focus_text == user["content"]
    assert snapshot.active_entities[0]["canonical_name"] == "Gio Style"
    assert len(snapshot.open_loops) == 1
    assert snapshot.recent_resolutions == ()
    assert snapshot.open_loops[0].kind == "assistant_question"
    assert snapshot.open_loops[0].opened_by_turn_ref == "old:2"


def test_next_user_turn_closes_assistant_question():
    redis = FakeRedis()
    store = ConversationContinuityStore(redis, ttl_s=600, clock=lambda: 130.0)
    docs = [
        _turn("old:1", 1, "assistant", "Vuoi continuare?", 100.0),
        _turn("new:1", 1, "user", "Sì, continuiamo.", 120.0),
    ]
    for doc in docs:
        redis.docs[f"euri:turn:{doc['turn_ref']}"] = doc
        store.record(doc)
    snapshot = store.load("personal", now=130.0)
    assert snapshot is not None
    assert snapshot.open_loops == ()
    assert snapshot.recent_resolutions[0].resolution == "user_replied"


def test_no_store_confirmation_does_not_replace_semantic_focus():
    store = ConversationContinuityStore(FakeRedis(), ttl_s=600, clock=lambda: 130.0)
    substantive = _turn(
        "old:1", 1, "user", "Il cliente sta ancora provando il blend.", 100.0,
        frame={
            "speech_acts": ["INFORM"],
            "facts": [{"fact": "la prova è ancora in corso", "modality": "pending"}],
            "memory_disposition": "candidate",
        },
    )
    confirmation = _turn(
        "old:2", 2, "user", "Perfetto, appena so qualcosa ti aggiorno.", 120.0,
        frame={"speech_acts": ["CONFIRM"], "memory_disposition": "no_store"},
    )
    focus, _entities, _open, _resolved = store._derive([substantive, confirmation])
    assert focus == substantive["content"]


def test_pending_question_survives_restart_only_until_deadline():
    redis = FakeRedis()
    store = ConversationContinuityStore(redis, ttl_s=600, clock=lambda: 100.0)
    data = {
        "memory_id": "m1",
        "claim": "La prova è in corso.",
        "question": "È corretto?",
        "question_id": "q1",
    }
    store.set_pending("memory_verification", data, "personal", timeout_s=300)
    restored = store.load_pending("personal", now=250.0)
    assert restored is not None
    assert restored["data"] == data
    assert store.load_pending("personal", now=401.0) is None
    store.clear_pending("personal")
    assert store.load_pending("personal", now=250.0) is None


def test_capsule_expiry_is_fail_closed():
    redis = FakeRedis()
    store = ConversationContinuityStore(redis, ttl_s=60, clock=lambda: 100.0)
    doc = _turn("old:1", 1, "user", "Focus temporaneo", 100.0)
    redis.docs["euri:turn:old:1"] = doc
    store.record(doc)
    assert store.load("personal", now=159.0) is not None
    assert store.load("personal", now=161.0) is None


def test_assistant_reply_closes_request_but_does_not_claim_action_completion():
    redis = FakeRedis()
    store = ConversationContinuityStore(redis, ttl_s=600, clock=lambda: 130.0)
    docs = [
        _turn(
            "new:1", 1, "user", "Controlla la GPU", 100.0,
            frame={
                "speech_acts": ["REQUEST_ACTION"],
                "actions": [{"effect": "read", "target": "gpu"}],
            },
        ),
        _turn("new:2", 2, "assistant", "Controllo.", 110.0),
    ]
    for doc in docs:
        redis.docs[f"euri:turn:{doc['turn_ref']}"] = doc
        store.record(doc)
    snapshot = store.load("personal", now=130.0)
    assert snapshot is not None
    assert snapshot.open_loops == ()
    _focus, _entities, loops, _resolved = store._derive(docs[:1])
    assert loops[0].kind == "user_request"
    # La chiusura conversazionale è "ha risposto", mai "azione eseguita".
    _focus, _entities, all_open, resolved = store._derive(docs)
    assert all_open == ()
    assert resolved[0].resolution == "assistant_replied"
    assert resolved[0].resolution != "action_executed"


def test_brain_restore_is_context_only_and_idempotent():
    brain = Brain()
    doc = _turn("old:1", 1, "user", "Riprendiamo Gio Style", 100.0)
    restored = brain.restore_continuity(
        [doc], memory_scope="personal", prompt_context="Continuità con fonte old:1"
    )
    assert restored == 1
    assert brain.restore_continuity([doc], memory_scope="personal") == 0
    assert brain._conversation_history[0]["restored_context"] is True
    assert brain.passive_messages_after(0) == []
    assert brain._conversation_history[0]["turn_ref"] == "old:1"
    assert brain._continuity_prompt_by_scope["personal"].endswith("old:1")


def test_direct_context_turn_is_archived_but_not_passively_relearned():
    brain = Brain()
    archived = []
    brain._turn_callback = lambda message: archived.append(message)
    brain.record_context_message("assistant", "Mi confermi questa informazione?")
    assert len(brain._conversation_history) == 1
    assert brain.passive_messages_after(0) == []
    assert archived[0]["passive_eligible"] is False


def run():
    tests = [
        test_workspace_derives_focus_entities_and_open_question_without_inference,
        test_next_user_turn_closes_assistant_question,
        test_no_store_confirmation_does_not_replace_semantic_focus,
        test_pending_question_survives_restart_only_until_deadline,
        test_capsule_expiry_is_fail_closed,
        test_assistant_reply_closes_request_but_does_not_claim_action_completion,
        test_brain_restore_is_context_only_and_idempotent,
        test_direct_context_turn_is_archived_but_not_passively_relearned,
    ]
    for test in tests:
        test()
        print(f"PASS — {test.__name__}")
    print(f"Risultato: {len(tests)}/{len(tests)} casi ok")
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
