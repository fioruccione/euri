#!/usr/bin/env python3
"""Regressioni deterministiche per Loop 2k Ideation Arena."""
import json
import math

from core.ideation_tournament import (
    Candidate,
    CandidateGroundingGate,
    EloRanker,
    IdeationArena,
    PairwiseMatch,
    deduplicate_candidates,
)


class FakeJSON:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, _path, value):
        self.docs[key] = value


class FakeRedis:
    def __init__(self):
        self.docs = {}
        self.ttls = {}
        self.events = []

    def json(self):
        return FakeJSON(self.docs)

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def xadd(self, stream, fields, **kwargs):
        self.events.append((stream, fields, kwargs))
        return f"1-{len(self.events)}"


def _candidate(candidate_id: str, proposal: str | None = None) -> Candidate:
    return Candidate(
        id=candidate_id,
        generation_group_id="run-one",
        perspective="test",
        proposal=proposal or f"Proposta {candidate_id}",
        mechanism=f"Meccanismo {candidate_id}",
        grounded_premises=["Il problema richiede alternative"],
        new_assumptions=[],
        falsification_test=f"Test {candidate_id}",
        risks=[],
    )


def test_elo_reference_formula():
    ranker = EloRanker(k_factor=32.0)
    assert math.isclose(ranker.expected_score(1200, 1200), 0.5)
    ratings = {"a": 1200.0, "b": 1200.0}
    ranker.update_ratings(ratings, "a", "b", 1.0)
    assert math.isclose(ratings["a"], 1216.0)
    assert math.isclose(ratings["b"], 1184.0)


def test_embedding_dedup_keeps_lineage_but_removes_competitor():
    first = _candidate("c1", "Stessa proposta")
    second = _candidate("c2", "Formulazione diversa")
    third = _candidate("c3", "Altra strada")

    vectors = iter(([1.0, 0.0], [0.99, 0.01], [0.0, 1.0]))
    def judge(_prompt, *, purpose, **_kwargs):
        assert purpose == "dedup_judge"
        return json.dumps({"comparisons": [{
            "left_id": "c1", "right_id": "c2", "verdict": "SAME",
            "reason": "stessa decisione operativa",
        }]})

    comparisons = deduplicate_candidates(
        [first, second, third],
        model_call=judge,
        embed_call=lambda _text: next(vectors),
        threshold=0.98,
    )

    assert not first.duplicate_of
    assert second.duplicate_of == "c1"
    assert second.duplicate_reason == "stessa decisione operativa"
    assert not third.duplicate_of
    assert any(item.verdict == "SAME" for item in comparisons)


def test_embedding_similarity_does_not_collapse_inverse_assignments():
    first = _candidate("c1", "Qwen sulla 3080, Whisper sulla 4060 Ti")
    first.mechanism = "Assegna Qwen a GPU 3080 e Whisper a GPU 4060 Ti"
    second = _candidate("c2", "Qwen sulla 4060 Ti, Whisper sulla 3080")
    second.mechanism = "Assegna Qwen a GPU 4060 Ti e Whisper a GPU 3080"
    vectors = iter(([1.0, 0.0], [1.0, 0.0]))

    comparisons = deduplicate_candidates(
        [first, second],
        model_call=lambda *_args, **_kwargs: json.dumps({
            "comparisons": [{
                "left_id": "c1", "right_id": "c2",
                "verdict": "DISTINCT",
                "reason": "assegnazioni inverse",
            }]
        }),
        embed_call=lambda _text: next(vectors),
        threshold=0.92,
    )

    assert not first.duplicate_of
    assert not second.duplicate_of
    assert comparisons[0].cosine_similarity == 1.0
    assert comparisons[0].verdict == "DISTINCT"


def test_unavailable_dedup_judge_preserves_alternatives():
    first, second = _candidate("c1"), _candidate("c2")
    comparisons = deduplicate_candidates(
        [first, second],
        model_call=lambda *_args, **_kwargs: "non-json",
        embed_call=lambda _text: [1.0, 0.0],
        threshold=0.92,
    )

    assert not first.duplicate_of
    assert not second.duplicate_of
    assert comparisons[0].verdict == "UNRESOLVED"
    assert comparisons[0].valid is False


def test_grounding_gate_is_fail_closed_on_invalid_output():
    candidates = [_candidate("c1"), _candidate("c2")]
    CandidateGroundingGate(
        lambda *_args, **_kwargs: "non-json"
    ).evaluate("problema", "evidenza", [], candidates)

    assert all(item.gate_status == "rejected" for item in candidates)
    assert all("gate non disponibile" in item.gate_reason for item in candidates)


