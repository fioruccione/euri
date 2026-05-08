# Euri — Sistema Cognitivo Adattivo

Euri è un assistente personale intelligente, vocale e **completamente offline-first**, che evolve insieme a te.  
Non si limita ad ascoltare e rispondere: memorizza, organizza, riflette sulle tue conversazioni per generare nuove intuizioni, e ora **può elaborare i tuoi file locali e analizzare immagini** tramite codice Python generato al volo.

> **Deployment attuale:** Workstation Linux (Pop!_OS) con doppia GPU NVIDIA RTX 4060 Ti 16GB.  

---

## Architettura Cognitiva (V2.7)

### 1. Intent Classification — Pipeline a Due Layer
La classificazione dell'intent è a cascata: il layer veloce esaurisce la maggior parte dei casi, il layer lento interviene solo quando necessario.

**Layer 1 — Regex Router (0ms):** ~18 categorie di intent con pattern ordinati per specificità. Copre la quasi totalità dei comandi strutturati (SAVE_MEMORY, SAVE_TODO, WEB_SEARCH, EXECUTE, TEACH, DICTATION…).

**Layer 2 — LLM Fallback Gemma 26B (~600ms):** chiamato *solo* quando il router restituisce CHAT. Classifica 7 intent critici (WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, COMPLETE, CHAT) con un prompt a definizioni precise. COMPLETE è gestito interamente dal LLM — il contesto conversazionale distingue "l'ho fatto" da narrazioni complesse che il regex non può disambiguare.

**Guard manifatturiero:** se la frase contiene termini chimici/analitici (XRF, talco, MFI, carbonato…) senza termini di sistema espliciti, EXECUTE viene bloccato in entrambi i layer.

> **AdaptiveClassifier (Welford) — sospeso:** con e5-large 1024-dim, il costo di encoding (~400ms) era uguale al LLM fallback, con il rischio aggiuntivo di corruzione dei centroidi per feedback loop su classificazioni errate. Architettura sospesa: il codice è presente ma `ADAPTIVE_CLASSIFIER_ENABLED = False`. Riabilitabile solo con un modello di embedding leggero dedicato all'intent.

### 2. Domain Gating + Ricerca 3-Livelli (RAG Autonomo)
Tutte le memorie estratte dalle conversazioni vengono lette dall'LLM, che assegna loro automaticamente delle "etichette di dominio" (es. *informatica, chimica, business, casa*).

Il recupero avviene a tre livelli in cascata:
1. **Identifier-first** — estrae dalla query acronimi (MFI, DCP), codici lotto (PPR-738P) e numeri decimali (3.2, 0.35%) e li cerca con keyword search diretta. Garantisce che fatti tecnici specifici vengano restituiti in cima anche quando il dominio è saturo di memorie simili.
2. **Domain-gated KNN** — ricerca vettoriale filtrata per dominio. Se il dominio ha pochi risultati, scala all'intero DB.
3. **Hybrid fill** — se i risultati sono ancora sotto il limite, `_search_hybrid` (semantic + safe_keywords) riempie i posti rimanenti.

### 3. Dream Engine (Sogni Onirici in background)
Quando non gli parli da almeno 2 ore, Euri "dorme" ed entra nel ciclo onirico.
- Pesca due memorie appartenenti a due domini *completamente diversi*.
- **Loop 2b** — Chiede a **Qwen3.6 35B** (*thinking attivo*, modello dedicato) di cercare isomorfismi strutturali tra i due concetti usando un processo in 3 passi: astrazione logica → ricerca della dinamica condivisa → formulazione del principio generale. Qwen3.6 è separato da Gemma4: più lento ma con ragionamento astratto superiore, usato solo nei cicli notturni senza vincoli di latenza.
- Se l'analogia è forte, genera un **CANDIDATE Insight**.
- **Loop 2c** — La promozione CANDIDATE→PROMOTED usa un sistema a due livelli: distanza cosine vettoriale (fast path) + **LLM judge con thinking** per la zona grigia (score 0.15–0.40). Il judge valuta se due insight formulati diversamente esprimono lo stesso principio strutturale profondo — un giudizio che il solo vettore cosine non può dare.
- Se abbastanza sogni indipendenti convergono, l'insight viene **PROMOSSO** e scritto permanentemente in Obsidian.

