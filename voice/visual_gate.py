"""
VisualGate — gate visivo intelligente stile Jarvis, con identità sdoppiata.

Il gate risponde a DUE domande diverse (prima collassavano in un bit solo):
  "c'è QUALCUNO?"        → is_user_present()  — basta per ascoltare la voce
                           (SpeakerAuth fa già da buttafuori sui comandi)
  "c'è il PROPRIETARIO?" → is_owner_present() — serve per parlare per primi
                           (initiative, reminder, saluto: l'efferente)
Il laboratorio è usato di notte dai capoturno: una faccia qualunque attiva
l'ascolto ma NON deve far parlare Euri.

Logica di presenza (invariata):
  INACTIVE → vede una faccia → ACTIVE
  ACTIVE   → rimane attivo finché c'è faccia OPPURE conversazione recente
  ACTIVE   → nessuna faccia E nessuna conversazione per IDLE_TIMEOUT → INACTIVE

Logica di identità (nuova):
  Detection YuNet (box + landmark) → embedding SFace via FaceAuth, throttled:
  ogni RECOG_RETRY_S finché sconosciuto, ri-verifica ogni RECOG_REFRESH_S quando
  noto. L'identità è STICKY finché ci sono facce (una testa girata non la fa
  cadere), ma decade dopo RECOG_MAX_FAILS ri-verifiche fallite o dopo
  IDENTITY_TIMEOUT_S senza facce. Un match positivo su una persona diversa
  la sostituisce subito.

Fail-open dichiarato:
  - webcam assente → is_user_present() sempre True ma is_blind()=True e
    is_owner_present() sempre False: chi vuole parlare per primo deve
    ripiegare sulla voce autenticata recente.
  - FaceAuth assente (modelli/faceprint mancanti) o fallback Haar (niente
    landmark) → presenza ok, identità sempre None: stesso ripiego.

Il daemon chiama notify_activity() ad ogni STT o TTS per tenere vivo il gate.
consume_just_activated()  → one-shot alla transizione INACTIVE→ACTIVE (pulse arrival).
consume_owner_arrived()   → one-shot quando l'identità DIVENTA il proprietario
                            (reminder persi, ripresa TEACH: si parla solo a lui).

Dipendenze: opencv-contrib-python
"""
import threading
import time
from pathlib import Path
from typing import Callable

from loguru import logger
import config

# Secondi senza faccia E senza conversazione prima di tornare INACTIVE
IDLE_TIMEOUT = 300.0        # 5 minuti
RECOG_RETRY_S = 2.0         # riprova a identificare uno sconosciuto ogni N s
RECOG_REFRESH_S = 60.0      # ri-verifica un'identità nota ogni N s
RECOG_MAX_FAILS = 3         # ri-verifiche fallite di fila prima di scordare chi era
IDENTITY_TIMEOUT_S = 120.0  # secondi senza facce prima di scordare chi era


