#!/usr/bin/env python3
"""Regressioni per frame semantico, identita' canonica e raw verbatim."""
import json
from unittest.mock import patch

from core.brain import Brain
from core.conversation_turns import ConversationTurnStore
from core.intent_router import Intent, classify
from core.semantic_turn import (
    arbitrate_routable_intent,
    SemanticTurnService,
    frame_bootstraps_owner_session,
    frame_blocks_passive_memory,
    frame_document_source,
    frame_requests_contextual_action,
    frame_requests_linguistic_response,
    frame_vetoes_contextual_action,
    gate_teaching_route,
    semantic_intent,
    trusted_teaching_session,
    trusted_deliberation_request,
    trusted_evidence_request,
    trusted_memory_retrieval_plan,
)
from core.web_search import answer_explicit_web_search


class FakeJSON:
    def __init__(self, docs):
        self.docs = docs

    def set(self, key, _path, value):
        self.docs[key] = value


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.docs = {}
        self.events = []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value
        return True

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping=None, **kwargs):
        values = dict(mapping or {})
        values.update(kwargs)
        self.hashes.setdefault(key, {}).update(values)
        return len(values)

    def xadd(self, stream, fields, **kwargs):
        self.events.append((stream, fields, kwargs))
        return f"1-{len(self.events)}"

    def json(self):
        return FakeJSON(self.docs)


def _correction_response(_prompt):
    return json.dumps({
        "interpreted_text": (
            "Correzione: il nome corretto dell'azienda e' Gio Style, non Joe Style; "
            "cerca sul web informazioni su Gio Style."
        ),
        "primary_intent": "WEB_SEARCH",
        "speech_acts": ["CORRECT_ENTITY", "REQUEST_WEB_SEARCH"],
        "entities": [{
            "observed_form": "Joe Style",
            "canonical_name": "Gio Style",
            "entity_type": "organization",
            "status": "explicit_correction",
            "evidence": "non Joe Style: il nome corretto e' Gio Style",
            "confidence": 0.99,
        }],
        "facts": [],
        "actions": [{"type": "web_search"}],
        "web_query": "Gio Style azienda",
        "preservation_mode": "semantic",
        "requires_clarification": False,
        "meaning_preserved": True,
        "confidence": 0.97,
    }, ensure_ascii=False)


def _teaching_frame(raw_text: str, *, evidence: str | None = None) -> dict:
    evidence = evidence if evidence is not None else raw_text
    return {
        "status": "interpreted",
        "raw_text": raw_text,
        "primary_intent": "TEACH",
        "speech_acts": ["INITIATE_TEACHING"],
        "confidence": 0.96,
        "requires_clarification": False,
        "teaching_session": {
            "recipient": "assistant",
            "goal": "knowledge_transfer",
            "interaction": "guided_session",
            "evidence": evidence,
            "evidence_grounded": evidence.casefold() in raw_text.casefold(),
            "confidence": 0.95,
        },
    }


def test_semantic_teaching_contract_can_open_teach_from_chat():
    raw = "Ti spiego come funziona ICMA2: ascoltami e fammi delle domande."
    frame = _teaching_frame(raw, evidence="ascoltami e fammi delle domande")

    contract = trusted_teaching_session(frame)

    assert contract is not None
    assert contract["recipient"] == "assistant"
    assert semantic_intent(frame) == "TEACH"
    assert arbitrate_routable_intent(
        frame,
        Intent.CHAT,
        allowed={"CHAT", "TEACH"},
    ) == "TEACH"
    assert gate_teaching_route(frame, Intent.TEACH) == "TEACH"


def test_third_party_explanation_cannot_open_teach():
    raw = (
        "Lui e' una guardia giurata: per fargli capire che non sei solo "
        "un assistente, spiegagli su quale azienda lavori."
    )
    frame = {
        "status": "interpreted",
        "raw_text": raw,
        "primary_intent": "CHAT",
        "speech_acts": ["ASK"],
        "confidence": 0.97,
        "requires_clarification": False,
        "teaching_session": {
            "recipient": "third_party",
            "goal": "explanation",
            "interaction": "single_turn",
            "evidence": "spiegagli su quale azienda lavori",
            "evidence_grounded": True,
            "confidence": 0.98,
        },
    }

    assert trusted_teaching_session(frame) is None
    assert gate_teaching_route(frame, Intent.TEACH) == "CHAT"
    assert arbitrate_routable_intent(
        frame,
        Intent.TEACH,
        allowed={"CHAT", "TEACH"},
    ) == "CHAT"


def test_teach_is_fail_closed_when_semantic_parser_falls_back():
    fallback = {
        "status": "fallback",
        "raw_text": "Ti racconto una cosa.",
        "primary_intent": "",
        "speech_acts": [],
        "confidence": 0.0,
    }

    assert trusted_teaching_session(fallback) is None
    assert gate_teaching_route(fallback, Intent.TEACH) == "CHAT"


def test_teaching_words_are_not_a_lexical_authorization():
    assert classify("Ti racconto una cosa.")[0] == Intent.CHAT
    assert classify("Posso spiegarti come funziona.")[0] == Intent.CHAT
    assert classify(
        "Spiegagli su quale azienda lavori, cosi' capisce meglio."
    )[0] == Intent.CHAT


