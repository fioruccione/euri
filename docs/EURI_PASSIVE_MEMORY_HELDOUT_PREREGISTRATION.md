# Preregistrazione — Valutazione held-out della memoria passiva di Euri

**Esperimento:** `euri_passive_memory_heldout`
**Versione protocollo:** v1
**Stato:** preregistrato, non eseguito. Il seed reale sarà fornito da
Stefano/Codex **dopo** il commit di questo protocollo.
**Commit base (HEAD alla stesura):** `8b412dfcb63209569d267c033ef4005cda0cf240`
(branch di lavoro `agent/synchronize-euri-runtime`; implementazione su
`experiment/passive-memory-heldout`).

> Regola di versionamento: qualsiasi modifica a protocollo, selettore, runner,
> scorer o analisi **dopo** aver visto qualunque risultato produce una nuova
> versione dell'esperimento (`v2`, ...). Questo file resta immutato come v1.

---

## 1. Domanda e ipotesi

Domanda: *la memoria passiva migliora il richiamo rispetto a `rag_only`,
mantenendo l'astensione sulle domande non supportate, e con quale costo
computazionale e quale frammentazione della memoria?*

- **Ipotesi primaria (H1):** rispetto a `rag_only`, `passive_memory` aumenta
  l'**evidence recall** appaiato **senza** ridurre la **correttezza avversariale**
  (astensione sulle domande non supportate).
- **Ipotesi nulla (H0):** nessuna differenza appaiata nell'evidence recall **e**
  nessuna differenza nella correttezza avversariale.
- **Guardrail preregistrato (prudenza):** un calo significativo dell'astensione
  avversariale è dichiarato **fallimento** anche se il token F1 migliora. Il
  successo **non** è mai definito sul solo F1.

Direzione di ogni delta: `passive_memory − rag_only`.

---

## 2. Disegno

- Confronto **appaiato** a due bracci, stessa selezione, stessi modelli, stessa
  configurazione:
  - **A. `rag_only`** — ingestione dei turni grezzi + retrieval, nessuna
    formazione automatica di memoria.
  - **B. `passive_memory`** — RAG + percorso reale Passive learner → Buttafuori →
    audit provenienza → dedup → salvataggio.
- **Unità di ingestione:** conversazione **intera** (tutte le sessioni). Così
  l'evidence gold delle domande campionate è sempre coperta e non esiste una leva
  di selezione delle sessioni che possa favorire un braccio.
- **Unità statistica indipendente: la conversazione.** Con la config consigliata
  **N = 3**, non 9. Domande e repliche sono osservazioni **annidate** dentro la
  conversazione; l'analisi è clusterizzata per conversazione (vedi §6). Non si
  tratta domande o repliche come indipendenti (niente pseudoreplicazione).
- **Repliche appaiate:** misurano la variabilità run-to-run di Gemma. **Non**
  aumentano N.
- **Ordine dei bracci:** alternato e **registrato** per replica (parità iniziale
  derivata dal seed), per controllare effetti di ordine/riscaldamento del modello.
  Entrambi i bracci resettano il DB, quindi non c'è travaso di stato.

### Universo eleggibile
LoCoMo ha 10 conversazioni. **Escluse** perché usate in sviluppo (diagnosi/tuning,
non più held-out): **conv-26** e **conv-42** (incluse le loro selezioni versionate
e localizzazioni). L'insieme escluso è derivato automaticamente dai fixter di
selezione in `benchmarks/euri_memory/fixtures/` più una guardia esplicita
`{conv-26, conv-42}`. Universo eleggibile risultante (8): **conv-30, conv-41,
conv-43, conv-44, conv-47, conv-48, conv-49, conv-50**.

### Algoritmo di campionamento (deterministico dal seed)
1. `--seed` **obbligatorio, senza default**: senza seed il comando non parte e il
   campione non esiste.
2. RNG master `random.Random(seed)` sceglie `num_conversations` conversazioni
   dall'universo eleggibile.
