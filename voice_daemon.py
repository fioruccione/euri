"""
voice_daemon.py — Loop principale always-on vocale di Euri.

Flusso:
  microfono → VAD → STT → intent_router → [branch] → brain → TTS → speaker
"""
import re
import sys
import signal
import threading
import time
import numpy as np
from loguru import logger
import config

import redis as redis_lib

from utils.redis_client import get_client, init_indexes, backfill_embeddings
from utils.date_utils import now, format_datetime

from voice.audio_io import AudioCapture, play_audio
from voice.vad import VAD
from voice.stt import STT
from voice.tts import TTS
from voice.visual_gate import VisualGate
from voice.speaker_auth import SpeakerAuth, ENROLL_UTTERANCES

from core.intent_router import (
    Intent, classify, extract_content_after_trigger,
    TEACH_END_SIGNALS, TRANSLATE_END_SIGNALS,
    DICTATION_END_SIGNALS, DICTATION_SAVE_FILE, DICTATION_COPY_CLIPBOARD,
    extract_target_language,
)
from core.intent_router import (
    SAVE_MEMORY_TRIGGERS, SAVE_TODO_TRIGGERS,
    SAVE_NOTE_TRIGGERS, SEARCH_TRIGGERS,
    detect_note_category,
)
from core.time_parser import extract_due_date
from core.memory_manager import MemoryManager
from core.brain import Brain
from core.embedder import Embedder
from agent.executor import Executor


# ──────────────────────────────────────────
# Interrupt vocale durante playback
# ──────────────────────────────────────────
_INTERRUPT_KEYWORDS = frozenset({"stop", "fermati", "basta", "taci", "silenzio"})

# ──────────────────────────────────────────
# Correzioni misrecognition STT (Whisper IT)
# ──────────────────────────────────────────
_WAKE_WORD_RE = re.compile(r'\beuri\b', re.IGNORECASE)
_CONVERSATION_WINDOW_SEC = 45  # secondi di conversazione attiva senza wake word

_STT_CORRECTIONS: dict[str, str] = {
    "salve a tutti": "salva tutto",
    "salve tutti": "salva tutto",
    "salve il tutto": "salva tutto",
    "salvati": "salva tutto",
    "salvatutto": "salva tutto",
    "plus vision": "PlastVision",
    "class vision": "PlastVision",
    "last vision": "PlastVision",
    "plusvision": "PlastVision",
    # Dettatura — varianti misrecognition
    "modalità dittatura": "modalità dettatura",
    "modalità di ettatura": "modalità dettatura",
    "modalità d'ettatura": "modalità dettatura",
    "modo dittatura": "modalità dettatura",
    "mode dittatura": "modalità dettatura",
    "inizia dittatura": "modalità dettatura",
    "ho detto modalità dittatura": "modalità dettatura",
    "copiano gli appunti": "copia negli appunti",
    "copiano l'appunti": "copia negli appunti",
    "clip buona": "clipboard",
    "clip, bordo": "clipboard",
    "clip bordo": "clipboard",
    "salva nella clip buona": "copia negli appunti",
    "salva nella clipboard": "copia negli appunti",
    "salva nella clip, bordo": "copia negli appunti",
    "salva nella clip bordo": "copia negli appunti",
    "copia nella clipboard": "copia negli appunti",
    "copia nella clip": "copia negli appunti",
}

# ──────────────────────────────────────────
# Setup logging
# ──────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
logger.add("logs/voice_daemon.log", rotation="10 MB", retention="7 days", level="DEBUG", enqueue=True)


