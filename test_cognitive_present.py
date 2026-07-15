"""Unit test puro del contratto Cognitive Present, senza daemon o Redis."""
import sys

from core.cognitive_present import (
    CognitivePresent,
    EpistemicStatus,
    InteractionChannel,
    InteractionPhase,
)


def test_provenance_and_expiry():
    present = CognitivePresent(clock=lambda: 100.0)
    assert not present.snapshot().conversation_open()
    present.observe(
        "owner_present",
        True,
        status=EpistemicStatus.OBSERVED,
        source="visual_gate",
        ttl_s=8,
    )
    snap = present.snapshot()
    owner = snap.observation("owner_present")
    assert owner is not None and owner.is_grounded(107.9)
    assert not owner.is_current(108.1)

    present.observe(
        "camera_intent",
        "Stefano sembra voler parlare",
        status=EpistemicStatus.INFERRED,
        source="visual_gate",
    )
    assert not present.snapshot().observation("camera_intent").is_grounded(100.0)


def test_semantic_version_and_revalidation():
    present = CognitivePresent(clock=lambda: 100.0)
    present.observe(
        "owner_present",
        True,
        status=EpistemicStatus.OBSERVED,
        source="visual_gate",
        ttl_s=8,
    )
    token = present.issue_decision_token("owner_present")
    version = present.version

    # Refresh uguale: aggiorna la freschezza, non invalida semanticamente.
    present.observe(
        "owner_present",
        True,
        status=EpistemicStatus.OBSERVED,
        source="visual_gate",
        observed_at=104.0,
        ttl_s=8,
    )
    assert present.version == version
    assert present.revalidate(
        token,
        require_phase=InteractionPhase.LISTENING,
        require_grounded=("owner_present",),
        now=111.0,
    ) == (True, "current")
    assert present.revalidate(token, now=112.1) == (False, "stale_observation:owner_present")

    # Cambiare la policy TTL e' semantico; rinfrescare lo stesso TTL non lo e'.
    present.observe(
        "owner_present",
        True,
        status=EpistemicStatus.OBSERVED,
        source="visual_gate",
        observed_at=113.0,
        ttl_s=12,
    )
    assert present.version == version + 1

    present.accept_user_turn("Aspetta", at=114.0)
    assert present.revalidate(token, now=113.0) == (False, "state_version_changed")


def test_observations_are_immutable_and_monotonic():
    present = CognitivePresent(clock=lambda: 10.0)
    source_value = {"identity": "stefano", "signals": ["face", "voice"]}
    item = present.observe(
        "owner",
        source_value,
        status=EpistemicStatus.OBSERVED,
        source="sensor_fusion",
        observed_at=10.0,
        ttl_s=8,
    )
    source_value["identity"] = "altered"
    assert item.value == (("identity", "stefano"), ("signals", ("face", "voice")))

    version = present.version
    stale = present.observe(
        "owner",
        "unknown",
        status=EpistemicStatus.OBSERVED,
        source="sensor_fusion",
        observed_at=9.0,
        ttl_s=8,
    )
    assert stale is item
    assert present.version == version
    assert present.snapshot().observation("owner") is item


def test_conversation_lease_starts_after_long_speech():
    present = CognitivePresent(conversation_window_s=45, clock=lambda: 0.0)
    present.accept_user_turn("Euri, dimmi", channel=InteractionChannel.VOICE, at=100.0)
    present.begin_speech(at=110.0, opens_conversation=True)

    # Quasi un minuto di playback non deve bruciare la finestra di follow-up.
    present.finish_speech(at=170.0)
    snap = present.snapshot(now=210.0)
    assert snap.phase is InteractionPhase.LISTENING
    assert snap.conversation_open(214.9)
    assert not snap.conversation_open(215.1)


def test_pending_question_is_versioned():
    present = CognitivePresent(clock=lambda: 1.0)
    v0 = present.version
    present.set_pending_question("insight:1", "Ti torna?")
    assert present.version == v0 + 1
    assert present.snapshot().pending_question_id == "insight:1"
    assert not present.clear_pending_question("insight:2")
    assert present.clear_pending_question("insight:1")


def test_processing_can_finish_without_tts():
    present = CognitivePresent(conversation_window_s=45, clock=lambda: 0.0)
    present.accept_user_turn("messaggio testuale", channel=InteractionChannel.TEXT, at=10.0)
    present.finish_processing(opens_conversation=True, at=20.0)
    snap = present.snapshot(now=60.0)
    assert snap.phase is InteractionPhase.LISTENING
    assert snap.conversation_open()


def test_focus_outlives_turn_lease_and_uses_only_accepted_user_turns():
    present = CognitivePresent(
        conversation_window_s=45,
        focus_window_s=300,
        max_focus_turns=2,
        clock=lambda: 0.0,
    )
    present.accept_user_turn("Stiamo controllando i provini IZOD", at=100.0)
    present.finish_processing(at=101.0)
    present.accept_user_turn("Forse i pezzi erano messi male", at=130.0)
    present.finish_processing(at=131.0)
    present.accept_user_turn("Il frigorifero controlla la temperatura", at=160.0)

    snap = present.snapshot(now=250.0)
    assert not snap.conversation_open(250.0)
    assert snap.focus_open(250.0)
    assert "provini IZOD" not in snap.focus_text()  # cap a due turni, nessuna sintesi
    assert "pezzi erano messi male" in snap.focus_text()
    assert "frigorifero" in snap.focus_text()
    assert not snap.focus_open(460.1)


if __name__ == "__main__":
    test_provenance_and_expiry()
    test_semantic_version_and_revalidation()
    test_observations_are_immutable_and_monotonic()
    test_conversation_lease_starts_after_long_speech()
    test_pending_question_is_versioned()
    test_processing_can_finish_without_tts()
    test_focus_outlives_turn_lease_and_uses_only_accepted_user_turns()
    print("PASS")
