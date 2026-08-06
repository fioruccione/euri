#!/usr/bin/env python3
"""Regressioni del trasporto TTS segmentato (nessun hardware reale)."""
from __future__ import annotations

import time

import numpy as np

from voice.tts import split_for_speech
from voice.tts_pipeline import run_tts_pipeline


def test_split_for_speech_preserves_order_and_prefers_sentences():
    text = "Prima frase breve. Seconda frase con il valore 3.8 corretto! Terza frase?"
    chunks = split_for_speech(text, max_chars=45)

    assert chunks == [
        "Prima frase breve.",
        "Seconda frase con il valore 3.8 corretto!",
        "Terza frase?",
    ]
    assert " ".join(chunks) == text


def test_split_for_speech_wraps_an_exceptionally_long_sentence():
    text = " ".join(["parola"] * 90)
    chunks = split_for_speech(text, max_chars=120)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert " ".join(chunks) == text


def test_pipeline_synthesizes_next_chunk_while_current_chunk_plays():
    chunks = ["primo segmento", "secondo segmento"]
    synth_calls: list[str] = []
    playback_calls: list[tuple[int, bool]] = []
    second_synth_completed = False

    def _fake_synthesize(text):
        nonlocal second_synth_completed
        synth_calls.append(text)
        time.sleep(0.02)
        if len(synth_calls) == 2:
            second_synth_completed = True
        return np.zeros(100, dtype=np.float32), 100

    def _fake_play(samples, sample_rate, index):
        playback_calls.append((len(samples), index == 0))
        if len(playback_calls) == 1:
            deadline = time.monotonic() + 0.5
            while not second_synth_completed and time.monotonic() < deadline:
                time.sleep(0.005)
            assert second_synth_completed
        return False

    result = run_tts_pipeline(
        chunks,
        synthesize=_fake_synthesize,
        play=_fake_play,
    )

    assert synth_calls == chunks
    assert playback_calls == [(100, True), (100, False)]
    assert result.played_chunks == 2
    assert result.interrupted is False


if __name__ == "__main__":
    test_split_for_speech_preserves_order_and_prefers_sentences()
    test_split_for_speech_wraps_an_exceptionally_long_sentence()
    test_pipeline_synthesizes_next_chunk_while_current_chunk_plays()
    print("test_tts_segmentation: OK")
