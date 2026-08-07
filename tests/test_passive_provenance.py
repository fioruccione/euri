#!/usr/bin/env python3
"""Regressioni per la copertura semantica delle fonti passive."""

from __future__ import annotations

from unittest.mock import patch

from core.brain import Brain
from core.validator import validate_passive_payload


class _Message:
    def __init__(self, content: str):
        self.content = content


class _Response:
    def __init__(self, content: str):
        self.message = _Message(content)


def _conversation() -> list[dict]:
    return [
        {
            "seq": 25,
            "role": "user",
            "speaker": "Joanna",
            "content": (
                "Ho finalmente finito la mia prima sceneggiatura completa "
                "e l'ho stampata venerdì scorso."
            ),
        },
        {
            "seq": 26,
            "role": "assistant",
            "speaker": "Nate",
            "content": "Di cosa parla?",
        },
        {
            "seq": 27,
            "role": "user",
            "speaker": "Joanna",
            "content": "È un misto di dramma e romanticismo.",
        },
    ]


def _screenplay_item() -> dict:
    return {
        "content": (
            "Joanna ha completato e stampato la sua prima sceneggiatura, "
            "un mix di dramma e romanticismo, venerdì scorso."
        ),
        "support": "strong",
        "memory_kind": "semantic_fact",
        "source_turn_ids": [25],
    }


def test_audit_repairs_incomplete_source_turn_union():
    response = _Response(
        '{"verdict":"SUPPORTED","source_turn_ids":"T25,T27"}'
    )
    with patch("core.brain.chat_client.chat", return_value=response) as chat:
        audited = Brain().audit_passive_memory_provenance(
            _screenplay_item(),
            _conversation(),
        )

    assert audited is not None
    assert audited["source_turn_ids"] == [25, 27]
    assert audited["provenance_audit"] == {
        "schema_version": 1,
        "status": "supported",
        "original_source_turn_ids": [25],
        "source_turn_ids": [25, 27],
        "repaired": True,
    }
    prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "[T25 | UTENTE | PARLANTE=Joanna]" in prompt
    assert "[T27 | UTENTE | PARLANTE=Joanna]" in prompt
    assert "[T26 | ASSISTENTE | PARLANTE=Nate]" not in prompt
    assert "anche aggiungendo quelli dimenticati" in prompt


def test_audit_restores_the_owner_turn_that_resolves_an_anaphora():
    item = {
        "content": (
            "La prima sceneggiatura di Joanna è un misto di dramma "
            "e romanticismo."
        ),
        "support": "strong",
        "memory_kind": "semantic_fact",
        "source_turn_ids": [27],
    }
    response = _Response(
        '{"verdict":"SUPPORTED","source_turn_ids":[27]}'
    )
    with patch("core.brain.chat_client.chat", return_value=response):
        audited = Brain().audit_passive_memory_provenance(item, _conversation())

    assert audited is not None
    assert audited["source_turn_ids"] == [25, 27]
    assert audited["provenance_audit"]["original_source_turn_ids"] == [27]
    assert audited["provenance_audit"]["repaired"] is True


def test_extractor_preserves_explicit_dataset_speaker_identity():
    with patch(
        "core.brain.chat_client.chat",
        return_value=_Response("NOTHING"),
    ) as chat:
        assert Brain().extract_passive_memories(_conversation()) == []

    prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "conversazione tra Joanna e il suo assistente Nate" in prompt
    assert "turni di Joanna" in prompt
    assert "[T1 | tempo non registrato] Joanna: Ho finalmente finito" in prompt
    assert "[T2 | tempo non registrato] Nate: Di cosa parla?" in prompt
    assert "identificatori LOCALI T1, T2" in prompt


def test_extractor_and_auditor_receive_accepted_semantic_identity_and_modality():
    conversation = [
        {
            "seq": 5,
            "role": "user",
            "speaker": "Stefano",
            "content": "Geostyle sta ancora provando il blend; aspettiamo il risultato.",
            "semantic_frame": {
                "status": "interpreted",
                "confidence": 0.97,
                "speech_acts": ["INFORM"],
                "entities": [{
                    "observed_form": "Geostyle",
                    "canonical_name": "Gio Style",
                    "entity_type": "organization",
                }],
                "facts": [{
                    "fact": "Gio Style sta provando il blend",
                    "modality": "pending",
                }],
                "memory_disposition": "candidate",
                "accepted_owner_turn": True,
            },
        },
        {"seq": 6, "role": "assistant", "speaker": "Euri", "content": "Va bene."},
    ]
    with patch("core.brain.chat_client.chat", return_value=_Response("NOTHING")) as chat:
        Brain().extract_passive_memories(conversation)
    extraction_prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "[FRAME T1 | interpretazione accettata]" in extraction_prompt
    assert '"canonical_name": "Gio Style"' in extraction_prompt
    assert '"modality": "pending"' in extraction_prompt
    assert "Non trasformare una prova svolta in un esito ottenuto" in extraction_prompt

    item = {
        "content": "Gio Style sta ancora provando il blend e il risultato è in attesa.",
        "support": "strong",
        "memory_kind": "semantic_fact",
        "source_turn_ids": [5],
    }
    with patch(
        "core.brain.chat_client.chat",
        return_value=_Response('{"verdict":"SUPPORTED","source_turn_ids":[5]}'),
    ) as chat:
        assert Brain().audit_passive_memory_provenance(item, conversation) is not None
    audit_prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "[FRAME T5 | interpretazione accettata]" in audit_prompt
    assert "la grafia canonical_name deve coincidere" in audit_prompt


