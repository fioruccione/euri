#!/usr/bin/env python3
"""Regressioni: profilo installazione e descrizioni su Euri restano piani distinti."""

import json
import os
import subprocess
import sys

import config
from core.memory_risk import is_document_summary, memory_verification_suffix
from core.rag_context import build_rag_context


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
        cwd=os.path.dirname(__file__),
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
    test_profile_names_are_configuration_not_cognitive_constants()
    print("test_self_model_provenance: OK")
