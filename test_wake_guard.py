"""Regressioni wake guard, activity timing e provenienza passive learner."""
import sys
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

WIN = vd._CONVERSATION_WINDOW_SEC

def make(translate=False, dictation=False, last_activity=100.0):
    d = vd.VoiceDaemon.__new__(vd.VoiceDaemon)
    d._translate_bidir = translate
    d._dictation_mode = dictation
    d._last_activity_ts = last_activity
    d._last_auth_voice_ts = 50.0
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


if __name__ == "__main__":
    test_addressed_guard()
    test_passive_weak_and_mixed_segment()
    test_activity_only_after_acceptance()
    print("PASS")
