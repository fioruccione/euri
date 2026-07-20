import sys
import os
import inspect
import queue as _queue
import re
import shutil
from pathlib import Path
import json
import time

# Aggiunge la root directory di Euri al sys.path per permettere gli import
sys.path.append(str(Path(__file__).parent.parent))

import streamlit as st
import redis
import numpy as np

import config
from core.brain import Brain
from core.memory_manager import MemoryManager

# Configurazione della pagina
st.set_page_config(
    page_title="Euri Control Room",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Applica CSS custom solo per le metriche
st.markdown("""
<style>
    .metric-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 6px;
        padding: 15px;
        text-align: center;
    }
    .metric-value {
        font-size: 2rem;
        font-weight: bold;
        color: #58a6ff;
    }
    .metric-label {
        font-size: 0.9rem;
        color: #8b949e;
        text-transform: uppercase;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_redis():
    """Connessione Redis condivisa."""
    return redis.Redis(host='localhost', port=6379, decode_responses=True)

@st.cache_resource
def get_embedder():
    """Carica l'embedder una sola volta per la sessione Streamlit."""
    from core.embedder import Embedder
    with st.spinner("Caricamento modello embedding..."):
        emb = Embedder()
        emb.load()
        return emb

@st.cache_resource
def get_brain():
    """Istanza condivisa del Brain."""
    return Brain()

@st.cache_resource
def get_executor():
    """Istanza condivisa dell'Executor — dà alla Silent Chat l'esecuzione dei tool
    (read_document, run_code, analyze_image…), come il voice daemon."""
    from agent.executor import Executor
    return Executor()

@st.cache_resource
def get_stt():
    from voice.stt import STT
    stt = STT()
    stt.load()
    return stt

@st.cache_resource
def get_tts_engine():
    from voice.tts import TTS
    tts = TTS()
    tts.load()
    return tts

@st.cache_resource
def get_voice_processor():
    """Processore WebRTC e coda audio — persistono tra i rerun (un'istanza per processo)."""
    import av, time as _t

    audio_q: _queue.Queue = _queue.Queue(maxsize=1)

    ENERGY_THR = 0.004      # soglia energia RMS — conservativa per mic iPhone via WebRTC/Opus
    SILENCE_N  = 70         # frame di silenzio prima di chiudere l'utterance
    MIN_N      = 15         # frame minimi di voce per utterance valida
    COOLDOWN_S = 3.5        # secondi di pausa dopo TTS per evitare echo

    class _Proc:
        def __init__(self):
            self._buf          = []
            self._silent       = 0
            self._active       = False
            self._cooldown_end = 0.0
            # debug: aggiornato a ogni frame, leggibile dal fragment UI
            self.debug = {"fmt": "?", "sr": 0, "ch": 0, "energy": 0.0, "active": False}

        def set_cooldown(self):
            self._cooldown_end = _t.time() + COOLDOWN_S
            self._buf    = []
            self._active = False
            self._silent = 0

        def recv(self, frame: av.AudioFrame) -> av.AudioFrame:
            arr = frame.to_ndarray()

            # Frame silenzio da restituire al browser — impedisce echo del microfono
            try:
                sf = av.AudioFrame.from_ndarray(
                    np.zeros_like(arr), format=frame.format.name, layout=frame.layout.name
                )
                sf.sample_rate = frame.sample_rate
                sf.pts = frame.pts
            except Exception:
                sf = frame  # fallback: non dovrebbe mai accadere

            if _t.time() < self._cooldown_end:
                return sf

            # Normalizza in base al dtype reale
            if arr.dtype == np.int16:
                pcm = arr.astype(np.float32) / 32768.0
            elif arr.dtype == np.int32:
                pcm = arr.astype(np.float32) / 2147483648.0
            else:
                pcm = arr.astype(np.float32)

            # → mono
            ch = frame.channels if frame.channels > 0 else 1
            mono = pcm.reshape(ch, -1).mean(axis=0)

            energy = float(np.abs(mono).mean())
            self.debug = {
                "fmt": frame.format.name, "sr": frame.sample_rate,
                "ch": ch, "energy": round(energy, 5), "active": self._active,
            }

            if energy > ENERGY_THR:
                self._active  = True
                self._silent  = 0
                self._buf.append((mono.copy(), frame.sample_rate))
            elif self._active:
                self._silent += 1
                self._buf.append((mono.copy(), frame.sample_rate))
                if self._silent >= SILENCE_N:
                    if len(self._buf) >= MIN_N:
                        audio = np.concatenate([b[0] for b in self._buf])
                        sr    = self._buf[0][1]
                        try:
                            audio_q.put_nowait((audio, sr))
                        except _queue.Full:
                            pass
                    self._buf    = []
                    self._active = False
                    self._silent = 0
            return sf  # silenzio: nessun echo verso il browser

    return _Proc(), audio_q

# Inizializzazione
r = get_redis()
embedder = get_embedder()
brain = get_brain()
memory_manager = MemoryManager(r, embedder)
executor = get_executor()
executor.brain = brain
executor.memory = memory_manager
if brain._episode_callback is None:
    brain._episode_callback = lambda summary, temporal_context: memory_manager.save_memory(
        summary,
        category="episodio", source="episode",
        memory_kind="conversation_episode", temporal_context=temporal_context,
    )

# Layout generale: 2 colonne (Main a sinistra, Terminale a destra)
main_col, term_col = st.columns([2.5, 1.5], gap="large")

# Sidebar
st.sidebar.title("Euri Control Room 🧠")
st.sidebar.markdown("---")
page = st.sidebar.radio("Navigazione", ["🎙️ Voce", "Telemetria & Welford", "Silent Chat", "RAG Explorer", "🪪 Volti & Accessi"])

st.sidebar.markdown("---")
st.sidebar.info(f"**Modello:** {config.OLLAMA_MODEL}\n\n**Vault:** {config.OBSIDIAN_VAULT_PATH}")

if st.sidebar.button("Pulisci Memoria Chat"):
    st.session_state.messages = []
    st.sidebar.success("Chat resettata!")


# ── COLONNA DESTRA: TERMINALE LIVE ───────────────────────────────────────────
with term_col:
    st.subheader("🖥️ Euri Terminal (Live)")
    
    @st.fragment(run_every="1s")
    def live_terminal():
        log_path = Path("logs/voice_daemon.log")
        if log_path.exists():
            try:
                # Legge le ultime 35 righe del log in modo efficiente
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    last_lines = "".join(lines[-35:])
                    # Mostriamo il codice senza il tasto copia per pulizia
                    st.code(last_lines, language="log")
            except Exception as e:
                st.error(f"Errore lettura log: {e}")
        else:
            st.info("In attesa che Euri parta e crei il log...")
            
    live_terminal()


# ── COLONNA SINISTRA: CONTENUTO PRINCIPALE ───────────────────────────────────
with main_col:

    # ── PAGE 0: VOCE MOBILE ──────────────────────────────────────────────────────
    if page == "🎙️ Voce":
        import io, wave, base64 as _b64, uuid as _uuid, time as _time

        st.title("🎙️ Voce Mobile")
        st.caption("La conversazione è condivisa col daemon — stessa storia, stesso contesto.")

        # Inizializza stream ID per non ricevere risposte vecchie
        if "mobile_out_id" not in st.session_state:
            try:
                msgs = r.xrevrange("euri:mobile:out", count=1)
                st.session_state.mobile_out_id = msgs[0][0] if msgs else "0-0"
            except Exception:
                st.session_state.mobile_out_id = "0-0"
        if "voice_history" not in st.session_state:
            st.session_state.voice_history = []
        if "mobile_waiting" not in st.session_state:
            st.session_state.mobile_waiting = False
        if "audio_widget_key" not in st.session_state:
            st.session_state.audio_widget_key = 0

        def _d(v):
            return v.decode() if isinstance(v, bytes) else (v or "")

        def _send_audio_to_daemon(audio_float32: np.ndarray, sr: int):
            """Serializza audio float32 e pubblica su euri:mobile:in."""
            req_id    = str(_uuid.uuid4())[:8]
            audio_b64 = _b64.b64encode(audio_float32.astype(np.float32).tobytes()).decode()
            r.setex("euri:mobile:active", 120, 1)
            r.xadd("euri:mobile:in", {
                "request_id": req_id,
                "audio_b64":  audio_b64,
                "sr":         str(sr),
            }, maxlen=10)
            st.session_state.mobile_waiting = True

        # Mostra cronologia (re-renderizzata ad ogni rerun completo della pagina)
        for turn in st.session_state.voice_history[-8:]:
            st.markdown(f"**{turn['role']}:** {turn['content']}")

        # Audio in attesa: prodotto dal rerun post-risposta, consumato una sola volta
        if "mobile_audio_data" in st.session_state:
            st.audio(st.session_state.mobile_audio_data, format="audio/wav", autoplay=True)
            del st.session_state.mobile_audio_data

        # Polling risposte — fragment run_every=1; quando trova la risposta
        # salva in session_state e chiama st.rerun() per aggiornare la pagina intera.
        @st.fragment(run_every=1)
        def _poll_daemon():
            if not st.session_state.mobile_waiting:
                return
            msgs = r.xread({"euri:mobile:out": st.session_state.mobile_out_id}, count=1)
            if not msgs:
                st.caption("⏳ Euri sta elaborando...")
                return
            for _, messages in msgs:
                for msg_id, data in messages:
                    st.session_state.mobile_out_id = _d(msg_id)
                    text     = _d(data.get("text", ""))
                    response = _d(data.get("response", ""))
                    ab64     = _d(data.get("audio_b64", ""))
                    sr_out   = int(_d(data.get("sample_rate", "22050")) or 22050)

                    if text:
                        st.session_state.voice_history.extend([
                            {"role": "Tu",   "content": text},
                            {"role": "Euri", "content": response},
                        ])
                    if ab64:
                        samples_i16 = np.frombuffer(_b64.b64decode(ab64), dtype=np.int16)
                        wav_buf = io.BytesIO()
                        with wave.open(wav_buf, "wb") as wf:
                            wf.setnchannels(1); wf.setsampwidth(2)
                            wf.setframerate(sr_out)
                            wf.writeframes(samples_i16.tobytes())
                        wav_buf.seek(0)
                        st.session_state.mobile_audio_data = wav_buf.read()
                        get_voice_processor()[0].set_cooldown()
                        r.expire("euri:mobile:active", 5)
                    elif not text:
                        st.session_state.voice_history.append(
                            {"role": "Euri", "content": "⚠ Non ho capito. Riprova."}
                        )
                    st.session_state.mobile_waiting = False
                    st.rerun()  # ricarica la pagina: mostra history aggiornata + riproduce audio

        _poll_daemon()

        tab_auto, tab_manual = st.tabs(["🔄 Auto (VAD continuo)", "🖐 Manuale (pulsante)"])

        # ── TAB AUTO: WebRTC + VAD → daemon ─────────────────────────────────────
        with tab_auto:
            from streamlit_webrtc import webrtc_streamer, WebRtcMode

            _proc, _audio_q = get_voice_processor()

            st.caption("Premi **START**, poi parla liberamente. Euri risponde dopo ~1s di silenzio.")

            webrtc_ctx = webrtc_streamer(
                key="euri-voice-auto",
                mode=WebRtcMode.SENDRECV,  # iOS Safari workaround: sendonly non avvia encoder
                rtc_configuration={"iceServers": []},
                audio_frame_callback=_proc.recv,  # recv() ritorna silenzio → no echo
                media_stream_constraints={
                    "audio": {
                        "echoCancellation": True,
                        "noiseSuppression": True,
                        "autoGainControl": True,
                    },
                    "video": False,
                },
                async_processing=True,
            )

            # Debug live: sempre visibile — distingue problema ICE da problema VAD
            @st.fragment(run_every=2)
            def _debug_audio():
                playing = webrtc_ctx.state.playing
                d = _proc.debug
                if not playing:
                    st.caption("⚫ WebRTC: in attesa di START (o connessione non stabilita)")
                elif d["sr"] == 0:
                    st.caption("🟡 WebRTC connesso — nessun frame audio ricevuto ancora")
                else:
                    icon = "🔴 parlando" if d["active"] else "⬜ silenzio"
                    st.caption(
                        f"🟢 WebRTC ok → fmt:`{d['fmt']}` sr:`{d['sr']}` "
                        f"energy:`{d['energy']:.5f}` (soglia:0.004) {icon}"
                    )
            _debug_audio()

            @st.fragment(run_every=1)
            def _auto_listen():
                if st.session_state.mobile_waiting:
                    # Scarta audio accumulato durante elaborazione daemon (anti-loop)
                    try:
                        while True:
                            _audio_q.get_nowait()
                    except _queue.Empty:
                        pass
                    return

                # La callback audio_frame_callback riempie _audio_q quando il VAD chiude l'utterance
                try:
                    audio_raw, sr = _audio_q.get_nowait()
                except _queue.Empty:
                    if webrtc_ctx.state.playing:
                        st.caption("🎙️ In ascolto...")
                    return
                _send_audio_to_daemon(audio_raw, int(sr))

            _auto_listen()

        # ── TAB MANUALE: pulsante → daemon ───────────────────────────────────────
        with tab_manual:
            st.caption("Premi il microfono, parla, aspetta la risposta audio.")

            # La key cambia dopo ogni invio: il rerun da st.rerun() trova un widget
            # fresco (valore None) e non rimanda lo stesso audio in loop.
            audio_input = st.audio_input(
                "🎤 Registra e invia",
                key=f"audio_{st.session_state.audio_widget_key}",
            )

            if audio_input is not None and not st.session_state.mobile_waiting:
                # Incrementa subito la key — il prossimo rerun vedrà widget vuoto
                st.session_state.audio_widget_key += 1
                raw = audio_input.getvalue()
                with io.BytesIO(raw) as buf:
                    with wave.open(buf) as wf:
                        sr_wav = wf.getframerate()
                        n_ch   = wf.getnchannels()
                        frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                if n_ch > 1:
                    audio = audio.reshape(-1, n_ch).mean(axis=1)
                _send_audio_to_daemon(audio, sr_wav)

    # ── PAGE 1: TELEMETRIA ────────────────────────────────────────────────────────
    elif page == "Telemetria & Welford":
        st.title("🎛️ Telemetria Sistema")
        st.markdown("Monitoraggio in tempo reale dei contatori Redis e dell'apprendimento online di Euri.")
        
        # Metriche generali
        col1, col2, col3, col4 = st.columns(4)
        
        try:
            mem_count = r.ft("idx:memories").info()["num_docs"]
        except Exception:
            mem_count = 0
            
        try:
            ins_count = r.ft("idx:insights").info()["num_docs"]
        except Exception:
            ins_count = 0
            
        try:
            todo_count = len(memory_manager.get_pending_todos())
        except Exception:
            todo_count = 0
            
        keys_count = r.dbsize()
        
        with col1:
            st.markdown(f"<div class='metric-card'><div class='metric-value'>{mem_count}</div><div class='metric-label'>Memorie</div></div>", unsafe_allow_html=True)
        with col2:
            st.markdown(f"<div class='metric-card'><div class='metric-value'>{ins_count}</div><div class='metric-label'>Insights</div></div>", unsafe_allow_html=True)
        with col3:
            st.markdown(f"<div class='metric-card'><div class='metric-value'>{todo_count}</div><div class='metric-label'>To-Do attivi</div></div>", unsafe_allow_html=True)
        with col4:
            st.markdown(f"<div class='metric-card'><div class='metric-value'>{keys_count}</div><div class='metric-label'>Chiavi Redis Totali</div></div>", unsafe_allow_html=True)
            
        st.markdown("---")
        
        # Adaptive Fingerprints (Welford)
        st.subheader("🧬 Adaptive Fingerprints (Stato Welford)")
        st.markdown("Stato attuale dei centroidi appresi dall'LLM per la classificazione veloce (latenza 5ms).")
        
        # Recuperiamo le chiavi da redis
        welford_keys = r.keys("euri:welford:*")
        if not welford_keys:
            st.info("Nessuno stato Welford in questa sessione. I centroidi si ricostruiscono automaticamente con le prossime conversazioni — normale dopo un riavvio.")
        else:
            # Prepariamo i dati per la tabella
            welford_data = []
            for key in welford_keys:
                intent_name = key.split(":")[-1]
                data = r.get(key)
                if data:
                    try:
                        state = json.loads(data)
                        welford_data.append({
                            "Intent": intent_name,
                            "Campioni (n)": state.get("n", 0),
                            "Deviazione Standard (σ)": round(state.get("std", 0.0), 4),
                            "Soglia Adattiva (est.)": round(config.ADAPTIVE_CLASSIFIER_BASE_THRESHOLD * (1 + config.ADAPTIVE_CLASSIFIER_VARIANCE_BETA * state.get("std", 0.0)), 3)
                        })
                    except Exception:
                        pass
            
            # Ordiniamo per campioni decrescenti
            welford_data.sort(key=lambda x: x["Campioni (n)"], reverse=True)
            st.dataframe(welford_data, use_container_width=True, hide_index=True)


    # ── PAGE 2: SILENT CHAT ───────────────────────────────────────────────────────
    elif page == "Silent Chat":
        st.title("💬 Silent Chat")
        st.markdown("Chatta con Euri usando la tastiera. Nessun Voice Daemon, no TTS. La sessione LLM è condivisa.")

        _CHAT_UPLOAD_TYPES = [
            "pdf", "docx", "pptx", "txt", "md", "csv", "tsv", "json",
            "xlsx", "xls", "ods", "png", "jpg", "jpeg", "webp", "bmp",
            "gif", "tiff",
        ]
        _SUPPORTED_UPLOAD_EXTS = {f".{ext}" for ext in _CHAT_UPLOAD_TYPES}
        _SPREADSHEET_EXTS = {".xlsx", ".xls", ".ods"}
        _LOCAL_FILE_PATH_RE = re.compile(
            rf"(?P<path>(?:~|/)[^\n\r]*?\.({'|'.join(_CHAT_UPLOAD_TYPES)}))",
            re.IGNORECASE,
        )

        def _chat_upload_dir() -> Path:
            data_dir = Path(config.CODE_RUNNER_INPUT_DIR).expanduser()
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir

        def _chat_upload_registry_path() -> Path:
            return _chat_upload_dir() / ".silent_chat_uploads.json"

        def _is_inside_dir(path: Path, parent: Path) -> bool:
            try:
                path.resolve().relative_to(parent.resolve())
                return True
            except Exception:
                return False

        def _safe_upload_name(raw_name: str) -> str:
            name = Path(raw_name or "upload").name
            original = Path(name)
            stem = "".join(
                ch if ch.isalnum() or ch in (" ", ".", "_", "-") else "_"
                for ch in original.stem
            ).strip(" ._")
            suffix = "".join(
                ch for ch in original.suffix.lower()
                if ch.isalnum() or ch == "."
            )[:16]
            return f"{(stem or 'upload')[:80]}{suffix}"

        def _unique_upload_path(data_dir: Path, filename: str) -> Path:
            target = data_dir / filename
            if not target.exists():
                return target
            original = Path(filename)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for i in range(1, 100):
                suffix = f"_{stamp}" if i == 1 else f"_{stamp}_{i}"
                candidate = data_dir / f"{original.stem}{suffix}{original.suffix}"
                if not candidate.exists():
                    return candidate
            return data_dir / f"{original.stem}_{stamp}_{int(time.time())}{original.suffix}"

        def _load_chat_upload_registry() -> list[dict]:
            registry = _chat_upload_registry_path()
            if not registry.exists():
                return []
            try:
                data = json.loads(registry.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except Exception:
                return []

        def _write_chat_upload_registry(entries: list[dict]) -> None:
            registry = _chat_upload_registry_path()
            if not entries:
                registry.unlink(missing_ok=True)
                return
            registry.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

        def _cleanup_chat_uploads(
            *, delete_all: bool = False, keep_paths: set[Path] | None = None
        ) -> int:
            data_dir = _chat_upload_dir()
            now = time.time()
            ttl = getattr(config, "SILENT_CHAT_UPLOAD_TTL_SECONDS", 24 * 3600)
            keep_resolved = {p.resolve() for p in (keep_paths or set()) if p.exists()}
            kept = []
            removed = 0
            for entry in _load_chat_upload_registry():
                raw_path = entry.get("path") or ""
                path = Path(raw_path).expanduser()
                uploaded_at = float(entry.get("uploaded_at") or 0)
                expired = ttl > 0 and uploaded_at and now - uploaded_at > ttl
                if not raw_path or not _is_inside_dir(path, data_dir):
                    continue
                if path.exists() and path.resolve() in keep_resolved:
                    kept.append(entry)
                    continue
                if delete_all or expired:
                    try:
                        path.unlink(missing_ok=True)
                        removed += 1
                    except Exception:
                        kept.append(entry)
                elif path.exists():
                    kept.append(entry)
            _write_chat_upload_registry(kept)
            return removed

        def _save_chat_uploads(uploaded_files) -> tuple[list[dict], list[str]]:
            if not uploaded_files:
                return [], []
            data_dir = _chat_upload_dir()
            _cleanup_chat_uploads(delete_all=True)
            saved, errors = [], []
            for upload in uploaded_files:
                original_name = Path(getattr(upload, "name", "upload")).name
                safe_name = _safe_upload_name(original_name)
                target = _unique_upload_path(data_dir, safe_name)
                try:
                    target.write_bytes(upload.getvalue())
                except Exception as e:
                    errors.append(f"{original_name}: {e}")
                    continue
                saved.append({
                    "name": target.name,
                    "original_name": original_name,
                    "path": str(target),
                    "uploaded_at": time.time(),
                    "size": target.stat().st_size,
                })
            if saved:
                _write_chat_upload_registry(saved)
            return saved, errors

        def _allowed_pasted_path_roots() -> list[Path]:
            roots = [
                Path.home() / "Scrivania",
                _chat_upload_dir(),
            ]
            unique = []
            for root in roots:
                root = root.expanduser()
                if root not in unique:
                    unique.append(root)
            return unique

        def _clean_local_path_candidate(raw_path: str) -> Path:
            text = raw_path.strip().strip("\"'`").rstrip(".,;:!?)]}")
            if text.startswith("file://"):
                text = text[7:]
            return Path(text).expanduser()

        def _extract_local_path_candidates(text: str) -> list[Path]:
            candidates = []
            raw_candidates = []
            stripped = text.strip()
            if stripped.startswith(("/", "~", "file://")):
                raw_candidates.append(stripped)
            raw_candidates.extend(m.group(1) for m in re.finditer(r"['\"]([^'\"]+)['\"]", text))
            raw_candidates.extend(m.group("path") for m in _LOCAL_FILE_PATH_RE.finditer(text))

            seen = set()
            for raw in raw_candidates:
                path = _clean_local_path_candidate(raw)
                key = str(path)
                if key in seen or path.suffix.lower() not in _SUPPORTED_UPLOAD_EXTS:
                    continue
                seen.add(key)
                candidates.append(path)
            return candidates

        def _is_path_only_prompt(text: str, paths: list[Path]) -> bool:
            if len(paths) != 1:
                return False
            try:
                return _clean_local_path_candidate(text).resolve(strict=False) == paths[0].resolve(strict=False)
            except Exception:
                return False

        def _stage_existing_chat_paths(paths: list[Path]) -> tuple[list[dict], list[str]]:
            if not paths:
                return [], []

            data_dir = _chat_upload_dir()
            allowed_roots = _allowed_pasted_path_roots()
            sources, errors = [], []

            for path in paths:
                try:
                    source = path.resolve(strict=True)
                except Exception:
                    errors.append(f"{path}: file non trovato")
                    continue
                if not source.is_file():
                    errors.append(f"{source}: non e' un file")
                    continue
                if source.suffix.lower() not in _SUPPORTED_UPLOAD_EXTS:
                    errors.append(f"{source.name}: formato non supportato")
                    continue
                if not any(_is_inside_dir(source, root) for root in allowed_roots):
                    roots = ", ".join(str(r) for r in allowed_roots)
                    errors.append(f"{source}: percorso non ammesso (root consentite: {roots})")
                    continue
                sources.append(source)

            if not sources:
                return [], errors

            keep_paths = {p for p in sources if _is_inside_dir(p, data_dir)}
            _cleanup_chat_uploads(delete_all=True, keep_paths=keep_paths)

            saved = []
            for source in sources:
                try:
                    if _is_inside_dir(source, data_dir):
                        target = source
                    else:
                        target = _unique_upload_path(data_dir, _safe_upload_name(source.name))
                        shutil.copy2(source, target)
                except Exception as e:
                    errors.append(f"{source.name}: copia non riuscita ({e})")
                    continue

                saved.append({
                    "name": target.name,
                    "original_name": source.name,
                    "path": str(target),
                    "uploaded_at": time.time(),
                    "size": target.stat().st_size,
                })

            if saved:
                _write_chat_upload_registry(saved)
            return saved, errors

        def _split_chat_value(value) -> tuple[str, list]:
            if value is None:
                return "", []
            if isinstance(value, str):
                return value, []
            if isinstance(value, dict):
                text = value.get("text") or value.get("message") or ""
                files = value.get("files") or []
                return str(text), list(files)
            text = getattr(value, "text", None)
            if text is None:
                text = getattr(value, "message", "")
            files = getattr(value, "files", None) or []
            return str(text or ""), list(files)

        def _compose_upload_prompt(user_text: str, uploads: list[dict]) -> str:
            names = ", ".join(u["name"] for u in uploads)
            has_spreadsheet = any(Path(u["name"]).suffix.lower() in _SPREADSHEET_EXTS for u in uploads)
            action = "Elabora" if has_spreadsheet else "Leggi e analizza"
            upload_request = f"{action} i file appena caricati: {names}."
            user_text = (user_text or "").strip()
            if user_text:
                return f"{user_text}\n\n{upload_request}"
            return upload_request

        # Inizializza cronologia messaggi
        if "messages" not in st.session_state:
            st.session_state.messages = []
        if "chat_log_offset" not in st.session_state:
            st.session_state.chat_log_offset = len(memory_manager.get_today_conversation())
        if "sc_upload_key" not in st.session_state:
            st.session_state.sc_upload_key = 0

        _cleanup_chat_uploads()

        # Mostra messaggi precedenti
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        current_uploads = _load_chat_upload_registry()
        if current_uploads:
            names = ", ".join(entry.get("name", "?") for entry in current_uploads)
            c_upload_info, c_upload_del = st.columns([4, 1])
            with c_upload_info:
                st.caption(f"File chat temporanei: {names}")
            with c_upload_del:
                if st.button("Elimina", key="sc_delete_uploads", use_container_width=True):
                    removed = _cleanup_chat_uploads(delete_all=True)
                    st.success(f"File rimossi: {removed}")
                    st.rerun()

        _chat_input_supports_files = "accept_file" in inspect.signature(st.chat_input).parameters
        if _chat_input_supports_files:
            chat_value = st.chat_input(
                "Scrivi a Euri o trascina file qui...",
                accept_file="multiple",
                file_type=_CHAT_UPLOAD_TYPES,
                max_upload_size=getattr(config, "SILENT_CHAT_UPLOAD_MAX_MB", 200),
            )
            prompt, incoming_uploads = _split_chat_value(chat_value)
        else:
            incoming_uploads = st.file_uploader(
                "Trascina qui file da analizzare",
                type=_CHAT_UPLOAD_TYPES,
                accept_multiple_files=True,
                key=f"sc_upload_{st.session_state.sc_upload_key}",
            )
            prompt = st.session_state.pop("sc_pending_upload_prompt", "")
            if not prompt:
                prompt = st.chat_input("Scrivi a Euri...")

        if incoming_uploads:
            saved_uploads, upload_errors = _save_chat_uploads(incoming_uploads)
            for err in upload_errors:
                st.error(f"Upload non riuscito: {err}")
            if saved_uploads:
                prompt = _compose_upload_prompt(prompt, saved_uploads)
                if not _chat_input_supports_files:
                    st.session_state.sc_pending_upload_prompt = prompt
                    st.session_state.sc_upload_key += 1
                    st.rerun()
            elif not prompt:
                st.stop()

        if prompt and not incoming_uploads:
            pasted_paths = _extract_local_path_candidates(prompt)
            if pasted_paths:
                staged_paths, path_errors = _stage_existing_chat_paths(pasted_paths)
                for err in path_errors:
                    st.error(f"Path locale non acquisito: {err}")
                if staged_paths:
                    user_text = "" if _is_path_only_prompt(prompt, pasted_paths) else prompt
                    prompt = _compose_upload_prompt(user_text, staged_paths)

        # Input utente
        if prompt:
            # ── Curiosity loop (Euri Pulse, ramo efferente) — versione scritta ───
            # Stessa orchestrazione della voce (core.reaction, una sola verità), ma
            # via tastiera: Stefano non deve parlare ad alta voce (zero costo sociale,
            # vedi project_euri_initiative_engine). Due casi, ENTRAMBI prima del flusso
            # normale di chat e con st.stop():
            #   (1) reazione in attesa → questo prompt È la reazione all'insight chiesto
            #       prima → si cattura come lezione ri-sognabile (capture_reaction);
            #   (2) Stefano chiede dei MIEI sogni/intuizioni → briefing: pesco un insight
            #       non groundato e glielo chiedo ("è vero che…?"), poi resto in attesa.
            import core.reaction as _rx

            _pending = st.session_state.pop("sc_awaiting", None)
            if _pending is not None:
                st.session_state.messages.append({
                    "role": "user", "content": prompt, "observed_at": time.time()
                })
                with st.chat_message("user"):
                    st.markdown(prompt)
                with st.chat_message("assistant"):
                    with st.spinner("Euri assorbe…"):
                        try:
                            _rx.capture_reaction(memory_manager, _pending["insight"], prompt)
                            ack = "Capito, me lo segno — lo lascio sedimentare e ci ri-sogno su."
                        except Exception as e:
                            ack = f"(Non sono riuscito a fissare la lezione: {e})"
                    st.markdown(ack)
                st.session_state.messages.append({
                    "role": "assistant", "content": ack, "observed_at": time.time()
                })
                memory_manager.log_conversation("Stefano", prompt)
                memory_manager.log_conversation("Euri", ack)
                st.stop()

            if _rx.BRIEFING_HINT_RE.search(prompt):
                _is_brief, _topic = _rx.understand_briefing(prompt)
                if _is_brief:
                    st.session_state.messages.append({
                        "role": "user", "content": prompt, "observed_at": time.time()
                    })
                    with st.chat_message("user"):
                        st.markdown(prompt)
                    with st.chat_message("assistant"):
                        with st.spinner("Euri cerca un sogno da chiederti…"):
                            text, insight = _rx.run_briefing(memory_manager.r, embedder, _topic)
                        st.markdown(text)
                    st.session_state.messages.append({
                        "role": "assistant", "content": text, "observed_at": time.time()
                    })
                    memory_manager.log_conversation("Stefano", prompt)
                    memory_manager.log_conversation("Euri", text)
                    if insight is not None:
                        st.session_state["sc_awaiting"] = {"insight": insight}
                    st.stop()

            # ── Audit di Coerenza: capture correction signal ─────────────
            # Se il prompt è una correzione, salva il signal PRIMA del retrieval
            # del turno corrente (last_rag_ctx contiene ancora il ctx del turno corretto).
            if memory_manager.detect_correction(prompt):
                prev_user_turn = ""
                for m in reversed(st.session_state.messages):
                    if m["role"] == "user":
                        prev_user_turn = m["content"]
                        break
                prev_euri_turn = memory_manager.get_last_euri_turn()
                memory_manager.save_correction_signal(
                    prompt_originale=prev_user_turn,
                    risposta_euri=prev_euri_turn,
                    correzione_user=prompt,
                    rag_ctx_ids=memory_manager.get_last_rag_ctx(),
                )

            # Mostra utente
            st.session_state.messages.append({
                "role": "user", "content": prompt, "observed_at": time.time()
            })
            with st.chat_message("user"):
                st.markdown(prompt)

            # Ricerca veloce su Redis per iniettare contesto — stesso builder della voce.
            with st.spinner("Cerco nella memoria..."):
                from core.rag_context import build_rag_context, infer_context_mode
                _context_mode = infer_context_mode(prompt, default="chat")
                _recent_hist = [
                    {
                        "role": m["role"],
                        "content": m["content"],
                        "observed_at": m.get("observed_at"),
                    }
                    for m in st.session_state.messages[:-1]
                ]
                _rag = build_rag_context(
                    prompt, memory_manager, mode=_context_mode, recent_history=_recent_hist
                )
                context = _rag.text
                ctx_ids_now = list(_rag.ids)
                memory_manager.set_last_rag_ctx(ctx_ids_now)

            # Risposta Euri
            with st.chat_message("assistant"):
                with st.spinner("Euri sta pensando..."):
                    # Prima prova a ESEGUIRE un tool (read_document, run_code, analyze_image…).
                    # Solo regex (llm_fallback=False): cheap su ogni messaggio, e una frase
                    # normale non matcha → ricade sulla chat. Se un tool matcha si mostra il
                    # SUO output reale (anche "non ci sono file") — fine della confabulazione
                    # sui file in chat testuale (vedi project_euri_silentchat_no_tools).
                    tool_res = None
                    try:
                        tool_res = executor.dispatch_text(prompt, llm_fallback=False)
                    except Exception:
                        tool_res = None

                    # Percorso SAVE reale: prima della chat, riconosci un comando
                    # "memorizza…" con lo STESSO router della voce (solo regex → zero
                    # latenza sui messaggi normali) e salva DAVVERO, invece di lasciare
                    # che l'LLM finga il salvataggio. Logica condivisa con la voce via
                    # core/save_service. Vedi [[project_euri_silentchat_no_tools]].
                    save_res = None
                    if tool_res is None:
                        from core.intent_router import classify, Intent
                        from core.save_service import save_memory_command
                        try:
                            _intent, _ = classify(prompt)
                        except Exception:
                            _intent = None
                        if _intent == Intent.SAVE_MEMORY:
                            # Sorgente anaforica = ultimo scambio PRIMA del prompt corrente
                            # (messages[-1] è il "memorizza…" appena appeso).
                            prev_user, prev_assist = "", ""
                            for _m in reversed(st.session_state.messages[:-1]):
                                if not prev_assist and _m["role"] == "assistant":
                                    prev_assist = _m["content"]
                                elif not prev_user and _m["role"] == "user":
                                    prev_user = _m["content"]
                                if prev_user and prev_assist:
                                    break
                            # History recente per il risolutore SAVE semantico (Gradino 1):
                            # escludo il "memorizza…" corrente (messages[-1]).
                            recent_history = [
                                {
                                    "role": _m["role"],
                                    "content": _m["content"],
                                    "observed_at": _m.get("observed_at"),
                                }
                                for _m in st.session_state.messages[:-1]
                            ]
                            save_res = save_memory_command(
                                prompt, memory_manager, brain,
                                prev_user_text=prev_user, prev_assistant_text=prev_assist,
                                fresh=True,
                                recent_history=recent_history,
                            )

                    if tool_res is not None:
                        response = tool_res.get("output") or "Comando eseguito."
                        # Il turno è stato risolto da un tool, non dal RAG. Evita che una
                        # correzione successiva venga attribuita a memorie iniettate solo
                        # incidentalmente prima del dispatch.
                        memory_manager.set_last_rag_ctx([])
                    elif save_res is not None:
                        response = save_res["reply"]
                    else:
                        chat_hint = "[Modalità chat testuale — nessun vincolo TTS. Puoi rispondere con più profondità, sviluppare i concetti, fare domande di ritorno. Sii presente e partecipe come in una conversazione reale.]"
                        context_full = (context + "\n\n" + chat_hint) if context else chat_hint
                        # Gradino 2 — strategia di retrieval (wide/subject) sul modello caldo,
                        # solo quando la pre-gate cheap scatta; specific_search → invariato.
                        from core.retrieval_strategy import augment_context_with_ids
                        _recent_hist = [
                            {
                                "role": m["role"],
                                "content": m["content"],
                                "observed_at": m.get("observed_at"),
                            }
                            for m in st.session_state.messages
                        ]
                        context_full, _, augment_ids = augment_context_with_ids(
                            prompt, context_full, memory_manager, brain, _recent_hist
                        )
                        # Audit di Coerenza: registra il ctx effettivo del turno corrente.
                        # Include anche gli ID aggiunti dagli augment strategici: sono spesso
                        # proprio quelli che causano una risposta poi corretta da Stefano.
                        ctx_ids_effective = list(dict.fromkeys([*ctx_ids_now, *augment_ids]))
                        memory_manager.set_last_rag_ctx(ctx_ids_effective)
                        from core.honesty import scrub_unbacked_save_claim
                        from core.act_word_check import (
                            emit_unbacked_action_commitment,
                            scrub_unbacked_action_claim,
                        )
                        response = scrub_unbacked_save_claim(brain.respond(prompt, context=context_full))
                        emit_unbacked_action_commitment(r, response, set(), channel="silent_chat")
                        response = scrub_unbacked_action_claim(response, set())
                    st.markdown(response)

            # Salva risposta
            st.session_state.messages.append({
                "role": "assistant", "content": response, "observed_at": time.time()
            })

            # Log della conversazione — alimenta il passive learner
            memory_manager.log_conversation("Stefano", prompt)
            memory_manager.log_conversation("Euri", response)

            # Ogni 6 turni (3 scambi) lancia l'estrazione passiva inline
            if len(st.session_state.messages) % 6 == 0:
                try:
                    from core.validator import validate_payload
                    full_log = memory_manager.get_today_conversation()
                    st.session_state.chat_log_offset = len(full_log)
                    # extract_passive_memories vuole list[dict] con role/content
                    recent_msgs = st.session_state.messages[-6:]
                    if recent_msgs:
                        facts = brain.extract_passive_memories(recent_msgs)
                        saved = 0
                        for fact_item in facts:
                            from core.temporal_context import derive_passive_memory_metadata
                            weak_support = isinstance(fact_item, dict) and fact_item.get("support") == "weak"
                            fact = fact_item.get("content", "") if isinstance(fact_item, dict) else str(fact_item)
                            metadata = derive_passive_memory_metadata(
                                fact_item if isinstance(fact_item, dict) else {"content": fact},
                                recent_msgs,
                            )
                            clean = validate_payload(fact, "memory")
                            if not clean:
                                continue
                            if memory_manager.is_duplicate_memory(clean, llm_probe_fn=brain.probe_same_meaning):
                                continue
                            mid = memory_manager.save_memory(
                                clean,
                                category="passivo",
                                source="passive",
                                idempotent=True,
                                memory_kind=metadata["memory_kind"],
                                temporal_context=metadata["temporal_context"],
                            )
                            if mid and (weak_support or metadata["memory_kind"] == "conversation_anchor"):
                                from core.memory_attention import remove_loop2e_candidate
                                key = f"euri:memory:{mid}"
                                if weak_support:
                                    memory_manager.r.json().set(key, "$.requires_verification", True)
                                    memory_manager.r.json().set(key, "$.passive_support", "tacit_acceptance")
                                remove_loop2e_candidate(memory_manager.r, mid)
                            saved += 1
                        if saved:
                            st.caption(f"Passive learner: {saved} fatto/i memorizzato/i.")
                except Exception as e:
                    st.caption(f"Passive learner: errore ({e})")


    # ── PAGE 3: RAG EXPLORER ──────────────────────────────────────────────────────
    elif page == "RAG Explorer":
        st.title("🔍 RAG Explorer")
        st.markdown("Esplora le memorie e testa la ricerca vettoriale Domain-Gated.")
        
        search_query = st.text_input("Cerca nel database vettoriale...", "*")
        
        col1, col2 = st.columns([1, 3])
        
        # Filtro dominio
        try:
            res = r.execute_command("FT.AGGREGATE", "idx:memories", "*", "GROUPBY", "1", "@domain")
            domains = ["Tutti"]
            for row in res[1:]:
                if isinstance(row, list) and len(row) >= 2:
                    d = row[1].decode('utf-8') if isinstance(row[1], bytes) else str(row[1])
                    if d: domains.append(d)
        except Exception:
            domains = ["Tutti"]
            
        with col1:
            selected_domain = st.selectbox("Filtra Dominio", domains)
            limit = st.slider("Risultati massimi", 1, 20, 5)
            
        with col2:
            if search_query:
                if search_query == "*":
                    # Ricerca generica
                    q_str = f"*"
                    if selected_domain != "Tutti":
                        safe_domain = selected_domain.replace(" ", "\\ ")
                        q_str = f"@domain:{{{safe_domain}}}"
                        
                    from redis.commands.search.query import Query
                    q = Query(q_str).paging(0, limit).return_fields("id", "content", "domain", "created_at")
                    res = r.ft("idx:memories").search(q)
                    
                    st.success(f"Trovate {res.total} memorie.")
                    for doc in res.docs:
                        content = getattr(doc, 'content', 'Contenuto non disponibile')
                        with st.expander(f"[{getattr(doc, 'domain', 'generale')}] {content[:50]}..."):
                            st.write(f"**ID:** {doc.id}")
                            st.write(f"**Data:** {getattr(doc, 'created_at', 'N/A')}")
                            st.write(content)
                else:
                    # Ricerca vettoriale
                    st.info("Ricerca vettoriale (KNN) in corso...")
                    results = memory_manager.search_memories(search_query, limit=limit, touch=False)
                    
                    if selected_domain != "Tutti":
                        results = [x for x in results if x.get("domain") == selected_domain]
                        
                    for idx, res in enumerate(results):
                        with st.expander(f"#{idx+1} [Score: {res['score']:.3f}] [{res.get('domain', 'generale')}] {res['content'][:50]}..."):
                            st.write(res["content"])

        # ── TODO MANAGER ──────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📋 Todo Manager")

        pending = memory_manager.get_pending_todos()

        if not pending:
            st.info("Nessun todo pendente.")
        else:
            st.caption(f"{len(pending)} todo pendenti")
            for todo in pending:
                tid = todo.get("id", "")
                content = todo.get("content", "")
                due = todo.get("_due_at")
                due_str = due.strftime("%d/%m %H:%M") if due else "nessuna scadenza"

                with st.expander(f"⏰ {content[:60]} | {due_str}"):
                    new_content = st.text_input("Contenuto", value=content, key=f"edit_{tid}")
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if st.button("💾 Salva", key=f"save_{tid}"):
                            # Nota: l'embedding resta quello del contenuto originale;
                            # per ritocchi minori è accettabile, riscritture profonde
                            # meglio farle a voce (nuovo impegno).
                            r.json().set(f"euri:memory:{tid}", "$.content", new_content)
                            st.success("Aggiornato!")
                            st.rerun()
                    with c2:
                        if st.button("✅ Completa", key=f"done_{tid}"):
                            memory_manager.complete_todo(tid)
                            st.success("Completato!")
                            st.rerun()
                    with c3:
                        if st.button("🗑️ Elimina", key=f"del_{tid}"):
                            r.delete(f"euri:memory:{tid}")
                            st.success("Eliminato!")
                            st.rerun()

    # ── PAGE 4: VOLTI & ACCESSI ──────────────────────────────────────────────────
    elif page == "🪪 Volti & Accessi":
        import time as _time
        import uuid as _uuid
        from datetime import datetime as _dt

        st.title("🪪 Volti & Accessi")
        st.markdown(
            "Gestione delle persone che Euri **riconosce in faccia**. "
            "Solo il proprietario abilita l'efferente (Euri che parla per prima); "
            "gli altri abilitati attivano l'ascolto. Una faccia sconosciuta non fa parlare Euri."
        )
        st.warning(
            "**Il faceprint è un dato biometrico.** Registra solo persone che sanno di essere "
            "registrate e sono d'accordo — sul posto di lavoro non è un dettaglio (GDPR). "
            "Resta tutto locale: si salva solo il vettore matematico del volto, mai le foto. "
            "La rimozione qui sotto revoca ed elimina il dato."
        )

        @st.cache_resource
        def get_face_auth():
            from voice.face_auth import FaceAuth
            fa = FaceAuth()
            fa.load()
            return fa

        face_auth = get_face_auth()
        face_auth.reload_faceprints()

        if not face_auth._recognizer:
            st.error("Modello SFace non disponibile — controlla FACE_RECOG_MODEL in config.py.")
        else:
            # ── Persone registrate ────────────────────────────────────────────
            st.subheader("Persone registrate")
            names = face_auth.enrolled_names()
            if not names:
                st.info("Nessun faceprint registrato. Il daemon riconosce solo dopo l'enrollment.")
            for name in names:
                fpath = Path(config.FACEPRINT_DIR) / f"{name}.npy"
                created = _dt.fromtimestamp(fpath.stat().st_mtime).strftime("%d/%m/%Y %H:%M") if fpath.exists() else "?"
                owner_badge = " 👑 proprietario" if name == config.FACE_AUTH_OWNER else ""
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.write(f"**{name}**{owner_badge} — registrato il {created}")
                with c2:
                    if st.button("🗑️ Revoca", key=f"face_del_{name}"):
                        face_auth.remove(name)
                        st.success(f"Faceprint '{name}' eliminato. Il daemon lo dimentica entro 30 secondi.")
                        st.rerun()

            # ── Nuovo enrollment ─────────────────────────────────────────────
            st.markdown("---")
            st.subheader("Registra una persona")
            st.caption(
                "Servono 4 scatti con postura e angolazioni diverse. "
                "Un solo volto per scatto. La Control Room comanda il VisualGate: "
                "la webcam resta aperta una sola volta e nessuna immagine raggiunge il browser."
            )

            new_name = st.text_input("Nome (minuscolo, senza spazi)", key="face_enroll_name").strip().lower()
            consent = st.checkbox("La persona è informata e d'accordo alla registrazione del suo faceprint.")
            valid_name = bool(re.fullmatch(r"[a-z0-9_-]{1,48}", new_name))
            if new_name and not valid_name:
                st.error("Il nome può contenere solo lettere minuscole, numeri, _ e -.")

            pose_labels = (
                "posizione abituale",
                "seduto diritto",
                "viso leggermente a sinistra",
                "viso leggermente a destra",
            )
            request_key = config.FACE_ENROLLMENT_REQUEST_KEY

            def _enrollment_status(session_id: str) -> dict:
                try:
                    return json.loads(
                        r.get(f"{config.FACE_ENROLLMENT_STATUS_PREFIX}{session_id}") or "{}"
                    )
                except (TypeError, ValueError):
                    return {}

            def _send_enrollment(payload: dict) -> None:
                r.set(
                    request_key,
                    json.dumps(payload, ensure_ascii=False),
                    ex=config.FACE_ENROLLMENT_TTL_S,
                )

            active_session = st.session_state.get("face_enroll_session", {})
            if active_session and active_session.get("name") != new_name:
                _send_enrollment({**active_session, "action": "cancel"})
                st.session_state.face_enroll_session = {}
                active_session = {}

            if valid_name and consent and not active_session:
                if st.button("Avvia registrazione dal VisualGate", type="primary"):
                    session = {
                        "session_id": _uuid.uuid4().hex,
                        "name": new_name,
                        "action": "start",
                        "nonce": _uuid.uuid4().hex,
                        "pose_index": 0,
                    }
                    r.delete(
                        f"{config.FACE_ENROLLMENT_STATUS_PREFIX}{session['session_id']}"
                    )
                    _send_enrollment(session)
                    st.session_state.face_enroll_session = session
                    active_session = session

            if active_session:
                status = _enrollment_status(active_session["session_id"])
                captured = int(status.get("captured", 0) or 0)
                state = str(status.get("state", "waiting"))
                st.progress(
                    min(captured / len(pose_labels), 1.0),
                    text=f"{captured}/{len(pose_labels)} scatti acquisiti dal VisualGate",
                )

                if state == "completed":
                    face_auth.reload_faceprints()
                    st.success(
                        f"Faceprint di '{active_session['name']}' aggiornato con quattro posture."
                    )
                    if st.button("Chiudi registrazione"):
                        st.session_state.face_enroll_session = {}
                        st.rerun()
                else:
                    if state == "error":
                        st.error(status.get("message", "Scatto non riuscito: riprova."))
                    elif state in {"waiting", "ready"} and not status:
                        st.info("Connessione al VisualGate in corso...")

                    pose_index = min(captured, len(pose_labels) - 1)
                    st.info(f"Posizionati: **{pose_labels[pose_index]}**")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button(
                            "Acquisisci questa postura",
                            type="primary",
                            use_container_width=True,
                        ):
                            nonce = _uuid.uuid4().hex
                            request = {
                                **active_session,
                                "action": "capture",
                                "nonce": nonce,
                                "pose_index": captured,
                            }
                            _send_enrollment(request)
                            deadline = _time.monotonic() + 5.0
                            with st.spinner("VisualGate: acquisizione in corso..."):
                                while _time.monotonic() < deadline:
                                    result = _enrollment_status(request["session_id"])
                                    if result.get("nonce") == nonce:
                                        break
                                    _time.sleep(0.2)
                            st.rerun()
                    with c2:
                        if st.button("Annulla registrazione", use_container_width=True):
                            _send_enrollment({**active_session, "action": "cancel"})
                            st.session_state.face_enroll_session = {}
                            st.rerun()
            elif new_name and not consent:
                st.info("Spunta la casella del consenso per procedere con gli scatti.")

        # ── Calibrazione sociale numerica ────────────────────────────────────
        st.markdown("---")
        st.subheader("Calibrazione percezione sociale")
        from voice.social_profile import derive_profile, load_profile, profile_path, save_profile

        owner_id = config.FACE_AUTH_OWNER
        social_profile_path = profile_path(owner_id, config.SOCIAL_PERCEPTION_PROFILE_DIR)
        if social_profile_path.exists():
            try:
                active_profile = load_profile(social_profile_path, actor_id=owner_id)
                pc1, pc2, pc3 = st.columns(3)
                pc1.metric("Sorriso lieve", f"{active_profile.thresholds['smile_entry']:.2f}")
                pc2.metric("Margine", f"{active_profile.diagnostics['separation_margin']:.2f}")
                pc3.metric("Profilo", _dt.fromtimestamp(active_profile.created_at).strftime("%d/%m %H:%M"))
            except Exception as exc:
                st.warning(f"Profilo presente ma non valido: {exc}")
        else:
            st.info("Soglie generiche attive. Nessun profilo personale registrato.")

        try:
            social_latest = json.loads(r.get("euri:social:latest") or "{}")
        except (TypeError, ValueError):
            social_latest = {}
        social_age = _time.time() - float(social_latest.get("observed_at", 0.0) or 0.0)
        receptor_ready = (
            social_latest.get("actor_id") == owner_id
            and social_latest.get("calibrated") is True
            and social_age <= config.SOCIAL_PERCEPTION_LATEST_TTL_S
        )
        sc1, sc2, sc3 = st.columns(3)
        sc1.metric("Recettore", "Pronto" if receptor_ready else "In attesa")
        sc2.metric("Sorriso ora", f"{float(social_latest.get('metrics', {}).get('smile', 0.0)):.2f}")
        sc3.metric("Postura", f"{float(social_latest.get('auxiliary_metrics', {}).get('head_pitch_deg', 0.0)):.1f}°")

        if st.button(
            "Avvia calibrazione guidata",
            type="primary",
            disabled=not receptor_ready,
            use_container_width=True,
        ):
            phase_specs = (
                ("relaxed_neutral", "Posizione abituale, volto neutro", 12),
                ("upright_neutral", "Seduto diritto, volto neutro", 12),
                ("relaxed_smile", "Posizione abituale, sorriso lieve", 12),
                ("upright_smile", "Seduto diritto, sorriso lieve", 12),
            )
            captured: dict[str, list[dict]] = {}
            phase_box = st.empty()
            progress = st.progress(0.0)
            total_s = sum(item[2] for item in phase_specs)
            elapsed_s = 0.0

            for phase_name, instruction, duration_s in phase_specs:
                phase_box.info(instruction)
                rows: list[dict] = []
                seen: set[float] = set()
                started = _time.monotonic()
                while True:
                    phase_elapsed = _time.monotonic() - started
                    if phase_elapsed >= duration_s:
                        break
                    try:
                        current = json.loads(r.get("euri:social:latest") or "{}")
                    except (TypeError, ValueError):
                        current = {}
                    observed_at = float(current.get("observed_at", 0.0) or 0.0)
                    fresh = _time.time() - observed_at <= config.SOCIAL_PERCEPTION_LATEST_TTL_S
                    if (
                        fresh
                        and current.get("actor_id") == owner_id
                        and current.get("calibrated") is True
                        and observed_at not in seen
                    ):
                        rows.append(current)
                        seen.add(observed_at)
                    progress.progress(min((elapsed_s + phase_elapsed) / total_s, 1.0))
                    _time.sleep(0.25)
                captured[phase_name] = rows
                elapsed_s += duration_s

            progress.progress(1.0)
            phase_box.empty()
            try:
                profile = derive_profile(owner_id, captured)
                save_profile(profile, social_profile_path)
                r.set(
                    f"euri:social:calibration:{owner_id}",
                    json.dumps(
                        {
                            "profile": profile.to_dict(),
                            "sample_counts": {
                                name: len(samples) for name, samples in captured.items()
                            },
                        },
                        ensure_ascii=False,
                    ),
                )
                st.success(
                    "Calibrazione salvata. "
                    f"Soglia sorriso lieve: {profile.thresholds['smile_entry']:.2f}. "
                    "Il daemon la ricarica automaticamente."
                )
            except ValueError as exc:
                st.error(f"Calibrazione non applicata: {exc}")
