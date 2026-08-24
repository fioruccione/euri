# Coding agent temporaneo di Euri

Stato: **MVP operativo e verificato il 22 agosto 2026**

Versione OpenCode provata: **1.18.21**

## Scopo e confine

Euri può costruire un piccolo strumento Python quando una verifica numerica non
è già coperta da un tool permanente. OpenCode è una capability specializzata:
non sostituisce il modello conversazionale, la memoria, il retrieval o la
decisione finale di Euri.

```text
Stefano
   ↓ richiesta esplicita / conferma di una proposta
Frame semantico + ActionController di Euri
   ↓ executor.build_computational_tool
CodingJob
   ↓ workspace privato e temporaneo fuori dal repository
OpenCode → Ollama locale → modello con tool calling
   ↓ crea o corregge main.py
SecurityScanner + CodeRunner bubblewrap (obbligatorio)
   ↓ stdout/stderr reale
errore ───────────────→ OpenCode corregge (massimo 3 tentativi)
   ↓ successo
Euri interpreta il risultato e risponde
```

Il codice prodotto non è promosso a tool permanente, non entra nei Dream e non
viene salvato come memoria cognitiva. Workspace e sessione OpenCode vengono
eliminati alla chiusura del job.

## A. Architettura precedente conservata

Il percorso storico `run_code` resta adatto all'elaborazione di file già
presenti in `Scrivania/dati_per_Euri`:

```text
Utente → routing Euri → Executor.run_code
       → CodeRunner.generate_and_run
       → Brain genera un singolo script
       → SecurityScanner AST
       → subprocess/bubblewrap
       → stdout, artefatti ed eventuale errore → Euri
```

Componenti principali:

- `agent/executor.py`: registra il tool e lega la richiesta al `CodeRunner`;
- `agent/code_runner.py`: pre-estrae i documenti, chiede il codice al `Brain`,
  applica lo scanner, esegue e raccoglie stdout/stderr;
- `core/brain.py`: genera il singolo script;
- `voice_daemon.py`: presenta la ricevuta del tool e inserisce il risultato nel
  contesto conversazionale.

Il percorso precedente non è un coding agent: genera un solo programma, non
gestisce un workspace multi-turn e non rimanda automaticamente gli errori al
modello per una correzione.

## B. Nuovi componenti

### `agent/opencode_adapter.py`

- verifica via Ollama che il modello esista e dichiari la capability `tools`;
- crea una configurazione OpenCode per job;
- avvia `opencode serve` solo su loopback con autenticazione casuale;
- consente a OpenCode lettura, ricerca e modifica nel solo workspace;
- nega shell, Web/rete esterna, sub-agent, LSP, skill e directory esterne;
  resta consentito soltanto il collegamento locale necessario a Ollama;
- impone una deadline reale alla richiesta, abortisce e termina il process group;
- cancella la sessione OpenCode, inclusi transcript e diff, alla chiusura.

### `agent/coding_job.py`

- crea un workspace `0700` sotto `XDG_RUNTIME_DIR` (fallback `/tmp`), quindi
  fuori dalla radice Git di Euri;
- chiede la materializzazione di un unico `main.py`;
- osserva `main.py` come condizione di completamento: quando il file nuovo e'
  stabile, interrompe il turno narrativo residuo di OpenCode e passa subito
  all'esecuzione controllata;
- rifiuta file troppo grandi;
- passa il codice al `CodeRunner` senza eseguirlo tramite OpenCode;
- restituisce stderr reale a OpenCode per un massimo di tre tentativi;
- applica un budget totale oltre ai timeout per singola richiesta/esecuzione;
- conserva soltanto una traccia minima: id job, modello, numero tentativi e hash
  SHA-256 del codice.

### `agent/code_runner.py`

Il nuovo metodo `execute_generated_code` riusa scanner e runtime esistenti, ma
per il coding agent è **fail-closed**: se bubblewrap è assente, disabilitato o
non utilizzabile, il programma non viene avviato direttamente. Il comportamento
legacy di `run_code` resta invariato.

## C. Confronto delle capability

| Capability | `run_code` precedente | OpenCode + CodingJob |
|---|---|---|
| Generazione Python | sì, singolo passaggio | sì, sessione specializzata |
| Esecuzione | CodeRunner | CodeRunner, mai OpenCode |
| Feedback stdout/stderr | ritorna a Euri | ritorna anche al generatore |
| Retry/correzione | no | sì, budget limitato |
| Workspace | sandbox CodeRunner | workspace di costruzione per job |
| Modifica multi-file | no | tecnicamente possibile ma MVP vincolato a `main.py` |
| Shell OpenCode | non applicabile | negata |
| Web/rete esterna OpenCode | non applicabile | negata; Ollama resta su loopback |
| Accesso repository Euri | nessuno | negato per collocazione e permission |
| Test autonomi | esecuzione unica | esecuzione reale a ogni tentativo |
| Persistenza del codice | no | no |
| Promozione a tool stabile | no | rinviata, nessun automatismo |
| Memoria/RAG | solo risultato conversazionale | idem; job e transcript esclusi |

