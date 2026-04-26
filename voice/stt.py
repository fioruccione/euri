"""
Speech-to-Text con faster-whisper.
Modello large-v3-turbo, ottimizzato per GPU NVIDIA con CUDA.
"""
import numpy as np
from loguru import logger
import config
from faster_whisper import WhisperModel

class STT:
    def __init__(self):
        self._loaded = False
        self.model = None

    def load(self):
        logger.info(f"Caricamento Whisper {config.WHISPER_MODEL} su CUDA...")
        # Usa CUDA con precisione FP16 per le RTX 4060 Ti
        self.model = WhisperModel(config.WHISPER_MODEL, device="cuda", compute_type="float16")
        
        # Warmup
        dummy = np.zeros(16000, dtype=np.float32)
        list(self.model.transcribe(dummy, language="it"))
        
        self._loaded = True
        logger.info("Whisper pronto")

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
