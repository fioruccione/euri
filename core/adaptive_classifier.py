"""
Adaptive Fingerprints — classificatore intent con Welford online learning.

Differenze rispetto al vecchio EmbeddingClassifier statico:
- Stato Welford (n, mean, M) persistito in Redis → sopravvive ai restart
- Threshold adattiva: base * (1 + β * σ) — si allarga o stringe in base alla varianza appresa
- Cold start dai prototipi statici, poi affina sul vocabolario reale dell'utente
- update(text, intent) chiamato dall'LLM fallback → feedback loop automatico

Complessità: O(1) per update (algoritmo di Welford), O(n_intents) per classify.
"""
from __future__ import annotations

import json
import numpy as np
from loguru import logger

import config
from core.intent_router import Intent

# ── Prototipi di partenza (cold start) ─────────────────────────────────────
# Stesse frasi dell'EmbeddingClassifier — il Welford le usa come primo campione.
_SEED_PROTOTYPES: dict[Intent, list[str]] = {
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

_REDIS_PREFIX = "euri:adaptive:intent:"


class _WelfordState:
    """
    Stato online di Welford per un singolo intent.
    Mantiene: n (conteggio), mean (centroide), M (somma varianze per Welford).
    """
    __slots__ = ("n", "mean", "M")

    def __init__(self, n: int, mean: np.ndarray, M: np.ndarray):
        self.n = n
        self.mean = mean  # shape (DIM,)
        self.M = M        # shape (DIM,) — somma delle varianze (Welford)

    @property
    def variance(self) -> np.ndarray:
        """Varianza per componente. Restituisce zero se n < 2."""
        if self.n < 2:
            return np.zeros_like(self.mean)
        return self.M / (self.n - 1)

    @property
    def std(self) -> float:
        """Deviazione standard scalare (media delle std per componente)."""
        return float(np.mean(np.sqrt(np.maximum(self.variance, 0))))

    @property
    def centroid(self) -> np.ndarray:
        """Centroide normalizzato per cosine similarity."""
        norm = np.linalg.norm(self.mean)
        if norm == 0:
            return self.mean
        return self.mean / norm

    def update(self, vec: np.ndarray) -> None:
        """Aggiorna online con un nuovo vettore (algoritmo di Welford)."""
        self.n += 1
        delta = vec - self.mean
        self.mean += delta / self.n
        delta2 = vec - self.mean
        self.M += delta * delta2

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "mean": self.mean.tolist(),
            "M": self.M.tolist(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_WelfordState":
        return cls(
            n=d["n"],
            mean=np.array(d["mean"], dtype=np.float32),
            M=np.array(d["M"], dtype=np.float32),
        )


class AdaptiveClassifier:
    """
    Classificatore intent Welford-based con persistenza Redis.

    Lifecycle:
      1. setup(embedder, r) — carica o inizializza da prototipi
      2. classify(text) → Intent | None
      3. update(text, intent) — chiamato dall'LLM fallback per raffinare
    """

    def __init__(self):
        self._embedder = None
        self._r = None
        self._states: dict[Intent, _WelfordState] = {}
        self._ready = False

    # ── Setup ──────────────────────────────────────────────────────────────

    def setup(self, embedder, r) -> None:
        """Inietta embedder e Redis client, carica o inizializza lo stato."""
        self._embedder = embedder
        self._r = r

        if not embedder or not embedder.available:
            logger.warning("AdaptiveClassifier: embedder non disponibile — layer disabilitato")
            return

        loaded = self._load_from_redis()
        if not loaded:
            logger.info("AdaptiveClassifier: cold start — inizializzazione da prototipi seed")
            self._init_from_prototypes()
            self._save_to_redis()
        else:
            logger.info(
                f"AdaptiveClassifier: caricato da Redis — "
                f"{len(self._states)} intent, "
                f"campioni: {', '.join(f'{i.value}={s.n}' for i, s in self._states.items())}"
            )

        self._ready = True
        logger.info(
            f"AdaptiveClassifier: pronto — {len(self._states)} intent, "
            f"threshold adattiva β={config.ADAPTIVE_CLASSIFIER_VARIANCE_BETA}"
        )

    def _init_from_prototypes(self) -> None:
        """Inizializza i centroidi Welford dai prototipi seed."""
        for intent, phrases in _SEED_PROTOTYPES.items():
            vecs = []
            for phrase in phrases:
                vec = self._embedder.encode(phrase, mode="passage")
                if vec is not None:
                    vecs.append(vec.astype(np.float32))
            if not vecs:
                continue

            # Inizializza Welford con tutti i vettori seed
            n = len(vecs)
            mean = np.mean(vecs, axis=0).astype(np.float32)
            M = np.zeros_like(mean)
            for vec in vecs:
                # Ricalcola M per Welford corretto
                pass
            # Calcola M dalla varianza campionaria
            if n >= 2:
                M = np.var(np.array(vecs), axis=0, ddof=1) * (n - 1)
                M = M.astype(np.float32)

            self._states[intent] = _WelfordState(n=n, mean=mean, M=M)

    def _load_from_redis(self) -> bool:
        """Carica stato Welford da Redis. Ritorna True se trovato."""
        if not self._r:
            return False
        loaded = {}
        for intent in _SEED_PROTOTYPES:
            key = f"{_REDIS_PREFIX}{intent.value}"
            try:
                raw = self._r.get(key)
                if raw:
                    loaded[intent] = _WelfordState.from_dict(json.loads(raw))
            except Exception as e:
                logger.debug(f"AdaptiveClassifier: errore caricamento {intent.value}: {e}")

        if len(loaded) == len(_SEED_PROTOTYPES):
            self._states = loaded
            return True
        return False

    def _save_to_redis(self) -> None:
        """Persiste stato Welford su Redis (senza TTL — permanente)."""
        if not self._r:
            return
        for intent, state in self._states.items():
            key = f"{_REDIS_PREFIX}{intent.value}"
            try:
                self._r.set(key, json.dumps(state.to_dict()))
            except Exception as e:
                logger.debug(f"AdaptiveClassifier: errore salvataggio {intent.value}: {e}")

    # ── Classificazione ────────────────────────────────────────────────────

    def classify(self, text: str) -> Intent | None:
        """
        Classifica l'utterance contro i centroidi Welford.
        Usa threshold adattiva: base * (1 + β * σ) per intent.
        Ritorna None se nessun intent supera la soglia.
        """
        if not self._ready:
            return None

        vec = self._embedder.encode(text, mode="query")
        if vec is None:
            return None

        vec_f = vec.astype(np.float32)
        norm = np.linalg.norm(vec_f)
        if norm == 0:
            return None
        vec_norm = vec_f / norm

        base = config.ADAPTIVE_CLASSIFIER_BASE_THRESHOLD
        beta = config.ADAPTIVE_CLASSIFIER_VARIANCE_BETA

        best_intent: Intent | None = None
        best_score: float = 0.0
        best_threshold: float = base

        for intent, state in self._states.items():
            adaptive_threshold = base * (1 + beta * state.std)
            score = float(np.dot(vec_norm, state.centroid))
            if score > best_score:
                best_score = score
                best_intent = intent
                best_threshold = adaptive_threshold

        # Soglie minime per intent ad alto costo con e5-large (1024-dim).
        # e5-large ha vicinato semantico più ampio di MiniLM: frasi generiche
        # raggiungono sim=0.84 su EXECUTE/STATUS. Sotto la soglia minima → LLM.
        _MIN_THRESHOLDS = {
            Intent.SILENCE_MODE: 0.88,
            Intent.SHUTDOWN:     0.90,
            Intent.EXECUTE:      0.88,
            Intent.STATUS:       0.88,
        }
        effective_threshold = max(best_threshold, _MIN_THRESHOLDS.get(best_intent, 0.0))

        if best_score >= effective_threshold:
            logger.info(
                f"AdaptiveClassifier: '{text[:50]}' → {best_intent.value} "
                f"(sim={best_score:.3f}, thr={effective_threshold:.3f}, "
                f"n={self._states[best_intent].n}, σ={self._states[best_intent].std:.3f})"
            )
            return best_intent

        logger.debug(
            f"AdaptiveClassifier: '{text[:50]}' sotto soglia "
            f"(best={best_score:.3f} < {effective_threshold:.3f}) → LLM"
        )
        return None

    # ── Aggiornamento (feedback loop) ──────────────────────────────────────

    def update(self, text: str, intent_value: str) -> None:
        """
        Aggiorna il centroide Welford con una nuova frase classificata correttamente dall'LLM.
        Chiamato automaticamente dopo ogni LLM fallback riuscito.
        """
        if not self._ready:
            return

        # Trova l'Intent corrispondente
        target: Intent | None = None
        for intent in _SEED_PROTOTYPES:
            if intent.value == intent_value:
                target = intent
                break
        if target is None or target not in self._states:
            return

        vec = self._embedder.encode(text, mode="query")
        if vec is None:
            return

        state = self._states[target]
        state.update(vec.astype(np.float32))
        self._save_to_redis()

        logger.info(
            f"AdaptiveClassifier: aggiornato {target.value} "
            f"(n={state.n}, σ={state.std:.3f})"
        )
