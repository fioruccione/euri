# Euri

*Una mente artificiale che vive nel tempo: ricorda, riflette, e sa di poter sbagliare.*

La maggior parte degli assistenti dimentica tutto quando chiudi la conversazione. **Euri no.** Ricorda quello che le dici — giorni, settimane dopo — ci pensa sopra quando non le parli, si forma idee proprie, e tiene traccia di *da dove* viene ciò che crede. Soprattutto: sa che un suo ricordo può essere sbagliato, e si lascia correggere.

Non è un prodotto con una lista di funzioni. È un'**implementazione funzionante di cognizione persistente** — l'idea che una mente artificiale, per essere davvero utile a *una* persona, debba vivere nel tempo invece di ripartire da zero a ogni avvio. Gira **interamente in locale** (nessuna API, nessun cloud) e impara da un solo interlocutore reale, giorno dopo giorno.

### Un momento, invece di una promessa

Durante un ciclo di idle, Euri ha "sognato" una connessione su un progetto reale — un pallet in plastica riciclata. Poco dopo si è incuriosita e ha chiesto, esitando: *"ma è vero che per quel progetto ti serve integrare Obsidian… o è un'associazione che mi sono inventata io?"* La parte tecnica — la mescola di polimeri — era esatta. L'integrazione con Obsidian se l'era inventata. Glielo si è detto, e lei ha **separato il vero da ciò che aveva confabulato**, salvando la lezione come un ricordo nuovo: che un giorno potrà tornare a sognare.

Nessun modello di linguaggio, da solo, fa questo. Euri lo fa perché sotto non c'è soltanto un LLM, ma un'**architettura di memoria** che gli dà ciò che gli manca: continuità nel tempo, provenienza di ciò che sa, e la possibilità di essere corretto senza riscrivere tutto.

> **Dov'è in esecuzione oggi:** workstation Linux (Pop!_OS), doppia GPU NVIDIA RTX 4060 Ti 16GB — tutto in locale.

---

*Quello che segue è* come *Euri fa tutto questo. Non è il contorno — è la prova.*

## Architettura Cognitiva (V2.20)

> **Principio di separazione:** Euri può collegare due esperienze senza
> confonderle. La somiglianza crea una relazione; solo l'identità autorizza
> aggiornamento, consolidamento o supersessione.

### 1. Intent Classification — Pipeline a Due Layer
La classificazione dell'intent è a cascata: il layer veloce esaurisce la maggior parte dei casi, il layer lento interviene solo quando necessario.

**Layer 1 — Regex Router (0ms):** ~18 categorie di intent con pattern ordinati per specificità. Copre la quasi totalità dei comandi strutturati (SAVE_MEMORY, SAVE_TODO, WEB_SEARCH, EXECUTE, TEACH, DICTATION…).

**Layer 2 — LLM Fallback Gemma 26B (~600ms):** chiamato *solo* quando il router restituisce CHAT. Classifica 7 intent critici (WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, COMPLETE, CHAT) con un prompt a definizioni precise. COMPLETE è gestito interamente dal LLM — il contesto conversazionale distingue "l'ho fatto" da narrazioni complesse che il regex non può disambiguare.

**Guard manifatturiero:** se la frase contiene termini chimici/analitici (XRF, talco, MFI, carbonato…) senza termini di sistema espliciti, EXECUTE viene bloccato in entrambi i layer.

> **AdaptiveClassifier — in ricostruzione (V2: plasticità ancorata):** la versione Welford è sospesa (`ADAPTIVE_CLASSIFIER_ENABLED = False`) — con e5-large 1024-dim l'encoding (~400ms) eguagliava il fallback LLM e i centroidi non erano calibrati (falsi positivi). Limite strutturale più sottile: il **selection bias**, il layer impara solo dalle utterance che *non* sa già classificare (le sole che raggiungono il maestro LLM), derivando verso la coda ambigua. La V2 è un **dimostratore di plasticità ancorata**: per ogni classe un *anchor* congelato + un *delta* vivo col guinzaglio (deriva massima vincolata), embedding statico sub-millisecondo, e un'**omeostasi in idle** — canary set + rollback automatico — che misura e ripristina l'integrità. In corso la **Fase −1**: l'harvest persistente delle etichette del maestro LLM (`euri:aclf:harvest`) accumula il dataset reale su cui costruire e validare, prima di riattivare il fast path.

