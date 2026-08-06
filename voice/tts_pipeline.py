"""Pipeline audio TTS indipendente da hardware e logica cognitiva."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class TtsPipelineResult:
    first_ready_s: float
    synth_cpu_s: float
    playback_s: float
    wall_s: float
    played_chunks: int
    total_chunks: int
    interrupted: bool


def run_tts_pipeline(
    chunks: Sequence[str],
    *,
    synthesize: Callable[[str], tuple[object, int]],
    play: Callable[[object, int, int], bool],
    on_first_ready: Callable[[float], None] | None = None,
    on_chunk_played: Callable[[int], None] | None = None,
) -> TtsPipelineResult:
    """Sintetizza un segmento avanti mentre quello corrente viene riprodotto."""
    if not chunks:
        raise ValueError("pipeline TTS senza segmenti")

    def _timed_synthesis(chunk: str):
        started = time.perf_counter()
        samples, sample_rate = synthesize(chunk)
        return samples, sample_rate, time.perf_counter() - started

    pipeline_started = time.perf_counter()
    samples, sample_rate, first_ready = _timed_synthesis(chunks[0])
    synth_total = first_ready
    playback_total = 0.0
    played_chunks = 0
    interrupted = False
    if on_first_ready is not None:
        on_first_ready(first_ready)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="euri-tts") as pool:
        next_future = None
        for index, _chunk in enumerate(chunks):
            if index + 1 < len(chunks):
                next_future = pool.submit(_timed_synthesis, chunks[index + 1])

            playback_started = time.perf_counter()
            interrupted = play(samples, sample_rate, index)
            playback_total += time.perf_counter() - playback_started
            if interrupted:
                if next_future is not None and not next_future.cancel():
                    # Evita che il successivo ack usi Sherpa mentre il worker
                    # sta ancora terminando il segmento preparato in anticipo.
                    _, _, chunk_synth_elapsed = next_future.result()
                    synth_total += chunk_synth_elapsed
                break

            played_chunks = index + 1
            if on_chunk_played is not None:
                on_chunk_played(played_chunks)

            if next_future is not None:
                samples, sample_rate, chunk_synth_elapsed = next_future.result()
                synth_total += chunk_synth_elapsed

    return TtsPipelineResult(
        first_ready_s=first_ready,
        synth_cpu_s=synth_total,
        playback_s=playback_total,
        wall_s=time.perf_counter() - pipeline_started,
        played_chunks=played_chunks,
        total_chunks=len(chunks),
        interrupted=interrupted,
    )
