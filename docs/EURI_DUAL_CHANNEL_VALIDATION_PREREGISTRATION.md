# Preregistrazione — Validazione A/B della policy DUAL-CHANNEL

**Esperimento:** `euri_dual_channel_validation`
**Versione protocollo:** v1
**Stato:** preregistrato, **non eseguito**. Attende revisione prima di generare
il seed, costruire il campione o toccare la produzione.
**Policy in prova (congelata):** `dual-channel-q2r1-v1`
(`benchmarks/euri_memory/dual_channel.py`, `FROZEN_POLICY`).

> Regola di versionamento: qualsiasi modifica a policy, universo, campionamento,
> metriche o analisi dopo aver visto un qualunque risultato produce una nuova
> versione. Questo file resta v1.

---

## 1. Contesto

L'held-out passive (`seed914917171`, conv-30/43/47) ha dato un **NO-GO** per la
memoria passiva come pool competitivo: token F1 −0,055, prudenza avversariale
−0,074, provenance **peggiore** del RAG puro (71,2% → 65,7%), a 11,2× di costo.
L'analisi offline ha isolato il meccanismo (sfratto del verbatim) e ha trovato
**una sola architettura net-positiva sulla provenance**: **dual-channel**, con la
base RAG grezza protetta e le note passive usate **solo come locator** per
idratare i loro turni sorgente verbatim (il testo sintetico non entra nel prompt).
Simulazione strutturale sul dev set: **71,2% → 75,8% di provenance** (+4,5 punti,
9 recuperi esclusivi su 198), **0 gold persi**, a slot/caratteri controllati; il
guadagno è concentrato in single-hop ma la policy resta **category-agnostic**.

Questa validazione misura, su conversazioni **mai usate**, se quel +4,5 di
*disponibilità del verbatim* si traduce in **qualità della risposta** senza
erodere la prudenza — cosa che la sola provenance non può dire.

---

## 2. Policy congelata `dual-channel-q2r1-v1`

Composizione del contesto (deterministica, nessun LLM; codice in
`dual_channel.compose_dual_channel`, riprodotta sul dev set dal test
`test_dual_channel.py::test_dev_set_reproduction_matches_analysis`):

- **base** = **intero contesto prodotto dal ramo `rag_only`**, **integralmente
  protetto**: mai rimosso né troncato (il RAG può produrre **più di 5 nodi** nelle
  query temporali; il compositore non tronca);
- **Q = 2** note passive considerate, nell'ordine di retrieval registrato, usate
  **solo come locator**;
- **R = 1** source turn verbatim per nota;
- **dedup** rispetto alla base e fra i sorgenti idratati;
- **max 2 aggiunte** (quindi `final_slots <= base_slots + 2`, con `base_slots`
  qualunque sia il numero di nodi prodotti dal RAG);
- **budget 2.500 caratteri**: limita **solo** le aggiunte, mai la base;
- **nessun testo sintetico** delle note entra nel prompt;
- **category-agnostic**: il forte effetto single-hop è **diagnostico**, non un
  gate basato sulle etichette LoCoMo.

La policy è congelata **prima** del seed e dei risultati.

---

## 3. Disegno

- Confronto **appaiato per domanda**, stessa selezione, stessi modelli, stessa
  base:
  - **A. `rag_only`** — risposta sull'intera base rag_only protetta.
  - **B. `dual_channel`** — risposta sulla base **+** ≤2 turni verbatim idratati
    dai locator passivi (policy §2).
  Le due risposte condividono **la stessa base**: B aggiunge soltanto, quindi
  l'appaiamento è massimo e per costruzione **nessun gold della base può perdersi**.
- **Universo:** le **5 conversazioni LoCoMo mai usate** — conv-41, conv-44,
  conv-48, conv-49, conv-50 (escluse conv-26/42 di sviluppo e conv-30/43/47 del
  precedente held-out, ormai dev set aperto). Helper:
  `dual_channel.untouched_universe`.
- **Repliche:** 2 per conversazione (misurano la variabilità di Gemma; **non**
  aumentano N).
- **Unità indipendente: la conversazione.** N = 5. Domande e repliche sono
  annidate: analisi clusterizzata (§6).
