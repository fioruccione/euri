# Euri e PlastVision — decisione del 30/07/2026

## Perché esiste questa nota

Questa nota conserva ciò che è stato verificato e deciso oggi. Non autorizza
una fusione dei progetti e non apre un nuovo sviluppo immediato.

## Stato verificato

### Euri

- Il contratto `loop2h-evidenced-identity-v1` è attivo nel runtime.
- Nel primo ciclo vivo dopo il riavvio, Loop 2h ha riconosciuto correttamente
  come stesso soggetto due memorie sul codice `FIORE 042`, con base esplicita e
  audit persistito.
- La reflection risultante ha però narrato la correzione come
  “osservazione recente”, mentre la memoria vincente era cronologicamente più
  vecchia della memoria errata. Il gate d'identità ha quindi funzionato; resta
  un difetto distinto nella narrazione temporale di Loop 2h.
- La reflection è rimasta prudenzialmente
  `internal_self_observation` e `requires_verification=true`.

### PlastVision

- PlastVision è funzionante. Il suo uso operativo è sospeso principalmente
  perché alcune parti degli impianti non sono ancora collegate ai PLC/MES, non
  perché il software sia inattivo o da ricostruire.
- Il server è un Dell PowerEdge T340 con Linux Mint. PlastVision raccoglie dati
  in PostgreSQL e Redis.
- PostgreSQL è il deposito dati costruito da PlastVision: **non** è un accesso
  diretto al database del MES.
- Le fonti attuali comprendono OPC-UA diretto, letture energetiche e meccanismi
  indiretti per ottenere il contesto MES. La provenienza di questi canali deve
  restare esplicita; non vanno presentati come equivalenti a un futuro accesso
  ufficiale al MES.
- Lettura live eseguita in transazione forzata read-only il 30/07/2026:
  - `raw_sensor_data`: circa 41,4 milioni di righe;
  - `production_context`: circa 10,2 milioni;
  - `dosatori_portata`: circa 583 mila;
  - `silos_peso`: circa 617 mila;
  - `consumi`: circa 534 mila;
  - i flussi principali risultavano aggiornati alle 16:58 circa.
- PlastVision possiede già raccolta, KPI deterministici, Guardian, RAG,
  memoria Redis e assistente AI. La directory locale contiene anche una vecchia
  copia di Euri, ormai divergente dall'Euri attuale e da non aggiornare per
  copia diretta.
- Il worktree locale di PlastVision contiene numerose modifiche e dati non
  tracciati: non va modificato prima di congelarne intenzionalmente lo stato.
- È stata rilevata una credenziale PostgreSQL hardcoded in uno script di
  avvio. La credenziale non è riportata qui: prima di qualunque integrazione
  dovrà essere rimossa dal codice, ruotata e sostituita da un'identità dedicata
  realmente read-only.

## Intuizione emersa

I due progetti affrontano lo stesso problema da direzioni complementari:

```text
PlastVision: macchine → misure → KPI → situazione attuale
Euri: eventi → provenienza → memoria → comprensione nel tempo
```

L'eventuale combinazione non deve affidare i calcoli industriali al modello.
PlastVision resta autorità per acquisizione, formule, soglie, KPI e stato
macchina. Un'Euri industriale potrebbe consumare soltanto eventi e risultati
deterministici per collegarli allo storico, spiegare ricorrenze e dichiarare
fonti e incertezza.

## Decisione presa oggi

**Non fondere ora Euri e PlastVision.**

- Euri personale resta il laboratorio cognitivo.
- PlastVision resta il sistema operativo industriale.
- Nessun clone del disco, fusione dei repository o modifica a PlastVision viene
  avviato sulla sola base dell'intuizione.
- L'ipotesi viene conservata per un pilot separato e reversibile dopo il
  collegamento dei PLC mancanti previsto dopo le ferie di agosto.

Questa è una sospensione deliberata, non una dimenticanza.

## Pilot minimo congelato come direzione, non come protocollo sperimentale

Quando i nuovi collegamenti saranno operativi e stabili:

1. PlastVision produce una fotografia giornaliera immutabile e depurata.
2. Un'istanza separata di Euri la legge senza accesso di scrittura.
3. La memoria industriale usa Redis/namespace separati dalla memoria personale.
4. Euri produce un briefing con fonti, timestamp e grado d'incertezza.
5. Per almeno due settimane Stefano confronta il briefing con la situazione
   reale, senza che Euri modifichi MES, PlastVision o impianti.

Il pilot ha valore soltanto se Euri:

- porta un'informazione o un collegamento che PlastVision da solo non espone;
- rende sempre visibile la provenienza;
- distingue misura, target, risultato e ipotesi;
- riduce tempo di analisi o intercetta una ricorrenza utile;
- non aumenta falsi allarmi o sicurezza ingiustificata.

Se produce soltanto una parafrasi dei grafici, l'integrazione non giustifica la
complessità.

## Prerequisiti prima del pilot

- raccolta dei nuovi PLC stabile per un periodo concordato;
- snapshot/versione pulita del repository PlastVision;
- credenziale hardcoded rimossa e ruotata;
- account PostgreSQL dedicato e verificato read-only;
- memoria, log, configurazione e identità dell'istanza industriale separati;
- nessuna azione verso macchine o MES;
- protocollo e criteri di successo congelati prima di osservare i risultati.

## Trigger per riaprire la decisione

Riaprire `PV-01` soltanto quando i collegamenti PLC/MES mancanti saranno
operativi e la raccolta sarà stata osservata stabile. Fino ad allora questo
documento è il promemoria sufficiente: non iniziare integrazioni laterali.