def test_passive_windows_are_overlapping_and_cover_the_tail():
    conversation = [
        {"seq": index, "role": "user", "content": f"Turno {index}"}
        for index in range(1, 30)
    ]
    windows = Brain._passive_extraction_windows(conversation)

    assert [(window[0]["seq"], window[-1]["seq"]) for window in windows] == [
        (1, 12),
        (9, 20),
        (17, 28),
        (25, 29),
    ]
    assert {row["seq"] for window in windows for row in window} == set(range(1, 30))


def test_windowed_extractor_has_no_global_six_fact_cap():
    conversation = [
        {
            "seq": index,
            "role": "user" if index % 2 else "assistant",
            "speaker": "Giulia" if index % 2 else "Marco",
            "content": f"Contenuto concreto del turno {index}.",
        }
        for index in range(1, 14)
    ]
    first = "\n".join(
        [
            "1. FORTE: [TIPO=FATTO; TURNI=1] Giulia preferisce il colore rosso.",
            "2. FORTE: [TIPO=FATTO; TURNI=3] Giulia usa un quaderno blu.",
            "3. FORTE: [TIPO=FATTO; TURNI=5] Giulia coltiva piante aromatiche.",
            "4. FORTE: [TIPO=FATTO; TURNI=7] Giulia studia la lingua spagnola.",
            "5. FORTE: [TIPO=FATTO; TURNI=9] Giulia visita spesso il museo civico.",
            "6. FORTE: [TIPO=FATTO; TURNI=11] Giulia prepara il pane in casa.",
        ]
    )
    second = "\n".join(
        [
            "1. FORTE: [TIPO=FATTO; TURNI=1,5] Giulia visita spesso il museo civico.",
            "2. FORTE: [TIPO=FATTO; TURNI=3] Giulia ascolta musica classica.",
            "3. FORTE: [TIPO=FATTO; TURNI=5] Giulia preferisce viaggiare in treno.",
        ]
    )
    with patch(
        "core.brain.chat_client.chat",
        side_effect=[_Response(first), _Response(second)],
    ) as chat:
        extracted = Brain().extract_passive_memories(conversation)

    assert chat.call_count == 2
    assert len(extracted) == 8
    museum = next(
        item for item in extracted if "museo civico" in item["content"]
    )
    assert museum["source_turn_ids"] == [9, 13]
    prompts = [
        call.kwargs["messages"][0]["content"] for call in chat.call_args_list
    ]
    assert "blocco 1/2" in prompts[0]
    assert "blocco 2/2" in prompts[1]
    assert "un solo predicato informativo principale" in prompts[0]


def test_windowed_extractor_maps_prefixed_local_ids_to_session_ids():
    conversation = [
        {
            "seq": 25,
            "role": "user",
            "speaker": "Joanna",
            "content": "Ho finito la mia prima sceneggiatura.",
        },
        {
            "seq": 26,
            "role": "assistant",
            "speaker": "Nate",
            "content": "Di quale genere è?",
        },
        {
            "seq": 27,
            "role": "user",
            "speaker": "Joanna",
            "content": "È un misto di dramma e romanticismo.",
        },
    ]
    response = _Response(
        "1. FORTE: [TIPO=FATTO; TURNI=T1,T3] "
        "La prima sceneggiatura di Joanna è un misto di dramma e romanticismo."
    )
    with patch("core.brain.chat_client.chat", return_value=response):
        extracted = Brain().extract_passive_memories(conversation)

    assert len(extracted) == 1
    assert extracted[0]["source_turn_ids"] == [25, 27]


def test_extractor_defers_invalid_sources_to_the_semantic_audit():
    response = _Response(
        "1. FORTE: [TIPO=FATTO; TURNI=T2] "
        "La prima sceneggiatura di Joanna è un misto di dramma e romanticismo."
    )
    with patch("core.brain.chat_client.chat", return_value=response):
        extracted = Brain().extract_passive_memories(_conversation())

    assert len(extracted) == 1
    assert extracted[0]["source_turn_ids"] == [26]
    assert extracted[0]["provenance_resolution"] == "deferred"