3. Per ogni conversazione, un RNG indipendente `random.Random(f"{seed}:{sample_id}")`
   campiona, **stratificando per categoria** (single_hop, temporal, multi_hop,
   open_domain, adversarial), `min(per_category, disponibili)` domande per
   categoria. L'indipendenza dei sotto-RNG rende il risultato invariante
   all'ordine di iterazione.
4. Le domande con **evidence gold assente dal corpus** (difetto del rilascio
   LoCoMo, es. `D:11:26`) sono escluse dall'universo eleggibile e **contate** nel
   manifest (`excluded_missing_evidence`).
5. Il manifest immutabile è prodotto **prima** di qualunque modello, con:
   `manifest_sha256` (hash canonico), seed, budget e cap, git commit,
   `corpus.sha256`, universo eleggibile, insieme escluso, per conversazione
   `{session_ids, question_ids, category_histogram}` (solo ID e conteggi),
   ordini dei bracci e `answer_seed` per replica.
6. **Cecità:** il manifest non contiene mai testo di domande o risposte gold.
   Chi costruisce il test non ispeziona il contenuto del campione.

---

## 3. Livelli di budget (preregistrati)

Il costo è dominato dall'ingestione passiva su conversazioni intere
(∝ conversazioni × repliche), non dal numero di domande.

| budget | conversazioni | domande/categoria | repliche | cap chiamate LLM | cap tempo |
|---|---:|---:|---:|---:|---:|
| `smoke` | 3 | 1 | 1 | 6 000 | 2 h |
| **`validation` (consigliato)** | **3** | **6** | **3** | **40 000** | **8 h** |
| `extended` | 5 | 10 | 5 | 160 000 | 32 h |

**Configurazione consigliata = `validation`.** Motivazione: è il minimo dello
scopo (≥3 conversazioni distinte) con 3 repliche appaiate per stimare la varianza
di Gemma, e con ~6 domande/categoria offre copertura stratificata piena a un costo
di alcune ore. `smoke` serve solo al primo segnale/cablaggio; `extended` alza
diversità e repliche quando c'è tempo macchina.

---

## 4. Metriche

Costo e qualità **non** si aggregano in un punteggio unico: si mostra il fronte di
compromesso (qualità · prudenza · costo · frammentazione).

**Primarie**
- evidence hit / recall;
- correttezza / astensione sulle domande avversariali/non supportate;
- token F1 appaiato.

**Secondarie**
- exact match;
- accuratezza per categoria;
- copertura di `source_turn_ids` (frazione di memorie passive salvate con
  provenienza non vuota) e riparazioni/rifiuti di provenienza;
- numero di memorie candidate, validate, salvate, rifiutate, duplicate;
- chiamate LLM locali (per braccio e per fase);
- tempo totale e per fase;
- token generati (`eval_count`) e di prompt (`prompt_eval_count`), già misurati
  dal tracker senza modificare il runtime.

Scorer: `locomo_reduced_deterministic_v1_not_official` — deterministico, **non**
il judge LoCoMo ufficiale. È un confronto A/B interno, non un risultato LoCoMo
pubblicabile.

---

## 5. Cap, arresto e run incomplete

- **Cap preregistrati** per budget (tabella §3). La run si arresta **solo** per
  errore tecnico o superamento di un cap (chiamate LLM cumulate / tempo cumulato).
  L'arresto **non dipende mai** dall'andamento delle metriche.
- **Checkpoint & resume** per coppia `(conversazione, replica)`: un unico processo
  `live_worker` isolato esegue **entrambi** i bracci sulla stessa selezione. La
  coppia entra nell'analisi **solo se** il suo report contiene entrambi i bracci
  con scoring completo. Un fallimento lascia la coppia **incompleta** e
  ripristinabile: **non** viene sostituita con un altro caso.
- Il checkpoint registra progressi e costo cumulato; il resume salta le coppie
  già complete e valide.

---

## 6. Analisi statistica