## D. Cosa è stato mantenuto, integrato e non sostituito

**Mantenuto:** ActionController, Executor, SecurityScanner, bubblewrap,
interruzione tramite `stop_event`, cattura stdout/stderr, directory dati e
artefatti, iniezione del risultato nella risposta di Euri.

**Integrato:** OpenCode è soltanto il costruttore/correttore di `main.py`.

**Non sostituito:** `run_code`, utile per operazioni note e rapide sui file. Il
nuovo tool serve quando il problema richiede di costruire un verificatore ad
hoc, non come passaggio obbligatorio per ogni calcolo.

## E. Attivazione e autorità

Il tool si chiama `build_computational_tool` ed è una capability contestuale:
non è stato aggiunto alcun trigger lessicale specifico. Il frame semantico e
l'ActionController devono riconoscere un vero gesto operativo.

- richiesta esplicita di Stefano + confidenza sufficiente: esecuzione;
- proposta autonoma di Euri: conferma obbligatoria prima della scrittura locale;
- dubbio, richiesta di opinione o spiegazione: normale conversazione;
- modello, OpenCode o sandbox indisponibili: fallimento dichiarato, senza
  degradazione a shell o esecuzione diretta.

## F. Modello locale

Il default è il modello conversazionale `gemma4:26b`. La build attuale
`hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M` dichiara in Ollama soltanto
`completion` e `vision`: è quindi adatta ai Dream ma non può pilotare i tool di
OpenCode. Il preflight la rifiuta prima di avviare un job, evitando attese
inutili. Un override `EURI_CODE_AGENT_MODEL` è ammesso soltanto per un modello
che esponga `tools`.

## G. Configurazione

Le variabili principali sono:

- `EURI_CODE_AGENT_ENABLED`;
- `EURI_OPENCODE_BIN`;
- `EURI_CODE_AGENT_MODEL`;
- `EURI_CODE_AGENT_OLLAMA_HOST`;
- `EURI_CODE_AGENT_MAX_ATTEMPTS` e `EURI_CODE_AGENT_MAX_STEPS`;
- `EURI_CODE_AGENT_NO_ARTIFACT_TIMEOUT` (default 150 s),
  `EURI_CODE_AGENT_PROMPT_TIMEOUT` e `EURI_CODE_AGENT_TOOL_TIMEOUT`;
- `EURI_CODE_AGENT_KEEP_FAILED_WORKSPACE` (solo diagnosi, default disattivo).
- `EURI_CODE_AGENT_RESEARCH_LOG_ENABLED` (default attivo),
  `EURI_CODE_AGENT_RESEARCH_LOG_DIR` e
  `EURI_CODE_AGENT_RESEARCH_LOG_RETENTION_DAYS` (default 7 giorni).

Ogni tentativo conserva, prima che la sessione effimera venga eliminata, il
prompt inviato, la risposta HTTP integrale, il transcript strutturato della
sessione, l'indice delle parti `tool`, lo stato dell'artefatto e l'esito della
sandbox. I file sono privati in `research_logs/coding_agent/`, ignorati da Git
e non vengono mai inseriti in Redis, Obsidian o retrieval. La cattura e'
best-effort e non puo' cambiare l'esito del job.

OpenCode deve essere presente nel `PATH`. Sulla workstation di prova è stato
installato in `~/.local/bin/opencode`.

## H. Rischi residui e limiti

- Un programma che termina correttamente può comunque implementare un modello
  matematico sbagliato: stdout verificabile non equivale a verità scientifica.
- Il modello può consumare il primo tentativo mostrando il codice senza creare
  il file; il ciclo lo rileva e richiede la materializzazione.
- La latenza comprende almeno una generazione LLM e può includere correzioni;
  il tool va usato per problemi che giustificano questo costo.
- Scanner e sandbox riducono il rischio esecutivo, ma non certificano la
  correttezza di dipendenze, assunzioni, unità o dataset.
- La futura promozione di un programma temporaneo a tool permanente richiederà
  test, revisione e approvazione esplicita; non è parte dell'MVP.

## Prova end-to-end del 22 agosto 2026

Caso: bilancio di massa con 1.250 kg in ingresso, 2,4% di umidità rimossa e
1,8% di scarto sulla massa secca.

- tentativo 1: Gemma ha restituito codice testuale ma non `main.py`; rilevato;
- tentativo 2: OpenCode ha creato `main.py`;
- SecurityScanner e bubblewrap: superati;
- esecuzione: 200 ms, exit code 0;
- risultato: 30 kg acqua, 21,96 kg scarto, 1.198,04 kg prodotto;
- controllo: somma delle masse pari a 1.250 kg;
- workspace: eliminato;
- progetto OpenCode osservato: `global`, directory sotto `XDG_RUNTIME_DIR`, non
  il repository Euri.

Sonde semantiche reali con Gemma4:

- richiesta operativa priva dei dati citati: `abstain`, nessun job;
- domanda esplicita di sola opinione: `converse`, nessun job;
- bilancio completo e confronto completo fra due linee: capability corretta,
  autorità `user_explicit`, confidenza 1,0 e decisione `execute` in entrambi i
  casi.

