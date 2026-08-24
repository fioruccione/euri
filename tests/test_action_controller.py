"""Regressioni del ponte generale intenzione→azione."""

from __future__ import annotations

import json
import sys
import threading
import types
from dataclasses import replace as dataclass_replace
from datetime import datetime
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch

from agent.executor import SandboxGuard, ToolResult, ToolSpec
from core.action_controller import (
    ActionAuthority,
    ActionCapability,
    ActionController,
    ActionDisposition,
    ActionEffect,
    ActionProposal,
    has_explicit_agenda_authority,
    looks_actionable,
)
from core.brain import Brain
from core import llm_classifier
from core.intent_router import Intent
from core.memory_manager import MemoryManager


def _stub_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _HardwareStub:
    pass


class _SpeakerVerdict(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    INDETERMINATE = "indeterminate"


# Gli adapter sono esercitati via __new__: nessun backend audio/video nativo.
_stub_module("voice.audio_io", AudioCapture=_HardwareStub, play_audio=lambda *_a, **_k: None)
_stub_module("voice.vad", VAD=_HardwareStub)
_stub_module("voice.stt", STT=_HardwareStub)
_stub_module(
    "voice.tts",
    TTS=_HardwareStub,
    split_for_speech=lambda text, **_kwargs: [text],
)
_stub_module("voice.visual_gate", VisualGate=_HardwareStub)
_stub_module("voice.face_auth", FaceAuth=_HardwareStub)
_stub_module(
    "voice.speaker_auth",
    SpeakerAuth=_HardwareStub,
    SpeakerVerdict=_SpeakerVerdict,
    ENROLL_UTTERANCES=3,
)

from voice_daemon import VoiceDaemon, _should_try_contextual_action


POSEIDON_ID = "f621c34c-710a-48b8-a360-5eb527d73d13"
HARDWARE_ID = "7bd10b48-b513-42d9-9b2d-1f41b35249b5"


class _Chat:
    def __init__(self, payload):
        self.payloads = list(payload) if isinstance(payload, list) else [payload]
        self.index = 0

    def chat(self, **_kwargs):
        payload = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        return SimpleNamespace(
            message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        )


class _Memory:
    def __init__(self):
        self.todos = [
            {"id": POSEIDON_ID, "content": "Provare il blend Poseidon", "_due_at": None},
            {"id": HARDWARE_ID, "content": "Checkpoint interocezione hardware", "_due_at": None},
        ]
        self.completed = []
        self.suspended = []
        self.rescheduled = []
        self.logged = []
        self.rag_ctx = None

    def get_pending_todos(self):
        return list(self.todos)

    def get_last_euri_turn(self):
        return "Hai un impegno scaduto: provare il blend Poseidon."

    def complete_todo(self, todo_id):
        self.completed.append(todo_id)
        return True

    def suspend_todo(self, todo_id):
        self.suspended.append(todo_id)
        return True

    def reschedule_todo(self, todo_id, due):
        self.rescheduled.append((todo_id, due))
        return True

    def log_conversation(self, role, text):
        self.logged.append((role, text))

    def set_last_rag_ctx(self, value):
        self.rag_ctx = value


class _Brain:
    def __init__(self):
        self.responses = []
        self.injections = []

    @staticmethod
    def complete_todo_response(content):
        return f"Fatto. '{content}' segnato come completato."

    def inject_tool_result(self, *args):
        self.injections.append(args)

    def respond(self, text, *, context="", **_kwargs):
        self.responses.append((text, context))
        return (
            "Il controllo mostra 7.8 GiB liberi. Questo dato è solo un sottopasso: "
            "per rispondere alla domanda originale bisogna valutare anche il flusso del codice."
        )


class _Executor:
    def __init__(self, contextual=None):
        self.contextual = contextual or []
        self.stop_event = threading.Event()
        self.calls = []

    def get_contextual_capabilities(self):
        return list(self.contextual)

    def execute(self, call):
        self.calls.append(call)
        return ToolResult(True, "GPU libera: 7.8 GiB")

    @staticmethod
    def resolve_document_format(_requested, _text):
        return "docx"


class _PulseRedis:
    def __init__(self):
        self.events = []

    def xadd(self, stream, fields, **_kwargs):
        event_id = f"{len(self.events) + 1}-0"
        self.events.append((stream, event_id, fields))
        return event_id


def _daemon(payload, *, contextual=None):
    daemon = VoiceDaemon.__new__(VoiceDaemon)
    daemon.memory = _Memory()
    daemon.brain = _Brain()
    daemon.executor = _Executor(contextual)
    daemon.action_controller = ActionController(chat=_Chat(payload), model="fake")
    daemon._brain_lock = threading.Lock()
    daemon.r = object()
    daemon._pending_action = None
    daemon._pending_reschedule = None
    daemon._build_context = lambda _text: "[contesto conversazionale]"
    daemon._augment_context_by_strategy = lambda _text, context: context
    daemon.spoken = []
    daemon._speak = daemon.spoken.append
    return daemon


def test_policy_boundaries():
    target = "todo-1"
    reversible = ActionCapability(
        "agenda.complete", "chiude", ActionEffect.REVERSIBLE,
        target_required=True, allowed_target_ids=frozenset({target}),
    )
    external = ActionCapability("send.mail", "invia", ActionEffect.EXTERNAL)
    readonly = ActionCapability("gpu.read", "legge", ActionEffect.READ_ONLY)
    local_write = ActionCapability(
        "executor.build_computational_tool",
        "costruisce uno strumento temporaneo",
        ActionEffect.LOCAL_WRITE,
    )
    controller = ActionController(chat=_Chat({}), model="fake")

    explicit = ActionProposal(
        "agenda.complete", {}, target, ActionAuthority.USER_EXPLICIT, 0.99
    )
    assert controller.decide(explicit, [reversible]).disposition == ActionDisposition.EXECUTE
    missing = ActionProposal(
        "agenda.complete", {}, None, ActionAuthority.USER_EXPLICIT, 0.99
    )
    assert controller.decide(missing, [reversible]).disposition == ActionDisposition.CLARIFY
    send = ActionProposal("send.mail", {}, None, ActionAuthority.USER_EXPLICIT, 0.99)
    assert controller.decide(send, [external]).disposition == ActionDisposition.CONFIRM
    self_read = ActionProposal(
        "gpu.read", {}, None, ActionAuthority.EURI_PROPOSED, 0.99
    )
    assert controller.decide(self_read, [readonly]).disposition == ActionDisposition.CONFIRM
    assert controller.decide(
        self_read, [readonly], allow_euri_read_only=True
    ).disposition == ActionDisposition.EXECUTE
    explicit_build = ActionProposal(
        "executor.build_computational_tool",
        {"task": "verifica il bilancio"},
        None,
        ActionAuthority.USER_EXPLICIT,
        0.99,
    )
    assert controller.decide(
        explicit_build, [local_write]
    ).disposition == ActionDisposition.EXECUTE
    self_build = dataclass_replace(
        explicit_build, authority=ActionAuthority.EURI_PROPOSED
    )
    assert controller.decide(
        self_build, [local_write]
    ).disposition == ActionDisposition.CONFIRM


def test_controller_can_return_an_explicit_conversation_decision():
    controller = ActionController(chat=_Chat({}), model="fake")
    conversational = ActionProposal(
        "", {}, None, ActionAuthority.NONE, 0.99,
        request_kind="conversation",
        reason="la richiesta chiede una spiegazione",
    )
    decision = controller.decide(conversational, [])
    assert decision.disposition == ActionDisposition.CONVERSE


def test_voice_contextual_probe_returns_to_chat_only_on_explicit_conversation():
    daemon = _daemon({
        "request_kind": "conversation",
        "mode": "none",
        "response_mode": "integrated",
        "capability": None,
        "args": {},
        "target_id": None,
        "authority": "none",
        "confidence": 0.99,
        "reason": "descrivere la struttura di Euri è una risposta",
    })
    handled, veto = daemon._try_contextual_action(
        "Descrivi la tua struttura e i tuoi limiti."
    )
    assert handled is False and veto is False


def test_execute_intent_enters_contextual_controller_before_legacy_handler():
    assert _should_try_contextual_action(Intent.EXECUTE, True) is True
    assert _should_try_contextual_action(Intent.EXECUTE, False) is False


def test_contextual_word_request_selects_compose_document():
    contextual = [{
        "name": "compose_document",
        "description": "Crea un Word dal documento attivo",
        "parameters_schema": {
            "instruction": {"type": "str", "required": True},
            "format": {"type": "str", "required": False},
            "filename": {"type": "str", "required": False},
        },
        "effect": "local_write",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "mode": "direct",
        "response_mode": "tool_result",
        "capability": "executor.compose_document",
        "args": {"format": "docx", "filename": "densificatore.docx"},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 1.0,
        "reason": "richiesta esplicita di creare un Word dal PDF attivo",
    }, contextual=contextual)
    handled, veto = daemon._try_contextual_action(
        "Crea un documento Word con le informazioni del PDF attivo."
    )
    assert handled is True and veto is False
    assert [call.tool_name for call in daemon.executor.calls] == ["compose_document"]
    assert daemon.executor.calls[0].parameters["format"] == "docx"


def test_poseidon_chat_intent_executes_grounded_target():
    daemon = _daemon({
        "capability": "agenda.complete",
        "args": {},
        "target_id": POSEIDON_ID,
        "authority": "user_explicit",
        "confidence": 0.98,
        "reason": "Stefano considera chiuso il todo appena nominato",
    })
    handled, veto = daemon._try_contextual_action(
        "Considero chiuso, lo rifacciamo più avanti ma decido la data io."
    )
    assert handled is True and veto is False
    assert daemon.memory.completed == [POSEIDON_ID]
    assert HARDWARE_ID not in daemon.memory.completed
    assert "segnato come completato" in daemon.spoken[-1]


def test_ambiguous_target_clarifies_without_mutation():
    daemon = _daemon({
        "capability": "agenda.complete",
        "args": {},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 0.96,
        "reason": "due impegni possibili",
    })
    handled, veto = daemon._try_contextual_action("Chiudilo.")
    assert handled is True and veto is False
    assert daemon.memory.completed == []
    assert "quale impegno" in daemon.spoken[-1].lower()


def test_clarification_reasons_again_and_executes_only_resolved_target():
    daemon = _daemon([
        {
            "mode": "direct",
            "capability": "agenda.complete",
            "args": {},
            "target_id": None,
            "authority": "user_explicit",
            "confidence": 0.96,
            "reason": "due impegni possibili",
        },
        {
            "mode": "direct",
            "capability": "agenda.complete",
            "args": {},
            "target_id": POSEIDON_ID,
            "authority": "user_explicit",
            "confidence": 0.99,
            "reason": "il chiarimento nomina Poseidon",
        },
    ])
    handled, _veto = daemon._try_contextual_action("Chiudilo.")
    assert handled is True and daemon.memory.completed == []
    daemon._handle_pending_action("Quello del Poseidon.")
    assert daemon.memory.completed == [POSEIDON_ID]


def test_contextual_read_only_uses_executor():
    contextual = [{
        "name": "gpu_usage",
        "description": "Legge lo stato GPU",
        "parameters_schema": {},
        "effect": "read_only",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "capability": "executor.gpu_usage",
        "args": {},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 0.97,
        "reason": "richiesta di controllo contestuale",
    }, contextual=contextual)
    handled, veto = daemon._try_contextual_action("Puoi controllarla adesso?")
    assert handled is True and veto is False
    assert [call.tool_name for call in daemon.executor.calls] == ["gpu_usage"]
    assert daemon.spoken[-1] == "GPU libera: 7.8 GiB"


def test_euri_can_fulfil_only_read_only_intention():
    contextual = [{
        "name": "gpu_usage",
        "description": "Legge lo stato GPU",
        "parameters_schema": {},
        "effect": "read_only",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "capability": "executor.gpu_usage",
        "args": {},
        "target_id": None,
        "authority": "none",  # origin=euri forza comunque euri_proposed
        "confidence": 0.97,
        "reason": "Euri propone un controllo reale",
    }, contextual=contextual)
    assert daemon._try_euri_readonly_action(
        "Ora controllo la GPU e ti dico.", "Come siamo messi con le risorse?"
    ) is True
    assert [call.tool_name for call in daemon.executor.calls] == ["gpu_usage"]
    assert "sottopasso" in daemon.spoken[-1]
    assert "Esito: GPU libera: 7.8 GiB" in daemon.brain.responses[-1][1]


def test_semantic_action_veto_skips_second_controller_on_reflective_draft():
    daemon = _daemon({}, contextual=[{
        "name": "read_log",
        "description": "Legge il log del servizio",
        "parameters_schema": {},
        "effect": "read_only",
        "requires_confirm": False,
    }])
    recovery_calls = []
    daemon._try_euri_readonly_action = (
        lambda draft, user_text: recovery_calls.append((draft, user_text)) or False
    )
    draft = (
        "La distinzione regge. Provo a elaborare il parallelo su un altro piano. "
        "La conclusione resta prudente."
    )

    reply, rerouted = daemon._finalize_unbacked_action_claims(
        draft,
        "Prova a fare un pensiero più profondo.",
        channel="test",
        semantic_action_veto=True,
    )

    assert rerouted is False
    assert recovery_calls == []
    assert reply == "La distinzione regge. La conclusione resta prudente."
    assert "background" not in reply


def test_unavailable_action_can_use_grounded_read_only_alternative():
    contextual = [{
        "name": "read_log",
        "description": "Legge il log del servizio",
        "parameters_schema": {},
        "effect": "read_only",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "mode": "alternative",
        "capability": "executor.read_log",
        "args": {},
        "target_id": None,
        "authority": "user_explicit",  # il parser deve degradare a proposta di Euri
        "confidence": 0.96,
        "unmet_intent": "riavviare il servizio",
        "reason": "il riavvio non è disponibile, il log può verificarne lo stato",
    }, contextual=contextual)
    handled, veto = daemon._try_contextual_action(
        "Non puoi riavviarlo? Almeno verifica se il servizio è vivo."
    )
    assert handled is True and veto is False
    assert [call.tool_name for call in daemon.executor.calls] == ["read_log"]
    assert "sottopasso" in daemon.spoken[-1].lower()
    assert "riavviare il servizio" in daemon.brain.responses[-1][1]


def test_reflective_request_keeps_tool_result_inside_final_answer():
    contextual = [{
        "name": "top_processes",
        "description": "Mostra i processi che usano più CPU",
        "parameters_schema": {"n": "integer", "sort_by": "string"},
        "effect": "read_only",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "mode": "alternative",
        # response_mode omesso apposta: ogni alternativa deve integrare comunque.
        "capability": "executor.top_processes",
        "args": {"n": 5, "sort_by": "cpu"},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 0.90,
        "unmet_intent": "valutare cosa migliorare nel sistema di Euri",
        "reason": "controllo parziale delle risorse",
    }, contextual=contextual)
    text = (
        "Fai un esame di quello che sai fare, magari usando i tuoi strumenti, "
        "e dimmi dove sarebbe necessario migliorare il tuo sistema."
    )
    handled, veto = daemon._try_contextual_action(text)
    assert handled is True and veto is False
    assert [call.tool_name for call in daemon.executor.calls] == ["top_processes"]
    assert daemon.spoken[-1] != "GPU libera: 7.8 GiB"
    assert "domanda originale" in daemon.spoken[-1]
    prompt_text, context = daemon.brain.responses[-1]
    assert prompt_text == text
    assert "executor.top_processes" in context
    assert "valutare cosa migliorare" in context
    assert daemon.brain.injections == []


def test_integrated_document_result_is_not_injected_twice():
    contextual = [{
        "name": "read_document",
        "description": "Legge un documento e ne espone i dati",
        "parameters_schema": {"question": "string"},
        "effect": "read_only",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "mode": "direct",
        "response_mode": "integrated",
        "capability": "executor.read_document",
        "args": {"question": "valuta il documento"},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 0.97,
        "reason": "la lettura e la valutazione fanno parte dello stesso turno",
    }, contextual=contextual)
    handled, veto = daemon._try_contextual_action(
        "Leggi il documento e spiegami quali problemi vedi."
    )
    assert handled is True and veto is False
    assert daemon.brain.injections == []
    assert len(daemon.brain.responses) == 1


def test_mutating_alternative_is_proposed_then_confirmed():
    contextual = [{
        "name": "prepare_local_report",
        "description": "Prepara un rapporto locale",
        "parameters_schema": {},
        "effect": "local_write",
        "requires_confirm": False,
    }]
    daemon = _daemon({
        "mode": "alternative",
        "capability": "executor.prepare_local_report",
        "args": {},
        "target_id": None,
        "authority": "user_explicit",
        "confidence": 0.97,
        "unmet_intent": "inviare il rapporto",
        "reason": "può prepararlo ma non inviarlo",
    }, contextual=contextual)
    handled, veto = daemon._try_contextual_action("Invia il rapporto al fornitore.")
    assert handled is True and veto is False
    assert daemon.executor.calls == []
    assert "alternativa" in daemon.spoken[-1].lower()
    daemon._handle_pending_action("Sì, procedi.")
    assert [call.tool_name for call in daemon.executor.calls] == ["prepare_local_report"]


def test_executor_rejects_invented_parameters():
    spec = ToolSpec("probe", "test", {}, lambda *_a, **_k: None)
    ok, error = SandboxGuard().validate_parameters(spec, {"invented": "value"})
    assert ok is False and "invented" in error


def test_action_hint_is_recall_only():
    assert looks_actionable("Considero chiuso, lo rifacciamo più avanti")
    assert looks_actionable("Puoi controllarla adesso?")
    assert not looks_actionable("Euri, ho dei todo in sospeso?")
    assert not looks_actionable("È interessante quello che dici sui polimeri")
    assert not looks_actionable(
        "Sto preparando l'estrusione di prova e preparerò 20-30 kg; "
        "oggi faccio la prova e domani avrò le risposte meccaniche."
    )


def test_descriptive_future_cannot_reschedule_unrelated_todo():
    daemon = _daemon({
        "capability": "agenda.reschedule",
        "args": {},
        "target_id": HARDWARE_ID,
        "authority": "user_explicit",
        "confidence": 0.99,
        "reason": "domani è presente nel turno",
    })
    daemon.r = _PulseRedis()
    handled, veto = daemon._try_contextual_action(
        "Sto preparando l'estrusione di prova e preparerò 20-30 kg di prodotto. "
        "Oggi 23 luglio faccio la prova e domani avrò le risposte meccaniche."
    )
    assert handled is False and veto is True
    assert daemon.memory.rescheduled == []
    assert [event[2]["kind"] for event in daemon.r.events] == ["proposed", "decided"]
    assert daemon.r.events[1][2]["causation_id"] == daemon.r.events[0][1]
    assert daemon.r.events[0][2]["event_class"] == "cognitive"


def test_explicit_reschedule_requires_and_uses_grounded_referent():
    daemon = _daemon({
        "capability": "agenda.reschedule",
        "args": {},
        "target_id": POSEIDON_ID,
        "authority": "user_explicit",
        "confidence": 0.99,
        "reason": "rimandalo riferisce il Poseidon appena nominato",
    })
    handled, veto = daemon._try_contextual_action("Rimandalo a domani.")
    assert handled is True and veto is False
    assert [item[0] for item in daemon.memory.rescheduled] == [POSEIDON_ID]
    assert has_explicit_agenda_authority(
        "Puoi rimandarlo a domani?", "agenda.reschedule"
    )
    assert has_explicit_agenda_authority(
        "Vorrei sospendere il checkpoint hardware.", "agenda.suspend"
    )
    assert has_explicit_agenda_authority(
        "Puoi chiudere il todo Poseidon?", "agenda.complete"
    )


def test_explicit_not_more_to_do_remains_authorized_completion():
    daemon = _daemon({
        "capability": "agenda.complete",
        "args": {},
        "target_id": POSEIDON_ID,
        "authority": "user_explicit",
        "confidence": 0.99,
        "reason": "il todo Poseidon non è più da fare",
    })
    handled, veto = daemon._try_contextual_action(
        "Quello del Poseidon per me non è più da fare."
    )
    assert handled is True and veto is False
    assert daemon.memory.completed == [POSEIDON_ID]


class _JsonRecorder:
    def __init__(self):
        self.calls = []

    def set(self, key, path, value):
        self.calls.append((key, path, value))


class _RedisRecorder:
    def __init__(self):
        self.json_api = _JsonRecorder()
        self.removed = []

    def exists(self, _key):
        return True

    def json(self):
        return self.json_api

    def srem(self, key, value):
        self.removed.append((key, value))


def test_suspend_todo_keeps_it_pending_without_due_date():
    redis = _RedisRecorder()
    memory = MemoryManager.__new__(MemoryManager)
    memory.r = redis
    with patch("core.memory_manager.now", return_value=datetime(2026, 7, 21, 10, 0)):
        assert memory.suspend_todo(POSEIDON_ID) is True
    writes = {path: value for _key, path, value in redis.json_api.calls}
    assert writes["$.status"] == "pending"
    assert writes["$.due_at"] is None
    assert writes["$.suspended_at"] is not None
    assert redis.removed == [("euri:pulse:clock_emitted", POSEIDON_ID)]


def test_overdue_wording_uses_calendar_days():
    with patch("core.brain.now", return_value=datetime(2026, 7, 21, 8, 0)):
        reply = Brain.__new__(Brain).format_today_summary([], [{
            "content": "Test Poseidon",
            "_due_at": datetime(2026, 7, 20, 9, 0),
        }])
    assert "da ieri" in reply
    assert "da oggi" not in reply


def test_semantic_gate_can_request_reasoning_without_regex():
    fake = _Chat({})
    fake.chat = lambda **_kwargs: SimpleNamespace(
        message=SimpleNamespace(content="ACTION_REASONING")
    )
    with (
        patch.object(llm_classifier, "chat_client", fake),
        patch.object(llm_classifier, "_tool_registry", None),
        patch.object(llm_classifier, "_adaptive_clf", None),
        patch.object(llm_classifier.config, "ACLF_HARVEST_ENABLED", False),
    ):
        result = llm_classifier.llm_fallback_classify(
            "Quello del Poseidon per me non è più da fare."
        )
    assert result == "ACTION_REASONING"


if __name__ == "__main__":
    test_policy_boundaries()
    test_controller_can_return_an_explicit_conversation_decision()
    test_voice_contextual_probe_returns_to_chat_only_on_explicit_conversation()
    test_execute_intent_enters_contextual_controller_before_legacy_handler()
    test_contextual_word_request_selects_compose_document()
    test_poseidon_chat_intent_executes_grounded_target()
    test_ambiguous_target_clarifies_without_mutation()
    test_clarification_reasons_again_and_executes_only_resolved_target()
    test_contextual_read_only_uses_executor()
    test_euri_can_fulfil_only_read_only_intention()
    test_unavailable_action_can_use_grounded_read_only_alternative()
    test_reflective_request_keeps_tool_result_inside_final_answer()
    test_integrated_document_result_is_not_injected_twice()
    test_mutating_alternative_is_proposed_then_confirmed()
    test_executor_rejects_invented_parameters()
    test_action_hint_is_recall_only()
    test_descriptive_future_cannot_reschedule_unrelated_todo()
    test_explicit_reschedule_requires_and_uses_grounded_referent()
    test_explicit_not_more_to_do_remains_authorized_completion()
    test_suspend_todo_keeps_it_pending_without_due_date()
    test_overdue_wording_uses_calendar_days()
    test_semantic_gate_can_request_reasoning_without_regex()
    print("test_action_controller: 22/22 OK")
