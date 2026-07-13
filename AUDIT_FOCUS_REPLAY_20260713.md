# Audit replay Active Focus — riconosci il tuo lavoro?

Per ogni giorno: i top-3 focus che il motore aveva ATTIVI a fine giornata.
Segna: `[S]` = sì, era il mio lavoro · `[~]` = in parte · `[N]` = no/fantasma.
GO pre-registrato: ≥80% dei giorni con S o ~ senza fantasmi in cima.

---

## Monday 22/06   [ ]S  [ ]~  [ ]N

- **0.99** [chimica polimeri] Provare sul bancale Poseidon il blend a mescola fredda: 70% PP MFI 12 + 30% PEMD da scarto  
  _(nato da user 21/06 12:01, rinforzato 28×)_
- **0.80** [chimica polimeri] La sessione riguarda l'ottimizzazione dei gradi di polipropilene attraverso la regolazione  
  _(nato da user 19/06 09:14, rinforzato 9×)_
- **0.68** [chimica polimeri] Test in corso con un additivo sperimentale per ridurre la fluidità del polipropilene ricic  
  _(nato da user 19/06 15:23, rinforzato 5×)_

---

## Tuesday 23/06   [ ]S  [ ]~  [ ]N

- **0.77** [chimica polimeri] La sessione riguarda l'ottimizzazione dei gradi di polipropilene attraverso la regolazione  
  _(nato da user 19/06 09:14, rinforzato 11×)_
- **0.77** [chimica polimeri] Provare sul bancale Poseidon il blend a mescola fredda: 70% PP MFI 12 + 30% PEMD da scarto  
  _(nato da user 21/06 12:01, rinforzato 32×)_
- **0.51** [controllo qualità] Stefano ipotizza che la presenza di sporcizia o patine negli ugelli e nei canali delle mac  
  _(nato da user 23/06 17:02, rinforzato 1×)_

---

## Wednesday 24/06   [ ]S  [ ]~  [ ]N

- **0.81** [logistica materiale] Progetto Poseidon: nuovo pallet aperto per sacconi (cod. 04PALXXX) con dimensioni 1100x110  
  _(nato da user 23/06 13:21, rinforzato 3×)_
- **0.58** [lavoro] Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica in fase di ap  
  _(nato da user 24/06 12:57, rinforzato 1×)_
- **0.57** [chimica polimeri] La sessione riguarda l'ottimizzazione dei gradi di polipropilene attraverso la regolazione  
  _(nato da user 19/06 09:14, rinforzato 11×)_

---

## Thursday 25/06   [ ]S  [ ]~  [ ]N

- **0.62** [logistica materiale] Progetto Poseidon: nuovo pallet aperto per sacconi (cod. 04PALXXX) con dimensioni 1100x110  
  _(nato da user 23/06 13:21, rinforzato 3×)_
- **0.45** [chimica polimeri] La risposta di Stefano conferma che l'ancora del mio insight è reale: la presenza di resid  
  _(nato da reaction 25/06 16:40, rinforzato 0×)_
- **0.44** [lavoro] Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica in fase di ap  
  _(nato da user 24/06 12:57, rinforzato 1×)_

---

## Friday 26/06   [ ]S  [ ]~  [ ]N

- **0.45** [anagrafica cliente] La risposta di Stefano indica che ho commesso un **errore di fondamento**: l'ancora reale   
  _(nato da reaction 26/06 16:21, rinforzato 0×)_
- **0.45** [logistica materiale] Progetto Poseidon: nuovo pallet aperto per sacconi (cod. 04PALXXX) con dimensioni 1100x110  
  _(nato da user 23/06 13:21, rinforzato 3×)_
- **0.43** [processi operativi] La risposta di Stefano indica che l'ancora reale della connessione è stata riconosciuta: i  
  _(nato da reaction 26/06 13:15, rinforzato 0×)_

---

## Saturday 27/06   [ ]S  [ ]~  [ ]N

- **0.45** [processo industriale] Stefano ha confermato l'ancora reale della mia connessione: la non-linearità del materiale  
  _(nato da reaction 27/06 09:54, rinforzato 0×)_
- **0.35** [anagrafica cliente] La risposta di Stefano indica che ho commesso un **errore di fondamento**: l'ancora reale   
  _(nato da reaction 26/06 16:21, rinforzato 0×)_

---

## Monday 13/07   [ ]S  [ ]~  [ ]N

- **0.43** [motori] La risposta di Stefano conferma che l'ancora del mio insight è reale: la necessità tecnica  
  _(nato da reaction 13/07 11:17, rinforzato 0×)_
- **0.42** [chimica industriale] Stefano ha confermato che l'ancora logica del mio insight è vera: il controllo della sciss  
  _(nato da reaction 13/07 08:06, rinforzato 0×)_


---

## ESITO (13/07/2026, audit fatto a voce con Stefano)

**GO — 7/7 giorni riconosciuti** ("mi sembrano tutti frutto delle mie conversazioni,
sono sicuramente mie conversazioni con Euri"). Nessun fantasma in cima.

Sfumatura onesta: Stefano ha validato la PROVENIENZA ("sono le mie conversazioni"),
con la nota che "per capire meglio servirebbe più contesto" — le etichette a una riga
sono magre. Requisito per il runtime: il focus deve portare più contesto del solo
seme (periodo di attività, numero di conversazioni che l'hanno alimentato, ultimo
rinforzo). Il ranking di salienza per-giorno non è stato validato in dettaglio:
da riverificare in esercizio.

Prossimo passo: design runtime (core/focus.py, flag FOCUS_ENABLED) DOPO la fine
della raccolta dream_trace (~18/07), come pre-registrato.