- **Inferenza primaria — bootstrap clusterizzato per conversazione.** Per ogni
  metrica primaria si calcola il delta appaiato `(passive − rag)` **per
  conversazione** (media sulle repliche e sulle domande applicabili di quella
  conversazione), poi si ricampionano **le conversazioni** con reinserimento
  (10 000 resamples) per un CI percentile 95%. L'unità ricampionata è la
  conversazione: niente pseudoreplicazione.
- **Delta per singola conversazione sempre riportato**, così un dialogo molto
  favorevole non può nascondere gli altri.
- **Test secondario descrittivo — McNemar esatto** sugli esiti binari a livello di
  domanda (correttezza avversariale, exact match, evidence hit), su coppie
  discordanti, repliche mediate. È esplicitamente marcato come **non clusterizzato**
  (tratta le domande come indipendenti): non è l'inferenza primaria.
- Si riportano **sempre N e intervalli**, non solo medie.
- Flag `underpowered = True` se N conversazioni < 10 **o** un CI primario
  attraversa lo zero.
- Guardrail prudenza: se il CI del delta di astensione avversariale è interamente
  < 0, è segnalata una **regressione di prudenza** a prescindere dal token F1.

---

## 7. Cosa viene registrato

Nel manifest e nei report per-coppia: git commit, `OLLAMA_MODEL`, host Ollama,
embedder e sua versione, temperatura risposta (0), `answer_seed` per replica,
hash del system prompt di risposta, `corpus.sha256`, `manifest_sha256`, porta
Redis effimera, conteggi/tempi/token LLM, statistiche del percorso passivo.

**Nota metodologica dichiarata apertamente** (scelta simmetrica, non favorisce
alcun braccio): la generazione risposta è a `temperature=0`; per far sì che le
repliche catturino anche la varianza di generazione — non solo quella
dell'ingestione — il runner varia l'`answer_seed` **per replica**, ma con lo
**stesso** valore sui due bracci della stessa replica.

---

## 8. Limite interpretativo

**3 conversazioni × 3 repliche è una validazione held-out sostanziale ma resta un
pilot clusterizzato: non è una stima definitiva sull'intero LoCoMo.** Con N = 3
cluster il bootstrap è volutamente coarse e il test sarà quasi certamente
sotto-potenziato: il report lo deve dichiarare. `extended` e, in seguito, il
LoCoMo completo sono i passi verso una stima con potenza reale.

Questo lavoro consegna protocollo, implementazione, verifiche e limiti. **Non**
esprime un verdetto sull'efficacia della memoria passiva.

---

## 9. Comandi (flusso italiano a due stadi)

```bash
R=benchmarks/euri_memory/reports

# 1) Selezione (seed OBBLIGATORIO, fornito dopo il commit del protocollo)
./venv/bin/python -m benchmarks.euri_memory.cli heldout-select \
  --seed <S> --budget validation --output $R/sel_seed<S>.json

# 2) Traduzione automatica delle SOLE 3 conversazioni selezionate (locale, Gemma)
#    (forecast:   ... heldout-localize --selection-manifest $R/sel_seed<S>.json --dry-run --output $R/locfc.json)
./venv/bin/python -m benchmarks.euri_memory.cli heldout-localize \
  --selection-manifest $R/sel_seed<S>.json --output $R/it_seed<S>.json

# 3) Manifest FINALE derivato (lega selezione + protocollo + localization SHA)
./venv/bin/python -m benchmarks.euri_memory.cli heldout-finalize \
  --selection-manifest $R/sel_seed<S>.json --localization $R/it_seed<S>.json \
  --output $R/final_seed<S>.json

# 4) Dry-run: forecast + prova d'isolamento, nessun modello
./venv/bin/python -m benchmarks.euri_memory.cli heldout-run \
  --manifest $R/final_seed<S>.json --output-dir $R/run_seed<S> --dry-run

# 5) Run reale appaiata italiana (checkpoint/resume, cap di arresto)
./venv/bin/python -m benchmarks.euri_memory.cli heldout-run \
  --manifest $R/final_seed<S>.json --localization $R/it_seed<S>.json \
  --output-dir $R/run_seed<S>

# 6) Analisi clusterizzata per conversazione (--manifest OBBLIGATORIO)
./venv/bin/python -m benchmarks.euri_memory.cli heldout-analyze \
  --results-dir $R/run_seed<S> --manifest $R/final_seed<S>.json \
  --output $R/run_seed<S>/analysis.json
```

