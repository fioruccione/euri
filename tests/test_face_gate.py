#!/usr/bin/env python3
"""Unit test per FaceAuth + gate sdoppiato del VisualGate.

Non richiede webcam/modelli ONNX: FaceAuth usa un recognizer finto, il
VisualGate viene esercitato sulla logica di identità (sticky, decadimento,
one-shot owner_arrived) senza avviare il loop camera.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

import config
from voice.face_auth import FaceAuth
from voice.visual_gate import VisualGate, RECOG_MAX_FAILS, IDENTITY_TIMEOUT_S


def check(name, cond, detail=""):
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}: {detail}")
    return bool(cond)


class FakeRecognizer:
    """Il 'volto' è direttamente un vettore: alignCrop/feature sono identità."""
    def alignCrop(self, frame, face_row):
        return frame

    def feature(self, aligned):
        return np.asarray(aligned, dtype=np.float32).reshape(1, -1)


def make_auth(tmpdir):
    config.FACEPRINT_DIR = str(tmpdir)
    auth = FaceAuth()
    auth._recognizer = FakeRecognizer()
    return auth


def vec(seed, dim=128):   # 128 = dimensione reale SFace; la soglia 0.363 ha senso lì
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    return v / np.linalg.norm(v)


def run():
    ok = []
    import tempfile
    tmp = tempfile.mkdtemp(prefix="faceprints_test_")

    # ── FaceAuth: enrollment + identify ─────────────────────────────────
    auth = make_auth(tmp)
    stefano = vec(1)
    capoturno = vec(2)

    ok.append(check("enroll richiede >=2 campioni",
                    not auth.enroll_from_embeddings("stefano", [stefano])))
    ok.append(check("enroll salva il faceprint",
                    auth.enroll_from_embeddings("stefano", [stefano, stefano * 0.99])))
    ok.append(check("enrollment conserva prototipi distinti",
                    auth._faceprints["stefano"].shape == (2, 128)))
    many = [np.roll(stefano, shift) for shift in range(12)]
    ok.append(check("enrollment limita i prototipi biometrici",
                    auth.enroll_from_embeddings("stefano", many)
                    and auth._faceprints["stefano"].shape == (8, 128)))
    ok.append(check("is_enabled dopo enrollment", auth.is_enabled()))

    name, sim = auth.identify(stefano, None)
    ok.append(check("identifica il proprietario", name == "stefano", f"sim={sim:.3f}"))

    name, sim = auth.identify(capoturno, None)
    ok.append(check("sconosciuto → None (no fail-open sull'identità)",
                    name is None, f"sim={sim:.3f}"))

    auth.enroll_from_embeddings("simone", [capoturno, capoturno])
    name, _ = auth.identify(capoturno, None)
    ok.append(check("multi-profilo: riconosce il secondo abilitato", name == "simone"))

    ok.append(check("remove revoca", auth.remove("simone") and "simone" not in auth.enrolled_names()))

    # reload da disco (nuovo oggetto, come al boot del daemon)
    auth2 = make_auth(tmp)
    auth2.reload_faceprints()
    ok.append(check("faceprint persistono su disco", auth2.enrolled_names() == ["stefano"]))

    # Compatibilita' con i vecchi file a vettore singolo: il reload li promuove
    # a matrice con un prototipo senza richiedere una migrazione distruttiva.
    np.save(Path(tmp) / "legacy.npy", capoturno)
    auth2.reload_faceprints()
    ok.append(check("faceprint storico 1D resta leggibile",
                    auth2._faceprints["legacy"].shape == (1, 128)))
    Path(tmp, "legacy.npy").unlink()
    auth2.reload_faceprints()

    # ── VisualGate: gate sdoppiato ──────────────────────────────────────
    config.FACE_AUTH_OWNER = "stefano"
    gate = VisualGate(face_auth=auth)
    gate._gate_active = True   # simula: qualcuno è nel campo visivo

    ok.append(check("faccia presente ma ignota → owner assente",
                    gate.is_user_present() and not gate.is_owner_present()))

    # riconoscimento del proprietario
    gate._update_identity(stefano, np.zeros(15), time.monotonic())
    ok.append(check("owner riconosciuto → is_owner_present", gate.is_owner_present()))
    ok.append(check("one-shot owner_arrived", gate.consume_owner_arrived()))
    ok.append(check("one-shot consumato", not gate.consume_owner_arrived()))
    ok.append(check("present_identity", gate.present_identity() == "stefano"))
    ok.append(check("owner fresco abilita recettore sociale",
                    gate.fresh_owner_identity(now=time.monotonic()) == "stefano"))
    ok.append(check("identita sticky vecchia non autorizza profilazione",
                    gate.fresh_owner_identity(
                        now=time.monotonic() + config.SOCIAL_PERCEPTION_IDENTITY_MAX_AGE_S + 1
                    ) is None))

    # identità sticky: una verifica fallita non la fa cadere...
    now = time.monotonic()
    gate._last_recog_ts = 0.0
    gate._update_identity(capoturno * 0.0 + vec(99), np.zeros(15), now)  # volto non verificabile
    ok.append(check("identità sticky su 1 verifica fallita", gate.is_owner_present()))

    # ...ma RECOG_MAX_FAILS verifiche fallite sì
    for _ in range(RECOG_MAX_FAILS - 1):
        gate._last_recog_ts = 0.0
        gate._update_identity(vec(99), np.zeros(15), time.monotonic())
    ok.append(check(f"identità decade dopo {RECOG_MAX_FAILS} verifiche fallite",
                    not gate.is_owner_present() and gate.present_identity() is None))

    # un'altra persona riconosciuta NON è owner e non emette owner_arrived
    gate._last_recog_ts = 0.0
    auth.enroll_from_embeddings("simone", [capoturno, capoturno])
    gate._update_identity(capoturno, np.zeros(15), time.monotonic())
    ok.append(check("altro abilitato: presenza sì, owner no",
                    gate.present_identity() == "simone" and not gate.is_owner_present()))
    ok.append(check("altro abilitato: nessun owner_arrived", not gate.consume_owner_arrived()))

    # ritorno del proprietario → owner_arrived di nuovo
    gate._last_recog_ts = 0.0
    gate._update_identity(stefano, np.zeros(15), time.monotonic())
    ok.append(check("ritorno owner → nuovo one-shot",
                    gate.is_owner_present() and gate.consume_owner_arrived()))

    # gate cieco / FaceAuth spento: mai owner
    gate_blind = VisualGate(face_auth=None)
    gate_blind._gate_active = True
    gate_blind._blind = True
    ok.append(check("cieco/fail-open: owner mai presente", not gate_blind.is_owner_present()))

    # Enrollment UI: il daemon riusa il frame del VisualGate. Redis trasporta
    # solo comandi e stato; un nonce gia' consumato non duplica il prototipo.
    enroll_auth = make_auth(tempfile.mkdtemp(prefix="guided_faceprints_test_"))
    enrollment_request = {
        "session_id": "session-1",
        "name": "stefano",
        "action": "start",
        "nonce": "start-1",
        "pose_index": 0,
    }
    enrollment_statuses = []
    gate_enroll = VisualGate(face_auth=enroll_auth)
    gate_enroll.set_enrollment_bridge(
        lambda: dict(enrollment_request), enrollment_statuses.append
    )
    gate_enroll._face_count = 1
    ok.append(check(
        "enrollment guidato prepara la sessione senza consumare il frame",
        not gate_enroll._process_enrollment(stefano, True, np.zeros(15), 1.0)
        and enrollment_statuses[-1]["state"] == "ready",
    ))
    for pose_index in range(4):
        enrollment_request.update({
            "action": "capture",
            "nonce": f"capture-{pose_index}",
            "pose_index": pose_index,
        })
        consumed = gate_enroll._process_enrollment(
            np.roll(stefano, pose_index), True, np.zeros(15), 2.0 + pose_index
        )
        duplicate = gate_enroll._process_enrollment(
            np.roll(stefano, pose_index), True, np.zeros(15), 2.4 + pose_index
        )
        ok.append(check(
            f"postura {pose_index + 1}: frame esclusivo e nonce idempotente",
            consumed and not duplicate
            and enrollment_statuses[-1]["captured"] == pose_index + 1,
        ))
    ok.append(check(
        "enrollment guidato salva quattro prototipi",
        enroll_auth._faceprints["stefano"].shape == (4, 128)
        and enrollment_statuses[-1]["state"] == "completed",
    ))

    # Gli indici V4L2 cambiano dopo riconnessioni USB: video0 può mancare mentre
    # la stessa webcam è disponibile su video1. La discovery accetta solo un nodo
    # che restituisce davvero un frame, non basta che il device si apra.
    class FakeCapture:
        def __init__(self, opened, frames):
            self.opened = opened
            self.frames = list(frames)
            self.released = False

        def isOpened(self):
            return self.opened

        def set(self, *_args):
            return True

        def read(self):
            return self.frames.pop(0) if self.frames else (False, None)

        def release(self):
            self.released = True

    first = FakeCapture(False, [])
    second = FakeCapture(True, [(True, object())])

    class FakeCV2:
        CAP_V4L2 = 200
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_FPS = 5

        def VideoCapture(self, source, _backend):
            return first if source == "/dev/video0" else second

    gate_camera = VisualGate(camera_index=None)
    gate_camera._cv2 = FakeCV2()
    gate_camera._camera_candidates = lambda: [
        ("/dev/video0", "/dev/video0"),
        ("/dev/video1", "/dev/video1"),
    ]
    cap, frame, label = gate_camera._open_camera()
    ok.append(check("camera discovery salta video0 assente",
                    cap is second and first.released))
    ok.append(check("camera discovery valida un frame reale",
                    frame is not None and label == "/dev/video1"))

    # Se una cattura già aperta si pianta (tipico xHCI dopo un cavo mosso), il
    # gate non deve restare INACTIVE sul vecchio handle. Rilascia, diventa cieco
    # e fail-open, invalida l'identità sticky e riapre la camera.
    broken = FakeCapture(True, [(False, None)])
    recovered = FakeCapture(True, [])
    open_states = []
    open_results = [
        (broken, object(), "/dev/video0"),
        (recovered, object(), "/dev/video1"),
    ]
    gate_reconnect = VisualGate(camera_index=None)
    gate_reconnect._cv2 = FakeCV2()
    gate_reconnect._running = True
    gate_reconnect._interval = 0.0
    gate_reconnect._reconnect_interval = 0.0
    gate_reconnect._read_failures_before_reconnect = 1
    gate_reconnect._gate_active = False
    gate_reconnect._identity = "stefano"

    def open_camera_sequence():
        if len(open_results) == 1:
            open_states.append((
                gate_reconnect.is_blind(),
                gate_reconnect.is_user_present(),
                gate_reconnect.present_identity(),
                gate_reconnect.is_owner_present(),
            ))
        return open_results.pop(0)

    detected_frames = 0

    def detect_until_recovered(_frame):
        nonlocal detected_frames
        detected_frames += 1
        if detected_frames == 2:
            gate_reconnect._running = False
        return False, None

    gate_reconnect._open_camera = open_camera_sequence
    gate_reconnect._detect = detect_until_recovered
    gate_reconnect._loop()
    ok.append(check(
        "read guasto: cattura rilasciata e nuova camera aperta",
        broken.released and recovered.released and detected_frames == 2,
    ))
    ok.append(check(
        "read guasto: durante il recovery il gate è cieco e fail-open",
        open_states == [(True, True, None, False)],
    ))
    ok.append(check(
        "read guasto: il recovery ripristina la disponibilità visiva",
        not gate_reconnect.is_blind(),
    ))

    print()
    passed = sum(ok)
    print(f"{passed}/{len(ok)} test passati")
    return 0 if passed == len(ok) else 1


if __name__ == "__main__":
    sys.exit(run())
