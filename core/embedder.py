"""
Sentence embedding su CPU per ricerca semantica nella memoria di Euri.
Modello multilingue ottimizzato per italiano — gira su CPU senza competere con GPU/Whisper/Ollama.
"""
import numpy as np
from loguru import logger

_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
DIM = 384


class Embedder:
    def __init__(self):
        self._model = None

    def load(self):
        from sentence_transformers import SentenceTransformer
        logger.info(f"Caricamento modello embedding ({_MODEL_NAME})...")
        self._model = SentenceTransformer(_MODEL_NAME, device="cpu")
        # warmup — forza caricamento pesi prima dell'uso reale
        self._model.encode("test", convert_to_numpy=True, normalize_embeddings=True)
        logger.info(f"Embedder pronto — dim: {DIM}, device: cpu")

    def encode(self, text: str) -> np.ndarray | None:
        """Restituisce vettore float32 normalizzato (cosine similarity = dot product)."""
        if self._model is None:
            return None
        try:
            return self._model.encode(
                text,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).astype(np.float32)
        except Exception as e:
            logger.error(f"Errore embedding: {e}")
            return None

    @property
    def available(self) -> bool:
        return self._model is not None
