"""
Classificatore intent basato su embedding semantici.

Livello intermedio tra regex (0ms) e LLM fallback (600ms).
Usa il modello paraphrase-multilingual-MiniLM-L12-v2 già caricato dall'Embedder
per confrontare l'utterance con prototipi rappresentativi per ogni intent.

Latenza attesa: ~20ms su CPU, ~5ms su GPU.
"""
from __future__ import annotations

import numpy as np
from loguru import logger
from core.intent_router import Intent

# Frasi prototipo per ogni intent classificabile via embedding.
# Ogni lista deve coprire le variazioni colloquiali, implicite e parafrasi
# più comuni — NON i trigger espliciti (quelli li gestiscono già le regex).
_PROTOTYPES: dict[Intent, list[str]] = {
    Intent.WEB_SEARCH: [
        "me lo cerchi su internet?",
        "vai a vedere online",
        "guarda cosa c'è scritto in rete",
        "cerca questa cosa sul web",
        "potresti cercarlo online?",
        "fai una ricerca su internet",
        "voglio sapere cosa dice il web",
        "controlla se esiste online",
        "trova informazioni su internet",
    ],
    Intent.SEARCH: [
        "ce l'hai in memoria?",
        "lo sapevi già?",
        "te l'avevo detto?",
        "hai qualcosa su questo argomento?",
        "ti ricordi qualcosa?",
        "cosa sai già di questo?",
        "hai informazioni su questo?",
        "è già nei tuoi ricordi?",
        "hai già sentito parlare di questo?",
    ],
    Intent.SAVE_MEMORY: [
        "tienitelo a mente",
        "è importante che tu sappia",
        "salvalo per dopo",
        "non dimenticartelo",
        "voglio che tu sappia questa cosa",
        "annotati questa informazione",
        "è un dato che dovresti tenere",
        "mettilo in memoria",
    ],
    Intent.SAVE_TODO: [
        "ho bisogno di ricordarmi di farlo",
        "segnami questo impegno",
        "metti in agenda",
        "non voglio dimenticarmi",
        "devo fare una cosa entro domani",
        "ricordamelo più tardi",
        "ho un appuntamento",
        "devo chiamare qualcuno",
        "mettimi un promemoria",
    ],
    Intent.EXECUTE: [
        "dimmi come sta la macchina",
        "tutto ok il sistema?",
        "quanta memoria libera ho?",
        "come va la CPU?",
        "controlla le risorse",
        "cosa sta girando?",
        "che processi ci sono attivi?",
        "com'è la situazione del disco?",
        "verifica lo stato del sistema",
    ],
    Intent.STATUS: [
        "come stai?",
        "tutto bene?",
        "sei operativo?",
        "come va?",
        "raccontami come stai",
        "cosa hai in sospeso?",
        "dimmi la tua situazione",
    ],
    Intent.SILENCE_MODE: [
        "non disturbarmi per un po'",
        "ho bisogno di silenzio",
        "sto lavorando, non interrompere",
        "non voglio essere disturbato",
        "mettiti in standby",
        "fai il silenzio",
    ],
    Intent.SHUTDOWN: [
        "puoi fermarti?",
        "basta per oggi",
        "chiuditi",
        "smetti di girare",
        "fermati",
        "puoi spegnerti?",
    ],
}


class EmbeddingClassifier:
    """
    Classifica gli intent usando cosine similarity sugli embedding.
    Singleton: carica i prototipi una volta sola al primo utilizzo.
    """

    def __init__(self):
        self._embedder = None
        self._prototypes: dict[Intent, np.ndarray] | None = None  # media degli embedding per intent
        self._ready = False

    def setup(self, embedder) -> None:
        """Inietta l'Embedder già caricato da voice_daemon e pre-calcola i prototipi."""
        self._embedder = embedder
        if not embedder or not embedder.available:
            logger.warning("EmbeddingClassifier: embedder non disponibile — layer disabilitato")
            return
        self._build_prototypes()
        self._ready = True
        logger.info(f"EmbeddingClassifier: pronto con {len(self._prototypes)} intent prototipo")

    def _build_prototypes(self) -> None:
        """Pre-calcola il centroide (media) degli embedding per ogni intent."""
        self._prototypes = {}
        for intent, phrases in _PROTOTYPES.items():
            vecs = []
            for phrase in phrases:
                vec = self._embedder.encode(phrase)
                if vec is not None:
                    vecs.append(vec)
            if vecs:
                centroid = np.mean(vecs, axis=0)
                # Normalizza per cosine similarity efficiente
                norm = np.linalg.norm(centroid)
                if norm > 0:
                    centroid = centroid / norm
                self._prototypes[intent] = centroid

    def classify(self, text: str, threshold: float = 0.72) -> Intent | None:
        """
        Ritorna l'intent più simile se la similarity supera la soglia,
        altrimenti None (passa all'LLM fallback).

        Args:
            text: utterance da classificare
            threshold: soglia minima di cosine similarity (default 0.72)
        """
        if not self._ready or not self._prototypes:
            return None

        vec = self._embedder.encode(text)
        if vec is None:
            return None

        # Normalizza il vettore query
        norm = np.linalg.norm(vec)
        if norm == 0:
            return None
        vec_norm = vec / norm

        best_intent: Intent | None = None
        best_score: float = 0.0

        for intent, prototype in self._prototypes.items():
            score = float(np.dot(vec_norm, prototype))  # cosine similarity
            if score > best_score:
                best_score = score
                best_intent = intent

        if best_score >= threshold:
            logger.info(
                f"EmbeddingClassifier: '{text[:50]}' → {best_intent.value} "
                f"(similarity={best_score:.3f})"
            )
            return best_intent

        logger.debug(
            f"EmbeddingClassifier: '{text[:50]}' sotto soglia "
            f"(best={best_score:.3f} < {threshold}) → passa a LLM"
        )
        return None
