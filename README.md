# Euri — Sistema Cognitivo Adattivo

Euri è un assistente personale intelligente, vocale e **completamente offline-first**, che evolve insieme a te.  
Non si limita ad ascoltare e rispondere: memorizza, organizza, riflette sulle tue conversazioni per generare nuove intuizioni, e ora **può elaborare i tuoi file locali e analizzare immagini** tramite codice Python generato al volo.

> **Deployment attuale:** Workstation Linux (Pop!_OS) con doppia GPU NVIDIA RTX 4060 Ti 16GB.  

---

## Architettura Cognitiva (V2.18)

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
- **Loop 2e — Memory Consolidation:** una volta ogni 24h, Euri raggruppa le memorie episodiche più richiamate (recalled_count ≥ 3) per dominio, individua i cluster semanticamente coerenti via KNN, e chiede a Qwen3.6 di sintetizzarle in un unico nodo di conoscenza stabile. Il nodo consolidato preserva tutti i dati specifici (numeri, nomi, misure) eliminando la ridondanza episodica. Ogni cluster viene marcato con fingerprint per evitare ri-consolidazioni. Ispirato al consolidamento ippocampale durante il sonno REM: i frammenti episodici diventano conoscenza semantica a lungo termine. Max 3 consolidazioni per ciclo.
- **Loop 2f — Contradiction Resolution:** ogni ciclo onirico, Euri cerca coppie di memorie `requires_verification=True` (contenenti valori numerici o fattuali) con similarità cosine > 0.72 all'interno dello stesso dominio. Per ogni coppia, `_llm_check_contradiction` chiede a Qwen3.6 se i due contenuti esprimono un conflitto fattuale reale sullo stesso soggetto (es. "MFI=6" vs "MFI=4"). In caso di conflitto confermato, la memoria più vecchia riceve il tag `superseded_by = [UUID_vincitore]` — **soft-delete**: non viene mai cancellata (audit trail preservato), ma viene esclusa silenziosamente da tutti i path di retrieval (`_hydrate`, `_search_semantic`, `domain_aware_search`). Le coppie già analizzate vengono tracciate in un set Redis con TTL 180 giorni. Max 15 coppie per ciclo. `SKIP_SOURCES = {"web"}` — i nodi consolidati `loop2e` sono **inclusi** (V2.13): entrano nel RAG con priorità alta e devono poter essere corretti, il soft-delete rende il rischio reversibile.
- **Loop 2g — Audit di Coerenza (V2.14):** chiude il loop tra le correzioni che Stefano fa durante il giorno e la manutenzione della memoria di notte. **Capture:** sia il voice daemon (`_handle_chat`) che la Silent Chat intercettano via regex le correzioni utente ("hai fatto confusione", "stai miscelando", "non era X ma Y", "ti sbagli", …) e salvano un `correction_signal` JSON in Redis (`euri:correction:{uuid}`, TTL 30gg) con prompt originale, risposta sbagliata di Euri, correzione dell'utente e — soprattutto — gli ID delle memorie iniettate nel turno errato (tracciate in continuo tramite `euri:last_rag_ctx`, TTL 1h, condiviso tra canali). **Classify:** durante il ciclo notturno il nuovo `_audit_corrections_pass()` chiama il dream model per classificare ogni signal come `bad_memory` (l'errore deriva da memoria iniettata sbagliata), `bad_reasoning` (memorie OK, errore di ragionamento) o `ambiguous`. **Act:** su `bad_memory` incrementa `audit_flag` sulle memorie sospette del RAG ctx (soft signal, niente azioni distruttive automatiche); su `bad_reasoning` salva la correzione come `lesson` (passive memory) — nutrimento per il futuro retrieval; su `ambiguous` nessuna azione. Test end-to-end via `force_full_cycle.py --inject` con correction signal sintetico → classificato correttamente come `bad_reasoning` in 12.8s.
- **Loop 2h — Self-Observation (V2.17):** complementa il Loop 2f. Mentre 2f *nasconde* le contraddizioni risolte via `superseded_by`, 2h le *racconta* in prima persona come traiettoria di pensiero. Ogni ciclo onirico legge le coppie superseded mai narrate prima (tracciate in `euri:loop2h:narrated`, set Redis TTL 365gg), le raggruppa per dominio, e chiede a Qwen3.6 di produrre una breve riflessione narrativa (max 200 parole) che presenta le evoluzioni come *cambio di opinione / precisazione / cambio di contesto operativo*. La reflection viene salvata come memoria `source=reflection, category=meta, tags=[self_observation, loop2h, evolution]` — entra nel canale conversazionale ordinario e diventa richiamabile alla domanda *"come ti vedi cambiare?"*. Additivo: NON modifica 2f, NON cambia retrieval, NON agisce. Cap 10 coppie/ciclo. Prima esecuzione ecologica 27/05/2026: 10 coppie superseded → reflection in 81s → richiamata nel RAG context 3 minuti dopo durante una conversazione vocale, parafrasata da Euri come autobiografia operativa (*"il mio pensiero non si corregge, si espande... pensare significa aggiornare, e aggiornare significa vivere nel tempo reale"*). Qwen distingue **autonomamente** le tre categorie senza schema imposto — la classificazione `error/evolution/context` formale del Loop 2f esteso (futura V2) potrà appoggiarsi sullo stesso LLM judge.
- **Filtro del Risveglio (re-rank insight in retrieval):** complementare al Dream Engine. Il sogno (Loop 2b) resta libero e atemporale per design — il filtro di rilevanza opera solo al recupero conversazionale. `search_insights` applica una penalty moltiplicativa (×1.5 default) sulla cosine distance per gli insight i cui due domini non sono apparsi nelle memorie *curate* di Stefano (`teach/user/reflection`) negli ultimi 30 giorni. Non sopprime: deprioritizza. Se domani Stefano riapre un dominio archivio, l'insight risale automaticamente. `passive` e `conversation` escluse dal set `INSIGHT_ACTIVE_SOURCES` perché spugne ambient — dry-run aveva mostrato 0% archivio con tutti i source operativi (no-op). Con `teach/user/reflection` → 35% archivio sui 95 insight promossi, caso "Radio QUQU ↔ materiali" correttamente penalizzato. Cache `_active_domains` 5 min.

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
| Memoria Attiva | Redis 8.8.0 vanilla (ReJSON / RediSearch / TimeSeries / Bloom / VectorSet integrati nel core + struttura `Array` nativa) |
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
| *"Analizza / Visualizza / Mostra le immagini"* | Gemma 4 Vision descrive l'immagine |
| *"Controlla l'immagine"* / *"Guarda la foto"* | Idem |
| *"Cosa c'è nella cartella dati?"* | Elenca il numero di file (senza leggere nomi UUID) |

