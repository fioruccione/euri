# Preregistrazione — Ablation prompt DEVELOPMENT v2

**Esperimento:** `euri_prompt_ablation_v2`
**Tipo:** DEVELOPMENT (LoCoMo ormai interamente aperto) — **non** una validazione
indipendente.
**Stato:** preparato, **non eseguito**. Attende audit prima della generazione.
**Manifest congelato:** `benchmarks/euri_memory/prompt_ablation_v2_manifest.json`
(`manifest_sha256 = fd93bb17…`).

> Regola: qualunque modifica a prompt, manifest, arm o metriche dopo aver visto i
> risultati produce una nuova versione. Questo file resta v2.

---

## 1. Obiettivo

Separare sperimentalmente **quattro possibili confondenti** del risultato
dual-channel, trattandoli **simmetricamente** (nessuno assunto vincitore):

1. prompt strict di astensione (false astensioni?);
2. distrattori nel contesto;
3. thinking attivo/disattivo;
4. sottostima del token-F1 sulle parafrasi corrette.

Il probe q123/q134 su contesti ricostruiti con SHA diverso **non** è prova
causale: resta ipotesi. Questa ablation la mette alla prova su contesti
byte-esatti.

## 2. Percorso 3 — ricostruzione byte-esatta ORA, cattura per il futuro

- I contesti dual sono **ricostruiti byte-per-byte** dagli artefatti del census
  (`prompt_ablation._reconstruct_contexts`: `build_rag_context` + memoria-stub
  `_FrozenBaseMemory`), come dimostrato dai 2.996 riusi della append-ablation.
- Lo **SHA-256 del contesto è verificato per OGNI caso** contro l'originale
  (`final_sha256`); divergenza → arresto chiuso.
- **Nessuna nuova ingestion, nessun nuovo retrieval, nessun Redis personale.**
- Un **hardening di cattura** (`dual_channel_worker._capture_generation`, opt-in
  via `EURI_DUAL_CAPTURE_DIR`) persiste i testi completi per gli esperimenti
  futuri: testi in `audit_output/` gitignored; nei report tracciabili solo hash,
  metadati e path relativi. Non eseguito qui.

## 3. Campione congelato (43/43/43)

Tre strati deterministici dai report di validazione (`evidence_hit`/`answerable`),
ID congelati prima dei risultati, **nessun gold**:

- **A — evidence-flip (43):** il dual aggiunge realmente il gold (rag miss → dual hit).
- **B — controlli answerable (43):** answerable non-flip, appaiati per
  (conversazione, replica, categoria).
- **C — avversariali (43):** appaiate per (conversazione, replica).

Tutti e tre gli strati si riempiono esattamente a 43 (nessuna carenza).

## 4. Cinque arm

| arm | famiglia | think | note |
|---|---|---|---|
| A0 | strict | no | prompt originale del benchmark, **rigenerato fresco** |
| A1 | strict | sì | identico ad A0, cambia solo il thinking |
| B0 | balanced | no | calibrazione bilaterale (rispondi se il fatto è nel contesto anche con distrattori) |
| B1 | balanced | sì | identico a B0, cambia solo il thinking |
| C0 | two-stage | no | stadio 1 selettore (JSON: answerable + indici frammenti, nessuna risposta) → stadio 2 risposta sui soli frammenti |

**A0 rigenerata, non riusata.** Modello e contesto sono identici alla vecchia A0,
ma una risposta prodotta giorni prima introdurrebbe un confondente temporale/runtime.
La vecchia A0 diventa un **controllo di stabilità generativa**: % risposte
identiche, delta F1, delta astensione, divergenze per domanda.

**SHA-256 dei prompt congelati:**
- strict `ac23ae63…`
- balanced `4e9b3fae…`
- two_stage_selector `6905b251…`
- two_stage_answer `84dc5be4…`

Il selettore C0 restituisce **indici di frammento ricostruibili**, validati
in-range sul contesto; non vede mai gold, evidence ID o risposte attese.
`answer_seed = 42` congelato, unico su tutti gli arm/casi: varia solo prompt × think.

## 5. Forecast (nessuna nuova ingestion)

- 129 casi × 5 arm = **645 generazioni di risposta**;
- 129 chiamate extra del **selettore C0**;
- **totale massimo 774** (cap tecnico). Costo solo di generazione: i contesti sono
  ricostruiti byte-esatti dagli artefatti. La cattura futura, se attivata, aggiunge
  il costo di una nuova run del dual-channel — fuori da questa preparazione.

## 6. Metriche

**Primarie (development):** false-abstention rate sugli answerable; adversarial
accuracy/abstention; delta token-F1 sugli answerable; comportamento sui 43
evidence-flip.
**Secondarie:** exact match; migliorate/peggiorate/invariate; risposta cambiata;
breakdown per strato/categoria/conversazione; latenza e costo; interazione
prompt × thinking.

Il **token-F1 non è l'unico verdetto.** Audit cieco delle risposte cambiate: arm
anonimizzati, ordine randomizzato, domanda+gold+risposta, nessuna indicazione
dell'arm; etichette umane: corretta / parzialmente corretta / errata / astensione
corretta / falsa astensione. Un judge LLM **non** è metrica ufficiale; se usato
come diagnostica va dichiarato separatamente e non entra nel verdetto.

## 7. Interpretazione predefinita (congelata)

- balanced riduce le false astensioni mantenendo delta avversariale ≥ −0,02 →
  il prompt strict era un confondente sostanziale;
- two-stage supera balanced mantenendo la prudenza → il problema dominante è
  selezione/de-crowding prima della risposta;
- thinking migliora strict e balanced in modo simile → il collo di bottiglia
  include il budget di ragionamento;
- thinking aumenta le risposte ma peggiora avversariali o verbosità/F1 → non è
  una soluzione generale;
- **nessun risultato autorizza modifiche di produzione**: produce solo il
  candidato per il benchmark indipendente successivo.

## 8. Vincoli e consegna

Nessuna modifica a produzione, README, CHANGELOG, paper o V2.21. Nessuna nuova
ingestion/retrieval, nessun Redis personale. Commit locale della sola
preparazione, **nessun push**, **nessuna esecuzione reale**. Poi arresto per audit.

Codice: `benchmarks/euri_memory/prompt_ablation_v2.py` (isolato, generazione gated
da `execute=True`, non invocata); cattura in `dual_channel_worker`; test puri in
`test_prompt_ablation_v2.py`.
