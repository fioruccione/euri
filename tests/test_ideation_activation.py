#!/usr/bin/env python3
"""Regressioni del confine conversazionale di Loop 2k."""
from types import SimpleNamespace
from enum import Enum
import sys
import types

from core.ideation_activation import (
    enqueue_job, format_result, pop_job, semantic_pending_decision,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        removed = self.values.pop(key, None) is not None
        removed = self.lists.pop(key, None) is not None or removed
        return int(removed)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpop(self, key):
        values = self.lists.get(key) or []
        return values.pop(0) if values else None

    def expire(self, key, ttl):
        return key in self.values or key in self.lists


def test_pending_decision_uses_semantic_acts_not_words():
    frame = {
        "status": "interpreted", "confidence": 0.96,
        "requires_clarification": False, "addressed_to_assistant": True,
        "speech_acts": ["CONFIRM"],
    }
    assert semantic_pending_decision(frame) == "confirm"

    # La superficie non conta: senza il verdetto semantico non c'e' consenso.
    frame["raw_text"] = "va bene, certo"
    frame["speech_acts"] = ["INFORM"]
    assert semantic_pending_decision(frame) is None


def test_pending_decision_rejects_ambiguous_or_unaddressed_turns():
    frame = {
        "status": "interpreted", "confidence": 0.96,
        "requires_clarification": False, "addressed_to_assistant": True,
        "speech_acts": ["CONFIRM", "REJECT"],
    }
    assert semantic_pending_decision(frame) is None
    frame["speech_acts"] = ["REJECT"]
    frame["addressed_to_assistant"] = False
    assert semantic_pending_decision(frame) is None


def test_completed_result_is_presented_as_falsifiable_internal_hypothesis():
    candidate = SimpleNamespace(
        id="c1",
        proposal="Eseguire prima un test reversibile.",
        falsification_test="Confrontare gli errori con la baseline.",
        risks=["Il campione potrebbe essere troppo piccolo."],
    )
    result = SimpleNamespace(
        status="completed", candidates=[candidate], top_candidate_ids=["c1"]
    )
    reply = format_result(result)
    assert "test reversibile" in reply
    assert "smentirla" in reply
    assert "ipotesi interna" in reply


def test_contested_result_does_not_force_a_winner():
    result = SimpleNamespace(
        status="contested",
        candidates=[
            SimpleNamespace(id="a", proposal="Strada A"),
            SimpleNamespace(id="b", proposal="Strada B"),
        ],
        top_candidate_ids=["a", "b"],
    )
    reply = format_result(result)
    assert "non c'e' un vincitore netto" in reply
    assert "Non forzo" in reply


def test_silent_chat_job_queue_preserves_the_authorized_payload():
    redis = FakeRedis()
    payload = {
        "token": "t1", "problem": "Confrontare A e B",
        "grounding_context": "fonte verificabile", "constraints": ["reversibile"],
    }
    enqueue_job(redis, "jobs", payload, ttl_s=60)
    assert pop_job(redis, "jobs") == payload
    assert pop_job(redis, "jobs") == {}


def test_suggestion_waits_for_semantic_consent_and_does_not_hijack_other_turns():
    def stub(name, **attributes):
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        sys.modules[name] = module

    class HardwareStub:
        pass

    class SpeakerVerdict(str, Enum):
        VERIFIED = "verified"
        REJECTED = "rejected"
        INDETERMINATE = "indeterminate"

    stub("voice.audio_io", AudioCapture=HardwareStub, play_audio=lambda *_a, **_k: None)
    stub("voice.vad", VAD=HardwareStub)
    stub("voice.stt", STT=HardwareStub)
    stub("voice.tts", TTS=HardwareStub, split_for_speech=lambda text, **_kwargs: [text])
    stub("voice.visual_gate", VisualGate=HardwareStub)
    stub("voice.face_auth", FaceAuth=HardwareStub)
    stub(
        "voice.speaker_auth", SpeakerAuth=HardwareStub,
        SpeakerVerdict=SpeakerVerdict, ENROLL_UTTERANCES=3,
    )
    from voice_daemon import VoiceDaemon

    daemon = VoiceDaemon.__new__(VoiceDaemon)
    daemon.r = FakeRedis()
    spoken = []
    recorded = []
    started = []
    daemon._speak = spoken.append
    daemon._record_ideation_exchange = lambda *args, **kwargs: recorded.append(args)
    daemon._start_semantic_ideation = (
        lambda payload, **kwargs: started.append((payload, kwargs)) or True
    )
    suggestion = {
        "status": "interpreted", "turn_id": "turn-1", "confidence": 0.94,
        "requires_clarification": False, "addressed_to_assistant": True,
        "speech_acts": ["ASK"],
        "deliberation_request": {
            "mode": "suggest", "problem": "Scegliere fra A e B",
            "reason": "tradeoff", "alternatives_visible": True,
            "constraints": [], "evidence": "fra A e B",
            "evidence_grounded": True, "confidence": 0.94,
        },
    }
    assert daemon._handle_semantic_ideation(
        "Fra A e B cosa faresti?", suggestion, trusted=True,
        observed_at=1.0, owner_authorized=True,
    ) is True
    assert spoken and not started

    unrelated = {
        "status": "interpreted", "confidence": 0.96,
        "requires_clarification": False, "addressed_to_assistant": True,
        "speech_acts": ["INFORM"], "deliberation_request": {"mode": "none"},
    }
    assert daemon._handle_semantic_ideation(
        "Intanto parliamo d'altro.", unrelated, trusted=True,
        observed_at=2.0, owner_authorized=True,
    ) is False
    assert not started

    confirmation = dict(unrelated, speech_acts=["CONFIRM"])
    assert daemon._handle_semantic_ideation(
        "Sì, procedi.", confirmation, trusted=True,
        observed_at=3.0, owner_authorized=True,
    ) is True
    assert started[0][0]["problem"] == "Scegliere fra A e B"


if __name__ == "__main__":
    test_pending_decision_uses_semantic_acts_not_words()
    test_pending_decision_rejects_ambiguous_or_unaddressed_turns()
    test_completed_result_is_presented_as_falsifiable_internal_hypothesis()
    test_contested_result_does_not_force_a_winner()
    test_silent_chat_job_queue_preserves_the_authorized_payload()
    test_suggestion_waits_for_semantic_consent_and_does_not_hijack_other_turns()
    print("test_ideation_activation: 6/6 OK")