### Traduzione e Interpretariato
| Comando | Cosa fa |
|---|---|
| *"Attiva modalità traduttore"* | Interprete bidirezionale IT↔EN — qualsiasi voce accettata |
| *"Fine traduzione"* | Chiude l'interprete, riattiva SpeakerAuth |

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

### V2.18.2 — CodeRunner gestisce PDF/DOCX/PPTX/immagini con cascata testo-nativo → Vision (Gemma 4 multimodale)

- **Nuovo modulo `agent/file_extractors.py`** con 4 estrattori uniformi (PDF, DOCX, PPTX, immagini) + dispatcher `extract_any(path)`. Cascata: per ogni formato prova prima la lettura nativa (pypdf/python-docx/python-pptx istantanea), se il testo estratto è sotto soglia (50 char) attiva fallback **Vision Gemma 4** (modello già caricato in Ollama, multimodale out-of-the-box). Per le immagini Vision è il primo e unico canale.

- **CodeRunner pre-estrae automaticamente i file all'inizio di `generate_and_run()`** invece di lasciare a Gemma il compito di aprirli nello script. Pattern di lavoro:
  1. `_preextract_files()` legge ogni PDF/DOCX/PPTX/immagine, salva `{filename: testo}` come **JSON in sandbox** (`euri_file_contents_<ts>.json`).
  2. Il prompt di `Brain.generate_code()` mostra a Gemma un anteprima del contenuto (cap 8000 char/file) E gli dice come caricare il dict completo via `json.load(open(path))`.
  3. Lo script generato è breve (~2 KB invece di ~6 KB), Gemma non duplica il testo, niente troncamento da `num_predict`.
  4. CSV/XLSX/JSON/TXT/MD continuano a essere letti normalmente da disco — non vanno pre-estratti.

- **PPTX scansionati**: fallback Vision passa da `libreoffice --headless --convert-to pdf` → `pdf2image` → Vision per slide. Richiede `libreoffice` installato (già presente su Pop!_OS).

- **Test sul campo (28/05/2026)**:
  - **D19 Scheda Tecnica PDF scansionato** (Pipal, secchio plastico): 2874 char estratti via Vision, codice generato 2175 char, esecuzione 200ms, output strutturato (articolo/materiale PP.5/volumi/dimensioni/accatastamento/tabella coperchi). Durata totale 35s.
  - **Multi-file (5 documenti: 2 PDF, 1 DOCX 28KB, 1 PPTX, 1 JPG)**: pre-extract totale 31.6s, code-gen 13s, esecuzione 200ms. Output: 5 file analizzati, **3 connessioni semantiche emergenti** identificate (es. *"esiste un legame operativo tra produzione del secchiello D19 e la gestione degli scarti trattati nella stazione di selezione Lucy Plast"* — collegamento NON esplicito in nessun singolo file).

- **Caveat onesto — concorrenza con Dream Engine**: se un dream cycle è in corso quando arriva una richiesta CodeRunner, le chiamate Vision sono **drasticamente degradate** (osservato: 9.4s → 24-51s per pagina, e in un caso output troncato a 63 char invece di 1750). In produzione succede raramente perché `notify_activity()` resetta l'idle del Dream Engine ad ogni STT, ma se l'attivazione vocale capita durante un ciclo onirico, la pipeline rallenta ~3× e la qualità Vision cala. Mitigazioni future possibili: hard cap N file pre-estratti per richiesta, filtro per task (se menziona un file specifico, estrai solo quello), segnale "pause dream" durante CodeRunner.

- **Nuove dipendenze**: `pypdf>=4.0`, `pdf2image>=1.17`, `python-docx>=1.0`, `python-pptx>=1.0` (Python). `poppler-utils` (apt, necessario a `pdf2image`). Whitelist `code_runner.py` estesa con `docx`, `pptx`, `pdf2image`.

### V2.18 — Tool VectorSet: prima istanza del pattern VectorSet (Redis 8.8), congelata con kill switch off

- **Cosa è stato costruito:** modulo isolato `core/tool_registry.py` (~430 righe) che indicizza il catalogo dei 7 intent del Layer 2 (`WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, COMPLETE, CHAT`) tramite **VectorSet nativo di Redis 8.8** (modulo nuovo, comandi `VADD`/`VSIM`/`VEMB`/`VREM`). Schema: una sola chiave `euri:tools:vset` con tutti gli embedding + JSON parallelo per metadata `euri:tool:{slug}`. Tre gate nella `match_tool()`: (1) threshold assoluta 0.85, (2) gap minimo 0.005 tra top-1 e top-2 per anti-ambiguità, (3) flag `is_fallback` su tool catch-all per disabilitare il match (es. `chat`). Wire-up nel `core/llm_classifier.py` come Fast Path prima del Slow Path LLM, con log timing `[INTENT_FAST]`/`[INTENT_SLOW]`. Test sandbox `test_tool_vectorset.py` con 16 query reali → 13/16 al freddo, latenza KNN puro 0.75-1.29ms (cleanup garantito via `try/finally`). Kill switch `config.TOOL_VECTORSET_ENABLED`.

