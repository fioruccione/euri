#!/usr/bin/env python3
"""Regressions for chronological conversation and episodic memory grounding."""
from datetime import datetime
from unittest.mock import patch

import config
from core.brain import Brain
from core.temporal_context import (
    derive_passive_memory_metadata,
    history_content_for_prompt,
    memory_time_label,
    qualitative_distance,
    resolve_text_event_time,
)


def _ts(hour: int, minute: int = 0) -> float:
    return _date_ts(2026, 7, 15, hour, minute)


def _date_ts(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> float:
    naive = datetime(year, month, day, hour, minute, second)
    if hasattr(config.TIMEZONE, "localize"):
        return config.TIMEZONE.localize(naive).timestamp()
    return naive.replace(tzinfo=config.TIMEZONE).timestamp()


def test_six_hour_gap_is_not_immediate():
    assert qualitative_distance(_ts(10, 56), _ts(17, 19)) == "circa 6 ore fa"
    rendered = history_content_for_prompt(
        {
            "role": "user",
            "content": "Riprendiamo l'IZOD.",
            "observed_at": _ts(10, 56),
            "segment_id": 1,
        },
        reference_at=_ts(17, 19),
    )
    assert "circa 6 ore fa" in rendered
    assert "poco fa" not in rendered


def test_brain_starts_a_new_segment_after_idle_gap():
    brain = Brain()
    with brain.history_lock:
        brain._append_history_locked("user", "Primo filo", True, observed_at=_ts(10, 56))
        brain._append_history_locked("assistant", "Va bene", True, observed_at=_ts(10, 57))
        brain._append_history_locked("user", "Riprendiamolo", True, observed_at=_ts(17, 19))

    rows = brain.passive_messages_after(0)
    assert [row["segment_id"] for row in rows] == [1, 1, 2]
    assert all(row["conversation_id"] == brain._conversation_id for row in rows)


def test_structured_passive_parser_preserves_kind_and_turns():
    parsed = Brain._parse_passive_fact_line(
        "FORTE: [TIPO=EPISODIO; TURNI=7,8] "
        "Stefano ha riaperto il tema IZOD; non ha fornito valori."
    )
    assert parsed == {
        "content": "Stefano ha riaperto il tema IZOD; non ha fornito valori.",
        "support": "strong",
        "memory_kind": "episode",
        "source_turn_ids": [7, 8],
    }


def test_passive_fact_requires_user_only_source_turns():
    conversation = [
        {"seq": 10, "role": "user", "content": "Il pezzo pesa 10,1 kg."},
        {"seq": 11, "role": "assistant", "content": "Quindi il ciclo e' 81 secondi."},
        {"seq": 12, "role": "user", "content": "Esatto."},
    ]

    user_fact = {
        "content": "Il pezzo pesa 10,1 kg.",
        "support": "strong",
        "memory_kind": "semantic_fact",
        "source_turn_ids": [10],
    }
    assistant_fact = {
        "content": "Il ciclo e' 81 secondi.",
        "support": "strong",
        "memory_kind": "semantic_fact",
        "source_turn_ids": [11, 12],
    }
    episode = {
        "content": "Stefano ed Euri hanno lasciato aperto il dato sul ciclo.",
        "support": "strong",
        "memory_kind": "episode",
        "source_turn_ids": [10, 11, 12],
    }

    assert Brain._passive_item_has_valid_provenance(user_fact, conversation)
    assert not Brain._passive_item_has_valid_provenance(assistant_fact, conversation)
    assert Brain._passive_item_has_valid_provenance(episode, conversation)
    assert not Brain._passive_item_has_valid_provenance(
        {"content": "Senza turni", "memory_kind": "semantic_fact"}, conversation
    )


def test_passive_anchor_resolves_this_morning_against_assertion_time():
    conversation = [
        {
            "seq": 7,
            "role": "user",
            "content": "Parliamo della prova IZOD fatta questa mattina.",
            "observed_at": _ts(10, 56),
            "conversation_id": "session-1",
            "segment_id": 1,
        },
        {
            "seq": 8,
            "role": "assistant",
            "content": "Non ho ancora valori o risultati.",
            "observed_at": _ts(10, 57),
            "conversation_id": "session-1",
            "segment_id": 1,
        },
    ]
    item = {
        "content": (
            "Stefano ha riaperto il tema della prova IZOD riferita a quella mattina; "
            "non ha ancora fornito valori."
        ),
        "memory_kind": "episode",
        "source_turn_ids": [7, 8],
    }
    metadata = derive_passive_memory_metadata(item, conversation)
    temporal = metadata["temporal_context"]

    assert metadata["memory_kind"] == "conversation_anchor"
    assert temporal["asserted_at"] == _ts(10, 56)
    assert temporal["event_start"] == _ts(0, 0)
    assert temporal["event_end"] == _ts(10, 56)
    assert temporal["event_precision"] == "part_of_day"
    assert temporal["conversation_id"] == "session-1"

    label = memory_time_label(
        {
            "event_start": temporal["event_start"],
            "event_end": temporal["event_end"],
            "asserted_at": temporal["asserted_at"],
            "temporal_context": temporal,
        },
        reference_at=_ts(17, 19),
    )
    assert "mattina del 15/07/2026" in label
    assert "riferito circa 6 ore fa" in label


def test_generic_memories_resolve_numeric_and_relative_time():
    numeric = resolve_text_event_time(
        "Il controllo e' stato eseguito il 14/07/2026.", asserted_at=_ts(17, 19)
    )
    relative = resolve_text_event_time(
        "Il controllo e' stato eseguito due ore fa.", asserted_at=_ts(17, 19)
    )
    assert datetime.fromtimestamp(numeric["event_start"], tz=config.TIMEZONE).date().isoformat() == "2026-07-14"
    assert relative["event_start"] < _ts(15, 19) < relative["event_end"]


def test_event_date_beats_incidental_recent_discourse_marker():
    asserted_at = _date_ts(2026, 8, 4, 12, 43)
    resolved = resolve_text_event_time(
        "Poco fa abbiamo ricordato la prova effettuata lunedì 3 agosto.",
        asserted_at=asserted_at,
    )
    event_date = datetime.fromtimestamp(
        resolved["event_start"], tz=config.TIMEZONE
    ).date()
    assert resolved["temporal_expression"].lower() == "3 agosto"
    assert resolved["event_precision"] == "explicit_day"
    assert event_date.isoformat() == "2026-08-03"


def test_passive_relative_date_overrides_wrong_llm_absolute_date():
    asserted_at = _date_ts(2022, 1, 23, 14, 1, 2)
    conversation = [
        {
            "seq": 25,
            "role": "user",
            "content": (
                "Ho finalmente finito la mia prima sceneggiatura completa "
                "e l'ho stampata venerdì scorso."
            ),
            "observed_at": asserted_at,
            "conversation_id": "session-2",
            "segment_id": 2,
        }
    ]
    item = {
        "content": (
            "Joanna ha terminato e stampato la sua prima sceneggiatura "
            "completa il 20/01/2022."
        ),
        "memory_kind": "semantic_fact",
        "source_turn_ids": [25],
    }

    metadata = derive_passive_memory_metadata(item, conversation)
    temporal = metadata["temporal_context"]
    event_date = datetime.fromtimestamp(
        temporal["event_start"], tz=config.TIMEZONE
    ).date()

    assert event_date.isoformat() == "2022-01-21"
    assert "21/01/2022" in metadata["canonical_content"]
    assert "20/01/2022" not in metadata["canonical_content"]
    assert temporal["source_temporal_expression"].lower() == "venerdì scorso"
    assert temporal["content_temporal_expression"] == "20/01/2022"
    assert temporal["content_date_corrected"] is True
    assert temporal["content_original_date"] == "20/01/2022"


def test_passive_extractor_uses_full_italian_anchor_and_forbids_date_math():
    conversation = [
        {
            "seq": 25,
            "role": "user",
            "content": "Ho stampato la sceneggiatura venerdì scorso.",
            "observed_at": _date_ts(2022, 1, 23, 14, 1, 2),
        },
        {
            "seq": 26,
            "role": "assistant",
            "content": "Congratulazioni!",
            "observed_at": _date_ts(2022, 1, 23, 14, 2),
        },
    ]

    class _Message:
        content = "NOTHING"

    class _Response:
        message = _Message()

    with patch("core.brain.chat_client.chat", return_value=_Response()) as chat:
        assert Brain().extract_passive_memories(conversation) == []

    prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "domenica 23 gennaio 2022, ore 14:01" in prompt
    assert "venerdì scorso" in prompt
    assert "NON calcolare né inventare una data assoluta" in prompt
    assert "l'unione di TUTTI i turni necessari" in prompt


def test_brain_prompt_exposes_time_but_marks_it_internal():
    brain = Brain()
    with brain.history_lock:
        brain._append_history_locked(
            "user", "Parliamo dell'IZOD", True, observed_at=_ts(10, 56)
        )
        brain._append_history_locked(
            "assistant", "Non ho ancora i valori", True, observed_at=_ts(10, 57)
        )

    class _Message:
        content = "Dimmi il valore."

    class _Response:
        message = _Message()

    with (
        patch("core.brain.time.time", return_value=_ts(17, 19)),
        patch("core.brain.chat_client.chat", return_value=_Response()) as chat,
    ):
        brain.respond("Il valore e' 4,2")

    messages = chat.call_args.kwargs["messages"]
    prompt = "\n".join(message["content"] for message in messages)
    assert "circa 6 ore fa" in prompt
    assert "non e' 'poco fa'" in prompt
    assert "Parliamo dell'IZOD" in prompt
    assert "Il valore e' 4,2" in prompt

    historical_messages = [
        message for message in messages
        if message["role"] in {"user", "assistant"}
        and message["content"] != "Il valore e' 4,2"
    ]
    assert historical_messages
    assert all("[tempo interno:" not in message["content"] for message in historical_messages)


def test_internal_time_label_never_reaches_output():
    cleaned = Brain._clean(
        "[tempo interno: oggi 18:40; adesso; segmento 1] Nessun problema."
    )
    assert cleaned == "Nessun problema."


if __name__ == "__main__":
    test_six_hour_gap_is_not_immediate()
    test_brain_starts_a_new_segment_after_idle_gap()
    test_structured_passive_parser_preserves_kind_and_turns()
    test_passive_fact_requires_user_only_source_turns()
    test_passive_anchor_resolves_this_morning_against_assertion_time()
    test_generic_memories_resolve_numeric_and_relative_time()
    test_event_date_beats_incidental_recent_discourse_marker()
    test_passive_relative_date_overrides_wrong_llm_absolute_date()
    test_passive_extractor_uses_full_italian_anchor_and_forbids_date_math()
    test_brain_prompt_exposes_time_but_marks_it_internal()
    test_internal_time_label_never_reaches_output()
    print("test_temporal_context: OK")
