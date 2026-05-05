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
            # Ignora durante cooldown post-TTS
            if _t.time() < self._cooldown_end:
                return frame

            arr = frame.to_ndarray()

            # Normalizza in base al dtype reale (più affidabile del nome del formato)
            if arr.dtype == np.int16:
                pcm = arr.astype(np.float32) / 32768.0
            elif arr.dtype == np.int32:
                pcm = arr.astype(np.float32) / 2147483648.0
            else:
                pcm = arr.astype(np.float32)

            # → mono: usa frame.channels per reshape corretto su qualsiasi layout
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
            return frame

    return _Proc(), audio_q

# Inizializzazione
r = get_redis()
embedder = get_embedder()
brain = get_brain()
memory_manager = MemoryManager(r, embedder)
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
                mode=WebRtcMode.SENDONLY,
                rtc_configuration={"iceServers": [
                    {"urls": ["stun:stun.l.google.com:19302"]},
                    {"urls": ["stun:stun1.l.google.com:19302"]},
                    {"urls": ["stun:stun2.l.google.com:19302"]},
                ]},
                audio_frame_callback=_proc.recv,
                media_stream_constraints={"audio": True, "video": False},
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
                    # Svuota la coda: audio arrivato durante l'elaborazione viene scartato.
                    # Senza questo, al termine del processing il chunk accumulato riparte
                    # subito verso il daemon creando un loop.
                    try:
                        while True:
                            _audio_q.get_nowait()
                    except _queue.Empty:
                        pass
                    return
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

            audio_input = st.audio_input("🎤 Registra e invia")

            if audio_input is not None and not st.session_state.mobile_waiting:
                raw = audio_input.getvalue()
                with io.BytesIO(raw) as buf:
                    with wave.open(buf) as wf:
                        sr_wav = wf.getframerate()
                        n_ch   = wf.getnchannels()
                        frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                if n_ch > 1:
                    audio = audio.reshape(-1, n_ch).mean(axis=1)
                # Manda al daemon al sample rate originale (il daemon risampla)
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
                            "Soglia Adattiva (est.)": round(config.ADAPTIVE_CLASSIFIER_BASE_THRESHOLD * (1 + config.ADAPTIVE_CLASSIFIER_VARIANCE_WEIGHT * state.get("std", 0.0)), 3)
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
            # Mostra utente
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Ricerca veloce su Redis per iniettare contesto
            with st.spinner("Cerco nella memoria..."):
                import time as _time

                def _age_label(ts):
                    try:
                        days = (_time.time() - float(ts)) / 86400 if ts else None
                    except Exception:
                        days = None
                    if days is None: return ""
                    if days < 1: return "oggi"
                    if days < 7: return f"{int(days)}g fa"
                    if days < 30: return f"{int(days/7)}sett fa"
                    if days < 365: return f"{int(days/30)}mesi fa"
                    return f"{int(days/365)}anni fa"

                def _fmt(r):
                    age = _age_label(r.get("created_at"))
                    label = f"[{r.get('domain', 'generale')} | {age}]" if age else f"[{r.get('domain', 'generale')}]"
                    return f"- {label} {r['content']}"

                # Memorie recenti per timestamp (sempre — danno continuità dopo riavvio)
                recent = memory_manager.get_recent_memories(limit=4)
                recent_ids = {r.get("id") for r in recent}

                # Memorie semanticamente rilevanti per la query corrente
                semantic = [r for r in memory_manager.search_memories(prompt, limit=3)
                            if r.get("id") not in recent_ids]

                insights = memory_manager.search_insights(prompt, limit=2)

                context = ""
                if recent:
                    context += "MEMORIE RECENTI (ultime per data):\n"
                    context += "\n".join(_fmt(r) for r in recent) + "\n"
                if semantic:
                    context += "\nMEMORIE CORRELATE ALLA DOMANDA:\n"
                    context += "\n".join(_fmt(r) for r in semantic) + "\n"
                if insights:
                    context += "\nPRINCIPI TRASVERSALI:\n"
                    context += "\n".join(
                        f"- [{i.get('domain_a','?')} ↔ {i.get('domain_b','?')}] {i['content']}"
                        for i in insights
                    ) + "\n"

            # Risposta Euri
            with st.chat_message("assistant"):
                with st.spinner("Euri sta pensando..."):
                    chat_hint = "[Modalità chat testuale — nessun vincolo TTS. Puoi rispondere con più profondità, sviluppare i concetti, fare domande di ritorno. Sii presente e partecipe come in una conversazione reale.]"
                    context_full = (context + "\n\n" + chat_hint) if context else chat_hint
                    response = brain.respond(prompt, context=context_full)
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
                            memory_manager.save_memory(clean, category="passivo", source="passive")
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
                    results = memory_manager.search_memories(search_query, limit=limit)
                    
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
