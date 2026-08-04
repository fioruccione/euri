"""Regressioni wake guard, activity timing e provenienza passive learner."""
import sys
import threading
import time
import types
from enum import Enum

sys.path.insert(0, '/home/fio/Euri')


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


# Il test esercita helper logici via __new__: i backend audio/video non devono
# inizializzare librerie native durante l'import del daemon.
_stub_module("voice.audio_io", AudioCapture=_HardwareStub, play_audio=lambda *_a, **_k: None)
_stub_module("voice.vad", VAD=_HardwareStub)
_stub_module("voice.stt", STT=_HardwareStub)
_stub_module("voice.tts", TTS=_HardwareStub)
_stub_module("voice.visual_gate", VisualGate=_HardwareStub)
_stub_module("voice.face_auth", FaceAuth=_HardwareStub)
_stub_module(
    "voice.speaker_auth",
    SpeakerAuth=_HardwareStub,
    SpeakerVerdict=_SpeakerVerdict,
    ENROLL_UTTERANCES=3,
)

import voice_daemon as vd
from core.cognitive_present import CognitivePresent
from core.intent_router import Intent, classify

WIN = vd._CONVERSATION_WINDOW_SEC

def make(translate=False, dictation=False, last_activity=100.0):
    d = vd.VoiceDaemon.__new__(vd.VoiceDaemon)
    d._translate_bidir = translate
    d._dictation_mode = dictation
    d._last_activity_ts = last_activity
    d._last_auth_voice_ts = 50.0
    d._voice_input_inflight = threading.Event()
    d.present = CognitivePresent(conversation_window_s=45, focus_window_s=300)
    return d

def test_addressed_guard():
    f = vd.VoiceDaemon._utterance_is_addressed
    cases = [
        ((True, WIN + 10, False, False), True),
        ((False, WIN - 5, False, False), True),
        ((False, WIN + 10, False, False), False),
        ((False, 0.0, False, False), True),
        ((False, WIN + 99, True, False), True),
        ((False, WIN + 99, False, True), True),
    ]
    for (has_wake, since_last, translate, dictation), expected in cases:
        assert f(make(translate, dictation), has_wake, since_last) is expected
    print("OK  guard indirizzamento 6/6")


# ── Punto 3: degrado a DEBOLE del parlato ambient (no wake nel segmento) ──────
def test_passive_weak_and_mixed_segment():
    g = vd.VoiceDaemon._passive_weak_support
    assert g("strong", True)  is False   # rivolto a Euri → fatto pieno
    assert g("strong", False) is True    # ambient → degradato anche se FORTE
    assert g("weak",   True)  is True     # weak resta weak
    assert g(None,     False) is True     # default + ambient → degradato
    assert g(None,     True)  is False    # default + rivolto → pieno
    history = [
        {"role": "user", "content": "t1", "trusted": True},
        {"role": "assistant", "content": "t2", "trusted": True},
        {"role": "user", "content": "a1", "trusted": False},
        {"role": "assistant", "content": "a2", "trusted": False},
        {"role": "user", "content": "t3", "trusted": True},
        {"role": "assistant", "content": "t4", "trusted": True},
        {"role": "user", "content": "a3", "trusted": False},
        {"role": "assistant", "content": "a4", "trusted": False},
    ]
    batches = vd.VoiceDaemon._passive_extraction_batches(history)
    assert [addressed for addressed, _ in batches] == [True, False]
    assert all(bool(msg["trusted"]) is addressed for addressed, batch in batches for msg in batch)
    assert [len(batch) for _, batch in batches] == [4, 4]

    owner_followups = [
        {
            "role": "user",
            "content": "Il cliente sta ancora provando il blend.",
            "trusted": False,
            "semantic_frame": {"accepted_owner_turn": True},
        },
        {"role": "assistant", "content": "Capito.", "trusted": False},
        {
            "role": "user",
            "content": "Aspettiamo ancora il risultato.",
            "trusted": False,
            "semantic_frame": {"accepted_owner_turn": True},
        },
        {"role": "assistant", "content": "Va bene.", "trusted": False},
    ]
    assert vd.VoiceDaemon._passive_extraction_batches(owner_followups) == [
        (True, owner_followups)
    ]

    short_mixed = history[:4]
    assert vd.VoiceDaemon._passive_extraction_batches(short_mixed) == [(False, short_mixed)]
    print("OK  segmento misto separato e ambient degradato")


