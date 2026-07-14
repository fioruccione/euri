# Cognitive Present: contratto preparatorio

**Stato: implementazione isolata, nessuna integrazione runtime durante la raccolta
`dream_trace`.** Il modulo `core/cognitive_present.py` non e' importato dal daemon,
non scrive in Redis e non entra nei prompt.

## Scopo

Il Cognitive Present rappresenta cio' che Euri sta vivendo nell'ordine dei secondi
e dei minuti. Non e' memoria a lungo termine e non e' retrieval: e' il piano
transitorio che impedisce a una decisione iniziata in uno stato di parlare in uno
stato ormai diverso.

Contiene:

- fase di interazione: ascolto, elaborazione, parlato;
- canale corrente: voce, mobile, testo o sistema;
- lease conversazionale, rinnovata al termine del parlato che apre una risposta;
- ultimo turno utente accettato e domanda pendente;
- osservazioni sensoriali o operative con fonte, stato epistemico e scadenza;
- versione monotona dello stato semantico.

## Provenienza

Ogni osservazione usa uno dei seguenti stati:

- `observed`: rilevato direttamente da un sensore;
- `user_asserted`: dichiarato dall'utente;
- `system_fact`: noto dal sistema o dalla configurazione;
- `inferred`: dedotto, quindi non utilizzabile come fatto senza copertura;
- `hypothetical`: possibilita' da verificare;
- `refuted`: non piu' valido.

Una capability e la sua disponibilita' devono essere osservazioni distinte. Per
esempio `camera_capability=configured` e `camera_available=false` consentono a Euri
di dire che la webcam e' temporaneamente indisponibile, senza inventare di non avere
alcun accesso alla webcam.

## Invarianti temporali

1. La durata del TTS non consuma la finestra concessa alla risposta dell'utente.
   La lease viene rinnovata quando il playback termina.
2. Un refresh sensoriale con lo stesso significato non incrementa la versione. Un
   cambio di identita', disponibilita', fase, domanda o turno utente la incrementa.
3. L'expiry resta vincolante anche senza cambio di versione: la rivalidazione legge
   l'osservazione corrente e controlla il TTL.
4. Una decisione asincrona riceve un `DecisionToken`. Prima dell'output efferente
   deve rivalidare versione, fase e osservazioni da cui dipende.
5. Il presente non viene consolidato automaticamente in memoria. Gli eventi che
   meritano memoria continuano a passare dai normali gate epistemici.

## Sequenza d'integrazione dopo Dream Trace

1. Shadow mode: alimentare il componente da VisualGate, voce, TTS e modalita', ma
   usare lo stato solo per audit.
2. Rivalidazione Initiative: scartare o ricalcolare una proposta se il token e'
   diventato stale prima del TTS.
3. Lease conversazionale: usare `finish_speech` come origine della finestra di
   follow-up, con regressione sulla risposta lunga osservata il 14/07.
4. Contesto LLM: iniettare solo uno snapshot compatto e groundato delle capability
   e percezioni correnti. Questa fase richiede un audit separato.

Active Focus resta un piano distinto, su scala di giorni. I due componenti non
devono essere attivati insieme: altrimenti non sarebbe possibile attribuire gli
effetti osservati al presente transitorio o alla continuita' di lavoro.
