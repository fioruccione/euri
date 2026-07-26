#!/usr/bin/env python3
"""Regressioni pure per la policy di convergenza claim_judge_v2."""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import config
import core.dream_engine as de_mod
from core.dream_engine import DreamEngine
from scripts.experiments.sample_dream_audit import _complete_trace_content


class FakeJson:
    def __init__(self, redis):
        self.redis = redis

    def get(self, key, path):
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


class FakeSearch:
    def __init__(self, redis):
        self.redis = redis

    def search(self, _query, query_params=None):
        docs = self.redis.neighbors if query_params is not None else self.redis.candidates
        return SimpleNamespace(docs=docs)


class FakeRedis:
    def __init__(self, candidates=None, neighbors=None, docs=None):
        self.candidates = candidates or []
        self.neighbors = neighbors or []
        self.docs = docs or {}
        self.cache = {}
        self.deleted = []
        self.streams = []
        self._json = FakeJson(self)

    def get(self, key):
        return self.cache.get(key)

    def setex(self, key, _ttl, value):
        self.cache[key] = value

    def json(self):
        return self._json

    def ft(self, _index):
        return FakeSearch(self)

    def exists(self, key):
        return key in self.docs

    def delete(self, key):
        self.deleted.append(key)
        self.docs.pop(key, None)

    def xadd(self, key, fields, **kwargs):
        self.streams.append((key, fields, kwargs))
        return "1-0"


def _candidate(key, content, score=None):
    values = {
        "id": key,
        "content": content,
        "embedding": json.dumps([1.0, 0.0]),
        "convergence_count": 1,
    }
    if score is not None:
        values["score"] = score
    return SimpleNamespace(**values)


def _engine_for(subject, neighbors):
    all_docs = [subject, *neighbors]
    docs = {
        doc.id: {
            "id": doc.id,
            "content": doc.content,
            "status": "candidate",
            "convergence_count": 1,
            "source_memory_ids": [f"{doc.id}:a", f"{doc.id}:b"],
            "premise_fidelity": 1.0,
            "bridge_validity": "supported",
        }
        for doc in all_docs
    }
    redis = FakeRedis(
        candidates=[subject],
        neighbors=[subject, *neighbors],
        docs=docs,
    )
    engine = DreamEngine(redis, embedder=None)
    engine._ensure_premise_fidelity = lambda *_args, **_kwargs: False
    traces = []
    engine._trace_convergence = lambda *args, **kwargs: traces.append((args, kwargs))
    return engine, redis, traces


def _patch_config(**values):
    old = {name: getattr(config, name) for name in values}
    for name, value in values.items():
        setattr(config, name, value)
    return old


def _restore_config(old):
    for name, value in old.items():
        setattr(config, name, value)