def test_passive_policy_excludes_the_whole_ephemeral_exchange():
    ephemeral = {
        "status": "interpreted",
        "confidence": 0.98,
        "memory_disposition": "ephemeral",
    }
    candidate = {
        "status": "interpreted",
        "confidence": 0.98,
        "memory_disposition": "candidate",
    }
    uncertain = {
        "status": "fallback",
        "confidence": 1.0,
        "memory_disposition": "no_store",
    }
    history = [
        {"role": "user", "content": "riavvio", "semantic_frame": ephemeral},
        {"role": "assistant", "content": "ricevuto"},
        {"role": "user", "content": "cliente", "semantic_frame": candidate},
        {"role": "assistant", "content": "capito"},
        {"role": "user", "content": "fallback", "semantic_frame": uncertain},
        {"role": "assistant", "content": "resta analizzabile"},
    ]
    eligible = vd.VoiceDaemon._passive_memory_eligible_history(history)
    assert [item["content"] for item in eligible] == [
        "cliente", "capito", "fallback", "resta analizzabile",
    ]
    print("OK  policy passiva: scambio effimero escluso, fallback fail-open")


def test_activity_only_after_acceptance():
    d = make(last_activity=100.0)
    assert d._accept_voice_transcript("", now_ts=1000.0) is None
    assert d._accept_voice_transcript("rumore rumore rumore rumore rumore rumore", now_ts=1000.0) is None
    assert d._accept_voice_transcript("questa frase non ti riguarda", now_ts=100.0 + WIN + 1) is None
    assert d._last_activity_ts == 100.0 and d._last_auth_voice_ts == 50.0

    accepted = d._accept_voice_transcript("Euri, ascoltami", now_ts=1000.0)
    assert accepted == ("Euri, ascoltami", True)
    assert d._last_activity_ts == 1000.0 and d._last_auth_voice_ts == 1000.0
    print("OK  vuoto, garbage e fuori-finestra non rinnovano activity")


def test_guest_turn_requires_wake_and_does_not_authenticate():
    d = make(last_activity=100.0)
    d.present.accept_user_turn("Euri, dimmi qualcosa", at=100.0)

    assert d._accept_voice_transcript(
        "Questa informazione non è rivolta a te.",
        now_ts=110.0,
        authenticated=False,
        require_wake_word=True,
        track_present=False,
    ) is None
    accepted = d._accept_voice_transcript(
        "Euri, il lotto di oggi è 24B17.",
        now_ts=120.0,
        authenticated=False,
        require_wake_word=True,
        track_present=False,
    )
    assert accepted is not None
    assert d._last_auth_voice_ts == 50.0
    print("OK  ospite: wake obbligatoria e nessun rinnovo autenticazione")


def test_multimodal_actor_resolution():
    d = make()
    d.visual_gate = type("Gate", (), {"is_owner_present": lambda _self: True})()
    assert d._resolve_voice_actor(_SpeakerVerdict.VERIFIED) == "stefano"
    assert d._resolve_voice_actor(_SpeakerVerdict.INDETERMINATE) == "stefano"
    assert d._resolve_voice_actor(_SpeakerVerdict.REJECTED) == "unknown"

    d.visual_gate = type("Gate", (), {"is_owner_present": lambda _self: False})()
    assert d._resolve_voice_actor(_SpeakerVerdict.INDETERMINATE) == "unknown"

    now_ts = time.time()
    d._last_auth_voice_ts = now_ts
    d.present.accept_user_turn("Euri, apro la conversazione", at=now_ts)
    assert d._resolve_voice_actor(_SpeakerVerdict.INDETERMINATE) == "stefano"
    print("OK  fusione voce/volto: indeterminato non equivale a Stefano")


