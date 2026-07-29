#!/usr/bin/env python3
"""Regressioni del confine personale/sperimentale della memoria."""
from __future__ import annotations

import json
from unittest.mock import patch

from core.conversation_turns import ConversationTurnStore
from core.dream_engine import dream_seed_rejection_reason
from core.memory_attention import is_loop2e_candidate
from core.memory_manager import MemoryManager
from core.memory_scope import (
    INVALID_SCOPE,
    PERSONAL_SCOPE,
    active_scope_state,
    current_scope,
    derive_scope,
    parse_scope_command,
    redis_tag_value,
    scope_clause,
    start_experiment,
    stop_experiment,
    use_memory_scope,
)


class _Json:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, path, value, **_kwargs):
        if path == "$":
            self.docs[key] = dict(value)
        else:
            field = path.removeprefix("$.")
            self.docs.setdefault(key, {})[field] = value

    def get(self, key, _path="$"):
        doc = self.docs.get(key)
        return [dict(doc)] if isinstance(doc, dict) else None


class _Redis:
    def __init__(self):
        self.values = {}
        self.docs = {}
        self._json = _Json(self.docs)

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    def json(self):
        return self._json

    def scan_iter(self, pattern):
        prefix = pattern.removesuffix("*")
        return iter(sorted(key for key in self.docs if key.startswith(prefix)))


def _eligible_doc(**overrides):
    doc = {
        "id": "m1",
        "source": "passive",
        "memory_scope": "personal",
        "content": "Stefano usa il materiale A.",
        "requires_verification": False,
        "recalled_count": 3,
        "last_recalled_at": 100.0,
        "created_at": 90.0,
        "embedding": [0.1, 0.2],
        "memory_axes": {"subject_status": "explicit"},
    }
    doc.update(overrides)
    return doc


def test_scope_commands_are_explicit_and_named():
    start = parse_scope_command(
        "Euri, avvia una sessione sperimentale chiamata Compound UBQ"
    )
    assert start and start.action == "start"
    assert start.label == "Compound UBQ"
    assert parse_scope_command("chiudi la sessione sperimentale").action == "stop"
    assert parse_scope_command("in che modalità di memoria siamo?").action == "status"
    assert parse_scope_command("sto scherzando sul Compound UBQ") is None


def test_active_scope_has_fail_safe_and_returns_to_personal():
    redis = _Redis()
    state = start_experiment(redis, "Compound UBQ", ttl_seconds=600)
    assert state["scope"] == "experiment_compound_ubq"
    assert active_scope_state(redis)["active"] is True

    previous = stop_experiment(redis)
    assert previous["scope"] == "experiment_compound_ubq"
    assert active_scope_state(redis)["scope"] == PERSONAL_SCOPE


def test_context_scope_is_nested_and_restored():
    assert current_scope() == PERSONAL_SCOPE
    with use_memory_scope("experiment_alpha"):
        assert current_scope() == "experiment_alpha"
        with use_memory_scope("personal"):
            assert current_scope() == PERSONAL_SCOPE
        assert current_scope() == "experiment_alpha"
    assert current_scope() == PERSONAL_SCOPE


def test_cross_scope_derivation_fails_closed():
    assert derive_scope([
        {"memory_scope": "experiment_alpha"},
        {"memory_scope": "experiment_alpha"},
    ]) == "experiment_alpha"
    assert derive_scope([
        {"memory_scope": "personal"},
        {"memory_scope": "experiment_alpha"},
    ]) is None
    assert derive_scope([{"memory_scope": "experiment-foo"}]) is None


def test_malformed_scope_is_quarantined_never_personal():
    from core.memory_scope import normalize_scope, scope_of

    assert normalize_scope("experiment-foo") == INVALID_SCOPE
    assert normalize_scope("unexpected") == INVALID_SCOPE
    assert scope_of({"memory_scope": "experiment-foo"}) == INVALID_SCOPE
    assert scope_clause("experiment-foo") == "@memory_scope:{invalid_scope}"


def test_redis_tag_escape_is_effective_for_raw_values():
    assert redis_tag_value("a-b c|d{e}") == r"a\-b\ c\|d\{e\}"