def test_zero_distance_requires_semantic_confirmation():
    subject = _candidate("seed", "claim operativo del seed")
    neighbors = [
        _candidate("n1", "claim estraneo uno", 0.0),
        _candidate("n2", "claim estraneo due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or False

    engine._evaluate_insights()

    assert len(calls) == 2
    assert redis.docs["seed"]["status"] == "candidate"
    assert redis.deleted == []
    args, meta = traces[-1]
    assert args[1] == 1                 # nessuna convergenza aggiunta
    assert args[2] == 2                 # entrambe sarebbero passate nella policy v1
    assert args[4] == "below_threshold"
    assert meta["n_judge_confirmed"] == 0


def test_convergence_trace_preserves_full_audit_content():
    content = (
        "Nel dominio [alfa] succede: " + "A" * 350 + "\n"
        "Nel dominio [beta] succede: " + "B" * 350 + "\n"
        "La connessione operativa non ovvia è: effetto completo e verificabile."
    )
    subject = _candidate("seed", content)
    engine, redis, _traces = _engine_for(subject, [])

    DreamEngine._trace_convergence(
        engine,
        subject, 3, 0, [], "promoted", judge_trace=[]
    )

    assert len(content) > 600
    assert len(redis.streams) == 1
    _key, fields, _kwargs = redis.streams[0]
    assert fields["seed_content"] == content
    assert fields["seed_content_complete"] == "1"
    assert fields["seed_content_chars"] == str(len(content))
    assert len(fields["seed_content_sha256"]) == 64


def test_audit_sampler_rejects_legacy_or_corrupt_trace_content():
    content = "candidate completo con terza riga verificabile"
    valid = {
        "seed_content": content,
        "seed_content_complete": "1",
        "seed_content_chars": str(len(content)),
        "seed_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    assert _complete_trace_content(valid) == content
    assert _complete_trace_content({"seed_content": content}) is None
    assert _complete_trace_content({**valid, "seed_content_chars": "600"}) is None
    assert _complete_trace_content({**valid, "seed_content_sha256": "0" * 64}) is None


def test_only_judge_confirmed_neighbors_are_absorbed():
    subject = _candidate(
        "seed",
        "Nel dominio [alfa] succede: viene osservato un segnale operativo. "
        "Nel dominio [beta] succede: viene regolato un parametro di processo. "
        "La connessione operativa non ovvia è: usare il segnale per regolare il parametro.",
    )
    neighbors = [
        _candidate("same1", "stesso meccanismo, formulazione uno", 0.0),
        _candidate("same2", "stesso meccanismo, formulazione due", 0.0),
        _candidate("different", "meccanismo non correlato", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    engine._llm_judge_same_insight = lambda _a, b: not b.startswith("meccanismo non")

    old_pulse, old_write = de_mod.pulse_emit, de_mod.write_insight
    de_mod.pulse_emit = lambda *_args, **_kwargs: None
    de_mod.write_insight = lambda *_args, **_kwargs: None
    try:
        engine._evaluate_insights()
    finally:
        de_mod.pulse_emit, de_mod.write_insight = old_pulse, old_write

    assert redis.docs["seed"]["status"] == "promoted"
    assert redis.docs["seed"]["requires_verification"] is True
    assert redis.docs["seed"]["verification_status"] == "internally_supported"
    assert redis.docs["seed"]["epistemic_status"] == "internally_convergent"
    assert redis.docs["seed"]["source_memory_ids"] == ["seed:a", "seed:b"]
    assert redis.docs["seed"]["convergence_source_memory_ids"] == [
        "seed:a",
        "seed:b",
        "same1:a",
        "same1:b",
        "same2:a",
        "same2:b",
    ]
    assert redis.docs["seed"]["convergent_insight_ids"] == ["same1", "same2"]
    assert set(redis.deleted) == {"same1", "same2"}
    assert "different" in redis.docs
    args, meta = traces[-1]
    assert args[1] == 3
    assert args[4] == "promoted"
    assert meta["n_judge_confirmed"] == 2


def test_pair_cache_is_symmetric_and_avoids_second_model_call():
    redis = FakeRedis()
    engine = DreamEngine(redis, embedder=None)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or True

    first = engine._cached_same_insight_judgement(
        "a", "contenuto a", "b", "contenuto b", allow_model_call=True
    )
    second = engine._cached_same_insight_judgement(
        "b", "contenuto b", "a", "contenuto a", allow_model_call=False
    )

    assert first == (True, True, False)
    assert second == (True, False, True)
    assert len(calls) == 1


def test_judge_accepts_only_exact_same_label():
    engine = DreamEngine(FakeRedis(), embedder=None)
    requests = []

    def response(label):
        return SimpleNamespace(message=SimpleNamespace(content=label))

    engine._ollama_chat = lambda **kwargs: requests.append(kwargs) or response("SAME")
    assert engine._llm_judge_same_insight("a", "b") is True
    assert requests[-1]["think"] is True
    assert requests[-1]["options"]["num_predict"] == 5000
    engine._ollama_chat = lambda **_kwargs: response("RELATED")
    assert engine._llm_judge_same_insight("a", "b") is False
    engine._ollama_chat = lambda **_kwargs: response("SAME perché sono simili")
    assert engine._llm_judge_same_insight("a", "b") is None


def test_budget_exhaustion_is_fail_closed():
    subject = _candidate("seed", "claim seed")
    neighbors = [
        _candidate("n1", "claim uno", 0.0),
        _candidate("n2", "claim due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    calls = []
    engine._llm_judge_same_insight = lambda a, b: calls.append((a, b)) or False
    old = _patch_config(CONVERGENCE_JUDGE_BUDGET=1)
    try:
        engine._evaluate_insights()
    finally:
        _restore_config(old)

    assert len(calls) == 1
    assert redis.docs["seed"]["status"] == "candidate"
    assert traces[-1][1]["n_judge_deferred"] == 1


def test_external_refutation_skips_all_expensive_repromotion_work_once():
    subject = _candidate(
        "seed",
        "Nel dominio [a] succede: A. Nel dominio [b] succede: B. "
        "La connessione operativa non ovvia è: C.",
    )
    neighbor = _candidate("neighbor", "stesso claim", 0.0)
    engine, redis, traces = _engine_for(subject, [neighbor])
    redis.docs["seed"].update({
        "demoted_once": True,
        "recalled_count": 9,
        "external_reaction": {"verdict": "SMENTITA"},
        "epistemic_status": "externally_refuted",
    })
    calls = []
    engine._ensure_premise_fidelity = (
        lambda *_args, **_kwargs: calls.append("fidelity") or True
    )
    engine._ensure_bridge_validity = (
        lambda *_args, **_kwargs: calls.append("bridge") or True
    )
    engine._cached_same_insight_judgement = (
        lambda *_args, **_kwargs: calls.append("judge") or (True, True, False)
    )

    engine._evaluate_insights()
    first_stream_count = len(redis.streams)
    engine._evaluate_insights()

    assert calls == []
    assert redis.docs["seed"]["status"] == "candidate"
    assert redis.docs["seed"]["promotion_blocked_reason"] == "external_refutation"
    assert len(traces) == 1
    assert traces[0][0][4] == "denied_repromotion"
    assert first_stream_count == 1
    assert len(redis.streams) == first_stream_count


def test_unused_age_demotion_is_blocked_before_judges():
    subject = _candidate("seed", "claim già demoto")
    engine, redis, traces = _engine_for(subject, [])
    redis.docs["seed"].update({
        "demoted_once": True,
        "recalled_count": 0,
    })
    calls = []
    engine._ensure_premise_fidelity = (
        lambda *_args, **_kwargs: calls.append("fidelity") or True
    )

    engine._evaluate_insights()

    assert calls == []
    assert redis.docs["seed"]["promotion_blocked_reason"] == "demoted_without_use"
    assert traces[0][0][4] == "denied_repromotion"


def test_bridge_validity_classifies_and_preserves_candidate_until_convergence():
    insight_key = "euri:insight:new"
    redis = FakeRedis(docs={
        insight_key: {
            "id": "new",
            "content": "Nel dominio [a] succede: A. Nel dominio [b] succede: B. "
                       "La connessione operativa non ovvia è: C.",
            "status": "candidate",
            "bridge_measurement_eligible": True,
            "source_memory_ids": ["euri:memory:a", "euri:memory:b"],
        },
        "euri:memory:a": {"content": "La memoria A descrive un parametro."},
        "euri:memory:b": {"content": "La memoria B descrive un risultato."},
    })
    engine = DreamEngine(redis, embedder=None)
    requests = []
    engine._ollama_chat = lambda **kwargs: requests.append(kwargs) or SimpleNamespace(
        message=SimpleNamespace(
            content="BRIDGE: HYPOTHESIS\nNOTE: manca una misura causale diretta"
        )
    )

    assert engine._ensure_bridge_validity(insight_key) is True
    assert redis.docs[insight_key]["bridge_validity"] == "hypothesis"
    assert redis.docs[insight_key]["bridge_validity_score"] == 0.5
    assert redis.docs[insight_key]["status"] == "candidate"
    assert requests[-1]["think"] is True
    assert requests[-1]["options"]["num_predict"] == 5000


def test_convergent_hypothesis_emits_pulse_without_entering_rag():
    subject = _candidate(
        "seed",
        "Nel dominio [materiale X] succede: aumenta il modulo. "
        "Nel dominio [materiale Y] succede: aumenta il modulo. "
        "La connessione operativa non ovvia è: potrebbero condividere un meccanismo.",
    )
    neighbors = [
        _candidate("same1", "stesso meccanismo, formulazione uno", 0.0),
        _candidate("same2", "stesso meccanismo, formulazione due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    redis.docs["seed"]["bridge_validity"] = "hypothesis"
    engine._llm_judge_same_insight = lambda *_args: True

    engine._evaluate_insights()

    assert redis.docs["seed"]["status"] == "hypothesis"
    assert (
        redis.docs["seed"]["epistemic_status"]
        == "internally_convergent_hypothesis"
    )
    assert redis.docs["seed"]["verification_status"] == "hypothesis_to_test"
    assert set(redis.deleted) == {"same1", "same2"}
    assert traces[-1][0][4] == "hypothesis_formed"
    assert any(fields["kind"] == "hypothesis_formed"
               for _key, fields, _kwargs in redis.streams)


def test_unmeasured_bridge_blocks_promotion_without_absorbing_neighbors():
    subject = _candidate(
        "seed",
        "Nel dominio [a] succede: A. Nel dominio [b] succede: B. "
        "La connessione operativa non ovvia è: C.",
    )
    neighbors = [
        _candidate("same1", "stesso claim uno", 0.0),
        _candidate("same2", "stesso claim due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    redis.docs["seed"].pop("bridge_validity")
    engine._llm_judge_same_insight = lambda *_args: True

    engine._evaluate_insights()

    assert redis.docs["seed"]["status"] == "candidate"
    assert redis.docs["seed"]["promotion_blocked_reason"] == "bridge_unmeasured"
    assert redis.deleted == []
    assert traces[-1][0][4] == "denied_quality_bridge_unmeasured"


def test_unfaithful_premise_blocks_promotion():
    subject = _candidate(
        "seed",
        "Nel dominio [a] succede: A. Nel dominio [b] succede: B. "
        "La connessione operativa non ovvia è: C.",
    )
    neighbors = [
        _candidate("same1", "stesso claim uno", 0.0),
        _candidate("same2", "stesso claim due", 0.0),
    ]
    engine, redis, traces = _engine_for(subject, neighbors)
    redis.docs["seed"]["premise_fidelity"] = 0.5
    engine._llm_judge_same_insight = lambda *_args: True

    engine._evaluate_insights()

    assert redis.docs["seed"]["status"] == "candidate"
    assert (
        redis.docs["seed"]["promotion_blocked_reason"]
        == "premise_fidelity_below_threshold"
    )
    assert redis.deleted == []
    assert (
        traces[-1][0][4]
        == "denied_quality_premise_fidelity_below_threshold"
    )


def test_bridge_parser_rejects_explanatory_free_text():
    parse = DreamEngine._parse_bridge_validity_response
    assert parse("BRIDGE: SUPPORTED\nNOTE: segue dalle fonti") == (
        "supported", 1.0, "segue dalle fonti"
    )
    assert parse("Secondo me è una buona ipotesi") is None


if __name__ == "__main__":
    test_zero_distance_requires_semantic_confirmation()
    test_convergence_trace_preserves_full_audit_content()
    test_audit_sampler_rejects_legacy_or_corrupt_trace_content()
    test_only_judge_confirmed_neighbors_are_absorbed()
    test_pair_cache_is_symmetric_and_avoids_second_model_call()
    test_judge_accepts_only_exact_same_label()
    test_budget_exhaustion_is_fail_closed()
    test_external_refutation_skips_all_expensive_repromotion_work_once()
    test_unused_age_demotion_is_blocked_before_judges()
    test_bridge_validity_classifies_and_preserves_candidate_until_convergence()
    test_convergent_hypothesis_emits_pulse_without_entering_rag()
    test_unmeasured_bridge_blocks_promotion_without_absorbing_neighbors()
    test_unfaithful_premise_blocks_promotion()
    test_bridge_parser_rejects_explanatory_free_text()
    print("test_convergence_policy: OK")
