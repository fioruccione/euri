# Cognitive Present: contratto runtime

**Stato: runtime v1 attivato il 15/07/2026 per decisione esplicita di Stefano.**
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

Active Focus resta un piano distinto, su scala di giorni. I due componenti non
vanno fusi: condividono provenienza e interfacce, ma hanno tempi e responsabilita'
diversi. L'eventuale attivazione di Active Focus resta una decisione separata.