def test_teach_requires_evidence_grounded_in_current_turn():
    frame = _teaching_frame(
        "Parliamo della manutenzione.",
        evidence="voglio insegnarti la manutenzione",
    )

    assert frame["teaching_session"]["evidence_grounded"] is False
    assert trusted_teaching_session(frame) is None
    assert semantic_intent(frame) == ""


def test_semantic_service_normalizes_grounded_teaching_contract():
    raw = "Ora ti insegno il ciclo di ICMA2: ascolta e poi fammi domande."

    def model(_prompt):
        return json.dumps({
            "interpreted_text": raw,
            "primary_intent": "TEACH",
            "speech_acts": ["INITIATE_TEACHING"],
            "entities": [],
            "facts": [],
            "actions": [],
            "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.96,
            "memory_disposition": "no_store",
            "teaching_session": {
                "recipient": "assistant",
                "goal": "knowledge_transfer",
                "interaction": "guided_session",
                "evidence": "ti insegno il ciclo di ICMA2",
                "confidence": 0.95,
            },
        }, ensure_ascii=False)

    frame = SemanticTurnService(FakeRedis(), model_call=model).interpret(raw)

    assert frame["schema_version"] == 7
    assert frame["teaching_session"]["evidence_grounded"] is True
    assert trusted_teaching_session(frame) is not None


def test_semantic_service_normalizes_explicit_deliberation_contract():
    raw = "Confronta quattro strategie diverse e mettile alla prova prima di scegliere."

    def model(_prompt):
        return json.dumps({
            "interpreted_text": raw,
            "primary_intent": "CHAT",
            "speech_acts": ["ASK", "REQUEST_DELIBERATION"],
            "entities": [], "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "semantic", "requires_clarification": False,
            "meaning_preserved": True, "confidence": 0.96,
            "memory_disposition": "no_store",
            "evidence_request": {
                "dependency": "none", "entities": [], "premises": [],
                "missing_facts": [], "acceptable_sources": [],
                "memory_only": False, "confidence": 0.95,
            },
            "deliberation_request": {
                "mode": "explicit",
                "problem": "Scegliere fra quattro strategie diverse dopo averle messe alla prova",
                "reason": "multiple_hypotheses",
                "alternatives_visible": True,
                "constraints": ["confrontare quattro strategie"],
                "evidence": "Confronta quattro strategie diverse",
                "confidence": 0.95,
            },
            "addressed_to_assistant": True,
            "address_relation": "direct_address", "address_confidence": 0.98,
        }, ensure_ascii=False)

    frame = SemanticTurnService(FakeRedis(), model_call=model).interpret(raw)

    contract = trusted_deliberation_request(frame)
    assert frame["schema_version"] == 7
    assert contract is not None
    assert contract["mode"] == "explicit"
    assert contract["evidence_grounded"] is True


def test_deliberation_is_fail_closed_without_grounded_evidence():
    frame = {
        "status": "interpreted", "primary_intent": "CHAT",
        "speech_acts": ["REQUEST_DELIBERATION"], "confidence": 0.98,
        "requires_clarification": False, "addressed_to_assistant": True,
        "deliberation_request": {
            "mode": "explicit", "problem": "Valutare alternative",
            "reason": "tradeoff", "alternatives_visible": True,
            "evidence": "", "evidence_grounded": False, "confidence": 0.98,
        },
    }
    assert trusted_deliberation_request(frame) is None


def test_suggested_deliberation_requires_visible_alternatives_and_high_confidence():
    frame = {
        "status": "interpreted", "primary_intent": "CHAT",
        "speech_acts": ["ASK"], "confidence": 0.90,
        "requires_clarification": False, "addressed_to_assistant": True,
        "deliberation_request": {
            "mode": "suggest", "problem": "Scegliere una strategia",
            "reason": "tradeoff", "alternatives_visible": True,
            "evidence": "quale strada conviene", "evidence_grounded": True,
            "confidence": 0.90,
        },
    }
    assert trusted_deliberation_request(frame) is not None
    frame["deliberation_request"]["alternatives_visible"] = False
    assert trusted_deliberation_request(frame) is None


def test_missing_required_evidence_blocks_deliberation():
    frame = {
        "status": "interpreted", "primary_intent": "CHAT",
        "speech_acts": ["REQUEST_DELIBERATION"], "confidence": 0.98,
        "requires_clarification": False, "addressed_to_assistant": True,
        "evidence_request": {
            "dependency": "required", "missing_facts": ["capacita' reale"],
        },
        "deliberation_request": {
            "mode": "explicit", "problem": "Scegliere fra due impianti",
            "reason": "tradeoff", "alternatives_visible": True,
            "evidence": "scegliere fra due impianti", "evidence_grounded": True,
            "confidence": 0.98,
        },
    }
    assert trusted_deliberation_request(frame) is None