def test_background_cognitive_gates_reject_experiments():
    experimental = _eligible_doc(memory_scope="experiment_alpha")
    assert not is_loop2e_candidate(experimental, now_ts=110.0)
    assert dream_seed_rejection_reason(experimental) == "non_personal_scope"


def test_verbatim_turn_inherits_and_renders_experiment_scope():
    redis = _Redis()
    store = ConversationTurnStore(redis)
    store.persist({
        "turn_ref": "conv:1",
        "conversation_id": "conv",
        "seq": 1,
        "role": "user",
        "content": "Il modulo fittizio è 1400 MPa.",
        "trusted": True,
        "observed_at": 1785312000.0,
        "segment_id": 1,
        "memory_scope": "experiment_ubq",
    })
    turn = store.get("conv:1")
    assert turn.memory_scope == "experiment_ubq"
    assert "scenario sperimentale ubq" in turn.render()


def test_idempotency_and_last_rag_context_are_scope_separated():
    manager = MemoryManager(_Redis(), embedder=None)
    personal = manager._idempotency_key("Dato identico", "passive", "personal")
    experiment = manager._idempotency_key(
        "Dato identico", "passive", "experiment_alpha"
    )
    assert personal != experiment

    manager.set_last_rag_ctx(["personal-id"], memory_scope="personal")
    manager.set_last_rag_ctx(["test-id"], memory_scope="experiment_alpha")
    assert manager.get_last_rag_ctx(memory_scope="personal") == ["personal-id"]
    assert manager.get_last_rag_ctx(
        memory_scope="experiment_alpha"
    ) == ["test-id"]


def test_saved_memory_carries_current_scope():
    class _CommitRedis(_Redis):
        def eval(self, _script, numkeys, *args):
            assert numkeys == 3
            memory_key = args[0]
            memory_id, raw_doc = args[3], args[4]
            self.docs[memory_key] = json.loads(raw_doc)
            return memory_id

        def zrem(self, *_args):
            return 0

        def expireat(self, *_args):
            return True

    redis = _CommitRedis()
    manager = MemoryManager(redis, embedder=None)
    with (
        use_memory_scope("experiment_alpha"),
        patch("core.memory_manager.assign_domain", return_value="test"),
        patch(
            "core.memory_manager.process_memory_outbox_event",
            return_value=True,
        ),
    ):
        mid = manager.save_memory("Dato fittizio per il test.", source="passive")
    assert redis.docs[f"euri:memory:{mid}"]["memory_scope"] == "experiment_alpha"


def test_legacy_backfill_is_idempotent_and_preserves_experiments():
    from utils.redis_client import backfill_memory_scopes

    redis = _Redis()
    redis.docs.update({
        "euri:memory:legacy": {"id": "legacy", "content": "storico"},
        "euri:turn:legacy:1": {"turn_ref": "legacy:1"},
        "euri:note:legacy": {"id": "legacy-note"},
        "euri:memory:test": {
            "id": "test",
            "memory_scope": "experiment_alpha",
        },
    })
    first = backfill_memory_scopes(redis)
    second = backfill_memory_scopes(redis)

    assert first == {"memories": 1, "turns": 1, "notes": 1, "skipped": 0}
    assert second == {"memories": 0, "turns": 0, "notes": 0, "skipped": 0}
    assert redis.docs["euri:memory:legacy"]["memory_scope"] == "personal"
    assert redis.docs["euri:memory:test"]["memory_scope"] == "experiment_alpha"


if __name__ == "__main__":
    test_scope_commands_are_explicit_and_named()
    test_active_scope_has_fail_safe_and_returns_to_personal()
    test_context_scope_is_nested_and_restored()
    test_cross_scope_derivation_fails_closed()
    test_malformed_scope_is_quarantined_never_personal()
    test_redis_tag_escape_is_effective_for_raw_values()
    test_background_cognitive_gates_reject_experiments()
    test_verbatim_turn_inherits_and_renders_experiment_scope()
    test_idempotency_and_last_rag_context_are_scope_separated()
    test_saved_memory_carries_current_scope()
    test_legacy_backfill_is_idempotent_and_preserves_experiments()
    print("test_memory_scope: OK")
