#!/usr/bin/env python3
"""Unit tests for Phase-0 social perception; no camera, Redis or MediaPipe."""
import sys
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from voice.social_perception import MediaPipeFaceBackend, SocialPerception, SocialSignalState


def check(name, condition, detail=""):
    flag = "PASS" if condition else "FAIL"
    print(f"[{flag}] {name}: {detail}")
    return bool(condition)


class FakeBackend:
    def __init__(self, samples=None, *, loads=True, raises=False):
        self.samples = list(samples or [])
        self.loads = loads
        self.raises = raises
        self.extract_calls = 0
        self.closed = False

    def load(self):
        return self.loads

    def close(self):
        self.closed = True

    def extract(self, _frame, _timestamp_ms):
        self.extract_calls += 1
        if self.raises:
            raise RuntimeError("synthetic backend fault")
        return self.samples.pop(0) if self.samples else None


def sample(smile=0.05, brow=0.05, gaze=0.05, pitch=0.0, yaw=0.0):
    metrics = {
        "smile": smile,
        "brow_contraction": brow,
        "gaze_down": gaze,
        "head_pitch_deg": pitch,
        "head_yaw_deg": yaw,
    }
    confidence = {key: 0.9 for key in metrics}
    return metrics, confidence


def run():
    ok = []

    state = SocialSignalState(
        calibration_samples=3,
        window_samples=1,
        stability_samples=2,
    )
    neutral = [state.observe("stefano", sample()[0], sample()[1], observed_at=i)
               for i in range(3)]
    ok.append(check("calibrazione non anticipata", not neutral[1].calibrated))
    ok.append(check("calibrazione al campione previsto", neutral[2].calibrated))
    ok.append(check("nessuna transizione durante calibrazione",
                    not any(item.transitions for item in neutral)))

    pose = state.observe(
        "stefano", sample(pitch=18.5, yaw=-7.0)[0], sample()[1], observed_at=2.5
    )
    serialized = pose.to_dict()
    ok.append(check(
        "posa testa resta misura descrittiva",
        pose.auxiliary_metrics["head_pitch_deg"] == 18.5
        and pose.auxiliary_metrics["head_yaw_deg"] == -7.0
        and serialized["auxiliary_metrics"]["head_pitch_deg"] == 18.5,
    ))
    ok.append(check(
        "snapshot non contiene immagini",
        not any("image" in key or "frame" in key for key in serialized),
    ))

    pitch_rad, yaw_rad, roll_rad = map(math.radians, (20.0, 15.0, 10.0))
    cx, sx = math.cos(pitch_rad), math.sin(pitch_rad)
    cy, sy = math.cos(yaw_rad), math.sin(yaw_rad)
    cz, sz = math.cos(roll_rad), math.sin(roll_rad)
    rotation = np.array([
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx, 0.0],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx, 0.0],
        [-sy, cy * sx, cy * cx, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    measured_pose = MediaPipeFaceBackend._head_pose(
        SimpleNamespace(facial_transformation_matrixes=[rotation])
    )
    ok.append(check(
        "assi posa testa nominati correttamente",
        all(abs(got - expected) < 0.01
            for got, expected in zip(measured_pose, (20.0, 15.0, 10.0))),
        detail=str(tuple(round(value, 2) for value in measured_pose)),
    ))

    spike = state.observe("stefano", sample(smile=0.9)[0], sample()[1], observed_at=3)
    ok.append(check("singolo frame non cambia stato",
                    spike.states["smile"] == "neutral" and not spike.transitions))
    stable = state.observe("stefano", sample(smile=0.9)[0], sample()[1], observed_at=4)
    ok.append(check("sorriso persistente genera transizione",
                    stable.states["smile"] == "marked" and
                    len(stable.transitions) == 1 and
                    stable.transitions[0].feature == "smile"))

    falling1 = state.observe("stefano", sample()[0], sample()[1], observed_at=5)
    falling2 = state.observe("stefano", sample()[0], sample()[1], observed_at=6)
    ok.append(check("isteresi e persistenza sul ritorno",
                    not falling1.transitions and
                    falling2.states["smile"] == "neutral" and
                    falling2.transitions[0].previous == "marked"))

    gaze1 = state.observe("stefano", sample(gaze=0.4)[0], sample()[1], observed_at=7)
    gaze2 = state.observe("stefano", sample(gaze=0.4)[0], sample()[1], observed_at=8)
    ok.append(check(
        "sguardo basso persistente usa soglia calibrata",
        not gaze1.transitions
        and gaze2.states["gaze_down"] == "present"
        and gaze2.transitions[0].feature == "gaze_down",
    ))

    reset = state.observe("altra_persona", sample()[0], sample()[1], observed_at=9)
    ok.append(check("cambio identita resetta calibrazione",
                    reset.actor_id == "altra_persona" and
                    reset.sample_count == 1 and not reset.calibrated))

    updates = []
    backend = FakeBackend([sample(), sample(), sample(), sample(smile=0.9)])
    receptor = SocialPerception(
        backend,
        owner_id="stefano",
        on_update=updates.append,
        fps=2,
        refresh_s=2,
        calibration_samples=3,
        window_samples=1,
        stability_samples=1,
    )
    ok.append(check("backend opzionale si avvia", receptor.start()))
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    receptor.process_frame(frame, "ospite", monotonic_at=1.0, observed_at=1.0)
    ok.append(check("persona non owner non viene profilata", backend.extract_calls == 0))
    receptor.process_frame(frame, "stefano", monotonic_at=1.0, observed_at=1.0)
    receptor.process_frame(frame, "stefano", monotonic_at=1.1, observed_at=1.1)
    ok.append(check("throttle evita inferenze eccessive", backend.extract_calls == 1))
    receptor.process_frame(frame, "stefano", monotonic_at=2.0, observed_at=2.0)
    receptor.process_frame(frame, "stefano", monotonic_at=3.0, observed_at=3.0)
    receptor.process_frame(frame, "stefano", monotonic_at=4.0, observed_at=4.0)
    ok.append(check("callback riceve refresh o transizioni", len(updates) >= 2))
    ok.append(check("transizione owner arriva al consumer",
                    any(item.transitions for item in updates)))
    receptor.stop()
    ok.append(check("stop chiude il backend", backend.closed))

    failed = SocialPerception(FakeBackend(loads=False), owner_id="stefano")
    ok.append(check("backend assente disabilita solo il recettore", not failed.start()))

    faulty_backend = FakeBackend([sample()], raises=True)
    faulty = SocialPerception(faulty_backend, owner_id="stefano")
    faulty.start()
    result = faulty.process_frame(frame, "stefano", monotonic_at=1.0, observed_at=1.0)
    ok.append(check("errore inferenza e fail-silent", result is None))

    print()
    passed = sum(ok)
    print(f"{passed}/{len(ok)} test passati")
    return 0 if passed == len(ok) else 1


if __name__ == "__main__":
    sys.exit(run())
