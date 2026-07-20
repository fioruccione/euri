# Percezione sociale visiva di Euri

**Stato: Fase 0 implementata il 20/07/2026; attiva dal prossimo riavvio, solo osservativa.**

## Intenzione

Estendere il VisualGate da semplice rilevatore di presenza e identita' a recettore
sociale non invasivo. Lo scopo non e' attribuire emozioni certe a una persona, ma
aiutare Euri a regolare tempi, tono e iniziativa come farebbe un collega attento.

Il risultato desiderato e' un assistente che conservi bene i ricordi e sappia anche
notare, con prudenza, se l'interlocutore sembra disponibile, concentrato altrove,
divertito o forse in difficolta' durante uno scambio.

Principio epistemico:

`movimento osservato -> andamento temporale -> ipotesi contestuale -> adattamento`

Non:

`espressione del volto -> emozione certa -> memoria`

## Confine tra osservazione e interpretazione

Il sensore puo' osservare elementi descrittivi e continui:

- sorriso lieve o marcato;
- sopracciglia contratte;
- occhi e testa rivolti verso tastiera, schermo o altrove;
- volto presente ma non orientato verso l'interazione;
- variazione rispetto al comportamento neutro abituale della persona riconosciuta.

Da questi segnali Euri puo' formulare soltanto ipotesi deboli, per esempio
`attenzione_probabile=tastiera` o `possibile_riscontro_positivo`. Non deve concludere
automaticamente `Stefano e' triste`, `arrabbiato` o `approva quello che ho detto`.
La letteratura mostra che il movimento facciale e' misurabile, ma il suo significato
emotivo dipende da persona e contesto e non ha una corrispondenza univoca.

## Architettura proposta

1. Riutilizzare i frame gia' acquisiti dal VisualGate, senza aprire una seconda volta
   la webcam e senza conservare immagini.
2. Aggiungere un estrattore di landmark/blendshape, preferibilmente MediaPipe Face
   Landmarker in modalita' video o live stream. YuNet resta responsabile di presenza
   e ritaglio; SFace resta responsabile dell'identita'.
3. Stabilizzare i valori con baseline personale, finestra mobile e isteresi. Battiti
   di ciglia, singoli frame e cambi di luce non devono creare eventi.
4. Pubblicare su `euri:pulse` soltanto transizioni significative e a bassa frequenza,
   mai il flusso grezzo dei frame.
5. Scrivere lo stato corrente nel `CognitivePresent` come osservazioni `observed`
   con TTL breve. Le interpretazioni restano `inferred` o `hypothetical`.
6. Il livello cognitivo decide se il segnale modifica il comportamento. Nessuna
   frase e' cablata nel recettore e nessuna singola osservazione autorizza il TTS.

La Fase 0 implementata usa gli stessi frame del VisualGate a 2 fps, esclusivamente
dopo un riconoscimento recente di Stefano. MediaPipe riduce i blendshape a tre
segnali (`smile`, `brow_contraction`, `gaze_down`) e conserva orientamento grezzo
della testa. Il dato corrente vive in `euri:social:latest` per 30 secondi; un punto
numerico al minuto alimenta `euri:social:baseline` per l'audit. Le sole transizioni
persistenti entrano in `euri:pulse` con salienza bassa. Nessuna immagine e nessuna
memoria cognitiva vengono scritte.

La Control Room rende ripetibile la calibrazione della Fase 0. L'identita' raccoglie
quattro prototipi SFace in posture diverse senza ridurre la soglia di autenticazione.
Il recettore sociale raccoglie quattro brevi finestre neutro/sorriso in postura
abituale/diritta e persiste soltanto soglie e riepiloghi numerici. Distribuzioni
sovrapposte vengono rifiutate: una calibrazione incerta non sostituisce le soglie
generiche. Questo migliora la misura, ma non abilita ancora alcun effetto cognitivo.

Esempio di stato transitorio, non di memoria:

```text
social.attention = keyboard
social.smile_trend = increasing
social.brow_tension = 0.58
social.confidence = 0.74
```

## Effetti comportamentali desiderati