### 2. Domain Gating + Ricerca 3-Livelli (RAG Autonomo)
Tutte le memorie estratte dalle conversazioni vengono lette dall'LLM, che assegna loro automaticamente delle "etichette di dominio" (es. *informatica, chimica, business, casa*). **Dal V2.19** l'assegnazione è **disambiguata dai vicini semantici** (P1): il tagger riceve come suggerimento non vincolante i domini delle memorie più vicine via KNN, evitando che frammenti corti finiscano nel dominio sbagliato (es. *neutro* del polipropilene letto come *fisica nucleare*). Nessun dominio è cablato nel codice — i suggerimenti vengono dalla memoria stessa di Euri, che resta un learner libero e portabile. All'**ingest**, un **Memory Guard** (V2.19) rifiuta dalle fonti non fidate (web) i contenuti con pattern di prompt-injection o esfiltrazione, prima che diventino memoria.

**Scelta della strategia (V2.19, controllore di memoria)** — su domande potenzialmente non-specifiche, prima della cascata il modello caldo decide *come* recuperare: `specific_search` (la cascata sotto, invariata), `wide_recall` (panoramica per AREE della memoria), `subject_recall` (tutto su un soggetto nominato) o `recent_context`. Una pre-gate regex (0ms) evita di interpellare il modello sulle domande fattuali secche. Dettagli nel changelog V2.19 (08/06).

Il recupero avviene a tre livelli in cascata:
1. **Identifier-first** — estrae dalla query acronimi (MFI, DCP), codici lotto (PPR-738P) e numeri decimali (3.2, 0.35%) e li cerca con keyword search diretta. Garantisce che fatti tecnici specifici vengano restituiti in cima anche quando il dominio è saturo di memorie simili.
2. **Domain-boosted KNN** *(V2.19)* — ricerca vettoriale sull'intero DB con un *boost* per le memorie nel dominio della query: il dominio è una **preferenza, non un filtro**. Un fatto molto pertinente ma archiviato in un dominio diverso da quello (non-deterministico) della domanda riemerge comunque. *(Prima della V2.19 era un gate rigido che filtrava per dominio e faceva fallback solo con <2 risultati → falsi negativi: Euri rispondeva "non ho niente in memoria" su fatti presenti in decine di memorie.)*
3. **Hybrid fill** — se i risultati sono ancora sotto il limite, `_search_hybrid` (semantic + safe_keywords) riempie i posti rimanenti.

### 3. Dream Engine (Cicli cognitivi in idle)
Quando non gli parli per un po', Euri entra in cicli cognitivi offline. Non è più un blocco "notturno": l'orchestratore separa pass leggeri, sogni creativi e manutenzione lenta.
- **Ciclo leggero** (~20 min di cadenza mentre è idle): valuta insight candidati, metabolizza correzioni pending, genera ipotesi trasversali da episodi ripetuti e propaga la provenienza.
- **Ciclo creativo** (~90 min): genera nuovi sogni cross-domain e promuove insight per convergenza.
- **Ciclo manutentivo** (~24h): risoluzione contraddizioni, self-observation, cleanup, pruning e consolidamento semantico.
- Pesca due memorie appartenenti a due domini *completamente diversi*, ma solo da
  fonti dirette o deliberatamente acquisite. Reflection, reaction, consolidamenti,
  anchor/episodi conversazionali, nodi superseded, contestati o da verificare non
  possono fondare un nuovo sogno; il JSON viene rivalidato dopo la shortlist Redis.
- **Loop 2b** — Chiede a **Qwen3.6 35B** (*thinking attivo*, modello dedicato) di cercare isomorfismi strutturali tra i due concetti usando un processo in 3 passi: astrazione logica → ricerca della dinamica condivisa → formulazione del principio generale. Qwen3.6 è separato da Gemma4: più lento ma con ragionamento astratto superiore, usato nei cicli offline senza vincoli di latenza realtime.
- **dream_trace (esperimento concluso nella raccolta, flag spento):** tra un ciclo creativo e il successivo sopravviveva un **residuo di esplorazione** distillato dal chain-of-thought del sogno appena concluso — max 5 righe, a livello di *strategia* ("che tipo di ponte ho provato e perché era debole"), mai contenuti né conclusioni. Raccolta congelata il 21/07 a 160 baseline / 74 trattamento validi; resta da compilare e aprire l'audit cieco descritto in `ESPERIMENTO_DREAM_TRACE.md`.
- Se l'analogia è forte, genera un **CANDIDATE Insight**.
- **Loop 2c** — La convergenza usa la distanza cosine soltanto come shortlist e un
  **LLM judge con thinking** per stabilire se due insight esprimono davvero lo
  stesso meccanismo. La convergenza da sola non basta più: le due premesse devono
  essere fedeli alle memorie sorgente e il ponte deve essere semanticamente
  `SUPPORTED`.