> **Nota tecnica:** Il timer di idle usa `time.time()` (wall-clock) per contare correttamente anche le ore in cui il PC è in sospensione.

### 4. Il Secondo Cervello (Integrazione Obsidian)
Euri è bidirezionalmente sincronizzato con **Obsidian** (cartella `EuriVault`).
- Tutte le memorie salvate e classificate compaiono come file Markdown categorizzati nelle cartelle dei domini in Obsidian.
- Gli **Insight Promossi** dal Dream Engine vengono scritti in Obsidian e generano collegamenti (`[[link]]`) visibili nel *Graph View*, mostrando l'evoluzione della sua rete neurale semantica.
- Se modifichi un testo dentro Obsidian, il Watcher in background aggiorna silenziosamente i database e i vettori di Euri su Redis.

### 5. CodeRunner — Data Orchestrator (Mani Digitali) ⭐ Nuovo in V2.1
Euri può ora **manipolare file locali** tramite comandi vocali. Genera script Python con Gemma, li valida con un SecurityScanner AST, e li esegue in un subprocess isolato e interrompibile.

**Formati supportati:**
| Formato | Estensioni | Libreria |
|---|---|---|
| PDF | `.pdf` | PyPDF2, pypdf |
| Excel | `.xlsx`, `.xls` | openpyxl, xlsxwriter |
| LibreOffice | `.ods`, `.odt`, `.odp` | odfpy |
| Testo strutturato | `.csv`, `.txt`, `.json`, `.xml` | pandas, csv, json |
| Immagini | `.jpg`, `.png`, `.bmp`, `.webp` | PIL (Pillow) |
| Grafici | (output) | matplotlib |
| Report PDF | (output) | reportlab |

