"""Regressioni wake guard, activity timing e provenienza passive learner."""
import sys
import threading
import types

sys.path.insert(0, '/home/fio/Euri')


def _stub_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _HardwareStub:
    pass


# Il test esercita helper logici via __new__: i backend audio/video non devono
# inizializzare librerie native durante l'import del daemon.
_stub_module("voice.audio_io", AudioCapture=_HardwareStub, play_audio=lambda *_a, **_k: None)
_stub_module("voice.vad", VAD=_HardwareStub)
_stub_module("voice.stt", STT=_HardwareStub)
_stub_module("voice.tts", TTS=_HardwareStub)
_stub_module("voice.visual_gate", VisualGate=_HardwareStub)
_stub_module("voice.face_auth", FaceAuth=_HardwareStub)
_stub_module("voice.speaker_auth", SpeakerAuth=_HardwareStub, ENROLL_UTTERANCES=3)

import voice_daemon as vd
from core.cognitive_present import CognitivePresent

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

    short_mixed = history[:4]
    assert vd.VoiceDaemon._passive_extraction_batches(short_mixed) == [(False, short_mixed)]
    print("OK  segmento misto separato e ambient degradato")


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


def test_initiative_token_is_cancelled_by_voice_inflight():
    d = make()
    token = d.present.issue_decision_token()
    d._voice_input_inflight.set()

    assert d._revalidate_initiative_output(token) == (False, "voice_input_inflight")
    print("OK  voce VAD in volo invalida l'efferenza Initiative")


if __name__ == "__main__":
    test_addressed_guard()
    test_passive_weak_and_mixed_segment()
    test_activity_only_after_acceptance()
    test_long_tts_lease_accepts_followup_without_wake_word()
    test_offtopic_reaction_returns_turn_to_dispatch()
    test_initiative_token_is_cancelled_by_voice_inflight()
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