- Se il ponte è plausibile ma introduce una premessa nuova, nasce uno stato
  intermedio `hypothesis`: Euri lo dichiara sul Pulse, lo conserva
  temporaneamente per audit ma non lo inietta nel RAG e non lo scrive tra gli
  insight promossi. `FORCED`, `UNKNOWN` e misure mancanti sono fail-closed.
  Solo gli insight sostenuti diventano **PROMOTED**, entrano nel recupero e
  vengono scritti in Obsidian. `PROMOTED` significa però *sostenuto
  internamente e recuperabile*, non *vero nel mondo*: senza una conferma esterna
  l'insight conserva `requires_verification=True`.
- **Loop 2e — Memory Consolidation:** una volta ogni 24h, Euri raggruppa le memorie episodiche più richiamate (recalled_count ≥ 3) per dominio, individua i cluster semanticamente coerenti via KNN, **pre-filtra i candidati con un indice leggero ordinato per salienza in Redis ZSET** e poi **filtra i frammenti di soggetto diverso con il same-subject gate** (V2.19 — anti-conflazione, vedi changelog 08/06) prima di chiedere a Qwen3.6 di sintetizzare i soli frammenti coerenti in un unico nodo di conoscenza stabile. Il nodo consolidato preserva tutti i dati specifici (numeri, nomi, misure) eliminando la ridondanza episodica. Ogni cluster viene marcato con fingerprint per evitare ri-consolidazioni. Ispirato al consolidamento ippocampale durante il sonno REM: i frammenti episodici diventano conoscenza semantica a lungo termine. Max 3 consolidazioni per ciclo.
- **Loop 2f — Contradiction Resolution:** nel ciclo manutentivo, Euri cerca
  coppie di memorie fattuali vicine nello stesso dominio e usa
  `_llm_classify_pair` come prima barriera semantica. Il suo vocabolario
  operativo corrisponde a: `SAME` con valori incompatibili → contraddizione e
  `superseded_by`; `RELATED` → entità distinte, nessun soft-delete e nota di
  confronto; `DIFFERENT` o giudizio non risolto → nessuna modifica. La memoria
  superata non viene cancellata, ma esclusa dal retrieval in modo reversibile.
  Le coppie già analizzate restano tracciate per 180 giorni; massimo 15 per
  ciclo. Le fonti web sono escluse, mentre i nodi consolidati Loop 2e possono
  essere corretti. Le note distinguono target, risultato misurato, stato
  temporale e vera alternativa: non possono raccomandare una voce se le fonti
  non dichiarano già alternative e criterio di scelta. Prima del salvataggio,
  un dedup semantico elimina soltanto confronti esplicitamente equivalenti.
- **Loop 2g — Audit di Coerenza (V2.14):** chiude il loop tra le correzioni che
  Stefano fa durante la conversazione e la manutenzione della memoria in idle.
  Voice daemon e Silent Chat usano regex soltanto per **rilevare che l'utente
  sta segnalando una possibile correzione** e salvare il relativo
  `correction_signal`; le regex non stabiliscono mai l'identità di materiali,
  progetti o persone. Il dream model distingue poi `bad_memory`,
  `bad_reasoning`, `ambiguous` e falsi segnali. Su `bad_memory` incrementa
  `audit_flag` sulle memorie sospette; su `bad_reasoning` salva una `lesson`;
  negli altri casi non modifica la conoscenza.
