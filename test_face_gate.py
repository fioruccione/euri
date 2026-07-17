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

    print()
    passed = sum(ok)
    print(f"{passed}/{len(ok)} test passati")
    return 0 if passed == len(ok) else 1


if __name__ == "__main__":
    sys.exit(run())
