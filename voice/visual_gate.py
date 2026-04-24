"""
VisualGate — gate visivo intelligente stile Jarvis.

Logica:
  INACTIVE → vede una faccia → ACTIVE + saluto
  ACTIVE   → rimane attivo finché c'è faccia OPPURE conversazione recente
  ACTIVE   → nessuna faccia E nessuna conversazione per IDLE_TIMEOUT → INACTIVE
  INACTIVE → vede di nuovo una faccia → ACTIVE + saluto di bentornato

Il daemon chiama notify_activity() ad ogni STT o TTS per tenere vivo il gate.
consume_just_activated() → True (one-shot) quando il gate si attiva: il daemon fa il saluto.

Dipendenze: opencv-contrib-python
"""
import threading
import time

from loguru import logger
import config

# Secondi senza faccia E senza conversazione prima di tornare INACTIVE
IDLE_TIMEOUT = 300.0  # 5 minuti


class VisualGate:

    def __init__(self, camera_index: int = 0, fps: float = 2.0, resolution: tuple = (320, 240)):
        self._camera_index = camera_index
        self._interval = 1.0 / fps
        self._resolution = resolution

        self._gate_active = False          # True = processa voce
        self._last_seen: float = 0.0       # ultimo frame con faccia
        self._last_activity: float = 0.0   # ultimo STT o TTS
        self._just_activated = False       # one-shot: daemon fa il saluto

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._detector = None
        self._cv2 = None

    # ──────────────────────────────────────────
    # API pubblica
    # ──────────────────────────────────────────

    def start(self):
        """Avvia il loop visivo in background. Fail-open se webcam non disponibile."""
        if config.DEMO_MODE:
            self._gate_active = True
            logger.info("VisualGate: DEMO_MODE — gate sempre ACTIVE, webcam non avviata")
            return
        try:
            import cv2
            self._cv2 = cv2
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            self._detector = cv2.CascadeClassifier(cascade_path)
            if self._detector.empty():
                raise RuntimeError("Haar cascade non trovato")
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="visual-gate")
            self._thread.start()
            logger.info("VisualGate avviato — in attesa di presenza (Haar cascade, 2fps)")
        except Exception as e:
            logger.warning(f"VisualGate non disponibile ({e}) — Euri funziona senza gate visivo")
            self._gate_active = True   # fail-open: senza webcam, processa sempre

    def stop(self):
        self._running = False

    def is_user_present(self) -> bool:
        """True se il gate è attivo (processa voce)."""
        with self._lock:
            return self._gate_active

    def notify_activity(self):
        """Chiamato dal daemon ad ogni STT trascritto o TTS pronunciato."""
        with self._lock:
            self._last_activity = time.monotonic()

    def consume_just_activated(self) -> bool:
        """
        One-shot: ritorna True la prima volta dopo un'attivazione.
        Il daemon usa questo per fare il saluto di bentornato.
        """
        with self._lock:
            if self._just_activated:
                self._just_activated = False
                return True
            return False

    # ──────────────────────────────────────────
    # Loop interno
    # ──────────────────────────────────────────

    def _loop(self):
        cv2 = self._cv2
        cap = cv2.VideoCapture(self._camera_index)
        if not cap.isOpened():
            logger.warning("VisualGate: webcam non accessibile — gate disabilitato (fail-open)")
            with self._lock:
                self._gate_active = True
            self._running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._resolution[1])
        cap.set(cv2.CAP_PROP_FPS, 2)
        logger.debug("VisualGate: webcam aperta")

        consecutive_no_face = 0

        while self._running:
            t_start = time.monotonic()
            ret, frame = cap.read()

            if not ret:
                time.sleep(self._interval)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self._detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(60, 60),
            )
            face_detected = len(faces) > 0

            with self._lock:
                now = time.monotonic()

                if face_detected:
                    self._last_seen = now
                    consecutive_no_face = 0

                    if not self._gate_active:
                        # Transizione INACTIVE → ACTIVE
                        self._gate_active = True
                        self._just_activated = True
                        self._last_activity = now
                        logger.info("VisualGate: presenza rilevata → ACTIVE")
                else:
                    consecutive_no_face += 1

                # Controlla se passare a INACTIVE
                # Solo se gate è attivo E abbiamo perso la faccia per almeno 3 frame
                if self._gate_active and consecutive_no_face >= 3:
                    time_since_face = now - self._last_seen
                    time_since_talk = now - self._last_activity
                    idle = min(time_since_face, time_since_talk)

                    if idle >= IDLE_TIMEOUT:
                        self._gate_active = False
                        logger.info(
                            f"VisualGate: {IDLE_TIMEOUT/60:.0f} min senza faccia né conversazione → INACTIVE"
                        )

            elapsed = time.monotonic() - t_start
            time.sleep(max(0.0, self._interval - elapsed))

        cap.release()
        logger.debug("VisualGate: webcam chiusa")
