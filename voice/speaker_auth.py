"""
SpeakerAuth — verifica dell'identità vocale prima dell'esecuzione dei comandi.

Usa Resemblyzer (GE2E encoder) per produrre d-vector a 256 dimensioni.
La verifica avviene tramite cosine similarity con il voiceprint di Stefano.

Flusso:
  Enrollment: 3 utterance → embedding medio → salvo su disco
  Verifica:   1 utterance → embedding → cosine similarity vs voiceprint

Dipendenze: resemblyzer (installa anche librosa, numba)
"""
import numpy as np
from pathlib import Path
from loguru import logger
import config


VOICEPRINT_PATH = Path.home() / "euri" / "models" / "voiceprint.npy"
SIMILARITY_THRESHOLD = 0.65   # cosine similarity minima — abbassa se troppe false reject
ENROLL_UTTERANCES = 3         # frasi da registrare per l'enrollment


class SpeakerAuth:
    def __init__(self):
        self._encoder = None
        self._voiceprint: np.ndarray | None = None
        self._enabled = False

    def load(self):
        """Carica il modello GE2E. Fail-open se non disponibile."""
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder()
            logger.info("SpeakerAuth: modello GE2E caricato")

            if VOICEPRINT_PATH.exists():
                self._voiceprint = np.load(VOICEPRINT_PATH)
                self._enabled = True
                logger.info("SpeakerAuth: voiceprint caricato — autenticazione attiva")
            else:
                logger.info("SpeakerAuth: nessun voiceprint — modalità aperta (fai enrollment)")
        except Exception as e:
            logger.warning(f"SpeakerAuth non disponibile ({e}) — autenticazione disabilitata")

    def is_enrolled(self) -> bool:
        return self._voiceprint is not None

    def is_enabled(self) -> bool:
        return self._enabled and self._encoder is not None

    def verify(self, audio: np.ndarray, sample_rate: int = 16000) -> bool:
        """
        Verifica se l'audio appartiene a Stefano.
        Ritorna True se autenticato (o se autenticazione non attiva).
        audio: float32 array normalizzato in [-1, 1]
        """
        if config.DEMO_MODE:
            return True  # modalità demo: accetta tutti

        if not self.is_enabled():
            return True  # fail-open

        if len(audio) < sample_rate * 0.5:
            return True  # troppo corto per analisi affidabile — lascia passare

        try:
            embedding = self._embed(audio, sample_rate)
            similarity = float(np.dot(embedding, self._voiceprint) /
                               (np.linalg.norm(embedding) * np.linalg.norm(self._voiceprint)))
            logger.debug(f"SpeakerAuth similarity: {similarity:.3f} (soglia {SIMILARITY_THRESHOLD})")
            return similarity >= SIMILARITY_THRESHOLD
        except Exception as e:
            logger.warning(f"SpeakerAuth verify error: {e} — fail-open")
            return True

    def enroll_from_segments(self, segments: list[np.ndarray], sample_rate: int = 16000) -> bool:
        """
        Crea il voiceprint da una lista di utterance audio.
        Ritorna True se enrollment completato con successo.
        """
        if self._encoder is None:
            return False
        try:
            embeddings = []
            for seg in segments:
                if len(seg) >= sample_rate * 0.5:
                    emb = self._embed(seg, sample_rate)
                    embeddings.append(emb)

            if len(embeddings) < 2:
                logger.warning("SpeakerAuth: troppo poco audio per enrollment")
                return False

            voiceprint = np.mean(embeddings, axis=0)
            voiceprint /= np.linalg.norm(voiceprint)  # normalizza

            VOICEPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.save(VOICEPRINT_PATH, voiceprint)
            self._voiceprint = voiceprint
            self._enabled = True
            logger.info(f"SpeakerAuth: voiceprint salvato ({len(embeddings)} utterance)")
            return True
        except Exception as e:
            logger.error(f"SpeakerAuth enrollment error: {e}")
            return False

    def disable(self):
        """Disabilita temporaneamente la verifica (es. modalità ospite)."""
        self._enabled = False
        logger.info("SpeakerAuth: autenticazione disabilitata temporaneamente")

    def enable(self):
        if self._voiceprint is not None:
            self._enabled = True
            logger.info("SpeakerAuth: autenticazione riattivata")

    def _embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Produce il d-vector GE2E dall'audio."""
        from resemblyzer import preprocess_wav
        # Resemblyzer vuole float32 a 16kHz — già nel formato corretto
        wav = preprocess_wav(audio, source_sr=sample_rate)
        return self._encoder.embed_utterance(wav)
