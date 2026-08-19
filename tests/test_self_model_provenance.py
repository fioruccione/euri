#!/usr/bin/env python3
"""Regressioni: profilo installazione e descrizioni su Euri restano piani distinti."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config
from core.memory_risk import is_document_summary, memory_verification_suffix
from core.rag_context import (
    build_rag_context,
    format_insight_for_context,
    format_reflection_for_context,
)


def _legacy_clipboard_memory(**overrides):
    doc = {
        "content": "Testo analizzato dagli appunti:\nEuri e' un sistema cognitivo.",
        "source": "teach",
        "memory_kind": "semantic_fact",
        "tags": ["clipboard", "testo"],
        "requires_verification": False,
    }
    doc.update(overrides)
    return doc


def test_legacy_clipboard_summary_is_labeled_without_migration():
    doc = _legacy_clipboard_memory()
    assert is_document_summary(doc) is True
    suffix = memory_verification_suffix(doc)
    assert "SINTESI DI DOCUMENTO" in suffix
    assert "non verifica interna" in suffix
    assert config.OWNER_DISPLAY_NAME in suffix


def test_explicit_user_fact_is_not_reclassified_as_document():
    doc = _legacy_clipboard_memory(
        content="Euri usa Redis 8.8.",
        tags=["architettura"],
    )
    assert is_document_summary(doc) is False


def test_rag_exposes_document_plane_to_the_model():
    doc = _legacy_clipboard_memory(
        id="legacy-doc",
        domain="intelligenza artificiale",
        created_at=1.0,
    )

    class Memory:
        def get_recent_reflections(self, **_kwargs):
            return []

        def get_recent_memories(self, **_kwargs):
            return [doc]

        def get_pending_todos(self):
            return []

        def search_memories(self, *_args, **_kwargs):
            return []

        def search_notes(self, *_args, **_kwargs):
            return []

        def search_insights(self, *_args, **_kwargs):
            return []

    context = build_rag_context("", Memory(), mode="chat")
    assert "SINTESI DOCUMENTO |" in context.text
    assert "non verifica interna" in context.text


def test_rag_keeps_partial_reaction_attached_to_original_insight():
    insight = {
        "id": "partial-insight",
        "content": "Giuseppe analizza i log di qualita' del materiale.",
        "domain_a": "gestione aziendale",
        "domain_b": "chimica",
        "status": "promoted",
        "requires_verification": True,
        "verification_status": "partially_refuted_by_user",
        "external_reaction": {
            "verdict": "PARZIALE",
            "reaction_patch": {
                "confirmed_claims": [
                    {"claim": "Il collegamento generale regge", "evidence": "regge"}
                ],
                "refuted_claims": [
                    {"claim": "Giuseppe valuta il materiale", "evidence": "non Giuseppe"}
                ],
                "replacement_claims": [
                    {"claim": "Il laboratorio valuta il materiale", "evidence": "laboratorio"}
                ],
            },
        },
    }

    class Memory:
        def get_recent_reflections(self, **_kwargs):
            return []

        def get_recent_memories(self, **_kwargs):
            return []

        def get_pending_todos(self):
            return []

        def search_memories(self, *_args, **_kwargs):
            return []

        def search_notes(self, *_args, **_kwargs):
            return []

        def search_insights(self, *_args, **_kwargs):
            return [insight]

    context = build_rag_context("Cosa sai dei log di qualita?", Memory(), mode="chat")
    assert "[CONNESSIONE EMERSA INTERNAMENTE — DA VERIFICARE]" in context.text
    assert "[CORREZIONE PARZIALE DI" in context.text
    assert "Giuseppe valuta il materiale" in context.text
    assert "Il laboratorio valuta il materiale" in context.text


def test_insight_context_exposes_absolute_provenance_without_inventing_legacy_producer():
    insight = {
        "content": "Collegamento storico da verificare.",
        "domain_a": "informatica",
        "domain_b": "business",
        "requires_verification": True,
        "created_at": datetime.fromisoformat(
            "2026-04-25T12:14:00+02:00"
        ).timestamp(),
        "verification_status": "legacy_internally_promoted",
    }

    rendered = format_insight_for_context(insight)

    assert "creato=2026-04-25T12:14:00+02:00" in rendered
    assert "verifica=legacy_internally_promoted" in rendered
    assert "tipo=insight" in rendered
    assert "produttore=non_registrato_legacy" in rendered
    assert "recente" not in rendered.lower()


def test_insight_context_uses_persisted_producer_and_explicit_missing_date():
    rendered = format_insight_for_context({
        "content": "Insight curato.",
        "domain_a": "a",
        "domain_b": "b",
        "requires_verification": False,
        "verification_status": "externally_confirmed_by_owner",
        "artifact_type": "curated_insight",
        "producer": "user",
    })

    assert "creato=non_registrata" in rendered
    assert "verifica=externally_confirmed_by_owner" in rendered
    assert "tipo=curated_insight" in rendered
    assert "produttore=user" in rendered


def test_reflection_context_exposes_time_status_type_and_structural_producer():
    reflection = {
        "content": "La prospettiva si è spostata verso la migrazione hardware.",
        "source": "reflection",
        "created_at": datetime.fromisoformat(
            "2026-08-07T11:08:34+02:00"
        ).timestamp(),
        "verification_status": "narrative_derived_from_supersession",
        "requires_verification": True,
        "memory_kind": "reflection",
        "tags": ["self_observation", "loop2h", "evolution"],
    }

    rendered = format_reflection_for_context(reflection)

    assert rendered.startswith("- [INTERPRETAZIONE DI EURI]")
    assert "creato=2026-08-07T11:08:34+02:00" in rendered
    assert "verifica=narrative_derived_from_supersession" in rendered
    assert "tipo=reflection" in rendered
    assert "produttore=loop2h" in rendered
    assert "recente" not in rendered.lower()


def test_reflection_context_does_not_invent_missing_legacy_producer():
    rendered = format_reflection_for_context({
        "content": "Una vecchia interpretazione interna.",
        "source": "reflection",
        "requires_verification": True,
    })

    assert "creato=non_registrata" in rendered
    assert "verifica=requires_verification" in rendered
    assert "tipo=reflection" in rendered
    assert "produttore=non_registrato_legacy" in rendered


def test_runtime_reflection_block_uses_metadata_and_keeps_two_item_cap():
    reflections = [
        {
            "id": f"reflection-{index}",
            "content": f"Interpretazione {index}.",
            "source": "reflection",
            "created_at": float(index),
            "requires_verification": True,
            "tags": ["loop2a"],
        }
        for index in range(2)
    ]

    class Memory:
        def get_recent_reflections(self, **kwargs):
            assert kwargs["limit"] == 2
            return reflections

        def get_recent_memories(self, **_kwargs):
            return []

        def get_pending_todos(self):
            return []

        def search_memories(self, *_args, **_kwargs):
            return []

        def search_notes(self, *_args, **_kwargs):
            return []

        def search_insights(self, *_args, **_kwargs):
            return []

    context = build_rag_context("", Memory(), mode="chat")

    assert context.text.count("[INTERPRETAZIONE DI EURI]") == 2
    assert context.text.count("produttore=loop2a") == 2


def test_semantic_reflection_result_uses_the_same_metadata_contract():
    reflection = {
        "id": "semantic-reflection",
        "content": "Una reflection recuperata dalla query.",
        "source": "reflection",
        "domain": "test",
        "created_at": datetime.fromisoformat(
            "2026-06-08T17:26:30+02:00"
        ).timestamp(),
        "requires_verification": True,
        "tags": ["loop2f"],
    }

    class Memory:
        def get_recent_reflections(self, **_kwargs):
            raise AssertionError("search mode non deve usare il blocco ambientale")

        def get_recent_memories(self, **_kwargs):
            return []

        def get_pending_todos(self):
            return []

        def search_memories(self, *_args, **_kwargs):
            return [reflection]

        def search_notes(self, *_args, **_kwargs):
            return []

        def search_insights(self, *_args, **_kwargs):
            return []

    context = build_rag_context(
        "Cerca la reflection recuperata dalla query", Memory(), mode="search"
    )

    assert "creato=2026-06-08T17:26:30+02:00" in context.text
    assert "verifica=requires_verification" in context.text
    assert "tipo=reflection" in context.text
    assert "produttore=loop2f" in context.text


def test_profile_names_are_configuration_not_cognitive_constants():
    env = os.environ.copy()
    env.update({
        "EURI_OWNER_ACTOR_ID": "ada",
        "EURI_OWNER_DISPLAY_NAME": "Ada",
        "EURI_ASSISTANT_DISPLAY_NAME": "Nora",
    })
    code = (
        "import json, config; "
        "from core.temporal_context import history_line_for_prompt; "
        "print(json.dumps({"
        "'owner_id': config.OWNER_ACTOR_ID, "
        "'prompt': config.SYSTEM_PROMPT, "
        "'line': history_line_for_prompt({'role':'user','content':'ciao'})"
        "}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["owner_id"] == "ada"
    assert "Ada" in payload["prompt"]
    assert "Nora" in payload["prompt"]
    assert "Stefano" not in payload["prompt"]
    assert "] Ada: ciao" in payload["line"]


if __name__ == "__main__":
    test_legacy_clipboard_summary_is_labeled_without_migration()
    test_explicit_user_fact_is_not_reclassified_as_document()
    test_rag_exposes_document_plane_to_the_model()
    test_rag_keeps_partial_reaction_attached_to_original_insight()
    test_insight_context_exposes_absolute_provenance_without_inventing_legacy_producer()
    test_insight_context_uses_persisted_producer_and_explicit_missing_date()
    test_reflection_context_exposes_time_status_type_and_structural_producer()
    test_reflection_context_does_not_invent_missing_legacy_producer()
    test_runtime_reflection_block_uses_metadata_and_keeps_two_item_cap()
    test_semantic_reflection_result_uses_the_same_metadata_contract()
    test_profile_names_are_configuration_not_cognitive_constants()
    print("test_self_model_provenance: OK")