def test_explicit_entity_correction_updates_history_and_passive_journal():
    redis = FakeRedis()
    brain = Brain()
    with brain.history_lock:
        brain._append_history_locked(
            "user", "Ieri dal cliente Joe Style abbiamo fatto una prova.", True
        )
        brain._append_history_locked(
            "assistant", "Ricordo la prova da Joe Style.", True
        )

    service = SemanticTurnService(redis, model_call=_correction_response)
    frame = service.interpret(
        "Il nome e' sbagliato: non Joe Style ma Gio Style. Cerca nel web.",
        recent_history=list(brain._conversation_history),
        memory_scope="personal",
    )

    assert semantic_intent(frame) == "WEB_SEARCH"
    assert frame["web_query"] == "Gio Style azienda"
    assert len(frame["canonicalizations"]) == 1
    assert service.registry.canonicalize("novita' da Joe Style", "personal") == (
        "novita' da Gio Style"
    )

    changed = brain.rewrite_entity_aliases(
        lambda value: service.registry.canonicalize(value, "personal")
    )
    assert changed == 2
    assert all("Gio Style" in row["content"] for row in brain._conversation_history)
    assert all("Joe Style" in row["raw_content"] for row in brain._conversation_history)
    pending = brain.passive_messages_after(0)
    assert all("Gio Style" in row["content"] for row in pending)
    assert any(event[1]["kind"] == "canonicalized" for event in redis.events)


def test_ordinary_entity_mention_never_creates_an_alias():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": "Oggi ho visitato Alfa Beta.",
            "primary_intent": "CHAT",
            "speech_acts": ["INFORM"],
            "entities": [{
                "observed_form": "Alfa Beta",
                "canonical_name": "Alfa Beta",
                "entity_type": "organization",
                "status": "mentioned",
                "evidence": "Alfa Beta",
                "confidence": 0.95,
            }],
            "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.96,
        })

    frame = SemanticTurnService(redis, model_call=model).interpret(
        "Oggi ho visitato Alfa Beta.", memory_scope="personal"
    )
    assert frame["canonicalizations"] == []
    assert redis.hashes == {}
    assert redis.events == []


def test_memory_retrieval_plan_is_semantic_and_grounded_in_frame_entities():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": "Cosa ricordi di Lucy Plast?",
            "primary_intent": "SEARCH",
            "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
            "entities": [{
                "observed_form": "Lucy Plast",
                "canonical_name": "Lucy Plast",
                "entity_type": "organization",
                "status": "mentioned",
                "evidence": "Lucy Plast",
                "confidence": 0.99,
            }],
            "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.97,
            "memory_disposition": "no_store",
            "memory_retrieval": {
                "needed": True,
                "focus": [
                    {"entity": "Lucy Plast", "role": "focus", "relevance": 0.98},
                    # Un nome che Gemma non ha ancorato in entities non puo'
                    # aprire uno schema per conto proprio.
                    {"entity": "Eurostampi", "role": "comparison", "relevance": 0.9},
                ],
                "relation": "panoramica aziendale",
                "evidence_goal": "overview",
                "confidence": 0.96,
            },
        }, ensure_ascii=False)

    frame = SemanticTurnService(redis, model_call=model).interpret(
        "Cosa ricordi di Lucy Plast?", memory_scope="personal"
    )

    assert frame["memory_retrieval"]["focus"] == [{
        "entity": "Lucy Plast",
        "role": "focus",
        "relevance": 0.98,
    }]
    assert trusted_memory_retrieval_plan(frame)["evidence_goal"] == "overview"


def test_trusted_memory_plan_preserves_explicit_no_retrieval_decision():
    frame = {
        "status": "interpreted",
        "confidence": 0.95,
        "memory_retrieval": {
            "needed": False,
            "focus": [{"entity": "Lucy Plast", "role": "context", "relevance": 0.2}],
            "relation": "menzione incidentale",
            "evidence_goal": "other",
            "confidence": 0.94,
        },
    }

    plan = trusted_memory_retrieval_plan(frame)
    assert plan is not None
    assert plan["needed"] is False


def test_evidence_request_is_semantic_and_cannot_introduce_unknown_entities():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": (
                "Noi di Lucy Plast vorremmo essere piu' produttivi al pari di "
                "Eurostampi: cosa potremmo imparare?"
            ),
            "primary_intent": "SEARCH",
            "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
            "entities": [
                {
                    "observed_form": "Lucy Plast",
                    "canonical_name": "Lucy Plast",
                    "entity_type": "organization",
                    "status": "mentioned",
                    "evidence": "Lucy Plast",
                    "confidence": 0.99,
                },
                {
                    "observed_form": "Eurostampi",
                    "canonical_name": "Eurostampi",
                    "entity_type": "organization",
                    "status": "mentioned",
                    "evidence": "Eurostampi",
                    "confidence": 0.99,
                },
            ],
            "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.97,
            "memory_disposition": "no_store",
            "memory_retrieval": {
                "needed": True,
                "focus": [
                    {"entity": "Lucy Plast", "role": "focus", "relevance": 0.9},
                    {"entity": "Eurostampi", "role": "comparison", "relevance": 0.9},
                ],
                "relation": "confronto di produttivita'",
                "evidence_goal": "comparison",
                "confidence": 0.95,
            },
            "evidence_request": {
                "dependency": "optional",
                "entities": ["Eurostampi", "Azienda inventata"],
                "premises": [
                    "L'utente considera Eurostampi un riferimento di produttivita'"
                ],
                "missing_facts": [
                    "processi o indicatori che rendono Eurostampi piu' produttiva"
                ],
                "acceptable_sources": ["current_user", "web", "database_segreto"],
                "memory_only": False,
                "confidence": 0.96,
            },
        }, ensure_ascii=False)

    frame = SemanticTurnService(redis, model_call=model).interpret(
        "Noi di Lucy Plast vorremmo essere piu' produttivi al pari di Eurostampi: "
        "cosa potremmo imparare?",
        memory_scope="personal",
    )

    request = trusted_evidence_request(frame)
    assert request is not None
    assert request["dependency"] == "optional"
    assert request["entities"] == ["Eurostampi"]
    assert request["acceptable_sources"] == ["current_user", "web"]
    assert request["premises"] == [
        "L'utente considera Eurostampi un riferimento di produttivita'"
    ]


