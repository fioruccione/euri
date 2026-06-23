import sys
import os
import queue as _queue
from pathlib import Path
import json

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
    brain._episode_callback = lambda summary: memory_manager.save_memory(
        summary,
        category="episodio", source="episode"
    )

# Layout generale: 2 colonne (Main a sinistra, Terminale a destra)
main_col, term_col = st.columns([2.5, 1.5], gap="large")

# Sidebar
st.sidebar.title("Euri Control Room 🧠")
st.sidebar.markdown("---")
page = st.sidebar.radio("Navigazione", ["🎙️ Voce", "Telemetria & Welford", "Silent Chat", "RAG Explorer"])

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
            todo_count = r.ft("idx:todos").info()["num_docs"]
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

        # Inizializza cronologia messaggi
        if "messages" not in st.session_state:
            st.session_state.messages = []
        if "chat_log_offset" not in st.session_state:
            st.session_state.chat_log_offset = len(memory_manager.get_today_conversation())

        # Mostra messaggi precedenti
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Input utente
        if prompt := st.chat_input("Scrivi a Euri..."):
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
                st.session_state.messages.append({"role": "user", "content": prompt})
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
                st.session_state.messages.append({"role": "assistant", "content": ack})
                memory_manager.log_conversation("Stefano", prompt)
                memory_manager.log_conversation("Euri", ack)
                st.stop()

            if _rx.BRIEFING_HINT_RE.search(prompt):
                _is_brief, _topic = _rx.understand_briefing(prompt)
                if _is_brief:
                    st.session_state.messages.append({"role": "user", "content": prompt})
                    with st.chat_message("user"):
                        st.markdown(prompt)
                    with st.chat_message("assistant"):
                        with st.spinner("Euri cerca un sogno da chiederti…"):
                            text, insight = _rx.run_briefing(memory_manager.r, embedder, _topic)
                        st.markdown(text)
                    st.session_state.messages.append({"role": "assistant", "content": text})
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
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Ricerca veloce su Redis per iniettare contesto — stesso builder della voce.
            with st.spinner("Cerco nella memoria..."):
                from core.rag_context import build_rag_context, infer_context_mode
                _context_mode = infer_context_mode(prompt, default="chat")
                _recent_hist = [
                    {"role": m["role"], "content": m["content"]}
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
                                {"role": _m["role"], "content": _m["content"]}
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
                            {"role": m["role"], "content": m["content"]}
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
            st.session_state.messages.append({"role": "assistant", "content": response})

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
                        for fact in facts:
                            clean = validate_payload(fact, "memory")
                            if not clean:
                                continue
                            if memory_manager.is_duplicate_memory(clean, llm_probe_fn=brain.probe_same_meaning):
                                continue
                            memory_manager.save_memory(clean, category="passivo", source="passive", idempotent=True)
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
                priority = todo.get("priority", "media")
                due_str = due.strftime("%d/%m %H:%M") if due else "nessuna scadenza"
                badge = "🔴" if priority == "alta" else "🟡" if priority == "media" else "🟢"

                with st.expander(f"{badge} {content[:60]} | {due_str}"):
                    new_content = st.text_input("Contenuto", value=content, key=f"edit_{tid}")
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        if st.button("💾 Salva", key=f"save_{tid}"):
                            r.json().set(f"euri:todo:{tid}", "$.content", new_content)
                            st.success("Aggiornato!")
                            st.rerun()
                    with c2:
                        if st.button("✅ Completa", key=f"done_{tid}"):
                            memory_manager.complete_todo(tid)
                            st.success("Completato!")
                            st.rerun()
                    with c3:
                        if st.button("🗑️ Elimina", key=f"del_{tid}"):
                            r.delete(f"euri:todo:{tid}")
                            st.success("Eliminato!")
                            st.rerun()
