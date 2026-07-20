"""
FaceAuth — riconoscimento dell'identità visiva (sorella di SpeakerAuth).

Usa SFace (OpenCV FaceRecognizerSF) per produrre embedding a 128 dimensioni
dal volto allineato sui landmark YuNet. Il confronto è cosine similarity
contro i faceprint delle persone abilitate.

Flusso:
  Enrollment: N frame con volto → N prototipi normalizzati → faceprints/<nome>.npy
  Identifica: 1 frame + face row YuNet → embedding → best prototype sopra soglia

I faceprint sono dati biometrici: restano file locali su disco, i frame non
vengono mai salvati. Le persone abilitate devono sapere di essere registrate.

Dipendenze: opencv-contrib-python (già richiesto dal VisualGate).
"""
import time

import numpy as np
from pathlib import Path
from loguru import logger
import config

_RELOAD_CHECK_S = 30.0   # ogni quanto controllare se i faceprint su disco sono cambiati
_MAX_PROTOTYPES = 8      # limita l'espansione dell'area biometrica di accettazione


class FaceAuth:
    def __init__(self):
        self._recognizer = None
        self._faceprints: dict[str, np.ndarray] = {}   # nome → matrice di prototipi
        self._disk_sig: tuple = ()                     # firma (nome, mtime) dei file all'ultimo reload
        self._last_reload_check: float = 0.0

    def load(self):
        """Carica il modello SFace e i faceprint. Fail-open se non disponibile:
        is_enabled() resta False e il gate non potrà mai dire "è il proprietario" —
        chi consuma deve ripiegare sulla voce autenticata recente."""
        if not getattr(config, "FACE_AUTH_ENABLED", True):
            logger.info("FaceAuth: disabilitato da config")
            return
        try:
            import cv2
            self._recognizer = cv2.FaceRecognizerSF_create(config.FACE_RECOG_MODEL, "")
            self.reload_faceprints()
            if self._faceprints:
                logger.info(f"FaceAuth: attivo — {len(self._faceprints)} faceprint ({', '.join(sorted(self._faceprints))})")
            else:
                logger.info("FaceAuth: modello caricato, nessun faceprint — fai enrollment (enroll_face.py)")
        except Exception as e:
            self._recognizer = None
            logger.warning(f"FaceAuth non disponibile ({e}) — riconoscimento visivo disabilitato")

    def reload_faceprints(self):
        """Rilegge i faceprint da disco (chiamabile a caldo dopo un enrollment)."""
        self._faceprints = {}
        fdir = Path(config.FACEPRINT_DIR)
        if not fdir.is_dir():
            return
        self._disk_sig = self._disk_signature()
        for f in fdir.glob("*.npy"):
            try:
                stored = np.asarray(np.load(f), dtype=np.float32)
                prototypes = stored.reshape(1, -1) if stored.ndim == 1 else stored
                prototypes = prototypes[:_MAX_PROTOTYPES]
                norms = np.linalg.norm(prototypes, axis=1, keepdims=True)
                valid = norms[:, 0] > 0
                if np.any(valid):
                    self._faceprints[f.stem] = prototypes[valid] / norms[valid]
            except Exception as e:
                logger.warning(f"FaceAuth: faceprint {f.name} illeggibile ({e})")

    def is_enabled(self) -> bool:
        """True se può davvero riconoscere: modello caricato E almeno un faceprint."""
        self._maybe_reload()
        return self._recognizer is not None and bool(self._faceprints)

    def _disk_signature(self) -> tuple:
        """Firma (nome, mtime) dei .npy su disco — cambia su enroll, update e revoca."""
        try:
            fdir = Path(config.FACEPRINT_DIR)
            return tuple(sorted((f.name, f.stat().st_mtime) for f in fdir.glob("*.npy")))
        except OSError:
            return self._disk_sig

    def _maybe_reload(self):
        """Auto-reload a caldo: la pagina di gestione (processo Streamlit) scrive/rimuove
        faceprint su disco — il daemon se ne accorge dalla firma dei file, senza restart
        e senza canali dedicati. Check al massimo ogni _RELOAD_CHECK_S."""
        if self._recognizer is None:
            return
        now = time.monotonic()
        if now - self._last_reload_check < _RELOAD_CHECK_S:
            return
        self._last_reload_check = now
        if self._disk_signature() != self._disk_sig:
            self.reload_faceprints()
            logger.info(f"FaceAuth: faceprint ricaricati da disco ({len(self._faceprints)}: "
                        f"{', '.join(sorted(self._faceprints)) or 'nessuno'})")

    def embed(self, frame: np.ndarray, face_row: np.ndarray) -> np.ndarray | None:
        """Embedding SFace dal frame BGR e dalla riga YuNet (box + 5 landmark)."""
        if self._recognizer is None:
            return None
        try:
            aligned = self._recognizer.alignCrop(frame, face_row)
            feat = self._recognizer.feature(aligned).flatten()
            norm = np.linalg.norm(feat)
            return feat / norm if norm > 0 else None
        except Exception as e:
            logger.debug(f"FaceAuth embed error: {e}")
            return None

    def identify(self, frame: np.ndarray, face_row: np.ndarray) -> tuple[str | None, float]:
        """
        Identifica il volto nel frame. Ritorna (nome, similarity) del best match
        sopra soglia, oppure (None, best_similarity) se nessuno è abbastanza simile.
        Nessun fail-open qui: un volto non riconosciuto è None, punto — la
        permissività sta nei consumer (fallback voce), non nell'identità.
        """
        if not self.is_enabled():
            return None, 0.0
        emb = self.embed(frame, face_row)
        if emb is None:
            return None, 0.0
        best_name, best_sim = None, -1.0
        for name, prototypes in self._faceprints.items():
            # Max-per-prototype preserves posture/angle examples. Averaging diverse
            # poses diluted the owner vector toward the threshold and caused the
            # repeated owner->unknown oscillation seen in normal chair movement.
            sim = float(np.max(prototypes @ emb))
            if sim > best_sim:
                best_name, best_sim = name, sim
        if best_sim >= config.FACE_AUTH_THRESHOLD:
            return best_name, best_sim
        return None, best_sim

    def enroll_from_embeddings(self, name: str, embeddings: list[np.ndarray]) -> bool:
        """Crea il faceprint di <name> da un insieme limitato di prototipi."""
        if len(embeddings) < 2:
            logger.warning("FaceAuth: troppi pochi campioni per enrollment")
            return False
        try:
            prototypes = np.asarray(
                np.stack(embeddings[:_MAX_PROTOTYPES]), dtype=np.float32
            )
            norms = np.linalg.norm(prototypes, axis=1, keepdims=True)
            if np.any(norms[:, 0] <= 0):
                raise ValueError("embedding nullo")
            prototypes = prototypes / norms
            fdir = Path(config.FACEPRINT_DIR)
            fdir.mkdir(parents=True, exist_ok=True)
            np.save(fdir / f"{name}.npy", prototypes)
            self._faceprints[name] = prototypes
            logger.info(
                f"FaceAuth: faceprint '{name}' salvato ({len(prototypes)} prototipi)"
            )
            return True
        except Exception as e:
            logger.error(f"FaceAuth enrollment error: {e}")
            return False

    def remove(self, name: str) -> bool:
        """Elimina il faceprint di <name> (revoca dell'abilitazione)."""
        fdir = Path(config.FACEPRINT_DIR)
        f = fdir / f"{name}.npy"
        try:
            if f.exists():
                f.unlink()
            self._faceprints.pop(name, None)
            logger.info(f"FaceAuth: faceprint '{name}' rimosso")
            return True
        except Exception as e:
            logger.error(f"FaceAuth remove error: {e}")
            return False

    def enrolled_names(self) -> list[str]:
        return sorted(self._faceprints)