**Resume** (se la run non termina entro domattina): rilancia identico il passo 5.
Il checkpoint salta solo le coppie complete E rivalidate; se identità, percorso o
report divergono, si arresta chiuso invece di proseguire.

I budget `smoke` / `validation` / `extended` cambiano solo `--budget` al passo 1.

### Stime (dal forecast, ordine di grandezza; il seed reale può variare)

- **Traduzione delle 3 conversazioni selezionate (passo 2):** ~2.200 unità di
  traduzione (turni + domande + risposte), ≈ **35–75 min** a 1–2 s/chiamata,
  fino a ~2 h se il modello è più lento. Solo locale, nessun costo.
- **Validation 3×3 (passo 5):** 9 coppie, ≈ **5.000–12.000** chiamate LLM locali,
  ≈ **4–10 h** a 3 s/chiamata. Cap preregistrato: 40.000 chiamate / 8 h wall per
  coppia cumulato — se superato, nessuna nuova coppia parte (arresto tecnico).

---

## 10. Hardening di integrità pre-esecuzione (protocollo v1)

Audit indipendente (Codex) completato: metodologia approvata, tutti i test
passano, nessun held-out eseguito. Poiché **nessun risultato è stato visto e non
esiste ancora un seed reale**, le seguenti guardie sono documentate come
**hardening pre-esecuzione del protocollo v1** — non una nuova versione. Legano
rigidamente gli artefatti a valle al manifest immutabile, così due esperimenti
non possono mescolarsi accidentalmente prima della cecità. Non toccano
campionamento, budget, metriche né codice di produzione.

1. **Corpus:** al run l'SHA-256 del corpus usato deve coincidere con
   `manifest["corpus"]["sha256"]`; un `--source` diverso è ammesso solo a parità
   di hash.
2. **Commit e worktree:** prima del run reale HEAD deve combaciare col commit del
   manifest e i file **tracciati** devono essere puliti (i report non tracciati
   sono ammessi).
3. **Output directory:** se contiene già `manifest.json`, il suo `manifest_sha256`
   deve coincidere, altrimenti fail-closed senza sovrascrivere. Il checkpoint
   registra `manifest_sha256`, `corpus_sha256` e git commit; un resume con valori
   diversi è rifiutato.
4. **Selezioni:** `_write_selection` costruisce il payload canonico atteso e non
   riusa mai alla cieca un file preesistente: identico → riuso, diverso →
   fail-closed.
5. **Report per coppia:** una coppia è completa solo se il report combacia su
   `sample_id`, `run_label`, `answer_seed`, `branch_order`, `source_sha256`, git
   commit + worktree tracciata pulita, `question_ids` esatti, hash della selezione
   e presenza di entrambi i bracci con scoring. Solo i report validati entrano nel
   checkpoint.
6. **Analisi:** con `--manifest`, `analyze` deriva le coppie attese, accetta solo
   report legati a quel manifest, rifiuta estranei e duplicati, elenca le coppie
   mancanti/incomplete e dichiara esplicitamente una run **parziale** (non la
   confonde con la validation completa).
7. **Tempo e cap:** il checkpoint accumula il **wall-clock reale** per coppia
   (avvio Redis, caricamento embedder, teardown inclusi). Il cap LLM/tempo è
   applicato a **granularità di coppia**: una coppia in corso può oltrepassarlo,
   ma nessuna nuova coppia parte dopo il superamento.
8. **Regressioni:** test che dimostrano il rifiuto di corpus con hash diverso,
   manifest diverso nella stessa output-dir, identità di checkpoint divergente,
   selezione preesistente alterata, report con seed/domande/ordine/commit/corpus
   errati, e report estraneo/duplicato in analisi.

---

## 11. Lingua primaria italiana — variante a pipeline congelata

