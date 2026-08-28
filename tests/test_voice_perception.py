#!/usr/bin/env python3
"""Regressioni pure della consapevolezza operativa vocale."""
import json

import config
from core.cognitive_present import CognitivePresent, EpistemicStatus
from core.voice_perception import (
    VoicePerceptionRecorder,
    is_voice_perception_question,
    read_recent_voice_perceptions,
    sanitize_voice_perception,
    voice_perception_answer,
    voice_perception_context,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.events = []
        self.last_ex = None

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value
        self.last_ex = ex

    def xadd(self, stream, fields, **kwargs):
        self.events.append((stream, dict(fields), kwargs))
        return f"{len(self.events)}-0"


def _event(trace_id, observed_at, **overrides):
    event = {
        "trace_id": trace_id,
        "started_at": observed_at - 30.1,
        "observed_at": observed_at,
        "duration_s": 30.1,
        "speaker_verdict": "verified",
        "speaker_similarity": 0.887,
        "speaker_threshold": 0.65,
        "speaker_reason": "threshold_comparison",
        "actor_scope": "owner",
        "stt_state": "text",
        "transcript_chars": 392,
        "detected_language": "it",
        "has_wake_word": False,
        "addressed": False,
        "decision": "wake_word_absent_outside_conversation",
        "delivered_to": "none",
        # Campi proibiti che non devono mai uscire dalla whitelist.
        "transcript": "Alessio, buongiorno...",
        "audio": [0.1, 0.2],
        "embedding": [1, 2, 3],
    }
    event.update(overrides)
    return event


def test_sanitizer_never_keeps_audio_text_or_embeddings():
    clean = sanitize_voice_perception(_event("voice:a", 100.0))
    serialized = json.dumps(clean, ensure_ascii=False)
    assert "transcript" not in clean
    assert "audio" not in clean
    assert "embedding" not in clean
    assert "Alessio" not in serialized
    assert clean["transcript_chars"] == 392


def test_phone_call_and_later_rejection_remain_distinct_causal_segments():
    redis = FakeRedis()
    present = CognitivePresent(clock=lambda: 100.0)
    recorder = VoicePerceptionRecorder(redis, present)

    recorder.record(_event("voice:phone", 100.0))
    recorder.record(_event(
        "voice:later",
        116.0,
        started_at=113.8,
        duration_s=2.2,
        speaker_verdict="rejected",
        speaker_similarity=0.402,
        actor_scope="guest",
        stt_state="empty",
        transcript_chars=0,
        decision="stt_empty",
    ))

    recent = read_recent_voice_perceptions(redis, now=117.0)
    assert [item["trace_id"] for item in recent] == ["voice:phone", "voice:later"]
    assert recent[0]["decision"] == "wake_word_absent_outside_conversation"
    assert recent[1]["decision"] == "stt_empty"

    context = voice_perception_context(redis, now=117.0)
    assert "trace=voice:phone" in context
    assert "similarity=0.887" in context
    assert "wake word assente fuori dalla finestra conversazionale" in context
    assert "trace=voice:later" in context
    assert "similarity=0.402" in context
    assert "STT=empty" in context
    assert "segmento distinto" in context
    assert "Alessio" not in context

    observation = present.snapshot(now=117.0).observation(
        "voice.last_pipeline_outcome"
    )
    assert observation is not None
    assert observation.status is EpistemicStatus.SYSTEM_FACT
    assert observation.evidence_ref == "voice:later"


def test_accepted_current_question_is_not_injected_as_a_failure():
    redis = FakeRedis()
    recorder = VoicePerceptionRecorder(redis)
    recorder.record(_event("voice:ignored", 100.0))
    recorder.record(_event(
        "voice:question",
        120.0,
        duration_s=3.9,
        has_wake_word=True,
        addressed=True,
        decision="accepted_wake_word",
        delivered_to="owner_dispatch",
    ))

    context = voice_perception_context(redis, now=121.0)
    assert "voice:ignored" in context
    assert "voice:question" not in context


def test_ttl_removes_old_operational_state_without_memory_fallback(monkeypatch=None):
    redis = FakeRedis()
    recorder = VoicePerceptionRecorder(redis)
    recorder.record(_event("voice:old", 100.0))
    assert read_recent_voice_perceptions(
        redis,
        now=100.0 + config.VOICE_PERCEPTION_TTL_S + 0.1,
    ) == []
    assert voice_perception_context(
        redis,
        now=100.0 + config.VOICE_PERCEPTION_TTL_S + 0.1,
    ) == ""


def test_pulse_event_is_telemetry_and_contains_only_sanitized_payload():
    redis = FakeRedis()
    VoicePerceptionRecorder(redis).record(_event("voice:pulse", 100.0))
    assert len(redis.events) == 1
    stream, fields, _kwargs = redis.events[0]
    assert stream == "euri:pulse"
    assert fields["event_class"] == "telemetry"
    assert fields["trace_id"] == "voice:pulse"
    payload = json.loads(fields["payload"])
    assert payload["decision"] == "wake_word_absent_outside_conversation"
    assert "transcript" not in payload
    assert "audio" not in payload
    assert "embedding" not in payload


def test_operational_question_reports_the_recorded_cause_not_an_llm_guess():
    redis = FakeRedis()
    VoicePerceptionRecorder(redis).record(_event("voice:ignored", 100.0))

    answer = voice_perception_answer(
        "Euri, hai sentito quello che e' successo poco fa?",
        redis,
        now=101.0,
    )

    assert "mancava la wake word" in answer
    assert "SpeakerAuth aveva verificato la voce" in answer
    assert "non aveva verificato" not in answer
    assert "non il contenuto della trascrizione" in answer


def test_perception_question_gate_does_not_capture_general_hearsay():
    assert is_voice_perception_question("Mi hai sentito?")
    assert is_voice_perception_question("Perche' non mi hai risposto?")
    assert not is_voice_perception_question("Hai sentito parlare della nuova pompa?")
    assert voice_perception_answer(
        "Hai sentito parlare della nuova pompa?",
        FakeRedis(),
        now=100.0,
    ) == ""


if __name__ == "__main__":
    tests = [globals()[name] for name in sorted(globals()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"test_voice_perception: OK ({len(tests)} casi)")
