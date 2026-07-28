# Preregistrazione — Ablation prompt DEVELOPMENT v2

**Esperimento:** `euri_prompt_ablation_v2`
**Tipo:** DEVELOPMENT (LoCoMo ormai interamente aperto) — **non** una validazione
indipendente.
**Stato:** preparato, **non eseguito**. Attende audit prima della generazione.
**Manifest congelato:** `benchmarks/euri_memory/prompt_ablation_v2_manifest.json`
(`manifest_sha256 = 6dc6a5fb…`).

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

**A0 rigenerata, non riusata.** Usa il contesto byte-esatto e il **seed originale
del census** (`run.answer_seed`, uguale su tutti gli arm di quella replica).
Il modello e il suo digest sono congelati nell'execution-manifest prima del run.
La vecchia A0 diventa un controllo di stabilità a contesto e seed bloccati; le
differenze residue possono includere runtime e non sono chiamate «pura
instabilità».

**SHA-256 dei prompt congelati:**
- strict `ac23ae63…` · balanced `4e9b3fae…` · two_stage_selector `6905b251…` ·
  two_stage_answer `84dc5be4…`

Il selettore C0 è **fail-closed** (`format="json"`, `answerable` booleano reale,
indici interi unici e in-range; qualsiasi violazione → astensione). Ordine degli
arm **controbilanciato deterministicamente per caso** e congelato nel manifest.
`case_id` canonico `conv-41__r0__q123` usato ovunque; `question_id` resta separato.

Il selettore C0 restituisce **indici di frammento ricostruibili**, validati
in-range sul contesto; non vede mai gold, evidence ID o risposte attese.
Ogni caso usa il seed della propria replica del census; all'interno del caso il
seed è identico per tutti e sette gli arm.

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
  report census. Congela inoltre **nome e digest del modello**; run, resume e
  analisi rifiutano valori differenti.
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
- **Prompt A0 byte-esatto**: il messaggio user conserva il wrapper originale
  `Partecipanti: …` di `dual_channel_worker._user_prompt`; SHA del payload,
  speaker, domanda, contesto e riferimento temporale sono verificati prima di
  salvare ciascun report.
- **Mini end-to-end senza modello**: un backend finto attraversa tutti i sette
  arm, inclusi i due stadi di C0, la cattura e la validazione completa. Un
  selettore C0 malformato viene verificato separatamente come fail-closed.

### Clock congelato → 129/129 byte-esatti (quarta soluzione)

Il primo dry-run aveva trovato **3/129 non ricostruibili** (`conv-49:q33` r0/r1,
`conv-49:q101` r0): `build_rag_context` produce etichette di recency
**dipendenti dal tempo**, che divergono in un giorno diverso dal census.

Soluzione adottata (senza produzione, ingestion o cattura): un **context manager
locale all'harness** (`frozen_clock`) congela `core.rag_context.now` a
`datetime.fromtimestamp(report["created_at"], tz=config.TIMEZONE)` durante la
ricostruzione. Con questo, **tutti i 129/129 tornano byte-esatti**. Il tempo di
riferimento è registrato nei metadati come `context_reference_at`.

Nota di metodo: la ricostruzione è **per-domanda** (`reconstruct_one`), non
sull'intero report — così una domanda estranea (`conv-49:q2`) non blocca la
conversazione (era il falso 27/129). Regressione: **clock corrente 126/129, clock
census 129/129**. Il dry-run integrale esce **non-zero** se `byte_exact_ok=false`.

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