- Sguardo stabile su tastiera o lavoro: ridurre o rinviare Initiative.
- Sorriso successivo a una battuta: possibile feedback positivo, senza considerarlo
  conferma fattuale e senza salvarlo in memoria.
- Segnale persistente di difficolta' durante una spiegazione, coerente con le parole:
  preferire una risposta piu' breve o una domanda delicata di chiarimento.
- Segnale ambiguo o isolato: nessuna azione.
- Persona sconosciuta: nessuna profilazione personale e nessuna memoria; al massimo
  disponibilita' generale all'interazione, nel rispetto del percorso ospite isolato.

La percezione sociale deve modificare principalmente **come e quando** Euri parla,
non **che cosa considera vero**.

## Fasi deliberate

### Fase 0 - Osservazione

- Estrarre pochi segnali visivi interpretabili.
- Calcolare baseline e stabilita' senza influenzare Euri.
- Loggare transizioni, confidenza, durata e fault.
- Misurare carico CPU e falsi positivi in luce, postura e uso reali.

### Fase 1 - Disponibilita'

- Usare soltanto orientamento e attenzione probabile per bloccare o rinviare
  interventi proattivi.
- Nessuna modifica alle risposte richieste esplicitamente dall'utente.

### Fase 2 - Regolazione conversazionale

- Fornire al prompt pochi segnali stabilizzati insieme al contesto del dialogo.
- Consentire al modello di adattare brevita', tono o richiesta di chiarimento.
- Rivalidare lo snapshot immediatamente prima di ogni eventuale efferenza.

Un eventuale interprete multimodale Gemma appartiene a questa fase, non al sensore.
Non deve analizzare continuamente la webcam: puo' ricevere un solo frame volatile
durante una pausa reale o per chiarire un evento ambiguo, insieme agli ultimi turni
e ai segnali stabilizzati. L'output deve separare osservazioni e ipotesi con relativa
confidenza; non puo' produrre verita' fattuali o memorie. Il flag
`SOCIAL_PERCEPTION_MULTIMODAL_ENABLED` e' predisposto ma resta spento e il consumer
non e' ancora implementato.

### Fase 3 - Calibrazione personale

- Apprendere soltanto dalle correzioni esplicite dell'utente quali segnali sono utili.
- Conservare parametri di baseline, non etichette emotive o immagini.
- Estendere eventualmente voce/prosodia e postura come sensi indipendenti, mantenendo
  provenienza e confidenza separate prima della fusione multimodale.

## Vincoli da non perdere

- Nessuna immagine o video persistente per impostazione predefinita.
- Nessuna memoria cognitiva creata da una sola osservazione visiva.
- Nessuna diagnosi psicologica, sanitaria o di sincerita'.
- Nessun classificatore opaco `felice/triste/arrabbiato` come fonte di verita'.
- Fail-open per ascolto e fail-silent per interpretazione sociale: se il recettore
  manca o e' incerto, Euri continua a funzionare senza fingere di vedere.
- Kill switch separato e Fase 0 osservativa prima di qualsiasi effetto live.
- I segnali non devono superare identita', permessi o quarantena dell'ospite.

## Criterio per riprendere il lavoro

La prima implementazione si ferma alla Fase 0 e include test deterministici
su smoothing, TTL, transizioni, cambio identita' e assenza di persistenza delle
immagini. Solo dati reali raccolti con la webcam di Stefano possono autorizzare la
Fase 1; la Fase 2 richiede inoltre esempi annotati da Stefano per distinguere almeno
concentrazione, indisponibilita' e riscontro positivo.

Installazione del modello locale ufficiale:

```bash
./venv/bin/python scripts/install_social_perception_model.py
```

Audit read-only dopo il riavvio:

```bash
./venv/bin/python scripts/audit_social_perception.py
```

Riferimenti tecnici iniziali:

- https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/python
- https://ai.google.dev/edge/api/mediapipe/python/mp/tasks/vision/drawing_styles/face_landmarker/Blendshapes
- https://journals.sagepub.com/doi/10.1177/1529100619832930
