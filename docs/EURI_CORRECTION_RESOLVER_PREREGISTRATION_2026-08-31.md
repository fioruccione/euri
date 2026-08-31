# Euri — preregistrazione Correction Resolver

**Data:** 31 agosto 2026

**Stato iniziale:** preregistrato prima della modifica runtime

**Caso organico:** ICMA 2 / FIMIC FPP20 / LAS500 → RAS500

## 1. Confine

Questo trattamento riguarda soltanto una correzione fattuale esplicita che
chiede anche il salvataggio. Non classifica cambiamenti di opinione,
ambivalenze, differenze di contesto o contraddizioni irrisolte: quei casi
restano nel programma `IDENT-01` e non autorizzano `latest truth wins`.

Il raw conversazionale resta immutabile. La memoria precedente deve restare
ispezionabile e diventare inattiva soltanto attraverso una relazione esplicita
e reversibile con la nuova memoria.

## 2. Baseline organica congelata

Il 31 agosto 2026 il turno iniziale è stato trascritto come `Fimici` e
`Hikma 2`. La prima ricerca ha comunque recuperato la memoria diretta
`bc8f7583-b331-4eff-a606-c6d3afed7bbf`, ma il follow-up «Da dove viene questo
ricordo?» ha ereditato il focus errato `Hikma 2` e ha ricevuto un RAG estraneo.

Stefano ha poi corretto esplicitamente:

- macchina `ICMA 2`;
- pompa `FIMIC FPP20`;
- filtro `FIMIC RAS500`, non `LAS500`;
- fonte della correzione: Stefano.

Il runtime ha prodotto:

1. correction signal `7eace62c-30cc-4c3a-bed6-7c71dc08c6d8`, pending, con
   `rag_ctx_ids` appartenenti al follow-up fallito e nessun nodo in quarantena;
2. memoria passiva `78399f5d-6fe1-4581-8d16-a28f1f882401` con il fatto corretto;
3. memoria user `dd9fc3cf-7040-42d0-842c-38527ccf5617` con lo stesso fatto;
4. supersede corretto del duplicato passivo da parte del nodo user;
5. nessuna relazione fra il nodo user nuovo e la memoria completa
   `bc8f7583-b331-4eff-a606-c6d3afed7bbf`, che è rimasta attiva con `LAS 500`.

Il guard atto-parola ha invece funzionato: quando Gemma ha dichiarato di avere
già corretto la memoria senza ricevuta, la risposta è stata rettificata prima
della conclusione del turno.

## 3. Ipotesi causali preregistrate

H1. Il learner passivo non distingue un turno `CORRECT_FACT` dal normale
apprendimento e può pubblicare la nuova formulazione prima del save esplicito.

H2. `SAVE_MEMORY` correttivo considera un solo nearest neighbour. Il duplicato
passivo appena creato può quindi nascondere l'antecedente autorevole e completo.

H3. Il correction signal conserva soltanto il RAG della risposta contestata.
Quando quel RAG era già sbagliato, il segnale nasce senza il candidato che una
ricerca basata sul testo della correzione recupera nel turno corrente.

H4. Creazione del nuovo nodo e `superseded_by` del precedente non costituiscono
oggi una singola relazione atomica e osservabile.

## 4. Trattamento preregistrato

1. Un frame affidabile con `CORRECT_FACT` o `CORRECT_ENTITY` non alimenta il
   learner passivo. Il raw e il correction signal restano conservati.
2. Il save correttivo valuta un insieme bounded di candidati attivi nello stesso
   scope. Un duplicato testuale della nuova versione non può essere scelto come
   antecedente.
3. La selezione privilegia una fonte diretta, l'overlap sul soggetto e gli
   identificatori esplicitamente rifiutati dalla correzione. In caso di parità
   sostanziale non sceglie.
4. La ricerca usa anche la formulazione correttiva recente, senza affidarsi
   soltanto al contesto del turno fallito.
5. La nuova memoria nasce con riferimento all'antecedente e stato pending; una
   transazione Redis collega `correction_of` e `superseded_by` prima di rendere
   conclusa la correzione. Se l'antecedente era stato messo in quarantena da un
   correction signal ancora pending, la stessa transazione chiude anche quel
   signal per impedirne una successiva reinterpretazione da Loop 2g.
6. Se il collegamento fallisce, il nuovo nodo resta escluso dal retrieval e la
   risposta non afferma che la memoria è stata corretta.
7. Dopo la costruzione del RAG del turno correttivo, il correction signal può
   aggiungere separatamente i candidate ID osservati e applicare la quarantena
   conservativa. Il contesto storico originario non viene sovrascritto.

## 5. Test congelati prima del trattamento

### C1 — caso ICMA2

Con candidati:

- nuova formulazione passiva identica con `RAS500`;
- memoria user completa con `LAS500`;
- memoria industriale simile ma di altro soggetto;

il resolver deve escludere il duplicato della nuova versione e scegliere la
memoria user completa con `LAS500`.

### C2 — ambiguità

Due memorie dirette ugualmente compatibili con lo stesso valore rifiutato non
autorizzano alcun supersede.

### C3 — assenza di antecedente

Una correzione senza candidato sopra soglia viene salvata come nuova
affermazione oppure resta pending secondo il chiamante, ma non ritira nodi
estranei.

### C4 — learner passivo

Un turno affidabile `CORRECT_FACT` e la relativa risposta sono esclusi
dall'estrazione passiva; un normale `INFORM` riutilizzabile resta eleggibile.

### C5 — relazione

Su successo, vecchio e nuovo nodo espongono rispettivamente `superseded_by` e
`correction_of`. Su errore della transazione non deve esistere una mezza
correzione richiamabile.

### C6 — signal enrichment

Gli ID recuperati cercando la correzione sono conservati in un campo distinto
dal RAG originario e possono mettere in quarantena il vero candidato senza
toccare nodi fuori scope o privi di overlap sufficiente.

## 6. Criteri di accettazione

- tutti i test C1–C6 verdi;
- regressioni SAVE, correction quarantine, semantic turn e passive provenance
  verdi;
- manifest unitario completo verde;
- nessuna modifica dei turni raw;
- nessun framework o database nuovo;
- rollback tramite config o revert del commit;
- il caso `Hikma 2` resta esplicitamente aperto in `RETR-01`: questo trattamento
  non può dichiararlo risolto per effetto collaterale.

## 7. Esito del trattamento

Implementazione completata il 31 agosto 2026. Durante la verifica è emersa una
collisione non visibile nella baseline: la quarantena corretta per il RAG
escludeva l'antecedente anche dalla successiva ricerca di salvataggio. Il confine
è stato quindi reso esplicito e coperto da regressione: soltanto il Correction
Resolver può leggere candidati `correction_pending`; Brain, RAG e altri
operatori continuano a escluderli.

I test congelati C1–C6, la chiusura atomica del signal, il rollback via config e
le regressioni SAVE/quarantine/semantic turn sono verdi. Il manifest unitario
completo chiude 91/91 in 77,3 secondi.

Con Euri ferma è stata applicata anche la riparazione append-only del caso
organico tramite `scripts/repair_20260831_icma2_correction.py`. La memoria
completa corretta è `8696bc28-ec1c-4f68-b99c-24db1052b5d5`; vecchio nodo,
duplicato corto e signal sono collegati/chiusi. Quattro copie Redis integrali e
la quarantena dei tre Markdown storici mantengono audit e reversibilità. I raw
turn non sono stati riscritti e i numeri del progetto restano da verificare.
