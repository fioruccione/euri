"""Visual social afference for Euri (Phase 0: observation only).

This module measures visible facial movements. It does not classify emotions and
does not call an LLM. MediaPipe is an optional backend: failure disables only this
receptor, never presence detection, face identity or the voice daemon.

The state machine intentionally consumes numeric signals rather than images. That
makes smoothing, transitions and future multimodal interpretation independently
testable, and guarantees that frames are never persisted by this component.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from loguru import logger


FEATURES = ("smile", "brow_contraction", "gaze_down")
AUXILIARY_METRICS = ("head_pitch_deg", "head_yaw_deg")


def _clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return lo
    if not math.isfinite(number):
        return lo
    return max(lo, min(hi, number))


@dataclass(frozen=True)
class SocialTransition:
    feature: str
    previous: str
    current: str
    value: float
    confidence: float
    observed_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SocialSnapshot:
    actor_id: str
    observed_at: float
    calibrated: bool
    sample_count: int
    metrics: dict[str, float]
    baselines: dict[str, float]
    states: dict[str, str]
    confidences: dict[str, float]
    auxiliary_metrics: dict[str, float]
    transitions: tuple[SocialTransition, ...] = ()

    def to_dict(self, *, include_transitions: bool = True) -> dict[str, Any]:
        payload = {
            "actor_id": self.actor_id,
            "observed_at": round(self.observed_at, 3),
            "calibrated": self.calibrated,
            "sample_count": self.sample_count,
            "metrics": {key: round(value, 4) for key, value in self.metrics.items()},
            "baselines": {key: round(value, 4) for key, value in self.baselines.items()},
            "states": dict(self.states),
            "confidences": {
                key: round(value, 4) for key, value in self.confidences.items()
            },
            "auxiliary_metrics": {
                key: round(value, 3) for key, value in self.auxiliary_metrics.items()
            },
        }
        if include_transitions:
            payload["transitions"] = [item.to_dict() for item in self.transitions]
        return payload


class SocialSignalState:
    """Stabilize visible movements and emit only persistent state changes."""

    def __init__(
        self,
        *,
        calibration_samples: int = 12,
        window_samples: int = 6,
        stability_samples: int = 4,
        baseline_cap: float = 0.25,
    ) -> None:
        if calibration_samples < 2 or window_samples < 1 or stability_samples < 1:
            raise ValueError("invalid social perception window configuration")
        self.calibration_samples = int(calibration_samples)
        self.window_samples = int(window_samples)
        self.stability_samples = int(stability_samples)
        self.baseline_cap = _clamp(baseline_cap)
        self._actor_id = ""
        self._samples: deque[dict[str, float]] = deque(
            maxlen=max(self.calibration_samples, self.window_samples)
        )
        self._states = {feature: "neutral" for feature in FEATURES}
        self._pending = {feature: ("neutral", 0) for feature in FEATURES}
        self._baselines = {feature: 0.0 for feature in FEATURES}
        self._sample_count = 0

    def reset(self, actor_id: str = "") -> None:
        self._actor_id = actor_id
        self._samples.clear()
        self._states = {feature: "neutral" for feature in FEATURES}
        self._pending = {feature: ("neutral", 0) for feature in FEATURES}
        self._baselines = {feature: 0.0 for feature in FEATURES}
        self._sample_count = 0

    @staticmethod
    def _state_for(feature: str, value: float, current: str) -> str:
        # Hysteresis is deliberately descriptive: these are visible movements,
        # not emotion labels. Thresholds are initial hypotheses for Phase 0.
        if feature == "smile":
            if current == "marked" and value >= 0.46:
                return "marked"
            if value >= 0.58:
                return "marked"
            if current in {"slight", "marked"} and value >= 0.18:
                return "slight"
            return "slight" if value >= 0.28 else "neutral"
        if feature == "brow_contraction":
            if current == "present" and value >= 0.28:
                return "present"
            return "present" if value >= 0.42 else "neutral"
        if feature == "gaze_down":
            if current == "present" and value >= 0.30:
                return "present"
            return "present" if value >= 0.50 else "neutral"
        return "neutral"

    def observe(
        self,
        actor_id: str,
        metrics: Mapping[str, float],
        confidences: Mapping[str, float] | None = None,
        *,
        observed_at: float | None = None,
    ) -> SocialSnapshot:
        if not actor_id:
            raise ValueError("actor_id is required")
        if actor_id != self._actor_id:
            self.reset(actor_id)

        at = time.time() if observed_at is None else float(observed_at)
        sample = {feature: _clamp(metrics.get(feature, 0.0)) for feature in FEATURES}
        confidence = {
            feature: _clamp((confidences or {}).get(feature, 0.5)) for feature in FEATURES
        }
        auxiliary = {}
        for key in AUXILIARY_METRICS:
            try:
                value = float(metrics.get(key, 0.0))
            except (TypeError, ValueError):
                value = 0.0
            auxiliary[key] = max(-180.0, min(180.0, value)) if math.isfinite(value) else 0.0
        self._samples.append(sample)
        self._sample_count += 1

        calibrated = self._sample_count >= self.calibration_samples
        if self._sample_count == self.calibration_samples:
            calibration = list(self._samples)[-self.calibration_samples:]
            self._baselines = {
                feature: float(np.median([item[feature] for item in calibration]))
                for feature in FEATURES
            }

        window = list(self._samples)[-self.window_samples:]
        smoothed_raw = {
            feature: float(np.median([item[feature] for item in window]))
            for feature in FEATURES
        }
        adjusted = {
            feature: _clamp(
                smoothed_raw[feature] - min(self._baselines[feature], self.baseline_cap)
            )
            for feature in FEATURES
        }

        transitions: list[SocialTransition] = []
        if calibrated:
            for feature in FEATURES:
                proposed = self._state_for(feature, adjusted[feature], self._states[feature])
                pending_state, count = self._pending[feature]
                count = count + 1 if pending_state == proposed else 1
                self._pending[feature] = (proposed, count)
                if proposed != self._states[feature] and count >= self.stability_samples:
                    previous = self._states[feature]
                    self._states[feature] = proposed
                    self._pending[feature] = (proposed, 0)
                    transitions.append(
                        SocialTransition(
                            feature=feature,
                            previous=previous,
                            current=proposed,
                            value=round(adjusted[feature], 4),
                            confidence=round(confidence[feature], 4),
                            observed_at=at,
                        )
                    )

        return SocialSnapshot(
            actor_id=actor_id,
            observed_at=at,
            calibrated=calibrated,
            sample_count=self._sample_count,
            metrics=adjusted,
            baselines=dict(self._baselines),
            states=dict(self._states),
            confidences=confidence,
            auxiliary_metrics=auxiliary,
            transitions=tuple(transitions),
        )


class MediaPipeFaceBackend:
    """Optional local MediaPipe backend. It never opens a camera or stores frames."""

    def __init__(self, model_path: str | Path) -> None:
        self.model_path = Path(model_path)
        self._mp = None
        self._landmarker = None
        self._last_timestamp_ms = 0

    def load(self) -> bool:
        if not self.model_path.is_file():
            logger.warning(
                f"Percezione sociale disabilitata: modello assente ({self.model_path})"
            )
            return False
        try:
            import mediapipe as mp

            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model_path)),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
            self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
            self._mp = mp
            return True
        except Exception as exc:
            logger.warning(f"Percezione sociale MediaPipe non disponibile ({exc})")
            self.close()
            return False

    def close(self) -> None:
        landmarker, self._landmarker = self._landmarker, None
        if landmarker is not None:
            try:
                landmarker.close()
            except Exception:
                pass
        self._mp = None

    @staticmethod
    def _pair(scores: Mapping[str, float], left: str, right: str) -> tuple[float, float]:
        a, b = _clamp(scores.get(left, 0.0)), _clamp(scores.get(right, 0.0))
        confidence = _clamp(1.0 - abs(a - b))
        return (a + b) / 2.0, confidence

    @staticmethod
    def _head_pose(result: Any) -> tuple[float, float]:
        matrices = getattr(result, "facial_transformation_matrixes", None) or []
        if not matrices:
            return 0.0, 0.0
        try:
            matrix = np.asarray(matrices[0], dtype=float).reshape(4, 4)
            pitch = math.degrees(math.atan2(-matrix[2, 0], math.hypot(matrix[0, 0], matrix[1, 0])))
            yaw = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
            return float(pitch), float(yaw)
        except Exception:
            return 0.0, 0.0

    def extract(self, frame_bgr: np.ndarray, timestamp_ms: int) -> tuple[dict[str, float], dict[str, float]] | None:
        if self._landmarker is None or self._mp is None:
            return None
        timestamp_ms = max(int(timestamp_ms), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        rgb = np.ascontiguousarray(frame_bgr[:, :, ::-1])
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        blendshape_sets = getattr(result, "face_blendshapes", None) or []
        if not blendshape_sets:
            return None
        scores = {
            str(item.category_name): _clamp(item.score)
            for item in blendshape_sets[0]
            if getattr(item, "category_name", None)
        }
        smile, smile_conf = self._pair(scores, "mouthSmileLeft", "mouthSmileRight")
        brow, brow_conf = self._pair(scores, "browDownLeft", "browDownRight")
        gaze, gaze_conf = self._pair(scores, "eyeLookDownLeft", "eyeLookDownRight")
        pitch, yaw = self._head_pose(result)
        return (
            {
                "smile": smile,
                "brow_contraction": brow,
                "gaze_down": gaze,
                "head_pitch_deg": pitch,
                "head_yaw_deg": yaw,
            },
            {
                "smile": smile_conf,
                "brow_contraction": brow_conf,
                "gaze_down": gaze_conf,
            },
        )


class SocialPerception:
    """Throttle frame inference and expose stable Phase-0 snapshots."""

    def __init__(
        self,
        backend: Any,
        *,
        owner_id: str,
        on_update: Callable[[SocialSnapshot], None] | None = None,
        fps: float = 2.0,
        refresh_s: float = 2.0,
        calibration_samples: int = 12,
        window_samples: int = 6,
        stability_samples: int = 4,
    ) -> None:
        if fps <= 0 or refresh_s <= 0:
            raise ValueError("fps and refresh_s must be positive")
        self.backend = backend
        self.owner_id = owner_id
        self.on_update = on_update
        self.interval_s = 1.0 / float(fps)
        self.refresh_s = float(refresh_s)
        self.state = SocialSignalState(
            calibration_samples=calibration_samples,
            window_samples=window_samples,
            stability_samples=stability_samples,
        )
        self._enabled = False
        self._last_process_mono = 0.0
        self._last_emit_mono = 0.0
        self._fault_logged = False

    def start(self) -> bool:
        try:
            self._enabled = bool(self.backend.load())
        except Exception as exc:
            logger.warning(f"Percezione sociale non disponibile ({exc})")
            self._enabled = False
        if self._enabled:
            logger.info(
                "Percezione sociale Fase 0 attiva - osservazione soltanto, nessun LLM"
            )
        return self._enabled

    def stop(self) -> None:
        self._enabled = False
        try:
            self.backend.close()
        except Exception:
            pass

    def is_enabled(self) -> bool:
        return self._enabled

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        actor_id: str | None,
        *,
        monotonic_at: float | None = None,
        observed_at: float | None = None,
    ) -> SocialSnapshot | None:
        # Unknown/non-owner faces are intentionally not profiled.
        if not self._enabled or actor_id != self.owner_id:
            return None
        mono = time.monotonic() if monotonic_at is None else float(monotonic_at)
        if self._last_process_mono and mono - self._last_process_mono < self.interval_s:
            return None
        self._last_process_mono = mono
        try:
            extracted = self.backend.extract(frame_bgr, int(mono * 1000))
        except Exception as exc:
            if not self._fault_logged:
                logger.warning(f"Percezione sociale: lettura fallita, fail-silent ({exc})")
                self._fault_logged = True
            return None
        if extracted is None:
            return None
        self._fault_logged = False
        metrics, confidences = extracted
        snapshot = self.state.observe(
            actor_id,
            metrics,
            confidences,
            observed_at=observed_at,
        )
        should_emit = bool(snapshot.transitions) or (
            not self._last_emit_mono or mono - self._last_emit_mono >= self.refresh_s
        )
        if should_emit:
            self._last_emit_mono = mono
            if self.on_update is not None:
                try:
                    self.on_update(snapshot)
                except Exception as exc:
                    logger.debug(f"Percezione sociale: consumer ignorato ({exc})")
            return snapshot
        return None


def build_social_perception(on_update=None) -> SocialPerception | None:
    """Build from config without making VisualGate depend on configuration details."""
    import config

    if not getattr(config, "SOCIAL_PERCEPTION_ENABLED", False):
        return None
    backend = MediaPipeFaceBackend(getattr(config, "SOCIAL_PERCEPTION_MODEL"))
    return SocialPerception(
        backend,
        owner_id=getattr(config, "FACE_AUTH_OWNER", "stefano"),
        on_update=on_update,
        fps=getattr(config, "SOCIAL_PERCEPTION_FPS", 2.0),
        refresh_s=getattr(config, "SOCIAL_PERCEPTION_REFRESH_S", 2.0),
        calibration_samples=getattr(config, "SOCIAL_PERCEPTION_CALIBRATION_SAMPLES", 12),
        window_samples=getattr(config, "SOCIAL_PERCEPTION_WINDOW_SAMPLES", 6),
        stability_samples=getattr(config, "SOCIAL_PERCEPTION_STABILITY_SAMPLES", 4),
    )