def _tts_trim(text: str, max_chars: int = 400) -> str:
    """Tronca testo lungo a max_chars (al confine di frase) per output vocale."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind(". ", 0, max_chars)
    if cut == -1:
        cut = max_chars
    else:
        cut += 1
    return text[:cut].strip() + " Dimmi se vuoi i dettagli."


class _PendingState:
    """Stato temporaneo con timeout — sostituisce le coppie (dict, float) sparse in __init__."""
    __slots__ = ("data", "_ts", "_timeout")

    def __init__(self, data: dict, timeout: float):
        self.data = data
        self._ts = time.time()
        self._timeout = timeout

    def expired(self) -> bool:
        return time.time() - self._ts > self._timeout


class VoiceDaemon:
    def __init__(self):
        self.r: redis_lib.Redis = get_client()
        self.embedder = Embedder()
        self.memory = MemoryManager(self.r, embedder=self.embedder)
        self.brain = Brain()
        Brain._shared_instance = self.brain  # Condivisa col CodeRunner
        self.brain._episode_callback = lambda summary: self.memory.save_memory(
            summary,
            category="episodio", source="episode"
        )
        self.executor = Executor()
        self.executor.brain  = self.brain
        self.executor.memory = self.memory
        self.vad = VAD()
        self.stt = STT()
        self.tts = TTS()
        self.visual_gate = VisualGate()
        self.speaker_auth = SpeakerAuth()
        self._enroll_mode = False
        self._enroll_segments: list = []
        self._running = False
        self._missed_reminders: list[str] = []  # promemoria scattati mentre eri assente
        self._teach_recovery_mode = False        # in attesa di risposta su recovery TEACH
        self._teach_snapshot_content = ""
        self._teach_mode = False
        self._teach_confirm_mode = False
        self._teach_buffer: list[str] = []
        self._teach_topic = ""
        self._teach_asked: list[str] = []
        self._teach_pending_save = ""
        self._web_pending: dict = {}  # contesto ultima ricerca web (per "approfondisci" / "salva")
        self._pending_todo: _PendingState | None = None   # todo in attesa di conferma (timeout 60s)
        self._pending_write: _PendingState | None = None  # richiesta scrittura file in attesa (timeout 120s)
        self._last_speech_content: str = ""      # ultima risposta lunga di Euri (per "scrivilo")
        self._last_speech_ts: float = 0.0        # timestamp di _last_speech_content (TTL 300s)
        self._translate_bidir = False       # modalità interprete bidirezionale IT↔EN
        self._dictation_mode = False
        self._dictation_buffer: list[str] = []
        self._audit_confirm_mode = False       # Euri ha fatto l'audit, attende sì/no per cancellare
        self._audit_rumore: list[dict] = []    # memorie segnate come rumore dall'ultimo audit
        self._last_activity_ts: float = 0.0   # timestamp ultima attività vocale (per passive learner)
        self._passive_history_len: int = 0     # lunghezza history già analizzata
        self._consolidation_last_run: float = 0.0  # timestamp ultimo Loop 2a
        self._brain_lock = threading.Lock()  # protegge brain tra main loop e mobile worker
        self._first_visual_activation = True  # True finché il VisualGate non vede l'utente per la prima volta
        self._tts_lock   = threading.Lock()  # protegge il modello TTS da accessi concorrenti

        # Impegni verbali → azioni reali: (pattern sulla risposta di Euri, callable(text, reply))
        self._IMPLICIT_ACTIONS = [
            (re.compile(r'\b(controllo|verifico|guardo)\b.{0,30}\b(todo|task|scadenz)', re.IGNORECASE),
             lambda t, r: self._handle_list_today("controlla i todo")),
            (re.compile(r'\b(leggo|guardo|controllo)\b.{0,20}\blog\b', re.IGNORECASE),
             lambda t, r: self._handle_execute("leggi il log")),
            (re.compile(r'\b(controllo|verifico)\b.{0,20}\b(cpu|ram|disco|spazio)\b', re.IGNORECASE),
             lambda t, r: self._handle_execute("controlla la cpu")),
            # Quando Euri dice "ho salvato / memorizzato / aggiornato": salva davvero i fatti del turno
            (re.compile(
                r'\b(tutto\s+salvato|ho\s+salvato|salvato\s+nel|ho\s+memorizzato|ho\s+preso\s+nota|ho\s+aggiornato)\b',
                re.IGNORECASE,
            ), lambda t, r: self._save_turn_knowledge(t, r)),
        ]

    def setup(self):
        logger.info("Inizializzazione Euri...")
        init_indexes(self.r)
        self.vad.load()
        self.stt.load()
        self.tts.load()
        self.visual_gate.start()
        self.speaker_auth.load()
        self.embedder.load()
        backfill_embeddings(self.r, self.embedder)

        if config.ADAPTIVE_CLASSIFIER_ENABLED:
            from core.adaptive_classifier import AdaptiveClassifier
            from core.llm_classifier import set_adaptive_classifier
            clf = AdaptiveClassifier()
            clf.setup(self.embedder, self.r)
            set_adaptive_classifier(clf)

        # Inizializza Dream Engine
        from core.dream_engine import DreamEngine
        self.dream_engine = DreamEngine(self.r, self.embedder, brain=self.brain, memory=self.memory)
        
        # Inizializza Obsidian Sync Watcher
        from utils.obsidian_sync import ObsidianSyncManager
        self.obsidian_sync = ObsidianSyncManager(self.r, self.embedder)

        from voice.audio_io import _sd_disabled, _output_device
        if _sd_disabled:
            logger.info("Audio: screen sharing rilevato — modalità afplay/say attiva")
        if _output_device is not None:
            import sounddevice as _sd
            logger.info(f"Audio output: [{_output_device}] '{_sd.query_devices(_output_device)['name']}' (AEC attiva)")
        else:
            logger.warning("Audio output: Jabra non trovato — uso device di sistema")
        logger.info("Euri pronto. In ascolto...")

    _URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)

    def _warmup_model(self):
        """Carica il modello LLM in VRAM senza produrre output vocale."""
        try:
            import ollama
            ollama.chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": "ok"}],
                options={"num_predict": 1},
                keep_alive=-1,
            )
            logger.info("Warm-up modello completato.")
        except Exception as e:
            logger.warning(f"Warm-up modello fallito: {e}")

    def _clean_for_speech(self, text: str) -> str:
        """Rimuove URL e artefatti non leggibili prima del TTS."""
        return self._URL_RE.sub('', text).strip()

    def _speak(self, text: str, lang: str = "it"):
        """Sintetizza e riproduce testo con interrupt listener attivo."""
        text = self._clean_for_speech(text)
        if not text:
            return
        self._last_activity_ts = time.monotonic()
        logger.info(f"Euri: {text}")
        self.visual_gate.notify_activity()
        self.r.set("euri:audio:lock", "1", ex=300)
        interrupted = False
        try:
            _t_tts = time.perf_counter()
            samples, sr = self.tts.synthesize(text, lang=lang)
            logger.info(f"[TIMING] TTS synth: {(time.perf_counter()-_t_tts)*1000:.0f}ms ({len(text)} chars)")
            stop_event = threading.Event()
            listener = threading.Thread(target=self._interrupt_listener, args=(stop_event,), daemon=True)
            listener.start()
            try:
                interrupted = play_audio(samples, sr, stop_event=stop_event)
            finally:
                stop_event.set()
                listener.join(timeout=2)
        except Exception as e:
            logger.error(f"Audio hardware irrecuperabile, fallback say: {e}")
            import subprocess
            voice = "Paola" if lang == "it" else "Samantha"
            try:
                subprocess.run(["say", "-v", voice, text], timeout=300)
            except Exception as say_err:
                logger.critical(f"say fallback fallito: {say_err} — Euri muto")
        finally:
            self.r.delete("euri:audio:lock")
        if interrupted:
            self._speak_simple("Ok.")

    def _speak_simple(self, text: str, lang: str = "it"):
        """Speak non-interrombibile — usato per acknowledgment post-interruzione."""
        text = self._clean_for_speech(text)
        if not text:
            return
        logger.info(f"Euri: {text}")
        self.r.set("euri:audio:lock", "1", ex=10)
        try:
            samples, sr = self.tts.synthesize(text, lang=lang)
            play_audio(samples, sr)
        except Exception as e:
            import subprocess
            voice = "Paola" if lang == "it" else "Samantha"
            try:
                subprocess.run(["say", "-v", voice, text], timeout=15)
            except Exception:
                pass
        finally:
            self.r.delete("euri:audio:lock")

    def _interrupt_listener(self, stop_event: threading.Event) -> None:
        """
        Thread leggero attivo durante il playback.
        Apre un secondo stream PyAudio sul Jabra, rileva voce tramite energia RMS,
        trascrive utterance brevi (<2.5s) e setta stop_event se sente un comando
        di interruzione (stop/fermati/basta/taci/silenzio).
        """
        import pyaudio

        _CHUNK = config.AUDIO_CHUNK_SAMPLES
        _RATE = config.AUDIO_RATE
        _RMS_VOICE = 0.015     # soglia energia per rilevare voce
        _RMS_SILENCE = 0.008   # soglia per rilevare silenzio (fine utterance)
        _MAX_UTTERANCE_S = 2.5  # comandi brevi — non storie

        pa = pyaudio.PyAudio()
        device_index = None
        target = config.AUDIO_INPUT_DEVICE
        if target:
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d["maxInputChannels"] > 0 and target.lower() in d["name"].lower():
                    device_index = i
                    break

        try:
            stream = pa.open(
                format=pyaudio.paInt16, channels=1, rate=_RATE,
                input=True, input_device_index=device_index,
                frames_per_buffer=_CHUNK,
            )
        except Exception as e:
            logger.warning(f"Interrupt listener: stream non disponibile: {e}")
            pa.terminate()
            return

        logger.debug("Interrupt listener attivo")
        try:
            while not stop_event.is_set():
                try:
                    raw = stream.read(_CHUNK, exception_on_overflow=False)
                except Exception:
                    break
                chunk = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

                if float(np.sqrt(np.mean(chunk ** 2))) < _RMS_VOICE:
                    continue

                # Voce rilevata — raccoglie utterance breve
                utterance = [chunk]
                t_start = time.monotonic()
                silence_run = 0

                while (time.monotonic() - t_start) < _MAX_UTTERANCE_S and not stop_event.is_set():
                    try:
                        raw2 = stream.read(_CHUNK, exception_on_overflow=False)
                    except Exception:
                        break
                    c2 = np.frombuffer(raw2, dtype=np.int16).astype(np.float32) / 32768.0
                    utterance.append(c2)
                    silence_run = silence_run + 1 if float(np.sqrt(np.mean(c2 ** 2))) < _RMS_SILENCE else 0
                    if silence_run >= 6:  # ~190ms di silenzio = fine parola
                        break

                if stop_event.is_set():
                    break

                audio_seg = np.concatenate(utterance)
                try:
                    text_heard, _ = self.stt.transcribe(audio_seg, force_lang="it")
                    if any(kw in text_heard.lower() for kw in _INTERRUPT_KEYWORDS):
                        logger.info(f"Playback interrotto: '{text_heard}'")
                        stop_event.set()
                except Exception:
                    pass
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
            logger.debug("Interrupt listener terminato")

    def _handle_shutdown(self, text: str = ""):
        logger.info("Shutdown vocale richiesto")
        self._speak_simple("Chiudo.")
        self._running = False
        self.visual_gate.stop()
        try:
            import sounddevice as _sd
            _sd.stop()
        except Exception:
            pass

    def _play_beep(self):
        """Segnale acustico breve — usato quando l'utente non è in frame ma c'è un reminder."""
        import subprocess
        # Suono di sistema macOS — nessuna dipendenza extra
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Ping.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _handle_save_memory(self, text: str):
        from core.validator import validate_payload
        # Se c'è una scadenza parsabile, è un todo mascherato da memory
        due_at = extract_due_date(text)
        if due_at:
            self._handle_save_todo(text)
            return
        content = extract_content_after_trigger(text, SAVE_MEMORY_TRIGGERS)
        if not content:
            self._speak("Cosa devo ricordare?")
            return
        content = validate_payload(content, "memory")
        if not content:
            self._speak("Non sembra una cosa utile da ricordare.")
            return
        if self.memory.is_duplicate_memory(content, llm_probe_fn=self.brain.probe_same_meaning):
            self._speak("Lo so già.")
            return
        self.memory.save_memory(content, source="user")
        self.memory.log_conversation("Stefano", text)
        reply = self.brain.confirm_save("memory", content)
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    def _handle_save_todo(self, text: str):
        from core.validator import validate_payload
        content = extract_content_after_trigger(text, SAVE_TODO_TRIGGERS)
        if not content:
            self._speak("Cosa devo segnarti?")
            return
        content = validate_payload(content, "todo")
        if not content:
            self._speak("Non sembra un impegno concreto.")
            return
        if self.memory.is_duplicate_todo(content):
            self._speak("L'ho già in lista.")
            return
        due_at = extract_due_date(text)
        # Non salva subito — chiede conferma e dettagli
        self._pending_todo = _PendingState({"content": content, "due_at": due_at}, timeout=60)
        due_str = f", scadenza {format_datetime(due_at)}" if due_at else ""
        self._speak(f"Segno: '{content}'{due_str}. Vuoi aggiungere dettagli o una scadenza? Oppure dimmi 'no' per annullare.")

    def _handle_pending_todo(self, text: str):
        """Gestisce la risposta di conferma/dettaglio/annullamento per un todo in attesa."""
        _CANCEL = re.compile(r'\b(no|annulla|lascia\s+perdere|non\s+salvare|non\s+voglio|scrap)\b', re.I)
        _CONFIRM = re.compile(r'\b(sì|si|ok|vai|salva|conferma|basta\s+così|va\s+bene)\b', re.I)

        pending = self._pending_todo
        self._pending_todo = None

        if _CANCEL.search(text):
            self._speak("Ok, non segno niente.")
            return

        content = pending.data["content"]
        due_at = pending.data["due_at"]

        if not _CONFIRM.search(text):
            # L'utente ha aggiunto dettagli — arricchisce il contenuto
            due_at = extract_due_date(text) or due_at
            content = f"{content} — {text.strip()}"

        self.memory.save_todo(content, due_at=due_at)
        due_str = format_datetime(due_at) if due_at else ""
        reply = self.brain.confirm_save("todo", content, due_str)
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    _SAVE_REPLY_RE = re.compile(
        r'\b(sì|si)[,.]?\s*(scrivilo?|salvalo?|mettilo?)\b'
        r'|\b(scrivilo?|salvalo?)\s*(lo|la|quello|questo)?\s*(nella?|sul?|in)\s*(cartella|file|disco|documento|testo)\b'
        r'|\bsalva\s+(quello\s+che\s+hai\s+(detto|appena\s+detto)|la\s+risposta|il\s+riassunto)\b',
        re.IGNORECASE
    )

    _WRITE_REQUEST_RE = re.compile(
        r'\b(potresti|puoi|riesci|mi\s+fai|fammi)\s+'
        r'(crea[ri]?|scriv[ei]?|generar[ei]?|preparar[ei]?|far[ei]?)\s+'
        r'(un[ao]?\s+)?(file|riassunto|testo|documento|report|schema|bozza|nota)\b'
        r'|\bcrea[ri]?\s+(un[ao]?\s+)?(documento|riassunto|schema|bozza|nota)(\s+di\s+testo)?\b'
        r'|\bscrivimi\s+(un[ao]?\s+)?(documento|testo|riassunto|schema|bozza|nota)\b'
        r'|\bgenera[mi]?\s+(un[ao]?\s+)?(documento|testo|riassunto|schema|bozza|nota)\b',
        re.IGNORECASE
    )

    _DATA_FORMAT_RE = re.compile(r'\b(csv|excel|xlsx?|pdf|grafico|tabella|ods|odt|odp)\b', re.I)

    def _handle_pending_write(self, text: str):
        """Gestisce la risposta di conferma/dettaglio/annullamento per una richiesta di scrittura file."""
        _CANCEL = re.compile(r'\b(no|annulla|lascia\s+perdere|non\s+importa|non\s+voglio|scrap)\b', re.I)
        _CONFIRM = re.compile(r'\b(sì|si|ok|vai|procedi|fai|conferm[ao]|basta\s+così|va\s+bene)\b', re.I)

        pending = self._pending_write
        self._pending_write = None

        if _CANCEL.search(text):
            self._speak("Ok, non creo niente.")
            return

        task = pending.data["task"]
        if not _CONFIRM.search(text):
            task = f"{task} — in più: {text.strip()}"

        # Se il task richiede formati dati strutturati (CSV, Excel, PDF…) → CodeRunner
        if self._DATA_FORMAT_RE.search(task):
            self._handle_execute(task)
            return

        # Altrimenti: documento di testo → LLM compone il contenuto, poi write_text
        self._speak("Sto componendo il documento.")
        compose_prompt = (
            f"Crea un documento di testo ben strutturato che risponda a questa richiesta: {task}\n"
            "Scrivi solo il contenuto del documento, senza introduzioni o note. "
            "Usa i dati e i valori menzionati nella conversazione recente."
        )
        composed = self.brain.respond(compose_prompt, context="[Componi un documento strutturato dai dati della conversazione. Solo contenuto, no prefazioni.]")
        from agent.tools.text_writer import tool_write_text
        res = tool_write_text({"text": composed})
        if res.success:
            fname = res.raw_data.get("filepath", "file").split("/")[-1]
            self.memory.log_conversation("Euri", f"[Documento salvato: {fname}]")
            self._speak(f"Documento creato e salvato come {fname}.")
        else:
            self._speak("Errore nella creazione del documento.")

    def _handle_save_note(self, text: str):
        from core.validator import validate_payload
        content = extract_content_after_trigger(text, SAVE_NOTE_TRIGGERS)
        if not content:
            content = text
        content = validate_payload(content, "note")
        if not content:
            self._speak("Non sembra un appunto utile.")
            return
        category = detect_note_category(text)
        self.memory.save_note(content, category=category)
        self.memory.log_conversation("Stefano", text)
        reply = self.brain.confirm_save("note", content)
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    def _handle_search(self, text: str):
        query = extract_content_after_trigger(text, SEARCH_TRIGGERS) or text
        self.memory.log_conversation("Stefano", text)

        # Cerca in tutti e tre gli store
        memories = self.memory.search_memories(query)
        notes = self.memory.search_notes(query)
        todos = self.memory.find_todo_by_content(query)

        all_results = memories + notes + todos
        reply = self.brain.format_search_results(all_results, query)
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    def _handle_list_today(self, text: str):
        self.memory.log_conversation("Stefano", text)
        todos = self.memory.get_todos_today()
        overdue = self.memory.get_overdue_todos()
        reply = self.brain.format_today_summary(todos, overdue)
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    def _handle_complete(self, text: str):
        self.memory.log_conversation("Stefano", text)
        # Cerca il todo più probabile
        keyword = self.brain.parse_completion_target(text)
        candidates = self.memory.find_todo_by_content(keyword)
        if not candidates:
            self._speak(f"Non trovo nessun todo su '{keyword}'. Puoi essere più preciso?")
            return
        # Completa il primo risultato
        todo = candidates[0]
        self.memory.complete_todo(todo["id"])
        reply = self.brain.complete_todo_response(todo["content"])
        self.memory.log_conversation("Euri", reply)
        self._speak(reply)

    def _handle_silence_mode(self, text: str):
        from utils.date_utils import parse_italian_date
        import re

        # Cerca un orario specifico nel testo
        match = re.search(r"(?:fino\s+(?:a|alle?)\s+)([\w\s:]+)", text, re.IGNORECASE)
        if match:
            until = parse_italian_date(match.group(1))
            if until:
                self.memory.set_silent_mode(until)
                self._speak(f"Ok, silenzio fino alle {until.strftime('%H:%M')}.")
                return

        # Default: 2 ore
        from datetime import timedelta
        until = now() + timedelta(hours=2)
        self.memory.set_silent_mode(until)
        self._speak("Ok, mi faccio sentire tra due ore.")

    def _handle_restore_alerts(self, text: str):
        self.memory.clear_silent_mode()
        self._speak("Alert riattivati.")

    def _handle_status(self, text: str):
        todos = self.memory.get_pending_todos()
        overdue = self.memory.get_overdue_todos()
        memories = self.memory.get_recent_memories(limit=999)
        reply = self.brain.generate_status(len(todos), len(overdue), len(memories))
        self._speak(reply)

    def _handle_execute(self, text: str):
        """Esegue un tool di sistema tramite l'Executor sandbox."""
        self.memory.log_conversation("Stefano", text)

        # Reset stop_event per la nuova esecuzione
        self.executor.stop_event.clear()

        # Fast path: selettore regex deterministico (0ms, evita chiamata LLM)
        call = self.executor.select_tool_by_regex(text)
        if call is None:
            # Slow path: LLM per tool meno comuni (write_text, evaluate_math, ecc.)
            tools_desc = self.executor.get_tools_description()
            llm_response = self.brain.decide_tool_call(text, tools_desc)
            call = self.executor.parse_llm_response(llm_response)
        if call is None:
            # Nessun tool identificato: risposta breve senza ulteriore LLM (evita doppia inferenza)
            hint = "Non so quale controllo fare. Prova a dire: CPU, RAM, disco, processi, uptime."
            self.memory.log_conversation("Euri", hint)
            self._speak(hint)
            return

        # Per run_code: sostituisce la sentinella __USER_TEXT__ con la frase reale dell'utente
        if call.tool_name == "run_code":
            if not call.parameters.get("task") or call.parameters.get("task") == "__USER_TEXT__":
                call.parameters["task"] = text

        # Feedback vocale differenziato
        if call.tool_name == "run_code":
            self._speak("Ci penso, genero ed eseguo il codice.")
        elif call.tool_name == "analyze_image":
            self._speak("Guardo l'immagine.")
        else:
            self._speak("Controllo.")

        result = self.executor.execute_safe(call)
        self.memory.log_conversation("Euri", result)

        # Per analyze_image: inietta il testo completo nella history prima di parlare,
        # poi parla solo un riassunto breve (evita 2+ minuti di UUID/descrizioni parlate)
        if call.tool_name in ("analyze_image", "clipboard_analyze"):
            self.brain.inject_tool_result(text, result)
            self._speak(_tts_trim(result, max_chars=400))
        else:
            self._speak(result)


    def _handle_audit_memory(self, text: str):
        """Audit vocale delle memorie: analizza con LLM e propone cancellazione del rumore."""
        import json as _json
        import ollama as _ollama

        self.memory.log_conversation("Stefano", text)
        self._speak("Controllo la memoria. Un momento.")

        # Leggi tutte le memorie passive (RedisJSON su Mac)
        all_docs = []
        for key in self.memory.r.scan_iter("euri:memory:*"):
            try:
                data = self.memory.r.json().get(key, "$")
                if not data:
                    continue
                doc = data[0]
                doc["_key"] = key
                all_docs.append(doc)
            except Exception:
                pass

        if not all_docs:
            self._speak("Non ho nessuna memoria salvata.")
            return

        # Suddividi per source
        by_source: dict[str, int] = {}
        for doc in all_docs:
            src = doc.get("source", "?")
            by_source[src] = by_source.get(src, 0) + 1

        totale = len(all_docs)
        stats_str = ", ".join(f"{v} da {k}" for k, v in sorted(by_source.items(), key=lambda x: -x[1]))
        self._speak(f"Ho {totale} memorie in totale: {stats_str}. Analizzo le passive.")

        # Audita solo le passive
        passive_docs = [d for d in all_docs if d.get("source") == "passive"]
        if not passive_docs:
            self._speak("Nessuna memoria passiva da analizzare. Tutto pulito.")
            return

        rumore = []
        for doc in passive_docs:
            content = doc.get("content", "")
            prompt = (
                f"Valuta se questo testo è un FATTO UTILE da ricordare su Stefano "
                f"oppure è RUMORE (frase generica, conversazione ambient, dato non personale).\n"
                f"Memoria: \"{content}\"\n"
                f"Rispondi SOLO con UTILE o RUMORE."
            )
            try:
                resp = _ollama.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0, "num_predict": 300},
                    think=False,
                )
                verdetto = (resp.message.content or "").strip().upper()
                if "RUMORE" in verdetto:
                    rumore.append(doc)
            except Exception:
                pass

        utili_count = len(passive_docs) - len(rumore)

        if not rumore:
            self._speak(f"Memoria pulita. Tutte e {len(passive_docs)} le memorie passive sono utili.")
            return

        self._audit_rumore = rumore
        self._audit_confirm_mode = True
        self._speak(
            f"Su {len(passive_docs)} memorie passive: {utili_count} utili, {len(rumore)} rumore. "
            f"Le cancello?"
        )

    def _handle_audit_confirm(self, text: str):
        """Gestisce sì/no dopo l'audit vocale."""
        self._audit_confirm_mode = False
        if self._TEACH_CONFIRM_YES.search(text):
            for doc in self._audit_rumore:
                self.memory.r.delete(doc["_key"])
            n = len(self._audit_rumore)
            self._audit_rumore = []
            self._speak(f"Fatto. {n} memorie rumore cancellate. La memoria è pulita.")
        else:
            self._audit_rumore = []
            self._speak("Ok, non cancello niente.")

    @staticmethod
    def _relative_time(ts) -> str:
        """Converte un timestamp unix in stringa relativa (es. '3 settimane fa')."""
        if not ts:
            return ""
        try:
            delta = time.time() - float(ts)
            days = delta / 86400
            if days < 1:
                return "oggi"
            if days < 7:
                d = int(days)
                return f"{d} {'giorno' if d == 1 else 'giorni'} fa"
            if days < 30:
                w = int(days / 7)
                return f"{w} {'settimana' if w == 1 else 'settimane'} fa"
            if days < 365:
                m = int(days / 30)
                return f"{m} {'mese' if m == 1 else 'mesi'} fa"
            y = int(days / 365)
            return f"{y} {'anno' if y == 1 else 'anni'} fa"
        except Exception:
            return ""

    # Parole funzione italiane da escludere dalla query di ricerca
    _STOP_WORDS = {
        "come", "cosa", "quando", "dove", "perché", "però", "anche", "solo",
        "tutto", "tutti", "tutta", "tutte", "questo", "questa", "questi", "queste",
        "quello", "quella", "quelli", "quelle", "volevo", "volendo", "posso", "devo",
        "sono", "essere", "avere", "fare", "dire", "stare", "della", "delle", "degli",
        "dello", "nella", "nelle", "negli", "nello", "negli", "oppure", "invece",
        "ancora", "adesso", "quindi", "allora", "certo", "magari", "tanto", "molto",
        "poco", "bene", "male", "così", "tipo", "parte", "fatto", "fatto", "altra",
        "altro", "altri", "altre", "prima", "dopo", "sempre", "spesso", "quasi",
        "circa", "forse", "senza", "verso", "dentro", "fuori", "sopra", "sotto",
        "mentre", "però", "comunque", "ricordi", "saper", "sapere",
    }

    def _build_context(self, text: str) -> str:
        """Cerca in Redis contenuto rilevante da iniettare come contesto nella risposta."""
        from utils.temporal import extract_temporal_range
        source_filter = config.DEMO_CONTEXT_SOURCES if config.DEMO_MODE else None

        # Reflections da Loop 2a — solo fuori DEMO_MODE (source=reflection non è in DEMO_CONTEXT_SOURCES)
        reflection_lines = []
        if not config.DEMO_MODE:
            for r in self.memory.get_recent_reflections(limit=2):
                reflection_lines.append(f"- {r['content']}")

        results = self.memory.get_recent_memories(limit=5, source_filter=source_filter)
        seen_ids = {r.get("id") for r in results}

        # Ricerca temporale: se la query contiene "ieri", "5 maggio", "lunedì" ecc.
        # le memorie di quel periodo vengono aggiunte in cima ai risultati.
        time_range = extract_temporal_range(text, now())
        if time_range:
            ts_start, ts_end = time_range
            time_mems = self.memory.search_memories_by_timerange(ts_start, ts_end, limit=5)
            for r in reversed(time_mems):
                if r.get("id") not in seen_ids:
                    results.insert(0, r)
                    seen_ids.add(r.get("id"))

        words = re.findall(
            r'\b[a-zA-ZàáâãäåèéêëìíîïòóôõöùúûüÀÁÂÃÄÅÈÉÊËÌÍÎÏÒÓÔÕÖÙÚÛÜ]{4,}\b', text
        )
        keywords = list(dict.fromkeys(
            w for w in words if w.lower() not in self._STOP_WORDS
        ))
        if keywords:
            extra_memories = self.memory.search_memories(text, limit=3, source_filter=source_filter)
            kw_query = " | ".join(keywords[:8])
            extra_notes = self.memory.search_notes(kw_query, limit=2)
            for r in extra_memories + extra_notes:
                if r.get("id") not in seen_ids:
                    results.append(r)
                    seen_ids.add(r.get("id"))

        # Insights cross-domain rilevanti per la query corrente
        insight_lines = []
        if keywords:
            for ins in self.memory.search_insights(text, limit=2):
                dom_a = ins.get("domain_a", "?")
                dom_b = ins.get("domain_b", "?")
                insight_lines.append(f"- [{dom_a} ↔ {dom_b}] {ins['content']}")

        sections = []
        if reflection_lines:
            sections.append("Sintesi recenti:\n" + "\n".join(reflection_lines))
        if results:
            mem_lines = []
            for r in results[:6]:
                age = self._relative_time(r.get("created_at"))
                label = f"[{r.get('domain', 'generale')} | {age}]" if age else f"[{r.get('domain', 'generale')}]"
                suffix = " [DATO NON VERIFICATO — contiene valori numerici]" if r.get("requires_verification") else ""
                mem_lines.append(f"- {label} {r['content']}{suffix}")
            sections.append("Ricordi/note rilevanti:\n" + "\n".join(mem_lines))
        if insight_lines:
            sections.append("Principi trasversali:\n" + "\n".join(insight_lines))

        # Triade RAG: log dei nodi usati per tracciare l'origine delle risposte
        if results:
            node_tags = [
                f"{r.get('source','?')}:{r.get('domain','?')}({r.get('id','')[:8]})"
                for r in results[:6]
            ]
            logger.info(f"RAG ctx [{len(results)} nodi]: {' | '.join(node_tags)}")

        return "\n\n".join(sections)

    def _handle_web_search(self, text: str):
        """Cerca sul web, risponde vocalmente, propone di salvare."""
        from core.web_search import is_online, search
        self.memory.log_conversation("Stefano", text)

        if not is_online():
            self._speak("Non ho internet adesso. Rispondo solo da quello che ricordo.")
            return

        query = self.brain.extract_search_query(text)
        if not query:
            self._speak("Cosa vuoi che cerchi?")
            return

        self._speak("Un secondo, cerco.")
        results = search(query)

        # Se risultati scarsi (0 o solo social/video), prova query fallback contestuale
        _WEAK_DOMAINS = ("youtube.com", "youtu.be", "threads.com", "instagram.com",
                         "facebook.com", "tiktok.com", "twitter.com", "x.com")
        is_weak = not results or all(
            any(d in r.get("url", "") for d in _WEAK_DOMAINS) for r in results
        )
        if is_weak:
            fallback_query = self.brain.extract_query_fallback(query)
            if fallback_query != query:
                logger.info(f"Query fallback: '{query}' → '{fallback_query}'")
                results = search(fallback_query)
                query = fallback_query

        if not results:
            self._speak(f"Non ho trovato niente di utile su '{query}'.")
            return

        summary = self.brain.summarize_web_results(results, query)
        self.memory.log_conversation("Euri", summary)
        self._speak(summary)

        # Salva automaticamente in Redis — la conoscenza web diventa permanente (TTL 60gg)
        # requires_verification forzato: fonte esterna, non va citata come fatto certo
        mem_content = f"Ricerca web '{query}':\n{summary}"
        mid = self.memory.save_memory(
            content=mem_content,
            category="web",
            tags=["web_search"],
            source="web",
        )
        self.memory.r.json().set(f"euri:memory:{mid}", "$.requires_verification", True)
        logger.info(f"Web search salvata in memoria: {mid[:8]}… (query: '{query[:50]}')")

        # Tieni il contesto per eventuali "approfondisci" / "salva quello che hai trovato"
        self._web_pending = {"summary": summary, "results": results, "query": query}

    def _handle_translate(self, text: str):
        """Avvia modalità interprete bidirezionale IT↔EN."""
        self._translate_bidir = True
        self._speak("Interprete attivo. Parla italiano, traduco in inglese. Risposta in inglese, traduco in italiano. 'Fine traduzione' per uscire.")

    def _handle_translate_bidir(self, text: str, detected_lang: str):
        """
        Traduzione bidirezionale IT↔EN automatica.
        Whisper rileva la lingua parlata — Euri traduce nella direzione opposta.
        """
        if TRANSLATE_END_SIGNALS.search(text):
            self._translate_bidir = False
            self._speak("Conversazione terminata. Traduzione chiusa.")
            return

        lang_code = detected_lang[:2].lower()

        if lang_code == "it":
            # Stefano parla italiano → traduce in inglese per l'interlocutore
            translation = self.brain.translate(text, "inglese")
            logger.info(f"Bidir IT→EN: '{text[:50]}'")
            self._speak(translation, lang="en")
        else:
            # Interlocutore parla inglese (o altra lingua) → traduce in italiano per Stefano
            translation = self.brain.translate(text, "italiano")
            logger.info(f"Bidir {lang_code.upper()}→IT: '{text[:50]}'")
            self._speak(translation, lang="it")

    def _handle_dictation_start(self, text: str):
        """Avvia la modalità dettatura: accumula tutto il parlato fino al comando di chiusura."""
        self._dictation_mode = True
        self._dictation_buffer = []
        self.memory.log_conversation("Stefano", text)
        self._speak(
            "Modalità dettatura attiva. Parla pure, accumulo tutto. "
            "Quando hai finito di dettare di' 'copia negli appunti', 'salva su file', oppure 'fine dettatura'."
        )

    def _handle_dictation_continue(self, text: str):
        """
        Gestisce ogni utterance in modalità dettatura.
        - Segnali di chiusura: eseguono l'azione richiesta (clipboard, file, entrambi)
        - Tutto il resto: accumulato nel buffer senza rispondere
        """
        # Segnale di uscita senza azione
        if DICTATION_END_SIGNALS.search(text):
            n = len(self._dictation_buffer)
            self._dictation_mode = False
            self._dictation_buffer = []
            self._speak(f"Dettatura annullata. Avevo accumulato {n} {'frase' if n == 1 else 'frasi'}.")
            return

        save_file = DICTATION_SAVE_FILE.search(text)
        copy_clip = DICTATION_COPY_CLIPBOARD.search(text)

        if save_file or copy_clip:
            if not self._dictation_buffer:
                self._dictation_mode = False
                self._speak("Non hai dettato niente.")
                return

            full_text = " ".join(self._dictation_buffer)
            n_words = len(full_text.split())
            results = []

            if copy_clip:
                try:
                    import pyperclip
                    pyperclip.copy(full_text)
                    results.append("copiato negli appunti")
                except Exception:
                    results.append("errore clipboard")

            if save_file:
                from agent.tools.text_writer import tool_write_text
                res = tool_write_text({"text": full_text})
                if res.success:
                    fname = res.raw_data.get("filepath", "file").split("\\")[-1]
                    results.append(f"salvato in {fname}")
                else:
                    results.append("errore nel salvataggio")

            self._dictation_mode = False
            self._dictation_buffer = []
            azioni = " e ".join(results)
            self.memory.log_conversation("Euri", f"[Dettatura: {n_words} parole — {azioni}]")
            self._speak(f"Fatto. {n_words} parole: {azioni}. Puoi incollare con Ctrl+V.")
            return

        # Nessun segnale di chiusura — accumula silenziosamente
        self._dictation_buffer.append(text)
        n = len(self._dictation_buffer)
        # Feedback leggero ogni 5 frasi per far sapere che Euri sta ancora ascoltando
        if n % 5 == 0:
            self._speak(f"{n} frasi accumulate.")

    def _handle_save_last(self, text: str):
        """Salva un riassunto della conversazione recente."""
        self.memory.log_conversation("Stefano", text)
        with self.brain.history_lock:
            history = list(self.brain._conversation_history)
        if len(history) < 2:
            self._speak("Non c'è abbastanza conversazione da salvare.")
            return
        # Prendi gli ultimi 20 messaggi (10 scambi)
        recent = history[-20:]
        dialogue = "\n".join(
            f"{'Stefano' if m['role'] == 'user' else 'Euri'}: {m['content']}"
            for m in recent
        )
        summary = self.brain.summarize_knowledge(dialogue)
        self.memory.save_memory(summary, category="conoscenza", source="conversation")
        self.memory.log_conversation("Euri", f"[Conversazione salvata]")
        self._speak("Salvato. Ho riassunto e memorizzato quello di cui abbiamo parlato.")

    def _save_turn_knowledge(self, user_text: str, euri_reply: str):
        """Estrae e salva i fatti concreti del turno corrente — chiamata da implicit actions.

        Usa gli ultimi scambi dalla history (inclusi i risultati di analyze_image iniettati)
        per dare al LLM il contesto completo, poi salva via il normale pipeline save_memory()
        che gestisce domain assignment e requires_verification automaticamente.
        """
        with self.brain.history_lock:
            history = list(self.brain._conversation_history)

        # Prendi gli ultimi 6 messaggi (3 scambi) — cattura il risultato dell'analisi immagine
        recent = history[-6:] if len(history) >= 6 else history
        lines = [
            f"{'Stefano' if m['role'] == 'user' else 'Euri'}: {m['content']}"
            for m in recent
        ]
        # Aggiungi il turno corrente se non è già in history
        lines += [f"Stefano: {user_text}", f"Euri: {euri_reply}"]
        context = "\n".join(lines)

        extracted = self.brain.extract_knowledge_from_turn(context)
        if not extracted:
            logger.info("Implicit action save: nessun fatto concreto estratto, skip.")
            return

        if self.memory.is_duplicate_memory(extracted, llm_probe_fn=self.brain.probe_same_meaning):
            logger.info("Implicit action save: fatto già in memoria, skip.")
            return

        self.memory.save_memory(extracted, source="conversation")
        self.memory.log_conversation("Euri", f"[Conoscenza salvata — implicit action: '{extracted[:60]}...']")
        logger.info(f"Implicit action: salvato — '{extracted[:80]}'")


    def _handle_chat(self, text: str):
        self.memory.log_conversation("Stefano", text)

        # Intercetta richieste conversazionali di creazione file prima di chiamare l'LLM
        if self._WRITE_REQUEST_RE.search(text):
            self._pending_write = _PendingState({"task": text}, timeout=120)
            self._speak("Perfetto. Di' 'vai' per procedere subito, oppure aggiungimi dettagli da includere nel file.")
            return

        context = self._build_context(text)
        context = (context + "\n\n" if context else "") + "[Modalità conversazione: sii presente e naturale, non rigido.]"
        with self._brain_lock:
            reply = self.brain.respond(text, context=context)
        self.memory.log_conversation("Euri", reply)
        if len(reply) > 150:
            self._last_speech_content = reply
            self._last_speech_ts = time.time()
        self._speak(reply)

        # Se Euri ha promesso un'azione, eseguila subito
        for pattern, action in self._IMPLICIT_ACTIONS:
            if pattern.search(reply):
                logger.info(f"Implicit action rilevata dalla risposta CHAT")
                try:
                    action(text, reply)
                except Exception as e:
                    logger.warning(f"Implicit action fallita: {e}")
                break

    def _handle_teach(self, text: str):
        """Avvia la modalità insegnamento: Euri ascolta e fa domande di approfondimento."""
        self._teach_mode = True
        self._teach_confirm_mode = False
        self._teach_buffer = [text]
        self._teach_topic = text
        self._teach_asked = []
        self._teach_pending_save = ""
        self.memory.log_conversation("Stefano", text)
        self._speak("Dimmi, ti ascolto.")

    # Impegni di Euri in CHAT che devono tradursi in azioni reali
    _IMPLICIT_ACTIONS: list[tuple] = []  # popolato in __init__ dopo che i metodi esistono

    _FAREWELL = re.compile(
        r'\b(ciao|arrivederci|ci\s+sentiamo|buonanotte|buonasera|a\s+dopo|a\s+presto|ci\s+vediamo|a\s+domani|saluti)\b',
        re.IGNORECASE
    )
    # Segnali di contenuto tecnico/fattuale che vale la pena salvare
    _TECHNICAL_CONTENT = re.compile(
        r'\b(\d+\s*°|\d+\s*%|gradi|pressione|temperatura|materiale|processo|fluidità|stampo|'
        r'progetto|fornitore|cliente|costo|prezzo|quantità|formula|ricetta|procedura|problema|soluzione)\b',
        re.IGNORECASE
    )

    _TEACH_CONFIRM_YES = re.compile(
        r'\b(sì|si|esatto|corretto|giusto|perfetto|ok|bene|confermo|salvalo?|vai'
        r'|salva|salvami|salvalo|salva\s+quello|salvo\s+quello|salva\s+tutto|salva\s+pure)\b',
        re.IGNORECASE
    )
    _TEACH_CONFIRM_NO = re.compile(
        r'\b(no|sbagliato|errato|non\s+è\s+giusto|non\s+è\s+corretto|corregg[io]|aspetta)\b',
        re.IGNORECASE
    )
    _WEB_DEEPEN = re.compile(
        r'\b(approfondisci|approfondire|cerca\s+(ancora|di\s+più|altri|altro)|'
        r'cerca\s+su|cercami|un[a\s]+altra\s+ricerca|dimmi\s+di\s+più|'
        r'prova\s+(ancora|un[a\s]+altra)|vai\s+avanti|continua\s+a\s+cercare)\b',
        re.IGNORECASE
    )

    def _reset_teach(self):
        self._teach_mode = False
        self._teach_confirm_mode = False
        self._teach_buffer = []
        self._teach_topic = ""
        self._teach_asked = []
        self._teach_pending_save = ""
        self.r.delete("euri:teach:snapshot")

    def _handle_teach_confirm(self, text: str):
        """Gestisce la conferma dopo il read-back del riassunto."""
        self.memory.log_conversation("Stefano", text)
        if self._TEACH_CONFIRM_YES.search(text):
            self.memory.save_memory(self._teach_pending_save, category="conoscenza", source="teach")
            self.memory.log_conversation("Euri", f"[Conoscenza salvata — argomento: {self._teach_topic[:60]}]")
            self._speak("Salvato. Ho capito e tenuto tutto a mente.")
            self._reset_teach()
        elif self._TEACH_CONFIRM_NO.search(text):
            self._teach_confirm_mode = False
            self._teach_mode = True
            self._speak("Dimmi cosa devo correggere, ti ascolto.")
        else:
            self._speak("Salvo così com'è, oppure c'è qualcosa da correggere?")

    def _handle_teach_continue(self, text: str):
        """Gestisce ogni utterance durante la modalità insegnamento."""
        self.memory.log_conversation("Stefano", text)

        # Web search richiesta dentro TEACH — gestiscila senza uscire dalla sessione
        web_intent, _ = classify(text)
        if web_intent == Intent.WEB_SEARCH:
            self._handle_web_search(text)
            return

        # Aggiungi sempre l'utterance al buffer prima di decidere
        self._teach_buffer.append(text)

        if TEACH_END_SIGNALS.search(text):
            self._teach_mode = False
            # Buffer ha solo il trigger iniziale, nessun contenuto reale
            if len(self._teach_buffer) <= 1:
                self._speak("Ok, non ho salvato niente.")
                self._reset_teach()
                return
            accumulated = "\n".join(self._teach_buffer)
            summary = self.brain.summarize_knowledge(accumulated)
            self._teach_pending_save = summary
            self._teach_confirm_mode = True
            # Read-back: legge i primi 220 caratteri del riassunto e chiede conferma
            brief = summary if len(summary) <= 220 else summary[:220].rsplit(" ", 1)[0] + "…"
            self._speak(f"Ho capito questo: {brief}. È corretto?")
            return

        # Snapshot progressivo ogni 5 utterance (protezione crash)
        if len(self._teach_buffer) % 5 == 0:
            snapshot = "\n".join(self._teach_buffer)
            self.r.set("euri:teach:snapshot", snapshot, ex=3600)

        accumulated = "\n".join(self._teach_buffer)
        probe = self.brain.probe_question(self._teach_topic, accumulated, self._teach_asked)
        self._teach_asked.append(probe)
        self.memory.log_conversation("Euri", probe)
        self._speak(probe)

    def _handle_enroll_voice(self, text: str):
        """Avvia la raccolta utterance per il voiceprint."""
        self._enroll_mode = True
        self._enroll_segments = []
        self.speaker_auth.disable()  # disabilita verifica durante enrollment
        self._speak(f"Registrazione voce. Dimmi {ENROLL_UTTERANCES} frasi qualsiasi.")

    def _handle_teach_recovery(self, text: str):
        """Gestisce la risposta alla domanda di ripristino sessione TEACH."""
        self._teach_recovery_mode = False
        if self._TEACH_CONFIRM_YES.search(text):
            lines = [l for l in self._teach_snapshot_content.split("\n") if l.strip()]
            self._teach_buffer = lines
            self._teach_topic = lines[0] if lines else "argomento precedente"
            self._teach_mode = True
            self._teach_confirm_mode = False
            self._teach_asked = []
            self._teach_pending_save = ""
            self._teach_snapshot_content = ""
            logger.info(f"TEACH recovery: ripristinati {len(lines)} utterance")
            self._speak("Riprendiamo. Dimmi pure, ti ascolto.")
        else:
            self.r.delete("euri:teach:snapshot")
            self._teach_snapshot_content = ""
            self._speak("Ok, ho cancellato la sessione precedente.")

    def _dispatch(self, text: str, detected_lang: str = "it"):
        """Smista il testo trascritto all'handler corretto."""
        if self.memory.is_silent_mode():
            # Anche in silenzio: ripristino e shutdown passano sempre
            _intent_check, _ = classify(text)
            if _intent_check not in (Intent.RESTORE_ALERTS, Intent.SHUTDOWN):
                logger.debug("Modalità silenziosa attiva — input ignorato")
                return

        # TEACH recovery: attende sì/no dopo domanda di ripristino sessione
        if self._teach_recovery_mode:
            self._handle_teach_recovery(text)
            return


        # Todo pending: attende conferma/dettagli/annullamento (timeout 60s)
        if self._pending_todo:
            if self._pending_todo.expired():
                self._pending_todo = None
                logger.debug("Todo pending scaduto (timeout 60s)")
            else:
                self._handle_pending_todo(text)
                return

        # Write pending: attende conferma/dettagli/annullamento (timeout 120s)
        if self._pending_write:
            if self._pending_write.expired():
                self._pending_write = None
                logger.debug("Write pending scaduto (timeout 120s)")
            else:
                self._handle_pending_write(text)
                return

        # Audit memory: attende sì/no per cancellare le memorie rumore
        if self._audit_confirm_mode:
            self._handle_audit_confirm(text)
            return

        # Modalità conversazione bidirezionale IT↔EN
        if self._translate_bidir:
            self._handle_translate_bidir(text, detected_lang)
            return

        # Dictation mode: accumula frasi fino al comando di chiusura
        if self._dictation_mode:
            self._handle_dictation_continue(text)
            return


        # Teach confirm mode: attende conferma prima di salvare
        if self._teach_confirm_mode:
            self._handle_teach_confirm(text)
            return

        # Teach mode: intercetta prima della classificazione normale
        if self._teach_mode:
            self._handle_teach_continue(text)
            return

        # "Scrivilo / salvalo" dopo una risposta lunga → usa il contenuto dell'ultima risposta (TTL 300s)
        if (self._last_speech_content
                and time.time() - self._last_speech_ts < 300
                and self._SAVE_REPLY_RE.search(text)):
            from agent.tools.text_writer import tool_write_text
            self.memory.log_conversation("Stefano", text)
            res = tool_write_text({"text": self._last_speech_content})
            if res.success:
                fname = res.raw_data.get("filepath", "file").split("/")[-1]
                reply = f"Salvato in {fname}."
            else:
                reply = "Errore nel salvataggio."
            self._last_speech_content = ""
            self._last_speech_ts = 0.0
            self.memory.log_conversation("Euri", reply)
            self._speak(reply)
            return

        _t_classify = time.perf_counter()
        intent, _ = classify(text)
        logger.info(f"[TIMING] Intent regex: {(time.perf_counter()-_t_classify)*1000:.0f}ms → {intent.value}")

        # Fallback LLM per intent critici non catturati dalle regex
        if intent == Intent.CHAT:
            from core.llm_classifier import llm_fallback_classify
            _t_llm_cls = time.perf_counter()
            fallback = llm_fallback_classify(text)
            logger.info(f"[TIMING] LLM classifier: {(time.perf_counter()-_t_llm_cls)*1000:.0f}ms → {fallback or 'CHAT'}")
            if fallback:
                intent = Intent(fallback)

        logger.info(f"Intent: {intent.value} — '{text}'")

        handlers = {
            Intent.SAVE_MEMORY: self._handle_save_memory,
            Intent.SAVE_TODO: self._handle_save_todo,
            Intent.SAVE_NOTE: self._handle_save_note,
            Intent.SAVE_LAST: self._handle_save_last,
            Intent.TRANSLATE: self._handle_translate,
            Intent.WEB_SEARCH: self._handle_web_search,
            Intent.SEARCH: self._handle_search,
            Intent.LIST_TODAY: self._handle_list_today,
            Intent.COMPLETE: self._handle_complete,
            Intent.SILENCE_MODE: self._handle_silence_mode,
            Intent.RESTORE_ALERTS: self._handle_restore_alerts,
            Intent.STATUS: self._handle_status,
            Intent.EXECUTE: self._handle_execute,
            Intent.DICTATION: self._handle_dictation_start,
            Intent.TEACH: self._handle_teach,
            Intent.ENROLL_VOICE: self._handle_enroll_voice,
            Intent.AUDIT_MEMORY: self._handle_audit_memory,
            Intent.SHUTDOWN: self._handle_shutdown,
            Intent.CHAT: self._handle_chat,
        }

        handler = handlers.get(intent, self._handle_chat)
        _t_handler = time.perf_counter()
        handler(text)
        logger.info(f"[TIMING] Handler {intent.value}: {(time.perf_counter()-_t_handler)*1000:.0f}ms")

    def _passive_learner_loop(self):
        """
        Worker silenzioso: dopo 45s di idle analizza la conversazione recente
        ed estrae fatti su Stefano da salvare passivamente in Redis (source=passive).
        Non parla, non interrompe, non notifica. Solo logger.info se salva qualcosa.

        Versione aggressiva: analizza TUTTA la history recente (non solo trusted/wake-word).
        Il validator LLM e il dedup semantico filtrano la spazzatura.
        """
        from core.validator import validate_payload
        IDLE_TRIGGER = 45       # secondi di silenzio prima di analizzare (era 60)
        MIN_NEW_EXCHANGES = 1   # scambi minimi dall'ultima analisi (era 2)

        while self._running:
            time.sleep(20)
            try:
                # Solo se c'è stata attività recente che ora è ferma
                if self._last_activity_ts == 0:
                    continue
                idle = time.monotonic() - self._last_activity_ts
                if idle < IDLE_TRIGGER:
                    continue

                # Abbastanza nuovi scambi da analizzare? — snapshot atomico sotto history_lock
                with self.brain.history_lock:
                    history = list(self.brain._conversation_history)
                new_exchanges = len(history) - self._passive_history_len
                if new_exchanges < MIN_NEW_EXCHANGES:
                    continue

                # Analizza tutto il segmento nuovo (rimosso filtro trusted)
                new_history = history[self._passive_history_len:]
                self._passive_history_len = len(history)

                if not new_history:
                    continue

                facts = self.brain.extract_passive_memories(new_history)
                if not facts:
                    continue

                saved = 0
                for fact in facts:
                    clean = validate_payload(fact, "memory")
                    if not clean:
                        continue
                    if self.memory.is_duplicate_memory(clean, llm_probe_fn=self.brain.probe_same_meaning):
                        continue
                    self.memory.save_memory(clean, category="passivo", source="passive")
                    saved += 1

                if saved:
                    logger.info(f"Passive learner: {saved} fatto/i salvato/i silenziosamente")

            except Exception as e:
                logger.error(f"Errore passive learner: {e}")

    def _consolidation_loop(self):
        """
        Loop 2a — consolidamento silenzioso in idle.
        Ogni 60s, se idle >30s e almeno MIN_NEW_MEMORIES nuove memorie dall'ultima run,
        genera una reflection e la salva in Redis con TTL 7 giorni (source=reflection).
        Non parla, non interrompe. Solo logger.info se salva.
        """
        from datetime import timedelta
        from utils.date_utils import now as _now

        IDLE_TRIGGER = 30         # secondi di silenzio prima di agire
        CHECK_INTERVAL = 60       # polling
        MIN_RUN_INTERVAL = 1800   # almeno 30 min tra una run e la successiva
        RECENT_WINDOW = 4 * 3600  # considera memorie delle ultime 4 ore
        MIN_MEMORIES = 3
        EXCLUDE_SOURCES = {"campus", "web", "reflection"}

        while self._running:
            time.sleep(CHECK_INTERVAL)
            try:
                if self._last_activity_ts == 0:
                    continue
                idle = time.monotonic() - self._last_activity_ts
                if idle < IDLE_TRIGGER:
                    continue
                if (time.monotonic() - self._consolidation_last_run) < MIN_RUN_INTERVAL:
                    continue

                # Raccogli memorie recenti (ultime 4h, escluso campus/web/reflection)
                import time as _time
                cutoff = _time.time() - RECENT_WINDOW
                all_memories = []
                cursor = 0
                while True:
                    cursor, keys = self.r.scan(cursor, match="euri:memory:*", count=100)
                    for key in keys:
                        try:
                            val = self.r.json().get(key)
                            if val:
                                all_memories.append(val)
                        except Exception:
                            pass
                    if cursor == 0:
                        break

                session_mems = [
                    m for m in all_memories
                    if float(m.get("created_at") or 0) >= cutoff
                    and m.get("source") not in EXCLUDE_SOURCES
                ]
                if len(session_mems) < MIN_MEMORIES:
                    self._consolidation_last_run = time.monotonic()
                    continue

                # Memorie correlate via ricerca semantica sul testo della sessione
                session_text = " ".join(
                    (m.get("content") or "")[:60] for m in session_mems[:4]
                )
                session_ids = {m.get("id") for m in session_mems}
                related = [
                    m for m in self.memory.search_memories(session_text, limit=7)
                    if m.get("id") not in session_ids and m.get("source") != "web"
                ][:5]

                reflection = self.brain.generate_reflection(session_mems, related)
                if not reflection:
                    self._consolidation_last_run = time.monotonic()
                    continue

                expires = _now() + timedelta(days=7)
                self.memory.save_memory(
                    reflection,
                    category="riflessione",
                    source="reflection",
                    expires_at=expires,
                )
                logger.info("Loop 2a: reflection salvata silenziosamente")
                self._consolidation_last_run = time.monotonic()

                # Cleanup memorie scadute (safety net per memorie mai richiamate)
                try:
                    expired = self.memory.get_expiring_memories(days_ahead=0)
                    for mem in expired:
                        self.r.delete(f"euri:memory:{mem['id']}")
                    if expired:
                        logger.info(f"Loop 2a: {len(expired)} memorie scadute rimosse")
                except Exception as cleanup_err:
                    logger.debug(f"Loop 2a cleanup error: {cleanup_err}")

            except Exception as e:
                logger.error(f"Errore consolidation loop: {e}")

    def _reminder_loop(self):
        """
        Thread proattivo: ogni 30s controlla i todo con scadenza e parla con escalation.

        Livelli:
          L1 — 60 min prima  (1 avviso)
          L2 — 30 min prima  (1 avviso)
          L3 — 15 min prima  (1 avviso)
          L4 — scadenza      (1 avviso)
          L5 — scaduto       (1 avviso, solo se scaduto da meno di 2 ore)

        Condizioni per parlare:
          - Non in quiet hours (23:00–07:00)
          - VisualGate: utente presente
          - Nessun audio lock attivo
        """
        # Finestre in minuti: (min_left_low, min_left_high, eligible_fn, message_fn)
        # eligible_fn(reminded_count, minutes_since_last_remind) → bool
        # L4/L5 usano also il tempo dall'ultimo remind per evitare ripetizioni
        _LEVELS = [
            ( 55,  65, lambda c, dt: c == 0,          lambda t: f"Tra un'ora: {t['content']}."),
            ( 25,  35, lambda c, dt: c <= 1,           lambda t: f"Fra mezz'ora: {t['content']}."),
            ( 10,  20, lambda c, dt: c <= 2,           lambda t: f"Fra un quarto d'ora: {t['content']}."),
            ( -3,   2, lambda c, dt: dt > 55,          lambda t: f"Adesso: {t['content']}."),
            (-120,  -3, lambda c, dt: c <= 4 and dt > 55, lambda t: f"Scaduto: {t['content']}. L'hai fatto?"),
        ]

        while self._running:
            try:
                t = now()
                hour = t.hour

                # Quiet hours 23:00–07:00
                if hour >= 23 or hour < 7:
                    time.sleep(30)
                    continue

                user_present = self.visual_gate.is_user_present()

                # Euri sta parlando → aspetta
                if self.r.exists("euri:audio:lock"):
                    time.sleep(5)
                    continue

                todos = self.memory.get_pending_todos()
                for todo in todos:
                    due = todo.get("_due_at")
                    if not due:
                        continue

                    minutes_left = (due - t).total_seconds() / 60
                    reminded = todo.get("reminded_count", 0)

                    # Minuti dall'ultimo remind (inf se mai ricordato)
                    last_reminded_ts = todo.get("last_reminded_at")
                    if last_reminded_ts:
                        from utils.date_utils import from_timestamp
                        last_reminded_dt = from_timestamp(last_reminded_ts)
                        dt_since = (t - last_reminded_dt).total_seconds() / 60
                    else:
                        dt_since = float("inf")

                    for low, high, eligible_fn, msg_fn in _LEVELS:
                        if low <= minutes_left <= high and eligible_fn(reminded, dt_since):
                            self.memory.mark_reminded(todo["id"])
                            if user_present:
                                msg = msg_fn(todo)
                                logger.info(f"Reminder vocale: {todo['content'][:50]}")
                                self._speak(msg)
                            else:
                                # Utente assente: beep + accoda per annuncio al rientro
                                logger.info(f"Reminder beep (assente): {todo['content'][:50]}")
                                self._play_beep()
                                self._missed_reminders.append(todo["content"])
                            break

            except Exception as e:
                logger.error(f"Errore reminder loop: {e}")

            time.sleep(30)

    def run(self):
        self._running = True

        # Intercetta Ctrl+C
        def _shutdown(sig, frame):
            logger.info("Shutdown segnalato...")
            self._running = False
            self.visual_gate.stop()
            if hasattr(self, 'dream_engine'):
                self.dream_engine.stop()
            if hasattr(self, 'obsidian_sync'):
                self.obsidian_sync.stop_watcher()
            # Sblocca eventuale sd.wait() ancora appeso in background
            # per evitare segfault durante il teardown di PortAudio
            try:
                import sounddevice as _sd
                _sd.stop()
            except Exception:
                pass

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # Thread proattivo reminder
        t = threading.Thread(target=self._reminder_loop, daemon=True)
        t.start()

        # Thread passive learner — analizza conversazioni in idle silenziosamente
        t2 = threading.Thread(target=self._passive_learner_loop, daemon=True)
        t2.start()

        # Thread Loop 2a — consolidamento silenzioso (reflection) in idle
        t3 = threading.Thread(target=self._consolidation_loop, daemon=True)
        t3.start()

        # Thread mobile worker — gestisce richieste dalla pagina Streamlit via Redis Stream
        t_mobile = threading.Thread(target=self._mobile_worker, daemon=True)
        t_mobile.start()

        # Avvia Dream Engine (Loop 2b/2c)
        if hasattr(self, 'dream_engine'):
            self.dream_engine.start()
            
        # Avvia Obsidian Watcher (Sync In)
        if hasattr(self, 'obsidian_sync'):
            self.obsidian_sync.start_watcher()

        # Riepilogo mattutino se ci sono cose urgenti
        self._morning_brief_if_needed()

        with AudioCapture() as mic:
            logger.info("── Euri in ascolto ──")
            while self._running:
                # Saluto al rientro: VisualGate ha appena rilevato presenza dopo assenza
                if self.visual_gate.consume_just_activated():
                    if self._first_visual_activation:
                        # Prima rilevazione dall'avvio: silenzio, solo warm-up modello
                        self._first_visual_activation = False
                        threading.Thread(target=self._warmup_model, daemon=True).start()
                    else:
                        if self._missed_reminders:
                            missed = self._missed_reminders.copy()
                            self._missed_reminders.clear()
                            if len(missed) == 1:
                                self._speak(f"Bentornato Stefano. Hai perso un promemoria: {missed[0]}.")
                            else:
                                elenco = ", ".join(missed)
                                self._speak(f"Bentornato Stefano. Hai perso {len(missed)} promemoria: {elenco}.")
                        else:
                            self._speak("Bentornato Stefano.")
                        # Controlla se c'è una sessione TEACH interrotta
                        snapshot = self.r.get("euri:teach:snapshot")
                        if snapshot and not self._teach_mode:
                            self._teach_snapshot_content = snapshot
                            self._teach_recovery_mode = True
                            self._speak("Ho trovato una sessione di insegnamento non completata. Vuoi riprendere da dove eravamo?")

                # Salta se proactive_agent sta parlando
                if self.r.exists("euri:audio:lock"):
                    continue

                chunk = mic.read_chunk()
                speech_ended, segment = self.vad.process_chunk(chunk)

                if not speech_ended:
                    continue

                # Gate mobile: silenzio se la pagina Voce Mobile sta processando
                if self.r.exists("euri:mobile:active"):
                    logger.debug("Mobile gate: voce ignorata — sessione mobile attiva")
                    self.vad.reset()
                    continue

                # Gate visivo: ignora voce se nessuno è presente
                if not self.visual_gate.is_user_present():
                    logger.debug("VisualGate: voce rilevata ma gate INACTIVE — ignorata")
                    self.vad.reset()
                    continue

                # Enrollment mode: raccoglie utterance per il voiceprint
                if self._enroll_mode:
                    self._enroll_segments.append(segment)
                    remaining = ENROLL_UTTERANCES - len(self._enroll_segments)
                    if remaining > 0:
                        self._speak(f"Ancora {remaining}.")
                    else:
                        self._enroll_mode = False
                        ok = self.speaker_auth.enroll_from_segments(self._enroll_segments)
                        self._enroll_segments = []
                        if ok:
                            self._speak("Registrazione completata. Ti riconosco adesso.")
                        else:
                            self._speak("Registrazione fallita. Riprova.")
                    self.vad.reset()
                    continue

                # Verifica identità vocale (prima di STT per risparmiare CPU)
                # In modalità interprete le voci esterne sono attese — skip auth
                if not self._translate_bidir and not self.speaker_auth.verify(segment):
                    logger.info("SpeakerAuth: voce non riconosciuta — comando ignorato")
                    self.vad.reset()
                    continue

                # Trascrizione
                force_lang = None if self._translate_bidir else "it"
                _t_stt = time.perf_counter()
                text, detected_lang = self.stt.transcribe(segment, force_lang=force_lang)
                logger.info(f"[TIMING] STT: {(time.perf_counter()-_t_stt)*1000:.0f}ms")
                self.visual_gate.notify_activity()
                if hasattr(self, 'dream_engine'):
                    self.dream_engine.notify_activity()
                self._last_activity_ts = time.monotonic()
                self.vad.reset()

                if not text:
                    continue

                # Filtro garbage STT: Whisper a volte alucina caratteri ripetuti su rumore di fondo.
                # Se >60% delle parole sono identiche (es. "č č č č...") scartiamo senza LLM.
                _words = text.split()
                if len(_words) >= 6:
                    _most_common_word_ratio = max(
                        _words.count(w) for w in set(_words)
                    ) / len(_words)
                    if _most_common_word_ratio > 0.60:
                        logger.debug(f"Garbage STT scartato (ratio={_most_common_word_ratio:.2f}): '{text[:40]}'")
                        continue

                # Correzione misrecognition comuni Whisper (IT) — skip in modalità traduzione/interprete
                if not self._translate_bidir:
                    _text_lower = text.lower()
                    for wrong, right in _STT_CORRECTIONS.items():
                        if wrong in _text_lower:
                            _text_lower = _text_lower.replace(wrong, right)
                            text = _text_lower
                            break

                # Wake word guard: in ambienti rumorosi risponde solo se:
                # - sente "Euri" nel testo, oppure
                # - è dentro una conversazione attiva (< CONVERSATION_WINDOW_SEC dall'ultima risposta)
                # Eccezione: modalità traduzione/interprete (parla anche l'interlocutore)
                has_wake_word = bool(_WAKE_WORD_RE.search(text))
                if not (self._translate_bidir or self._dictation_mode):
                    since_last = time.monotonic() - self._last_activity_ts
                    in_conversation = since_last < _CONVERSATION_WINDOW_SEC
                    if not has_wake_word and not in_conversation:
                        logger.debug(f"Wake word assente e fuori finestra ({since_last:.0f}s) — ignorato: '{text[:40]}'")
                        continue

                # Trusted = wake word esplicito. Scambi ambient (solo finestra) non analizzati dal passive learner.
                self.brain._next_trusted = has_wake_word

                # Dispatch
                self._dispatch(text, detected_lang=detected_lang)

        logger.info("Euri spento.")

    def _mobile_worker(self):
        """
        Thread che gestisce richieste vocali dalla pagina mobile.
        Legge audio da euri:mobile:in (Redis Stream), usa gli stessi STT/Brain/TTS
        del daemon principale, risponde su euri:mobile:out.
        La conversazione è condivisa — brain_lock garantisce history coerente.
        """
        import base64
        from math import gcd
        from scipy.signal import resample_poly

        STREAM_IN  = "euri:mobile:in"
        STREAM_OUT = "euri:mobile:out"
        last_id    = "$"   # solo messaggi nuovi dall'avvio

        def _d(v):
            return v.decode() if isinstance(v, bytes) else (v or "")

        logger.info("Mobile worker: in ascolto su euri:mobile:in")

        while self._running:
            try:
                msgs = self.r.xread({STREAM_IN: last_id}, count=1, block=3000)
                if not msgs:
                    continue

                for _, messages in msgs:
                    for msg_id, data in messages:
                        last_id   = _d(msg_id)
                        req_id    = _d(data.get("request_id", b"?"))
                        audio_b64 = _d(data.get("audio_b64", b""))
                        sr        = int(_d(data.get("sr", b"48000")))

                        if not audio_b64:
                            continue

                        # float32 bytes → numpy
                        audio_raw = np.frombuffer(base64.b64decode(audio_b64), dtype=np.float32)

                        # Resample → 16kHz
                        if sr != 16000:
                            g         = gcd(16000, sr)
                            audio_16k = resample_poly(audio_raw, 16000 // g, sr // g).astype(np.float32)
                        else:
                            audio_16k = audio_raw

                        # STT (modello condiviso col main loop — già thread-safe in faster-whisper)
                        _t = time.perf_counter()
                        text, _ = self.stt.transcribe(audio_16k, force_lang="it")
                        logger.info(f"[Mobile] STT {(time.perf_counter()-_t)*1000:.0f}ms → '{text[:70]}'")

                        if not text.strip():
                            self.r.xadd(STREAM_OUT, {
                                "request_id": req_id, "text": "", "response": "",
                                "audio_b64": "", "sample_rate": "22050",
                            }, maxlen=20)
                            continue

                        self._last_activity_ts = time.monotonic()
                        self.memory.log_conversation("Stefano", text)

                        context = self._build_context(text)
                        context = (context + "\n\n" if context else "") + \
                            "[Messaggio da interfaccia mobile — Stefano è lontano dalla workstation. " \
                            "Rispondi in modo conciso e TTS-friendly, niente markdown.]"

                        # Brain — lock condiviso con _handle_chat()
                        with self._brain_lock:
                            response = self.brain.respond(text, context=context)

                        self.memory.log_conversation("Euri", response)
                        logger.info(f"[Mobile] Euri: {response[:80]}")

                        # TTS — lock per sicurezza sui modelli ONNX
                        with self._tts_lock:
                            samples, sample_rate = self.tts.synthesize(response)

                        samples_i16   = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
                        audio_b64_out = base64.b64encode(samples_i16.tobytes()).decode()

                        self.r.xadd(STREAM_OUT, {
                            "request_id": req_id,
                            "text":        text,
                            "response":    response,
                            "audio_b64":   audio_b64_out,
                            "sample_rate": str(sample_rate),
                        }, maxlen=20)

            except Exception as e:
                logger.error(f"Mobile worker: {e}")
                time.sleep(1)

    def _morning_brief_if_needed(self):
        """Riepilogo mattutino alla prima interazione del giorno."""
        last_boot_key = "euri:meta:last_boot"
        today = now().strftime("%Y-%m-%d")
        last_boot = self.r.get(last_boot_key)

        if last_boot == today:
            return

        self.r.set(last_boot_key, today)

        todos = self.memory.get_todos_today()
        overdue = self.memory.get_overdue_todos()

        if todos or overdue:
            brief = self.brain.format_today_summary(todos, overdue)
            self._speak(f"Buongiorno Stefano. {brief}")


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)

    daemon = VoiceDaemon()
    daemon.setup()
    daemon.run()
