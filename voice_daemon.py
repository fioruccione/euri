"""
voice_daemon.py — Loop principale always-on vocale di Euri.

Flusso:
  microfono → VAD → STT → intent_router → [branch] → brain → TTS → speaker
"""
import json
import os
import re
import shutil
import sys
import signal
import threading
import time
import uuid
from dataclasses import replace as dataclass_replace
import numpy as np
from loguru import logger
import config

import redis as redis_lib

from utils.redis_client import get_client, init_indexes, backfill_embeddings
from utils.date_utils import now, format_datetime

from voice.audio_io import AudioCapture, play_audio
from voice.vad import VAD
from voice.stt import STT
from voice.tts import TTS, split_for_speech
from voice.tts_pipeline import run_tts_pipeline
from voice.visual_gate import VisualGate
from voice.face_auth import FaceAuth
from voice.speaker_auth import SpeakerAuth, SpeakerVerdict, ENROLL_UTTERANCES
from voice.social_perception import SocialSnapshot, build_social_perception

from core.pulse import cognitive_emit, pulse_emit
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
from core.honesty import scrub_unbacked_save_claim
from core.act_word_check import (
    emit_unbacked_action_commitment,
    needs_honest_correction,
    scrub_unbacked_action_claim,
    strip_leading_stage_direction,
    unbacked_action_claim_details,
)
from core.action_controller import (
    ActionController,
    ActionDisposition,
    ActionEffect,
    build_capability_snapshot,
    looks_actionable,
)
from core.ollama_client import chat_client
from core.guest_claims import (
    GuestClaimStore,
    extract_guest_claim,
    respond_to_guest,
)
from core.cognitive_present import (
    CognitivePresent,
    EpistemicStatus,
    InteractionChannel,
    InteractionPhase,
)
from core.voice_perception import (
    VoicePerceptionRecorder,
    voice_perception_answer,
    with_voice_perception_context,
)
from core.worker_supervisor import WorkerSupervisor
from core.semantic_turn import (
    SemanticTurnService,
    filter_passive_memory_history,
    frame_bootstraps_owner_session,
    frame_is_correction,
    frame_requests_linguistic_response,
    frame_requests_contextual_action,
    frame_vetoes_contextual_action,
    gate_teaching_route,
    semantic_intent,
    trusted_deliberation_request,
    trusted_teaching_session,
)
from core.ideation_activation import (
    active_key as ideation_active_key,
    delivery_key as ideation_delivery_key,
    enqueue_delivery as enqueue_ideation_delivery,
    format_result as format_ideation_result,
    load_json as load_ideation_json,
    pending_key as ideation_pending_key,
    peek_delivery as peek_ideation_delivery,
    job_queue_key as ideation_job_queue_key,
    pop_job as pop_ideation_job,
    ui_stream_key as ideation_ui_stream_key,
    semantic_pending_decision,
    store_json as store_ideation_json,
)
from agent.executor import Executor, ToolCall, build_injected_context


_OWNER_ID = config.OWNER_ACTOR_ID
_OWNER_NAME = config.OWNER_DISPLAY_NAME
_ASSISTANT_NAME = config.ASSISTANT_DISPLAY_NAME
_REACTION_ACK = (
    "Ricevuto. Registro la tua risposta e aggiorno lo stato della connessione "
    "in base a ciò che hai detto."
)


def _should_try_contextual_action(intent: Intent, candidate: bool) -> bool:
    """Il controller precede i legacy handler anche quando regex dice EXECUTE."""
    return intent in {Intent.COMPLETE, Intent.RESCHEDULE} or (
        intent in {Intent.CHAT, Intent.EXECUTE} and candidate
    )


# ──────────────────────────────────────────
# Interrupt vocale durante playback
# ──────────────────────────────────────────
_INTERRUPT_KEYWORDS = frozenset({"stop", "fermati", "basta", "taci", "silenzio"})

# Le azioni implicite devono essere impegni operativi del turno, non descrizioni
# metacognitive come "quello che leggo nei log e come lo interpreto". L'ancoraggio
# a inizio frase conserva "Controllo il log" / "Ora leggo il log" e scarta il
# verbo presente quando e' subordinato a un ragionamento piu' ampio.
_IMPLICIT_READ_LOG_RE = re.compile(
    r'(?:^|(?<=[.!?])\s+)'
    r'(?:(?:ora|adesso|intanto)\s+)?'
    r'(?:leggo|guardo|controllo)\b[^.\n]{0,20}\b(?:il\s+)?log\b',
    re.IGNORECASE,
)