def test_premise_sufficient_analogy_does_not_create_a_knowledge_gap():
    frame = {
        "status": "interpreted",
        "confidence": 0.98,
        "evidence_request": {
            "dependency": "none",
            "entities": [],
            "premises": ["Peroni e Raffo hanno entrambe una birra bionda"],
            "missing_facts": [],
            "acceptable_sources": [],
            "memory_only": False,
            "confidence": 0.97,
        },
    }

    request = trusted_evidence_request(frame)
    assert request is not None
    assert request["dependency"] == "none"


def test_elliptical_web_authorization_is_resolved_from_recent_dialogue():
    redis = FakeRedis()
    captured = {}

    def model(prompt):
        captured["prompt"] = prompt
        return json.dumps({
            "interpreted_text": "Non lo so, controlla nel web.",
            "primary_intent": "WEB_SEARCH",
            "speech_acts": ["REQUEST_WEB_SEARCH"],
            "entities": [{
                "observed_form": "Eurostampi",
                "canonical_name": "Eurostampi",
                "entity_type": "organization",
                "status": "resolved",
                "evidence": "dialogo recente",
                "confidence": 0.95,
            }],
            "facts": [], "actions": [],
            "web_query": "Eurostampi processi indicatori produttivita'",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.96,
            "memory_disposition": "no_store",
            "memory_retrieval": {
                "needed": False, "focus": [], "relation": "",
                "evidence_goal": "other", "confidence": 0.95,
            },
            "evidence_request": {
                "dependency": "none", "entities": [], "premises": [],
                "missing_facts": [], "acceptable_sources": [],
                "memory_only": False, "confidence": 0.95,
            },
        }, ensure_ascii=False)

    history = [{
        "role": "assistant",
        "content": (
            "Non ho dati sui processi e sugli indicatori di produttivita' di "
            "Eurostampi. Puoi dirmeli tu oppure posso cercarli sul Web."
        ),
        "memory_scope": "personal",
    }]
    frame = SemanticTurnService(redis, model_call=model).interpret(
        "Non lo so, controlla nel web.",
        recent_history=history,
        memory_scope="personal",
    )

    assert frame["primary_intent"] == "WEB_SEARCH"
    assert frame["web_query"] == "Eurostampi processi indicatori produttivita'"
    assert "Eurostampi" in captured["prompt"]
    assert "dialogo recente" in captured["prompt"]


def test_explicit_web_service_uses_semantic_query_and_persists_external_source():
    class FakeBrain:
        def __init__(self):
            self.frame_seen = None

        def extract_search_query(self, _text, semantic_frame=None):
            self.frame_seen = semantic_frame
            return semantic_frame["web_query"]

        def extract_query_fallback(self, query):
            raise AssertionError(f"fallback inatteso per {query}")

        def summarize_web_results(self, results, query):
            assert results[0]["title"] == "Eurostampi"
            return f"Sintesi verificabile per {query}."

    class FakeMemory:
        def __init__(self):
            self.saved = None

        def save_memory(self, **kwargs):
            self.saved = kwargs
            return "web-memory-1"

    frame = {
        "status": "interpreted",
        "confidence": 0.97,
        "primary_intent": "WEB_SEARCH",
        "speech_acts": ["REQUEST_WEB_SEARCH"],
        "web_query": "Eurostampi processi indicatori produttivita'",
    }
    brain = FakeBrain()
    memory = FakeMemory()
    result = answer_explicit_web_search(
        "Non lo so, controlla nel web.",
        brain,
        memory,
        semantic_frame=frame,
        online_check=lambda: True,
        search_fn=lambda query: [{
            "title": "Eurostampi",
            "url": "https://example.test/eurostampi",
            "body": query,
        }],
    )

    assert result["status"] == "ok"
    assert result["query"] == frame["web_query"]
    assert brain.frame_seen is frame
    assert memory.saved["source"] == "web"
    assert memory.saved["final_fields"] == {"requires_verification": True}