Euri, i suoi prompt, le regex e la gestione temporale operano **principalmente in
italiano**: l'held-out primario deve quindi essere italiano. Per non dover
tradurre ~4.800 turni prima del seed, si adotta questa **variante preregistrata**
(equivalente per validità, esplicitamente documentata):

1. Il **protocollo di traduzione** è **congelato e committato prima del seed**:
   deterministico, locale (Gemma via Ollama, `temperature=0`, `seed` fisso),
   `prompt_sha256` registrato. Nessun servizio a pagamento.
2. **Dopo** il seed si traducono automaticamente **solo le 3 conversazioni
   selezionate**, ma integralmente (tutti i turni + le domande campionate).
3. **Nessuna ispezione o correzione manuale** del contenuto selezionato.
4. **Controlli automatici soltanto:** completezza di ID turni/domande, evidence
   (preservata perché resta nel corpus), date e riferimenti temporali relativi,
   numeri (anni/quantità ≥4 cifre come invariante duro), nomi, answerability e
   presenza delle risposte avversariali.
5. L'artefatto italiano è **sigillato con SHA-256**; un **manifest finale
   derivato** lega `selection_manifest_sha256` + protocollo di traduzione +
   `localization_sha256`, con il proprio `manifest_sha256`.
6. **Entrambi i bracci ricevono esattamente lo stesso artefatto italiano** (la
   stessa slice per conversazione): la traduzione è cieca, congelata e identica,
   quindi non introduce alcun vantaggio artificiale per la memoria passiva.
7. **Nessun risultato è osservabile prima della chiusura del manifest finale**:
   `heldout-run` reale rifiuta un manifest non `stage=final`/`language=it` e un
   artefatto il cui SHA non combacia.

Il manifest resta **cieco**: né il selection manifest né il finale contengono
testo di domande o gold; l'artefatto italiano (che li contiene, tradotti
automaticamente) è un file separato, sigillato, mai ispezionato manualmente.

Alternativa più stringente (se un domani c'è tempo macchina): localizzare
**l'intero universo eleggibile** (8 conversazioni) prima del seed. La pipeline la
supporta concettualmente; la variante a 3 conversazioni è quella scelta per il
lancio, per costo.

---

## 12. Hardening di integrità #2 (pre-esecuzione, protocollo v1)

Secondo giro d'audit indipendente, sempre prima del seed e dei risultati. Legami
crittografici rafforzati; nessun cambio a campionamento, budget, bracci, metriche
o codice di produzione.

- **Resume fail-closed:** un checkpoint esistente deve avere identità completa
  (`manifest/corpus/git`) e coincidente; ogni coppia `completed` è **rivalidata**
  con `validate_pair_report` prima di poterla saltare; report assente, corrotto,
  con percorso divergente o coppia estranea → rifiuto, non skip.
- **Manifest nell'output-dir:** il manifest preesistente deve superare
  `verify_manifest` **e** avere identità canonica identica; contenuto o digest
  divergenti (anche con `manifest_sha256` lasciato stantìo) → chiuso.
- **Legame crittografico report ↔ manifest:** il report registra
  `manifest_sha256`, `selection_manifest_sha256`, `localization_sha256` e lingua;
  `validate_pair_report` ne richiede l'uguaglianza esatta, oltre a
  `models.answer_seed`, `question_ids` come **sequenza** esatta, profili
  **esattamente** `rag_only`+`passive_memory` (niente duplicati/extra) e item di
  scoring che coprono esattamente le domande attese.
- **Analisi:** `--manifest` è **obbligatorio** in `heldout-analyze`; il percorso
  permissivo resta solo come API esplicitamente non-held-out.
- **Git fail-closed:** `assert_head_matches_manifest` fallisce anche quando il
  commit atteso o HEAD non sono disponibili, non solo quando differiscono.
- **Regressioni:** aggiunte per checkpoint senza identity, report manomesso dopo
  il checkpoint, coppia `completed` estranea, report mancante, manifest manomesso
  con SHA stantìo, e ogni alterazione del legame report↔manifest.

Siamo ancora **prima del seed e dei risultati**: resta protocollo v1.
