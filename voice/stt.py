"""
Speech-to-Text con faster-whisper.
Modello large-v3-turbo, ottimizzato per GPU NVIDIA con CUDA.
"""
import gc
import subprocess
import numpy as np
from loguru import logger
import config
from faster_whisper import WhisperModel

class STT:
    def __init__(self):
        self._loaded = False
        self.model = None

    @staticmethod
    def _cuda_candidates() -> list[tuple[int, int | None]]:
        """GPU candidate ordinate per VRAM libera; fallback stabile senza NVML."""
        configured = str(getattr(config, "WHISPER_CUDA_DEVICE_INDEX", "auto")).strip().lower()
        if configured != "auto":
            try:
                return [(int(configured), None)]
            except ValueError:
                logger.warning(
                    f"WHISPER_CUDA_DEVICE_INDEX non valido ({configured!r}); uso selezione automatica"
                )

        nvml = None
        try:
            import pynvml

            nvml = pynvml
            nvml.nvmlInit()
            devices = []
            for index in range(nvml.nvmlDeviceGetCount()):
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                free_bytes = int(nvml.nvmlDeviceGetMemoryInfo(handle).free)
                devices.append((index, free_bytes))
            return sorted(devices, key=lambda item: item[1], reverse=True) or [(0, None)]
        except Exception as exc:
            logger.debug(f"Selezione GPU Whisper: NVML non disponibile ({exc})")
        finally:
            if nvml is not None:
                try:
                    nvml.nvmlShutdown()
                except Exception:
                    pass

        try:
            output = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=3,
            )
            devices = []
            for line in output.splitlines():
                index_raw, free_mib_raw = (part.strip() for part in line.split(",", 1))
                devices.append((int(index_raw), int(float(free_mib_raw)) * 1024 * 1024))
            return sorted(devices, key=lambda item: item[1], reverse=True) or [(0, None)]
        except Exception as exc:
            logger.debug(f"Selezione GPU Whisper: nvidia-smi non disponibile ({exc})")
            return [(0, None)]

    def load(self):
        candidates = self._cuda_candidates()
        last_error = None
        dummy = np.zeros(16000, dtype=np.float32)

        for position, (device_index, free_bytes) in enumerate(candidates):
            free_label = (
                f", VRAM libera {free_bytes / (1024 ** 3):.1f} GiB"
                if free_bytes is not None else ""
            )
            logger.info(
                f"Caricamento Whisper {config.WHISPER_MODEL} su CUDA:{device_index}{free_label}..."
            )
            try:
                # Sulla workstation condivisa l'ordine di avvio degli altri servizi
                # cambia la distribuzione della VRAM: device_index evita di assumere GPU 0.
                self.model = WhisperModel(
                    config.WHISPER_MODEL,
                    device="cuda",
                    device_index=device_index,
                    compute_type="float16",
                )
                list(self.model.transcribe(dummy, language="it"))
                self._loaded = True
                logger.info(f"Whisper pronto su CUDA:{device_index}")
                return
            except RuntimeError as exc:
                last_error = exc
                self.model = None
                gc.collect()
                is_oom = "out of memory" in str(exc).lower()
                has_fallback = position + 1 < len(candidates)
                if not (is_oom and has_fallback):
                    raise
                logger.warning(
                    f"Whisper: CUDA:{device_index} senza VRAM sufficiente; provo la GPU successiva"
                )

        if last_error is not None:
            raise last_error

    def transcribe(self, audio: np.ndarray, force_lang: str | None = config.WHISPER_LANGUAGE) -> tuple[str, str]:
        """
        Trascrive un segmento audio.
        audio: float32 numpy array a 16kHz mono.
        force_lang: None = auto-detect (usato in modalità traduzione bidirezionale).
        Ritorna (testo, lingua_rilevata). Testo vuoto se niente.
        """
        segments, info = self.model.transcribe(
            audio,
            language=force_lang,
            beam_size=5,
            initial_prompt=config.WHISPER_INITIAL_PROMPT,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        
        text = " ".join([segment.text for segment in segments]).strip()
        lang = info.language if info else force_lang or "it"

        if text:
            logger.info(f"STT: '{text}' (lang={lang})")
        else:
            logger.debug("STT: nessun testo rilevato")

        return text, lang