**Sicurezza (SecurityScanner AST):**
- Whitelist di import: solo librerie sicure e approvate.
- Blacklist di pattern: `os.system`, `subprocess`, `socket`, `eval`, `exec`, `__import__` sono bloccati staticamente prima dell'esecuzione.
- Timeout 30s, ambiente subprocess sanitizzato (nessuna variabile d'ambiente sensibile).
- Interrupt vocale: dire "Stop" durante l'esecuzione termina immediatamente il processo.

**Cartelle I/O:**
- Input: `~/Scrivania/dati_per_Euri/`
- Output: `~/Scrivania/scambio_dati/`

### 6. Visione Artificiale ⭐ Nuovo in V2.1
Euri può analizzare immagini locali usando **Gemma 4 Vision** (multimodale), senza nessun servizio esterno. Basta mettere un'immagine nella cartella dati e chiedere: *"Analizza la foto"*.

### 7. Control Room (Streamlit UI)
Un'interfaccia web leggera (`ui/app.py`) per:
- Monitorare la telemetria dei classificatori (AdaptiveClassifier sospeso — sezione disponibile ma non aggiornata).
- Chattare silenziosamente (senza far scattare il Passive Learner vocale).
- Esplorare e interrogare il database RAG.

---

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Ragionamento / LLM (conversazione) | Ollama — `gemma4:26b` |
| Ragionamento / LLM (Dream Engine) | Ollama — `qwen3.6:35b` |
| Visione Artificiale | Gemma 4 Vision (multimodale, offline) |
| Memoria Attiva | Redis Stack (JSON + RediSearch full-text + VECTOR embedding) |
| Memoria Passiva/UI | Obsidian Vault sincronizzato via `watchdog` |
| STT / Trascrizione | faster-whisper `large-v3-turbo` (CUDA float16 — NVIDIA RTX 4060 Ti) |
| TTS / Voce | sherpa-onnx + Piper (`vits-piper-it_IT-paola-medium`) |
| Embedding | sentence-transformers `intfloat/multilingual-e5-large` (1024-dim, asimmetrico query/passage) |
| Classificatore Veloce | ~~Welford AdaptiveClassifier~~ — sospeso (vedi sezione 1) |
| Web search | ddgs (DuckDuckGo, no API key) + beautifulsoup4 |
| Gate visivo | OpenCV Haar cascade (webcam, 2fps) |
| CodeRunner / Sandbox | subprocess isolato + AST SecurityScanner |

---

## Come Avviare il Sistema

### Avviare la Voce di Euri
```bash
euri   # alias configurato in ~/.bashrc, lancia il Voice Daemon
```
Oppure:
```bash
cd /home/fio/Euri && ./start_euri.sh
```

### Avviare la Control Room (Streamlit)
In un terminale **separato**:
```bash
cd /home/fio/Euri
./venv/bin/streamlit run ui/app.py
```
*(Il parametro `--server.fileWatcherType=none` è già impostato nel file di configurazione interno `.streamlit/config.toml`)*

### Avviare Obsidian
Apri l'applicazione Obsidian e usa "Open folder as vault" per aprire:
`/home/fio/EuriVault`

### Forzare un "Sogno" (Test del Dream Engine)
```bash
cd /home/fio/Euri
./venv/bin/python force_dream.py
```

---

## Comandi Vocali — Guida Rapida

### Memoria e Apprendimento
| Comando | Cosa fa |
|---|---|
| *"Ricordami che..."* / *"Segna che..."* | Salva una memoria immediata |
| *"Salva tutto"* | Il Passive Learner salva il riassunto della conversazione |
| *"Cosa sai di me?"* | Audit delle memorie salvate |
| *"Ti racconto una cosa..."* | Modalità insegnamento esplicito |

### Elaborazione File (CodeRunner)
| Comando | Cosa fa |
|---|---|
| *"Analizza il documento PDF"* | Estrae e riassume il testo del PDF |
| *"Leggi il file Excel e dimmi..."* | Carica ed elabora il foglio di calcolo |
| *"Unisci i CSV nella cartella dati"* | Genera ed esegue script di merge |
| *"Converti il file LibreOffice in CSV"* | Trasforma formati |
| *"Genera un grafico dai dati"* | Crea immagine con matplotlib |
| *"Stop"* (durante esecuzione) | Interrompe immediatamente lo script |

### Visione e Immagini
| Comando | Cosa fa |
|---|---|
| *"Analizza la foto nella cartella dati"* | Gemma 4 Vision descrive l'immagine |
| *"Controlla l'immagine"* / *"Guarda la foto"* | Idem |
| *"Cosa c'è nella cartella dati?"* | Elenca i file disponibili |

### Assistenza Generale
| Comando | Cosa fa |
|---|---|
| *"Cerca nel web..."* | Ricerca DuckDuckGo + sintesi dei risultati |
| *"Calcola..."* | Valutatore matematico sicuro |
| *"Scrivi: [testo]"* | Salva su file e copia negli appunti |
| *"Spegniti"* | Chiude Euri correttamente |

---

## Salvataggio delle Informazioni

### Salvataggio Vocale
- **Memoria:** *"Ricordami che..."* / *"Segna che..."*
- **Todo con scadenza:** *"Devo fare X fra 5 minuti"*
- **Passive Learner:** Euri ascolta passivamente e dopo 45 secondi di silenzio salva informazioni utili in background.

### Salvataggio via Dropzone (Obsidian)
Crea una nota testuale nella cartella `EuriVault/Dropzone` in Obsidian e scrivi quello che ti serve. Euri lo leggerà, classificherà il dominio, sposterà il file e lo inserirà nel suo database RAG in meno di un secondo.

---

## Changelog

### V2.7 — Ricerca Memoria 3-Livelli + SpeakerAuth Monitoring

- **Ricerca memoria identifier-first:** `search_memories()` ora opera a 3 livelli: (1) estrazione identificatori dalla query (acronimi, codici lotto, numeri decimali) → keyword search diretta in Redis; (2) domain-gated KNN semantico; (3) hybrid fill con `_search_hybrid`. Garantisce che fatti tecnici specifici (MFI lotto, concentrazioni DCP, codici progetto) non vengano sepolti da memorie semanticamente centrali già consolidate nello stesso dominio. Test automatizzato end-to-end: 3/3 storage, recall semantico, pipeline LLM.
- **SpeakerAuth similarity logging:** similarity score portato da DEBUG a INFO — visibile nel log normale per monitorare la soglia in produzione e calibrarla su voci simili (es. colleghi con timbro analogo).
- **`test_memory.py`:** script autonomo di test mnemonico che inietta fatti sintetici, verifica storage Redis, recall semantico e pipeline LLM completa, poi pulisce. Rilanciabile in qualsiasi momento.

### V2.6 — Quality Audit + Numerical Verification + Dream Engine Format

- **Audit qualità memorie passive:** campione 50 memorie valutato da Stefano → 52% accurate, 22% false, 26% generiche. Eliminate 3 memorie pericolose con dosaggi errati (veleno operativo in contesto manifatturiero).
- **`requires_verification` flag:** `save_memory()` detecta automaticamente contenuti con numeri, percentuali, dosaggi e unità di misura (regex su cifre+unità). Il campo viene scritto nel documento JSON. In `_build_context()`, le memorie flaggate vengono iniettate nel prompt con il suffisso `[DATO NON VERIFICATO — contiene valori numerici]` — Euri le cita con cautela invece che come fatti certi. Le memorie precedenti senza il campo non sono impattate.
- **Dream Engine — formato strutturato:** riscritto il prompt di `_generate_dream()`. Output ora forzato in tre righe etichettate: "Nel dominio [X] succede: [concreto]", "Nel dominio [Y] succede: [concreto]", "La connessione operativa non ovvia è: [effetto pratico verificabile]". Insight senza tutte e tre le righe vengono scartati prima della promozione. Eliminato il template filosofico precedente che produceva principi astratti formulati in modo elaborato.
- **Audit insight PROMOTED:** 30 insight valutati → 27% genuinamente non ovvi. Difetto principale identificato: template di scrittura uniforme rendeva impossibile distinguere insight profondi da banalità. Il nuovo formato forza la distinzione a monte.

### V2.5 — Memory TTL
- **Memory TTL:** sincronizzazione `r.expireat()` con `expires_at` JSON. Sliding window operativa: ogni recall estende il TTL di 90 giorni. Loop 2a come safety net per memorie pre-fix.

### V2.4 — Stabilità Architetturale + Document Routing + Concorrenza

**Fix concorrenza (bug silenzioso critico):**
- **Race condition passive learner**: `brain._conversation_history` veniva letta senza lock dal passive learner (thread background) mentre `_compress_episode()` (altro thread daemon) poteva rimpiazzare la lista con `self._conversation_history = self._conversation_history[CHUNK:]`. Risultato: `_passive_history_len` si desincronizzava silenziosamente, con un epoch intero di apprendimento perso senza log di errore. Fix: `Brain.history_lock` (threading.Lock) protegge ora tutte le letture e scritture su `_conversation_history` — sia in `respond()` che in `_compress_episode()`, e con snapshot `list(...)` nel passive learner e in `_handle_save_last`.

**State machine timeout:**
- Sostituiti i tre pattern `(dict, float)` sparsi nell'`__init__` (`_pending_todo` + `_pending_todo_ts`, `_pending_write` + `_pending_write_ts`) con la classe `_PendingState(data, timeout)` e metodo `.expired()`. Il timeout è codificato nel costruttore, non nel sito di controllo in `_dispatch`. Pattern uniforme per ogni stato temporaneo futuro.

**`_last_speech_content` TTL:**
- Aggiunto `_last_speech_ts` — il contenuto dell'ultima risposta lunga scade dopo 300 secondi. Prima, una risposta di ore prima poteva essere salvata silenziosamente da un misrecognition STT che triggerava `_SAVE_REPLY_RE`.

**Routing documenti di testo:**
- "Crea un documento di testo con tutti questi valori" non va più a CodeRunner. Rimosso da EXECUTE il pattern `\bcrea[ri]?\s+(un\s+)?(file|riassunto|testo|documento|report)\b` e `document[io]` dalla lista format (riga 160). Estesa `_WRITE_REQUEST_RE` con forme imperative senza "potresti/puoi": `crea (un) documento`, `scrivimi (un) testo`, `generami (un) schema`.
- `_handle_pending_write` ora distingue: task con formati dati strutturati (csv, excel, pdf…) → CodeRunner; task generico di testo → LLM compone il documento dalla conversazione recente → `tool_write_text`. In entrambi i casi il flusso passa per la conferma vocale.

### V2.3 — Embedding Upgrade + Mobile Voice + Memory Coherence + Intent Routing

- **Embedding: MiniLM → multilingual-e5-large**: sostituito `paraphrase-multilingual-MiniLM-L12-v2` (384-dim) con `intfloat/multilingual-e5-large` (1024-dim). Encoding asimmetrico: `"query: "` per ricerche/classificazione, `"passage: "` per il salvataggio in Redis. Migrazione completa: 306 memorie + 92 insight ri-embeddati, indici Redis ricreati a DIM=1024, fingerprint Welford resettate.
- **WebRTC mobile voice (iOS Safari)**: risolto il problema "in ascolto ma nessun frame audio" su iPhone. Causa radice: con `WebRtcMode.SENDONLY`, iOS Safari non attiva il proprio encoder audio. Fix: `WebRtcMode.SENDRECV` + silence frame (`np.zeros_like(arr)`) — nessun echo, VAD invariato.
- **Memory coherence**: system prompt aggiornato — la conversazione corrente è memoria quanto Redis. Fix per "Non ho niente in memoria" su fatti discussi in sessione.
- **Ricerca temporale additiva**: `_build_context` ora parsa riferimenti temporali italiani ("ieri", "5 maggio", "lunedì", "due giorni fa" ecc.) e prepend le memorie Redis di quel periodo al contesto. Implementata via filtro numerico `@created_at` su RediSearch — additiva, non restrittiva.
- **AdaptiveClassifier disabilitato**: con e5-large (1024-dim) il Welford aveva stessa latenza del LLM (~400ms) con falsi positivi sistematici. Routing ora: regex (0ms) → LLM Gemma (600ms). Fingerprint Redis puliti.
- **Prompt LLM intent riscritto**: definizioni precise per EXECUTE (solo hardware esplicito), SEARCH (memoria interna vs internet), COMPLETE (aggiunto con guard anti-narrazione), WEB_SEARCH, SAVE_TODO, SAVE_MEMORY, CHAT.
- **COMPLETE migrato a LLM**: rimossi i pattern regex ambigui ("ho fatto X", "l'ho fatto") — il LLM distingue narrazione da completamento task usando il contesto. Restano nel regex solo le utterance isolate inequivocabili.
- **Guard manifatturiero EXECUTE**: se la frase contiene termini chimici/analitici (XRF, talco, carbonato, MFI...) senza termini di sistema, EXECUTE viene bloccato a prescindere dal classificatore.
- **RESTORE_ALERTS e SHUTDOWN** esentati dal blocco silence mode — si può uscire dalla modalità silenziosa a voce anche se Euri ignora tutto il resto.
- **SpeakerAuth**: rigetto voci non riconosciute silenzioso (rimossa risposta vocale "prendo ordini solo da Stefano").
- **`_build_context` semantic fix**: `search_memories` e `search_insights` ora ricevono il testo completo invece del join di keyword — e5-large e `assign_domain` lavorano su linguaggio naturale.

### V2.2 — Thinking nei Loop Cognitivi + Qwen3.6 Dream Engine
- **Architettura dual-model**: `DREAM_OLLAMA_MODEL = "qwen3.6:35b"` separato da `OLLAMA_MODEL`. Gemma4 26B per la conversazione vocale (latenza < 2s), Qwen3.6 35B per i cicli onirici notturni (nessun vincolo di latenza, ragionamento astratto superiore).
- **Prompt analogico in 3 passi** (`_generate_dream`): astrazione logica → ricerca della dinamica condivisa → formulazione del principio generale. Evita connessioni superficiali senza forzare un dominio specifico.
- **Timeout LLM alzato a 150s** nel Dream Engine (Qwen3.6 impiega ~85s per il judge; il precedente 90s era troppo vicino al limite).
- **Contesto temporale relativo nelle memorie**: ogni memoria iniettata nel contesto ora include l'età relativa — `[chimica polimeri | 3 settimane fa]` invece di `[chimica polimeri]`. Euri sa quando ha imparato ogni cosa e può ragionarci sopra spontaneamente. Stesso meccanismo nel Dream Engine: le memorie portano il loro `created_at` nel prompt del sogno, abilitando insight evolutivi oltre agli isomorfismi strutturali.
- **Silent Chat integrata nel Passive Learner**: la chat testuale ora chiama `log_conversation()` e trigger l'estrazione passiva ogni 6 messaggi — stessa pipeline del voice daemon.
- **Fix Dream Engine hang notturno**: wrapper `_ollama_chat()` con `ThreadPoolExecutor` — se Ollama non risponde entro il timeout il ciclo viene abortito pulitamente.
- **Fix intent router**: 3 pattern regex tightened per evitare falsi positivi su linguaggio manifatturiero (`risultato di`, `percentuale di`, `monitoraggio`).
- **Episodic Compression (Layer 0)**: ogni 30 messaggi, i 20 più vecchi vengono compressi in un episodio e iniettati come sistema message nelle chiamate successive. TTL 7 giorni.


- **Thinking attivo (Loop 2b)**: `_generate_dream()` usa `think=True` con `num_predict=2000`. Prima il cap di 100 token troncava la risposta dopo il ragionamento interno; ora Gemma ragiona liberamente prima di formulare l'insight.
- **Thinking attivo (Loop 2a)**: `generate_reflection()` usa `think=True` con `num_predict=1000`. Il consolidamento silenzioso delle memorie produce sintesi più accurate.
- **Thinking attivo (Passive Learner)**: `extract_passive_memories()` usa `think=True` con `num_predict=2000`. L'estrazione di fatti dalla conversazione è più precisa e selettiva.
- **Thinking attivo (TEACH)**: `summarize_knowledge()` usa `think=True` con `num_predict=2000`. La sintesi delle sessioni di insegnamento esplicito è più fedele e completa.
- **LLM Judge in Loop 2c**: aggiunto `_llm_judge_same_insight()` con `think=True`. La promozione degli insight ora usa un sistema a due livelli — vettore cosine (< 0.15: certo) + giudizio LLM ragionato (0.15–0.40: zona grigia) — invece del solo embedding superficiale.
- **Path vocale invariato**: `respond()`, `decide_tool_call()`, `translate()` e tutti i path real-time restano con `think=False` per preservare la latenza.
- **Fix critico Domain Gating** (`domain_gater.py`): il validatore rifiutava qualsiasi etichetta con uno spazio, mandando l'80% delle memorie a `"generale"` e svuotando di efficacia il RAG domain-gated. Ora i domini a due parole (es. "chimica polimeri", "stampaggio iniezione") vengono accettati correttamente.
- **Executor regex CodeRunner** (`executor.py`): pattern estesi per riconoscere più formati file e comandi vocali; sentinella `__USER_TEXT__` per passare la frase originale al task `run_code`.

### V2.1 — CodeRunner Data Orchestrator
- **CodeRunner** (`agent/code_runner.py`): SecurityScanner AST + Subprocess sandbox interrompibile.
- **3 nuovi tool** nell'Executor: `run_code`, `analyze_image`, `list_data_files`.
- **Visione Artificiale**: analisi immagini tramite Gemma 4 Vision (multimodale, offline).
- **Formati supportati**: PDF, Excel, LibreOffice (ODS/ODT/ODP), CSV, JSON, TXT, immagini.
- **Fix Dream Engine**: corretto bug `time.monotonic()` → `time.time()` per PC con sospensione.
- **Cartelle I/O**: `~/Scrivania/dati_per_Euri/` → `~/Scrivania/scambio_dati/`.

### V2.0 — Sistema Cognitivo Adattivo
- Welford Adaptive Classifier su Redis.
- Dream Engine (sogni onirici e insight notturni).
- Integrazione bidirezionale Obsidian (Vault + Dropzone).
- Passive Learner (apprendimento implicito).
- RediSearch full-text + VECTOR KNN.

### V1.0 — Voice Assistant
- STT con faster-whisper CUDA.
- TTS con sherpa-onnx + Piper.
- RAG base su Redis.
- Gate visivo con OpenCV.
