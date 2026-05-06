"""
Sentence embedding su CPU per ricerca semantica nella memoria di Euri.
Modello: intfloat/multilingual-e5-base — multilingue, dim=768, asimmetrico query/passage.
Gira su CPU per non competere con Ollama/Whisper sulle GPU.
"""
import numpy as np
from loguru import logger

_MODEL_NAME = "intfloat/multilingual-e5-large"
DIM = 1024

_PREFIX = {"query": "query: ", "passage": "passage: "}


class Embedder:
    def __init__(self):
        self._model = None

    def load(self):
        from sentence_transformers import SentenceTransformer
        logger.info(f"Caricamento modello embedding ({_MODEL_NAME})...")
        self._model = SentenceTransformer(_MODEL_NAME, device="cpu")
        self._model.encode("test", convert_to_numpy=True, normalize_embeddings=True)
        logger.info(f"Embedder pronto — dim: {DIM}, device: cpu")

    def encode(self, text: str, mode: str = "passage") -> np.ndarray | None:
        """
        Restituisce vettore float32 normalizzato.
        mode='passage' → per salvare memorie/insights in Redis
        mode='query'   → per ricerche semantiche e classificazione intent
        """
        if self._model is None:
            return None
        try:
            prefixed = _PREFIX.get(mode, "") + text
            return self._model.encode(
                prefixed,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).astype(np.float32)
        except Exception as e:
            logger.error(f"Errore embedding: {e}")
            return None

    @property
    def available(self) -> bool:
        return self._model is not None