def test_owner_confirmation_promotes_guest_claim_with_provenance():
    d = make()
    saved = {}
    settled = {}
    spoken = []

    class Memory:
        def save_memory(self, content, **kwargs):
            saved.update({"content": content, **kwargs})
            return "memory-1"

        def log_conversation(self, *_args):
            return None

    class Store:
        def settle(self, claim_id, status, **kwargs):
            settled.update({"id": claim_id, "status": status, **kwargs})

    d.memory = Memory()
    d.guest_claims = Store()
    d._guest_review_cooldown_until = 0.0
    d._pending_guest_review = vd._PendingState({
        "id": "claim-1",
        "claim": "Il lotto Poseidon è 24B17.",
        "observed_at": 123.0,
    }, timeout=300)
    d._speak = lambda text, **_kwargs: spoken.append(text)

    d._handle_pending_guest_review("Sì, confermo che è corretto.")

    assert saved["source"] == "user"
    assert saved["memory_kind"] == "semantic_fact"
    assert saved["temporal_context"]["origin_actor_id"] == "unknown"
    assert saved["temporal_context"]["confirmed_by_actor_id"] == "stefano"
    assert settled == {
        "id": "claim-1",
        "status": "confirmed",
        "reviewed_by": "stefano",
        "promoted_memory_id": "memory-1",
    }
    assert d._pending_guest_review is None
    assert spoken and "provenienza" in spoken[-1]
    print("OK  conferma proprietario promuove il claim mantenendo la provenienza")


def test_first_utterance_without_wake_does_not_open_a_session():
    d = make(last_activity=0.0)

    assert d._accept_voice_transcript(
        "Questa è soltanto una conversazione ambientale.", now_ts=1000.0
    ) is None
    assert d._last_activity_ts == 0.0
    assert d._last_auth_voice_ts == 50.0
    print("OK  primo turno senza wake: lease chiusa senza timestamp epoch")


def test_owner_semantic_bootstrap_opens_first_session_and_reuses_frame():
    d = make(last_activity=0.0)
    d.visual_gate = type(
        "Gate",
        (),
        {"is_owner_present": lambda _self: True},
    )()
    frame = {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "addressed_to_assistant": True,
        "address_relation": "direct_address",
        "address_confidence": 0.98,
    }
    calls = []
    d._bootstrap_semantic_interpreter = lambda text: calls.append(text) or frame

    accepted = d._accept_voice_transcript(
        "Ciao, sono tornato; scusami se ho dovuto fermarti.",
        now_ts=1000.0,
        authenticated=True,
        include_semantic_frame=True,
    )
    assert accepted == (
        "Ciao, sono tornato; scusami se ho dovuto fermarti.",
        False,
        frame,
    )
    assert calls == ["Ciao, sono tornato; scusami se ho dovuto fermarti."]
    assert d._last_activity_ts == 1000.0
    print("OK  bootstrap owner: primo turno diretto accettato e frame riusabile")


def test_owner_bootstrap_rejects_ambient_and_never_applies_to_guest():
    d = make(last_activity=0.0)
    d.visual_gate = type(
        "Gate",
        (),
        {"is_owner_present": lambda _self: True},
    )()
    calls = []
    d._bootstrap_semantic_interpreter = lambda text: calls.append(text) or {
        "status": "interpreted",
        "confidence": 0.99,
        "requires_clarification": False,
        "addressed_to_assistant": False,
        "address_relation": "ambient",
        "address_confidence": 0.99,
    }
    assert d._accept_voice_transcript(
        "Il bancale e' pronto vicino alla porta.",
        now_ts=1000.0,
        authenticated=True,
    ) is None
    assert len(calls) == 1

    assert d._accept_voice_transcript(
        "Puoi controllare il bancale?",
        now_ts=1010.0,
        authenticated=False,
        require_wake_word=True,
    ) is None
    assert len(calls) == 1
    print("OK  bootstrap owner: ambient rifiutato e ospite sempre vincolato alla wake")


def test_long_tts_lease_accepts_followup_without_wake_word():
    d = make(last_activity=110.0)
    d.present.accept_user_turn("Euri, rileggi la memoria", at=100.0)
    d.present.begin_speech(at=110.0)
    d.present.finish_speech(at=170.0)

    accepted = d._accept_voice_transcript(
        "Questa memoria è contorta e non è legata a quel singolo evento.",
        now_ts=205.0,
    )
    assert accepted is not None and accepted[1] is False
    print("OK  lease dalla fine TTS: follow-up accettato dopo risposta lunga")


def test_long_utterance_keeps_consent_from_speech_start():
    d = make(last_activity=100.0)

    accepted = d._accept_voice_transcript(
        "Il bancale pesa dieci chili e il ciclo dura ottantuno secondi.",
        now_ts=170.0,
        addressed_at=120.0,
    )

    assert accepted is not None and accepted[1] is False
    assert d._last_activity_ts == 170.0
    assert d._last_auth_voice_ts == 170.0

    outside = make(last_activity=100.0)
    assert outside._accept_voice_transcript(
        "Questa conversazione ambientale riguarda soltanto il bancale.",
        now_ts=210.0,
        addressed_at=100.0 + WIN + 1,
    ) is None
    print("OK  turno lungo conserva il consenso dall'inizio, senza fail-open ambientale")


