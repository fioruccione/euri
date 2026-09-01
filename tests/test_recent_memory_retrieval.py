#!/usr/bin/env python3
"""Regressioni pure per il vincolo strutturale «memoria recente»."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import config
from core.rag_context import (
    _prioritize_authoritative_named_project,
    build_rag_context,
    infer_context_mode,
)
from core.retrieval_context import resolve_retrieval_context
from core.retrieval_strategy import choose_strategy
from utils.temporal import detect_recent_memory_intent, extract_temporal_range


def _local_dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    naive = datetime(year, month, day, hour)
    if hasattr(config.TIMEZONE, "localize"):
        return config.TIMEZONE.localize(naive)
    return naive.replace(tzinfo=config.TIMEZONE)


def _memory(mid: str, content: str, when: datetime, **extra) -> dict:
    ts = when.timestamp()
    return {
        "id": mid,
        "content": content,
        "source": "user",
        "domain": "test",
        "created_at": ts,
        "asserted_at": ts,
        **extra,
    }


class FakeMemory:
    def __init__(self, window: list[dict], semantic: list[dict] | None = None):
        self.window = list(window)
        self.semantic = list(semantic or [])
        self.timerange_calls = []
        self.semantic_calls = 0

    def get_recent_reflections(self, **_kwargs):
        return []

    def get_recent_memories(self, **_kwargs):
        return []

    def search_memories_by_timerange(self, start, end, **kwargs):
        self.timerange_calls.append((start, end, kwargs))
        return list(self.window)

    def search_memories(self, _query, **_kwargs):
        self.semantic_calls += 1
        return list(self.semantic)

    def search_notes(self, *_args, **_kwargs):
        return []

    def get_pending_todos(self):
        return []

    def search_insights(self, *_args, **_kwargs):
        return []


NOW = _local_dt(2026, 7, 29, 10)


def test_recent_intent_is_separate_from_event_date_parser():
    intent = detect_recent_memory_intent(
        "Raccontami qualcosa che abbiamo fatto di recente.",
        NOW,
        window_days=14,
    )

    assert intent is not None
    assert intent.window_days == 14
    assert intent.end == NOW.timestamp()
    assert intent.start == _local_dt(2026, 7, 15, 10).timestamp()
    # Non contaminare il parser usato all'ingest delle memorie.
    assert extract_temporal_range("Recentemente abbiamo provato UBQ.", NOW) is None
    assert infer_context_mode("Che cosa abbiamo fatto ultimamente?") == "search"

    class BrainMustNotBeCalled:
        def classify_retrieval_strategy(self, *_args, **_kwargs):
            raise AssertionError("il vincolo recente non deve passare dal classificatore")

    with patch("utils.date_utils.now", return_value=NOW):
        assert choose_strategy(
            "Raccontami cosa abbiamo fatto di recente.",
            BrainMustNotBeCalled(),
        ) == ("recent_context", "")


def test_recent_query_excludes_old_event_created_today_and_semantic_fallback():
    newest = _memory(
        "recent-1",
        "Ieri abbiamo validato il nuovo recupero.",
        _local_dt(2026, 7, 28, 12),
    )
    older_recent = _memory(
        "recent-2",
        "Abbiamo preparato i provini UBQ.",
        _local_dt(2026, 7, 20, 9),
    )
    old_event_rewritten_today = _memory(
        "old-poseidon",
        "Il test Poseidon risale al 21 giugno.",
        _local_dt(2026, 7, 29, 8),
        event_start=_local_dt(2026, 6, 21, 9).timestamp(),
        event_end=_local_dt(2026, 6, 21, 11).timestamp(),
    )
    memory = FakeMemory(
        [older_recent, old_event_rewritten_today, newest],
        semantic=[old_event_rewritten_today],
    )

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Euri, raccontami qualcosa che abbiamo fatto di recente.",
            memory,
            mode="search",
        )

    assert len(memory.timerange_calls) == 1
    start, end, kwargs = memory.timerange_calls[0]
    assert start == _local_dt(2026, 7, 15, 10).timestamp()
    assert end == NOW.timestamp()
    assert kwargs["touch"] is False
    assert memory.semantic_calls == 0
    assert newest["content"] in rag.text
    assert older_recent["content"] in rag.text
    assert old_event_rewritten_today["content"] not in rag.text
    assert rag.text.index(newest["content"]) < rag.text.index(
        older_recent["content"]
    )
    assert rag.ids == ["recent-1", "recent-2"]
    temporal = rag.diagnostics["temporal_query"]
    assert temporal["kind"] == "recent_memory"
    assert temporal["candidate_hits"] == 3
    assert temporal["eligible_hits"] == 2
    assert temporal["visible_hits"] == 2
    assert temporal["fallback"] == "none"


def test_empty_recent_window_is_explicit_and_never_expands_silently():
    old = _memory(
        "old",
        "Il vecchio test Poseidon.",
        _local_dt(2026, 6, 21, 9),
    )
    memory = FakeMemory([], semantic=[old])

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Cosa abbiamo fatto recentemente?",
            memory,
            mode="search",
        )

    assert memory.semantic_calls == 0
    assert old["content"] not in rag.text
    assert "Nessuna memoria disponibile nella finestra richiesta" in rag.text
    assert "non sostituirlo con ricordi più vecchi" in rag.text
    assert rag.ids == []
    assert rag.diagnostics["temporal_query"]["fallback"] == "none"


def test_normal_topic_search_remains_semantic_and_unbounded_by_recent_window():
    old = _memory(
        "old",
        "Il test Poseidon è stato eseguito il 21 giugno.",
        _local_dt(2026, 6, 21, 9),
    )
    memory = FakeMemory([], semantic=[old])

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Che cosa sai del test Poseidon?",
            memory,
            mode="search",
        )

    assert memory.timerange_calls == []
    assert memory.semantic_calls == 1
    assert old["content"] in rag.text
    assert rag.diagnostics["temporal_query"] is None


def test_named_trace_request_merges_immediate_history_and_durable_memory():
    project = _memory(
        "icma2",
        "Progetto ICMA2: prova della pompa FIMIC FPP20.",
        _local_dt(2026, 7, 20, 9),
    )
    memory = FakeMemory([], semantic=[project])
    history = [{
        "role": "assistant",
        "content": "Stavamo discutendo un impianto.",
        "observed_at": NOW.timestamp() - 30,
    }]

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Parlavamo della pompa sull'ICMA2. Hai tracce di questo discorso?",
            memory,
            mode="search",
            recent_history=history,
        )

    assert memory.semantic_calls == 1
    assert "Conversazione recente immediata" in rag.text
    assert project["content"] in rag.text
    assert rag.diagnostics["durable_recall_requested"] is True
    assert rag.diagnostics["history_resolved_query"] is False


def test_pure_immediate_reference_still_avoids_durable_search():
    old = _memory(
        "old",
        "Una memoria durevole non richiesta.",
        _local_dt(2026, 7, 20, 9),
    )
    memory = FakeMemory([], semantic=[old])
    history = [{
        "role": "user",
        "content": "Il punto precedente era la latenza.",
        "observed_at": NOW.timestamp() - 30,
    }]

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Di cosa parlavamo prima?",
            memory,
            mode="search",
            recent_history=history,
        )

    assert memory.semantic_calls == 0
    assert old["content"] not in rag.text
    assert rag.diagnostics["durable_recall_requested"] is False
    assert rag.diagnostics["history_resolved_query"] is True


def test_trusted_semantic_plan_can_merge_history_without_lexical_memory_cue():
    project = _memory(
        "icma2-semantic",
        "Il progetto ICMA2 riguarda la pompa FPP20.",
        _local_dt(2026, 7, 20, 9),
    )
    memory = FakeMemory([], semantic=[project])
    semantic_frame = {
        "status": "interpreted",
        "confidence": 0.96,
        "memory_retrieval": {
            "needed": True,
            "focus": [{"entity": "ICMA2", "relevance": 1.0}],
            "relation": "continuita' del progetto",
            "evidence_goal": "continuity",
            "confidence": 0.96,
        },
    }

    with patch("core.rag_context.now", return_value=NOW):
        rag = build_rag_context(
            "Riprendiamo quello che dicevamo del progetto ICMA2.",
            memory,
            mode="search",
            recent_history=[{
                "role": "assistant",
                "content": "Avevamo aperto il tema.",
                "observed_at": NOW.timestamp() - 20,
            }],
            semantic_frame=semantic_frame,
        )

    assert memory.semantic_calls == 1
    assert project["content"] in rag.text
    assert rag.diagnostics["durable_recall_requested"] is True


def test_named_project_priority_reserves_direct_source_without_claiming_truth():
    ambient = _memory(
        "ambient",
        "Oggi abbiamo parlato di un argomento generico.",
        _local_dt(2026, 7, 29, 9),
        source="reflection",
    )
    derived = _memory(
        "derived",
        "Una riflessione sulla pompa FIMIC.",
        _local_dt(2026, 7, 29, 9),
        source="reflection",
    )
    project = _memory(
        "project",
        "Progetto ICMA2: prova tecnica della pompa FIMIC FPP20.",
        _local_dt(2026, 7, 20, 9),
        requires_verification=True,
    )

    ordered, priority_id = _prioritize_authoritative_named_project(
        [ambient, derived, project],
        "Hai tracce del progetto ICMA2 con FIMIC?",
    )

    assert [item["id"] for item in ordered] == ["project", "ambient", "derived"]
    assert priority_id == "project"
    assert ordered[0]["requires_verification"] is True


def _memory_search_frame(*, needed=True, focus=None, confidence=0.97):
    return {
        "status": "interpreted",
        "confidence": confidence,
        "memory_scope": "personal",
        "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
        "memory_retrieval": {
            "needed": needed,
            "focus": list(focus or []),
            "relation": "continuita'",
            "evidence_goal": "continuity",
            "confidence": confidence,
        },
    }


def _owner_entity_turn(name, *, seq=7, segment_id=3, scope="personal",
                       canonical=None, status="mentioned"):
    canonical = canonical or name
    return {
        "role": "user",
        "content": f"Stiamo parlando di {name}.",
        "raw_content": f"Stiamo parlando di {name}.",
        "trusted": True,
        "turn_ref": f"conv:{seq}",
        "segment_id": segment_id,
        "memory_scope": scope,
        "semantic_frame": {
            "status": "interpreted",
            "confidence": 0.97,
            "accepted_owner_turn": True,
            "entities": [{
                "observed_form": name,
                "canonical_name": canonical,
                "entity_type": "project",
                "status": status,
                "confidence": 0.96,
            }],
        },
        "observed_at": NOW.timestamp() - 30,
    }


def test_systemic_followup_inherits_last_grounded_owner_subject_without_mutation():
    frame = _memory_search_frame(needed=True)
    history = [_owner_entity_turn("ICMA2")]
    original_frame = {**frame, "memory_retrieval": dict(frame["memory_retrieval"])}

    resolution = resolve_retrieval_context(
        "Nelle tue memorie dovresti avere tutti i dettagli.", frame, history
    )

    assert resolution.effective_query.endswith("Entita' attive del filo: ICMA2")
    assert resolution.semantic_plan["focus"][0]["entity"] == "ICMA2"
    assert resolution.contextual_focus[0]["source_turn_ref"] == "conv:7"
    assert resolution.reason == "inherited_recent_owner_focus"
    assert frame == original_frame


def test_systemic_followup_uses_effective_query_for_identifier_first_recall():
    project = _memory(
        "icma2-systemic",
        "Progetto ICMA2: la pompa FIMIC FPP20 alimenta il filtro RAS500.",
        _local_dt(2026, 7, 20, 9),
    )
    memory = FakeMemory([], semantic=[project])
    queries = []
    original = memory.search_memories

    def capture(query, **kwargs):
        queries.append(query)
        return original(query, **kwargs)

    memory.search_memories = capture
    rag = build_rag_context(
        "Nelle tue memorie dovresti avere tutti i dettagli.",
        memory,
        mode="search",
        recent_history=[_owner_entity_turn("ICMA2")],
        semantic_frame=_memory_search_frame(needed=True),
    )

    assert queries and "ICMA2" in queries[0]
    assert project["id"] in rag.ids
    assert rag.diagnostics["retrieval_resolution"]["query_changed"] is True


def test_systemic_resolution_preserves_no_retrieval_and_scope_segment_boundaries():
    no_retrieval = resolve_retrieval_context(
        "Era solo una menzione.",
        _memory_search_frame(needed=False),
        [_owner_entity_turn("ICMA2")],
    )
    assert no_retrieval.effective_query == no_retrieval.raw_query
    assert no_retrieval.contextual_focus == ()

    other_scope = resolve_retrieval_context(
        "Che cosa ricordi?",
        _memory_search_frame(needed=True),
        [_owner_entity_turn("ICMA2", scope="experiment_demo")],
    )
    assert other_scope.reason == "no_grounded_context_focus"

    history = [
        _owner_entity_turn("ICMA2", seq=1, segment_id=2),
        {
            "role": "user", "content": "Nuovo argomento senza nomi.",
            "trusted": True, "turn_ref": "conv:2", "segment_id": 3,
            "memory_scope": "personal", "semantic_frame": {
                "status": "interpreted", "confidence": 0.97,
                "accepted_owner_turn": True, "entities": [],
            },
        },
    ]
    new_segment = resolve_retrieval_context(
        "Che cosa ricordi?", _memory_search_frame(needed=True), history
    )
    assert new_segment.reason == "no_grounded_context_focus"


def test_systemic_resolution_never_promotes_pronominal_coreference():
    pronoun = _owner_entity_turn(
        "loro", canonical="Gio Style", status="resolved"
    )
    resolution = resolve_retrieval_context(
        "Che cosa ricordi?", _memory_search_frame(needed=True), [pronoun]
    )
    assert resolution.effective_query == resolution.raw_query
    assert resolution.contextual_focus == ()


def test_systemic_resolution_uses_latest_named_topic_in_same_segment():
    history = [
        _owner_entity_turn("ICMA2", seq=1, segment_id=3),
        _owner_entity_turn("Eurostampi", seq=2, segment_id=3),
    ]

    resolution = resolve_retrieval_context(
        "Che cosa ricordi?", _memory_search_frame(needed=True), history
    )

    assert resolution.effective_query.endswith("Entita' attive del filo: Eurostampi")
    assert "ICMA2" not in resolution.effective_query
    assert resolution.contextual_focus[0]["source_turn_ref"] == "conv:2"


def test_systemic_resolution_preserves_explicit_focus_and_query():
    frame = _memory_search_frame(
        needed=True,
        focus=[{"entity": "ICMA2", "role": "focus", "relevance": 0.98}],
    )

    resolution = resolve_retrieval_context(
        "Che cosa ricordi di ICMA2?",
        frame,
        [_owner_entity_turn("Eurostampi")],
    )

    assert resolution.effective_query == resolution.raw_query
    assert resolution.reason == "current_query_grounded"
    assert resolution.contextual_focus == ()


def test_systemic_resolution_fails_closed_on_low_confidence_and_flag_off():
    low_confidence = resolve_retrieval_context(
        "Che cosa ricordi?",
        _memory_search_frame(needed=True),
        [_owner_entity_turn("ICMA2")],
        minimum_confidence=0.99,
    )
    assert low_confidence.reason == "no_grounded_context_focus"

    with patch.object(config, "SYSTEMIC_RETRIEVAL_ENABLED", False):
        disabled = resolve_retrieval_context(
            "Che cosa ricordi?",
            _memory_search_frame(needed=True),
            [_owner_entity_turn("ICMA2")],
        )
    assert disabled.effective_query == disabled.raw_query
    assert disabled.reason == "disabled"
    assert disabled.diagnostics()["enabled"] is False


def test_local_reference_is_rewritten_from_the_immediately_grounded_state():
    owner = _owner_entity_turn("Linea Alfa", seq=10, segment_id=4)
    owner["content"] = "Descrivimi la configurazione attuale della Linea Alfa."
    owner["raw_content"] = owner["content"]
    assistant = {
        "role": "assistant",
        "content": "La linea attuale usa il filtro e poi la pompa.",
        "turn_ref": "conv:11",
        "segment_id": 4,
        "memory_scope": "personal",
    }

    resolution = resolve_retrieval_context(
        "Restiamo sulla configurazione che hai appena descritto: dov'e' la pompa?",
        _memory_search_frame(
            needed=True,
            focus=[{"entity": "Linea Alfa", "role": "focus", "relevance": 0.98}],
        ),
        [owner, assistant],
    )

    assert resolution.reason == "local_reference_natural_rewrite"
    assert "stato attuale" in resolution.effective_query
    assert "risposta immediatamente precedente" in resolution.effective_query
    assert "Linea Alfa" in resolution.effective_query
    assert "Entita' attive del filo" in resolution.effective_query
    assert "Entita' attive del filo" not in resolution.brain_query
    assert "stato attuale" in resolution.brain_query
    assert resolution.local_reference_turn_ref == "conv:11"
    assert resolution.raw_query.startswith("Restiamo sulla configurazione")


def test_local_reference_rewrite_is_generic_and_fails_closed_without_state():
    proposed = _owner_entity_turn("Sistema Beta", seq=20, segment_id=8)
    proposed["content"] = "Parliamo della versione proposta del Sistema Beta."
    proposed["raw_content"] = proposed["content"]
    assistant = {
        "role": "assistant",
        "content": "La versione proposta sposta il sensore a valle.",
        "turn_ref": "conv:21",
        "segment_id": 8,
        "memory_scope": "personal",
    }
    rewritten = resolve_retrieval_context(
        "In quella versione, il sensore dove va?",
        _memory_search_frame(needed=False),
        [proposed, assistant],
    )
    assert rewritten.reason == "local_reference_natural_rewrite"
    assert "stato proposto" in rewritten.effective_query

    ungrounded = dict(proposed)
    ungrounded["content"] = "Parliamo del Sistema Beta."
    ungrounded["raw_content"] = ungrounded["content"]
    abstained = resolve_retrieval_context(
        "In quella versione, il sensore dove va?",
        _memory_search_frame(needed=False),
        [ungrounded, assistant],
    )
    assert abstained.reason == "semantic_no_retrieval"
    assert abstained.effective_query == abstained.raw_query

    ambiguous = dict(proposed)
    ambiguous["content"] = (
        "Confrontiamo la versione attuale e la versione proposta del Sistema Beta."
    )
    ambiguous["raw_content"] = ambiguous["content"]
    unresolved = resolve_retrieval_context(
        "In quella versione, il sensore dove va?",
        _memory_search_frame(needed=False),
        [ambiguous, assistant],
    )
    assert unresolved.reason == "semantic_no_retrieval"
    assert unresolved.effective_query == unresolved.raw_query


if __name__ == "__main__":
    tests = [globals()[name] for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"test_recent_memory_retrieval: OK ({len(tests)} casi)")