def test_resolved_entity_is_projected_only_into_the_current_turn():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": "Torniamo a parlare di Geostyle.",
            "primary_intent": "WEB_SEARCH",
            "speech_acts": ["ASK", "REQUEST_WEB_SEARCH"],
            "entities": [{
                "observed_form": "Geostyle",
                "canonical_name": "Gio Style",
                "entity_type": "organization",
                "status": "resolved",
                "evidence": "Il contesto precedente identifica Geostyle come Gio Style",
                "confidence": 1.0,
            }],
            "facts": [],
            "actions": [],
            "web_query": "Geostyle materiali plastici",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.99,
            "memory_disposition": "no_store",
        }, ensure_ascii=False)

    service = SemanticTurnService(redis, model_call=model)
    frame = service.interpret(
        "Torniamo a parlare di Geostyle.", memory_scope="personal"
    )

    assert frame["raw_text"] == "Torniamo a parlare di Geostyle."
    assert frame["interpreted_text"] == "Torniamo a parlare di Gio Style."
    assert frame["web_query"] == "Gio Style materiali plastici"
    assert frame["canonical_projections"] == [{
        "observed_form": "Geostyle",
        "canonical_name": "Gio Style",
        "entity_type": "organization",
        "confidence": 1.0,
        "scope": "current_turn",
    }]
    assert frame["canonicalizations"] == []
    assert service.registry.canonicalize("Geostyle", "personal") == "Geostyle"
    assert redis.hashes == {}
    assert redis.events == []


def test_anaphora_is_not_projected_as_a_canonical_name():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            # Riproduce anche la variante peggiore: il modello ha già riscritto
            # l'anafora prima che intervenga la proiezione deterministica.
            "interpreted_text": "Non l'avrei fatta riparare da Gio Style.",
            "primary_intent": "CHAT",
            "speech_acts": ["INFORM"],
            "entities": [{
                "observed_form": "loro",
                "canonical_name": "Gio Style",
                "entity_type": "organization",
                "status": "resolved",
                "evidence": "contextual reference to a workshop",
                "confidence": 0.9,
            }],
            "facts": [{
                "claim": "L'utente portera' l'auto da un'altra officina",
                "modality": "planned",
                "durability": "reusable",
            }],
            "actions": [],
            "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.95,
            "memory_disposition": "candidate",
        }, ensure_ascii=False)

    service = SemanticTurnService(redis, model_call=model)
    frame = service.interpret(
        "Non l'avrei fatta riparare da loro.", memory_scope="personal"
    )

    assert frame["interpreted_text"] == "Non l'avrei fatta riparare da loro."
    assert frame["canonical_projections"] == []
    assert frame["canonicalizations"] == []
    assert redis.hashes == {}


def test_anaphora_labelled_as_explicit_correction_is_never_learned():
    """Il percorso durevole e' il piu' pericoloso: l'alias appreso e' permanente
    e riscrive ogni turno futuro. Se il modello sbaglia etichetta e dichiara
    CORRECT_ENTITY su un'anafora, la guardia di grafia deve fermarlo."""
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": "Gio Style non l'ha fatta.",
            "primary_intent": "CHAT",
            "speech_acts": ["CORRECT_ENTITY"],
            "entities": [{
                "observed_form": "loro",
                "canonical_name": "Gio Style",
                "entity_type": "organization",
                "status": "explicit_correction",
                "evidence": "l'utente chiarisce a chi si riferiva",
                "confidence": 0.95,
            }],
            "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.95,
            "memory_disposition": "no_store",
        }, ensure_ascii=False)

    service = SemanticTurnService(redis, model_call=model)
    frame = service.interpret("loro non l'ha fatta.", memory_scope="personal")

    assert frame["canonicalizations"] == []
    assert redis.hashes == {}
    assert redis.events == []
    # Il turno successivo non deve trovare "loro" trasformato in un'azienda.
    assert service.registry.canonicalize(
        "Ho parlato con loro ieri.", "personal"
    ) == "Ho parlato con loro ieri."


def test_verbatim_mode_keeps_raw_text_even_if_model_rewrites_it():
    redis = FakeRedis()

    def model(_prompt):
        return json.dumps({
            "interpreted_text": "testo cambiato",
            "primary_intent": "DICTATION",
            "speech_acts": ["DICTATE"],
            "entities": [], "facts": [], "actions": [], "web_query": "",
            "preservation_mode": "verbatim",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.99,
        })

    raw = "Dettatura: lascia EsAttamente QUESTO testo."
    frame = SemanticTurnService(redis, model_call=model).interpret(raw)
    assert frame["interpreted_text"] == raw


def test_spelled_variant_reuses_the_confirmed_canonical_format():
    redis = FakeRedis()
    service = SemanticTurnService(redis, model_call=_correction_response)
    first = service.interpret(
        "Il nome corretto e' Gio Style, non Joe Style.", memory_scope="personal"
    )
    assert first["canonicalizations"][0]["canonical_name"] == "Gio Style"

    def spelled_model(_prompt):
        payload = json.loads(_correction_response(_prompt))
        payload["primary_intent"] = "EXECUTE"
        payload["speech_acts"] = ["CORRECT_ENTITY", "REQUEST_ACTION"]
        payload["entities"][0]["canonical_name"] = "G-I-O Style"
        return json.dumps(payload)

    service._model_call = spelled_model
    second = service.interpret(
        "Correggilo da Joe Style a G-I-O Style.", memory_scope="personal"
    )
    assert second["canonicalizations"][0]["canonical_name"] == "Gio Style"
    assert semantic_intent(second) == "CHAT"


def test_web_query_uses_shared_frame_without_second_llm_interpretation():
    brain = Brain()
    frame = {
        "status": "interpreted",
        "confidence": 0.98,
        "primary_intent": "WEB_SEARCH",
        "speech_acts": ["REQUEST_WEB_SEARCH"],
        "web_query": "Gio Style stampaggio plastica",
    }
    with patch("core.brain.chat_client.chat", side_effect=AssertionError("no second LLM")):
        assert brain.extract_search_query(
            "fai la ricerca", semantic_frame=frame
        ) == "Gio Style stampaggio plastica"