### Controesempio operativo dalla Silent Chat

Nel primo uso reale dalla Silent Chat, OpenCode ha materializzato `main.py` dopo
circa 191 secondi ma Gemma ha continuato il proprio turno fino al timeout di 420
secondi. Il job e' quindi fallito pur avendo gia' prodotto l'artefatto richiesto.
La condizione di stop e' stata corretta al confine giusto: Euri ora osserva la
creazione o modifica stabile di `main.py`, abortisce soltanto il turno residuo
del coding agent e consegna il file al CodeRunner. Il timeout resta come guardia
per il caso in cui l'artefatto non venga mai creato.

La ripetizione dopo il riavvio ha chiuso la catena reale in circa 166 secondi
complessivi, contro i circa 447 del caso fallito. OpenCode ha usato tre tentativi;
la condizione di stop sull'artefatto e' intervenuta dopo 40,2 secondi nel secondo
e dopo 14,2 nel terzo. CodeRunner ha eseguito il candidato finale in 200 ms e ha
restituito correttamente il primo passo discreto di convenienza della linea B:
16,0% di scarto A (6.732 kg conformi B contro 6.720 kg A).

### Secondo ciclo live: successi e limite residuo

Il ciclo successivo dalla Silent Chat ha separato tre casi importanti:

- bilancio sequenziale su 10.000 kg: corretto e chiuso (400 kg di umidita',
  192 kg di contaminazione, 282,24 kg di perdita di processo, 9.125,76 kg
  conformi; bilancio finale 10.000 kg);
- ottimizzazione discreta di due linee: corretta; su passo 100 kg il minimo e'
  2.800 kg alla linea A e 7.000 kg alla B, costo 1.582 euro e 9.016 kg conformi;
- simulazione Monte Carlo: fallita chiusa dopo 420 secondi. OpenCode non ha
  materializzato un nuovo `main.py`, quindi SecurityScanner e CodeRunner non
  sono mai stati avviati.

Il timeout conferma un limite diverso dal monologo successivo alla scrittura:
lo stop sull'artefatto puo' accelerare un file gia' creato, ma non un tentativo
che non produce alcun artefatto.

Il test del 23 agosto ha riprodotto lo stesso limite su un'ottimizzazione di tre
materiali: il primo tentativo e' rimasto 420 secondi senza creare `main.py`,
impedendo ai due retry dichiarati di partire. E' quindi attivo un watchdog
*no-artifact* separato, predefinito a 150 secondi e configurabile tramite
`EURI_CODE_AGENT_NO_ARTIFACT_TIMEOUT`. Alla scadenza Euri:

1. interrompe il turno OpenCode corrente;
2. elimina sessione, transcript e diff del tentativo;
3. apre una sessione pulita;
4. ripresenta il problema completo con la richiesta di materializzare subito
   `main.py`;
5. prosegue con il tentativo successivo entro il budget totale del job.

Il watchdog non aumenta il tempo totale e non considera progresso il solo testo
generato dal modello: l'unica evidenza utile resta la comparsa dell'artefatto.

Anche la comparsa di `main.py` e un exit code `0` non bastano a dichiarare il
job riuscito. Il processo puo' infatti essere sintatticamente valido ma
incompleto (`pass`, funzioni mai chiamate, calcolo senza output). Per il coding
agent Euri richiede quindi almeno un risultato osservabile: stdout reale oppure
un artefatto persistente prodotto nella cartella di output. Se mancano entrambi,
l'esito diventa `missing_observable_result` e viene restituito a OpenCode come
evidenza per il tentativo di correzione. Il contratto del `run_code` storico non
cambia.

## Osservabilita' nella Silent Chat

Un job lungo non resta piu' dietro al solo spinner generico. La Silent Chat
mostra un pannello aggiornato da eventi reali del `CodingJob`:

- preparazione del workspace e avvio del backend;
- numero del tentativo OpenCode in corso;
- materializzazione e dimensione di `main.py`;
- controllo ed esecuzione nella sandbox;
- eventuale correzione dopo stderr;
- completamento oppure errore.

Durante l'attesa il pannello espone anche secondi trascorsi e, quando
`nvidia-smi` e' disponibile, utilizzo, VRAM e potenza di ciascuna GPU. Non viene
mostrata una percentuale di completamento: il modello non rende conoscibile in
anticipo quanto manca, quindi una barra percentuale sarebbe inventata. La
telemetria e' osservativa e un suo errore non modifica mai il job.

Il pannello compare soltanto dopo che il frame semantico ha selezionato davvero
`build_computational_tool`. Un test live del 23 agosto ha distinto questo caso da
un falso blocco: il JSON del frame ricco era troncato, la richiesta era ricaduta
in CHAT e nessun job OpenCode esisteva da osservare. Il servizio semantico usa
ora un budget piu' ampio e, se il frame ricco non e' decodificabile, esegue un
secondo passaggio JSON molto compatto che recupera il solo gesto operativo. La
capability concreta resta scelta e autorizzata dall'ActionController.
