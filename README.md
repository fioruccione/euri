# Euri — Sistema Cognitivo Adattivo

Euri è un assistente personale intelligente, vocale e **completamente offline-first**, che evolve insieme a te.  
Non si limita ad ascoltare e rispondere: memorizza, organizza e riflette sulle tue conversazioni per generare nuove intuizioni e adattarsi al tuo linguaggio.

> **Deployment attuale:** Workstation Linux (Pop!_OS) con doppia GPU NVIDIA RTX 4060 Ti 16GB.  

---

## Architettura Cognitiva (V2)

Con l'aggiornamento alla V2, Euri è passato dall'essere un semplice assistente vocale a un sistema cognitivo auto-organizzante.

### 1. Adaptive Intent Classification (Apprendimento Online Welford)
Il sistema di classificazione degli intenti è dinamico. Quando parli, l'Adaptive Classifier usa l'algoritmo di **Welford** per confrontare la tua frase con i "centroidi" matematici (embedding) dei vari comandi (es. CHAT, EXECUTE, SEARCH).  
Se la classificazione fallisce, l'LLM pesante interviene, corregge il tiro, e il sistema **aggiorna i centroidi matematici in tempo reale su Redis**.
*Risultato:* Euri impara il tuo gergo personale e diventerà sempre più veloce (5ms) a capirti senza usare la GPU.

### 2. Domain Gating (RAG Autonomo)
Tutte le memorie estratte dalle conversazioni vengono lette dall'LLM, che assegna loro automaticamente delle "etichette di dominio" (es. *informatica, chimica, business, casa*).  
Durante il recupero delle informazioni, Euri restringe prima la ricerca al dominio pertinente e poi, se non trova nulla, scala all'intero database vettoriale.

### 3. Dream Engine (Sogni Onirici in background)
Quando non gli parli da almeno 2 ore, Euri "dorme" ed entra nel ciclo onirico.
- Pesca due memorie appartenenti a due domini *completamente diversi*.
- Chiede all'LLM (Gemma 4) di cercare isomorfismi o analogie creative tra i due concetti.
- Se l'analogia è forte, genera un **CANDIDATE Insight**.
- Se più sogni indipendenti confermano la stessa analogia, l'insight viene **PROMOSSO** e scritto permanentemente.

### 4. Il Secondo Cervello (Integrazione Obsidian)
Euri è bidirezionalmente sincronizzato con **Obsidian** (cartella `EuriVault`).
- Tutte le memorie salvate e classificate compaiono come file Markdown categorizzati nelle cartelle dei domini in Obsidian.
- Gli **Insight Promossi** dal Dream Engine vengono scritti in Obsidian e generano collegamenti (`[[link]]`) visibili nel *Graph View*, mostrando l'evoluzione della sua rete neurale semantica.
- Se modifichi un testo dentro Obsidian, il Watcher in background aggiorna silenziosamente i database e i vettori di Euri su Redis.

### 5. Control Room (Streamlit UI)
Un'interfaccia web leggera (`ui/app.py`) per:
- Monitorare la telemetria di Welford (vedere come si stanno spostando i pesi matematici dell'apprendimento).
- Chattare silenziosamente (senza far scattare il Passive Learner vocale).
- Esplorare e interrogare il database RAG.

---

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Ragionamento / LLM | Ollama — `gemma4:26b` |
| Memoria Attiva | Redis Stack (JSON + RediSearch full-text + VECTOR embedding) |
| Memoria Passiva/UI | Obsidian Vault sincronizzato via `watchdog` |
| STT / Trascrizione | faster-whisper `large-v3-turbo` (CUDA float16 — NVIDIA RTX 4060 Ti) |
| TTS / Voce | sherpa-onnx + Piper (`vits-piper-it_IT-paola-medium`) |
| Embedding | sentence-transformers paraphrase-multilingual-MiniLM-L12-v2 |
| Classificatore Veloce| Algoritmo di Welford su Vettori (Aggiornamento Online) |
| Web search | ddgs (DuckDuckGo, no API key) + beautifulsoup4 |
| Gate visivo | OpenCV Haar cascade (webcam, 2fps) |

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

## Salvataggio delle Informazioni

### Salvataggio Vocale
- **Memoria:** *"Ricordami che..."* / *"Segna che..."*
- **Todo con scadenza:** *"Devo fare X fra 5 minuti"*
- **Passive Learner:** Euri ascolta passivamente e dopo 45 secondi di silenzio salva informazioni utili in background.

### Salvataggio via Dropzone (Obsidian)
Crea una nota testuale nella cartella `EuriVault/Dropzone` in Obsidian e scrivi quello che ti serve. Euri lo leggerà, classificherà il dominio, sposterà il file e lo inserirà nel suo database RAG in meno di un secondo.

---

## Modifiche e Porting Architetturali Recenti

- **Streaming Bidirezionale Obsidian**: Il watcher in `utils/obsidian_sync.py` ascolta gli eventi del file system (creazione, modifica, eliminazione) e sincronizza lo stato in Redis.
- **Welford in Redis**: Per evitare la "Catastrophic Forgetting", lo stato vettoriale dei centroidi viene salvato regolarmente su Redis. Ai successivi riavvii Euri ricorda i pattern di classificazione senza doverli re-imparare dall'LLM.
- **Isolamento Streamlit**: L'interfaccia Streamlit usa un'istanza `Brain` disaccoppiata. Questo garantisce che la chat scritta non attivi inavvertitamente il Passive Learner, lasciando alla voce la prerogativa esclusiva dell'apprendimento implicito.
- **RediSearch JSON Serialization**: I vettori vengono immagazzinati in campi JSON come stringhe o float arrays nativi e processati in `float32.tobytes()` prima delle query KNN per garantire la compatibilità con i nuovi standard di Redis Stack.