def test_semantic_routing_requires_a_matching_speech_act():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "primary_intent": "SAVE_MEMORY",
        "speech_acts": ["INFORM"],
    }
    assert semantic_intent(frame) == ""
    frame["speech_acts"] = ["REQUEST_SAVE"]
    assert semantic_intent(frame) == "SAVE_MEMORY"

    frame.update({
        "primary_intent": "EXECUTE",
        "speech_acts": ["CORRECT_ENTITY", "REQUEST_ACTION"],
    })
    assert semantic_intent(frame) == "CHAT"


def test_turn_archive_preserves_raw_and_keeps_interpretation_additive():
    redis = FakeRedis()
    store = ConversationTurnStore(redis)
    store.persist({
        "turn_ref": "conv:1",
        "conversation_id": "conv",
        "seq": 1,
        "role": "user",
        "content": "Visita a Gio Style",
        "raw_content": "Visita a Joe Style",
        "trusted": True,
        "observed_at": 1.0,
        "segment_id": 1,
        "memory_scope": "personal",
        "semantic_frame": {"turn_id": "t1"},
    })
    doc = redis.docs["euri:turn:conv:1"]
    assert doc["content"] == "Visita a Joe Style"
    assert doc["interpreted_content"] == "Visita a Gio Style"
    assert doc["semantic_frame"]["turn_id"] == "t1"


def test_web_regex_does_not_match_cerca_inside_ricerca():
    intent, _ = classify("questa ricerca nel web crea problemi alla conversazione")
    assert intent == Intent.CHAT


def test_meta_status_is_ephemeral_chat_and_vetoes_fuzzy_action():
    def model(_prompt):
        return json.dumps({
            "interpreted_text": (
                "Ho riavviato Euri dopo una piccola modifica al codice della struttura."
            ),
            "primary_intent": "",
            "speech_acts": ["INFORM"],
            "entities": [],
            "facts": [{
                "claim": "Euri e' stata riavviata dopo una modifica al codice",
                "type": "change",
                "modality": "asserted",
                "durability": "session_only",
            }],
            "actions": [],
            "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.98,
            "memory_disposition": "ephemeral",
            "memory_reason": "stato temporaneo dell'assistente",
        })

    frame = SemanticTurnService(FakeRedis(), model_call=model).interpret(
        "Ho riavviato Euri dopo una piccola modifica al codice della struttura."
    )
    assert frame["primary_intent"] == "CHAT"
    assert semantic_intent(frame) == "CHAT"
    assert frame_blocks_passive_memory(frame)
    assert frame_vetoes_contextual_action(frame)


def test_reusable_industrial_facts_override_an_incoherent_ephemeral_label():
    def model(_prompt):
        return json.dumps({
            "interpreted_text": (
                "Il materiale ha probabilmente un MFI basso; nella produzione futura "
                "useremo il grado corretto e la macchina della prova non era ottimizzata."
            ),
            "primary_intent": "CHAT",
            "speech_acts": ["INFORM"],
            "entities": [],
            "facts": [{
                "claim": "Il materiale ha un MFI basso",
                "modality": "probable",
                "durability": "reusable",
            }, {
                "claim": "Nella produzione futura verra' usato il grado corretto",
                "modality": "planned",
                "durability": "reusable",
            }, {
                "claim": "La macchina usata nella prova non era ottimizzata",
                "modality": "asserted",
                "durability": "reusable",
            }],
            "actions": [],
            "web_query": "",
            "preservation_mode": "semantic",
            "requires_clarification": False,
            "meaning_preserved": True,
            "confidence": 0.99,
            "memory_disposition": "ephemeral",
            "memory_reason": "riflessione tecnica spontanea",
        }, ensure_ascii=False)

    raw = (
        "Il materiale ha probabilmente un MFI basso; nella produzione futura "
        "useremo il grado corretto e la macchina della prova non era ottimizzata."
    )
    frame = SemanticTurnService(FakeRedis(), model_call=model).interpret(raw)

    assert frame["memory_disposition"] == "candidate"
    assert frame["memory_reason"] == "almeno un fatto riutilizzabile nel frame"
    assert [fact["modality"] for fact in frame["facts"]] == [
        "probable", "planned", "asserted",
    ]
    assert all(fact["durability"] == "reusable" for fact in frame["facts"])
    assert not frame_blocks_passive_memory(frame)


def test_explicit_action_is_never_vetoed_by_contextual_guard():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "primary_intent": "EXECUTE",
        "speech_acts": ["REQUEST_ACTION"],
        "actions": [{
            "effect": "read system state",
            "target": "gpu",
            "capability_class": "executor.gpu_usage",
            "effect_scope": "read",
            "polarity": "requested",
        }],
        "memory_disposition": "no_store",
    }
    assert not frame_vetoes_contextual_action(frame)


