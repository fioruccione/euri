"""
Voice Activity Detection con silero-vad.
Accumula chunk audio e segnala inizio/fine parlato.
"""
import numpy as np
import torch
from loguru import logger
import config


class VAD:
    """
    Wrapper silero-vad per uso streaming chunk-by-chunk.
    Usa VADIterator: ogni chunk da 30ms entra, esce speech/silence.
    """

    def __init__(self):
        self._model = None
        self._iterator = None
        self._audio_buffer: list[np.ndarray] = []
        self._is_speaking = False

    def load(self):
        logger.info("Caricamento modello silero-vad...")
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        (_, _, _, VADIterator, _) = utils
        self._model = model
        self._iterator = VADIterator(
            model,
            threshold=config.VAD_THRESHOLD,
            sampling_rate=config.VAD_SAMPLING_RATE,
            min_silence_duration_ms=config.VAD_SILENCE_DURATION_MS,
            speech_pad_ms=300,
        )
        logger.info("silero-vad pronto")

    def process_chunk(self, chunk: np.ndarray) -> tuple[bool, np.ndarray | None]:
        """
        Processa un chunk da 30ms.

        Ritorna (speech_ended, audio_segment):
        - speech_ended=False, audio_segment=None → ancora in ascolto
        - speech_ended=True, audio_segment=np.ndarray → segmento completo pronto per STT
        """
        tensor = torch.from_numpy(chunk)
        speech_dict = self._iterator(tensor, return_seconds=False)

        if speech_dict:
            if "start" in speech_dict:
                self._is_speaking = True
                self._audio_buffer = []

            if self._is_speaking:
                self._audio_buffer.append(chunk)

            if "end" in speech_dict and self._is_speaking:
                self._is_speaking = False
                segment = np.concatenate(self._audio_buffer)
                self._audio_buffer = []
                duration_s = len(segment) / config.VAD_SAMPLING_RATE
                logger.debug(f"Parlato rilevato: {duration_s:.1f}s")
                return True, segment

        elif self._is_speaking:
            self._audio_buffer.append(chunk)

        return False, None

    @property
    def is_speaking(self) -> bool:
        """True dal trigger di inizio voce fino alla chiusura del segmento."""
        return self._is_speaking

    def reset(self):
        """Reset stato interno (usa dopo ogni segmento processato)."""
        if self._iterator:
            self._iterator.reset_states()
        self._audio_buffer = []
        self._is_speaking = False
