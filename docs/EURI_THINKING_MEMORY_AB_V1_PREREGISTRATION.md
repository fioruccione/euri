# Preregistrazione — Thinking × memoria A/B v1

**Esperimento:** `euri_thinking_memory_ab_v1`  
**Tipo:** DEVELOPMENT — LoCoMo è interamente aperto, quindi non è validazione
indipendente.  
**Stato:** eseguito il 28/07/2026; protocollo congelato prima dei risultati.

## Domanda

Il miglioramento osservato con Gemma4 in modalità thinking appartiene al modello
in generale oppure rende realmente più utile la memoria dual-channel?

## Disegno congelato

Si riusano gli stessi 129 casi già congelati dalla prompt-ablation v2:

- 43 `evidence_flip`;
- 43 controlli answerable appaiati;
- 43 avversariali.

Due soli arm:

| arm | contesto | prompt | thinking | num_predict |
|---|---|---|---|---:|
| `rag_think` | RAG puro originale | strict | sì | 2000 |
| `dual_think` | RAG protetto + turni originali localizzati dalle passive | strict | sì | 2000 |

Seed, modello, digest, domanda, speaker, temperatura e prompt sono identici.
Varia soltanto il contesto di memoria. L'ordine dei due arm è alternato
deterministicamente per caso. Massimo 258 chiamate.

Entrambi i contesti vengono ricostruiti byte-per-byte dagli artefatti del census
con il clock congelato al `created_at` originale. Divergenza dallo SHA salvato:
arresto chiuso. Nessuna nuova ingestion, retrieval o lettura del Redis personale.

## Metriche e verdetto congelato

Contrasto primario: `dual_think − rag_think`.

- token-F1 complessivo sugli answerable;
- token-F1 sui 43 `evidence_flip`;
- accuratezza/astensione sui 43 avversariali;
- delta per ciascuna delle cinque conversazioni;
- bootstrap clusterizzato per conversazione;
- migliorate/peggiorate e McNemar avversariale descrittivo;
- latenza separata per arm.

Verdetto:

- **GO:** delta F1 complessivo > 0, delta F1 `evidence_flip` > 0, almeno 4/5
  conversazioni non negative, delta avversariale ≥ −0,02;
- **NO-GO:** delta F1 complessivo ≤ 0, oppure delta `evidence_flip` ≤ 0, oppure
  delta avversariale < −0,02;
- **INCONCLUSIVO:** ogni altro esito.

Anche un GO resta sviluppo su LoCoMo aperto. L'audit umano cieco delle risposte
cambiate resta necessario perché il token-F1 penalizza alcune parafrasi corrette.

## Conseguenza prevista

- GO: candidato per thinking selettivo quando il dual-channel aggiunge verbatim;
- parità: il vantaggio appartiene soprattutto al thinking generale;
- NO-GO: non attribuire alla memoria il miglioramento osservato nella v2.

Nessuna modifica alla produzione prima del risultato.

## Esito post-esecuzione

Verdetto preregistrato: **GO**.

| metrica | `rag_think` | `dual_think` | delta dual−rag |
|---|---:|---:|---:|
| token F1 answerable | 0,1604 | 0,2123 | +0,0519 |
| evidence-flip F1 | — | — | +0,0942 |
| false astensioni | 0,5814 | 0,5000 | −0,0814 |
| accuratezza avversariale | 0,9535 | 0,9535 | 0 |

Sedici casi sono migliorati e sei peggiorati; quattro conversazioni su cinque
sono non negative. L'intervallo bootstrap clusterizzato del delta F1 è
`[0,005995; 0,110144]`. Il risultato dimostra, nel development set aperto, che
il vantaggio non appartiene soltanto al thinking generale: a thinking
controllato, il contesto dual-channel resta migliore del RAG puro. Non
costituisce validazione indipendente sull'intero LoCoMo o su altri benchmark.