class VisualGate:

    def __init__(self, camera_index: int | str | None = None, fps: float = 2.0,
                 resolution: tuple = (640, 480),
                 face_auth=None, social_perception=None):
        configured_camera = (
            camera_index if camera_index is not None
            else getattr(config, "VISUAL_GATE_CAMERA_DEVICE", None)
        )
        # L'override resta stabile; `_camera_index` descrive invece l'ultimo nodo
        # aperto. Se la discovery era automatica, una riconnessione deve poter
        # trovare anche un /dev/videoN diverso.
        self._camera_override = configured_camera
        self._camera_index = configured_camera
        self._interval = 1.0 / fps
        self._resolution = resolution
        self._face_auth = face_auth
        self._social_perception = social_perception
        self._read_failures_before_reconnect = max(
            1, int(getattr(config, "VISUAL_GATE_READ_FAILURES_BEFORE_RECONNECT", 1))
        )
        self._reconnect_interval = max(
            0.0, float(getattr(config, "VISUAL_GATE_RECONNECT_S", 3.0))
        )
        self._reconnect_max_interval = max(
            self._reconnect_interval,
            float(getattr(config, "VISUAL_GATE_RECONNECT_MAX_S", 30.0)),
        )

        self._gate_active = False          # True = processa voce
        self._last_seen: float = 0.0       # ultimo frame con faccia
        self._last_activity: float = 0.0   # ultimo STT o TTS
        self._just_activated = False       # one-shot: transizione INACTIVE→ACTIVE
        self._blind = False                # True = webcam non disponibile (fail-open):
                                           # is_user_present() è sempre True e NON è un segnale
                                           # di presenza affidabile → chi consuma deve ripiegare
                                           # sull'interazione recente.

        self._identity: str | None = None  # chi è davanti allo schermo (None = ignoto)
        self._identity_sim: float = 0.0
        self._last_identity_positive_ts: float = 0.0
        self._owner_arrived = False        # one-shot: l'identità è DIVENTATA il proprietario
        self._last_recog_ts: float = 0.0
        self._recog_fails = 0

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._detector = None              # YuNet (con landmark) oppure Haar (fallback)
        self._detector_kind = None         # "yunet" | "haar"
        self._cv2 = None
        self._face_count = 0

        # Enrollment guidato: la UI invia solo comandi, questo processo riusa
        # il frame gia' posseduto dal VisualGate. Nessuna immagine o embedding
        # attraversa Redis.
        self._enrollment_request_reader: Callable[[], dict | None] | None = None
        self._enrollment_status_writer: Callable[[dict], None] | None = None
        self._enrollment_session_id = ""
        self._enrollment_name = ""
        self._enrollment_embeddings: list = []
        self._enrollment_last_nonce = ""
        self._enrollment_last_activity = 0.0
        self._last_enrollment_poll = 0.0

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
            try:
                self._detector = cv2.FaceDetectorYN_create(
                    config.FACE_DETECT_MODEL, "", self._resolution, 0.7)
                self._detector_kind = "yunet"
            except Exception as e:
                logger.warning(f"VisualGate: YuNet non disponibile ({e}) — fallback Haar, "
                               "niente riconoscimento identità")
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                self._detector = cv2.CascadeClassifier(cascade_path)
                if self._detector.empty():
                    raise RuntimeError("Haar cascade non trovato")
                self._detector_kind = "haar"
            self._running = True
            if self._social_perception is not None:
                # Fase 0: backend opzionale e fail-silent. Presence/identity non
                # dipendono mai dalla percezione sociale.
                self._social_perception.start()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="visual-gate")
            self._thread.start()
            recog = "riconoscimento attivo" if (self._detector_kind == "yunet" and
                                                self._face_auth and self._face_auth.is_enabled()) \
                    else "solo presenza"
            logger.info(f"VisualGate avviato — {self._detector_kind}, 2fps, {recog}")
        except Exception as e:
            logger.warning(f"VisualGate non disponibile ({e}) — Euri funziona senza gate visivo")
            self._gate_active = True   # fail-open: senza webcam, processa sempre
            self._blind = True         # ...ma segnala che la presenza NON è osservabile

    def stop(self):
        self._running = False
        if self._social_perception is not None:
            self._social_perception.stop()

    def set_social_perception(self, receptor) -> None:
        """Collega il recettore prima di `start`; presenza/identita' restano autonome."""
        if self._running:
            raise RuntimeError("social perception must be attached before VisualGate.start")
        self._social_perception = receptor

    def set_enrollment_bridge(self, request_reader, status_writer) -> None:
        """Collega il canale UI prima di `start`, senza condividere frame o biometria."""
        if self._running:
            raise RuntimeError("enrollment bridge must be attached before VisualGate.start")
        self._enrollment_request_reader = request_reader
        self._enrollment_status_writer = status_writer

    def is_user_present(self) -> bool:
        """True se il gate è attivo (c'è QUALCUNO, o presenza recente): processa voce."""
        with self._lock:
            return self._gate_active

    def is_owner_present(self) -> bool:
        """True se davanti allo schermo c'è il PROPRIETARIO riconosciuto in faccia.
        È il segnale per l'efferente (parlare per primi). Mai True se cieco o
        senza riconoscimento: in quei casi il consumer ripiega sulla voce
        autenticata recente."""
        if config.DEMO_MODE:
            return True
        with self._lock:
            return (not self._blind and self._gate_active and
                    self._identity == getattr(config, "FACE_AUTH_OWNER", "stefano"))

    def present_identity(self) -> str | None:
        """Nome della persona riconosciuta davanti allo schermo, o None se ignota."""
        with self._lock:
            return self._identity

    def operational_snapshot(self, *, now: float | None = None) -> dict:
        """Stato visivo sanitizzato per altri processi locali.

        Non espone frame, embedding o similarity. ``owner_present`` richiede sia
        un volto visto adesso sia un match positivo recente: e' quindi piu'
        stretto dell'identita' sticky usata internamente dal gate vocale.
        """
        at = time.monotonic() if now is None else float(now)
        owner = getattr(config, "FACE_AUTH_OWNER", "owner")
        identity_max_age = getattr(config, "SOCIAL_PERCEPTION_IDENTITY_MAX_AGE_S", 8)
        face_max_age = max(1.5, self._interval * 3.0)
        with self._lock:
            face_detected = bool(
                self._last_seen > 0 and at - self._last_seen <= face_max_age
            )
            identity_fresh = bool(
                face_detected
                and self._identity is not None
                and self._last_identity_positive_ts > 0
                and at - self._last_identity_positive_ts <= identity_max_age
            )
            identity = self._identity if identity_fresh else None
            recognition_available = bool(
                not self._blind
                and self._running
                and self._detector_kind == "yunet"
                and self._face_auth is not None
                and self._face_auth.is_enabled()
            )
            return {
                "camera_available": bool(self._running and not self._blind),
                "recognition_available": recognition_available,
                "gate_active": bool(self._gate_active),
                "face_detected": face_detected,
                "identity": identity,
                "owner_present": bool(identity == owner),
                "demo_mode": bool(config.DEMO_MODE),
            }

    def fresh_owner_identity(self, *, now: float | None = None) -> str | None:
        """Owner only when a positive face match is recent enough for profiling.

        Normal presence may use sticky identity across a temporary bad pose. Social
        perception has a stricter privacy boundary: stickiness alone is not proof
        that the current face still belongs to the owner.
        """
        at = time.monotonic() if now is None else float(now)
        max_age = getattr(config, "SOCIAL_PERCEPTION_IDENTITY_MAX_AGE_S", 8)
        with self._lock:
            owner = getattr(config, "FACE_AUTH_OWNER", "stefano")
            if (self._identity == owner and self._last_identity_positive_ts > 0 and
                    at - self._last_identity_positive_ts <= max_age):
                return owner
            return None

    def is_blind(self) -> bool:
        """True se la webcam non è disponibile (fail-open): is_user_present() vale sempre True
        e NON va usato come prova di presenza — usare l'interazione recente come segnale primario."""
        with self._lock:
            return self._blind

    def notify_activity(self):
        """Chiamato dal daemon ad ogni STT trascritto o TTS pronunciato."""
        with self._lock:
            self._last_activity = time.monotonic()

    def consume_just_activated(self) -> bool:
        """One-shot: True la prima volta dopo una transizione INACTIVE→ACTIVE
        (qualcuno è arrivato — per il pulse afferente, non per parlare)."""
        with self._lock:
            if self._just_activated:
                self._just_activated = False
                return True
            return False

    def consume_owner_arrived(self) -> bool:
        """One-shot: True quando l'identità riconosciuta DIVENTA il proprietario.
        È il momento giusto per l'efferente di rientro (reminder persi, TEACH)."""
        with self._lock:
            if self._owner_arrived:
                self._owner_arrived = False
                return True
            return False

    # ──────────────────────────────────────────
    # Loop interno
    # ──────────────────────────────────────────

    def _camera_candidates(self) -> list[tuple[int | str, str]]:
        """Restituisce sorgenti V4L2 in ordine stabile, oppure l'override esplicito.

        Gli indici `/dev/videoN` non sono persistenti tra riconnessioni USB. Inoltre
        una singola webcam può esporre più nodi (video e metadata): `_open_camera`
        prova quindi un frame reale, non si limita a `isOpened()`.
        """
        if self._camera_override is not None:
            return [(self._camera_override, str(self._camera_override))]

        def device_number(path: Path) -> int:
            suffix = path.name.removeprefix("video")
            return int(suffix) if suffix.isdigit() else 10**9

        devices = sorted(Path("/dev").glob("video[0-9]*"), key=device_number)
        if devices:
            return [(str(path), str(path)) for path in devices]
        # Portabilità/fail-open: su piattaforme senza V4L2 OpenCV può comunque
        # risolvere la camera predefinita tramite il proprio backend.
        return [(0, "indice 0")]

    def _open_camera(self):
        """Apre il primo candidato che produce davvero un frame valido."""
        cv2 = self._cv2
        backend = getattr(cv2, "CAP_V4L2", None)
        for source, label in self._camera_candidates():
            try:
                cap = (cv2.VideoCapture(source, backend)
                       if backend is not None else cv2.VideoCapture(source))
            except Exception as e:
                logger.debug(f"VisualGate: apertura {label} fallita ({e})")
                continue
            if not cap.isOpened():
                cap.release()
                continue

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._resolution[1])
            cap.set(cv2.CAP_PROP_FPS, 2)
            ret, frame = cap.read()
            if ret and frame is not None:
                self._camera_index = source
                return cap, frame, label
            cap.release()
        return None, None, None

    def _enter_camera_fail_open(self) -> bool:
        """Marca la camera indisponibile senza trasformare il buio in presenza.

        Ritorna True soltanto alla transizione, utile per non ripetere il warning
        a ogni tentativo di riconnessione.
        """
        with self._lock:
            transitioned = not self._blind
            self._blind = True
            self._gate_active = True
            self._just_activated = False
            self._face_count = 0
            self._last_seen = 0.0
            # Un'identità sticky appartiene al vecchio stream: dopo un guasto non
            # può autorizzare iniziativa o profilazione sulla nuova connessione.
            self._identity = None
            self._identity_sim = 0.0
            self._last_identity_positive_ts = 0.0
            self._owner_arrived = False
            self._recog_fails = 0
            return transitioned

    def _detect(self, frame):
        """Ritorna (face_detected: bool, best_row) dove best_row è la riga YuNet
        (box+landmark) della faccia più grande, o None (Haar non dà landmark)."""
        cv2 = self._cv2
        if self._detector_kind == "yunet":
            self._detector.setInputSize((frame.shape[1], frame.shape[0]))
            _, faces = self._detector.detect(frame)
            if faces is None or len(faces) == 0:
                self._face_count = 0
                return False, None
            self._face_count = len(faces)
            best = max(faces, key=lambda f: f[2] * f[3])
            return True, best
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._detector.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
        self._face_count = len(faces)
        return len(faces) > 0, None

    def _publish_enrollment(self, request: dict, state: str, **extra) -> None:
        if self._enrollment_status_writer is None:
            return
        payload = {
            "session_id": str(request.get("session_id", "")),
            "name": str(request.get("name", "")),
            "state": state,
            "captured": len(self._enrollment_embeddings),
            "updated_at": time.time(),
            **extra,
        }
        try:
            self._enrollment_status_writer(payload)
        except Exception as exc:
            logger.debug(f"VisualGate enrollment: stato non pubblicato ({exc})")

    def _reset_enrollment(self, request: dict | None = None) -> None:
        self._enrollment_session_id = str((request or {}).get("session_id", ""))
        self._enrollment_name = str((request or {}).get("name", ""))
        self._enrollment_embeddings = []
        self._enrollment_last_nonce = ""
        self._enrollment_last_activity = time.monotonic() if request else 0.0

    def _process_enrollment(self, frame, face_detected: bool, face_row, now: float) -> bool:
        """Consuma un comando UI; True sospende gli altri consumer per quel frame."""
        if self._enrollment_request_reader is None or self._face_auth is None:
            return False
        if now - self._last_enrollment_poll < 0.25:
            return False
        self._last_enrollment_poll = now
        try:
            request = self._enrollment_request_reader()
        except Exception as exc:
            logger.debug(f"VisualGate enrollment: richiesta non leggibile ({exc})")
            return False
        if not isinstance(request, dict):
            if (self._enrollment_session_id and self._enrollment_last_activity and
                    now - self._enrollment_last_activity > float(
                        getattr(config, "FACE_ENROLLMENT_TTL_S", 300)
                    )):
                self._reset_enrollment()
            return False

        session_id = str(request.get("session_id", ""))
        name = str(request.get("name", ""))
        action = str(request.get("action", ""))
        nonce = str(request.get("nonce", ""))
        if not session_id or not name:
            return False
        self._enrollment_last_activity = now

        if action == "cancel":
            if session_id == self._enrollment_session_id:
                self._reset_enrollment()
            self._publish_enrollment(request, "cancelled")
            return False

        if session_id != self._enrollment_session_id:
            self._reset_enrollment(request)
            self._publish_enrollment(request, "ready")

        if action != "capture" or not nonce or nonce == self._enrollment_last_nonce:
            return False
        self._enrollment_last_nonce = nonce
        expected_index = len(self._enrollment_embeddings)
        try:
            requested_index = int(request.get("pose_index", -1))
        except (TypeError, ValueError):
            requested_index = -1
        if requested_index != expected_index:
            self._publish_enrollment(
                request, "error", nonce=nonce, message="sequenza degli scatti non valida"
            )
            return True
        if not face_detected or face_row is None:
            self._publish_enrollment(
                request, "error", nonce=nonce, message="nessun volto rilevato"
            )
            return True
        if self._face_count != 1:
            self._publish_enrollment(
                request, "error", nonce=nonce, message="serve esattamente un volto"
            )
            return True

        embedding = self._face_auth.embed(frame, face_row)
        if embedding is None:
            self._publish_enrollment(
                request, "error", nonce=nonce, message="embedding del volto non riuscito"
            )
            return True
        self._enrollment_embeddings.append(embedding)
        if len(self._enrollment_embeddings) < 4:
            self._publish_enrollment(request, "captured", nonce=nonce)
            return True

        saved = self._face_auth.enroll_from_embeddings(
            self._enrollment_name, self._enrollment_embeddings
        )
        if saved:
            logger.info(
                f"VisualGate: enrollment guidato completato per '{self._enrollment_name}'"
            )
            self._publish_enrollment(request, "completed", nonce=nonce)
        else:
            self._publish_enrollment(
                request, "error", nonce=nonce, message="salvataggio faceprint fallito"
            )
        return True

    def _update_identity(self, frame, face_row, now: float):
        """Riconoscimento throttled + identità sticky. Chiamato SOLO con faccia nel frame."""
        if face_row is None or not (self._face_auth and self._face_auth.is_enabled()):
            return
        interval = RECOG_RETRY_S if self._identity is None else RECOG_REFRESH_S
        if self._social_perception is not None and self._identity == getattr(
                config, "FACE_AUTH_OWNER", "stefano"):
            # Keep the stricter social identity proof fresh without changing the
            # sticky presence semantics used by the rest of Euri.
            max_age = getattr(config, "SOCIAL_PERCEPTION_IDENTITY_MAX_AGE_S", 8)
            interval = min(interval, max(1.0, float(max_age) / 2.0))
        if now - self._last_recog_ts < interval:
            return
        self._last_recog_ts = now
        name, sim = self._face_auth.identify(frame, face_row)

        with self._lock:
            if name is not None:
                self._recog_fails = 0
                if name != self._identity:
                    logger.info(f"VisualGate: riconosciuto '{name}' (sim={sim:.3f})")
                    self._identity = name
                    if name == getattr(config, "FACE_AUTH_OWNER", "stefano"):
                        self._owner_arrived = True
                self._identity_sim = sim
                self._last_identity_positive_ts = now
            elif self._identity is not None:
                # Faccia presente ma non verificabile (profilo, luce): l'identità è
                # sticky, ma dopo RECOG_MAX_FAILS ri-verifiche fallite non possiamo
                # più affermare chi c'è — meglio un falso "ignoto" che un falso owner.
                self._recog_fails += 1
                if self._recog_fails >= RECOG_MAX_FAILS:
                    logger.info(f"VisualGate: identità '{self._identity}' non più verificabile → ignoto")
                    self._identity = None
                    self._last_identity_positive_ts = 0.0
                    self._recog_fails = 0

    def _loop(self):
        cap = None
        first_frame = None
        consecutive_no_face = 0
        consecutive_read_failures = 0
        reconnect_delay = self._reconnect_interval

        while self._running:
            if cap is None:
                cap, first_frame, camera_label = self._open_camera()
                if cap is None:
                    if self._enter_camera_fail_open():
                        logger.warning(
                            "VisualGate: webcam non accessibile — fail-open; "
                            "nuovo tentativo automatico"
                        )
                    time.sleep(reconnect_delay)
                    reconnect_delay = min(
                        self._reconnect_max_interval,
                        max(self._reconnect_interval, reconnect_delay * 2.0),
                    )
                    continue

                with self._lock:
                    recovered = self._blind
                    self._blind = False
                consecutive_no_face = 0
                consecutive_read_failures = 0
                reconnect_delay = self._reconnect_interval
                if recovered:
                    logger.info(f"VisualGate: webcam riconnessa ({camera_label})")
                else:
                    logger.info(f"VisualGate: webcam aperta ({camera_label})")

            t_start = time.monotonic()
            if first_frame is not None:
                ret, frame = True, first_frame
                first_frame = None
            else:
                ret, frame = cap.read()

            if not ret or frame is None:
                consecutive_read_failures += 1
                if consecutive_read_failures < self._read_failures_before_reconnect:
                    time.sleep(self._interval)
                    continue

                logger.warning(
                    "VisualGate: stream webcam non risponde — rilascio e riconnessione"
                )
                cap.release()
                cap = None
                first_frame = None
                consecutive_read_failures = 0
                consecutive_no_face = 0
                self._enter_camera_fail_open()
                continue

            consecutive_read_failures = 0
            face_detected, face_row = self._detect(frame)

            # Prima dell'identificazione: l'enrollment deve funzionare anche per
            # una persona non ancora registrata, ma soltanto su comando UI esplicito.
            enrollment_frame = self._process_enrollment(
                frame, face_detected, face_row, time.monotonic()
            )

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
                    if (self._identity is not None and
                            now - self._last_seen >= IDENTITY_TIMEOUT_S):
                        logger.info(f"VisualGate: '{self._identity}' non visto da "
                                    f"{IDENTITY_TIMEOUT_S/60:.0f} min → identità dimenticata")
                        self._identity = None
                        self._last_identity_positive_ts = 0.0
                        self._recog_fails = 0

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

            # Riconoscimento fuori dal lock del frame-state (fa inference SFace)
            if face_detected and not enrollment_frame:
                self._update_identity(frame, face_row, time.monotonic())
                if self._social_perception is not None:
                    self._social_perception.process_frame(
                        frame,
                        self.fresh_owner_identity(),
                        monotonic_at=time.monotonic(),
                        observed_at=time.time(),
                    )

            elapsed = time.monotonic() - t_start
            time.sleep(max(0.0, self._interval - elapsed))

        if cap is not None:
            cap.release()
        logger.debug("VisualGate: webcam chiusa")
