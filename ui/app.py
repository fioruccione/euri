import sys
import os
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
page = st.sidebar.radio("Navigazione", ["Telemetria & Welford", "Silent Chat", "RAG Explorer"])

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

    # ── PAGE 1: TELEMETRIA ────────────────────────────────────────────────────────
    if page == "Telemetria & Welford":
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
                results = memory_manager.search_memories(prompt, limit=3)
                context = ""
                if results:
                    context = "MEMORIE CORRELATE TROVATE IN REDIS:\n"
                    for r in results:
                        import time as _time
                        ts = r.get("created_at")
                        try:
                            days = (_time.time() - float(ts)) / 86400 if ts else None
                        except Exception:
                            days = None
                        if days is None:
                            age = ""
                        elif days < 1:
                            age = "oggi"
                        elif days < 7:
                            age = f"{int(days)}g fa"
                        elif days < 30:
                            age = f"{int(days/7)}sett fa"
                        elif days < 365:
                            age = f"{int(days/30)}mesi fa"
                        else:
                            age = f"{int(days/365)}anni fa"
                        label = f"[{r.get('domain', 'generale')} | {age}]" if age else f"[{r.get('domain', 'generale')}]"
                        context += f"- {label} {r['content']}\n"

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
