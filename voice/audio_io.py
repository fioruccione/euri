"""
Gestione microfono e speaker via PyAudio.
Produce chunk da 30ms a 16kHz mono int16 — formato richiesto da silero-vad.
"""
import concurrent.futures
import time
import numpy as np
import pyaudio
import sounddevice as sd
from loguru import logger
import config

# Timeout massimo per la riproduzione audio via sounddevice.
# sd.wait() può bloccarsi indefinitamente se CoreAudio è degradato.
_SD_PLAY_TIMEOUT = 10  # secondi

# Flag: dopo il primo timeout/errore sounddevice viene bypassato per tutta la sessione.
# Evita 10s di attesa inutile su ogni risposta quando CoreAudio è rotto.
# Preimpostato a True se screen sharing è attivo (screensharingd rompe PortAudio).
def _check_screen_sharing() -> bool:
    import subprocess, sys
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["pgrep", "-x", "screensharingd"],
            capture_output=True, timeout=2
        )
        return result.returncode == 0
    except Exception:
        return False

_sd_disabled: bool = _check_screen_sharing() or getattr(config, "AUDIO_SKIP_SOUNDDEVICE", False)


def _find_output_device() -> int | None:
    """Cerca il device di output per nome (substring, case-insensitive) tra i device sounddevice."""
    target = getattr(config, "AUDIO_OUTPUT_DEVICE", None)
    if not target:
        return None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and target.lower() in d["name"].lower():
                return i
    except Exception:
        pass
    return None


_output_device: int | None = _find_output_device()
# I log su _sd_disabled e _output_device vengono emessi da setup() in voice_daemon.py
# (logger non ancora configurato qui a livello modulo)


class AudioCapture:
    """Stream continuo dal microfono. Blocca sul chunk da 30ms."""

    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._stream = None

    def _find_input_device(self) -> int | None:
        """Cerca il device di input per nome (substring, case-insensitive). Ritorna l'indice o None."""
        target = config.AUDIO_INPUT_DEVICE
        if not target:
            return None
        for i in range(self._pa.get_device_count()):
            d = self._pa.get_device_info_by_index(i)
            if d["maxInputChannels"] > 0 and target.lower() in d["name"].lower():
                logger.info(f"Microfono selezionato: [{i}] '{d['name']}'")
                return i
        logger.warning(f"Microfono '{target}' non trovato — uso device di default")
        return None

    def open(self):
        device_index = self._find_input_device()
        self._stream = self._pa.open(
            format=pyaudio.paInt16,
            channels=config.AUDIO_CHANNELS,
            rate=config.AUDIO_RATE,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=config.AUDIO_CHUNK_SAMPLES,
        )
        logger.info(f"Microfono aperto — rate={config.AUDIO_RATE}, chunk={config.AUDIO_CHUNK_SAMPLES} campioni")

    def read_chunk(self) -> np.ndarray:
        """Legge un chunk da 30ms. Ritorna array float32 normalizzato in [-1, 1]."""
        raw = self._stream.read(config.AUDIO_CHUNK_SAMPLES, exception_on_overflow=False)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return audio

    def close(self):
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        self._pa.terminate()
        logger.info("Microfono chiuso")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


def play_audio(samples: np.ndarray, sample_rate: int, stop_event=None) -> bool:
    """
    Riproduce audio dallo speaker.
    samples: numpy array float32 o int16.
    stop_event: threading.Event opzionale — se settato interrompe la riproduzione.
    Ritorna True se interrotto da stop_event, False se completato normalmente.
    """
    global _sd_disabled
    if samples.dtype != np.float32:
        samples = samples.astype(np.float32) / 32768.0

    if _sd_disabled:
        return _play_via_cli(samples, sample_rate, stop_event=stop_event)

    def _sd_play():
        sd.stop()
        sd.play(samples, samplerate=sample_rate, device=_output_device)
        sd.wait()

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_sd_play)
    ex.shutdown(wait=False)

    deadline = time.monotonic() + _SD_PLAY_TIMEOUT
    while not future.done():
        if stop_event is not None and stop_event.is_set():
            sd.stop()
            logger.info("sounddevice interrotto da comando vocale")
            return True
        if time.monotonic() > deadline:
            sd.stop()
            _sd_disabled = True
            logger.warning(f"sounddevice timeout ({_SD_PLAY_TIMEOUT}s) — disabilitato per la sessione, fallback CLI")
            return _play_via_cli(samples, sample_rate, stop_event=stop_event)
        time.sleep(0.05)

    try:
        future.result(timeout=0.1)
    except Exception as e:
        _sd_disabled = True
        logger.warning(f"sounddevice error: {e} — disabilitato per la sessione, fallback CLI")
        return _play_via_cli(samples, sample_rate, stop_event=stop_event)

    return False


def _play_via_cli(samples: np.ndarray, sample_rate: int, stop_event=None) -> bool:
    """
    Fallback cross-platform: scrive WAV temp e lo riproduce con aplay (Linux) o afplay (macOS).
    stop_event: threading.Event opzionale — se settato interrompe la riproduzione.
    Ritorna True se interrotto da stop_event, False se completato normalmente.
    """
    import tempfile, os, sys, soundfile as sf
    from subprocess import Popen, CalledProcessError
    
    is_mac = sys.platform == "darwin"
    player_cmd = "afplay" if is_mac else "aplay"
    
    tmp_path = None
    audio_duration = len(samples) / sample_rate
    afplay_timeout = max(5, min(600, audio_duration + 5))
    try:
        import subprocess as _sp
        _sp.run(["pkill", "-x", player_cmd], capture_output=True)
        time.sleep(0.15)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        sf.write(tmp_path, samples, sample_rate)

        proc = Popen([player_cmd, tmp_path])
        deadline = time.monotonic() + afplay_timeout

        while proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                proc.kill()
                proc.wait()
                logger.info(f"{player_cmd} interrotto da comando vocale")
                return True
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise TimeoutError(f"{player_cmd} timeout ({afplay_timeout:.0f}s)")
            time.sleep(0.05)

        rc = proc.returncode
        if rc == -2 or rc == 9:  # SIGINT = shutdown normale, 9 = kill
            return False
        if rc != 0:
            raise CalledProcessError(rc, player_cmd)
        return False

    except Exception as e:
        if getattr(e, "returncode", None) in (-2, 9):
            return False
        logger.error(f"{player_cmd} fallback non recuperabile: {e}")
        raise
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def play_audio_file(path: str):
    """Riproduce un file WAV direttamente."""
    import soundfile as sf
    data, sr = sf.read(path, dtype="float32")
    sd.play(data, samplerate=sr)
    sd.wait()