def test_copeland_reports_a_non_transitive_cycle_as_contested():
    candidates = [_candidate("c1"), _candidate("c2"), _candidate("c3")]
    matches = [
        PairwiseMatch("c1", "c2", "c1", "", "c1", True),
        PairwiseMatch("c2", "c3", "c2", "", "c2", True),
        PairwiseMatch("c1", "c3", "c3", "", "c1", True),
    ]
    ranked = EloRanker().rank(candidates, matches)

    assert {item.copeland_score for item in ranked} == {0.0}
    assert all(item.wins == 1 and item.losses == 1 for item in ranked)


class SuccessfulModel:
    def __call__(self, _prompt, *, purpose, **_kwargs):
        if purpose.startswith("generator:"):
            candidate_id = purpose.split(":", 1)[1]
            return json.dumps({
                "proposal": f"Proposta indipendente {candidate_id}",
                "mechanism": f"Meccanismo verificabile {candidate_id}",
                "grounded_premises": ["Il problema richiede alternative"],
                "new_assumptions": [],
                "falsification_test": f"Esperimento {candidate_id}",
                "risks": [f"Rischio {candidate_id}"],
            })
        if purpose == "grounding_gate":
            return json.dumps({
                "assessments": [{
                    "id": f"c{index}",
                    "premise_fidelity": "FAITHFUL",
                    "constraints": "PASS",
                    "assumptions_explicit": True,
                    "reason": "premesse fedeli",
                } for index in range(1, 5)]
            })
        if purpose.startswith("pairwise:"):
            _, first, second = purpose.split(":")
            # Il candidato con indice minore vince indipendentemente dalla
            # posizione A/B: il test controlla la cecita' dell'ordine.
            winner = "A" if int(first[1:]) < int(second[1:]) else "B"
            return json.dumps({
                "winner": winner,
                "rationale": "domina sui criteri dichiarati",
            })
        raise AssertionError(purpose)


def test_full_pipeline_persists_only_a_non_cognitive_artifact():
    redis = FakeRedis()
    arena = IdeationArena(
        model_call=SuccessfulModel(),
        redis_client=redis,
        embed_call=lambda text: [
            1.0 if "c1" in text else 0.0,
            1.0 if "c2" in text else 0.0,
            1.0 if "c3" in text else 0.0,
            1.0 if "c4" in text else 0.0,
        ],
    )
    result = arena.run(
        "Scegli una strategia.",
        grounding_context="Il problema richiede alternative.",
        constraints=["Non inventare dati"],
        source_refs=["euri:memory:m1"],
    )

    assert result.status == "completed"
    assert result.top_candidate is not None
    assert result.top_candidate.id == "c1"
    assert len(result.matches) == 6
    assert all(match.valid for match in result.matches)
    assert len({item.generation_group_id for item in result.candidates}) == 1

    assert result.artifact_key.startswith("euri:ideation:")
    assert set(redis.docs) == {result.artifact_key}
    artifact = redis.docs[result.artifact_key]
    assert len(artifact["dedup_comparisons"]) == 6
    assert artifact["eligible_for_rag"] is False
    assert artifact["eligible_for_memory"] is False
    assert artifact["eligible_for_insight_convergence"] is False
    assert artifact["requires_verification"] is True
    assert not any(
        key.startswith("euri:memory:") or key.startswith("euri:insight:")
        for key in redis.docs
    )
    assert redis.ttls[result.artifact_key] == 7 * 24 * 3600
    assert redis.events


def test_pipeline_does_not_crown_a_winner_when_gate_rejects_all():
    class RejectingModel(SuccessfulModel):
        def __call__(self, prompt, *, purpose, **kwargs):
            if purpose == "grounding_gate":
                return json.dumps({"assessments": []})
            return super().__call__(prompt, purpose=purpose, **kwargs)

    result = IdeationArena(model_call=RejectingModel()).run(
        "Problema senza basi."
    )

    assert result.status == "insufficient_candidates"
    assert result.top_candidate is None
    assert result.matches == []
    assert all(item.gate_status == "rejected" for item in result.candidates)


if __name__ == "__main__":
    test_elo_reference_formula()
    test_embedding_dedup_keeps_lineage_but_removes_competitor()
    test_embedding_similarity_does_not_collapse_inverse_assignments()
    test_unavailable_dedup_judge_preserves_alternatives()
    test_grounding_gate_is_fail_closed_on_invalid_output()
    test_copeland_reports_a_non_transitive_cycle_as_contested()
    test_full_pipeline_persists_only_a_non_cognitive_artifact()
    test_pipeline_does_not_crown_a_winner_when_gate_rejects_all()
    print("test_ideation_tournament: 8/8 OK")
