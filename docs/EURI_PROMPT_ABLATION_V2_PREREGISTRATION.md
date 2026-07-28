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

## 4. Sette arm (thinking isolato dal budget)

Per attribuire davvero un effetto al *solo* thinking servono i controlli di budget
(`no-think/2000`): altrimenti il fattore sarebbe "thinking operativo + budget 2000".

| arm | famiglia | think | num_predict | note |
|---|---|---|---:|---|
| A0 | strict | no | 160 | prompt originale del benchmark, **rigenerato fresco** |
| A2 | strict | no | 2000 | controllo budget (isola il budget da A0) |
| A1 | strict | sì | 2000 | think isolato (vs A2, stesso budget) |
| B0 | balanced | no | 160 | calibrazione bilaterale |
| B2 | balanced | no | 2000 | controllo budget |
| B1 | balanced | sì | 2000 | think isolato (vs B2) |
| C0 | two-stage | no | 160 | selettore JSON fail-closed → risposta sui soli frammenti |

**A0 rigenerata, non riusata.** Modello e contesto identici alla vecchia A0, ma
usa il **seed originale del census** (`run.answer_seed`, uguale su tutti gli arm
di quella replica); la vecchia A0 diventa **controllo di stabilità generativa** a
parità di seed (differenze = pura instabilità nel tempo).

**SHA-256 dei prompt congelati:**
- strict `ac23ae63…` · balanced `4e9b3fae…` · two_stage_selector `6905b251…` ·
  two_stage_answer `84dc5be4…`

Il selettore C0 è **fail-closed** (`format="json"`, `answerable` booleano reale,
indici interi unici e in-range; qualsiasi violazione → astensione). Ordine degli
arm **controbilanciato deterministicamente per caso** e congelato nel manifest.
`case_id` canonico `conv-41__r0__q123` usato ovunque; `question_id` resta separato.

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

- 129 casi × 7 arm = **903 generazioni di risposta**;
- 129 chiamate extra del **selettore C0**;
- **totale massimo 1.032** (cap tecnico). Costo solo di generazione: i contesti
  sono ricostruiti byte-esatti dagli artefatti. La cattura futura, se attivata,
  aggiunge il costo di una nuova run del dual-channel — fuori da questa preparazione.

## 5-bis. Hardening pre-risultati (audit Codex)

- **Manifest a due livelli** (punto 7): il *case-manifest* committato registra la
  **baseline di produzione `bac00a0`**, non il commit sperimentale (niente
  autoreferenza); l'*execution-manifest* — non tracciato, in `audit_output/` —
  è firmato con l'HEAD corrente e lega corpus, localizzazione e gli **SHA dei 10
  report census**.
- **Integrità riusata dall'held-out** (punto 6): verifica corpus, artefatto di
  localizzazione, worktree tracciata pulita, HEAD == commit, modello e digest non
  nulli, output-dir e checkpoint legati all'identità completa, ogni report
  completato **rivalidato** prima dello skip; estranei/mancanti/corrotti rifiutati.
- **Nessun `forbidden_evidence_hits`** (punto 9): il gold può comparire
  legittimamente nel contesto; la garanzia è che gold/evidence **non sono
  parametri** dei builder e che messaggi/contesti sono ricostruibili dagli hash.
- **Cattura** (punto 8): filename e record includono `run_label`/`replica`/
  `case_id`; directory obbligatoriamente sotto `audit_output/` gitignored.
- **Audit cieco** (punto 11): codici casuali non riconducibili al nome dell'arm,
  mappa arm↔codice conservata **separata**; righe con domanda, gold, risposta e
  replica.
- **Dry-run integrale** (punto 12): materializza e rivalida tutti i **129 casi**
  (129 `case_id`, **nessuna collisione**, **17 `question_id` in due repliche**)
  ricostruendo i contesti byte-esatti PER-DOMANDA, **senza modello**. CLI completa
  `ablation-dry-run/exec-manifest/run/analyze/audit`.

### Esito del dry-run: 126/129 byte-esatti, 3 NON ricostruibili

Il dry-run ha fatto il suo lavoro di gate e ha trovato un limite reale:
**126/129 casi ricostruiscono byte-per-byte; 3 no** — `conv-49:q33` (r0 e r1) e
`conv-49:q101` (r0). La causa probabile è che `build_rag_context` produce testo
**dipendente dal tempo** (etichette di recency/ordinamento), quindi qualche base
diverge se ricostruita in un giorno diverso da quello del census. Nota di metodo:
`_reconstruct_contexts` di Codex processa l'intero report e aborta alla prima
divergenza (`conv-49:q2`, non un caso target) → la ricostruzione va fatta
**per-domanda** (fatto qui), altrimenti una domanda estranea blocca l'intera
conversazione (era il falso 27/129).

**Conseguenza (decisione dell'audit, non eseguita):** il percorso 3 (ricostruzione
byte-esatta) regge per 126/129 ma non per tutti. Le opzioni, tutte da approvare:
1. **cattura** (percorso 2, strumentazione già pronta): una nuova run del
   dual-channel cattura i contesti al momento della generazione → 129 byte-fedeli
   senza fragilità di ricostruzione;
2. **quarantena dichiarata** dei 3 casi (esecuzione su 126, ma rompe il 43/43/43
   e l'appaiamento per strato);
3. **ricostruzione time-independent** (congelare il tempo di `build_rag_context`).

L'esecuzione resta **bloccata** finché non si sceglie. Il dry-run che cattura il
problema è, di per sé, il deliverable che funziona.

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
