# Cognitive Present: contratto runtime

**Stato: runtime v2 attivato il 27/08/2026 per decisione esplicita di Stefano.**
Il componente resta transitorio e locale al processo: non scrive in Redis e non
viene consolidato automaticamente in memoria. L'integrazione corrente copre fase,
lease vocale, focus breve, domande pendenti e rivalidazione di Initiative.

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
- focus conversazionale breve: ultimi turni utente accettati, senza sintesi interna;
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
6. La lease di risposta e il focus non coincidono: la prima dura secondi e autorizza
   un follow-up senza wake word; il secondo dura minuti e impedisce a Initiative di
   cambiare argomento. Un focus non rende automaticamente indirizzato il parlato.

## Integrazione runtime v1

1. Voce e TTS alimentano fase, lease e ultimi turni utente accettati.
2. Initiative classifica un candidato rispetto al focus come `EXTENDS`, `RELATED`
   o `UNRELATED`; solo `EXTENDS` puo' entrare prima della scadenza del focus.
3. Initiative rivalida un `DecisionToken` immediatamente prima del TTS: un nuovo
   turno, una domanda pendente o un cambio fase rendono stale la proposta.
4. Il contenuto completo dello snapshot non entra nel prompt generale del Brain.
   Capability e percezioni correnti richiedono ancora un audit separato.

## Integrazione runtime v2: esito della pipeline vocale

Ogni segmento terminato dal VAD riceve un `trace_id` distinto. Il daemon registra
un esito strutturato attraverso gate mobile/visivo, SpeakerAuth, STT e consenso
conversazionale. La causa finale e' prodotta dal ramo di codice che accetta o
ferma il segmento; non viene ricostruita a posteriori dalle righe di log.

Il Cognitive Present conserva a TTL soltanto l'ultimo esito sanitizzato come
`voice.last_pipeline_outcome`. Il record non contiene audio, trascrizione o
embedding: soltanto durata, verdetto e score SpeakerAuth, presenza di testo,
wake word, indirizzamento, destinazione e reason code. Una vista Redis bounded
con TTL 5 minuti permette ai canali locali di condividere le ultime trace; Pulse
riceve la stessa whitelist come evento `telemetry`, quindi il Cognitive Projector
la ignora e nessun oggetto cognitivo nasce automaticamente.

Il Brain vede al massimo tre segmenti recenti non inoltrati. Il prompt dichiara
che sono eventi distinti, vieta di inferirne contenuto o identita' mancanti e ne
consente l'uso soltanto per domande sul recente funzionamento percettivo. I turni
accettati non vengono reiniettati da questo ponte e continuano a seguire la
normale history.

Una domanda esplicita come «mi hai sentito?» usa direttamente il reason code
sanitizzato e non affida al modello la scelta della causa. Separatamente, il
primo frame VAD revoca un'eventuale chat Dream in streaming. L'avviso «ti ho
sentito» viene emesso solo se il runtime osservava davvero una chiamata idle
attiva; non e' inferito dalle parole dell'utente.

Active Focus resta un piano distinto, su scala di giorni. I due componenti non
vanno fusi: condividono provenienza e interfacce, ma hanno tempi e responsabilita'
diversi. L'eventuale attivazione di Active Focus resta una decisione separata.