def test_parser_accepts_turnos_but_rejects_other_malformed_metadata():
    parsed = Brain._parse_passive_fact_line(
        "FORTE: [TIPO=FATTO; TURNOS=T1,T3] "
        "La prima sceneggiatura di Joanna è un misto di dramma e romanticismo."
    )
    assert parsed is not None
    assert parsed["source_turn_ids"] == [1, 3]
    assert not parsed["content"].startswith("[TIPO=")

    malformed = Brain._parse_passive_fact_line(
        "FORTE: [TIPO=FATTO; FONTI=T1] Joanna ha una sceneggiatura."
    )
    assert malformed is None


def test_passive_gate_never_rewrites_the_extracted_content():
    original = (
        "Joanna ha completato la sceneggiatura, un mix di dramma "
        "e romanticismo, venerdì scorso."
    )
    with patch(
        "core.validator.chat_client.chat",
        return_value=_Response("KEEP"),
    ):
        assert validate_passive_payload(original) == original

    with patch(
        "core.validator.chat_client.chat",
        return_value=_Response("JUNK"),
    ):
        assert validate_passive_payload(original) is None

    with patch(
        "core.validator.chat_client.chat",
        return_value=_Response("KEEP, ma la riscriverei"),
    ):
        assert validate_passive_payload(original) is None


def test_audit_rejects_unsupported_or_assistant_grounded_fact():
    unsupported = _Response(
        '{"verdict":"UNSUPPORTED","source_turn_ids":[]}'
    )
    with patch("core.brain.chat_client.chat", return_value=unsupported):
        assert (
            Brain().audit_passive_memory_provenance(
                _screenplay_item(),
                _conversation(),
            )
            is None
        )

    invalid_role = _Response(
        '{"verdict":"SUPPORTED","source_turn_ids":[26]}'
    )
    with patch("core.brain.chat_client.chat", return_value=invalid_role):
        assert (
            Brain().audit_passive_memory_provenance(
                _screenplay_item(),
                _conversation(),
            )
            is None
        )


def test_audit_can_repair_an_initially_invalid_source_role():
    item = _screenplay_item()
    item["source_turn_ids"] = [26]
    repaired = _Response(
        '{"verdict":"SUPPORTED","source_turn_ids":[25,27]}'
    )
    with patch("core.brain.chat_client.chat", return_value=repaired):
        audited = Brain().audit_passive_memory_provenance(item, _conversation())

    assert audited is not None
    assert audited["source_turn_ids"] == [25, 27]
    assert audited["provenance_audit"]["original_source_turn_ids"] == [26]
    assert audited["provenance_audit"]["repaired"] is True


def test_episode_may_preserve_both_sides_of_the_dialogue():
    item = {
        "content": "Joanna ha presentato la sceneggiatura e Nate ha chiesto di cosa parli.",
        "support": "strong",
        "memory_kind": "episode",
        "source_turn_ids": [25, 26],
    }
    response = _Response(
        '{"verdict":"SUPPORTED","source_turn_ids":[25,26]}'
    )
    with patch("core.brain.chat_client.chat", return_value=response) as chat:
        audited = Brain().audit_passive_memory_provenance(item, _conversation())

    assert audited is not None
    assert audited["source_turn_ids"] == [25, 26]
    prompt = chat.call_args.kwargs["messages"][0]["content"]
    assert "[T26 | ASSISTENTE | PARLANTE=Nate]" in prompt
    assert audited["provenance_audit"]["repaired"] is False


def test_audit_is_fail_closed_on_ambiguous_output():
    with patch(
        "core.brain.chat_client.chat",
        return_value=_Response("Probabilmente supportato."),
    ):
        assert (
            Brain().audit_passive_memory_provenance(
                _screenplay_item(),
                _conversation(),
            )
            is None
        )


if __name__ == "__main__":
    test_audit_repairs_incomplete_source_turn_union()
    test_audit_restores_the_owner_turn_that_resolves_an_anaphora()
    test_extractor_preserves_explicit_dataset_speaker_identity()
    test_extractor_and_auditor_receive_accepted_semantic_identity_and_modality()
    test_passive_windows_are_overlapping_and_cover_the_tail()
    test_windowed_extractor_has_no_global_six_fact_cap()
    test_windowed_extractor_maps_prefixed_local_ids_to_session_ids()
    test_extractor_defers_invalid_sources_to_the_semantic_audit()
    test_parser_accepts_turnos_but_rejects_other_malformed_metadata()
    test_passive_gate_never_rewrites_the_extracted_content()
    test_audit_rejects_unsupported_or_assistant_grounded_fact()
    test_audit_can_repair_an_initially_invalid_source_role()
    test_episode_may_preserve_both_sides_of_the_dialogue()
    test_audit_is_fail_closed_on_ambiguous_output()
    print("test_passive_provenance: OK")