def test_adaptive_followup_requires_owner_focus_and_semantic_acceptance():
    d = make(last_activity=100.0)
    d.present.accept_user_turn("Euri, spiegami il sensore", at=100.0)
    d.visual_gate = type(
        "Gate",
        (),
        {"is_owner_present": lambda _self: True},
    )()
    d.brain = type(
        "BrainStub",
        (),
        {
            "history_lock": threading.RLock(),
            "_conversation_history": [
                {"role": "user", "content": "Euri, spiegami il sensore"},
                {"role": "assistant", "content": "Il segnale resta descrittivo."},
            ],
        },
    )()
    d._adaptive_followup_classifier = lambda *_args, **_kwargs: {
        "accepted": True,
        "relation": "direct_followup",
        "confidence": 0.97,
    }

    accepted = d._accept_voice_transcript(
        "In che modo questo potrebbe aiutarci?",
        now_ts=200.0,
        authenticated=True,
    )
    assert accepted is not None and accepted[1] is False

    d._adaptive_followup_classifier = lambda *_args, **_kwargs: {
        "accepted": False,
        "relation": "ambient",
        "confidence": 0.99,
    }
    assert d._accept_voice_transcript(
        "Mi sa che carica l'87.",
        now_ts=300.0,
        authenticated=True,
    ) is None

    d.visual_gate = type(
        "Gate",
        (),
        {"is_owner_present": lambda _self: False},
    )()
    assert d._accept_voice_transcript(
        "In che modo questo potrebbe aiutarci?",
        now_ts=310.0,
        authenticated=True,
    ) is None
    print("OK  seguito adattivo: owner+focus+semantica, altrimenti wake")


def test_memory_operations_are_domain_independent():
    recall_cases = [
        "Euri, cosa hai in memoria?",
        "Euri, cosa sai di me?",
        "Euri, a proposito delle prove sul Poseidon di ieri, cosa hai in memoria?",
        "Cosa ricordi del viaggio in Giappone?",
        "Cosa hai in memoria riguardo alla terapia?",
        "Cosa ricordi del romanzo che sto scrivendo?",
    ]
    for text in recall_cases:
        assert classify(text)[0] == Intent.SEARCH, text

    status_cases = [
        "Euri, quante memorie hai?",
        "Euri, qual e' lo stato della memoria?",
    ]
    for text in status_cases:
        assert classify(text)[0] == Intent.STATUS, text

    audit_cases = [
        "Euri, fai un audit della memoria",
        "Euri, pulisci le memorie",
        "Euri, cerca le memorie duplicate",
        "Euri, analizza il rumore nella memoria",
    ]
    for text in audit_cases:
        assert classify(text)[0] == Intent.AUDIT_MEMORY, text

    assert classify("Euri, controlla la memoria")[0] == Intent.CHAT
    assert classify("Euri, controlla la memoria RAM")[0] == Intent.EXECUTE
    print("OK  recall, stato e manutenzione distinti senza dipendere dal dominio")


def test_memory_audit_candidates_are_bounded_and_risk_first():
    docs = [
        {"id": "clean-new", "created_at": 30},
        {"id": "risky", "created_at": 10, "requires_verification": True},
        {"id": "flagged", "created_at": 5, "audit_flag": "check"},
        {"id": "anchor", "created_at": 100, "memory_kind": "conversation_anchor"},
        {"id": "old", "created_at": 1},
    ]
    selected = vd.VoiceDaemon._select_memory_audit_candidates(docs, limit=3)
    assert [doc["id"] for doc in selected] == ["flagged", "risky", "clean-new"]
    assert all(doc["id"] != "anchor" for doc in selected)
    print("OK  audit memoria limitato, risk-first e senza anchor episodici")