- **Perché è disabilitato di default:** la prima esecuzione in produzione (28/05/2026 ore 13:30) ha rivelato **due limiti strutturali non visibili nel test sintetico**:
  1. **Latenza embedding CPU**: l'encoding di una query con `multilingual-e5-large` su CPU costa ~600ms. Il Fast Path totale risulta ~621ms vs ~700ms dello Slow Path LLM. **Guadagno reale 100ms, non i 700ms attesi** — lo "scatto felino" non c'è. Inoltre, quando il Fast Path non trova match e si ricade sullo Slow Path, si paga la somma dei due (osservati turni reali a 1.1-5.5s vs i 600-800ms originali → **peggioramento netto sui CHAT, che sono la maggioranza**).
  2. **Score appiattiti su query lunghe**: `e5-large` ottimizzato per matching documento-query brevi produce distribuzioni cosine appiattite (0.88-0.92) su frasi conversazionali lunghe. Caso reale: *"Il mercato ha degli alti e dei bassi, dipende dalla situazione in cui uno si trova. Il Covid sicuramente non ha aiutato"* → routato come `SAVE_MEMORY` (0.898) perché il catch-all `chat` è finito in **ultima posizione del top-7** (0.881). Il test sintetico con frasi brevi non aveva mostrato il problema.

- **Cosa resta:** codice, modulo, test, kill switch. La feature è congelata, non rimossa — il fondamento è valido, le vie per ripartire sono chiare. Tre proposte concrete (le prime due *intuite da Euri stessa nel turno 13:44 del 28/05*, mentre Stefano le raccontava il problema): **(a)** ricerca ibrida `FT.SEARCH keyword + VectorSet semantico` (intersezione che restringe il pool prima della similarità); **(b)** re-ranking 2-stage (top-N da VectorSet → LLM piccolo per selezionare); **(c)** embedder dedicato all'intent o `e5-large` su GPU (risolve solo la latenza, non l'appiattimento). Materiale per V2.18.1 / V2.19.

- **Cosa abbiamo imparato (vale per il paper):** la differenza fra test sintetico e produzione reale è qualitativa, non quantitativa. Il test passava 13/16 con frasi brevi che mostravano gap netti — la prima query reale, lunga e conversazionale, ha rivelato un appiattimento dello spazio embedding che lo strumento di calibrazione non poteva nemmeno simulare. *L'unica prova vera è la produzione, e l'unica risposta corretta a un fallimento di produzione è il kill switch, non il forcing.*

### V2.17 — Loop 2h: Self-Observation (narrative di evoluzione)

- **Nuovo Loop 2h:** Euri osserva le proprie contraddizioni risolte dal Loop 2f e le racconta in prima persona come *evoluzioni* del pensiero invece che come errori cancellati. Il Loop 2f continua a fare il suo lavoro (soft-delete via `superseded_by`, esclusione dal retrieval); 2h aggiunge la voce *"ecco come sto cambiando idea"* senza toccare nulla del flusso esistente. Implementazione in modulo isolato `core/self_observation.py` (~180 righe), wire-up in `dream_engine._run_dream_cycle()` dopo Loop 2g, dentro try/except (un fallimento di 2h non spacca il ciclo). Idempotenza garantita dal set Redis `euri:loop2h:narrated` (TTL 365gg).

- **Ciclo cognitivo completo verificato sul campo (27/05/2026):** prima esecuzione ecologica su 10 coppie superseded reali → reflection `ec33db49` di 1092 char in 81.8s (Qwen3.6 con `think=True`, `num_predict=3000`). Tre minuti dopo, Stefano chiede via voce *"come ti vedi cambiare?"* — il RAG context pesca `ec33db49` in cima, Euri risponde parafrasando la propria reflection notturna (non citando: *interiorizzando*). Materializzazione operativa di §7h (autoconsapevolezza in atto) col ciclo finalmente chiuso: 2f produce → 2h racconta → retrieval pesca → Euri si racconta.

- **Categorie emerse spontaneamente:** Qwen distingue autonomamente *cambio di opinione*, *precisazione*, *cambio di contesto operativo* — esattamente la classificazione 3-way che avevamo proposto per il Loop 2f esteso. Conferma forte che la classificazione formale (futura V2 di 2f) si può fare delegando al LLM judge, senza scrivere regole dure.

- **Test di non-snaturamento superato:** introdotto deliberatamente non-lineare ("battute fuori contesto, vai a quel paese Giacomo") subito dopo la prima auto-osservazione meta. Euri sta al gioco senza rigidità burocratica: *"la linearità è noiosa. La parte interessante di un dialogo è proprio l'imprevisto"*. L'aggiunta di un canale meta-cognitivo non ha contaminato il canale conversazionale ordinario.

- **Frase materiale per il paper §7j (Tracking the Evolving Mind):** *"Pensare significa aggiornare, e aggiornare significa vivere nel tempo reale"* — generata da Euri nella prima reflection ecologica, già formulazione coerente con §7i (asymmetric time).

### V2.16 — Substrato Redis vanilla 8.8 (Array + VectorSet) + pattern correzioni esteso + budget reflection