- **Lingua:** italiana, con lo **stesso protocollo di traduzione congelato**
  dell'held-out (`heldout_localization`, `euri-heldout-it-translation-v1`),
  applicato alle sole conversazioni selezionate; manifest cieco, artefatto
  italiano sigillato con SHA.
- **Campionamento domande:** stratificato per categoria e deterministico da seed
  (riuso della pipeline `heldout-select` sull'universo untouched), per controllare
  il costo; N indipendente resta 5 a prescindere dal numero di domande. Il
  **census** (tutte le domande) è un'alternativa a scelta della revisione.

---

## 4. Ipotesi

- **H1 (primaria, qualità):** `dual_channel` aumenta il **token F1 appaiato**
  rispetto a `rag_only`, **senza** ridurre la **correttezza avversariale**
  (astensione).
- **H0:** nessuna differenza appaiata nel token F1 **e** nella prudenza.
- **Guardrail preregistrati:**
  - un calo significativo della prudenza avversariale è **fallimento** anche con
    F1 in aumento;
  - **nessun gold della base può essere perso** (invariante strutturale della
    policy: verificato, deve essere 0).
- **Attesa strutturale (secondaria, non è il test):** la provenance/evidence
  verbatim sale (~+4,5 pt sul dev set). Poiché B aggiunge e non rimuove, questo è
  quasi garantito: **non conta come successo**; il successo è F1 senza costo di
  prudenza.

---

## 5. Metriche

**Primarie**
- token F1 appaiato;
- correttezza / astensione avversariale;
- evidence (verbatim) recall.

**Secondarie**
- exact match;
- accuratezza per categoria — **diagnostica, mai un gate** (la policy è
  category-agnostic);
- costo: retrieval aggiuntivo, slot e caratteri finali, chiamate LLM, token;
- composizione: aggiunte, duplicati saltati, scarti per cap slot / budget,
  copertura di idratazione (da `DualChannelComposition.to_record`).

Scorer: `locomo_reduced_deterministic_v1_not_official` (interno, non ufficiale).

---

## 6. Analisi

- **Primaria — bootstrap clusterizzato per conversazione** (N=5): delta appaiato
  `dual − rag` per conversazione (media su repliche e domande), CI 95% percentile
  ricampionando **le conversazioni**. Delta per singola conversazione riportato.
- **Secondario descrittivo — McNemar esatto** sugli esiti binari a livello di
  domanda (non clusterizzato).
- Sempre **N e intervalli**; flag `underpowered` (N=5 < 10 → dichiarato).
- Breakdown per categoria **solo diagnostico**: non decide né condiziona la policy.
- Guardrail prudenza e invariante “0 gold persi” verificati e riportati.

---

## 7. Strumentazione (candidati, idratazione, composizione)

Per ogni domanda si registra:
- **candidati base** (nodi `rag_only`: id, turn, position, retrieval_path);
- **candidati locator** (note passive: id, source_turn_ids, position,
  retrieval_path, nell'ordine registrato);
- **decisioni di idratazione** (per ogni source: added / duplicate /
  discarded_slot_cap / discarded_budget) e **composizione finale** (base +
  aggiunte, caratteri, slot) — da `dual_channel.compose_dual_channel().to_record()`;
- **punteggi/ranking**: **da rivedere**. `build_rag_context` di produzione **non
  espone i punteggi di similarità** nei suoi nodi (solo position/retrieval_path).
  Instrumentarli richiederebbe una **modifica di produzione**, che NON è fatta in
  questo protocollo: è un punto esplicito per la vostra revisione. In assenza, si
  registrano position e retrieval_path come proxy.

La composizione dual-channel è **solo benchmark**: non modifica la produzione.

---

## 8. Integrità e disciplina

- Riuso delle guardie dell'held-out: `manifest_sha256`, corpus SHA, git commit +
  worktree pulita, artefatto italiano sigillato, checkpoint/resume con
  rivalidazione, legame crittografico report↔manifest, cap di arresto tecnici.
- **Dev-set bruciato:** conv-30/43/47 non rientrano; le 5 conversazioni untouched
  sono la validazione pulita.
- **Freeze prima dei risultati:** policy, universo e metriche sono fissati qui;
  nessun risultato è osservabile prima della chiusura del manifest finale.
- N=5 conversazioni: validazione **sostanziale ma ancora un pilot clusterizzato**,
  non una stima definitiva su LoCoMo.

---

## 9. Cosa richiede la vostra revisione prima di procedere

1. **Green-light all'esecuzione** (il campione non è generato, la run non è
   avviata).
2. **Strumentazione dei punteggi di retrieval**: se la volete, serve una modifica
   di produzione a `build_rag_context` per esporre i punteggi dei candidati —
   decisione vostra; non l'ho fatta.
3. **Census vs campione stratificato** delle domande per le 5 conversazioni.
4. **Costruzione dell'arm `dual_channel` nel worker** (due retrieval: base
   raw-only + locator passivi; generazione sul contesto composto): specificata
   qui, da implementare/eseguire dopo il vostro via. Il **cuore congelato**
   (composizione + strumentazione) è già pronto e testato in
   `benchmarks/euri_memory/dual_channel.py`.