def test_offtopic_reaction_returns_turn_to_dispatch():
    import core.utterance_pragmatics as pragmatics

    d = make()
    d.memory = type("Mem", (), {"log_conversation": lambda *_a, **_k: None})()
    question_id = "initiative:izod"
    d._awaiting_reaction = vd._PendingState({
        "insight": {"id": "abc", "content": "protocollo e progetto"},
        "question": "Il collegamento tra protocollo e progetto regge?",
        "question_id": question_id,
    }, timeout=300)
    d.present.set_pending_question(question_id, "Il collegamento regge?")
    old = pragmatics.classify_reply_type
    pragmatics.classify_reply_type = lambda *_a, **_k: "OFF_TOPIC"
    try:
        handled = d._handle_reaction(
            "Abbiamo un frigorifero per l'IZOD, forse i provini erano messi male."
        )
    finally:
        pragmatics.classify_reply_type = old

    assert handled is False
    assert d._awaiting_reaction is None
    assert d.present.snapshot().pending_question_id == ""
    print("OK  reaction OFF_TOPIC: nessuna cattura, turno restituito al dispatch")


def test_reaction_ack_does_not_prejudge_async_verdict():
    ack = vd._REACTION_ACK.lower()
    assert "confermi" not in ack
    assert "correggi" not in ack
    assert "sì o a un no" not in ack
    assert "registro la tua risposta" in ack
    print("OK  reaction ack neutro prima del verdetto asincrono")


def test_initiative_token_is_cancelled_by_voice_inflight():
    d = make()
    token = d.present.issue_decision_token()
    d._voice_input_inflight.set()

    assert d._revalidate_initiative_output(token) == (False, "voice_input_inflight")
    print("OK  voce VAD in volo invalida l'efferenza Initiative")


def test_implicit_read_log_requires_sentence_level_commitment():
    assert vd._IMPLICIT_READ_LOG_RE.search("Controllo il log e ti dico.")
    assert vd._IMPLICIT_READ_LOG_RE.search("Ora leggo il log.")
    assert not vd._IMPLICIT_READ_LOG_RE.search(
        "Finché c'è qualcosa da sintonizzare tra quello che leggo nei log "
        "e come lo interpreto, il lavoro non è finito."
    )
    print("OK  read_log implicito: azione esplicita, non descrizione metacognitiva")


if __name__ == "__main__":
    test_addressed_guard()
    test_passive_weak_and_mixed_segment()
    test_passive_policy_excludes_the_whole_ephemeral_exchange()
    test_activity_only_after_acceptance()
    test_guest_turn_requires_wake_and_does_not_authenticate()
    test_multimodal_actor_resolution()
    test_owner_confirmation_promotes_guest_claim_with_provenance()
    test_first_utterance_without_wake_does_not_open_a_session()
    test_owner_semantic_bootstrap_opens_first_session_and_reuses_frame()
    test_owner_bootstrap_rejects_ambient_and_never_applies_to_guest()
    test_long_tts_lease_accepts_followup_without_wake_word()
    test_long_utterance_keeps_consent_from_speech_start()
    test_adaptive_followup_requires_owner_focus_and_semantic_acceptance()
    test_memory_operations_are_domain_independent()
    test_memory_audit_candidates_are_bounded_and_risk_first()
    test_offtopic_reaction_returns_turn_to_dispatch()
    test_reaction_ack_does_not_prejudge_async_verdict()
    test_initiative_token_is_cancelled_by_voice_inflight()
    test_implicit_read_log_requires_sentence_level_commitment()
    print("PASS")


def _test_readback_chronological():
    """READ_BACK legge l'ULTIMA cronologica anche se il ranking epistemico (f19ce39)
    ha spinto giù la memoria fresca-ma-rischiosa: l'audit è sulla cronologia."""
    import time as _t
    class FakeMem:
        def get_recent_memories(self, limit, source_filter, touch):
            return [
                {"id": "old", "content": "Nota vecchia e pulita.", "source": "reflection",
                 "created_at": _t.time() - 3600},
                {"id": "new", "content": "Lezione PVC: ridurre di 25 gradi non basta.",
                 "source": "reaction", "created_at": _t.time() - 60,
                 "requires_verification": True},
            ]
        _safe_keywords = staticmethod(lambda t: [])
    d = vd.VoiceDaemon.__new__(vd.VoiceDaemon)
    d.memory = FakeMem()
    d._READBACK_SRC_HINTS = vd.VoiceDaemon._READBACK_SRC_HINTS
    target = vd.VoiceDaemon._find_readback_target(d, "cosa hai salvato poco fa?")
    assert target["id"] == "new", f"letta la sbagliata: {target['id']}"
    print("OK  readback: ultima CRONOLOGICA nonostante il ranking epistemico")


_test_readback_chronological()
