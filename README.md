# Euri — Sistema Cognitivo Adattivo

Euri è un assistente personale intelligente, vocale e **completamente offline-first**, che evolve insieme a te.  
Non si limita ad ascoltare e rispondere: memorizza, organizza, riflette sulle tue conversazioni per generare nuove intuizioni, e ora **può elaborare i tuoi file locali e analizzare immagini** tramite codice Python generato al volo.

> **Deployment attuale:** Workstation Linux (Pop!_OS) con doppia GPU NVIDIA RTX 4060 Ti 16GB.  

---

## Architettura Cognitiva (V2.1)

### 1. Adaptive Intent Classification (Apprendimento Online Welford)
Il sistema di classificazione degli intenti è dinamico. Quando parli, l'Adaptive Classifier usa l'algoritmo di **Welford** per confrontare la tua frase con i "centroidi" matematici (embedding) dei vari comandi (es. CHAT, EXECUTE, SEARCH).  
Se la classificazione fallisce, l'LLM pesante interviene, corregge il tiro, e il sistema **aggiorna i centroidi matematici in tempo reale su Redis**.  
*Risultato:* Euri impara il tuo gergo personale e diventa sempre più veloce (5ms) a capirti senza usare la GPU.

### 2. Domain Gating (RAG Autonomo)
Tutte le memorie estratte dalle conversazioni vengono lette dall'LLM, che assegna loro automaticamente delle "etichette di dominio" (es. *informatica, chimica, business, casa*).  
Durante il recupero delle informazioni, Euri restringe prima la ricerca al dominio pertinente e poi, se non trova nulla, scala all'intero database vettoriale.

### 3. Dream Engine (Sogni Onirici in background)
Quando non gli parli da almeno 2 ore, Euri "dorme" ed entra nel ciclo onirico.
- Pesca due memorie appartenenti a due domini *completamente diversi*.
- Chiede all'LLM (Gemma 4) di cercare isomorfismi o analogie creative tra i due concetti.
- Se l'analogia è forte, genera un **CANDIDATE Insight**.
- Se più sogni indipendenti confermano la stessa analogia, l'insight viene **PROMOSSO** e scritto permanentemente.

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
- Monitorare la telemetria di Welford (vedere come si stanno spostando i pesi matematici dell'apprendimento).
- Chattare silenziosamente (senza far scattare il Passive Learner vocale).
- Esplorare e interrogare il database RAG.

---

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Ragionamento / LLM | Ollama — `gemma4:26b` |
| Visione Artificiale | Gemma 4 Vision (multimodale, offline) |
| Memoria Attiva | Redis Stack (JSON + RediSearch full-text + VECTOR embedding) |
| Memoria Passiva/UI | Obsidian Vault sincronizzato via `watchdog` |
| STT / Trascrizione | faster-whisper `large-v3-turbo` (CUDA float16 — NVIDIA RTX 4060 Ti) |
| TTS / Voce | sherpa-onnx + Piper (`vits-piper-it_IT-paola-medium`) |
| Embedding | sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 |
| Classificatore Veloce | Algoritmo di Welford su Vettori (Aggiornamento Online) |
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
