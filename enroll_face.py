"""
Enrollment facciale — registra il faceprint di una persona abilitata.

Uso:
  python enroll_face.py stefano          # registra (o aggiorna) il faceprint
  python enroll_face.py --list           # elenca le persone registrate
  python enroll_face.py --remove nome    # revoca un'abilitazione

Il faceprint è un dato BIOMETRICO: viene calcolato localmente (SFace) e salvato
come vettore .npy in FACEPRINT_DIR — i frame della webcam non vengono mai
salvati. Registra solo persone che sanno di essere registrate e sono d'accordo.

La persona deve guardare la webcam: lo script raccoglie campioni del volto
da angolazioni leggermente diverse e salva l'embedding medio.
"""
import sys
import time

import cv2
import numpy as np

import config
from voice.face_auth import FaceAuth

SAMPLES = 8            # campioni di volto da raccogliere
SAMPLE_GAP_S = 0.7     # distanza minima tra campioni (per variare la posa)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    auth = FaceAuth()
    auth.load()

    if sys.argv[1] == "--list":
        names = auth.enrolled_names()
        print("Persone registrate:", ", ".join(names) if names else "(nessuna)")
        return

    if sys.argv[1] == "--remove":
        if len(sys.argv) < 3:
            print("Uso: python enroll_face.py --remove nome")
            sys.exit(1)
        name = sys.argv[2].strip().lower()
        if name in auth.enrolled_names():
            auth.remove(name)
            print(f"Faceprint '{name}' rimosso.")
        else:
            print(f"'{name}' non è registrato.")
        return

    name = sys.argv[1].strip().lower()
    print(f"\nEnrollment di '{name}'.")
    print("ATTENZIONE: il faceprint è un dato biometrico. Registra solo persone")
    print("che sanno di essere registrate e sono d'accordo. Resta tutto locale.\n")
    if input("Confermi? [s/N] ").strip().lower() not in ("s", "si", "sì", "y", "yes"):
        print("Annullato.")
        return

    detector = cv2.FaceDetectorYN_create(config.FACE_DETECT_MODEL, "", (640, 480), 0.8)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERRORE: webcam non accessibile.")
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"Guarda la webcam, muovi leggermente la testa. Raccolgo {SAMPLES} campioni...")
    embeddings = []
    last_sample = 0.0
    t_start = time.time()
    try:
        while len(embeddings) < SAMPLES:
            if time.time() - t_start > 60:
                print("Timeout: volto non rilevato abbastanza a lungo. Riprova con più luce.")
                break
            ret, frame = cap.read()
            if not ret:
                continue
            detector.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = detector.detect(frame)
            if faces is None or len(faces) == 0:
                continue
            if len(faces) > 1:
                print("  Più volti nel frame — dev'esserci solo la persona da registrare.")
                time.sleep(1)
                continue
            if time.time() - last_sample < SAMPLE_GAP_S:
                continue
            emb = auth.embed(frame, faces[0])
            if emb is not None:
                embeddings.append(emb)
                last_sample = time.time()
                print(f"  campione {len(embeddings)}/{SAMPLES}")
    finally:
        cap.release()

    if len(embeddings) >= 2 and auth.enroll_from_embeddings(name, embeddings):
        print(f"\nFatto: '{name}' registrato ({len(embeddings)} campioni).")
        print("Il daemon rilegge i faceprint al prossimo avvio (o dalla pagina di gestione).")
    else:
        print("\nEnrollment fallito: raccolti troppo pochi campioni validi.")


if __name__ == "__main__":
    main()