def test_grounded_request_action_survives_empty_primary_intent():
    # Regressione 05/08 17:00: "Crealo in formato Word" non raggiungeva il
    # controller nonostante REQUEST_ACTION e un effetto completo.
    frame = {
        "status": "interpreted",
        "confidence": 1.0,
        "requires_clarification": False,
        "primary_intent": "",
        "speech_acts": ["REQUEST_ACTION"],
        "actions": [{
            "effect": "Creazione di un documento in formato .docx",
            "target": "document",
            "capability_class": "document_generation",
            "effect_scope": "write",
            "polarity": "requested",
            "source_kind": "active_document",
            "source_scope": "unspecified",
        }],
    }
    assert semantic_intent(frame) == ""
    assert frame_requests_contextual_action(frame)
    assert not frame_vetoes_contextual_action(frame)
    assert frame_document_source(frame) == {"source_mode": "active_document"}


def test_conversation_document_source_is_semantic_and_scoped():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["REQUEST_ACTION"],
        "actions": [{
            "effect": "Creare una relazione Word dalla discussione corrente",
            "target": "documento Word",
            "capability_class": "document_creation",
            "effect_scope": "write",
            "polarity": "requested",
            "source_kind": "recent_conversation",
            "source_scope": "current_thread",
        }],
    }
    assert frame_requests_contextual_action(frame)
    assert frame_document_source(frame) == {
        "source_mode": "recent_conversation",
        "source_scope": "current_thread",
    }


def test_conversational_imperative_does_not_request_a_tool():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "ACTION_REASONING",
        "speech_acts": ["ASK", "REQUEST_ACTION"],
        "actions": [{
            "effect": "Descrivere capacità, struttura e limiti di Euri",
            "target": "risposta conversazionale",
            "capability_class": "response_generation",
            "effect_scope": "response",
            "polarity": "requested",
        }],
    }
    assert semantic_intent(frame) == ""
    assert not frame_requests_contextual_action(frame)
    assert frame_requests_linguistic_response(frame)
    assert frame_vetoes_contextual_action(frame)


def test_linguistic_response_does_not_cover_a_mixed_operational_action():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["REQUEST_ACTION"],
        "actions": [{
            "effect": "Presentare il progetto",
            "target": "risposta conversazionale",
            "capability_class": "response_generation",
            "effect_scope": "response",
            "polarity": "requested",
        }, {
            "effect": "Creare il file della presentazione",
            "target": "documento Word",
            "capability_class": "document_generation",
            "effect_scope": "write",
            "polarity": "requested",
        }],
    }
    assert frame_requests_contextual_action(frame)
    assert not frame_requests_linguistic_response(frame)


def test_fact_correction_without_document_source_cannot_start_compose_tool():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["CORRECT_FACT", "REQUEST_ACTION"],
        "actions": [{
            "effect": "Correggere il nome dell'azienda",
            "target": "informazione sull'azienda",
            "capability_class": "document_revision",
            "effect_scope": "write",
            "polarity": "requested",
            "source_kind": "unspecified",
        }],
    }
    assert semantic_intent(frame) == "CHAT"
    assert not frame_requests_contextual_action(frame)
    assert frame_vetoes_contextual_action(frame)


def test_fact_correction_of_active_document_remains_operational():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["CORRECT_FACT", "REQUEST_ACTION"],
        "actions": [{
            "effect": "Correggere il nome dell'azienda nel file attivo",
            "target": "documento attivo",
            "capability_class": "document_revision",
            "effect_scope": "write",
            "polarity": "requested",
            "source_kind": "active_document",
        }],
    }
    assert semantic_intent(frame) == "EXECUTE"
    assert frame_requests_contextual_action(frame)
    assert not frame_vetoes_contextual_action(frame)


def test_semantic_frame_cannot_revoke_an_explicit_save_command():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["CORRECT_FACT", "REQUEST_ACTION", "REQUEST_SAVE"],
        "actions": [{
            "effect": "Correggere informazioni generiche",
            "target": "informazione",
            "capability_class": "document_revision",
            "effect_scope": "write",
            "polarity": "requested",
            "source_kind": "unspecified",
        }],
    }
    assert arbitrate_routable_intent(
        frame,
        "SAVE_MEMORY",
        allowed={"CHAT", "SAVE_MEMORY", "EXECUTE"},
    ) == "SAVE_MEMORY"


def test_negated_tool_request_cannot_become_execute():
    frame = {
        "status": "interpreted",
        "confidence": 1.0,
        "requires_clarification": False,
        "primary_intent": "EXECUTE",
        "speech_acts": ["REQUEST_ACTION"],
        "actions": [{
            "effect": "Eseguire strumenti",
            "target": "strumenti di Euri",
            "capability_class": "tool_use",
            "effect_scope": "external",
            "polarity": "negated",
        }],
    }
    assert semantic_intent(frame) == ""
    assert not frame_requests_contextual_action(frame)
    assert frame_vetoes_contextual_action(frame)


def test_ungrounded_action_label_cannot_hijack_a_memory_answer():
    # Riproduce la forma strutturale del frame reale: etichetta operativa ma
    # nessun effetto/target/capability rappresentato.
    frame = {
        "status": "interpreted",
        "confidence": 1.0,
        "requires_clarification": False,
        "primary_intent": "ACTION_REASONING",
        "speech_acts": ["INFORM", "REQUEST_ACTION"],
        "actions": [],
    }
    assert semantic_intent(frame) == ""
    assert frame_vetoes_contextual_action(frame)