- **Loop 2h — Self-Observation:** prima di raccontare una supersessione distingue
  semanticamente **identità** e **somiglianza**, senza liste di progetti o regex
  decisionali. È la seconda barriera, dopo il Loop 2f: `SAME` consente la
  reflection di evoluzione; `RELATED` ripristina le due memorie distinte,
  inverte in due fasi e con audit il vecchio `superseded_by`, quindi pubblica
  sul Pulse una nota prudente del tipo “X assomiglia a Y per…, ma resta
  diverso”; `DIFFERENT` ripristina le entità senza inventare un ponte;
  `UNKNOWN` non modifica nulla e lascia la coppia ritentabile. Oggi Euri
  **rileva e registra** queste analogie: il Pulse non le trasforma ancora in
  relazioni durevoli recuperabili dal RAG. Una reflection già prodotta dal Loop
  2h, oppure marcata come conflazione tra entità, non può alimentare una nuova
  self-observation: la narrativa non diventa prova di se stessa.
- I gate semantici aggiungono chiamate al modello locale durante la manutenzione:
  cicli più lenti, meno insight promossi e più astensioni sono un costo atteso
  della maggiore integrità, non una regressione.
- **Filtro del Risveglio (re-rank insight in retrieval):** complementare al Dream Engine. Il sogno (Loop 2b) resta libero e atemporale per design — il filtro di rilevanza opera solo al recupero conversazionale. `search_insights` applica una penalty moltiplicativa (×1.5 default) sulla cosine distance per gli insight i cui due domini non sono apparsi nelle memorie *curate* di Stefano (`teach/user/reflection`) negli ultimi 30 giorni. Non sopprime: deprioritizza. Se domani Stefano riapre un dominio archivio, l'insight risale automaticamente. `passive` e `conversation` escluse dal set `INSIGHT_ACTIVE_SOURCES` perché spugne ambient — dry-run aveva mostrato 0% archivio con tutti i source operativi (no-op). Con `teach/user/reflection` → 35% archivio sui 95 insight promossi, caso "Radio QUQU ↔ materiali" correttamente penalizzato. Cache `_active_domains` 5 min.

- **Propagazione di provenienza (V2.20, invariante A):** ogni ciclo, dopo 2f/2e, `_provenance_propagation_pass` ricalcola **dal vivo** la solidità delle fonti di ogni nodo consolidato (`consolidated_from`). Un nodo le cui fonti sono state superseded/contraddette/cancellate viene marcato `provenance_stale` (**down-rank** nel retrieval: demozione, non esclusione → fail-safe) + `requires_verification` (Euri si copre, *"da confermare"*). Si auto-guarisce se le fonti rientrano. Chiude il buco per cui una correzione a una memoria-foglia poteva essere **silenziosamente disfatta** da un nodo consolidato che l'aveva già assorbita: le correzioni ora si **propagano** lungo gli edge di provenienza, invece di fermarsi alla foglia. Audit read-only in `diag_provenance.py`.

- **Metadati agnostici per le memorie:** ogni memoria porta anche `memory_axes`, un riassunto strutturale che separa contenuto, soggetto esplicito/acefalo, marker temporali e motivi di audit. Le memorie acefale vengono marcate `requires_verification=True` e restano correggibili; il campo è pensato per retrieval e controllo, non per cambiare il dominio a mano.

> **Nota tecnica:** Il timer di idle usa `time.time()` (wall-clock) per contare correttamente anche le ore in cui il PC è in sospensione.

### Euri Pulse — Bus Afferente + Iniziativa
Euri ha già dei *sensi* — presenza (VisualGate), file del Vault, orologio dei reminder, e l'**interocezione** dei propri loop (sogni, insight, consolidamenti) — ma finora ognuno era un arco riflesso privato: sentiva *e reagiva* nello stesso gesto. **Euri Pulse** dà loro un sensorio condiviso: i sensi emettono eventi tipizzati su uno stream Redis `euri:pulse`, con un envelope volutamente generico `{sense, source (extero|intero), kind, payload, salience, ts}` — così qualsiasi stimolo futuro entra senza toccare il bus. Dal V2.20 esiste anche un consumer prudente: l'**Initiative Controller** rilegge il JSON reale collegato all'evento, valuta tensione/idle/cooldown e chiede al modello se vale una domanda breve. Oggi consuma solo insight promossi e memorie passive incerte: non parla "per riempire", parla solo se può nominare l'evento che l'ha attivata. `pulse_watch.py` resta lo strumento di osservazione (tail / `--replay` / `--stats`). Kill-switch `PULSE_ENABLED` + `INITIATIVE_ENABLED`.