Nessuna esecuzione del campione, nessuna modifica alla produzione, nessun F1
prodotto in questo turno.

---

## 10. Hardening #2 pre-seed (correzioni applicate, protocollo v1)

Secondo giro di correzioni, sempre prima del seed e dei risultati. Nessun cambio
a Q/R/budget/bracci/metriche; nessuna modifica alla produzione.

1. **Base = intero contesto rag_only protetto** (non "top-5"): il compositore non
   tronca mai; `final_slots <= base_slots + 2` con `base_slots` qualunque.
2. Policy invariata: `dual-channel-q2r1-v1` (Q=2, R=1, max 2 aggiunte, budget 2.500
   solo sulle aggiunte).
3. **Locator congelati come nella simulazione dev**: retrieval normale su store
   raw+passive, poi i **primi due nodi con `source=passive`** nell'ordine
   registrato (`locators_from_nodes`). Nessuna nuova ricerca passive-only.
4. **Testo delle note mai nel prompt**: i `source_turn_ids` sono risolti nel
   corpus italiano e resi come **turni verbatim con speaker** (`build_turn_renderer`).
5. **Renderer deterministico**: `final_chars == len(final_context_text)`, inclusi
   intestazione, speaker, separatori e newline; il budget è calcolato sul rendering
   reale e **non modifica mai la base**.
6. **Precalcolo per domanda** — base su store raw-only, locator su store
   raw+passive, contesto dual composto. **A e B usano la stessa base byte-per-byte**;
   `base_sha256` registrato e verificato; una divergenza **arresta la coppia
   fail-closed** (`run_dual_channel_pair`).
7. **Controbilanciato solo l'ordine di generazione** delle risposte, non la
   definizione dei contesti.
8. **Census** di tutte le domande eleggibili delle 5 conversazioni, **incluse le
   avversariali**; esclusi solo item strutturalmente invalidi (evidence gold non
   nel corpus), con conteggio e motivo. Risultato del census:
   **989 eleggibili** (779 answerable + 210 avversariali), **1 esclusa** (conv-50,
   `evidence_gold_non_nel_corpus`).
9. **2 repliche** per conversazione; **N indipendente = 5**.
10. **Nessuna modifica a `core/rag_context.py`** per i punteggi. Si registrano
    position, retrieval_path, nodi raw, locator, source idratati, contesto finale,
    SHA, slot e caratteri.
11. Regressioni aggiunte (`test_dual_channel.py`): base con >5 nodi conservata;
    `final_slots <= base_slots + 2`; uguaglianza byte/SHA della base fra i bracci;
    budget sul rendering reale; assenza di testo sintetico; locator coerenti con la
    simulazione dev.
12. **Riproduzione dev col renderer definitivo**: invariata —
    **198 casi, 9 recuperi esclusivi, 0 gold persi, provenance 75,76%**. Il conteggio
    esatto del budget introduce 28 scarti-budget in più ma non tocca le coperture
    del gold. **Parametri non ritoccati.**

### Forecast del census (2 repliche)

10 coppie · **3.956 generazioni** (989 × 2 repliche × 2 bracci) · **~12.000–23.000**
chiamate LLM locali stimate · **~10–19 h** a 3 s/chiamata. Il census è costoso: la
scelta census-vs-campione resta un punto di revisione (§9.3).

Comandi (nessuna esecuzione qui):
```
./venv/bin/python -m benchmarks.euri_memory.cli dual-dry-run \
  --source benchmarks/euri_memory/data/locomo10.json --output <dir>/dual_dry_run.json
```