def test_memory_answer_is_search_not_an_operational_effect():
    frame = {
        "status": "interpreted",
        "confidence": 0.98,
        "requires_clarification": False,
        "primary_intent": "SEARCH",
        "speech_acts": ["ASK", "REQUEST_MEMORY_SEARCH"],
        "actions": [],
    }
    assert semantic_intent(frame) == "SEARCH"
    assert frame_vetoes_contextual_action(frame)


def test_shared_frame_routes_natural_remember_request_without_magic_phrase():
    frame = {
        "status": "interpreted",
        "confidence": 0.98,
        "requires_clarification": False,
        "primary_intent": "SAVE_MEMORY",
        "speech_acts": ["INFORM", "CORRECT_FACT", "REQUEST_SAVE"],
    }
    allowed = {
        "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
        "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "TRANSLATE", "DICTATION",
    }
    assert arbitrate_routable_intent(
        frame, Intent.CHAT, allowed=allowed
    ) == "SAVE_MEMORY"
    # Il frame non puo' scavalcare una route mutante fuori dal perimetro.
    assert arbitrate_routable_intent(
        frame, "SHUTDOWN", allowed=allowed
    ) == "SHUTDOWN"
    frame["confidence"] = 0.40
    assert arbitrate_routable_intent(
        frame, Intent.CHAT, allowed=allowed
    ) == "CHAT"


def test_owner_bootstrap_requires_direct_high_confidence_address():
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "addressed_to_assistant": True,
        "address_relation": "direct_address",
        "address_confidence": 0.97,
    }
    assert frame_bootstraps_owner_session(frame)
    frame["address_relation"] = "direct_followup"
    assert not frame_bootstraps_owner_session(frame)
    frame["address_relation"] = "direct_address"
    frame["address_confidence"] = 0.80
    assert not frame_bootstraps_owner_session(frame)


def test_pre_gate_frame_does_not_persist_corrections_until_accepted():
    redis = FakeRedis()
    service = SemanticTurnService(redis, model_call=_correction_response)
    frame = service.interpret(
        "Il nome e' sbagliato: non Joe Style ma Gio Style. Cerca nel web.",
        memory_scope="personal",
        session_bootstrap=True,
        persist_corrections=False,
    )
    assert frame["canonicalizations"] == []
    assert redis.hashes == {}
    assert redis.events == []

    service.commit_precomputed(frame)
    assert len(frame["canonicalizations"]) == 1
    assert redis.hashes
    assert redis.events


if __name__ == "__main__":
    test_semantic_teaching_contract_can_open_teach_from_chat()
    test_third_party_explanation_cannot_open_teach()
    test_teach_is_fail_closed_when_semantic_parser_falls_back()
    test_teaching_words_are_not_a_lexical_authorization()
    test_teach_requires_evidence_grounded_in_current_turn()
    test_semantic_service_normalizes_grounded_teaching_contract()
    test_semantic_service_normalizes_explicit_deliberation_contract()
    test_deliberation_is_fail_closed_without_grounded_evidence()
    test_suggested_deliberation_requires_visible_alternatives_and_high_confidence()
    test_missing_required_evidence_blocks_deliberation()
    test_explicit_entity_correction_updates_history_and_passive_journal()
    test_ordinary_entity_mention_never_creates_an_alias()
    test_memory_retrieval_plan_is_semantic_and_grounded_in_frame_entities()
    test_trusted_memory_plan_preserves_explicit_no_retrieval_decision()
    test_evidence_request_is_semantic_and_cannot_introduce_unknown_entities()
    test_premise_sufficient_analogy_does_not_create_a_knowledge_gap()
    test_elliptical_web_authorization_is_resolved_from_recent_dialogue()
    test_explicit_web_service_uses_semantic_query_and_persists_external_source()
    test_resolved_entity_is_projected_only_into_the_current_turn()
    test_anaphora_is_not_projected_as_a_canonical_name()
    test_verbatim_mode_keeps_raw_text_even_if_model_rewrites_it()
    test_spelled_variant_reuses_the_confirmed_canonical_format()
    test_web_query_uses_shared_frame_without_second_llm_interpretation()
    test_semantic_routing_requires_a_matching_speech_act()
    test_turn_archive_preserves_raw_and_keeps_interpretation_additive()
    test_web_regex_does_not_match_cerca_inside_ricerca()
    test_meta_status_is_ephemeral_chat_and_vetoes_fuzzy_action()
    test_reusable_industrial_facts_override_an_incoherent_ephemeral_label()
    test_explicit_action_is_never_vetoed_by_contextual_guard()
    test_grounded_request_action_survives_empty_primary_intent()
    test_conversation_document_source_is_semantic_and_scoped()
    test_conversational_imperative_does_not_request_a_tool()
    test_negated_tool_request_cannot_become_execute()
    test_ungrounded_action_label_cannot_hijack_a_memory_answer()
    test_memory_answer_is_search_not_an_operational_effect()
    test_shared_frame_routes_natural_remember_request_without_magic_phrase()
    test_owner_bootstrap_requires_direct_high_confidence_address()
    test_pre_gate_frame_does_not_persist_corrections_until_accepted()
    print("OK — semantic turn")
