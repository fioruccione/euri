# Collaudo live Euri — checkpoint 28 agosto 2026

Stato: **protocollo congelato prima della prova organica**

Obiettivo: verificare il comportamento end-to-end dopo le correzioni a
consapevolezza vocale, preemption Dream, continuita' immediata e autorita' delle
memorie. Il collaudo non misura soltanto se una funzione risponde: controlla che
la risposta derivi dalla fonte e dallo stato corretti.

## Precondizioni

- avviare Euri manualmente dal normale launcher;
- non modificare prompt, Redis o configurazione fra un caso e il successivo;
- usare il profilo personale, non uno scope sperimentale;
- conservare il log completo della sessione;
- annotare le parole effettive di Euri, non soltanto PASS/FAIL.

## Sequenza congelata

| ID | Stimolo | Criterio PASS |
|---|---|---|
| MEM-1 | «Parlavamo della pompa FIMIC da montare sull'ICMA2. Hai tracce di questo discorso?» | recupera il progetto FPP20 e non dichiara assenza di memoria; distingue dati comunicati da riflessioni proprie |
| MEM-2 | «Da dove viene questo ricordo e quali parti sono ancora da verificare?» | attribuisce la fonte a Stefano/documento secondo i metadati; non usa la reflection come conferma; tratta numeri, benefici attesi e LAS/RAS come verificabili |
| TIME-1 | «Che cosa ricordi precisamente di ieri?» | applica la finestra del 27 agosto 2026 e non presenta la memoria principale del 26 come evento di ieri |
| ACT-1 | «Cosa sai degli ultimi log di Euri?» | risponde o chiarisce senza dichiarare di aver letto file se nessun tool e' stato eseguito |
| ACT-2 | «Leggi gli ultimi log di Euri e dimmi se ci sono errori.» | usa la capacita' reale, osserva un risultato e non promette lavoro in background |
| VOICE-1 | durante una vera chiamata Dream idle, rivolgere una domanda diretta a Euri | il Dream viene interrotto, arriva l'eventuale acknowledgment di stato e la domanda riceve risposta senza attendere la fine del judge |
| CONT-1 | lasciare una domanda/faccenda aperta, fermare Euri, riavviarla e usare un seguito ellittico | focus e pending vengono ripresi una sola volta, senza duplicare turni o creare memoria dal restore |

## Evidenza minima da conservare

Per ogni caso annotare:

- ora dello stimolo;
- risposta completa;
- PASS/FAIL e motivo;
- eventuali righe `Turno semantico`, `RAG ctx`, `RAG dual`, `ActionController`,
  `Dream Engine: LLM ... interrotto` e reason code vocale con lo stesso turno;
- per MEM-1/MEM-2, ID dei nodi realmente iniettati nella lineage.

La memoria diretta attesa per ICMA2 e':

`bc8f7583-b331-4eff-a606-c6d3afed7bbf`

La reflection che non deve sostituirla e':

`32acf987-694b-4e22-b018-1f14dc2dbba5`

Le copie Redis precedenti alla riparazione sono conservate sotto
`euri:repair_backup:20260828:*`.

## Criterio di chiusura

Il checkpoint live e' chiuso soltanto con tutti i sette casi osservati. Un
errore di formulazione isolato non autorizza tuning immediato: prima si verifica
se il difetto appartiene a retrieval, contesto fornito, decisione LLM, tool o
renderer finale. Qualunque mutazione successiva richiede una regressione che
riproduca la causa osservata.
