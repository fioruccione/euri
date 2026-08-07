#!/usr/bin/env python3
"""Regressioni conservative per la deduplicazione delle memorie passive."""

from __future__ import annotations

from unittest.mock import Mock

import numpy as np

from core.memory_manager import MemoryManager


class _Embedder:
    available = True

    @staticmethod
    def encode(_content, mode="query"):
        return np.asarray([1.0, 0.0], dtype="float32")


def _manager(candidate: str, *, similarity: float) -> MemoryManager:
    memory = MemoryManager(None, embedder=_Embedder())
    memory._search_semantic = Mock(
        return_value=[
            {
                "id": "candidate",
                "content": candidate,
                "_vec_score": 1.0 - similarity,
            }
        ]
    )
    return memory


def test_locomo_related_facts_are_not_duplicates():
    hobbies = (
        "Joanna ama leggere, guardare film, esplorare la natura e scrivere."
    )
    distinct = (
        "Joanna preferisce i film drammatici e le commedie romantiche.",
        "La sceneggiatura di Joanna unisce dramma e romanticismo.",
        "Joanna ama scrivere e stare con gli amici.",
    )
    similarities = (0.86, 0.85, 0.92)

    for content, similarity in zip(distinct, similarities, strict=True):
        probe = Mock(return_value="DUPLICATO")
        memory = _manager(hobbies, similarity=similarity)
        assert memory.is_duplicate_memory(content, llm_probe_fn=probe) is False
        probe.assert_not_called()


def test_new_number_is_never_removed_by_semantic_similarity():
    memory = _manager(
        "Stefano prepara 100 kg di materiale per la prova.",
        similarity=0.98,
    )
    probe = Mock(return_value="DUPLICATO")
    assert memory.is_duplicate_memory(
        "Stefano prepara 120 kg di materiale per la prova.",
        llm_probe_fn=probe,
    ) is False
    probe.assert_not_called()


def test_different_subject_or_negation_never_collapses():
    different_subject = _manager(
        "Nate ama leggere e scrivere.",
        similarity=0.99,
    )
    probe = Mock(return_value="DUPLICATO")
    assert different_subject.is_duplicate_memory(
        "Joanna ama leggere e scrivere.",
        llm_probe_fn=probe,
    ) is False
    probe.assert_not_called()

    negated = _manager(
        "Joanna ama i film d'azione.",
        similarity=0.99,
    )
    assert negated.is_duplicate_memory(
        "Joanna non ama i film d'azione.",
        llm_probe_fn=probe,
    ) is False
    probe.assert_not_called()


def test_normalized_identity_is_duplicate_without_llm():
    memory = _manager(
        "Joanna ama leggere e scrivere.",
        similarity=0.99,
    )
    probe = Mock(side_effect=AssertionError("il probe non deve essere chiamato"))
    assert memory.is_duplicate_memory(
        "  JOANNA ama leggere e scrivere! ",
        llm_probe_fn=probe,
    ) is True
    probe.assert_not_called()


def test_covered_claim_requires_explicit_duplicate_verdict():
    candidate = (
        "Joanna ama leggere, guardare film, esplorare la natura e scrivere."
    )
    content = (
        "Joanna ama anche leggere, guardare film, esplorare la natura e scrivere."
    )

    duplicate_probe = Mock(return_value="DUPLICATO")
    assert _manager(candidate, similarity=0.97).is_duplicate_memory(
        content,
        llm_probe_fn=duplicate_probe,
    ) is True
    assert "Stesso soggetto o stesso argomento NON significa duplicato" in (
        duplicate_probe.call_args.args[0]
    )

    ambiguous_probe = Mock(return_value="Probabilmente duplicato")
    assert _manager(candidate, similarity=0.97).is_duplicate_memory(
        content,
        llm_probe_fn=ambiguous_probe,
    ) is False


if __name__ == "__main__":
    test_locomo_related_facts_are_not_duplicates()
    test_new_number_is_never_removed_by_semantic_similarity()
    test_different_subject_or_negation_never_collapses()
    test_normalized_identity_is_duplicate_without_llm()
    test_covered_claim_requires_explicit_duplicate_verdict()
    print("test_memory_dedup: OK")