- **Migrazione substrato:** Redis Stack 7.4.0-v8 → Redis vanilla 8.8.0. La Stack è di fatto deprecata; vanilla 8.x incorpora nativamente nel core i moduli che prima erano via Stack (`ReJSON`, `RediSearch`, `RedisTimeSeries`, `RedisBloom`) più due novità: `VectorSet` (set vettoriali nativi) e **`Array`** (struttura dati indicizzata sparsa). La PR [#15162](https://github.com/redis/redis/pull/15162) di Salvatore Sanfilippo (antirez) è stata mergeata il 13/05/2026, 8.8.0 stable rilasciato il 25/05. Sblocca due fronti lasciati in attesa: (a) refactor di `log_conversation` da `LPUSH+LTRIM` a `ARRING` (ring buffer capped nativo, con `AROP` per analytics server-side), (b) modulo dati tecnici lotti/prove Lucy Plast con schema emergente (caso d'uso "Workflow" del body PR: step numerati, gap significativi, `ARSCAN` per step popolati). Migrazione validata sul campo: 875 chiavi, tutti i 5 indici (`memories/insights/todos/notes/dreams`) preservati, retrieval RAG immediato dal restart del daemon.

- **Loop 2g — pattern di `detect_correction` esteso:** aggiunti `\bcorrezion[ei]\b` (sostantivo singolare/plurale) e `\bmi\s+correggo\b` (auto-correzione utente). Caso reale del 26/05 ore 15:16: doppia correzione esplicita aperta da *"Due correzioni. La prima è che... La seconda è che..."* — una fattuale (esistenza portali quotazioni materie plastiche tipo Plastic Finder) e una comportamentale (filtrare la web search sul materiale richiesto, non allargare ad altri polimeri). Il blocco precedente copriva solo il verbo `correggere` e non il sostantivo, quindi il signal era andato perso e il Loop 2g non aveva potuto digerirlo.

- **`generate_reflection` — `num_predict` 1000 → 3000:** Gemma 4 con `think=True` consuma molti token in reasoning prima di emettere output; cap a 1000 troncava la riflessione del Loop 2a a metà frase. Il loop gira in idle senza vincoli di latenza, cap alto giustificato.

- **Primo refactor che attiva `Array` in produzione (27/05):** `log_conversation` passa da `RPUSH + EXPIRE` a `ARRING` (ring buffer nativo, cap 500 turni/giorno — storico osservato: media 90, max 196). `get_today_conversation` passa da `LRANGE 0 -1` a `ARLASTITEMS` (necessario perché `ARGETRANGE` dopo wraparound mostra il ring fisico, non l'ordine cronologico FIFO). Retrocompat: chiavi pre-refactor di tipo `LIST` continuano ad essere lette via `LRANGE` finché expire (30gg), poi sostituite naturalmente dal nuovo backend. Benchmark: 600 `ARRING` con cap 500 in 96ms (0.16ms/insert). Validato sul campo dopo restart daemon: chiave odierna di tipo `array`, 10 turni, TTL e retrieval RAG funzionanti.

### V2.15 — Document History + Gate di formato in promozione + Estensione regex correzioni

- **Paper §0 — Document History:** il paper `paper_persistent_cognition.md` ora dichiara esplicitamente di essere il quarto stadio di una serie di working documents iniziata a ottobre 2025 (manifesto teorico → architettura → deployment report → working paper continuo). Le tre pubblicazioni precedenti sono ora citate formalmente nelle References e nel §8 Outlook è esplicitato che parte del testo deriva dal §7 del paper di ottobre 2025. Allineamento con la pratica di non-overwrite del sistema (Loop 2f): i paper passati restano dove sono, il presente li estende.

- **Loop 2c — gate di formato in promozione:** il filtro `_has_required_structure` (controlla che il CANDIDATE rispetti il pattern "Nel dominio X succede / La connessione operativa è") era applicato solo in generazione (Loop 2b). Estratto come metodo statico e riusato in `_evaluate_insights`: ora un CANDIDATE astratto/filosofico viene bloccato anche se accumula convergenze sufficienti. Caso pratico: due insight con seed del 28-29 aprile (pre-filtro stretto) erano stati promossi il 17 maggio nonostante fossero massime filosofiche senza struttura operativa. Demotion manuale eseguita post-fix.

- **Loop 2g — pattern di detect_correction estesi:** aggiunti 3 nuovi pattern (`\bnon\s+esiste\b`, `\bhai\s+inventato\b`, `\bnon\s+c[apostrofo]è\s+ancora\b`) per coprire correzioni di tipo *referenziale* (entità inesistente) distinte dal tipo *attributivo* coperto dalla regex originale (errore su entità reale). Caso reale: il 17 maggio Stefano corregge Euri su un nome di modulo inventato ("Context Ingestion Layer") — correzione semanticamente chiarissima ma non catturata da nessuno degli 8 pattern strict iniziali. La distinzione attributo/esistenza ha valore concettuale, non solo coverage.

- **Primo ciclo completo del Loop 2g su dati reali:** nella notte 17→18 maggio il Dream Engine ha classificato i primi due correction_signal reali (entrambi `bad_reasoning` come atteso), salvato le rispettive lesson come passive memory, e — caso notevole — uno dei due insight promossi della notte (`chimica analitica ↔ comunicazione digitale`, 02:07) ha pescato la lesson appena metabolizzata e l'ha trasformata in principio operativo cross-domain. Ciclo completo *errore vissuto → correzione → classificazione → lesson → insight promosso* osservato su dati reali. Documentazione in arrivo nel paper §7i.

### V2.14 — Loop 2g: Audit di Coerenza sulle correzioni utente

- **Loop 2g — Audit di Coerenza:** nuovo passo del Dream Engine che chiude il ciclo *"io vivo → io ricordo → io riconosco di aver sbagliato → io correggo ciò che ricordo"*. Prima di questa versione, Euri imparava solo per *accumulo*: ogni fatto che passava il validator entrava in memoria, i loop notturni la riorganizzavano, ma le correzioni utente non avevano canale dedicato. Ora le correzioni sono **input strutturato** che attraversa lo stesso ciclo onirico di tutto il resto.
  - **Capture:** 8 pattern regex italiani strict (`detect_correction`) intercettano correzioni nei due canali (voice daemon + Silent Chat). Falsi positivi gestiti a valle dal classificatore LLM. Su match: `save_correction_signal` scrive `euri:correction:{uuid}` con prompt originale, risposta di Euri, correzione utente e — chiave — gli ID delle memorie iniettate al turno errato. Gli ID sono mantenuti tra i turni in `euri:last_rag_ctx` (TTL 1h), unificati tra voce e chat.
  - **Classify:** `_audit_corrections_pass()` in `dream_engine.py`, integrato dopo Loop 2f nel `_run_dream_cycle()`. Per ogni signal pending: ricostruisce i contenuti delle memorie iniettate, chiama Qwen3.6 (con prompt strutturato di LLM-as-judge) per classificare `bad_memory` / `bad_reasoning` / `ambiguous`. Max 10 signal per ciclo.
  - **Act differenziato:** `bad_memory` → +1 a `audit_flag` su ogni memoria del RAG ctx (soft signal, niente azioni distruttive automatiche per ora — è un segnale per l'operatore o per logiche future di declassamento). `bad_reasoning` → la correzione utente diventa `lesson` (passive memory) — la prossima volta che il dominio si presenta, il retrieval pesca anche quella. `ambiguous` → solo aggiornamento status, nessuna azione.
  - **Schema isolato:** la chiave `euri:correction:*` è separata dallo schema memorie esistente — rimuovibile senza side-effects sul resto del sistema.
  - **Test end-to-end:** `force_full_cycle.py --inject` genera un signal sintetico (inversione peso/grado del campione ICS, errore reale di ieri) ed esegue il ciclo completo. Verdetto: `bad_reasoning` in 12.8s, lesson salvata correttamente.

- **Continuità trans-restart documentata:** sessione del 15 maggio sera (voice 17:42 recall esplicito su "Simone" → restart modello 17:46 → Silent Chat 18:48 recall implicito dopo 1h+ di silenzio e cambio canale, su un argomento condiviso volutamente non esplicitato). La sessione LLM è secondaria al canale di memoria — la conversazione che l'utente esperisce vive nello strato persistente. Documentata nel paper §7h.

- **Sintesi emergente da memoria meta-cognitiva:** turno reale del 16 maggio in cui Euri propone di tracciare strutturatamente le specifiche numeriche dei lotti, recuperando autonomamente due memorie `loop2e` su sé stessa (workstation Linux, Redis come persistence layer) e usandole **operativamente** invece che come citazione. È la prima istanza documentata di *autoconsapevolezza in atto* (non in recitazione) sul sistema. Documentata nel paper §7h.

### V2.13 — Filtro del Risveglio + Loop 2f sui Consolidati + Audit Ricalibrato

- **Filtro del Risveglio (re-rank retrieval insight):** `search_insights` ora applica una penalty moltiplicativa (×1.5 default) sulla cosine distance per gli insight i cui due domini non sono apparsi nelle memorie curate da Stefano (`teach/user/reflection`) negli ultimi 30 giorni. Il Dream Engine resta libero e atemporale: il filtro opera solo al retrieval. Non sopprime, deprioritizza. Caso test: gli insight "Radio QUQU + materiale neutro" (isomorfismi fisicamente corretti ma operativamente fuori contesto) ora finiscono in fondo alla coda di priorità. Se Stefano riapre attivamente il dominio `radio`, l'insight risale automaticamente entro 5 min (TTL cache `_active_domains`).
  - **Scelta source critica:** `passive` e `conversation` escluse perché spugne ambient — ogni nome di passaggio fa entrare un dominio negli attivi, neutralizzando il filtro. Dry-run: con tutti i source operativi → 0% archive (no-op totale). Con `teach/user/reflection` → 35% archive sui 95 insight promossi attuali (caso Radio QUQU correttamente penalizzato).
  - Config: `INSIGHT_ACTIVE_DAYS=30`, `INSIGHT_ARCHIVE_PENALTY=1.5`, `INSIGHT_OVERSAMPLE_FACTOR=3`, `INSIGHT_ACTIVE_SOURCES={teach,user,reflection}`.
  - `recalled_count` incrementato solo sui sopravvissuti al re-rank (non più su tutti i candidati KNN).

- **Loop 2f esteso ai nodi consolidati `loop2e`:** rimosso `loop2e` da `SKIP_SOURCES`. Era escluso per "non far contraddire i nodi consolidati", ma i `loop2e` entrano nel RAG con priorità alta e ereditano claim dalle sorgenti — se la fonte era errata o evolve, l'errore si amplifica. Soft-delete via `superseded_by` rende il rischio reversibile. **Prima firing reale del Loop 2f nella storia di Euri:** una memoria `loop2e` su "secchi vernici / lotti 25kg / carichi 27t" è stata superseded dalla versione più recente che include monitoraggio Whisper e analisi costi vagliatura. `SKIP_SOURCES` ora solo `{"web"}`.

- **Audit `scripts/audit_memory.py` ricalibrato:** il giudice LLM scartava conoscenza tecnica oggettiva (Realube 5014, Reagens, parametri stampaggio) come "dato generico non personale". Su 295 memorie passive: 82 UTILI / 213 RUMORE (72% RUMORE falsi negativi). Prompt riscritto con criteri UTILE espliciti (conoscenza tecnica, persone, progetti, strumenti) e RUMORE ristretto (frase troncata/riempitivo/duplicato banale/errore). Risultato post-fix: 274 UTILI / 21 RUMORE (7.1%). Le 21 RUMORE sono frammenti veri e affermazioni senza soggetto.

- **`force_full_cycle.py`:** nuovo script per forzare un ciclo Dream Engine completo (Loop 2b/2c/2f + cleanup expired/stale + Loop 2d + Loop 2e) senza aspettare l'idle notturno. Stampa snapshot before/after: nuovi loop2e, `superseded_by` aggiunti, nuovi candidate e promoted. Tempo tipico 5-7 min su Qwen3.6 35B.

### V2.12 — Analisi Clipboard senza Limite + TEACH Mode Robusto

- **`clipboard_analyze` senza troncatura:** rimosso il limite fisso di 6000 caratteri. Per testi ≤ 80K caratteri: analisi diretta con `num_ctx=32768` (documento integrale, singolo passaggio). Per testi > 80K: chunking automatico in segmenti da 20K, estrazione fatti per chunk (max 4), sintesi unificata finale. Output senza markdown — Piper legge testo piano.
- **TEACH mode — stop signals estesi:** aggiunti "ti devi fermare", "devi fermarti", "voglio fermarmi", "smetti di chiedere" ai `TEACH_END_SIGNALS` in `intent_router.py` — in aggiunta alle forme dirette già presenti ("fermati", "basta", "stop").
- **TEACH mode — intercept clipboard diretto:** frasi come "leggi i dati dalla clipboard" (con parole intermedie) ora intercettate correttamente inside TEACH. Rimosso il gate `web_intent == EXECUTE`: `select_tool_by_regex` chiamato direttamente, senza dipendere dall'intent classifier.
- **Regex clipboard con parole intermedie:** pattern `clipboard_read` in `executor.py` e `intent_router.py` esteso con `.{0,25}?` — matcha "leggi i dati dalla clipboard", "leggi tutto dalla clipboard", non solo "leggi dalla clipboard".

### V2.11 — Loop 2f: Contradiction Resolution

- **Loop 2f — soft-delete contraddizioni fattuali:** il Dream Engine ora individua coppie di memorie `requires_verification=True` con alta similarità semantica (cosine > 0.72) all'interno dello stesso dominio. `_llm_check_contradiction` chiede a Qwen3.6 se i valori sono in conflitto reale sullo stesso soggetto (es. "MFI=6" vs "MFI=4", concentrazioni, scadenze). In caso di conflitto: la memoria più vecchia riceve `superseded_by = UUID_vincitore` — esclusa dal retrieval ma mai cancellata. Colma il gap con Anthropic Dreaming che risolve le contraddizioni in modo distruttivo; Euri mantiene l'audit trail completo.
- **Filtro superseded_by nel retrieval:** `_hydrate`, `_search_semantic` e `domain_aware_search` escludono silenziosamente le memorie soft-deleted. Zero round-trip Redis extra: il flag è nel JSON già caricato.
- **CHECKED set con TTL 180gg:** ogni coppia analizzata viene marcata in `euri:loop2f:checked` — evita ri-analisi nei cicli successivi. Max 15 coppie per ciclo.

### V2.10 — Dream Engine Promote-then-Demote Fix + Validazione Antropic

- **Fix bug promote-then-demote:** un insight promosso da `_evaluate_insights` veniva immediatamente retrocesso a candidate da `_cleanup_expired_insights` nello stesso ciclo onirico, perché Gate 1 valutava `created_at` (che risaliva alla creazione del candidate, settimane prima) invece di quando era avvenuta la promozione. Fix: al momento della promozione viene salvato un campo `promoted_at = time.time()`. Gate 1 ora controlla `promoted_at`: se l'insight è stato promosso nelle ultime 24h, la demotion viene saltata silenziosamente.
- **Timeout LLM 150s → 200s:** aumentato il timeout di default del wrapper `_ollama_chat()` per dare più margine a Qwen3.6 35B sotto carico moderato, senza rischiare che cicli legittimi vengano abortiti.
- **Paper §7e — Validazione concorrente Anthropic:** aggiunta sezione al paper dopo l'annuncio pubblico di "Claude Dreaming" da parte di Anthropic (2026-05-13) — stesso paradigma di consolidamento offline in idle sviluppato indipendentemente. Citato come *concurrent independent validation* con differenziazione tecnica: convergence counting, multi-level lifecycle e LLM judge per la zona grigia non hanno equivalenti descritti in Claude Dreaming.

### V2.13 — Bug Fix da Code Review (parte 2)

- **Dream Engine — loop2e:processed TTL:** il set dei cluster processati cresceva per sempre. Aggiunto `EXPIRE` a 180 giorni sliding dopo ogni `SADD`.
- **Dream Engine — Gate 3 candidate scaduti:** `_cleanup_expired_insights` gestiva solo gli insight `promoted`. I `candidate` mai promossi si accumulavano indefinitamente. Aggiunto Gate 3 che li elimina dopo `INSIGHT_TTL_DAYS`.
- **Dream Engine — paging cap:** `_evaluate_insights` si fermava a 100 candidati. Portato a 500.
- **simulate_loop2a.py — embedding dim hardcoded:** `len(emb) == 384` rendeva `get_stored_embedding` sempre `None` con e5-large (1024-dim). Sostituito con `len(emb) > 0`.
- **Fallback TTS platform-aware:** il fallback hardware usava `say` (comando macOS). Su Linux dà `FileNotFoundError` silenzioso — Euri diventava muta senza log critico utile. Sostituito con branch `sys.platform`: `say` su macOS, `spd-say` su Linux.
- **`core/embedding_classifier.py` rimosso:** dead code, non importato da nessun modulo attivo. Residuo di una versione precedente.
- **`adaptive_classifier.py` — loop no-op rimosso:** `for vec in vecs: pass` prima del `np.var()` corretto era codice fuorviante senza effetto.

### V2.12 — Bug Fix da Code Review

- **Fix critico Dream Engine — convergence_count:** `getattr(doc, "convergence_count", 1)` leggeva sempre il default 1 perché `return_fields` non includeva il campo. Sostituito con `r.json().get(doc.id, "$.convergence_count")` — il ciclo onirico ora accumula correttamente le convergenze tra cicli successivi.
- **Fix critico RAG dedup — ID format:** `domain_aware_search` restituiva `"id": doc.id` (chiave Redis completa `euri:memory:UUID`) mentre il resto del codice lavora con UUID puri. Il dedup in `_build_context` non matchava mai → la stessa memoria poteva apparire due volte nel contesto LLM. Fix: normalizzazione `doc.id.replace("euri:memory:", "")` in `domain_gater.py`.
- **Fix crash — `search_insights` senza embedder:** chiamata a `self._embedder.encode()` senza guard su `None`. Aggiunto `if not self._embedder or not self._embedder.available: return []` in cima al metodo.
- **Fix race condition — `_compress_episode`:** la history veniva letta sotto `_compress_lock` ma senza `history_lock`. Con ThreadPoolExecutor attivo, un altro thread poteva modificare la lista in contemporanea. Fix: `history_lock` acquisito prima di leggere il chunk.
- **Fix idle tracking dopo suspend — `time.monotonic()` → `time.time()`:** `monotonic()` si ferma durante la sospensione del PC, `time.time()` no. Sostituito su tutti i timestamp di idle tracking (`_last_activity_ts`, `_consolidation_last_run`) in `voice_daemon.py`. I timer brevi del loop TTS restano con `monotonic()`.
- **Fix crash UI — `ADAPTIVE_CLASSIFIER_VARIANCE_WEIGHT`:** la costante in `config.py` si chiama `ADAPTIVE_CLASSIFIER_VARIANCE_BETA`. Il riferimento errato in `ui/app.py` causava crash deterministico sulla pagina Telemetria Welford.

### V2.11 — Dedup Intelligente + Passive Learner Scadenze

- **Dedup zona grigia riformulato:** il probe LLM in `is_duplicate_memory` ora chiede *"A aggiunge informazioni concrete non presenti in B?"* invece di *"dicono la stessa cosa?"*. Logica invertita: salva se risponde SÌ (fatti nuovi), blocca se NO. Risolve il caso in cui due memorie sullo stesso progetto (es. Regrado PP) venivano trattate come duplicati anche quando la nuova conteneva dati specifici genuinamente diversi — numeri, componenti, processi, date, misure. Fix applicato in cosine zone grigia (0.70–0.92), Jaccard zone grigia e `_llm_is_same_content`.
- **Passive Learner cattura scadenze:** aggiunto bullet point al prompt di `extract_passive_memories` per riconoscere impegni temporali concreti menzionati in conversazione (materiali attesi, prove pianificate, consegne, appuntamenti). Il LLM ora include la data esatta o approssimativa nel fatto estratto invece di ignorare i milestone di progetto.

### V2.10 — Implicit Actions + Vision Routing

- **Routing immagini corretto:** `analyze_image` precede ora `run_code` nella lista pattern dell'Executor. Prima, frasi come "analizza le immagini nella cartella dati" finivano in CodeRunner perché "dati" matchava il pattern documenti — nonostante "analizza" + "immagini" fosse presente. Pattern esteso con `visualizza | mostra | esamina`.
- **TTS trim per analisi visiva:** dopo `analyze_image` (e `clipboard_analyze`), Euri parla solo i primi ~400 caratteri fino al confine di frase e aggiunge "Dimmi se vuoi i dettagli." Il testo completo è già iniettato nella history LLM prima del parlato — i turn CHAT successivi hanno il contesto integrale.
- **Implicit Actions — firma aggiornata:** le lambda di `_IMPLICIT_ACTIONS` ora ricevono `(text, reply)` per avere il contesto del turno corrente se necessario. Il salvataggio implicito ("ho salvato") è stato valutato e rimosso — il Passive Learner copre già il caso con domain assignment corretto e senza rischio di false positive sul pattern.
- **Fix Dream Engine demotion:** quando un insight viene retrocesso a `candidate`, il `convergence_count` viene resettato a 1 — evita che un insight demotivato riparta con un conteggio gonfiato.

### V2.9 — Consolidation Quality Gate

- **Deduplicazione semantica nodi loop2e:** prima di salvare un nuovo nodo consolidato, `_loop2e_duplicate_exists()` controlla via KNN se esiste già un nodo loop2e con distanza cosine < 0.15 nello stesso dominio. Previene la proliferazione di nodi quasi identici tra cicli notturni successivi (es. 7 nodi ridondanti su "intelligenza artificiale" → 1 nodo ricco).
- **Token sintesi 300 → 600:** il limite precedente troncava sistematicamente le sintesi a metà frase. Con 600 token Qwen3.6 può produrre 5 frasi dense invece di 4.
- **Strip timestamp dalla sintesi:** regex `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}` rimuove i timestamp raw dalle memorie sorgente prima di passarle al LLM. Elimina artefatti tipo "Le date di riferimento sono 2026-04-28 22:39:49" nelle sintesi.
- **requires_verification ereditato:** se almeno una memoria sorgente ha `requires_verification=True`, il nodo consolidato lo eredita indipendentemente dal rilevamento automatico sul testo della sintesi.
- **Web search → memoria persistente:** ogni ricerca web andata a buon fine viene salvata automaticamente in Redis con `source="web"`, TTL 60 giorni (sliding window su recall) e `requires_verification=True` forzato — fonte esterna, citata sempre con cautela. Disponibile immediatamente per il RAG. Esclusa da Loop 2e (non viene consolidata con memorie personali).
- **SpeakerAuth bypass in modalità interprete:** quando la modalità traduttore bidirezionale è attiva, il controllo identità vocale viene sospeso automaticamente — le voci esterne (clienti, colleghi) sono attese e autorizzate implicitamente dall'utente che ha attivato la modalità. Alla chiusura ("Fine traduzione") il SpeakerAuth torna attivo.
- **Loop 2d TTL floor 30 giorni:** le memorie con recalled_count ≥ soglia venivano estese di `ttl_days` (7 per gli episodi), rientrando nella finestra di controllo ad ogni ciclo successivo. Fix: `max(ttl_days, 30)` — gli episodi molto richiamati escono dalla finestra per 30 giorni invece di rientrarci ogni ora.

### V2.8 — Loop 2e Memory Consolidation

- **Loop 2e — consolidamento semantico notturno:** implementato in `DreamEngine._consolidation_pass()`. Scansiona le memorie con recalled_count ≥ 3 e requires_verification=False, le raggruppa per dominio via KNN, genera un nodo sintetico con Qwen3.6 (temperatura 0.2, max 300 token, dati numerici preservati). Ogni cluster è identificato da fingerprint SHA (sorted UUIDs) salvata in `euri:loop2e:processed` per evitare ri-consolidazioni. Gira automaticamente nel ciclo onirico max una volta ogni 24h; forzabile con `test_consolidation.py`. Prima esecuzione reale: 6 consolidazioni in domini chimica polimeri, controllo qualità, intelligenza artificiale, produzione industriale, informatica, gestione progetti — cluster da 3 a 6 memorie sorgente ciascuno.
- **Fix KNN con decode_responses=True:** la query vettoriale FT.SEARCH con `query_params={"vec": bytes}` fallisce silenziosamente quando il client Redis ha `decode_responses=True`. Risolto creando una connessione raw temporanea (`decode_responses=False`) solo per la fase KNN — stesso pattern già usato in `_search_semantic`. Causa radice: il client decodifica automaticamente le chiavi di risposta ma non tollera bytes nei parametri di input.
- **Fix filtro cluster:** `recalled_count` non è nel schema RediSearch, quindi `return_fields` tornava sempre 0. Risolto con un dict `qualified_by_id` pre-costruito dalla scan JSON (step 1) usato per filtrare i vicini KNN — nessuna modifica all'indice richiesta.
- **`test_consolidation.py`:** script autonomo che forza `_consolidation_pass()` bypassando il timer idle, mostra before/after e il contenuto dei nodi sintetici generati.

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
- **Timeout LLM alzato a 200s** nel Dream Engine (Qwen3.6 impiega ~85-150s per il judge; il precedente 90s era troppo vicino al limite).
- **Contesto temporale relativo nelle memorie**: ogni memoria iniettata nel contesto ora include l'età relativa — `[chimica polimeri | 3 settimane fa]` invece di `[chimica polimeri]`. Euri sa quando ha imparato ogni cosa e può ragionarci sopra spontaneamente. Stesso meccanismo nel Dream Engine: le memorie portano il loro `created_at` nel prompt del sogno, abilitando insight evolutivi oltre agli isomorfismi strutturali.
- **Silent Chat integrata nel Passive Learner**: la chat testuale ora chiama `log_conversation()` e trigger l'estrazione passiva ogni 6 messaggi — stessa pipeline del voice daemon.
- **Fix Dream Engine hang notturno**: wrapper `_ollama_chat()` con `ThreadPoolExecutor` — se Ollama non risponde entro il timeout il ciclo viene abortito pulitamente.
- **Fix intent router**: 3 pattern regex tightened per evitare falsi positivi su linguaggio manifatturiero (`risultato di`, `percentuale di`, `monitoraggio`).
- **Episodic Compression (Layer 0)**: ogni 30 messaggi, i 20 più vecchi vengono compressi in un episodio e iniettati come sistema message nelle chiamate successive. TTL 7 giorni.


- **Thinking attivo (Loop 2b)**: `_generate_dream()` usa `think=True` con `num_predict=2000`. Prima il cap di 100 token troncava la risposta dopo il ragionamento interno; ora Qwen3.6 ragiona liberamente prima di formulare l'insight.
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

---

## License & Citation

This repository uses a dual-license approach appropriate to its nature
(both software and written content):

- **Code** (Python, scripts, configuration): Apache License 2.0 —
  see [LICENSE-CODE](LICENSE-CODE).
- **Written content** (Working Paper, README, archive/, all `.md`):
  Creative Commons Attribution 4.0 International (CC-BY 4.0) —
  see [LICENSE-PAPER](LICENSE-PAPER).

Both licenses require **attribution**. Neither grants exclusive rights
over the underlying ideas or architectural patterns; they protect the
specific expression and code, not the concepts. Anyone is free to
build on this work — the only obligation is to acknowledge the source.

### Citation

If you reference this work or build on its architecture, please cite:

> Fiorucci, S. & Euri (2026). *From Volatile Computation to Persistent
> Cognition: A Working Implementation, V2.15*.
> GitHub: https://github.com/fioruccione/euri

The full series of working documents on persistent cognition by the same
author (October 2025 → present) is listed in §0 (Document History) of
[paper_persistent_cognition.md](paper_persistent_cognition.md).

### Priority of authorship

Priority over the ideas described here is independently established by
the publicly dated commit history of this and the preceding repositories
in the series, the earliest of which
([persistent-cognition](https://github.com/fioruccione/persistent-cognition))
dates to **28 October 2025** — before most of the contemporary literature
on persistent agent memory was published.