Gli eventi cognitivi `memory_relation/comparison_noted` dichiarano una
somiglianza tra memorie distinte senza creare una nuova memoria e senza
autorizzare fusioni. Il Cognitive Projector li conserva come timeline
osservabile, ma l'Initiative Controller e il RAG non li consumano ancora come
relazioni semantiche: questo confine è intenzionale e impedisce che un'analogia
diventi silenziosamente un fatto.

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

Il VisualGate include inoltre una **percezione sociale Fase 0** locale e osservativa:
MediaPipe misura pochi movimenti facciali stabilizzati e la posa della testa sugli
stessi frame usati per presenza e identita'. Non assegna emozioni, non salva immagini,
non chiama l'LLM e non modifica ancora il comportamento di Euri. Specifica e percorso
di attivazione progressiva sono in `SPEC_SOCIAL_PERCEPTION.md`.

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
| STT / Trascrizione | faster-whisper `large-v3` (CUDA float16 — NVIDIA RTX 4060 Ti) |
| TTS / Voce | sherpa-onnx + Piper (`vits-piper-it_IT-paola-medium`) |
| Embedding | sentence-transformers `intfloat/multilingual-e5-large` (1024-dim, asimmetrico query/passage) |
| Classificatore Veloce | AdaptiveClassifier V2 (plasticità ancorata) — in ricostruzione, Fase −1 harvest (vedi sezione 1) |
| Web search | ddgs (DuckDuckGo, no API key) + beautifulsoup4 |
| Gate visivo | OpenCV YuNet + SFace (webcam, 2fps), MediaPipe Face Landmarker osservativo |
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
| *"Memorizza questo / queste informazioni"* | Salva l'ultimo scambio (tuo turno + risposta di Euri) come sintesi fedele |
| *"Ricordati il [soggetto discusso]"* | Cattura la **sostanza** di ciò che avete detto su quel soggetto, non solo l'etichetta nel comando (V2.19) |
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
- **Memoria anaforica:** *"memorizza questo / queste informazioni"* — salva l'ultimo scambio (sintesi fedele), non le parole del comando.
- **Memoria su un soggetto discusso (V2.19):** *"ricordati il macinato di Seari"* — il modello caldo capisce che rimandi a un tema appena affrontato e cattura la **sostanza** della conversazione su quel soggetto, invece di salvare la sola etichetta presente nel comando.
- **Arricchimento (merge):** se aggiungi un dettaglio nuovo a qualcosa di già salvato, Euri **arricchisce** la memoria esistente e te lo annuncia ("Ho aggiornato la memoria: …") invece di scartarlo come duplicato o crearne uno doppio. Se invece è un soggetto diverso, salva separato.
- **Impegni con scadenza:** *"Devo fare X fra 5 minuti"* — un impegno è una **memoria di prima classe** con `due_at` e stato pending/done (niente più silo separato): passa dallo stesso path hardened delle memorie, il piano conversazionale lo vede sempre (*"che impegni ho?"*, *"cosa è scaduto?"* rispondono col contenuto reale, anche per scadenze future), e il promemoria viene consegnato **una volta sola, quando sei presente**, formulato naturalmente. Gli scaduti vengono nominati, non contati.
- **Passive Learner:** Euri ascolta passivamente e dopo 45 secondi di silenzio salva informazioni utili in background. La deduplicazione è conservativa: la similarità semantica propone soltanto candidati e un fatto viene eliminato solo se il contenuto è già coperto e il giudice restituisce un verdetto esplicito. In caso di dubbio Euri conserva il fatto, perché un doppione è correggibile mentre un'informazione persa no. Prima del salvataggio, un audit separato verifica inoltre che ogni affermazione sia sostenuta dai turni dichiarati e ripara gli ID sorgente incompleti; il gate di utilità passivo decide solo `KEEP/JUNK` e non riscrive più il testo.
- **Stessa cosa in Silent Chat:** i comandi di salvataggio funzionano identici nella chat testuale (stesso coordinatore), senza più fingere il salvataggio.

### Salvataggio via Dropzone (Obsidian)
Crea una nota testuale nella cartella `EuriVault/Dropzone` in Obsidian e scrivi quello che ti serve. Euri lo leggerà, classificherà il dominio, sposterà il file e lo inserirà nel suo database RAG in meno di un secondo.

