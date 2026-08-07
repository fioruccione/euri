"""Regressione: Whisper usa la GPU piu' libera e ritenta dopo un OOM."""
import sys
from unittest.mock import patch

from voice.stt import STT


class _FakeWhisper:
    attempts = []

    def __init__(self, _model, *, device, device_index, compute_type):
        assert device == "cuda"
        assert compute_type == "float16"
        self.attempts.append(device_index)
        if device_index == 1:
            raise RuntimeError("CUDA failed with error out of memory")

    def transcribe(self, *_args, **_kwargs):
        return [], None


def test_whisper_retries_next_gpu_after_oom():
    _FakeWhisper.attempts = []
    stt = STT()
    with patch.object(STT, "_cuda_candidates", return_value=[(1, 8 << 30), (0, 6 << 30)]), \
            patch("voice.stt.WhisperModel", _FakeWhisper):
        stt.load()

    assert _FakeWhisper.attempts == [1, 0]
    assert stt._loaded is True
    assert isinstance(stt.model, _FakeWhisper)


def test_gpu_candidates_fall_back_to_nvidia_smi():
    with patch.dict(sys.modules, {"pynvml": None}), \
            patch("voice.stt.subprocess.check_output", return_value="0, 2048\n1, 6144\n"):
        assert STT._cuda_candidates() == [(1, 6144 << 20), (0, 2048 << 20)]


if __name__ == "__main__":
    test_whisper_retries_next_gpu_after_oom()
    test_gpu_candidates_fall_back_to_nvidia_smi()
    print("2/2 test passati")