# ──────────────────────────────────────────
# Correzioni misrecognition STT (Whisper IT)
# ──────────────────────────────────────────
_WAKE_WORD_RE = re.compile(r'\beuri\b', re.IGNORECASE)
_CONVERSATION_WINDOW_SEC = int(
    getattr(config, "CONVERSATION_LEASE_SECONDS", 45)
)  # secondi di conversazione diretta senza wake word

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
if not os.environ.get("EURI_TEST_TIER"):
    logger.add(
        "logs/voice_daemon.log",
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
        enqueue=True,
    )


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
        from core.conversation_turns import ConversationTurnStore
        self.turn_store = ConversationTurnStore(self.r)
        from core.personality_model import PersonalityModel
        self.personality_model = PersonalityModel(self.r)
        self.guest_claims = GuestClaimStore(self.r)
        self.brain = Brain()
        self.brain._personality_context_callback = self.personality_model.render_context
        self.semantic_turns = SemanticTurnService(self.r)
        Brain._shared_instance = self.brain  # Condivisa col CodeRunner
        self.brain._turn_callback = self.turn_store.persist
        self.brain._episode_callback = lambda summary, temporal_context: self.memory.save_memory(
            summary,
            category="episodio", source="episode",
            memory_kind="conversation_episode", temporal_context=temporal_context,
            memory_scope=temporal_context.get("memory_scope"),
        )
        self.executor = Executor()
        self.executor.brain  = self.brain
        self.executor.memory = self.memory
        self.executor.operation_channel = "voice"
        from core.document_workspace import DocumentWorkspace
        self.document_workspace = DocumentWorkspace(self.r)
        self.executor.document_workspace = self.document_workspace
        self.action_controller = ActionController()
        self.vad = VAD()
        self.stt = STT()
        self.tts = TTS()
        self.face_auth = FaceAuth()
        self.visual_gate = VisualGate(face_auth=self.face_auth)
        self.speaker_auth = SpeakerAuth()
        self._enroll_mode = False
        self._enroll_segments: list = []
        self._running = False
        self._workers = WorkerSupervisor()
        self._stop_event = self._workers.stop_event
        self._shutdown_lock = threading.Lock()
        self._shutdown_done = False
        self._missed_reminders: list[str] = []  # promemoria scattati mentre eri assente
        self._clock_emitted: set = set()         # fallback in-memory se Redis è giù; primario = set Redis (sopravvive ai restart)
        self._teach_recovery_mode = False        # in attesa di risposta su recovery TEACH
        self._teach_snapshot_content = ""
        self._teach_mode = False
        self._teach_confirm_mode = False
        self._teach_buffer: list[str] = []
        self._teach_topic = ""
        self._teach_asked: list[str] = []
        self._teach_pending_save = ""
        self._teach_contract: dict = {}
        self._web_pending: dict = {}  # contesto ultima ricerca web (per "approfondisci" / "salva")
        self._pending_todo: _PendingState | None = None   # todo in attesa di conferma (timeout 60s)
        self._pending_reschedule: _PendingState | None = None  # impegno da spostare, manca la data (timeout 120s)
        self._pending_action: _PendingState | None = None  # proposta ad alto impatto in attesa owner
        self._pending_readback: _PendingState | None = None   # memoria riletta, attesa correzione/aggiunta (180s)
        self._pending_write: _PendingState | None = None  # richiesta scrittura file in attesa (timeout 120s)
        self._awaiting_reaction: _PendingState | None = None  # insight su cui Euri ha chiesto conferma, in attesa della reazione di Stefano (timeout 300s)
        self._awaiting_memory_verification: _PendingState | None = None
        self._pending_guest_review: _PendingState | None = None  # claim ospite chiesto esplicitamente a Stefano
        self._ideation_thread: threading.Thread | None = None
        self._guest_review_cooldown_until: float = 0.0
        self._last_created_file: str | None = None  # ultimo file creato da Euri (per "aprilo")
        self._last_created_file_ts: float = 0.0  # quando — recency per disambiguare "apri il documento"
        self._last_speech_content: str = ""      # ultima risposta lunga di Euri (per "scrivilo")
        self._last_speech_ts: float = 0.0        # timestamp di _last_speech_content (TTL 300s)
        self._last_user_text: str = ""           # ultimo prompt utente (Audit di Coerenza)
        self._translate_bidir = False       # modalità interprete bidirezionale IT↔EN
        self._dictation_mode = False
        self._dictation_buffer: list[str] = []
        self._audit_confirm_mode = False       # Euri ha fatto l'audit, attende sì/no per cancellare
        self._audit_rumore: list[dict] = []    # memorie segnate come rumore dall'ultimo audit
        self._last_activity_ts: float = 0.0   # timestamp ultima attività vocale (per passive learner)
        self._last_auth_voice_ts: float = 0.0  # ultima voce AUTENTICATA e accettata dal guard — prova
                                               # d'identità per l'efferente quando la faccia non basta.
                                               # NON aggiornato dal TTS di Euri (niente auto-rinfresco).
        self._passive_last_seq: int = 0        # sequence ID journal già analizzato
        self._consolidation_last_run: float = 0.0  # timestamp ultimo Loop 2a
        self._consolidation_boot_ts: float = time.time()
        self._brain_lock = threading.Lock()  # protegge brain tra main loop e mobile worker
        self._first_visual_activation = True  # True finché il VisualGate non vede l'utente per la prima volta
        self._tts_lock   = threading.Lock()  # protegge il modello TTS da accessi concorrenti
        # Dal primo frame VAD fino alla fine di STT/dispatch. Initiative non deve
        # confondere "testo non ancora disponibile" con un confine di turno libero.
        self._voice_input_inflight = threading.Event()
        self._dream_busy_at_voice_start = False
        self.present = CognitivePresent(
            conversation_window_s=getattr(config, "CONVERSATION_LEASE_SECONDS", 45),
            focus_window_s=getattr(config, "CONVERSATION_FOCUS_SECONDS", 5 * 60),
            max_focus_turns=getattr(config, "CONVERSATION_FOCUS_MAX_TURNS", 4),
        )
        self.voice_perception = VoicePerceptionRecorder(self.r, self.present)
        from core.memory_scope import get_active_scope
        self.turn_store.restore_into(self.brain, get_active_scope(self.r))
        self._restore_pending_continuity(get_active_scope(self.r))
        self._last_social_baseline_ts = 0.0
        self.social_perception = build_social_perception(self._handle_social_snapshot)
        self.visual_gate.set_social_perception(self.social_perception)
        self.visual_gate.set_enrollment_bridge(
            self._read_face_enrollment_request,
            self._write_face_enrollment_status,
        )
        self._initiative_focus_cache: dict[tuple[str, int], str] = {}

        # Impegni verbali → azioni reali: (pattern sulla risposta di Euri, callable(text, reply))
        self._IMPLICIT_ACTIONS = [
            (re.compile(r'\b(controllo|verifico|guardo)\b.{0,30}\b(todo|task|scadenz)', re.IGNORECASE),
             lambda t, r: self._handle_list_today("controlla i todo")),
            # Claim di riprogrammazione non backed (caso Poseidon 13/07: "Impegno
            # aggiornato" detto da CHAT senza tool) → esegue lo spostamento VERO,
            # pescando la data dal testo utente o, in fallback, dal claim stesso.
            (re.compile(r'\bimpegno\s+(aggiornato|spostato|riprogrammato)\b|'
                        r'\bho\s+(spostato|riprogrammato|aggiornato)\b.{0,30}\b(impegno|promemoria|scadenza)',
                        re.IGNORECASE),
             lambda t, r: self._handle_reschedule(t, reply_hint=r)),
            (_IMPLICIT_READ_LOG_RE,
             lambda t, r: self._handle_execute("leggi il log")),
            (re.compile(r'\b(controllo|verifico)\b.{0,20}\b(cpu|ram|disco|spazio)\b', re.IGNORECASE),
             lambda t, r: self._handle_execute("controlla la cpu")),
        ]

    def _persist_pending_continuity(self, kind: str, state: _PendingState) -> None:
        try:
            from core.memory_scope import get_active_scope
            self.turn_store.continuity.set_pending(
                kind,
                state.data,
                get_active_scope(self.r),
                timeout_s=max(1, int(state._timeout)),
            )
        except Exception as exc:
            logger.debug("Continuità pending non persistita ({})", exc)

    def _clear_pending_continuity(self) -> None:
        try:
            from core.memory_scope import get_active_scope
            self.turn_store.continuity.clear_pending(get_active_scope(self.r))
        except Exception as exc:
            logger.debug("Continuità pending non rimossa ({})", exc)

    def _restore_pending_continuity(self, memory_scope: str) -> None:
        """Ri-arma una domanda proattiva pronunciata prima del riavvio."""
        try:
            payload = self.turn_store.continuity.load_pending(memory_scope)
        except Exception as exc:
            logger.debug("Continuità pending non disponibile ({})", exc)
            return
        if not payload:
            return
        data = payload.get("data") or {}
        question = str(data.get("question") or "").strip()
        question_id = str(data.get("question_id") or "").strip()
        remaining = max(1.0, float(payload["expires_at"]) - time.time())
        if not question or not question_id:
            return
        state = _PendingState(data, timeout=remaining)
        if payload.get("kind") == "reaction" and isinstance(data.get("insight"), dict):
            self._awaiting_reaction = state
        elif payload.get("kind") == "memory_verification" and data.get("memory_id"):
            self._awaiting_memory_verification = state
        else:
            return
        self.present.set_pending_question(question_id, question)
        logger.info(
            "Continuità pending: domanda {} ripristinata ({}s residui)",
            payload.get("kind"),
            round(remaining),
        )

    def _read_face_enrollment_request(self) -> dict | None:
        """Canale locale UI->VisualGate; contiene comandi, mai frame o embedding."""
        try:
            raw = self.r.get(
                getattr(config, "FACE_ENROLLMENT_REQUEST_KEY", "euri:face_enrollment:request")
            )
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.debug(f"Face enrollment: richiesta Redis ignorata ({exc})")
            return None

    def _write_face_enrollment_status(self, payload: dict) -> None:
        session_id = str(payload.get("session_id", ""))
        if not session_id:
            return
        self.r.set(
            f"{getattr(config, 'FACE_ENROLLMENT_STATUS_PREFIX', 'euri:face_enrollment:status:')}"
            f"{session_id}",
            json.dumps(payload, ensure_ascii=False),
            ex=getattr(config, "FACE_ENROLLMENT_TTL_S", 300),
        )

    def setup(self):
        logger.info("Inizializzazione Euri...")
        init_indexes(self.r)
        from core.memory_scope import active_scope_state
        scope_state = active_scope_state(self.r)
        if scope_state.get("active"):
            logger.warning(
                "Scope memoria SPERIMENTALE ancora attivo: {} ({})",
                scope_state.get("label"),
                scope_state.get("scope"),
            )
        else:
            logger.info("Scope memoria: personal")
        self.vad.load()
        self.stt.load()
        self.tts.load()
        self.face_auth.load()
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

        # V2.18: ToolRegistry per Fast Path VectorSet (Redis 8.8 nativo)
        # Bootstrap idempotente: se i tool sono già in Redis, register() li
        # aggiorna in-place. Costo: 7 embedding+VADD a ogni boot (~1s).
        if config.TOOL_VECTORSET_ENABLED:
            from core.tool_registry import ToolRegistry, DEFAULT_TOOL_DEFINITIONS
            from core.llm_classifier import set_tool_registry
            tool_reg = ToolRegistry(self.r, self.embedder)
            n = tool_reg.bootstrap_from_definitions(DEFAULT_TOOL_DEFINITIONS)
            logger.info(f"ToolRegistry: {n}/{len(DEFAULT_TOOL_DEFINITIONS)} tool registrati (Fast Path attivo)")
            set_tool_registry(tool_reg)

        # Inizializza Dream Engine
        from core.dream_engine import DreamEngine
        self.dream_engine = DreamEngine(
            self.r,
            self.embedder,
            brain=self.brain,
            memory=self.memory,
            personality_model=self.personality_model,
        )
        
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
        if getattr(config, "CODE_AGENT_ENABLED", False):
            opencode_path = shutil.which(config.CODE_AGENT_OPENCODE_BIN)
            if opencode_path:
                logger.info(
                    "Coding agent: configurato — OpenCode={} model={} "
                    "workspace={} sandbox=bubblewrap-obbligatoria",
                    opencode_path,
                    config.CODE_AGENT_MODEL,
                    config.CODE_AGENT_WORKSPACE_ROOT,
                )
            else:
                logger.warning(
                    "Coding agent: abilitato ma OpenCode non trovato nel PATH ({})",
                    config.CODE_AGENT_OPENCODE_BIN,
                )
        logger.info(
            "Memoria dual-channel: mode={} (archivio turni durevole attivo; "
            "gate q_src>={} margin>={} redundancy<={}; thinking_selettivo={} "
            "budget={})",
            config.RAG_DUAL_CHANNEL_MODE,
            config.RAG_DUAL_SELECTIVE_MIN_QUERY_SOURCE,
            config.RAG_DUAL_SELECTIVE_MIN_MARGIN,
            config.RAG_DUAL_SELECTIVE_MAX_REDUNDANCY,
            config.RAG_DUAL_SELECTIVE_THINKING,
            config.RAG_DUAL_THINKING_NUM_PREDICT,
        )
        personality_projection = self.personality_model.load(_OWNER_ID)
        personality_traits = list(personality_projection.get("traits") or [])
        logger.info(
            "Modello identitario: revisione={} stabili={} candidati={} contestati={} "
            "(proiezione derivata owner-scoped)",
            int(personality_projection.get("revision") or 0),
            sum(1 for item in personality_traits if item.get("status") == "stable"),
            sum(1 for item in personality_traits if item.get("status") == "candidate"),
            sum(1 for item in personality_traits if item.get("status") == "contested"),
        )
        from core.conversation_turns import get_verbatim_lifecycle_pending
        lifecycle_pending = get_verbatim_lifecycle_pending(self.r)
        if lifecycle_pending:
            counts = lifecycle_pending.get("counts") or {}
            logger.warning(
                "Lifecycle verbatim: revisione ancora pendente — {} orfani, "
                "{} riferimenti mancanti, {} malformati; nessuna cancellazione automatica",
                counts.get("orphan_candidates", 0),
                counts.get("missing_source_refs", 0),
                counts.get("malformed_turns", 0),
            )
        from core.memory_utility_shadow import get_memory_utility_review_pending
        utility_pending = get_memory_utility_review_pending(self.r)
        if utility_pending:
            logger.warning(
                "Utilità memoria shadow: revisione ancora pendente — {:.1f} "
                "giorni, {} risposte, {} entità; nessun auto-tuning eseguito",
                float(utility_pending.get("observation_age_days") or 0),
                utility_pending.get("turns_responded", 0),
                utility_pending.get("entities_observed", 0),
            )

    def _handle_social_snapshot(self, snapshot: SocialSnapshot) -> None:
        """Persist Phase-0 numbers and transitions, without changing behavior."""
        payload = snapshot.to_dict()
        try:
            self.r.set(
                "euri:social:latest",
                json.dumps(payload, ensure_ascii=False),
                ex=getattr(config, "SOCIAL_PERCEPTION_LATEST_TTL_S", 30),
            )
            interval = getattr(config, "SOCIAL_PERCEPTION_BASELINE_INTERVAL_S", 60)
            if (snapshot.calibrated and
                    snapshot.observed_at - self._last_social_baseline_ts >= interval):
                self.r.xadd(
                    "euri:social:baseline",
                    {
                        "actor_id": snapshot.actor_id,
                        "metrics": json.dumps(snapshot.metrics, separators=(",", ":")),
                        "baselines": json.dumps(snapshot.baselines, separators=(",", ":")),
                        "states": json.dumps(snapshot.states, separators=(",", ":")),
                        "confidences": json.dumps(snapshot.confidences, separators=(",", ":")),
                        "auxiliary_metrics": json.dumps(
                            snapshot.auxiliary_metrics, separators=(",", ":")
                        ),
                        "ts": f"{snapshot.observed_at:.3f}",
                    },
                    maxlen=20160,  # circa 14 giorni a un punto/minuto
                    approximate=True,
                )
                self._last_social_baseline_ts = snapshot.observed_at
        except Exception as exc:
            logger.debug(f"Percezione sociale: persistenza ignorata ({exc})")

        # Preparato per una fase successiva. Spento in Fase 0 per non cambiare
        # neppure indirettamente la rivalidazione delle decisioni asincrone.
        if getattr(config, "SOCIAL_PERCEPTION_PRESENT_ENABLED", False):
            try:
                self.present.observe(
                    "social.visual",
                    {
                        "actor_id": snapshot.actor_id,
                        "states": snapshot.states,
                        "auxiliary_metrics": snapshot.auxiliary_metrics,
                        "calibrated": snapshot.calibrated,
                    },
                    status=EpistemicStatus.OBSERVED,
                    source="visual_gate",
                    ttl_s=max(3.0, getattr(config, "SOCIAL_PERCEPTION_REFRESH_S", 2.0) * 3),
                )
            except Exception as exc:
                logger.debug(f"Percezione sociale: Cognitive Present ignorato ({exc})")

        for transition in snapshot.transitions:
            item = transition.to_dict()
            item["actor_id"] = snapshot.actor_id
            item["calibrated"] = snapshot.calibrated
            pulse_emit(
                self.r,
                "social",
                "extero",
                "movement_transition",
                payload=item,
                salience=0.2,
            )
            logger.info(
                "Percezione sociale: "
                f"{transition.feature} {transition.previous}->{transition.current} "
                f"({transition.value:.2f}, conf={transition.confidence:.2f})"
            )

    def _record_voice_segment(
        self,
        *,
        trace_id: str,
        started_at: float,
        observed_at: float,
        duration_s: float,
        decision: str,
        speaker_verdict: str = "not_run",
        speaker_evidence: dict | None = None,
        actor_id: str = "unknown",
        stt_state: str = "not_run",
        transcript_chars: int = 0,
        detected_language: str = "",
        has_wake_word: bool = False,
        addressed: bool = False,
        delivered_to: str = "none",
    ) -> None:
        """Chiude una trace percettiva senza conservare audio o testo."""
        recorder = getattr(self, "voice_perception", None)
        if recorder is None:
            return
        evidence = speaker_evidence or {}
        actor_scope = (
            "owner" if actor_id == _OWNER_ID
            else "unknown" if actor_id == "unknown" and speaker_verdict == "not_run"
            else "guest" if actor_id == "unknown"
            else "interpreter" if actor_id == "interpreter"
            else "system"
        )
        try:
            recorder.record({
                "trace_id": trace_id,
                "started_at": started_at,
                "observed_at": observed_at,
                "duration_s": duration_s,
                "speaker_verdict": speaker_verdict,
                "speaker_similarity": evidence.get("similarity"),
                "speaker_threshold": evidence.get("threshold"),
                "speaker_reason": evidence.get("reason", ""),
                "actor_scope": actor_scope,
                "stt_state": stt_state,
                "transcript_chars": transcript_chars,
                "detected_language": detected_language,
                "has_wake_word": has_wake_word,
                "addressed": addressed,
                "decision": decision,
                "delivered_to": delivered_to,
            })
            logger.info(
                "VoiceTrace: {} decision={} speaker={} stt={} delivered={}",
                trace_id,
                decision,
                speaker_verdict,
                stt_state,
                delivered_to,
            )
        except Exception as exc:
            # La consapevolezza e' un senso fail-open: non puo' fermare la voce.
            logger.debug("VoiceTrace non registrata: {}", exc)

    def _mark_voice_input_started(self) -> None:
        """Apre il confine foreground e revoca un eventuale LLM Dream attivo."""
        if self._voice_input_inflight.is_set():
            return
        self._voice_input_inflight.set()
        self._dream_busy_at_voice_start = False
        if self.r.exists("euri:mobile:active"):
            return
        try:
            if not self.visual_gate.is_user_present():
                return
        except Exception:
            return
        dream = getattr(self, "dream_engine", None)
        if dream is not None:
            try:
                self._dream_busy_at_voice_start = bool(dream.notify_activity())
            except Exception as exc:
                logger.debug("Dream foreground notify ignorata: {}", exc)

    def _finish_voice_input(self) -> None:
        self._voice_input_inflight.clear()
        self._dream_busy_at_voice_start = False

    def _acknowledge_dream_preemption(self, actor_id: str) -> bool:
        """Conferma l'ascolto solo quando la voce ha davvero revocato il Dream."""
        was_busy = bool(getattr(self, "_dream_busy_at_voice_start", False))
        self._dream_busy_at_voice_start = False
        if (
            not was_busy
            or actor_id != _OWNER_ID
            or not getattr(config, "DREAM_VOICE_BUSY_ACK_ENABLED", True)
        ):
            return False
        logger.info("Voce foreground: Dream LLM revocato, invio acknowledgment")
        self._speak_simple(
            getattr(
                config,
                "DREAM_VOICE_BUSY_ACK_TEXT",
                "Ti ho sentito. Interrompo un'attivita' in background e ti rispondo.",
            )
        )
        return True

    def _handle_voice_perception_question(
        self,
        text: str,
        *,
        trusted: bool,
        observed_at: float | None,
    ) -> bool:
        """Risponde dal record causale, senza chiedere al modello di reinterpretarlo."""
        reply = voice_perception_answer(text, self.r)
        if not reply:
            return False
        self.memory.log_conversation(_OWNER_NAME, text)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self.brain.record_context_message(
            "user",
            text,
            trusted=trusted,
            observed_at=observed_at,
        )
        self.brain.record_context_message("assistant", reply, trusted=trusted)
        logger.info("VoiceTrace: spiegazione operativa deterministica")
        self._speak(reply)
        return True

    _URL_RE = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)

    def _warmup_model(self):
        """Carica il modello LLM in VRAM senza produrre output vocale."""
        try:
            ollama_chat = chat_client.chat
            ollama_chat(
                model=config.OLLAMA_MODEL,
                messages=[{"role": "user", "content": "ok"}],
                options={
                    "num_predict": 1,
                    "num_ctx": config.CHAT_OLLAMA_NUM_CTX,
                },
                keep_alive=-1,
            )
            logger.info("Warm-up modello completato.")
        except Exception as e:
            logger.warning(f"Warm-up modello fallito: {e}")

    def _clean_for_speech(self, text: str) -> str:
        """Rimuove URL e artefatti non leggibili prima del TTS."""
        return self._URL_RE.sub('', text).strip()

    def _speak(self, text: str, lang: str = "it", *, opens_conversation: bool = True):
        """Sintetizza e riproduce il testo finale con interrupt listener attivo."""
        text = self._clean_for_speech(text)
        if not text:
            return
        self._last_activity_ts = time.time()
        present_started = False
        try:
            self.present.begin_speech(
                channel=InteractionChannel.VOICE,
                opens_conversation=opens_conversation,
            )
            present_started = True
        except Exception as e:
            logger.debug(f"Cognitive Present: begin_speech ignorato: {e}")
        logger.info(f"Euri: {text}")
        self.visual_gate.notify_activity()
        self.r.set("euri:audio:lock", "1", ex=300)
        interrupted = False
        fallback_text = text
        try:
            chunks = (
                split_for_speech(
                    text,
                    max_chars=getattr(config, "TTS_SEGMENT_MAX_CHARS", 360),
                )
                if getattr(config, "TTS_SEGMENTED_ENABLED", True)
                else [text]
            )
            if not chunks:
                return

            stop_event = threading.Event()
            listener = None

            def _on_first_ready(first_ready_s: float):
                nonlocal listener
                logger.info(
                    f"[TIMING] TTS first-ready: {first_ready_s*1000:.0f}ms "
                    f"(chunk 1/{len(chunks)}, {len(chunks[0])}/{len(text)} chars)"
                )
                # Mantiene riconoscibile la metrica storica, specificando che ora
                # misura il tempo critico fino al primo audio e non l'intero testo.
                logger.info(
                    f"[TIMING] TTS synth: {first_ready_s*1000:.0f}ms "
                    f"({len(chunks[0])} first-chunk chars)"
                )
                listener = threading.Thread(
                    target=self._interrupt_listener,
                    args=(stop_event,),
                    daemon=True,
                )
                listener.start()

            def _play_chunk(samples, sample_rate: int, index: int) -> bool:
                return play_audio(
                    samples,
                    sample_rate,
                    stop_event=stop_event,
                    terminate_existing=(index == 0),
                )

            def _mark_played(count: int):
                nonlocal fallback_text
                fallback_text = " ".join(chunks[count:])

            try:
                result = run_tts_pipeline(
                    chunks,
                    synthesize=lambda chunk: self.tts.synthesize(chunk, lang=lang),
                    play=_play_chunk,
                    on_first_ready=_on_first_ready,
                    on_chunk_played=_mark_played,
                )
                interrupted = result.interrupted
            finally:
                stop_event.set()
                if listener is not None:
                    listener.join(timeout=2)
            logger.info(
                f"[TIMING] TTS pipeline: first_ready={result.first_ready_s*1000:.0f}ms "
                f"synth_cpu={result.synth_cpu_s*1000:.0f}ms "
                f"playback={result.playback_s*1000:.0f}ms "
                f"wall={result.wall_s*1000:.0f}ms "
                f"chunks={result.played_chunks}/{result.total_chunks} chars={len(text)} "
                f"interrupted={interrupted}"
            )
        except Exception as e:
            logger.error(f"Audio hardware irrecuperabile, fallback TTS: {e}")
            import subprocess, sys
            try:
                if not fallback_text:
                    return
                if sys.platform == "darwin":
                    voice = "Paola" if lang == "it" else "Samantha"
                    subprocess.run(["say", "-v", voice, fallback_text], timeout=300)
                else:
                    subprocess.run(["spd-say", "-l", lang, fallback_text], timeout=300)
            except Exception as tts_err:
                logger.critical(f"Fallback TTS fallito: {tts_err} — Euri muto")
        finally:
            self.r.delete("euri:audio:lock")
            ended_at = time.time()
            # La durata del playback non consuma la lease e non anticipa Initiative.
            self._last_activity_ts = ended_at
            if present_started:
                try:
                    self.present.finish_speech(at=ended_at)
                except Exception as e:
                    logger.debug(f"Cognitive Present: finish_speech ignorato: {e}")
        if interrupted:
            self._speak_simple("Ok.")

    def _speak_simple(self, text: str, lang: str = "it"):
        """Speak non-interrombibile — usato per acknowledgment post-interruzione."""
        text = self._clean_for_speech(text)
        if not text:
            return
        logger.info(f"Euri: {text}")
        present_started = False
        try:
            self.present.begin_speech(
                channel=InteractionChannel.VOICE,
                opens_conversation=True,
            )
            present_started = True
        except Exception as e:
            logger.debug(f"Cognitive Present: begin_speech simple ignorato: {e}")
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
            ended_at = time.time()
            self._last_activity_ts = ended_at
            if present_started:
                try:
                    self.present.finish_speech(at=ended_at)
                except Exception as e:
                    logger.debug(f"Cognitive Present: finish_speech simple ignorato: {e}")

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
        self._request_shutdown()

    def _wait_or_stop(self, seconds: float) -> bool:
        """True se e' stato richiesto lo stop durante l'attesa."""
        return self._stop_event.wait(seconds)

    def background_health(self) -> dict[str, dict]:
        """Snapshot diagnostico dei worker, incluso il Dream Engine."""
        health = self._workers.health(
            stale_after_s=getattr(config, "WORKER_HEARTBEAT_STALE_SECONDS", 180)
        )
        if hasattr(self, "dream_engine"):
            thread = getattr(self.dream_engine, "_thread", None)
            health["dream-engine"] = {
                "state": "running" if getattr(self.dream_engine, "_running", False) else "stopped",
                "alive": bool(thread and thread.is_alive()),
            }
        return health

    def _request_shutdown(self) -> None:
        self._running = False
        self._stop_event.set()
        try:
            import sounddevice as _sd
            _sd.stop()
        except Exception:
            pass

    def _shutdown_components(self) -> None:
        """Teardown idempotente con join dei loop prima dell'uscita del processo."""
        with self._shutdown_lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True

        self._request_shutdown()
        components = []
        if hasattr(self, "dream_engine"):
            components.append(("dream-engine", self.dream_engine.stop))
        if hasattr(self, "obsidian_sync"):
            components.append(("obsidian", self.obsidian_sync.stop_watcher))
        components.append(("visual-gate", self.visual_gate.stop))
        for name, stop in components:
            try:
                stop()
            except Exception as exc:
                logger.warning(f"Shutdown {name} fallito: {exc}")
        alive = self._workers.shutdown(timeout=8)
        if alive:
            logger.warning(f"Shutdown: worker non terminati entro deadline: {', '.join(alive)}")

    def _play_beep(self):
        """Segnale acustico breve — usato quando l'utente non è in frame ma c'è un reminder."""
        import subprocess
        # Suono di sistema macOS — nessuna dipendenza extra
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Ping.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _handle_save_memory(self, text: str):
        from core.save_service import save_memory_command
        # Se c'è una scadenza parsabile, è un todo mascherato da memory
        due_at = extract_due_date(text)
        if due_at:
            self._handle_save_todo(text)
            return
        # Logica SAVE_MEMORY (risoluzione contenuto anaforico/pre-trigger + Buttafuori +
        # merge costruttivo) condivisa con la Silent Chat via core/save_service. Qui la voce
        # fornisce l'ultimo scambio (TTL 300s) e fa l'I/O (speak + log).
        # Vedi [[project_euri_save_anaforico]].
        fresh = bool(self._last_speech_content
                     and time.time() - self._last_speech_ts < 300)
        # Snapshot della conversazione recente per il risolutore SAVE semantico (Gradino 1):
        # serve a catturare la SOSTANZA di un soggetto discusso ("ricordati il macinato di
        # Seari"), non solo l'ultimo scambio. Vedi [[feedback_insegnamento_naturale]].
        with self.brain.history_lock:
            recent_history = list(self.brain._conversation_history)
        result = save_memory_command(
            text, self.memory, self.brain,
            prev_user_text=self._last_user_text or "",
            prev_assistant_text=self._last_speech_content or "",
            fresh=fresh,
            recent_history=recent_history,
            active_artifact=self.executor.get_session_artifact(),
        )
        if result["saved"]:
            self.memory.log_conversation(_OWNER_NAME, text)
            self.memory.log_conversation(_ASSISTANT_NAME, result["reply"])
        self._speak(result["reply"])

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
        if due_at:
            # Contenuto + scadenza GIÀ chiari → salva diretto, niente giro di conferma.
            # La verbosità ("vuoi aggiungere…?") serviva solo quando manca qualcosa: qui non manca.
            self.memory.save_todo(content, due_at=due_at)
            reply = self.brain.confirm_save("todo", content, format_datetime(due_at))
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return
        # Manca la scadenza → lì la domanda ha senso (resta il pending per la risposta).
        self._pending_todo = _PendingState({"content": content, "due_at": None}, timeout=120)
        self._speak(f"Segno: '{content}'. Per quando? (o 'no' per annullare)")

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
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
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

    # Briefing di curiosità — NON più il regex robotico che decideva da solo (e scambiava
    # "ultimamente" per un tema). Questo è solo un PRE-FILTRO LARGO (recall, ~0ms): la frase
    # accenna a sogni/pensieri/intuizioni? Se sì, è il MODELLO a capire se è davvero una
    # richiesta sui sogni di Euri e a estrarne il tema (vedi _understand_briefing).
    # Una sola verità: il pre-filtro è condiviso con la Silent Chat (core.reaction).
    from core.reaction import BRIEFING_HINT_RE as _BRIEFING_HINT_RE

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
            self._remember_created_file(res.raw_data.get("filepath"))   # per "aprilo"
            fname = res.raw_data.get("filepath", "file").split("/")[-1]
            self.memory.log_conversation(_ASSISTANT_NAME, f"[Documento salvato: {fname}]")
            self._speak(f"Documento creato e salvato come {fname}.")
        else:
            self._speak("Errore nella creazione del documento.")

    def _understand_briefing(self, text: str) -> tuple[bool, str | None]:
        """Delegato alla logica condivisa (core.reaction) — stessa di voce e Silent Chat."""
        from core.reaction import understand_briefing
        return understand_briefing(text)

    def _handle_dream_briefing(self, topic: str | None = None):
        """Bootstrap della curiosità: pesca un insight non groundato e lo CHIEDE a Stefano come
        un bambino. Orchestrazione condivisa in core.reaction.run_briefing (stessa di Silent
        Chat); qui si parla a voce e si mette in attesa-reazione via _PendingState (30 min)."""
        from core.reaction import run_briefing
        text, insight = run_briefing(self.r, self.embedder, topic)
        self.brain.record_context_message("assistant", text)
        self.memory.log_conversation(_ASSISTANT_NAME, text)
        self._speak(text)
        if insight is not None:
            question_id = f"briefing:{insight.get('id') or time.time()}"
            self._awaiting_reaction = _PendingState(
                {"insight": insight, "question": text, "question_id": question_id},
                timeout=300,
            )
            self._persist_pending_continuity("reaction", self._awaiting_reaction)
            self.present.set_pending_question(question_id, text)

    def _handle_reaction(self, text: str) -> bool:
        """Stefano ha risposto alla domanda di curiosità. La risposta è la verità ESTERNA
        che fonda o smentisce l'insight: la cattura (sintesi della lezione su Qwen, lenta)
        gira in BACKGROUND per non bloccare la voce. Euri dà solo un cenno naturale."""
        pending = self._awaiting_reaction
        if not pending:
            return False

        # Guardia chiarimento: se Stefano CHIEDE invece di rispondere ("di quale
        # insight parli?", "non capisco cosa intendi"), NON consumare il turno come
        # verdetto — altrimenti un non-risposta diventa un mezzo-verdetto (DA_VALUTARE
        # → requires_verification, finding 26/06). Classificatore pragmatico via Gemma
        # (non regex: capisce il fraseggio nuovo). Euri ri-nomina l'insight e RI-ARMA.
        from core.utterance_pragmatics import classify_reply_type
        reply_type = classify_reply_type(pending.data.get("question", ""), text)
        if reply_type == "CLARIFICATION":
            self.brain.record_context_message("user", text)
            self.memory.log_conversation(_OWNER_NAME, text)
            ins = pending.data.get("insight", {})
            question = pending.data.get("question", "")
            question_id = pending.data.get("question_id") or f"reaction:{ins.get('id') or time.time()}"
            self._awaiting_reaction = _PendingState(
                {"insight": ins, "question": question, "question_id": question_id},
                timeout=300,
            )
            self._persist_pending_continuity("reaction", self._awaiting_reaction)
            self.present.set_pending_question(question_id, question)
            content = (ins.get("content") or "").strip()
            if content:
                # L'insight ha struttura fissa a 3 righe e la TESI è la terza: il vecchio
                # taglio cieco [:220] la mangiava (due volte dal vivo, 02-03/07) e Stefano
                # finiva per "confermare" un claim mai pronunciato — che la cattura della
                # reazione poi registrava come validato. Si legge TUTTO: la reazione può
                # fondare solo ciò che è stato davvero detto.
                snippet = " ".join(line.strip() for line in content.splitlines() if line.strip())
            else:
                snippet = "il pensiero che ti ho appena condiviso"
            reply = f"Mi riferivo a questo: {snippet}. Secondo te regge, o è una forzatura?"
            self.brain.record_context_message("assistant", reply)
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return True

        if reply_type == "OFF_TOPIC":
            question_id = pending.data.get("question_id")
            self._awaiting_reaction = None
            self._clear_pending_continuity()
            self.present.clear_pending_question(question_id)
            logger.info("Reaction pending chiusa: replica OFF_TOPIC, turno restituito al dispatch")
            return False

        self._awaiting_reaction = None
        self._clear_pending_continuity()
        self.present.clear_pending_question(pending.data.get("question_id"))
        insight = pending.data["insight"]
        self.brain.record_context_message("user", text)
        self.memory.log_conversation(_OWNER_NAME, text)

        def _bg(ins=insight, reaction=text):
            try:
                from core.reaction import capture_reaction
                capture_reaction(self.memory, ins, reaction)
            except Exception as e:
                logger.error(f"Cattura reazione fallita: {e}")

        threading.Thread(target=_bg, daemon=True).start()
        self.brain.record_context_message("assistant", _REACTION_ACK)
        self._speak(_REACTION_ACK)
        return True

    def _handle_memory_verification(self, text: str) -> bool:
        """Collega la risposta dell'utente alla memoria passiva chiesta dal Pulse."""
        pending = self._awaiting_memory_verification
        if not pending:
            return False
        from core.utterance_pragmatics import classify_memory_verification_reply

        data = pending.data
        verdict = classify_memory_verification_reply(
            data.get("question", ""), data.get("claim", ""), text
        )
        if verdict == "CLARIFICATION":
            self.brain.record_context_message("user", text)
            self.memory.log_conversation(_OWNER_NAME, text)
            reply = f"Mi riferivo a questa informazione: {data.get('claim', '')}. È corretta?"
            self.brain.record_context_message("assistant", reply)
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return True
        if verdict == "OFF_TOPIC":
            self._awaiting_memory_verification = None
            self._clear_pending_continuity()
            self.present.clear_pending_question(data.get("question_id"))
            logger.info("Verifica memoria passiva chiusa: replica OFF_TOPIC")
            return False

        memory_id = str(data.get("memory_id") or "")
        key = f"euri:memory:{memory_id}"
        now_ts = time.time()
        try:
            if verdict == "CONFIRM":
                self.r.json().set(key, "$.requires_verification", False)
                self.r.json().set(key, "$.passive_support", "owner_confirmed")
                self.r.json().set(key, "$.verification_status", "externally_confirmed_by_owner")
                self.r.json().set(key, "$.epistemic_status", "externally_confirmed")
                self.r.json().set(key, "$.confirmed_by_user_at", now_ts)
                reply = "Confermato. Ora questa informazione è fondata sulla tua verifica."
            else:
                self.r.json().set(key, "$.requires_verification", True)
                self.r.json().set(key, "$.passive_support", "owner_refuted")
                self.r.json().set(key, "$.verification_status", "externally_refuted")
                self.r.json().set(key, "$.epistemic_status", "externally_refuted")
                self.r.json().set(key, "$.refuted_by_user_at", now_ts)
                reply = (
                    "Ricevuto. La memoria resta contestata; terrò la tua correzione "
                    "separata dal dato precedente."
                )
            cognitive_emit(
                self.r,
                "memory",
                "extero",
                "verified" if verdict == "CONFIRM" else "refuted",
                producer="initiative_memory_verification",
                trace_id=data.get("question_id") or f"memory-verification:{memory_id}",
                logical_event_id=f"memory-verification:{memory_id}:{int(now_ts)}",
                entity_refs=[{"type": "memory", "id": memory_id, "role": "target"}],
                payload={"id": memory_id, "verdict": verdict},
                epistemic_before="passive_requires_verification",
                epistemic_after=(
                    "externally_confirmed" if verdict == "CONFIRM" else "externally_refuted"
                ),
                salience=0.75,
            )
            logger.info(
                f"Verifica memoria passiva {memory_id[:8]}: {verdict} → stato aggiornato"
            )
        except Exception as e:
            logger.error(f"Verifica memoria passiva fallita {memory_id[:8]}: {e}")
            return True

        self._awaiting_memory_verification = None
        self._clear_pending_continuity()
        self.present.clear_pending_question(data.get("question_id"))
        self.brain.record_context_message("user", text)
        self.brain.record_context_message("assistant", reply)
        self.memory.log_conversation(_OWNER_NAME, text)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)
        return True

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
        self.memory.log_conversation(_OWNER_NAME, text)
        reply = self.brain.confirm_save("note", content)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _finalize_unbacked_action_claims(
        self,
        reply: str,
        user_text: str,
        *,
        channel: str,
        semantic_action_veto: bool = False,
    ) -> tuple[str, bool]:
        """Applica il guard finale senza scavalcare un veto semantico già emesso.

        Ritorna ``(testo, rerouted)``. Il veto riguarda soltanto le promesse
        morbide: un claim forte su un'azione dichiarata come già compiuta resta
        soggetto alla correzione ordinaria.
        """
        claim_details = unbacked_action_claim_details(reply, set())
        semantic_soft_veto = (
            semantic_action_veto
            and bool(claim_details)
            and all(item["category"] == "immediate_commitment" for item in claim_details)
        )
        for item in claim_details:
            logger.info(
                "Guard atto-parola: category={} semantic_veto={} sentence={!r}",
                item["category"], semantic_soft_veto, item["sentence"],
            )
        if (
            needs_honest_correction(reply, set())
            and not semantic_soft_veto
            and self._try_euri_readonly_action(reply, user_text)
        ):
            return "", True
        if not semantic_soft_veto:
            emit_unbacked_action_commitment(self.r, reply, set(), channel=channel)
        return scrub_unbacked_action_claim(
            reply, set(), semantic_action_veto=semantic_soft_veto
        ), False

    def _handle_search(
        self, text: str, *, trusted: bool = False, observed_at: float | None = None,
        semantic_frame: dict | None = None, semantic_action_veto: bool = False,
    ):
        """SEARCH path allineato a CHAT: usa _build_context per evitare
        l'allucinazione di assenza ('non ce l'ho' su memorie che invece esistono).
        Prima della V2.16 questo handler usava solo search_memories+search_notes
        e format_search_results, producendo un retrieval più povero del CHAT path
        e quindi risposte tipo 'non l'avevo mai sentita' su soggetti ben presenti
        nello store."""
        # Audit di Coerenza: capture correction signal anche dal canale SEARCH.
        # Prima della V2.16 questo check era solo in _handle_chat: correzioni
        # classificate dal router come SEARCH (es. "qui ti correggo, X non è
        # come ho detto") venivano perse silenziosamente.
        if self.memory.detect_correction(text) or frame_is_correction(semantic_frame):
            try:
                self.memory.save_correction_signal(
                    prompt_originale=self._last_user_text or "",
                    risposta_euri=self.memory.get_last_euri_turn(),
                    correzione_user=text,
                    rag_ctx_ids=self.memory.get_last_rag_ctx(),
                )
            except Exception as e:
                logger.debug(f"Audit capture (SEARCH) fallito: {e}")
        self._last_user_text = text

        self.memory.log_conversation(_OWNER_NAME, text)
        context = self._build_context(
            text, mode="search", semantic_frame=semantic_frame
        )
        # Gradino 2 — strategia di retrieval scelta dal modello caldo (wide/subject), solo
        # quando la pre-gate cheap sospetta una domanda non-specifica. NON tocca il retrieval
        # principale: lo affianca. Fail-safe a specific_search.
        context = self._augment_context_by_strategy(text, context)
        search_hint = (
            "[Modalità ricerca: rispondi alla domanda dell'utente usando "
            "SOLO le informazioni presenti nel contesto sopra. Se le memorie "
            "rilevanti non sono nel contesto, dichiaralo onestamente — non "
            "inventare. Se invece il soggetto è presente, riassumi quello che sai.]"
        )
        context = (context + "\n\n" if context else "") + search_hint
        lineage = self._start_response_lineage(
            text, channel="voice_search", mode="search"
        )
        try:
            with self._brain_lock:
                reply = self.brain.respond(
                    text,
                    context=context,
                    trusted=trusted,
                    actor_id=_OWNER_ID if trusted else None,
                    observed_at=observed_at,
                    raw_user_text=(semantic_frame or {}).get("raw_text"),
                    semantic_frame=semantic_frame,
                    **self._memory_thinking_kwargs(),
                )
        except Exception:
            self._finish_response_lineage(
                lineage, "", outcome="failed", attribute_usage=False
            )
            raise
        reply = scrub_unbacked_save_claim(reply)  # pavimento di onestà: SEARCH non salva
        reply, rerouted = self._finalize_unbacked_action_claims(
            reply,
            text,
            channel="voice_search",
            semantic_action_veto=semantic_action_veto,
        )
        if rerouted:
            self._finish_response_lineage(
                lineage, "", outcome="rerouted", attribute_usage=False
            )
            return
        self._finish_response_lineage(lineage, reply)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _handle_list_today(self, text: str):
        self.memory.log_conversation(_OWNER_NAME, text)
        todos = self.memory.get_todos_today()
        overdue = self.memory.get_overdue_todos()
        reply = self.brain.format_today_summary(todos, overdue)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _action_snapshot(self):
        """Catalogo vivo + bersagli ammessi per il controller intenzione→azione."""
        capabilities, state_context, targets = build_capability_snapshot(
            self.memory.get_pending_todos(),
            self.executor.get_contextual_capabilities(),
        )
        state_provider = getattr(
            self.executor, "document_action_state_context", None
        )
        document_state = state_provider() if callable(state_provider) else ""
        state_context = "\n".join(
            part for part in (state_context, document_state) if part
        )
        return capabilities, state_context, targets

    def _emit_action_transition(self, proposal, kind: str, **payload) -> str | None:
        """Timeline causale dell'azione; osservazionale e sempre fail-open."""
        if proposal is None:
            return None
        trace_id = proposal.trace_id or f"action:{proposal.capability or 'none'}"
        chain = getattr(self, "_action_pulse_chain", None)
        if chain is None:
            chain = {}
            self._action_pulse_chain = chain
        event_id = cognitive_emit(
            self.r,
            "action",
            "intero",
            kind,
            producer="action_controller" if kind in {"proposed", "decided", "revalidated"} else "voice_adapter",
            trace_id=trace_id,
            causation_id=chain.get(trace_id, ""),
            logical_event_id=trace_id,
            entity_refs=(
                [{"type": "todo", "id": proposal.target_id}]
                if proposal.target_id else []
            ),
            payload={
                "capability": proposal.capability or None,
                "target_id": proposal.target_id,
                "authority": proposal.authority.value,
                "confidence": proposal.confidence,
                **payload,
            },
            epistemic_before=payload.get("epistemic_before", ""),
            epistemic_after=payload.get("epistemic_after", ""),
            salience=0.65 if kind == "executed" else 0.35,
        )
        if event_id:
            chain[trace_id] = event_id.decode() if isinstance(event_id, bytes) else str(event_id)
        return event_id

    def _execute_action_proposal(
        self,
        proposal,
        text: str,
        *,
        log_user: bool = True,
        allow_euri_read_only: bool = False,
        confirmed: bool = False,
        trusted: bool = False,
        observed_at: float | None = None,
        force_integrated: bool = False,
    ) -> bool:
        """Adapter deterministico: rivalida lo stato e parla soltanto dopo l'esito."""
        capabilities, _state, todos_by_id = self._action_snapshot()
        fresh = self.action_controller.decide(
            proposal, capabilities, allow_euri_read_only=allow_euri_read_only
        )
        self._emit_action_transition(
            proposal,
            "revalidated",
            disposition=fresh.disposition.value,
            reason=fresh.reason,
        )
        allowed = fresh.disposition == ActionDisposition.EXECUTE or (
            confirmed and fresh.disposition == ActionDisposition.CONFIRM
        )
        if not allowed:
            reply = "Lo stato è cambiato e non posso più eseguire quell'azione con certezza."
            if log_user:
                self.memory.log_conversation(_OWNER_NAME, text)
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return True

        if log_user:
            self.memory.log_conversation(_OWNER_NAME, text)
        capability = proposal.capability
        todo = todos_by_id.get(proposal.target_id or "")
        integrate_response = force_integrated or proposal.integrate_response
        before_state = (
            {
                "status": todo.get("status", "pending"),
                "due_at": todo.get("due_at"),
                "suspended_at": todo.get("suspended_at"),
            }
            if todo else {}
        )

        raw_data = {}
        ok = False
        deferred = False
        after_state = dict(before_state)
        if capability == "agenda.complete" and todo:
            ok = self.memory.complete_todo(todo["id"])
            if ok:
                after_state["status"] = "done"
            reply = (self.brain.complete_todo_response(todo.get("content", "")) if ok
                     else "Non riesco a chiuderlo: quell'impegno non è più disponibile.")
        elif capability == "agenda.suspend" and todo:
            ok = self.memory.suspend_todo(todo["id"])
            if ok:
                after_state.update({"status": "pending", "due_at": None})
            reply = (f"Fatto. Tengo in sospeso senza scadenza: {todo.get('content', '')}"
                     if ok else "Non riesco a sospenderlo: quell'impegno non è più disponibile.")
        elif capability == "agenda.reschedule" and todo:
            new_due = self._extract_reschedule_date(text)
            if new_due is None:
                deferred = True
                self._pending_reschedule = _PendingState(
                    {"id": todo["id"], "content": todo.get("content", "")}, timeout=120
                )
                reply = f"A quando lo sposto: {todo.get('content', '')[:70]}?"
            else:
                ok = self.memory.reschedule_todo(todo["id"], new_due)
                if ok:
                    after_state.update({
                        "status": "pending",
                        "due_at": new_due.isoformat(),
                        "suspended_at": None,
                    })
                reply = (f"Fatto. Spostato a {format_datetime(new_due)}: "
                         f"{todo.get('content', '')[:80]}" if ok
                         else "Non riesco a spostarlo: quell'impegno non è più disponibile.")
        elif capability.startswith("executor."):
            tool_name = capability.split(".", 1)[1]
            if tool_name == "compose_document":
                # Nome, percorso, stato e riepilogo provengono dalla ricevuta reale.
                # Un secondo testo generativo potrebbe contraddirli (per esempio
                # dicendo "sto ancora creando" dopo un salvataggio gia' verificato).
                integrate_response = False
            parameters = dict(proposal.args)
            if tool_name == "compose_document":
                # Il modello seleziona la capability, ma la richiesta che guida
                # l'editor resta il turno utente verbatim: nessuna parafrasi del
                # controller puo' cambiare il documento da produrre.
                parameters["instruction"] = text
                parameters["format"] = self.executor.resolve_document_format(
                    str(parameters.get("format") or ""), text
                )
            call = ToolCall(tool_name=tool_name, parameters=parameters)
            self.executor.stop_event.clear()
            result = self.executor.execute(call)
            ok = bool(result.success)
            reply = result.output
            raw_data = result.raw_data
            try:
                self.memory.set_last_rag_ctx([])
            except Exception as exc:
                logger.debug(f"clear last_rag_ctx contextual action fallito: {exc}")
            if not integrate_response and tool_name in {
                "analyze_image", "read_document", "clipboard_read", "compose_document"
            }:
                self.brain.inject_tool_result(
                    text, build_injected_context(reply, result.raw_data)
                )
        else:
            reply = "Non ho un adapter reale per quell'azione; non ho eseguito nulla."

        operation_reply = reply
        if integrate_response:
            reply = self._respond_after_contextual_action(
                proposal,
                text,
                reply,
                raw_data=raw_data,
                trusted=trusted,
                observed_at=observed_at,
                action_effect=(
                    next(
                        (cap.effect for cap in capabilities if cap.name == capability),
                        ActionEffect.LOCAL_WRITE,
                    )
                ),
            )
            if capability.startswith("agenda."):
                # Una mutazione non deve sparire dentro la risposta generata: il
                # proprietario deve poterla osservare senza leggere i log.
                reply = f"{operation_reply}\n\n{reply}"
        elif proposal.alternative:
            reply = f"L'azione esatta non è disponibile. Come alternativa: {reply}"
        self._emit_action_transition(
            proposal,
            "deferred" if deferred else ("executed" if ok else "failed"),
            success=ok,
            before=before_state,
            after=after_state,
        )
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)
        logger.info(
            f"ActionController: {'eseguita' if ok else 'fallita'} {capability} "
            f"target={proposal.target_id or '-'}"
        )
        return True

    def _respond_after_contextual_action(
        self,
        proposal,
        text: str,
        result_text: str,
        *,
        raw_data: dict | None = None,
        trusted: bool = False,
        observed_at: float | None = None,
        action_effect: ActionEffect = ActionEffect.READ_ONLY,
    ) -> str:
        """Integra un esito operativo senza perdere la domanda che lo ha motivato.

        Questo percorso non rientra nel dispatch: evita una seconda classificazione e
        una seconda azione. Il risultato verificato diventa contesto della sola risposta
        finale, che deve coprire anche la parte esplicativa/valutativa rimasta aperta.
        """
        context = self._build_context(text)
        context = self._augment_context_by_strategy(text, context)
        grounded_result = build_injected_context(result_text, raw_data)
        action_note = (
            "[ESITO OPERATIVO VERIFICATO NEL TURNO]\n"
            f"Capability eseguita: {proposal.capability}\n"
            f"Esito: {grounded_result}\n"
        )
        if proposal.alternative:
            action_note += (
                "Il gesto era soltanto un'alternativa e non ha soddisfatto interamente "
                f"questo obiettivo: {proposal.unmet_intent or 'richiesta originale'}.\n"
            )
        action_note += (
            "Rispondi ora alla richiesta originale con un'unica risposta coerente. "
            "Usa l'esito come evidenza, non come sostituto della risposta. Distingui "
            "cio' che il tool dimostra dalle tue valutazioni e non inventare altre azioni."
        )
        context = (context + "\n\n" if context else "") + action_note
        with self._brain_lock:
            reply = self.brain.respond(
                text,
                context=context,
                trusted=trusted,
                actor_id=_OWNER_ID if trusted else None,
                observed_at=observed_at,
                **self._memory_thinking_kwargs(),
            )
        if action_effect == ActionEffect.READ_ONLY:
            reply = scrub_unbacked_save_claim(reply)
        turn_actions = {proposal.capability}
        emit_unbacked_action_commitment(
            self.r, reply, turn_actions, channel="voice_contextual_action"
        )
        return scrub_unbacked_action_claim(reply, turn_actions)

    def _try_contextual_action(
        self,
        text: str,
        *,
        trusted: bool = False,
        observed_at: float | None = None,
        semantic_frame: dict | None = None,
    ) -> tuple[bool, bool]:
        """Ritorna (turno_gestito, veto_semantico_su_azione).

        Il veto distingue un vero NONE/low-confidence del controller da un guasto
        del modello: nel primo caso un classificatore piu' largo non puo' scavalcare
        il grounding e far partire comunque un handler mutante.
        """
        capabilities, state_context, todos_by_id = self._action_snapshot()
        previous = self.memory.get_last_euri_turn()
        with self._brain_lock:
            proposal = self.action_controller.propose(
                text,
                previous_euri_turn=previous,
                capabilities=capabilities,
                state_context=state_context,
                targets_by_id=todos_by_id,
            )
        if proposal is None:
            # Il controller non ha prodotto un giudizio valido: una richiesta
            # realmente operativa deve restare fail-closed.
            return False, True
        if (
            proposal.capability == "executor.compose_document"
            and hasattr(self.executor, "merge_document_source_hint")
        ):
            proposal = dataclass_replace(
                proposal,
                args=self.executor.merge_document_source_hint(
                    proposal.args, semantic_frame
                ),
            )
        self._emit_action_transition(proposal, "proposed", reason=proposal.reason)
        decision = self.action_controller.decide(
            proposal,
            capabilities,
            allow_euri_read_only=bool(proposal.alternative),
        )
        self._emit_action_transition(
            proposal,
            "decided",
            disposition=decision.disposition.value,
            reason=decision.reason,
        )
        logger.info(
            f"ActionController: {decision.disposition.value} cap={proposal.capability or '-'} "
            f"target={proposal.target_id or '-'} conf={proposal.confidence:.2f} "
            f"authority={proposal.authority.value}"
        )
        if decision.disposition == ActionDisposition.CONVERSE:
            logger.info("ActionController: gesto linguistico → ritorno a CHAT")
            return False, False
        if decision.disposition == ActionDisposition.ABSTAIN:
            return False, True
        if decision.disposition == ActionDisposition.CLARIFY:
            self.memory.log_conversation(_OWNER_NAME, text)
            self._pending_action = _PendingState(
                {"kind": "clarify", "proposal": proposal, "text": text}, timeout=120
            )
            reply = ("Ho capito l'azione, ma non quale impegno intendi. Quale devo usare?"
                     if proposal.capability.startswith("agenda.")
                     else "Ho capito l'azione, ma il bersaglio non è abbastanza chiaro.")
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return True, False
        if decision.disposition == ActionDisposition.CONFIRM:
            self.memory.log_conversation(_OWNER_NAME, text)
            self._pending_action = _PendingState(
                {"kind": "confirm", "proposal": proposal, "text": text}, timeout=120
            )
            capability = next(
                (cap for cap in capabilities if cap.name == proposal.capability), None
            )
            description = (
                capability.description.split(".", 1)[0]
                if capability else proposal.capability
            )
            if proposal.alternative:
                reply = (
                    "Non posso eseguire esattamente la richiesta. "
                    f"Posso però proporti questa alternativa: {description}. Procedo?"
                )
            else:
                reply = f"Posso eseguire {description}, ma richiede conferma. Procedo?"
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return True, False
        return self._execute_action_proposal(
            proposal,
            text,
            allow_euri_read_only=bool(proposal.alternative),
            trusted=trusted,
            observed_at=observed_at,
        ), False

    def _try_euri_readonly_action(self, draft_reply: str, user_text: str) -> bool:
        """Una intenzione di Euri puo' auto-eseguire soltanto osservazioni read-only.

        Il draft non viene pronunciato ne' usato come autorizzazione. Viene convertito
        in proposta, ristretto alle capability read-only e poi rivalidato dall'Executor.
        """
        capabilities, state_context, _todos = self._action_snapshot()
        capabilities = [
            cap for cap in capabilities
            if cap.effect == ActionEffect.READ_ONLY and cap.name.startswith("executor.")
        ]
        if not capabilities:
            return False
        with self._brain_lock:
            proposal = self.action_controller.propose(
                draft_reply,
                previous_euri_turn=user_text,
                capabilities=capabilities,
                state_context=state_context,
                origin="euri",
            )
        decision = self.action_controller.decide(
            proposal, capabilities, allow_euri_read_only=True
        )
        if decision.disposition != ActionDisposition.EXECUTE or proposal is None:
            return False
        logger.info(
            f"ActionController: intenzione Euri read-only → {proposal.capability}"
        )
        return self._execute_action_proposal(
            proposal,
            user_text,
            log_user=False,
            allow_euri_read_only=True,
            force_integrated=True,
        )

    def _handle_pending_action(self, text: str):
        pending = self._pending_action
        self._pending_action = None
        if pending is None:
            return
        self.memory.log_conversation(_OWNER_NAME, text)
        if pending.data.get("kind") == "clarify":
            capabilities, state_context, todos_by_id = self._action_snapshot()
            combined = f"{pending.data['text']}\nChiarimento dell'utente: {text}"
            with self._brain_lock:
                proposal = self.action_controller.propose(
                    combined,
                    previous_euri_turn="Quale bersaglio intendi?",
                    capabilities=capabilities,
                    state_context=state_context,
                    targets_by_id=todos_by_id,
                )
            if proposal is not None:
                self._emit_action_transition(proposal, "proposed", reason=proposal.reason)
            decision = self.action_controller.decide(
                proposal,
                capabilities,
                allow_euri_read_only=bool(proposal and proposal.alternative),
            )
            if proposal is not None:
                self._emit_action_transition(
                    proposal,
                    "decided",
                    disposition=decision.disposition.value,
                    reason=decision.reason,
                )
            if decision.disposition == ActionDisposition.EXECUTE and proposal is not None:
                self._execute_action_proposal(
                    proposal,
                    combined,
                    log_user=False,
                    allow_euri_read_only=bool(proposal.alternative),
                )
                return
            if decision.disposition == ActionDisposition.CONFIRM and proposal is not None:
                self._pending_action = _PendingState(
                    {"kind": "confirm", "proposal": proposal, "text": combined}, timeout=120
                )
                reply = f"Ora il bersaglio è chiaro. Confermi {proposal.capability}?"
            else:
                reply = "Non riesco ancora a collegarlo a un bersaglio univoco; non ho eseguito nulla."
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return
        if re.search(r"\b(sì|si|vai|procedi|fallo|confermo)\b", text, re.IGNORECASE):
            self._execute_action_proposal(
                pending.data["proposal"], pending.data["text"],
                log_user=False, confirmed=True
            )
            return
        reply = "Va bene, non ho eseguito l'azione."
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _handle_complete(self, text: str):
        self.memory.log_conversation(_OWNER_NAME, text)
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
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    # Ore in lettere ("alle nove" → "alle 9:00") e rumore da togliere dalla query
    # di targeting (verbi del gesto + riferimenti temporali: identificano l'AZIONE,
    # non l'impegno). Forme strutturali dell'italiano, nessun idioma di settore.
    _HOUR_WORDS = {"una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
                   "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
                   "dodici": 12, "tredici": 13, "quattordici": 14, "quindici": 15,
                   "sedici": 16, "diciassette": 17, "diciotto": 18, "diciannove": 19,
                   "venti": 20, "ventuno": 21, "ventidue": 22, "ventitre": 23}
    _RESCHED_NOISE = {"sposta", "spostalo", "spostala", "spostami", "rimanda", "rimandalo",
                      "rimandala", "posticipa", "rinvia", "riprogramma", "impegno",
                      "promemoria", "scadenza", "appuntamento", "aggiornato", "spostato",
                      "riprogrammato", "spostata", "riprogrammata", "lunedì", "martedì",
                      "mercoledì", "giovedì", "venerdì", "sabato", "domenica", "domani",
                      "dopodomani", "oggi", "stasera", "stamattina", "settimana",
                      "settimane", "giorno", "giorni", "mese", "mesi", "prossimo",
                      "prossima", "mattina", "pomeriggio", "sera", "adesso", "subito"}

    @classmethod
    def _extract_reschedule_date(cls, text: str):
        """extract_due_date + tre normalizzazioni per gli spostamenti:
        'rimandalo DI una settimana' → 'tra una settimana'; 'alle nove' → 'alle 9:00'
        (forme strutturali italiane, non idiomi di settore); giorno senza orario →
        default 09:00 (una scadenza a mezzanotte non è mai l'intento di un impegno)."""
        if not text:
            return None
        from core.time_parser import extract_due_date
        t = re.sub(r"\b(?:di|per)\s+((?:un[oa]?|\d+)\s+(?:giorn[io]|settiman[ae]|or[ae]|mes[ei]))\b",
                   r"tra \1", text, flags=re.IGNORECASE)
        t = re.sub(r"\b(alle?)\s+(" + "|".join(cls._HOUR_WORDS) + r")\b",
                   lambda m: f"alle {cls._HOUR_WORDS[m.group(2).lower()]}:00", t,
                   flags=re.IGNORECASE)
        due = extract_due_date(t)
        if due is not None and due.hour == 0 and due.minute == 0:
            due = due.replace(hour=9)
        return due

    def _find_reschedule_target(self, *texts):
        """Individua l'impegno da spostare: keyword OR sui testi (utente prima, claim
        di Euri come fallback), spogliati dei verbi del gesto e delle parole temporali.
        La frase intera sanitizzata sarebbe un AND di tutti i token: non trova mai."""
        for t in texts:
            if not t:
                continue
            kw = [w for w in self.memory._safe_keywords(t) if w not in self._RESCHED_NOISE]
            if not kw:
                continue
            candidates = self.memory.find_todo_by_content(" | ".join(kw[:5]))
            if candidates:
                return candidates[0]
        return None

    def _handle_reschedule(self, text: str, reply_hint: str = ""):
        """Sposta la scadenza di un impegno — il gesto che backa i claim di
        riprogrammazione (prima esisteva solo la parola, non l'azione)."""
        self.memory.log_conversation(_OWNER_NAME, text)
        pending = self.memory.get_pending_todos()
        if not pending:
            self._speak("Non ho impegni in agenda da spostare.")
            return
        # Target: con un solo pending è lui; altrimenti keyword-match su utente+claim
        target = pending[0] if len(pending) == 1 else self._find_reschedule_target(text, reply_hint)
        if target is None:
            self._speak("Quale impegno devo spostare? Dimmi qualche parola del contenuto.")
            return
        # Data: dal testo dell'utente; in fallback dal claim di Euri (implicit action)
        new_due = self._extract_reschedule_date(text) or self._extract_reschedule_date(reply_hint)
        if new_due is None:
            self._pending_reschedule = _PendingState(
                {"id": target["id"], "content": target.get("content", "")}, timeout=120)
            self._speak(f"A quando sposto: {target.get('content', '')[:70]}?")
            return
        self._apply_reschedule(target, new_due)

    def _handle_pending_reschedule(self, text: str):
        """Risposta alla domanda 'a quando lo sposto?'."""
        pending = self._pending_reschedule
        self._pending_reschedule = None
        if re.search(r"\b(annulla|lascia\s+stare|lascia\s+perdere|niente|no)\b", text.lower()):
            self._speak("Ok, la scadenza resta com'è.")
            return
        new_due = self._extract_reschedule_date(text)
        if new_due is None:
            self._speak("Non ho capito la data — la scadenza resta com'era.")
            return
        self._apply_reschedule(pending.data, new_due)

    def _apply_reschedule(self, todo: dict, new_due):
        if self.memory.reschedule_todo(todo["id"], new_due):
            reply = f"Fatto. Spostato a {format_datetime(new_due)}: {todo.get('content', '')[:80]}"
        else:
            reply = "Non riesco a spostarlo — quell'impegno non lo trovo più."
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    # ── Gesto di rilettura (audit a voce della memoria appena scritta) ─────────
    # Nato dal caso 14/07 08:14: "leggimi l'ultima memoria su questa riflessione" →
    # SEARCH semantico → deriva sul tema "memoria/AI" → risposta CONFABULATA (recitata
    # l'intestazione del contesto come fosse il ricordo). La rilettura è
    # un'INTERROGAZIONE (ultimo nodo per created_at), non una conversazione: qui
    # l'LLM non tocca il contenuto — decide solo il routing (Layer 2) e la cura dopo.

    _READBACK_SRC_HINTS = [
        # (parole nella richiesta) → filtro source; l'ordine conta, il primo che matcha vince
        (re.compile(r"\b(lezion|riflession|correzion|reazion)", re.IGNORECASE), ["reaction", "reflection"]),
        (re.compile(r"\b(impegn|promemoria|scadenz)", re.IGNORECASE), None),  # gestito a parte via status
        (re.compile(r"\b(insegnat|teach|studiat)", re.IGNORECASE), ["teach"]),
    ]

    def _find_readback_target(self, text: str) -> dict | None:
        """L'ultima memoria per created_at, con filtro-fonte se la richiesta lo suggerisce
        ("l'ultima lezione" → reaction/reflection) e restringimento per keyword se nomina
        un soggetto. Deterministico: niente semantica, niente LLM."""
        source_filter = None
        for pat, filt in self._READBACK_SRC_HINTS:
            if pat.search(text):
                source_filter = filt
                break
        recent = self.memory.get_recent_memories(limit=10, source_filter=source_filter, touch=False)
        # La rilettura è un gesto di AUDIT sulla CRONOLOGIA: si ri-ordina per created_at
        # perché get_recent_memories ora riordina per rischio epistemico (f19ce39) e una
        # lezione fresca con numeri (requires_verification) scivolerebbe sotto una memoria
        # vecchia ma pulita — e "cosa hai salvato poco fa?" leggerebbe la cosa sbagliata.
        # Per l'audit vale l'opposto: le memorie rischiose sono quelle da riascoltare.
        recent.sort(key=lambda m: float(m.get("created_at") or 0), reverse=True)
        if not recent:
            return None
        # Il rumore include interrogative e deittici temporali ("COSA hai salvato POCO
        # fa?"): sopravvissuti al filtro diventano keyword di contenuto e scavalcano la
        # più recente (caso live 14/07: "cosa" matchava una reflection al posto della
        # lezione in cima). E il match è per PAROLA INTERA, non per sottostringa.
        kw = [w for w in self.memory._safe_keywords(text)
              if w not in {"memoria", "memorie", "lezione", "lezioni", "riflessione",
                           "ultima", "ultime", "ultimo", "ultimi", "appena", "salvato",
                           "salvata", "memorizzato", "scritto", "creato", "creata",
                           "leggi", "leggimi", "rileggi", "rileggimi", "grado",
                           "proposito", "risposta", "questa", "sentire", "cosa",
                           "come", "quando", "quale", "poco", "prima", "adesso",
                           "oggi", "ieri", "fammi", "dimmi", "voce"}]
        if kw:
            pat = re.compile(r"\b(" + "|".join(map(re.escape, kw)) + r")\b", re.IGNORECASE)
            for m in recent:
                if pat.search(m.get("content") or ""):
                    return m
        return recent[0]

    def _handle_read_back(self, text: str):
        self.memory.log_conversation(_OWNER_NAME, text)
        mem = self._find_readback_target(text)
        if not mem:
            self._speak("Non trovo memorie recenti da rileggerti.")
            return
        content = re.sub(r"^#\s*Memoria\s*\([^)]*\)\s*", "", (mem.get("content") or "")).strip()
        when = ""
        try:
            from utils.date_utils import from_timestamp
            dt = mem.get("_created_at") or from_timestamp(mem.get("created_at"))
            if dt:
                when = f", salvata alle {dt.strftime('%H:%M')} del {dt.strftime('%d/%m')}"
        except Exception:
            pass
        src = mem.get("source", "?")
        reply = (f"Te la leggo fedele — fonte {src}{when}: {content} — "
                 f"Se c'è da correggere o aggiungere qualcosa, dimmelo adesso.")
        self._pending_readback = _PendingState({"id": mem.get("id"), "content": content}, timeout=180)
        self.present.set_pending_question(f"readback:{mem.get('id')}", reply)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _handle_pending_readback(self, text: str):
        """La risposta dopo la rilettura: ok/niente → chiudi; 'aggiungi…' → merge
        costruttivo; correzione → riscrittura fedele. In entrambi i casi di modifica:
        nuovo nodo source=user (fonte massima) + supersede del vecchio — il canale di
        CURA della memoria guidato da Stefano, mai edit silenziosi."""
        pending = self._pending_readback
        self._pending_readback = None
        self.present.clear_pending_question(f"readback:{pending.data.get('id')}")
        # Il triage lo fa il classificatore pragmatico (regex solo sui casi ovvi,
        # Gemma per la varietà del parlato, fallback conservativo=OK): "Tutto
        # perfetto, hai capito benissimo" è un OK anche senza parole-chiave —
        # il prefisso-regex qui creava un nodo spurio (caso live 14/07 08:47).
        from core.utterance_pragmatics import classify_readback_reply
        kind = classify_readback_reply(text)
        if kind == "OK":
            self._speak("Ok, la lascio com'è.")
            return
        body = re.sub(r"^no[,\s]+", "", text, flags=re.IGNORECASE)
        old_id, old_content = pending.data["id"], pending.data["content"]
        if kind == "AGGIUNTA":
            merged = self.brain.merge_memories(old_content, body)
            new_content = merged if merged not in ("DIVERSO", "NESSUNA AGGIUNTA") else None
            verb = "arricchita"
        else:
            new_content = self.brain.apply_correction_to_memory(old_content, body)
            verb = "corretta"
        if not new_content:
            # mai perdere la parola di Stefano: la sua frase diventa il nodo nuovo
            new_content = body
            verb = "sostituita con le tue parole"
        # Guardia P-GT sul canale di cura (una soglia di similarità NON distingue
        # "riscritto identico" da "corretto un numero" sui testi lunghi):
        #  1. testo invariato → nessun nodo;
        #  2. i token NUOVI salienti devono avere radice nelle parole dell'utente —
        #     se la riscrittura introduce parole mai dette, si rifiuta onestamente
        #     invece di salvare invenzioni (falso-positivo ack o LLM che ricama).
        _norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
        _toks = lambda s: set(re.findall(r"\w+", _norm(s)))
        if _norm(new_content) == _norm(old_content):
            self._speak("Ho riguardato la memoria ma non c'era nulla da cambiare — la lascio com'è.")
            return
        added = {t for t in _toks(new_content) - _toks(old_content) if len(t) >= 3 or t.isdigit()}
        removed = {t for t in _toks(old_content) - _toks(new_content) if len(t) >= 3}
        if not added and not removed:
            self._speak("Ho riguardato la memoria ma non c'era nulla da cambiare — la lascio com'è.")
            return
        if added and not (added & _toks(body)):
            self._speak("Non ho capito bene cosa dovrei cambiare — ripetimi la correzione "
                        "con le parole esatte e la applico.")
            return
        new_id = self.memory.save_memory(new_content, source="user")
        if new_id and old_id:
            self.memory.supersede_memory(old_id, new_id)
        reply = (f"Fatto, memoria {verb}. Ora dice: {new_content[:220]}"
                 if new_id else "Non sono riuscita a salvare la modifica.")
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
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
        if re.search(r"\b(memori[ae]|ricord[oi])\b", text, re.IGNORECASE):
            by_source: dict[str, int] = {}
            total = 0
            for key in self.memory.r.scan_iter("euri:memory:*"):
                try:
                    source = self.memory.r.json().get(key, "$.source")
                    source = source[0] if source else "?"
                except Exception:
                    source = "?"
                by_source[str(source)] = by_source.get(str(source), 0) + 1
                total += 1
            details = ", ".join(
                f"{count} da {source}"
                for source, count in sorted(by_source.items(), key=lambda item: -item[1])
            )
            reply = f"Ho {total} memorie in totale"
            if details:
                reply += f": {details}"
            from core.conversation_turns import get_verbatim_lifecycle_pending
            lifecycle_pending = get_verbatim_lifecycle_pending(self.memory.r)
            if lifecycle_pending:
                counts = lifecycle_pending.get("counts") or {}
                reply += (
                    ". Ho anche una revisione dell'archivio originale pendente: "
                    f"{counts.get('orphan_candidates', 0)} candidati orfani, "
                    f"{counts.get('missing_source_refs', 0)} riferimenti mancanti "
                    f"e {counts.get('malformed_turns', 0)} turni malformati. "
                    "Non ho cancellato nulla"
                )
            from core.memory_utility_shadow import get_memory_utility_review_pending
            utility_pending = get_memory_utility_review_pending(self.memory.r)
            if utility_pending:
                reply += (
                    ". È inoltre maturata la revisione dei dati sull'utilità "
                    "delle memorie richiamate. I dati sono pronti, ma non ho "
                    "ritoccato automaticamente i pesi né i gate di promozione"
                )
            self._speak(reply + ".")
            return
        todos = self.memory.get_pending_todos()
        overdue = self.memory.get_overdue_todos()
        memories = self.memory.get_recent_memories(limit=999, touch=False)
        reply = self.brain.generate_status(len(todos), len(overdue), len(memories))
        self._speak(reply)

    def _handle_execute(self, text: str):
        """Esegue un tool di sistema tramite l'Executor sandbox."""
        self.memory.log_conversation(_OWNER_NAME, text)
        self._last_user_text = text
        # Un tool non è una risposta basata su RAG: una correzione al turno successivo
        # non deve ereditare i nodi memoria del turno precedente.
        try:
            self.memory.set_last_rag_ctx([])
        except Exception as e:
            logger.debug(f"clear last_rag_ctx EXECUTE fallito: {e}")

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
            self.memory.log_conversation(_ASSISTANT_NAME, hint)
            self._speak(hint)
            return

        # Sostituisce la sentinella __USER_TEXT__ (pattern run_code/read_document)
        # con la frase reale dell'utente, su qualunque parametro la contenga.
        for _k, _v in call.parameters.items():
            if _v == "__USER_TEXT__":
                call.parameters[_k] = text

        # Feedback vocale differenziato
        if call.tool_name == "build_computational_tool":
            self._speak("Costruisco uno strumento temporaneo e verifico il risultato.")
        elif call.tool_name == "run_code":
            self._speak("Ci penso, genero ed eseguo il codice.")
        elif call.tool_name == "read_document":
            self._speak("Leggo il documento.")
        elif call.tool_name == "analyze_image":
            self._speak("Guardo l'immagine.")
        elif call.tool_name == "teach_text":
            self._speak("Lo memorizzo.")
        else:
            self._speak("Controllo.")

        result = self.executor.execute(call)
        spoken = result.output
        self.memory.log_conversation(_ASSISTANT_NAME, spoken)
        try:
            self.memory.set_last_rag_ctx([])
        except Exception as e:
            logger.debug(f"clear last_rag_ctx EXECUTE result fallito: {e}")

        # Continuità conversazionale (V2.18.3 → fix V2.19): inietta il risultato del
        # tool nella history LLM del Brain, così i turn CHAT successivi lo "vedono"
        # (es. "cosa ne pensi?" subito dopo un'analisi). log_conversation scrive su
        # Redis, ma respond() costruisce il contesto solo da _conversation_history:
        # senza questo inject il CodeRunner risponderebbe "non vedo nulla".
        if call.tool_name in ("analyze_image", "clipboard_read", "clipboard_analyze", "clipboard_analyze_save", "run_code", "build_computational_tool", "read_document", "ingest_documents", "read_url", "teach_text", "compose_document"):
            # Disaccoppia "cosa dice" da "cosa ricorda": nel contesto va anche il
            # contenuto FEDELE (run_code → CSV prodotto; read_document → testo grezzo
            # del documento), così le domande quantitative successive ("quanto era
            # l'IZOD?") leggono i valori esatti invece di confabularli.
            self.brain.inject_tool_result(text, build_injected_context(spoken, result.raw_data))

        # analyze_image/clipboard/read_document parlano un riassunto breve (evita di
        # leggere a voce tabelle/descrizioni intere); run_code parla l'output completo.
        if call.tool_name in ("analyze_image", "clipboard_analyze", "clipboard_analyze_save", "read_document"):
            self._speak(_tts_trim(spoken, max_chars=400))
        else:
            self._speak(spoken)


    def _handle_workflow(self, text: str) -> bool:
        """
        Esegue una richiesta operativa COMPOSTA via Workflow Planner.
        Ritorna True se l'ha gestita (piano ≥2 step), False per lasciar
        proseguire il dispatch normale (piano vuoto o monostep → fail-open).
        """
        from core import workflow_planner
        # Conversazione recente come FONTE del workflow (fix 01/07: la bozza ignorava
        # il discorso — "scrivimi una mail su questo" perdeva il contesto appena detto).
        try:
            with self.brain.history_lock:
                hist = list(self.brain._conversation_history)[-16:]
            convo_text = "\n".join(
                f"{_OWNER_NAME if m.get('role') == 'user' else _ASSISTANT_NAME}: "
                f"{m.get('content', '')}"
                for m in hist if m.get("content")
            )[-4000:]
        except Exception as e:
            logger.debug(f"workflow: brief history fallito: {e}")
            convo_text = ""
        steps = workflow_planner.plan(text, history_brief=convo_text)
        if len(steps) < 2:
            return False  # non è un vero workflow → dispatch normale

        caps = " → ".join(s["cap"].lower() for s in steps)
        logger.info(f"Workflow: {len(steps)} step ({caps}) — '{text}'")
        self.memory.log_conversation(_OWNER_NAME, text)
        self._speak("Va bene, procedo per passi.")
        try:
            self.memory.set_last_rag_ctx([])
        except Exception as e:
            logger.debug(f"clear last_rag_ctx WORKFLOW fallito: {e}")

        engine = workflow_planner.WorkflowEngine(self.executor, self.brain, conversation=convo_text)
        result = engine.run(steps)

        # Filo conduttore: il workflow deve restare nel thread, come _handle_execute.
        # Senza, Euri "non sa di aver fatto quello che ha fatto" (es. "apri il documento
        # appena creato" → non lo collega). Inietta cosa ha prodotto + dove nella history
        # del Brain, così i turni CHAT successivi lo vedono.
        if result.get("ok"):
            parts = [f"Ho appena eseguito un workflow per {_OWNER_NAME}."]
            if result.get("path"):
                self._remember_created_file(result["path"])   # per "aprilo"
                parts.append(
                    f"Ho creato e salvato una bozza nel file {result['path']} "
                    "(non inviata, è per la revisione)."
                )
            if result.get("text"):
                parts.append(f"Contenuto della bozza:\n{result['text'][:1500]}")
            self.brain.inject_tool_result(text, "\n".join(parts))

        self.memory.log_conversation(_ASSISTANT_NAME, result["spoken"])
        self._speak(result["spoken"])
        return True

    def _remember_created_file(self, path: str | None):
        """Registra l'ultimo file creato + quando (per "aprilo" con recency)."""
        if path:
            self._last_created_file = path
            self._last_created_file_ts = time.time()

    def _handle_open_file(self, text: str = ""):
        """Apre l'ultimo file creato da Euri nell'app di sistema (xdg-open) —
        '.md o altro'. Chiude il giro 'salva per revisione → aprila per revisione'."""
        import os
        import subprocess
        path = self._last_created_file
        if not path or not os.path.exists(path):
            self._speak("Non ho un file recente da aprire.")
            return
        name = os.path.basename(path)
        try:
            subprocess.Popen(
                ["xdg-open", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            reply = f"Apro {name}."
        except Exception as e:
            logger.error(f"Apertura file fallita ({path}): {e}")
            reply = "Non sono riuscita ad aprirlo."
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _handle_audit_memory(self, text: str):
        """Audit vocale limitato: analizza candidati prioritari e propone il rumore."""
        import json as _json
        _ollama = chat_client  # instradato: chat_client.chat(...) == _ollama.chat(...)

        self.memory.log_conversation(_OWNER_NAME, text)
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
        self._speak(f"Ho {totale} memorie in totale: {stats_str}.")

        # Audita solo le passive
        passive_docs = [d for d in all_docs if d.get("source") == "passive"]
        if not passive_docs:
            self._speak("Nessuna memoria passiva da analizzare. Tutto pulito.")
            return

        audit_limit = max(1, int(getattr(config, "AUDIT_MEMORY_MAX_CANDIDATES", 40)))
        batch_size = max(1, int(getattr(config, "AUDIT_MEMORY_BATCH_SIZE", 10)))
        candidates = self._select_memory_audit_candidates(passive_docs, limit=audit_limit)
        self._speak(
            f"Faccio un controllo mirato su {len(candidates)} memorie passive prioritarie "
            f"delle {len(passive_docs)} presenti."
        )

        rumore = []
        for offset in range(0, len(candidates), batch_size):
            if not getattr(self, "_running", True):
                logger.info("Audit memoria interrotto dallo shutdown")
                return
            batch = candidates[offset:offset + batch_size]
            rows = "\n".join(
                f"{index}. {str(doc.get('content', ''))[:700]}"
                for index, doc in enumerate(batch, start=1)
            )
            prompt = (
                "Valuta queste memorie passive. Una memoria e' RUMORE solo se e' una frase "
                "generica, un artefatto di conversazione o un dato privo di un soggetto e di "
                "riuso futuro. Non chiamare rumore un fatto concreto solo perche' richiede "
                "verifica. Rispondi in JSON: {\"noise\": [numeri delle righe]} .\n\n"
                + rows
            )
            try:
                resp = _ollama.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0, "num_predict": 500},
                    format="json",
                    think=False,
                )
                parsed = _json.loads(resp.message.content or "{}")
                noise_rows = parsed.get("noise", []) if isinstance(parsed, dict) else []
                for row in noise_rows:
                    try:
                        index = int(row) - 1
                    except (TypeError, ValueError):
                        continue
                    if 0 <= index < len(batch):
                        rumore.append(batch[index])
            except Exception as exc:
                logger.warning(f"Audit memoria: batch {offset // batch_size + 1} non valutato: {exc}")

        checked_count = len(candidates)
        utili_count = checked_count - len(rumore)

        if not rumore:
            self._speak(
                f"Nel campione prioritario di {checked_count} memorie passive non ho trovato rumore."
            )
            return

        self._audit_rumore = rumore
        self._audit_confirm_mode = True
        self._speak(
            f"Nel campione di {checked_count} memorie passive: {utili_count} utili, "
            f"{len(rumore)} possibili rumori. "
            f"Le cancello?"
        )

    @staticmethod
    def _select_memory_audit_candidates(passive_docs: list[dict], limit: int) -> list[dict]:
        """Prioritizza rischio epistemico e recenza, escludendo i fili episodici."""
        eligible = [
            doc for doc in passive_docs
            if doc.get("memory_kind") != "conversation_anchor"
            and not doc.get("superseded_by")
            and not doc.get("correction_pending")
        ]

        def priority(doc: dict) -> tuple[int, float]:
            risk = 0
            risk += 100 if doc.get("audit_flag") else 0
            risk += 40 if doc.get("requires_verification") else 0
            risk += 25 if doc.get("passive_support") == "tacit_acceptance" else 0
            try:
                created_at = float(doc.get("created_at") or 0)
            except (TypeError, ValueError):
                created_at = 0.0
            return risk, created_at

        return sorted(eligible, key=priority, reverse=True)[:max(0, int(limit))]

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

    _RECENT_CONTEXT_RE = re.compile(
        r'\b(?:stavamo\s+parlando|parlavamo|dicevamo|detto\s+prima|poco\s+fa|'
        r'prima\?|di\s+cosa\s+(?:stavamo\s+)?parlavamo)\b',
        re.IGNORECASE,
    )

    def _build_context(
        self,
        text: str,
        *,
        mode: str = "chat",
        semantic_frame: dict | None = None,
    ) -> str:
        """Cerca in Redis contenuto rilevante da iniettare come contesto nella risposta."""
        from core.rag_context import build_runtime_rag_context
        with self.brain.history_lock:
            recent_history = list(self.brain._conversation_history)
        rag = build_runtime_rag_context(
            text,
            self.memory,
            self.turn_store,
            mode=mode,
            recent_history=recent_history,
            semantic_frame=semantic_frame,
        )
        # Thread-local: voce e mobile possono costruire contesti in parallelo.
        # La struttura serve soltanto alla lineage shadow e non entra nel prompt.
        local = getattr(self, "_response_rag_local", None)
        if local is None:
            local = threading.local()
            self._response_rag_local = local
        local.rag = rag
        local.augment_ids = []
        try:
            self.memory.set_last_rag_ctx(rag.ids)
        except Exception as e:
            logger.debug(f"set_last_rag_ctx fallito: {e}")
        from core.memory_scope import current_scope
        context = self.semantic_turns.registry.canonicalize(
            rag.text, current_scope()
        )
        state_provider = getattr(
            self.executor, "document_action_state_context", None
        )
        document_state = state_provider() if callable(state_provider) else ""
        if document_state:
            context = "\n\n".join((context, "[STATO OPERATIVO CONDIVISO]\n" + document_state))
        # Solo esiti sanitizzati dei recenti segmenti NON inoltrati. Questo
        # permette al Brain di spiegare perche' non ha ricevuto un audio senza
        # esporre trascrizioni ambientali o reinterpretare righe di log slegate.
        context = with_voice_perception_context(context, self.r)
        return context

    def _augment_context_by_strategy(self, text: str, context: str) -> str:
        """
        Gradino 2: amplia il context secondo la strategia di retrieval scelta dal modello
        caldo (solo quando la pre-gate cheap sospetta una domanda non-specifica).
        specific_search/recent_context → context invariato. Fail-safe: su errore, invariato.
        """
        try:
            from core.retrieval_strategy import augment_context_with_ids
            with self.brain.history_lock:
                recent = list(self.brain._conversation_history)
            local = getattr(self, "_response_rag_local", None)
            rag = getattr(local, "rag", None) if local is not None else None
            context, note, augment_ids = augment_context_with_ids(
                text,
                context,
                self.memory,
                self.brain,
                recent,
                turn_store=self.turn_store,
                rag_context=rag,
            )
            local = getattr(self, "_response_rag_local", None)
            if local is not None:
                local.augment_ids = list(augment_ids)
            if augment_ids:
                base_ids = self.memory.get_last_rag_ctx()
                self.memory.set_last_rag_ctx(list(dict.fromkeys([*base_ids, *augment_ids])))
            if note:
                logger.info(f"Retrieval strategy: {note}")
        except Exception as e:
            logger.debug(f"strategy augment fallito: {e}")
        from core.memory_scope import current_scope
        return self.semantic_turns.registry.canonicalize(
            context, current_scope()
        )

    def _memory_thinking_kwargs(self) -> dict:
        """Policy per-turno dal RAG thread-local; nessuno stato globale."""
        from core.rag_context import selective_thinking_decision

        local = getattr(self, "_response_rag_local", None)
        rag = getattr(local, "rag", None) if local is not None else None
        decision = selective_thinking_decision(rag)
        if decision["enabled"]:
            logger.info(
                "Thinking selettivo attivato: reason={} turni={}",
                decision["reason"],
                ",".join(decision["promoted_turn_ids"]),
            )
        return {
            "thinking": decision["enabled"],
            "thinking_reason": decision["reason"],
        }

    def _start_response_lineage(self, text: str, *, channel: str, mode: str):
        """Apre la trace shadow del turno senza influire sul percorso conversazionale."""
        try:
            from core.response_lineage import (
                load_augmented_memory_nodes,
                start_response_turn,
            )
            local = getattr(self, "_response_rag_local", None)
            rag = getattr(local, "rag", None) if local is not None else None
            nodes = list(getattr(rag, "nodes", []) or [])
            augment_ids = list(getattr(local, "augment_ids", []) or []) if local else []
            if augment_ids:
                memory_positions = [
                    int(node.get("position") or 0)
                    for node in nodes if node.get("kind") == "memory"
                ]
                nodes.extend(load_augmented_memory_nodes(
                    self.memory,
                    augment_ids,
                    start_position=max(memory_positions, default=0) + 1,
                ))
            return start_response_turn(
                self.r,
                query=text,
                channel=channel,
                mode=mode,
                nodes=nodes,
            )
        except Exception as e:
            logger.debug(f"Response lineage start ignorata: {e}")
            return None

    def _finish_response_lineage(
        self,
        lineage,
        response: str,
        *,
        outcome: str = "delivered",
        attribute_usage: bool = True,
    ) -> None:
        try:
            from core.response_lineage import finish_response_turn
            finish_response_turn(
                self.r,
                lineage,
                response=response,
                outcome=outcome,
                attribute_usage=attribute_usage,
            )
        except Exception as e:
            logger.debug(f"Response lineage finish ignorata: {e}")

    def _handle_web_search(self, text: str, *, semantic_frame: dict | None = None):
        """Cerca sul web, risponde vocalmente, propone di salvare."""
        from core.web_search import is_online, search
        self.memory.log_conversation(_OWNER_NAME, text)

        if not is_online():
            self._speak("Non ho internet adesso. Rispondo solo da quello che ricordo.")
            return

        query = self.brain.extract_search_query(text, semantic_frame=semantic_frame)
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
        self.memory.log_conversation(_ASSISTANT_NAME, summary)
        self._speak(summary)

        # Salva automaticamente in Redis — la conoscenza web diventa permanente (TTL 60gg)
        # requires_verification forzato: fonte esterna, non va citata come fatto certo
        mem_content = f"Ricerca web '{query}':\n{summary}"
        mid = self.memory.save_memory(
            content=mem_content,
            category="web",
            tags=["web_search"],
            source="web",
            final_fields={"requires_verification": True},
        )
        if mid:
            logger.info(f"Web search salvata in memoria: {mid[:8]}… (query: '{query[:50]}')")
        else:
            logger.warning(f"Web search NON salvata: contenuto sospetto bloccato dal MemoryGuard (query '{query[:50]}')")

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
        self.memory.log_conversation(_OWNER_NAME, text)
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
                    self._remember_created_file(res.raw_data.get("filepath"))   # per "aprilo"
                    fname = res.raw_data.get("filepath", "file").split("\\")[-1]
                    results.append(f"salvato in {fname}")
                else:
                    results.append("errore nel salvataggio")

            self._dictation_mode = False
            self._dictation_buffer = []
            azioni = " e ".join(results)
            self.memory.log_conversation(_ASSISTANT_NAME, f"[Dettatura: {n_words} parole — {azioni}]")
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
        self.memory.log_conversation(_OWNER_NAME, text)
        from core.memory_scope import current_scope, scope_of
        with self.brain.history_lock:
            history = [
                message for message in self.brain._conversation_history
                if scope_of(message) == current_scope()
            ]
        if len(history) < 2:
            self._speak("Non c'è abbastanza conversazione da salvare.")
            return
        # Prendi gli ultimi 20 messaggi (10 scambi)
        recent = history[-20:]
        dialogue = "\n".join(
            f"{_OWNER_NAME if m['role'] == 'user' else _ASSISTANT_NAME}: {m['content']}"
            for m in recent
        )
        summary = self.brain.summarize_knowledge(dialogue)
        self.memory.save_memory(summary, category="conoscenza", source="conversation")
        self.memory.log_conversation(_ASSISTANT_NAME, f"[Conversazione salvata]")
        self._speak("Salvato. Ho riassunto e memorizzato quello di cui abbiamo parlato.")



    def _handle_chat(
        self, text: str, *, trusted: bool = False, observed_at: float | None = None,
        semantic_frame: dict | None = None, semantic_action_veto: bool = False,
    ):
        # Audit di Coerenza: capture correction signal PRIMA di loggare/rispondere,
        # così last_rag_ctx contiene ancora il ctx del turno precedente (corretto).
        if self.memory.detect_correction(text) or frame_is_correction(semantic_frame):
            try:
                self.memory.save_correction_signal(
                    prompt_originale=self._last_user_text or "",
                    risposta_euri=self.memory.get_last_euri_turn(),
                    correzione_user=text,
                    rag_ctx_ids=self.memory.get_last_rag_ctx(),
                )
            except Exception as e:
                logger.debug(f"Audit capture fallito: {e}")
        self._last_user_text = text

        self.memory.log_conversation(_OWNER_NAME, text)

        # Intercetta richieste conversazionali di creazione file prima di chiamare l'LLM
        if self._WRITE_REQUEST_RE.search(text):
            self._pending_write = _PendingState({"task": text}, timeout=120)
            self._speak("Perfetto. Di' 'vai' per procedere subito, oppure aggiungimi dettagli da includere nel file.")
            return

        context = self._build_context(text, semantic_frame=semantic_frame)
        # Gradino 2 — strategia di retrieval (wide/subject) sul modello caldo, solo quando la
        # pre-gate cheap scatta; specific_search → context invariato.
        context = self._augment_context_by_strategy(text, context)
        # Il VisualGate era gia' afferente sul pulse e disponibile alla Silent Chat,
        # ma la voce non riceveva il suo stato corrente: il modello poteva quindi
        # negare un sensore che il daemon stava usando nello stesso momento.
        # Iniettiamo soltanto lo snapshot sanitizzato e a TTL breve: non e' memoria,
        # non attribuisce autorita' e non contiene frame, embedding o similarity.
        from core.visual_presence import with_visual_context
        context = with_visual_context(context, self.r)
        context = (context + "\n\n" if context else "") + "[Modalità conversazione: sii presente e naturale, non rigido.]"
        lineage = self._start_response_lineage(
            text, channel="voice_chat", mode="chat"
        )
        try:
            with self._brain_lock:
                reply = self.brain.respond(
                    text,
                    context=context,
                    trusted=trusted,
                    actor_id=_OWNER_ID if trusted else None,
                    observed_at=observed_at,
                    raw_user_text=(semantic_frame or {}).get("raw_text"),
                    semantic_frame=semantic_frame,
                    **self._memory_thinking_kwargs(),
                )
        except Exception:
            self._finish_response_lineage(
                lineage, "", outcome="failed", attribute_usage=False
            )
            raise
        linguistic_response = frame_requests_linguistic_response(
            semantic_frame,
            minimum_confidence=getattr(
                config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
            ),
        )
        if linguistic_response:
            # Il testo E' l'azione richiesta (discorso/presentazione): non esiste
            # una promessa operativa da smentire e la didascalia non va letta dal TTS.
            reply = strip_leading_stage_direction(reply)
        else:
            reply = scrub_unbacked_save_claim(reply)  # CHAT non salva
            reply, rerouted = self._finalize_unbacked_action_claims(
                reply,
                text,
                channel="voice_chat",
                semantic_action_veto=semantic_action_veto,
            )
            if rerouted:
                self._finish_response_lineage(
                    lineage, "", outcome="rerouted", attribute_usage=False
                )
                return
        self._finish_response_lineage(lineage, reply)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
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

    def _handle_teach(
        self,
        text: str,
        *,
        trusted: bool = False,
        observed_at: float | None = None,
        semantic_frame: dict | None = None,
    ):
        """Avvia TEACH soltanto da un contratto semantico grounded."""
        contract = trusted_teaching_session(
            semantic_frame,
            minimum_confidence=getattr(
                config, "SEMANTIC_TEACH_MIN_CONFIDENCE", 0.82
            ),
        )
        if contract is None:
            logger.warning("TEACH negato: contratto semantico assente o insufficiente")
            self._handle_chat(
                text,
                trusted=trusted,
                observed_at=observed_at,
                semantic_frame=semantic_frame,
            )
            return
        self._teach_mode = True
        self._teach_confirm_mode = False
        self._teach_buffer = [text]
        self._teach_topic = text
        self._teach_asked = []
        self._teach_pending_save = ""
        self._teach_contract = contract
        self.memory.log_conversation(_OWNER_NAME, text)
        logger.info(
            "TEACH avviato semanticamente: evidence='{}' conf={:.2f}",
            str(contract.get("evidence") or "")[:120],
            float(contract.get("confidence") or 0.0),
        )
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

    @staticmethod
    def _encode_teach_snapshot(
        buffer: list[str], topic: str, contract: dict
    ) -> str:
        return json.dumps({
            "schema_version": 2,
            "authorized": bool(contract),
            "buffer": [str(item) for item in buffer if str(item).strip()],
            "topic": str(topic or ""),
            "contract": dict(contract or {}),
        }, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode_teach_snapshot(raw) -> dict | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payload = json.loads(str(raw or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            return None
        contract = payload.get("contract")
        buffer = payload.get("buffer")
        if payload.get("authorized") is not True:
            return None
        if not isinstance(contract, dict) or not contract:
            return None
        if not isinstance(buffer, list) or not any(str(item).strip() for item in buffer):
            return None
        return {
            "buffer": [str(item) for item in buffer if str(item).strip()],
            "topic": str(payload.get("topic") or ""),
            "contract": dict(contract),
        }

    def _reset_teach(self):
        self._teach_mode = False
        self._teach_confirm_mode = False
        self._teach_buffer = []
        self._teach_topic = ""
        self._teach_asked = []
        self._teach_pending_save = ""
        self._teach_contract = {}
        self.r.delete("euri:teach:snapshot")

    def _handle_teach_confirm(self, text: str):
        """Gestisce la conferma dopo il read-back del riassunto."""
        self.memory.log_conversation(_OWNER_NAME, text)
        if self._TEACH_CONFIRM_YES.search(text):
            if not self._teach_contract or not self._teach_pending_save.strip():
                logger.warning(
                    "TEACH salvataggio negato: sessione non autorizzata o riepilogo assente"
                )
                self._speak(
                    "Non salvo: questa sessione non ha un'origine didattica abbastanza chiara."
                )
                self._reset_teach()
                return
            self.memory.save_memory(self._teach_pending_save, category="conoscenza", source="teach")
            self.memory.log_conversation(_ASSISTANT_NAME, f"[Conoscenza salvata — argomento: {self._teach_topic[:60]}]")
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
        self.memory.log_conversation(_OWNER_NAME, text)

        # Azioni eseguibili dentro TEACH senza uscire dalla sessione
        # Clipboard e immagini: intercetta direttamente senza dipendere dall'intent
        call = self.executor.select_tool_by_regex(text)
        if call and call.tool_name in ("clipboard_analyze", "clipboard_analyze_save", "analyze_image", "clipboard_read"):
            self._handle_execute(text)
            return
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
            snapshot = self._encode_teach_snapshot(
                self._teach_buffer,
                self._teach_topic,
                self._teach_contract,
            )
            self.r.set("euri:teach:snapshot", snapshot, ex=3600)

        accumulated = "\n".join(self._teach_buffer)
        probe = self.brain.probe_question(self._teach_topic, accumulated, self._teach_asked)
        self._teach_asked.append(probe)
        self.memory.log_conversation(_ASSISTANT_NAME, probe)
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
            snapshot = self._decode_teach_snapshot(self._teach_snapshot_content)
            if snapshot is None:
                self.r.delete("euri:teach:snapshot")
                self._teach_snapshot_content = ""
                self._speak(
                    "Non riprendo quella sessione: manca il contratto didattico verificato."
                )
                return
            lines = snapshot["buffer"]
            self._teach_buffer = lines
            self._teach_topic = snapshot.get("topic") or lines[0]
            self._teach_contract = snapshot["contract"]
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

    def _resolve_voice_actor(self, verdict: SpeakerVerdict) -> str:
        """Fonde voce e volto senza trasformare l'incertezza in identita'."""
        if verdict == SpeakerVerdict.VERIFIED:
            return _OWNER_ID
        if verdict == SpeakerVerdict.INDETERMINATE:
            try:
                if self.visual_gate.is_owner_present():
                    return _OWNER_ID
            except Exception:
                pass
            # Webcam cieca o volto temporaneamente perso: una clip breve puo'
            # appartenere a Stefano solo dentro una conversazione appena aperta
            # da una sua voce verificata. Fuori da questa finestra resta ospite.
            if 0 < time.time() - self._last_auth_voice_ts <= _CONVERSATION_WINDOW_SEC:
                try:
                    if self.present.snapshot().conversation_open():
                        return _OWNER_ID
                except Exception:
                    pass
        return "unknown"

    def _handle_guest_turn(self, text: str, *, observed_at: float | None = None) -> None:
        """Conversazione ospite isolata da memoria privata, intenti e strumenti."""
        at = float(observed_at or time.time())

        with self._brain_lock:
            claim = extract_guest_claim(text)
        quarantined = None
        if claim:
            quarantined = self.guest_claims.add(
                claim,
                original_text=text,
                observed_at=at,
                channel="voice",
            )

        # Senza un'identita' stabile non si presume che due turni appartengano
        # alla stessa persona: niente history condivisa tra ospiti sconosciuti.
        with self._brain_lock:
            reply = respond_to_guest(text)
        if quarantined:
            reply = (
                f"{reply} Terrò questa informazione separata e chiederò a {_OWNER_NAME} "
                "di confermarla."
            )

        self.memory.log_conversation("Ospite non identificato", text)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        logger.info(f"Guest mode: turno isolato — '{text[:70]}'")
        self._speak(reply, opens_conversation=False)

    def _guest_review_blocked(self) -> bool:
        return any((
            self._teach_recovery_mode,
            self._teach_mode,
            self._teach_confirm_mode,
            self._audit_confirm_mode,
            self._pending_todo is not None,
            self._pending_reschedule is not None,
            self._pending_action is not None,
            self._pending_readback is not None,
            self._pending_write is not None,
            self._awaiting_reaction is not None,
            self._awaiting_memory_verification is not None,
        ))

    def _offer_next_guest_claim(self) -> bool:
        """Chiede a Stefano un solo verdetto per volta, mai a un ospite."""
        if (
            not getattr(self, "_running", True)
            or self._pending_guest_review
            or time.time() < self._guest_review_cooldown_until
            or self._guest_review_blocked()
        ):
            return False
        pending = self.guest_claims.pending(limit=1)
        if not pending:
            return False
        claim = pending[0]
        self._pending_guest_review = _PendingState(claim, timeout=5 * 60)
        self._speak(
            "Una persona non identificata mi ha riferito questa informazione: "
            f"{claim['claim']}. È corretta?"
        )
        return True

    _GUEST_REVIEW_YES = re.compile(
        r"\b(s[iì]|esatto|corretto|confermo|va\s+bene|salvala|memorizzala)\b",
        re.IGNORECASE,
    )
    _GUEST_REVIEW_NO = re.compile(
        r"\b(no|falso|sbagliat[oa]|non\s+[èe]\s+corrett[oa]|scartala|eliminala)\b",
        re.IGNORECASE,
    )
    _GUEST_REVIEW_LATER = re.compile(
        r"\b(pi[uù]\s+tardi|dopo|non\s+ora|non\s+lo\s+so|da\s+verificare)\b",
        re.IGNORECASE,
    )

    def _handle_pending_guest_review(self, text: str) -> None:
        pending = self._pending_guest_review
        if pending is None:
            return
        claim = dict(pending.data)
        claim_id = str(claim.get("id") or "")

        if self._GUEST_REVIEW_YES.search(text):
            confirmed_at = time.time()
            content = (
                f"{_OWNER_NAME} conferma come corretta un'informazione riferita da un "
                f"interlocutore non identificato: {claim.get('claim', '')}"
            )
            temporal_context = {
                "schema_version": 1,
                "asserted_at": confirmed_at,
                "event_start": None,
                "event_end": None,
                "origin_actor_id": "unknown",
                "confirmed_by_actor_id": _OWNER_ID,
                "guest_claim_id": claim_id,
                "guest_reported_at": claim.get("observed_at"),
            }
            mid = self.memory.save_memory(
                content,
                category="conoscenza",
                tags=["guest_confirmed"],
                source="user",
                idempotent=True,
                memory_kind="semantic_fact",
                temporal_context=temporal_context,
            )
            if mid:
                self.guest_claims.settle(
                    claim_id,
                    "confirmed",
                    reviewed_by=_OWNER_ID,
                    promoted_memory_id=mid,
                )
                reply = "Confermata. Ora è in memoria con la provenienza dell'ospite."
            else:
                reply = "Non sono riuscita a salvarla. La lascio in attesa."
            self._pending_guest_review = None
            self._guest_review_cooldown_until = time.time() + 5 * 60
        elif self._GUEST_REVIEW_NO.search(text):
            self.guest_claims.settle(claim_id, "rejected", reviewed_by=_OWNER_ID)
            self._pending_guest_review = None
            self._guest_review_cooldown_until = time.time() + 5 * 60
            reply = "Scartata. Non entrerà nella memoria."
        elif self._GUEST_REVIEW_LATER.search(text):
            self._pending_guest_review = None
            self._guest_review_cooldown_until = time.time() + 30 * 60
            reply = "Va bene, resta in quarantena e te la richiederò più avanti."
        else:
            reply = "Per questa verifica dimmi sì, no oppure più tardi."

        self.memory.log_conversation(_OWNER_NAME, text)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self._speak(reply)

    def _handle_memory_scope_command(self, text: str) -> bool:
        """Controllo deterministico del confine personale/sperimentale."""
        from core.memory_scope import (
            active_scope_state,
            parse_scope_command,
            start_experiment,
            stop_experiment,
        )

        command = parse_scope_command(text)
        if command is None:
            return False
        if command.action == "start":
            if not command.label:
                self._speak(
                    "Dammi un nome per la sessione sperimentale, per esempio Progetto Alfa."
                )
                return True
            if self._awaiting_reaction or self._awaiting_memory_verification:
                self._clear_pending_continuity()
            state = start_experiment(
                self.r,
                command.label,
                ttl_seconds=getattr(
                    config, "MEMORY_EXPERIMENT_SCOPE_TTL_SECONDS", 24 * 3600
                ),
            )
            self.turn_store.restore_into(self.brain, state.get("scope"))
            # Una domanda proattiva o una verifica nata nella memoria personale
            # non può ricevere risposta dentro lo scenario appena aperto.
            if self._awaiting_reaction:
                self.present.clear_pending_question(
                    self._awaiting_reaction.data.get("question_id")
                )
            if self._awaiting_memory_verification:
                self.present.clear_pending_question(
                    self._awaiting_memory_verification.data.get("question_id")
                )
            self._awaiting_reaction = None
            self._awaiting_memory_verification = None
            logger.info(
                "Scope memoria: sessione sperimentale attiva scope={} label={!r}",
                state["scope"],
                state["label"],
            )
            self._speak(
                f"Sessione sperimentale {state['label']} attiva. "
                "Da ora ricordi e turni restano separati dalla memoria personale. "
                "La chiuderò automaticamente entro ventiquattro ore se non lo fai tu."
            )
            return True
        if command.action == "stop":
            previous = stop_experiment(self.r)
            self.turn_store.restore_into(self.brain, "personal")
            if previous.get("active"):
                logger.info(
                    "Scope memoria: sessione sperimentale chiusa scope={}",
                    previous.get("scope"),
                )
                self._speak(
                    f"Sessione sperimentale {previous.get('label')} chiusa. "
                    "Sono tornata alla memoria personale."
                )
            else:
                self._speak("Non c'era una sessione sperimentale attiva.")
            return True

        state = active_scope_state(self.r)
        if state.get("active"):
            self._speak(
                f"Siamo nella sessione sperimentale {state.get('label')}. "
                "La memoria personale resta separata."
            )
        else:
            self._speak("Siamo nella memoria personale ordinaria.")
        return True

    def _dispatch(
        self,
        text: str,
        detected_lang: str = "it",
        *,
        trusted: bool = False,
        observed_at: float | None = None,
        semantic_frame: dict | None = None,
        owner_authenticated: bool = False,
    ):
        """Applica lo scope conversazionale anche ai percorsi anticipati/pending."""
        from core.memory_scope import get_active_scope, use_memory_scope

        with use_memory_scope(get_active_scope(self.r)):
            return self._dispatch_scoped(
                text,
                detected_lang=detected_lang,
                trusted=trusted,
                observed_at=observed_at,
                semantic_frame=semantic_frame,
                owner_authenticated=owner_authenticated,
            )

    def _apply_semantic_canonicalizations(self, frame: dict) -> dict:
        """Propaga nel contesto gli alias gia' confermati dal frame accettato."""
        if not frame.get("canonicalizations"):
            return frame
        from core.memory_scope import normalize_scope

        scope = normalize_scope(frame.get("memory_scope"))
        changed = self.brain.rewrite_entity_aliases(
            lambda value: self.semantic_turns.registry.canonicalize(value, scope)
        )
        logger.info(
            "Turno semantico: identita' aggiornata in {} messaggi recenti",
            changed,
        )
        return frame

    def _interpret_semantic_turn(self, text: str) -> dict:
        """Interpreta una volta il turno e aggiorna gli alias gia' in-flight."""
        from core.memory_scope import current_scope

        scope = current_scope()
        with self.brain.history_lock:
            recent_history = list(self.brain._conversation_history)
        runtime_context = self.executor.document_action_state_context()
        with self._brain_lock:
            frame = self.semantic_turns.interpret(
                text,
                recent_history=recent_history,
                memory_scope=scope,
                runtime_context=runtime_context,
            )
        return self._apply_semantic_canonicalizations(frame)

    def _interpret_semantic_bootstrap(self, text: str) -> dict:
        """Frame pre-gate puro: nessuna correzione viene persistita prima dell'accept."""
        from core.memory_scope import get_active_scope

        scope = get_active_scope(self.r)
        with self.brain.history_lock:
            recent_history = list(self.brain._conversation_history)
        runtime_context = self.executor.document_action_state_context()
        with self._brain_lock:
            return self.semantic_turns.interpret(
                text,
                recent_history=recent_history,
                memory_scope=scope,
                session_bootstrap=True,
                persist_corrections=False,
                runtime_context=runtime_context,
            )

    def _dispatch_scoped(
        self,
        text: str,
        detected_lang: str = "it",
        *,
        trusted: bool = False,
        observed_at: float | None = None,
        semantic_frame: dict | None = None,
        owner_authenticated: bool = False,
    ):
        """Smista il testo trascritto all'handler corretto."""
        if self._handle_memory_scope_command(text):
            return
        if self._pending_guest_review:
            if self._pending_guest_review.expired():
                self._pending_guest_review = None
                logger.debug("Guest review pending scaduta; claim ancora in quarantena")
            else:
                self._handle_pending_guest_review(text)
                return

        if self.memory.is_silent_mode():
            # Anche in silenzio: ripristino e shutdown passano sempre
            _intent_check, _ = classify(text)
            if _intent_check not in (Intent.RESTORE_ALERTS, Intent.SHUTDOWN):
                logger.debug("Modalità silenziosa attiva — input ignorato")
                return

        if self._pending_action:
            if self._pending_action.expired():
                self._pending_action = None
                logger.debug("Action confirmation pending scaduta")
            else:
                self._handle_pending_action(text)
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

        # Rilettura pending: memoria appena riletta, attesa correzione/aggiunta (180s)
        if self._pending_readback:
            if self._pending_readback.expired():
                self.present.clear_pending_question(
                    f"readback:{self._pending_readback.data.get('id')}"
                )
                self._pending_readback = None
                logger.debug("Readback pending scaduto (timeout 180s)")
            else:
                self._handle_pending_readback(text)
                return

        # Riprogrammazione pending: Euri ha chiesto "a quando lo sposto?" (timeout 120s)
        if self._pending_reschedule:
            if self._pending_reschedule.expired():
                self._pending_reschedule = None
                logger.debug("Reschedule pending scaduto (timeout 120s)")
            else:
                self._handle_pending_reschedule(text)
                return

        # Write pending: attende conferma/dettagli/annullamento (timeout 120s)
        if self._pending_write:
            if self._pending_write.expired():
                self._pending_write = None
                logger.debug("Write pending scaduto (timeout 120s)")
            else:
                self._handle_pending_write(text)
                return

        # Reazione pending: Euri ha chiesto conferma su un suo insight (curiosità) e attende
        # la risposta di Stefano per trasformarla in lezione ri-sognabile (timeout 300s).
        if self._awaiting_reaction:
            if self._awaiting_reaction.expired():
                self.present.clear_pending_question(
                    self._awaiting_reaction.data.get("question_id")
                )
                self._awaiting_reaction = None
                self._clear_pending_continuity()
                logger.debug("Reaction pending scaduta")
            else:
                if self._handle_reaction(text):
                    return

        if self._awaiting_memory_verification:
            if self._awaiting_memory_verification.expired():
                self.present.clear_pending_question(
                    self._awaiting_memory_verification.data.get("question_id")
                )
                self._awaiting_memory_verification = None
                self._clear_pending_continuity()
                logger.debug("Verifica memoria passiva scaduta")
            elif self._handle_memory_verification(text):
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

        # Domande sul recente ascolto sono introspezione operativa, non CHAT:
        # la causa viene dal reason code della trace e non puo' essere riscritta
        # dal modello (caso reale SpeakerAuth verificato raccontato come rifiutato).
        if self._handle_voice_perception_question(
            text,
            trusted=trusted,
            observed_at=observed_at,
        ):
            return

        # Unica interpretazione operativa post-STT. Le modalita' pending,
        # traduzione e dettatura sono gia' uscite sopra per preservare il verbatim.
        from core.memory_scope import current_scope as _current_memory_scope
        self.turn_store.sync_into(self.brain, _current_memory_scope())
        if semantic_frame is None:
            semantic_frame = self._interpret_semantic_turn(text)
        else:
            semantic_frame = self.semantic_turns.commit_precomputed(semantic_frame)
            semantic_frame = self._apply_semantic_canonicalizations(semantic_frame)
        if owner_authenticated:
            # È un attributo del canale accettato, distinto dalla wake word:
            # un follow-up del proprietario dentro la lease resta una sua
            # asserzione anche se non ripete "Euri".
            semantic_frame = dict(semantic_frame)
            semantic_frame["accepted_owner_turn"] = True
        text = str(semantic_frame.get("interpreted_text") or text)

        # Loop 2k usa lo stesso frame gia' prodotto per il turno. Una richiesta
        # esplicita parte; un'opportunita' rilevata da Euri resta una proposta e
        # attende un CONFIRM semantico separato. Nessun trigger lessicale.
        if self._handle_semantic_ideation(
            text,
            semantic_frame,
            trusted=trusted,
            observed_at=observed_at,
            owner_authorized=bool(
                owner_authenticated
                or trusted
                or semantic_frame.get("accepted_owner_turn") is True
            ),
        ):
            return

        # "Scrivilo / salvalo" dopo una risposta lunga → usa il contenuto dell'ultima risposta (TTL 300s)
        if (self._last_speech_content
                and time.time() - self._last_speech_ts < 300
                and self._SAVE_REPLY_RE.search(text)):
            from agent.tools.text_writer import tool_write_text
            self.memory.log_conversation(_OWNER_NAME, text)
            res = tool_write_text({"text": self._last_speech_content})
            if res.success:
                fname = res.raw_data.get("filepath", "file").split("/")[-1]
                reply = f"Salvato in {fname}."
            else:
                reply = "Errore nel salvataggio."
            self._last_speech_content = ""
            self._last_speech_ts = 0.0
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self._speak(reply)
            return

        # Briefing di curiosità (trigger/bootstrap): Stefano tira fuori i "sogni" di Euri →
        # lei chiede da bambina se un suo insight non-groundato è vero ("è vero che...?").
        # NON è ancora la primitiva di curiosità (sarebbe Euri a iniziare da sola): è
        # l'impalcatura che genera le risposte da cui impararla. La reazione di Stefano
        # rientra poi via _awaiting_reaction → _handle_reaction.
        # Pre-filtro largo (recall): la frase accenna a sogni/pensieri? Se sì, il MODELLO
        # decide se è davvero una richiesta sui sogni di Euri e ne estrae il TEMA — capendo,
        # non contando connettori (così "cosa hai sognato ULTIMAMENTE" non scambia l'avverbio
        # per un tema, e "hai pensato al Poseidon?" funziona senza la frase-magica).
        from core.memory_scope import current_scope, is_experimental
        if (
            not is_experimental(current_scope())
            and self._BRIEFING_HINT_RE.search(text)
        ):
            _is_briefing, _topic = self._understand_briefing(text)
            if _is_briefing:
                self.memory.log_conversation(_OWNER_NAME, text)
                self._handle_dream_briefing(topic=_topic)
                return

        # "Aprilo / apri la bozza appena creata": apre l'ultimo file prodotto da
        # Euri (xdg-open). Prima della classify perché "apri ... documento" verrebbe
        # catturato da read_document (cartella di input), non dall'artefatto creato.
        if self._last_created_file and time.time() - self._last_created_file_ts < 600:
            from core.utterance_pragmatics import is_open_created_file_request
            if is_open_created_file_request(text):
                self.memory.log_conversation(_OWNER_NAME, text)
                self._handle_open_file(text)
                return

        # Workflow composto (planner): "leggi il documento, riassumilo e preparami
        # una bozza di mail, non inviarla". Pre-gate economico → planner; se è un
        # vero multi-step (≥2) lo esegue incatenando i tool esistenti, altrimenti
        # torna al dispatch normale (fail-open). Kill-switch in config.
        if config.WORKFLOW_PLANNER_ENABLED:
            from core import workflow_planner
            if workflow_planner.looks_like_workflow(text) and self._handle_workflow(text):
                return

        _t_classify = time.perf_counter()
        intent, _ = classify(text)
        logger.info(f"[TIMING] Intent regex: {(time.perf_counter()-_t_classify)*1000:.0f}ms → {intent.value}")

        semantic_label = semantic_intent(
            semantic_frame,
            minimum_confidence=getattr(
                config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
            ),
        )
        semantic_action_reasoning = semantic_label in {
            "ACTION_REASONING", "EXECUTE", "COMPLETE", "RESCHEDULE",
        } or frame_requests_contextual_action(
            semantic_frame,
            minimum_confidence=getattr(
                config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
            ),
        )
        # Il frame puo' arbitrare gli intent conversazionali. Mutazioni di stato,
        # shutdown e tool di sistema restano invece sotto router/controller
        # deterministici e non acquistano autorita' dal modello.
        semantic_routable = {
            "CHAT", "WEB_SEARCH", "SEARCH", "SAVE_MEMORY", "SAVE_TODO",
            "SAVE_NOTE", "SAVE_LAST", "READ_BACK", "TRANSLATE", "DICTATION",
            "TEACH",
        }
        current_routable = {
            Intent.CHAT, Intent.WEB_SEARCH, Intent.SEARCH, Intent.SAVE_MEMORY,
            Intent.SAVE_TODO, Intent.SAVE_NOTE, Intent.SAVE_LAST, Intent.READ_BACK,
            Intent.TRANSLATE, Intent.DICTATION, Intent.TEACH,
        }
        from core.semantic_turn import arbitrate_routable_intent
        shared_label = arbitrate_routable_intent(
            semantic_frame,
            intent,
            allowed=semantic_routable,
            minimum_confidence=getattr(
                config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
            ),
        )
        if shared_label != intent.value and intent in current_routable:
            intent = Intent(shared_label)
            logger.info("Intent condiviso dal frame semantico: {}", intent.value)

        gated_teach = gate_teaching_route(
            semantic_frame,
            intent,
            minimum_confidence=getattr(
                config, "SEMANTIC_TEACH_MIN_CONFIDENCE", 0.82
            ),
        )
        if gated_teach != intent.value:
            logger.info(
                "TEACH fail-closed: {} → {} (status={}, semantic={})",
                intent.value,
                gated_teach,
                semantic_frame.get("status") or "unknown",
                semantic_label or "unknown",
            )
            intent = Intent(gated_teach)

        action_checked = False
        action_veto = False
        contextual_action_candidate = (
            looks_actionable(text) or semantic_action_reasoning
        )
        frame_action_veto = contextual_action_candidate and frame_vetoes_contextual_action(
            semantic_frame,
            minimum_confidence=getattr(
                config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
            ),
        )
        if frame_action_veto:
            contextual_action_candidate = False
            if semantic_action_reasoning:
                action_veto = True
            if intent == Intent.EXECUTE:
                # Il router lessicale non può scavalcare un frame affidabile che
                # descrive soltanto una risposta, una negazione o un'ipotesi.
                intent = Intent.CHAT
            logger.info(
                "ActionController evitato dal frame semantico: acts={}",
                ",".join(semantic_frame.get("speech_acts") or []) or "-",
            )
        if _should_try_contextual_action(intent, contextual_action_candidate):
            action_checked = True
            handled, action_veto = self._try_contextual_action(
                text,
                trusted=trusted,
                observed_at=observed_at,
                semantic_frame=semantic_frame,
            )
            if handled:
                return
            if semantic_action_reasoning and intent == Intent.EXECUTE:
                # Un EXECUTE già compreso semanticamente non torna al router
                # legacy/regex. Se il controller ha riconosciuto un semplice gesto
                # linguistico, rientra in CHAT; se ha fallito, action_veto conserva
                # il fail-closed più sotto.
                intent = Intent.CHAT
            if intent in {Intent.COMPLETE, Intent.RESCHEDULE}:
                # Una mutazione non ricade mai sugli handler legacy se il
                # controller non ha prodotto/eseguito una decisione grounded.
                # Guasto del modello e veto semantico sono entrambi fail-closed.
                action_veto = True
                intent = Intent.CHAT

        # Fallback LLM per intent critici non catturati dalle regex
        if intent == Intent.CHAT and semantic_frame.get("status") != "interpreted":
            from core.llm_classifier import llm_fallback_classify
            _t_llm_cls = time.perf_counter()
            fallback = llm_fallback_classify(text)
            logger.info(f"[TIMING] LLM classifier: {(time.perf_counter()-_t_llm_cls)*1000:.0f}ms → {fallback or 'CHAT'}")
            if fallback:
                if fallback == "ACTION_REASONING":
                    fallback_intent = Intent.CHAT
                    if not action_checked:
                        handled, veto = self._try_contextual_action(
                            text,
                            trusted=trusted,
                            observed_at=observed_at,
                            semantic_frame=semantic_frame,
                        )
                        action_checked = True
                        if handled:
                            return
                        action_veto = action_veto or veto
                else:
                    fallback_intent = Intent(fallback)
                if fallback_intent in {Intent.COMPLETE, Intent.RESCHEDULE}:
                    if action_veto:
                        fallback_intent = Intent.CHAT
                    elif not action_checked:
                        handled, veto = self._try_contextual_action(
                            text,
                            trusted=trusted,
                            observed_at=observed_at,
                            semantic_frame=semantic_frame,
                        )
                        action_checked = True
                        if handled:
                            return
                        # Nessun fallback mutante fuori dal controller, anche
                        # quando la proposta manca per errore/timeout del modello.
                        action_veto = True
                        fallback_intent = Intent.CHAT
                intent = fallback_intent

        if (
            intent in {Intent.CHAT, Intent.EXECUTE}
            and action_veto
            and semantic_action_reasoning
        ):
            # Un REQUEST_ACTION compreso semanticamente non puo' degradare a CHAT:
            # Gemma altrimenti puo' narrare al futuro un'operazione che nessun tool
            # ha eseguito. Il fallimento resta osservabile e senza effetti.
            reply = (
                "Non ho eseguito nulla: ho capito che mi stai chiedendo un'azione, "
                "ma non sono riuscito a collegarla con sufficiente certezza a uno "
                "strumento reale. Riformula il risultato che vuoi ottenere."
            )
            self.memory.log_conversation(_OWNER_NAME, text)
            self.memory.log_conversation(_ASSISTANT_NAME, reply)
            self.brain.record_context_message(
                "user",
                text,
                trusted=trusted,
                observed_at=observed_at,
                raw_content=(semantic_frame or {}).get("raw_text"),
                semantic_frame=semantic_frame,
            )
            self.brain.record_context_message(
                "assistant",
                reply,
                trusted=trusted,
            )
            self._speak(reply)
            logger.info("REQUEST_ACTION fail-closed dopo veto del controller")
            return

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
            Intent.RESCHEDULE: self._handle_reschedule,
            Intent.READ_BACK: self._handle_read_back,
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
        if intent in {Intent.CHAT, Intent.SEARCH}:
            handler(
                text,
                trusted=trusted,
                observed_at=observed_at,
                semantic_frame=semantic_frame,
                semantic_action_veto=frame_action_veto,
            )
        elif intent == Intent.WEB_SEARCH:
            handler(text, semantic_frame=semantic_frame)
        elif intent == Intent.TEACH:
            handler(
                text,
                trusted=trusted,
                observed_at=observed_at,
                semantic_frame=semantic_frame,
            )
        else:
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
        from core.validator import validate_passive_payload
        IDLE_TRIGGER = 45       # secondi di silenzio prima di analizzare (era 60)
        MIN_NEW_EXCHANGES = 1   # scambi minimi dall'ultima analisi (era 2)

        while self._running:
            self._workers.heartbeat("passive-learner")
            if self._wait_or_stop(20):
                break
            try:
                # Solo se c'è stata attività recente che ora è ferma
                if self._last_activity_ts == 0:
                    continue
                idle = time.time() - self._last_activity_ts
                if idle < IDLE_TRIGGER:
                    continue

                # Journal append-only fino all'ack: la compressione della history
                # conversazionale non può più invalidare il cursore del learner.
                new_history = self.brain.passive_messages_after(self._passive_last_seq)
                new_exchanges = len(new_history)
                if new_exchanges < MIN_NEW_EXCHANGES:
                    continue

                if not new_history:
                    continue
                through_seq = new_history[-1]["seq"]

                # Il substrato originale deve essere durevole PRIMA che una
                # memoria passiva possa pubblicarne i riferimenti. Su errore
                # l'eccezione impedisce l'ack e il journal verrà ritentato.
                self.turn_store.persist_many(new_history)

                saved = 0
                extracted = 0
                validated = 0
                rejected = 0
                duplicates = 0
                eligible_history = self._passive_memory_eligible_history(new_history)
                policy_blocked = len(new_history) - len(eligible_history)
                # Non mischiare nel medesimo prompt scambi rivolti esplicitamente a
                # Euri e parlato ambient: un solo messaggio trusted non deve rendere
                # FORTE un fatto estratto dalla parte ambient del batch.
                for segment_addressed, segment_history in self._passive_extraction_batches(eligible_history):
                    from core.memory_scope import normalize_scope
                    segment_scope = normalize_scope(
                        segment_history[0].get("memory_scope")
                        if segment_history else None
                    )
                    facts = self.brain.extract_passive_memories(segment_history) or []
                    for fact_item in facts:
                        extracted += 1
                        from core.temporal_context import derive_passive_memory_metadata
                        support = fact_item.get("support") if isinstance(fact_item, dict) else None
                        weak_support = self._passive_weak_support(support, segment_addressed)
                        fact = fact_item.get("content", "") if isinstance(fact_item, dict) else str(fact_item)
                        # Ultimo confine contro la race: anche se il batch e' stato
                        # fotografato prima della correzione, il fatto estratto
                        # viene ricondotto allo stato entita' corrente prima del save.
                        fact = self.semantic_turns.registry.canonicalize(
                            fact, segment_scope
                        )
                        if isinstance(fact_item, dict):
                            fact_item = dict(fact_item, content=fact)
                        clean = validate_passive_payload(fact)
                        if not clean:
                            rejected += 1
                            continue
                        audit_candidate = (
                            dict(fact_item)
                            if isinstance(fact_item, dict)
                            else {
                                "content": clean,
                                "memory_kind": "semantic_fact",
                                "source_turn_ids": [],
                            }
                        )
                        audit_candidate["content"] = clean
                        audited_item = self.brain.audit_passive_memory_provenance(
                            audit_candidate,
                            segment_history,
                        )
                        if not audited_item:
                            rejected += 1
                            continue
                        provenance_audit = dict(
                            audited_item.get("provenance_audit") or {}
                        )
                        metadata = derive_passive_memory_metadata(
                            audited_item,
                            segment_history,
                        )
                        clean = metadata["canonical_content"]
                        validated += 1
                        if self.memory.is_duplicate_memory(
                            clean,
                            llm_probe_fn=self.brain.probe_same_meaning,
                            memory_scope=segment_scope,
                        ):
                            duplicates += 1
                            continue
                        final_fields = {
                            "passive_provenance": provenance_audit,
                        }
                        if weak_support:
                            final_fields.update(
                                {
                                    "requires_verification": True,
                                    "passive_support": "tacit_acceptance",
                                }
                            )
                        else:
                            final_fields.update(
                                {
                                    "requires_verification": False,
                                    "passive_support": "owner_asserted",
                                    "epistemic_status": "user_asserted",
                                }
                            )
                        mid = self.memory.save_memory(
                            clean,
                            category="passivo",
                            source="passive",
                            idempotent=True,
                            memory_kind=metadata["memory_kind"],
                            temporal_context=metadata["temporal_context"],
                            final_fields=final_fields,
                            memory_scope=segment_scope,
                        )
                        if mid and (weak_support or metadata["memory_kind"] == "conversation_anchor"):
                            from core.memory_attention import remove_loop2e_candidate
                            remove_loop2e_candidate(self.memory.r, mid)
                        saved += 1

                if saved:
                    logger.info(f"Passive learner: {saved} fatto/i salvato/i silenziosamente")
                logger.info(
                    "Passive learner: pass completato "
                    f"messaggi={new_exchanges} esclusi_policy={policy_blocked} "
                    f"estratti={extracted} "
                    f"validati={validated} scartati={rejected} "
                    f"duplicati={duplicates} salvati={saved}"
                )
                self._passive_last_seq = through_seq
                self.brain.ack_passive_messages(through_seq)

            except Exception as e:
                logger.error(f"Errore passive learner: {e}")

    def _consolidation_loop(self):
        """
        Loop 2a — consolidamento silenzioso in idle.
        Usa un checkpoint durevole e un solo segmento/sessione coerente. Se un turno
        comincia durante l'elaborazione, la pubblicazione viene annullata.
        """
        from datetime import timedelta
        from core.pulse import cognitive_emit
        from core.reflection_policy import (
            LOOP2A_CHECKPOINT_KEY,
            latest_reflection_checkpoint,
            reflection_parent_ids,
            select_reflection_session,
        )
        from utils.date_utils import now as _now

        IDLE_TRIGGER = 5 * 60     # una pausa nel dialogo non è ancora idle cognitivo
        CHECK_INTERVAL = 60       # polling
        MIN_RUN_INTERVAL = 1800   # almeno 30 min tra una run e la successiva
        MIN_MEMORIES = 3

        while self._running:
            self._workers.heartbeat("consolidation")
            if self._wait_or_stop(CHECK_INTERVAL):
                break
            try:
                if self._last_activity_ts == 0:
                    continue
                idle = time.time() - self._last_activity_ts
                if idle < IDLE_TRIGGER:
                    continue
                if (time.time() - self._consolidation_last_run) < MIN_RUN_INTERVAL:
                    continue
                if self._voice_input_inflight.is_set() or self.r.exists("euri:audio:lock"):
                    continue
                activity_snapshot = self._last_activity_ts
                snapshot_at = time.time()

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

                raw_checkpoint = self.r.get(LOOP2A_CHECKPOINT_KEY)
                if isinstance(raw_checkpoint, bytes):
                    raw_checkpoint = raw_checkpoint.decode("utf-8", errors="replace")
                try:
                    checkpoint = float(raw_checkpoint)
                except (TypeError, ValueError):
                    checkpoint = latest_reflection_checkpoint(
                        all_memories, self._consolidation_boot_ts
                    )
                    self.r.set(LOOP2A_CHECKPOINT_KEY, checkpoint, nx=True)

                session_mems = select_reflection_session(
                    all_memories,
                    checkpoint=checkpoint,
                    snapshot_at=snapshot_at,
                )
                if len(session_mems) < MIN_MEMORIES:
                    self._consolidation_last_run = time.time()
                    continue

                # Memorie correlate via ricerca semantica sul testo della sessione
                session_text = " ".join(
                    (m.get("content") or "")[:120] for m in session_mems
                )
                session_ids = {m.get("id") for m in session_mems}
                related = [
                    m for m in self.memory.search_memories(session_text, limit=7)
                    if m.get("id") not in session_ids and m.get("source") != "web"
                ][:5]

                # Non accodare un altro lavoro Ollama quando il dialogo sta usando
                # il modello. Il lock resta preso durante la singola generazione.
                if not self._brain_lock.acquire(blocking=False):
                    continue
                try:
                    if (
                        self._last_activity_ts != activity_snapshot
                        or self._voice_input_inflight.is_set()
                    ):
                        continue
                    reflection = self.brain.generate_reflection(session_mems, related)
                finally:
                    self._brain_lock.release()

                if (
                    self._last_activity_ts != activity_snapshot
                    or self._voice_input_inflight.is_set()
                ):
                    logger.info(
                        "Loop 2a: snapshot invalidato da nuova attività; reflection non pubblicata"
                    )
                    continue

                processed_through = max(
                    float(memory.get("created_at") or 0) for memory in session_mems
                )
                if not reflection:
                    self.r.set(LOOP2A_CHECKPOINT_KEY, processed_through)
                    self._consolidation_last_run = time.time()
                    continue

                parent_ids = reflection_parent_ids(session_mems, related)
                latest_temporal = session_mems[-1].get("temporal_context") or {}
                expires = _now() + timedelta(days=7)

                def _snapshot_still_valid():
                    return (
                        self._last_activity_ts == activity_snapshot
                        and not self._voice_input_inflight.is_set()
                    )

                mid = self.memory.save_memory(
                    reflection,
                    category="riflessione",
                    source="reflection",
                    expires_at=expires,
                    final_fields={
                        "requires_verification": True,
                        "epistemic_status": "internal_reflection",
                        "source_memory_ids": parent_ids,
                        "session_memory_ids": [
                            str(memory.get("id")) for memory in session_mems
                            if memory.get("id")
                        ],
                        "related_memory_ids": [
                            str(memory.get("id")) for memory in related
                            if memory.get("id")
                        ],
                        "reflection_scope": {
                            "conversation_id": latest_temporal.get("conversation_id"),
                            "segment_id": latest_temporal.get("segment_id"),
                            "checkpoint_before": checkpoint,
                            "processed_through": processed_through,
                            "snapshot_at": snapshot_at,
                        },
                    },
                    precommit_guard=_snapshot_still_valid,
                )
                if mid:
                    try:
                        doc = self.r.json().get(f"euri:memory:{mid}", "$")
                        domain = doc[0].get("domain", "generale") if doc else "generale"
                        self.memory.supersede_duplicate_reflections(mid, domain, reflection)
                    except Exception as dedup_err:
                        logger.debug(f"Loop 2a reflection dedup error: {dedup_err}")
                    cognitive_emit(
                        self.r,
                        "reflection",
                        "intero",
                        "created",
                        producer="loop2a",
                        trace_id=f"reflection:{mid}",
                        logical_event_id=f"reflection:{mid}",
                        entity_refs=[{"type": "memory", "id": mid, "role": "child"}],
                        parent_refs=parent_ids,
                        payload={
                            "id": mid,
                            "session_parent_ids": [
                                str(memory.get("id")) for memory in session_mems
                                if memory.get("id")
                            ],
                            "related_parent_ids": [
                                str(memory.get("id")) for memory in related
                                if memory.get("id")
                            ],
                        },
                        epistemic_before="source_memories",
                        epistemic_after="internal_reflection_requires_verification",
                        salience=0.4,
                    )
                    self.r.set(LOOP2A_CHECKPOINT_KEY, processed_through)
                    logger.info("Loop 2a: reflection salvata silenziosamente")
                else:
                    logger.info(
                        "Loop 2a: reflection non pubblicata; checkpoint invariato"
                    )
                self._consolidation_last_run = time.time()

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

    def _formulate_reminder(self, todo: dict, minutes_left: float) -> str:
        """Promemoria FORMULATO naturalmente da Gemma, grounded sul contenuto del todo:
        tesse la consegna, NON inventa la sostanza. Fallback non-template se l'LLM tace."""
        content = (todo.get("content") or "").strip()
        overdue = minutes_left < -1
        timing = ("È già passata l'ora in cui voleva farlo: chiedigli con garbo se è riuscito."
                  if overdue else "Scade tra poco.")
        prompt = (
            f"Sei {_ASSISTANT_NAME}, l'assistente di {_OWNER_NAME}. L'utente ti aveva "
            f"chiesto di ricordargli questo:\n"
            f"«{content}»\n\n"
            f"{timing}\n"
            "Diglielo come glielo diresti DI PERSONA: UNA frase, naturale e amichevole, "
            "nessun elenco, nessun preambolo tipo 'Promemoria:' o 'Scaduto:'. "
            "NON aggiungere fatti, numeri o dettagli che non sono già nel testo qui sopra."
        )
        try:
            with self._brain_lock:
                resp = chat_client.chat(
                    model=config.OLLAMA_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.6, "num_predict": 200},
                    think=False,
                )
            line = (resp.message.content or "").strip()
            if line:
                return line
        except Exception as e:
            logger.debug(f"_formulate_reminder fallback: {e}")
        return (f"Senti, ti ricordo: {content}" if not overdue
                else f"Prima volevi {content[:120]} — ci sei riuscito?")

    CLOCK_EMITTED_KEY = "euri:pulse:clock_emitted"

    def _mark_clock_emitted(self, tid) -> bool:
        """True solo alla PRIMA emissione clock/threshold per questo todo.

        Dedup persistito su un set Redis, così un riavvio del daemon non ri-emette il
        clock già visto (il set in-memory si azzerava al restart → dup sullo stream Pulse).
        Fail-open: se Redis è irraggiungibile, ricade sul set in-memory — la consegna NON
        dipende da qui (usa reminded_count), quindi al peggio ricompare una dup afferente.
        """
        if not tid:
            return True
        try:
            return bool(self.r.sadd(self.CLOCK_EMITTED_KEY, str(tid)))
        except Exception:
            if tid in self._clock_emitted:
                return False
            self._clock_emitted.add(tid)
            return True

    def _reminder_loop(self):
        """
        Thread proattivo (ogni 30s). Due piani distinti:
          AFFERENTE (clock): quando una scadenza entra in finestra (<=60 min) emette UNA volta
            euri:pulse clock/threshold — il tempo del mondo, osservato sempre.
          EFFERENTE (consegna): ricorda UNA sola volta e SOLO quando STEFANO è presente —
            non "qualcuno": il laboratorio di notte è dei capoturno. Presenza di Stefano =
            RICONOSCIUTO in faccia (is_owner_present, FaceAuth) OPPURE voce AUTENTICATA di
            recente (SpeakerAuth). Il vecchio is_user_present (una faccia qualunque) non
            basta più per parlare. Presenza IGNOTA ≠ presente → non si consegna nel vuoto:
            si attende e si consegna al primo momento di presenza, anche dopo la scadenza.
            La frase è FORMULATA da Gemma (grounded sul todo), non un template a livelli.
        """
        LEAD_MIN = 15              # consegna da 15 min prima della scadenza
        OVERDUE_CAP_MIN = 24 * 60  # ...fino a 24h dopo, in attesa di presenza
        PRESENT_WINDOW = 300       # "interazione recente" = ultimi 5 min

        while self._running:
            self._workers.heartbeat("reminder")
            try:
                t = now()
                if t.hour >= 23 or t.hour < 7:         # quiet hours 23:00–07:00
                    if self._wait_or_stop(30):
                        break
                    continue
                if self.r.exists("euri:audio:lock"):   # Euri sta parlando → aspetta
                    if self._wait_or_stop(5):
                        break
                    continue

                # Presenza di STEFANO: riconosciuto in faccia OPPURE voce autenticata recente.
                seen = self.visual_gate.is_owner_present()
                spoke = (self._last_auth_voice_ts > 0 and
                         (time.time() - self._last_auth_voice_ts) <= PRESENT_WINDOW)
                present = seen or spoke

                for todo in self.memory.get_pending_todos():
                    due = todo.get("_due_at")
                    if not due:
                        continue
                    minutes_left = (due - t).total_seconds() / 60
                    tid = todo.get("id")

                    # AFFERENTE — clock/threshold UNA volta, all'ingresso in finestra (<=60 min).
                    # Dedup persistito su Redis: un restart del daemon non ri-emette il clock già visto.
                    if minutes_left <= 60 and self._mark_clock_emitted(tid):
                        pulse_emit(self.r, "clock", "extero", "threshold",
                                   payload={"content": todo.get("content", "")[:60],
                                            "minutes_left": round(minutes_left),
                                            "present": present},
                                   salience=max(0.3, min(0.95, 1 - minutes_left / 60)))

                    # EFFERENTE — consegna UNA volta, solo se presente, nella finestra
                    if (todo.get("reminded_count", 0) == 0 and present and
                            -OVERDUE_CAP_MIN <= minutes_left <= LEAD_MIN):
                        self.memory.mark_reminded(tid)
                        logger.info(f"Reminder (presente, naturale): {todo.get('content','')[:50]}")
                        self._speak(self._formulate_reminder(todo, minutes_left))
                        break   # un promemoria per giro, non sommergere

            except Exception as e:
                logger.error(f"Errore reminder loop: {e}")

            if self._wait_or_stop(30):
                break

    def _record_ideation_exchange(
        self,
        user_text: str,
        reply: str,
        *,
        semantic_frame: dict | None = None,
        trusted: bool = True,
        observed_at: float | None = None,
    ) -> None:
        """Rende il dialogo visibile senza trasformare l'arena in memoria."""
        self.memory.log_conversation(_OWNER_NAME, user_text)
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self.brain.record_context_message(
            "user",
            user_text,
            trusted=trusted,
            observed_at=observed_at,
            raw_content=(semantic_frame or {}).get("raw_text"),
            semantic_frame=semantic_frame,
        )
        self.brain.record_context_message("assistant", reply, trusted=trusted)

    def _reconcile_ideation_runtime(self) -> None:
        """Rimuove soltanto il lock di un processo che non esiste piu'."""
        key = ideation_active_key(_OWNER_ID)
        active = load_ideation_json(self.r, key)
        if not active:
            return
        try:
            pid = int(active.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        alive = False
        if pid > 0 and pid != os.getpid():
            try:
                os.kill(pid, 0)
                alive = True
            except (ProcessLookupError, PermissionError):
                alive = False
        if not alive:
            self.r.delete(key)
            logger.info("Loop 2k: lock orfano riconciliato al boot")

    def _release_ideation_lock(self, token: str) -> None:
        key = ideation_active_key(_OWNER_ID)
        try:
            current = load_ideation_json(self.r, key)
            if str(current.get("token") or "") == token:
                self.r.delete(key)
        except Exception as exc:
            logger.debug(f"Loop 2k: rilascio lock ignorato ({exc})")

    def _run_ideation_job(self, payload: dict, token: str) -> None:
        """Esegue il lavoro costoso fuori dal path della conversazione."""
        started = time.monotonic()
        try:
            result = self.dream_engine.run_ideation_tournament(
                str(payload.get("problem") or ""),
                grounding_context=str(payload.get("grounding_context") or ""),
                constraints=list(payload.get("constraints") or []),
                source_refs=list(payload.get("source_refs") or []),
                n_candidates=int(getattr(config, "IDEATION_ARENA_DEFAULT_CANDIDATES", 4)),
            )
            reply = format_ideation_result(result)
            delivery = {
                "run_id": str(getattr(result, "run_id", "") or ""),
                "artifact_key": str(getattr(result, "artifact_key", "") or ""),
                "status": str(getattr(result, "status", "") or ""),
                "reply": reply,
                "completed_at": time.time(),
                "delivery_channel": str(
                    payload.get("delivery_channel") or "voice"
                ),
            }
            logger.info(
                "Loop 2k conversazionale: completato status={} in {:.1f}s",
                delivery["status"], time.monotonic() - started,
            )
        except Exception as exc:
            logger.exception("Loop 2k conversazionale fallito")
            reply = (
                "Il confronto si e' interrotto prima di produrre un risultato affidabile. "
                "Non ne ricavo una conclusione; possiamo riprovarlo piu' avanti."
            )
            delivery = {
                "run_id": "", "artifact_key": "", "status": "failed",
                "reply": reply, "completed_at": time.time(),
                "error": type(exc).__name__,
                "delivery_channel": str(
                    payload.get("delivery_channel") or "voice"
                ),
            }
        finally:
            self._release_ideation_lock(token)

        # La conversazione condivisa riceve subito il risultato; la voce attende
        # invece presenza e canale libero nel worker di consegna.
        self.memory.log_conversation(_ASSISTANT_NAME, reply)
        self.brain.record_context_message("assistant", reply, trusted=True)
        try:
            self.r.xadd(
                ideation_ui_stream_key(_OWNER_ID),
                {
                    "reply": reply,
                    "run_id": delivery.get("run_id", ""),
                    "status": delivery.get("status", ""),
                },
                maxlen=20,
                approximate=True,
            )
        except Exception as exc:
            logger.debug(f"Loop 2k: notifica Silent Chat ignorata ({exc})")
        if delivery.get("delivery_channel") != "silent_chat":
            try:
                enqueue_ideation_delivery(
                    self.r,
                    ideation_delivery_key(_OWNER_ID),
                    delivery,
                    ttl_s=getattr(config, "IDEATION_DELIVERY_TTL_S", 86400),
                )
            except Exception as exc:
                logger.warning(f"Loop 2k: consegna non persistita ({exc})")

    def _start_semantic_ideation(
        self,
        payload: dict,
        *,
        user_text: str,
        semantic_frame: dict | None,
        trusted: bool,
        observed_at: float | None,
    ) -> bool:
        """Acquisisce il singolo slot, prepara il grounding e avvia il job."""
        if not getattr(config, "IDEATION_ARENA_ENABLED", False):
            reply = "Il confronto tra ipotesi in questo momento e' disabilitato."
            self._record_ideation_exchange(
                user_text, reply, semantic_frame=semantic_frame,
                trusted=trusted, observed_at=observed_at,
            )
            self._speak(reply)
            return True
        if self._ideation_thread is not None and self._ideation_thread.is_alive():
            reply = "Sto gia' confrontando un altro problema. Ti avviso appena ho finito."
            self._record_ideation_exchange(
                user_text, reply, semantic_frame=semantic_frame,
                trusted=trusted, observed_at=observed_at,
            )
            self._speak(reply)
            return True

        token = str(uuid.uuid4())
        active = {
            "token": token, "pid": os.getpid(), "problem": payload.get("problem", ""),
            "started_at": time.time(),
        }
        try:
            acquired = self.r.set(
                ideation_active_key(_OWNER_ID),
                json.dumps(active, ensure_ascii=False),
                nx=True,
                ex=getattr(config, "IDEATION_ACTIVE_TTL_S", 3600),
            )
        except Exception:
            acquired = False
        if not acquired:
            reply = "C'e' gia' un confronto in corso. Ti avviso appena termina."
            self._record_ideation_exchange(
                user_text, reply, semantic_frame=semantic_frame,
                trusted=trusted, observed_at=observed_at,
            )
            self._speak(reply)
            return True

        try:
            frame = payload.get("semantic_frame") or semantic_frame or {}
            original_text = str(payload.get("user_text") or user_text)
            context = self._build_context(original_text, semantic_frame=frame)
            local = getattr(self, "_response_rag_local", None)
            rag = getattr(local, "rag", None) if local is not None else None
            source_refs = [f"semantic-turn:{frame.get('turn_id', '')}"]
            source_refs.extend(
                f"rag:{item}" for item in (getattr(rag, "ids", None) or [])
            )
            job_payload = dict(payload)
            job_payload.update({
                "grounding_context": context,
                "source_refs": [item for item in source_refs if not item.endswith(":")],
                "delivery_channel": "voice",
            })
        except Exception as exc:
            self._release_ideation_lock(token)
            logger.warning(f"Loop 2k: grounding fallito ({exc})")
            reply = (
                "Non sono riuscita a costruire un contesto abbastanza stabile per il "
                "confronto, quindi non l'ho avviato."
            )
            self._record_ideation_exchange(
                user_text, reply, semantic_frame=semantic_frame,
                trusted=trusted, observed_at=observed_at,
            )
            self._speak(reply)
            return True

        reply = (
            "Va bene. Metto a confronto quattro strade indipendenti e provo anche a "
            "smentirle prima di scegliere. Ci vorranno alcuni minuti; nel frattempo "
            "possiamo continuare a parlare."
        )
        self._record_ideation_exchange(
            user_text, reply, semantic_frame=semantic_frame,
            trusted=trusted, observed_at=observed_at,
        )
        self._speak(reply)
        self._ideation_thread = threading.Thread(
            target=self._run_ideation_job,
            args=(job_payload, token),
            daemon=True,
            name="euri-ideation-job",
        )
        self._ideation_thread.start()
        logger.info(
            "Loop 2k conversazionale: avviato reason={} problem='{}'",
            payload.get("reason", "other"), str(payload.get("problem") or "")[:120],
        )
        return True

    def _handle_semantic_ideation(
        self,
        text: str,
        semantic_frame: dict,
        *,
        trusted: bool,
        observed_at: float | None,
        owner_authorized: bool,
    ) -> bool:
        """Richiesta esplicita o proposta con consenso, entrambe semantiche."""
        if not owner_authorized:
            return False
        pending_key = ideation_pending_key(_OWNER_ID)
        pending = load_ideation_json(self.r, pending_key)
        decision = semantic_pending_decision(
            semantic_frame,
            minimum_confidence=getattr(config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72),
        )
        if pending and decision == "reject":
            self.r.delete(pending_key)
            reply = "Va bene, lascio perdere il confronto e continuiamo normalmente."
            self._record_ideation_exchange(
                text, reply, semantic_frame=semantic_frame,
                trusted=trusted, observed_at=observed_at,
            )
            self._speak(reply)
            return True
        if pending and decision == "confirm":
            self.r.delete(pending_key)
            return self._start_semantic_ideation(
                pending,
                user_text=text,
                semantic_frame=semantic_frame,
                trusted=trusted,
                observed_at=observed_at,
            )

        contract = trusted_deliberation_request(semantic_frame)
        if contract is None:
            return False
        payload = {
            "problem": contract.get("problem", ""),
            "reason": contract.get("reason", "other"),
            "constraints": list(contract.get("constraints") or []),
            "user_text": text,
            "semantic_frame": semantic_frame,
            "created_at": time.time(),
        }
        if contract.get("mode") == "explicit":
            if pending:
                self.r.delete(pending_key)
            return self._start_semantic_ideation(
                payload,
                user_text=text,
                semantic_frame=semantic_frame,
                trusted=trusted,
                observed_at=observed_at,
            )

        if pending:
            reply = (
                "Ho gia' lasciato aperta una proposta di confronto. Puoi confermarla, "
                "rifiutarla oppure continuare il discorso normalmente."
            )
        else:
            store_ideation_json(
                self.r, pending_key, payload,
                ttl_s=getattr(config, "IDEATION_PENDING_TTL_S", 600),
            )
            reply = (
                "Qui vedo almeno due strade realmente diverse e una risposta unica "
                "rischierebbe di nascondere il compromesso. Vuoi che le faccia competere "
                "nel confronto approfondito? Richiedera' alcuni minuti."
            )
        self._record_ideation_exchange(
            text, reply, semantic_frame=semantic_frame,
            trusted=trusted, observed_at=observed_at,
        )
        self._speak(reply)
        return True

    def _ideation_delivery_worker(self) -> None:
        """Pronuncia risultati gia' loggati solo quando l'efferenza e' sicura."""
        key = ideation_delivery_key(_OWNER_ID)
        while self._running:
            self._workers.heartbeat("ideation-delivery")
            delivery = peek_ideation_delivery(self.r, key)
            if delivery and str(delivery.get("reply") or "").strip():
                reason = self._initiative_block_reason(idle_seconds=2, cooldown_s=0)
                if not reason:
                    try:
                        self._speak(str(delivery["reply"]))
                        self.r.lpop(key)
                    except Exception as exc:
                        logger.warning(f"Loop 2k: consegna vocale rinviata ({exc})")
            if self._wait_or_stop(2):
                break

    def _ideation_job_worker(self) -> None:
        """Consuma anche i lavori autorizzati dalla Silent Chat."""
        key = ideation_job_queue_key(_OWNER_ID)
        while self._running:
            self._workers.heartbeat("ideation-job")
            payload = pop_ideation_job(self.r, key)
            if not payload:
                if self._wait_or_stop(2):
                    break
                continue
            token = str(payload.get("token") or "")
            if not token:
                logger.warning("Loop 2k: job in coda privo di token, scartato")
                continue
            active_key = ideation_active_key(_OWNER_ID)
            if not load_ideation_json(self.r, active_key):
                self.r.set(
                    active_key,
                    json.dumps({
                        "token": token, "pid": os.getpid(),
                        "problem": payload.get("problem", ""),
                        "started_at": time.time(),
                    }, ensure_ascii=False),
                    nx=True,
                    ex=getattr(config, "IDEATION_ACTIVE_TTL_S", 3600),
                )
            self._ideation_thread = threading.Thread(
                target=self._run_ideation_job,
                args=(payload, token),
                daemon=True,
                name="euri-ideation-job",
            )
            self._ideation_thread.start()
            while self._ideation_thread.is_alive() and self._running:
                self._workers.heartbeat("ideation-job")
                if self._wait_or_stop(2):
                    break

    def _initiative_block_reason(self, *, idle_seconds: float | None = None,
                                 cooldown_s: float | None = None) -> str:
        """Ritorna "" se Euri può iniziare una domanda proattiva adesso."""
        try:
            from core.memory_scope import get_active_scope, is_experimental
            if is_experimental(get_active_scope(self.r)):
                return "experimental_memory_scope"
        except Exception:
            return "memory_scope_unavailable"
        if self._voice_input_inflight.is_set():
            return "voice_input_inflight"
        try:
            phase = self.present.snapshot().phase
            if phase is not InteractionPhase.LISTENING:
                return f"present_phase:{phase.value}"
        except Exception:
            pass
        if self._awaiting_reaction:
            return "awaiting_reaction"
        if self._awaiting_memory_verification:
            return "awaiting_memory_verification"
        if any([
            self._pending_todo,
            self._pending_reschedule,
            self._pending_action,
            self._pending_readback,
            self._pending_write,
            self._teach_recovery_mode,
            self._teach_mode,
            self._teach_confirm_mode,
            self._translate_bidir,
            self._dictation_mode,
            self._audit_confirm_mode,
            self._enroll_mode,
        ]):
            return "modal_state_active"
        try:
            if self.r.exists("euri:audio:lock"):
                return "audio_lock"
        except Exception:
            pass

        now_ts = time.time()
        idle = (now_ts - self._last_activity_ts) if self._last_activity_ts else 999999
        idle_threshold = (
            getattr(config, "INITIATIVE_IDLE_SECONDS", 90)
            if idle_seconds is None else float(idle_seconds)
        )
        if idle < idle_threshold:
            return f"recent_activity:{idle:.0f}s"

        try:
            last_raw = self.r.get("euri:initiative:last_ask_ts")
            last_ask = float(last_raw or 0)
        except Exception:
            last_ask = 0.0
        cooldown = (
            getattr(config, "INITIATIVE_COOLDOWN_S", 3 * 3600)
            if cooldown_s is None else float(cooldown_s)
        )
        if last_ask and now_ts - last_ask < cooldown:
            return f"cooldown:{int(cooldown - (now_ts - last_ask))}s"

        # Presenza di STEFANO, non di "qualcuno": la faccia del capoturno di notte
        # attiva l'ascolto ma non deve far partire una domanda proattiva. Conta il
        # riconoscimento facciale (owner) o la voce autenticata recente — non
        # _last_activity_ts, che viene rinfrescato anche dal TTS di Euri stessa.
        try:
            seen = self.visual_gate.is_owner_present()
        except Exception:
            seen = False
        spoke_recently = self._last_auth_voice_ts > 0 and (now_ts - self._last_auth_voice_ts) <= 300
        if not (seen or spoke_recently):
            return "owner_not_present"

        return ""

    def _revalidate_initiative_output(self, decision_token) -> tuple[bool, str]:
        """Ultimo guard prima dell'efferenza: stato semantico e voce in volo."""
        current, reason = self.present.revalidate(
            decision_token,
            require_phase=InteractionPhase.LISTENING,
        )
        if not current:
            return False, reason
        if self._voice_input_inflight.is_set():
            return False, "voice_input_inflight"
        return True, "current"

    def _handle_initiative_candidate(self, event_id: str, event: dict, *, from_pending: bool = False) -> bool:
        """Valuta un evento Pulse e, se maturo, fa parlare Euri.

        Ritorna True se il candidato è stato chiuso (parlato/scartato), False se
        resta pendente per una condizione temporanea.
        """
        from core.initiative import (
            build_candidate,
            classify_focus_relevance,
            clear_pending,
            generate_question,
            mark_seen,
            record_candidate,
            store_event_pending,
            store_pending,
            was_seen,
        )

        if was_seen(self.r, event_id) and not from_pending:
            return True

        if (not from_pending
                and str(event.get("sense") or "") == "memory"
                and str(event.get("kind") or "") == "saved"):
            # `save_memory` emette il Pulse prima che alcuni caller post-marchino il
            # nodo (es. passive_support=tacit_acceptance). Prima di decidere, lascia
            # stabilizzare il JSON e rivaluta dal pending.
            store_event_pending(self.r, event_id, event)
            return False

        candidate = build_candidate(self.r, event_id, event)
        if not candidate.eligible:
            record_candidate(self.r, candidate, decision="skip", reason=candidate.reason)
            clear_pending(self.r, event_id)
            mark_seen(self.r, event_id)
            return True

        try:
            present_snapshot = self.present.snapshot()
            focus_active = present_snapshot.focus_open()
            focus_text = present_snapshot.focus_text() if focus_active else ""
        except Exception:
            present_snapshot = None
            focus_active = False
            focus_text = ""

        contextual = False
        if focus_active:
            # Prima escludi stati/modalità che vietano sempre l'efferenza; nessuna
            # chiamata LLM mentre Euri parla, processa o aspetta già una risposta.
            block_reason = self._initiative_block_reason(idle_seconds=0, cooldown_s=0)
            if block_reason:
                if not from_pending:
                    store_pending(self.r, candidate)
                    record_candidate(self.r, candidate, decision="defer", reason=block_reason)
                return False

            cache_key = (event_id, present_snapshot.last_user_turn_id)
            relevance = self._initiative_focus_cache.get(cache_key)
            if relevance is None:
                with self._brain_lock:
                    relevance = classify_focus_relevance(focus_text, candidate)
                self._initiative_focus_cache[cache_key] = relevance
                if len(self._initiative_focus_cache) > 256:
                    self._initiative_focus_cache.pop(next(iter(self._initiative_focus_cache)))
            if relevance != "EXTENDS":
                reason = f"focus_{relevance.lower()}"
                if not from_pending:
                    store_pending(self.r, candidate)
                    record_candidate(self.r, candidate, decision="defer", reason=reason)
                return False
            contextual = True
            block_reason = self._initiative_block_reason(
                idle_seconds=getattr(config, "INITIATIVE_CONTEXTUAL_MIN_IDLE_S", 8),
                cooldown_s=getattr(config, "INITIATIVE_CONTEXTUAL_COOLDOWN_S", 3 * 60),
            )
        else:
            block_reason = self._initiative_block_reason()
        if block_reason:
            if not from_pending:
                store_pending(self.r, candidate)
                record_candidate(self.r, candidate, decision="defer", reason=block_reason)
            return False

        if getattr(config, "INITIATIVE_SHADOW_ONLY", False):
            record_candidate(self.r, candidate, decision="shadow", reason="shadow_only")
            clear_pending(self.r, event_id)
            mark_seen(self.r, event_id)
            return True

        decision_token = self.present.issue_decision_token()
        with self._brain_lock:
            proposal = generate_question(candidate, focus_text=focus_text if contextual else "")
        if contextual:
            proposal = dict(proposal)
            proposal["focus_relevance"] = "EXTENDS"
            proposal["focus_turn_id"] = present_snapshot.last_user_turn_id

        if not proposal.get("should_ask"):
            record_candidate(self.r, candidate, decision="skip", reason="llm_declined", proposal=proposal)
            clear_pending(self.r, event_id)
            mark_seen(self.r, event_id)
            return True

        question = str(proposal.get("question") or "").strip()
        if not question:
            record_candidate(self.r, candidate, decision="skip", reason="empty_question", proposal=proposal)
            clear_pending(self.r, event_id)
            mark_seen(self.r, event_id)
            return True

        current, stale_reason = self._revalidate_initiative_output(decision_token)
        if not current:
            if not from_pending:
                store_pending(self.r, candidate)
                record_candidate(
                    self.r,
                    candidate,
                    decision="defer",
                    reason=f"stale_decision:{stale_reason}",
                    proposal=proposal,
                )
            return False

        try:
            self.r.set("euri:initiative:last_ask_ts", str(time.time()), ex=7 * 24 * 3600)
        except Exception:
            pass

        # La risposta dell'utente rientra nello stesso circuito già usato dal briefing
        # manuale: capture_reaction salva la lezione e aggiorna l'insight.
        if str(candidate.event.get("sense") or "") == "insight":
            self._awaiting_reaction = _PendingState(
                {
                    "insight": candidate.related,
                    "question": question,
                    "question_id": event_id,
                },
                timeout=300,
            )
            self._persist_pending_continuity("reaction", self._awaiting_reaction)
            self.present.set_pending_question(event_id, question)
        elif (
            str(candidate.event.get("sense") or "") == "memory"
            and str(candidate.related.get("source") or "") == "passive"
        ):
            self._awaiting_memory_verification = _PendingState(
                {
                    "memory_id": candidate.related.get("id"),
                    "claim": candidate.related.get("content", ""),
                    "question": question,
                    "question_id": event_id,
                },
                timeout=300,
            )
            self._persist_pending_continuity(
                "memory_verification", self._awaiting_memory_verification
            )
            self.present.set_pending_question(event_id, question)

        record_candidate(self.r, candidate, decision="spoken", proposal=proposal)
        clear_pending(self.r, event_id)
        mark_seen(self.r, event_id)
        self.brain.record_context_message("assistant", question)
        self.memory.log_conversation(_ASSISTANT_NAME, question)
        self._speak(question)
        return True

    def _initiative_worker(self):
        """Consuma Pulse e fa emergere poche domande proattive, prompt-based."""
        if not getattr(config, "INITIATIVE_ENABLED", False):
            logger.info("Initiative controller disabilitato da config")
            return

        from core.initiative import PULSE_STREAM, clear_pending, iter_pending

        last_id = "$"  # niente replay massivo al boot: solo eventi nuovi + pending espliciti
        logger.info("Initiative controller: in ascolto su euri:pulse")
        while self._running:
            self._workers.heartbeat("initiative")
            try:
                for event_id, event in iter_pending(
                    self.r,
                    limit=3,
                    min_age_s=getattr(config, "INITIATIVE_PENDING_MIN_AGE_S", 0),
                ):
                    closed = self._handle_initiative_candidate(event_id, event, from_pending=True)
                    if closed:
                        clear_pending(self.r, event_id)

                streams = self.r.xread(
                    {PULSE_STREAM: last_id},
                    count=10,
                    block=getattr(config, "INITIATIVE_PULSE_BLOCK_MS", 5000),
                )
                for _stream, entries in streams:
                    for event_id, fields in entries:
                        last_id = event_id
                        self._handle_initiative_candidate(event_id, fields)
            except Exception as e:
                logger.error(f"Initiative controller: errore loop: {e}")
                if self._wait_or_stop(5):
                    break

    def _memory_outbox_loop(self):
        """Recupera gli effetti derivati rimasti pendenti dopo save o crash."""
        from core.memory_outbox import drain_memory_outbox

        while self._running:
            self._workers.heartbeat("memory-outbox")
            _ok, failed = drain_memory_outbox(self.r, limit=20)
            if self._wait_or_stop(5 if failed else 1):
                break

    def _cognitive_projector_loop(self):
        """Pulse v2 → timeline causale osservazionale, durevole e replayabile."""
        from core.cognitive_projector import (
            consume_projector_batch,
            ensure_projector_group,
        )

        ensure_projector_group(self.r)
        logger.info("Cognitive Projector: in ascolto durevole su euri:pulse")
        while self._running:
            self._workers.heartbeat("cognitive-projector")

            # Prima recupera ciò che questo consumer stabile aveva ricevuto ma non
            # ACKato prima di un crash; normalmente il batch è vuoto.
            read, projected, ignored = consume_projector_batch(
                self.r,
                pending=True,
                count=getattr(config, "COGNITIVE_PROJECTOR_BATCH_SIZE", 100),
            )
            if read:
                if projected + ignored < read and self._wait_or_stop(1):
                    break
                continue

            consume_projector_batch(
                self.r,
                pending=False,
                count=getattr(config, "COGNITIVE_PROJECTOR_BATCH_SIZE", 100),
                block_ms=getattr(config, "COGNITIVE_PROJECTOR_BLOCK_MS", 2000),
            )

    def _visual_presence_worker(self):
        """Condivide con la UI solo stato visivo effimero, mai dati biometrici."""
        from core.visual_presence import publish_visual_presence

        refresh_s = max(0.5, float(config.VISUAL_PRESENCE_REFRESH_S))
        while self._running:
            self._workers.heartbeat("visual-presence")
            try:
                publish_visual_presence(
                    self.r,
                    self.visual_gate.operational_snapshot(),
                    ttl_s=config.VISUAL_PRESENCE_STATE_TTL_S,
                )
            except Exception as exc:
                logger.debug(f"Visual presence bridge: publish ignorata ({exc})")
            if self._wait_or_stop(refresh_s):
                break

    def _worker_watchdog(self):
        """Rende osservabili i thread vivi ma senza battito; non duplica il worker."""
        seen_stalled: set[str] = set()
        stale_after = float(getattr(config, "WORKER_HEARTBEAT_STALE_SECONDS", 180))
        while self._running:
            self._workers.heartbeat("worker-watchdog")
            snapshot = self._workers.health(stale_after_s=stale_after)
            stalled = {
                name for name, item in snapshot.items()
                if name != "worker-watchdog" and item.get("state") == "stalled"
            }
            for name in sorted(stalled - seen_stalled):
                age = snapshot[name].get("heartbeat_age_s", 0.0)
                logger.warning(
                    f"Worker {name}: thread vivo ma senza heartbeat da {age:.0f}s"
                )
            for name in sorted(seen_stalled - stalled):
                logger.info(f"Worker {name}: heartbeat ripristinato")
            seen_stalled = stalled
            if self._wait_or_stop(30):
                break

    def run(self):
        self._workers.prepare()
        self._shutdown_done = False
        self._running = True
        self._reconcile_ideation_runtime()

        # Intercetta Ctrl+C
        def _shutdown(sig, frame):
            logger.info("Shutdown segnalato...")
            self._request_shutdown()

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # Thread proattivo reminder
        self._workers.start("reminder", self._reminder_loop)

        # Thread passive learner — analizza conversazioni in idle silenziosamente
        self._workers.start("passive-learner", self._passive_learner_loop)

        # Thread Loop 2a — consolidamento silenzioso (reflection) in idle
        self._workers.start("consolidation", self._consolidation_loop)

        # Thread mobile worker — gestisce richieste dalla pagina Streamlit via Redis Stream
        self._workers.start("mobile", self._mobile_worker)

        # Thread initiative controller — Pulse → tension → domanda prompt-based.
        self._workers.start(
            "initiative",
            self._initiative_worker,
            enabled=getattr(config, "INITIATIVE_ENABLED", False),
        )

        # Consegna asincrona dei tornei espliciti, separata dall'iniziativa Pulse.
        self._workers.start(
            "ideation-delivery",
            self._ideation_delivery_worker,
            enabled=getattr(config, "IDEATION_ARENA_ENABLED", False),
        )
        self._workers.start(
            "ideation-job",
            self._ideation_job_worker,
            enabled=getattr(config, "IDEATION_ARENA_ENABLED", False),
        )

        # Outbox memoria — TTL, indice attenzione, Pulse e Obsidian replayabili.
        self._workers.start("memory-outbox", self._memory_outbox_loop)

        # Proiezione Pulse v2 — timeline cognitiva osservazionale, nessuna mutazione.
        self._workers.start(
            "cognitive-projector",
            self._cognitive_projector_loop,
            enabled=getattr(config, "COGNITIVE_PROJECTOR_ENABLED", False),
        )

        # Stato visivo corrente per Silent Chat: snapshot sanitizzato con TTL breve.
        self._workers.start("visual-presence", self._visual_presence_worker)

        # Osserva la reattività, non solo thread.is_alive().
        self._workers.start("worker-watchdog", self._worker_watchdog)

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
                # Qualcuno è arrivato nel campo visivo: SOLO afferente (pulse) — non
                # sappiamo ancora CHI è (il riconoscimento arriva 1-2s dopo), e di
                # notte può essere un capoturno: nessuna voce su questo segnale.
                if self.visual_gate.consume_just_activated():
                    pulse_emit(self.r, "presence", "extero", "arrival",
                               payload={"first": self._first_visual_activation}, salience=0.7)
                    if self._first_visual_activation:
                        # Prima rilevazione dall'avvio: silenzio, solo warm-up modello
                        self._first_visual_activation = False
                        threading.Thread(target=self._warmup_model, daemon=True).start()

                # STEFANO è arrivato (riconosciuto in faccia): qui vive l'efferente di
                # rientro — reminder persi e ripresa TEACH si dicono solo a lui.
                # (Saluto a vuoto RIMOSSO, Stefano 23/06: si parla solo con un motivo
                # grounded — la Regola d'Oro resta.)
                if self.visual_gate.consume_owner_arrived():
                    pulse_emit(self.r, "presence", "extero", "owner_arrival",
                               payload={"identity": self.visual_gate.present_identity()},
                               salience=0.75)
                    if self._missed_reminders:
                        missed = self._missed_reminders.copy()
                        self._missed_reminders.clear()
                        if len(missed) == 1:
                            self._speak(
                                f"Bentornato {_OWNER_NAME}. Hai perso un promemoria: {missed[0]}."
                            )
                        else:
                            elenco = ", ".join(missed)
                            self._speak(
                                f"Bentornato {_OWNER_NAME}. Hai perso {len(missed)} "
                                f"promemoria: {elenco}."
                            )
                    # Controlla se c'è una sessione TEACH interrotta
                    snapshot = self.r.get("euri:teach:snapshot")
                    teach_snapshot = self._decode_teach_snapshot(snapshot)
                    if teach_snapshot and not self._teach_mode:
                        self._teach_snapshot_content = snapshot
                        self._teach_recovery_mode = True
                        self._speak("Ho trovato una sessione di insegnamento non completata. Vuoi riprendere da dove eravamo?")
                    else:
                        if snapshot and teach_snapshot is None:
                            self.r.delete("euri:teach:snapshot")
                            logger.warning(
                                "TEACH recovery: snapshot legacy/non autorizzato eliminato"
                            )
                        self._offer_next_guest_claim()

                # Salta se proactive_agent sta parlando
                if self.r.exists("euri:audio:lock"):
                    continue

                chunk = mic.read_chunk()
                speech_ended, segment = self.vad.process_chunk(chunk)

                if self.vad.is_speaking:
                    self._mark_voice_input_started()

                if not speech_ended:
                    continue

                # Tiene chiuso il confine anche dopo il VAD, mentre identità, STT e
                # dispatch stanno ancora attribuendo significato al turno.
                self._mark_voice_input_started()
                segment_ended_at = time.time()
                segment_duration_s = (
                    len(segment) / config.VAD_SAMPLING_RATE
                    if segment is not None else 0.0
                )
                utterance_started_at = max(
                    0.0,
                    segment_ended_at - segment_duration_s,
                )
                perception_trace_id = f"voice:{uuid.uuid4()}"

                # Gate mobile: silenzio se la pagina Voce Mobile sta processando
                if self.r.exists("euri:mobile:active"):
                    logger.debug("Mobile gate: voce ignorata — sessione mobile attiva")
                    self._record_voice_segment(
                        trace_id=perception_trace_id,
                        started_at=utterance_started_at,
                        observed_at=time.time(),
                        duration_s=segment_duration_s,
                        decision="mobile_session_active",
                    )
                    self.vad.reset()
                    self._finish_voice_input()
                    continue

                # Gate visivo: ignora voce se nessuno è presente
                if not self.visual_gate.is_user_present():
                    logger.debug("VisualGate: voce rilevata ma gate INACTIVE — ignorata")
                    self._record_voice_segment(
                        trace_id=perception_trace_id,
                        started_at=utterance_started_at,
                        observed_at=time.time(),
                        duration_s=segment_duration_s,
                        decision="visual_gate_inactive",
                    )
                    self.vad.reset()
                    self._finish_voice_input()
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
                    self._record_voice_segment(
                        trace_id=perception_trace_id,
                        started_at=utterance_started_at,
                        observed_at=time.time(),
                        duration_s=segment_duration_s,
                        decision="voice_enrollment",
                        actor_id=_OWNER_ID,
                    )
                    self.vad.reset()
                    self._finish_voice_input()
                    continue

                # La voce produce un verdetto a tre stati. Anche una voce rifiutata
                # viene trascritta: potra' parlare soltanto nel percorso ospite,
                # senza RAG privato, tool o memoria diretta. In modalita' interprete
                # le voci esterne sono previste e restano nel solo flusso traduzione.
                speaker_verdict = (
                    SpeakerVerdict.INDETERMINATE
                    if self._translate_bidir
                    else self.speaker_auth.classify(segment)
                )
                if self._translate_bidir:
                    speaker_evidence = {
                        "similarity": None,
                        "threshold": None,
                        "reason": "interpreter_mode",
                    }
                else:
                    evidence_reader = getattr(
                        self.speaker_auth,
                        "last_classification",
                        None,
                    )
                    speaker_evidence = (
                        evidence_reader() if callable(evidence_reader) else {}
                    )

                # Trascrizione. Il consenso conversazionale appartiene all'inizio
                # fisico del turno, non alla fine di VAD+STT: un intervento lungo
                # iniziato dentro la lease non deve scadere mentre viene acquisito.
                force_lang = None if self._translate_bidir else "it"
                _t_stt = time.perf_counter()
                text, detected_lang = self.stt.transcribe(segment, force_lang=force_lang)
                logger.info(f"[TIMING] STT: {(time.perf_counter()-_t_stt)*1000:.0f}ms")
                self.vad.reset()

                actor_id = (
                    "interpreter"
                    if self._translate_bidir
                    else self._resolve_voice_actor(speaker_verdict)
                )
                if actor_id == "unknown":
                    logger.info(
                        "SpeakerAuth: interlocutore non verificato — percorso ospite isolato"
                    )

                acceptance_outcome: dict = {}
                accepted = self._accept_voice_transcript(
                    text,
                    addressed_at=utterance_started_at,
                    authenticated=actor_id == _OWNER_ID,
                    require_wake_word=actor_id == "unknown",
                    # L'eventuale acknowledgment del Dream deve precedere lo
                    # stato PROCESSING. Il turno owner viene registrato subito
                    # dopo, una sola volta, con lo stesso timestamp fisico.
                    track_present=False,
                    include_semantic_frame=True,
                    outcome=acceptance_outcome,
                )
                decision = str(acceptance_outcome.get("reason") or "unknown")
                addressed = bool(acceptance_outcome.get("accepted"))
                if decision == "stt_garbage":
                    stt_state = "garbage"
                else:
                    stt_state = "text" if text else "empty"
                delivered_to = "none"
                if addressed:
                    delivered_to = (
                        "guest_dispatch" if actor_id == "unknown"
                        else "interpreter_dispatch" if actor_id == "interpreter"
                        else "owner_dispatch"
                    )
                self._record_voice_segment(
                    trace_id=perception_trace_id,
                    started_at=utterance_started_at,
                    observed_at=time.time(),
                    duration_s=segment_duration_s,
                    decision=decision,
                    speaker_verdict=getattr(
                        speaker_verdict,
                        "value",
                        str(speaker_verdict),
                    ),
                    speaker_evidence=speaker_evidence,
                    actor_id=actor_id,
                    stt_state=stt_state,
                    transcript_chars=len(text or ""),
                    detected_language=detected_lang or "",
                    has_wake_word=bool(acceptance_outcome.get("has_wake_word")),
                    addressed=addressed,
                    delivered_to=delivered_to,
                )
                if accepted is None:
                    self._finish_voice_input()
                    continue
                text, has_wake_word, bootstrap_frame = accepted

                self.visual_gate.notify_activity()
                if hasattr(self, 'dream_engine'):
                    self.dream_engine.notify_activity()
                self._acknowledge_dream_preemption(actor_id)
                if actor_id == _OWNER_ID:
                    try:
                        self.present.accept_user_turn(
                            text,
                            channel=InteractionChannel.VOICE,
                            at=utterance_started_at,
                        )
                    except Exception as e:
                        logger.debug(
                            f"Cognitive Present: accept_user_turn ignorato: {e}"
                        )

                # Dispatch. Se un handler termina senza TTS, chiude comunque la fase
                # PROCESSING; gli handler che parlano la chiudono via finish_speech.
                try:
                    if actor_id == "unknown":
                        self._handle_guest_turn(text, observed_at=utterance_started_at)
                    else:
                        self._dispatch(
                            text,
                            detected_lang=detected_lang,
                            trusted=has_wake_word or bootstrap_frame is not None,
                            observed_at=utterance_started_at,
                            semantic_frame=bootstrap_frame,
                            owner_authenticated=actor_id == _OWNER_ID,
                        )
                        if actor_id == _OWNER_ID:
                            self._offer_next_guest_claim()
                finally:
                    try:
                        if self.present.snapshot().phase is InteractionPhase.PROCESSING:
                            self.present.finish_processing(opens_conversation=False)
                    except Exception as e:
                        logger.debug(f"Cognitive Present: finish_processing ignorato: {e}")
                    self._finish_voice_input()

        self._shutdown_components()
        logger.info("Euri spento.")

    @staticmethod
    def _partition_passive_history(history: list[dict]) -> list[tuple[bool, list[dict]]]:
        """Separa per provenienza e scope senza fondere mondi epistemici."""
        from core.memory_scope import normalize_scope

        buckets: dict[tuple[str, bool], list[dict]] = {}
        order: list[tuple[str, bool]] = []
        current_exchange: dict[str, bool] = {}
        for message in history:
            scope = normalize_scope(message.get("memory_scope"))
            role = str(message.get("role") or "")
            if role == "user":
                frame = message.get("semantic_frame")
                addressed = bool(message.get("trusted")) or bool(
                    isinstance(frame, dict)
                    and frame.get("accepted_owner_turn") is True
                )
                current_exchange[scope] = addressed
            else:
                addressed = bool(message.get("trusted")) or current_exchange.get(scope, False)
            key = (scope, addressed)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(message)
        return [(addressed, buckets[(scope, addressed)]) for scope, addressed in order]

    @staticmethod
    def _passive_memory_eligible_history(history: list[dict]) -> list[dict]:
        """Esclude un turno no-store/effimero e la relativa risposta dal learner.

        Il journal raw viene comunque archiviato prima di questo filtro. Il
        criterio e' fail-open: un frame assente, incerto o di fallback continua
        a seguire la validazione passiva preesistente.
        """
        minimum_confidence = getattr(
            config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
        )
        return filter_passive_memory_history(
            history,
            minimum_confidence=minimum_confidence,
        )

    @classmethod
    def _passive_extraction_batches(cls, history: list[dict]) -> list[tuple[bool, list[dict]]]:
        """Evita contaminazione FORTE senza creare nuove perdite su batch piccoli.

        L'estrattore richiede almeno quattro messaggi. Se una partizione mista
        scende sotto la soglia, conserva il batch intero ma lo marca non rivolto:
        tutti i fatti risultanti vengono quindi degradati a DEBOLE.
        """
        from core.memory_scope import normalize_scope

        by_scope: dict[str, list[dict]] = {}
        scope_order: list[str] = []
        for message in history:
            scope = normalize_scope(message.get("memory_scope"))
            if scope not in by_scope:
                by_scope[scope] = []
                scope_order.append(scope)
            by_scope[scope].append(message)

        result: list[tuple[bool, list[dict]]] = []
        for scope in scope_order:
            scoped = by_scope[scope]
            partitions = cls._partition_passive_history(scoped)
            if len(partitions) > 1 and any(len(batch) < 4 for _, batch in partitions):
                result.append((False, scoped))
            else:
                result.extend(partitions)
        return result

    @staticmethod
    def _passive_weak_support(support: str | None, segment_addressed: bool) -> bool:
        """DEBOLE (→ requires_verification, fuori da Loop 2e) se il giudizio LLM è
        'weak' OPPURE se il segmento non era rivolto a Euri (nessuna wake word). Non
        scarta nulla: degrada l'incerto. Protegge provenienza/qualità epistemica."""
        return support == "weak" or not segment_addressed

    def _utterance_is_addressed(self, has_wake_word: bool, since_last: float,
                                conversation_open: bool = False) -> bool:
        """True se l'utterance va processata (guard di consenso conversazionale).
        In traduzione/dettato parla anche l'interlocutore → sempre processata.
        Altrimenti: wake word esplicita OPPURE dentro la finestra di conversazione
        attiva. `since_last` DEVE misurare dal turno PRECEDENTE, non dall'utterance
        corrente (che azzererebbe la finestra e renderebbe il guard un no-op)."""
        if self._translate_bidir or self._dictation_mode:
            return True
        return has_wake_word or conversation_open or since_last < _CONVERSATION_WINDOW_SEC

    def _adaptive_followup_addressed(
        self,
        text: str,
        *,
        authenticated: bool,
        require_wake_word: bool,
        focus_open: bool,
    ) -> bool:
        """Recupera un seguito fuori lease solo con identità e continuità forti."""
        if (
            not getattr(config, "CONVERSATION_ADAPTIVE_FOLLOWUP_ENABLED", False)
            or not authenticated
            or require_wake_word
            or not focus_open
        ):
            return False
        try:
            if not self.visual_gate.is_owner_present():
                return False
            with self.brain.history_lock:
                history = list(self.brain._conversation_history)
            from core.addressedness import (
                classify_adaptive_followup,
                recent_dialogue_text,
            )
            dialogue = recent_dialogue_text(history)
            classifier = getattr(
                self,
                "_adaptive_followup_classifier",
                classify_adaptive_followup,
            )
            result = classifier(text, dialogue)
            return bool(result.get("accepted"))
        except Exception as exc:
            logger.debug(f"Addressedness gate: precondizione fallita ({exc})")
            return False

    def _semantic_owner_bootstrap(
        self,
        text: str,
        *,
        authenticated: bool,
        require_wake_word: bool,
    ) -> dict | None:
        """Accetta il primo turno senza wake solo con biometria e semantica forti."""
        if (
            not getattr(config, "CONVERSATION_SEMANTIC_BOOTSTRAP_ENABLED", True)
            or not authenticated
            or require_wake_word
            or self._translate_bidir
            or self._dictation_mode
        ):
            return None
        try:
            if not self.visual_gate.is_owner_present():
                return None
            interpreter = getattr(
                self,
                "_bootstrap_semantic_interpreter",
                self._interpret_semantic_bootstrap,
            )
            frame = interpreter(text)
            accepted = frame_bootstraps_owner_session(
                frame,
                minimum_confidence=getattr(
                    config,
                    "CONVERSATION_SEMANTIC_BOOTSTRAP_MIN_CONFIDENCE",
                    0.92,
                ),
                minimum_frame_confidence=getattr(
                    config, "SEMANTIC_TURN_MIN_CONFIDENCE", 0.72
                ),
            )
            logger.info(
                "Bootstrap semantico owner: {} relation={} conf={:.2f}",
                "ACCEPT" if accepted else "REJECT",
                frame.get("address_relation") or "unclear",
                float(frame.get("address_confidence") or 0.0),
            )
            return frame if accepted else None
        except Exception as exc:
            logger.debug("Bootstrap semantico owner: fail-closed ({})", exc)
            return None

    @staticmethod
    def _is_garbage_transcript(text: str) -> tuple[bool, float]:
        """Rileva l'allucinazione Whisper composta quasi solo da parole ripetute."""
        words = text.split()
        if len(words) < 6:
            return False, 0.0
        ratio = max(words.count(word) for word in set(words)) / len(words)
        return ratio > 0.60, ratio

    def _accept_voice_transcript(
        self,
        text: str,
        now_ts: float | None = None,
        *,
        addressed_at: float | None = None,
        authenticated: bool = True,
        require_wake_word: bool = False,
        track_present: bool = True,
        include_semantic_frame: bool = False,
        outcome: dict | None = None,
    ) -> tuple[str, bool] | tuple[str, bool, dict | None] | None:
        """Valida consenso e STT; solo una voce accettata rinnova l'attività.

        Il consenso si valuta quando l'utente ha iniziato a parlare. Un turno
        lungo, iniziato dentro la lease, non deve scadere mentre VAD e STT lo
        stanno ancora acquisendo.
        """
        def record_outcome(
            accepted: bool,
            reason: str,
            *,
            has_wake_word: bool = False,
        ) -> None:
            if outcome is not None:
                outcome.clear()
                outcome.update({
                    "accepted": bool(accepted),
                    "reason": str(reason),
                    "has_wake_word": bool(has_wake_word),
                })

        if not text:
            record_outcome(False, "stt_empty")
            return None

        garbage, ratio = self._is_garbage_transcript(text)
        if garbage:
            logger.debug(f"Garbage STT scartato (ratio={ratio:.2f}): '{text[:40]}'")
            record_outcome(False, "stt_garbage")
            return None

        if not self._translate_bidir:
            text_lower = text.lower()
            for wrong, right in _STT_CORRECTIONS.items():
                if wrong in text_lower:
                    text = text_lower.replace(wrong, right)
                    break

        now_ts = time.time() if now_ts is None else now_ts
        guard_at = now_ts if addressed_at is None else min(float(addressed_at), now_ts)
        has_wake_word = bool(_WAKE_WORD_RE.search(text))
        bootstrap_frame: dict | None = None
        # Prima interazione dopo l'avvio: non esiste un turno precedente. Manteniamo
        # chiusa la lease senza stampare nel log l'intera epoch come durata trascorsa.
        has_previous_activity = self._last_activity_ts > 0
        since_last = (
            guard_at - self._last_activity_ts
            if has_previous_activity else float("inf")
        )
        try:
            present_snapshot = self.present.snapshot(now=guard_at)
            conversation_open = present_snapshot.conversation_open(guard_at)
            focus_open = present_snapshot.focus_open(guard_at)
        except Exception:
            conversation_open = False
            focus_open = False
        addressed = self._utterance_is_addressed(has_wake_word, since_last, conversation_open)
        if addressed:
            if has_wake_word:
                acceptance_reason = "accepted_wake_word"
            elif self._translate_bidir:
                acceptance_reason = "accepted_interpreter_mode"
            elif self._dictation_mode:
                acceptance_reason = "accepted_dictation_mode"
            elif conversation_open:
                acceptance_reason = "accepted_conversation_lease"
            elif since_last < _CONVERSATION_WINDOW_SEC:
                acceptance_reason = "accepted_activity_window"
            else:
                acceptance_reason = "accepted_other"
        else:
            acceptance_reason = ""
        if not addressed:
            addressed = self._adaptive_followup_addressed(
                text,
                authenticated=authenticated,
                require_wake_word=require_wake_word,
                focus_open=focus_open,
            )
            if addressed:
                acceptance_reason = "accepted_adaptive_followup"
        if not addressed and not has_previous_activity:
            bootstrap_frame = self._semantic_owner_bootstrap(
                text,
                authenticated=authenticated,
                require_wake_word=require_wake_word,
            )
            addressed = bootstrap_frame is not None
            if addressed:
                acceptance_reason = "accepted_semantic_bootstrap"
        if require_wake_word:
            addressed = has_wake_word
            acceptance_reason = (
                "accepted_wake_word" if addressed else "guest_wake_word_required"
            )
        if not addressed:
            elapsed = f"{since_last:.0f}s" if has_previous_activity else "nessun turno precedente"
            logger.debug(
                "Wake word assente e inizio turno fuori finestra "
                f"({elapsed}) — ignorato: '{text[:40]}'"
            )
            rejection_reason = acceptance_reason or (
                "wake_word_absent_outside_conversation"
                if has_previous_activity
                else "wake_word_absent_no_previous_turn"
            )
            record_outcome(
                False,
                rejection_reason,
                has_wake_word=has_wake_word,
            )
            return None

        record_outcome(
            True,
            acceptance_reason or "accepted_other",
            has_wake_word=has_wake_word,
        )
        self._last_activity_ts = now_ts
        if authenticated and not self._translate_bidir:
            self._last_auth_voice_ts = now_ts
        if track_present:
            try:
                self.present.accept_user_turn(
                    text,
                    channel=InteractionChannel.VOICE,
                    at=now_ts,
                )
            except Exception as e:
                logger.debug(f"Cognitive Present: accept_user_turn ignorato: {e}")
        if include_semantic_frame:
            return text, has_wake_word, bootstrap_frame
        return text, has_wake_word

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
            self._workers.heartbeat("mobile")
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

                        # Il worker mobile vive in un thread proprio: i ContextVar non
                        # ereditano automaticamente lo scope attivo del daemon.
                        from core.memory_scope import bind_memory_scope, get_active_scope
                        bind_memory_scope(get_active_scope(self.r))
                        self._last_activity_ts = time.time()
                        raw_mobile_text = text
                        semantic_frame = self._interpret_semantic_turn(raw_mobile_text)
                        text = str(
                            semantic_frame.get("interpreted_text") or raw_mobile_text
                        )
                        self.memory.log_conversation(_OWNER_NAME, text)

                        context = self._build_context(
                            text, semantic_frame=semantic_frame
                        )
                        context = (context + "\n\n" if context else "") + \
                            f"[Messaggio da interfaccia mobile — {_OWNER_NAME} è lontano dalla workstation. " \
                            "Rispondi in modo conciso e TTS-friendly, niente markdown.]"

                        # Brain — lock condiviso con _handle_chat()
                        lineage = self._start_response_lineage(
                            text, channel="mobile", mode="chat"
                        )
                        try:
                            with self._brain_lock:
                                response = self.brain.respond(
                                    text,
                                    context=context,
                                    actor_id=_OWNER_ID,
                                    raw_user_text=raw_mobile_text,
                                    semantic_frame=semantic_frame,
                                    **self._memory_thinking_kwargs(),
                                )
                        except Exception:
                            self._finish_response_lineage(
                                lineage, "", outcome="failed", attribute_usage=False
                            )
                            raise
                        response = scrub_unbacked_save_claim(response)  # pavimento di onestà: mobile non salva
                        emit_unbacked_action_commitment(self.r, response, set(), channel="mobile")
                        response = scrub_unbacked_action_claim(response, set())  # mobile non agisce: niente claim d'azione
                        self._finish_response_lineage(lineage, response)

                        self.memory.log_conversation(_ASSISTANT_NAME, response)
                        logger.info(f"[Mobile] Euri: {response[:80]}")

                        # TTS — lock per sicurezza sui modelli ONNX
                        with self._tts_lock:
                            samples, sample_rate = self.tts.synthesize(response)

                        samples_i16   = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
                        audio_b64_out = base64.b64encode(samples_i16.tobytes()).decode()

                        self.r.xadd(STREAM_OUT, {
                            "request_id": req_id,
                            "text":        raw_mobile_text,
                            "response":    response,
                            "audio_b64":   audio_b64_out,
                            "sample_rate": str(sample_rate),
                        }, maxlen=20)

            except Exception as e:
                logger.error(f"Mobile worker: {e}")
                if self._wait_or_stop(1):
                    break

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
            self._speak(f"Buongiorno {_OWNER_NAME}. {brief}")


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)

    daemon = VoiceDaemon()
    try:
        daemon.setup()
        daemon.run()
    finally:
        daemon._shutdown_components()