---

## Localizzazione e Personalizzazione

Euri e' nato come assistente personale per un utente reale, Stefano, e per una conversazione in italiano. Il repository non e' ancora un template neutro: lingua, nome dell'utente, tono, esempi operativi e alcune euristiche sono parte dell'ecologia locale del progetto.

Chi vuole riusare Euri deve prima personalizzare almeno questi punti:

- **Lingua:** prompt, istruzioni, regex/cue conversazionali, STT/TTS, messaggi UI e test sono pensati per l'italiano.
- **Utente:** il nome `Stefano` compare in prompt, esempi, logica di memoria passiva e formulazioni di risposta. Va sostituito con il nome/profilo dell'utente reale, oppure parametrizzato.
- **Persona di Euri:** genere, tono, voce TTS, formule di saluto e stile di conversazione sono scelte locali.
- **Contesto operativo:** percorsi come `EuriVault`, domini ricorrenti, esempi aziendali e abitudini di lavoro riflettono l'installazione originale.

Euri parla italiano per default. La **lingua** delle risposte vive nei prompt (`config.SYSTEM_PROMPT`, l'hint della Silent Chat, `EURI_CONTEXT.md`) e nelle euristiche di recall/conversazione: tradurli sposta la conversazione solo se vengono tradotti insieme anche cue, esempi e aspettative del modello.

Un punto merita attenzione a parte: l'**àncora temporale** iniettata nel contesto del modello a ogni turno (`core/brain.py`, "Data e ora corrente: …"). È resa **esplicitamente in italiano** dagli array `_GIORNI` e `_MESI` in `utils/date_utils.py` — *non* via `strftime('%A'/'%B')`. È una scelta deliberata: `strftime` segue il locale di sistema (spesso `C/POSIX` → giorno in inglese, es. "Saturday"), e un modello che risponde in italiano tende a ignorare un'àncora in lingua straniera, confabulando un cliché ("è venerdì sera") invece di leggere il dato. Scrivere la data nella lingua della conversazione la rende un'àncora forte. **Per un'altra lingua:** tradurre `_GIORNI`/`_MESI` (e l'etichetta "Data e ora corrente" in `brain.py`). `dt.weekday()` è 0=lunedì, quindi l'ordine degli array parte dal lunedì.

La stessa convenzione vale ora anche per il **Passive learner**. Ogni turno nel
prompt di estrazione usa l'àncora completa italiana, per esempio “domenica 23
gennaio 2022”, e il modello deve conservare espressioni come “venerdì scorso”
senza convertirle. La data canonica viene calcolata dal resolver deterministico
rispetto a `asserted_at`. Le sessioni lunghe sono analizzate in finestre
sovrapposte e i dettagli vengono salvati come fatti atomici; gli ID locali del
modello sono ricondotti ai turni reali e un audit semantico fail-closed risolve
o scarta le fonti prima del calcolo temporale. Se il modello produce comunque
una data numerica in conflitto, il guard corregge il contenuto prima di dedup e
salvataggio, registrando l'intervento nel `temporal_context`.

---

## Changelog

Versione corrente: **V2.20**. Lo storico completo delle modifiche è in [CHANGELOG.md](CHANGELOG.md).

Novità recenti:
- V2.20 (continua, 15/06/2026) — Propagazione di provenienza (invariante A): le correzioni si propagano ai nodi consolidati, il marcio non riemerge più; strip degli header `# Memoria (data)` nelle fusioni
- V2.20 (continua, 13/06/2026) — Euri Pulse (Fase 0): bus afferente `euri:pulse`, i sensi osservano senza ancora agire
- V2.20 (continua, 13/06/2026) — Àncora temporale in italiano: Euri non sbaglia più il giorno della settimana
- V2.19 (continua, 09/06/2026) — Richiamo temporale: la memoria vissuta prima dei pensieri riflessivi
- V2.19 (continua, 08/06/2026) — Plausibility gate: negative result (archiviato) + contesto operativo opzionale
- V2.19 (continua, 08/06/2026) — Controllore di memoria: decisioni semantiche come ruolo del modello già caldo

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
> Cognition: A Working Implementation, V2.19*.
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
