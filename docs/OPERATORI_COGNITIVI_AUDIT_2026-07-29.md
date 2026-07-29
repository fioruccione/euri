# Tabella degli operatori cognitivi di Euri — audit descrittivo

**Data:** 29/07/2026 · **Natura:** solo lettura. Nessuna modifica al codice, nessuna
scrittura Redis, nessuna esecuzione LLM, nessuna decisione keep/remove.
**Stato:** audit a sei occhi completato contro codice, stato Redis read-only e
prima mappa del prior art; restano le lacune dichiarate in §6.

## Come leggere questo documento

Ogni operatore separa quattro livelli, e la separazione è il punto:

| Sigla | Significato |
|---|---|
| **[C]** | Ciò che il **codice** fa certamente — verificato leggendo le righe indicate |
| **[I]** | Ciò che il progetto **intende** ottenere — dichiarato in docstring/CHANGELOG |
| **[M]** | Ciò che è già stato **misurato** — con l'esito, anche negativo |
| **[H]** | Ciò che resta **ipotesi** — non misurato |

Dove non ho verificato riga per riga scrivo **`da verificare`**. Non ho colmato
nessun buco con una supposizione plausibile.

### Avvertenza sul prior art

Conosco e posso mappare con ragionevole fiducia: **Generative Agents**,
**MemoryBank**, **Zep/Graphiti**, **Truth Maintenance Systems**.

La prima stesura non mappava **RMM, MemSifter, SF-AMS e MemLineage** perché
posteriori o non verificati rispetto alla conoscenza dell'autore. L'audit Codex
successivo li ha controllati sulle fonti primarie e li ha inseriti nella matrice
comparativa in §7. Le singole schede rinviano a quella matrice: una somiglianza
funzionale non viene trattata automaticamente come equivalenza o anteriorità.

---

## 1. Quadro d'insieme

### 1.1 Fasi del Dream Engine

`core/dream_engine.py:407` — `_run_due_idle_cycles` esegue solo i sotto-cicli scaduti,
e **solo mentre Euri è idle**.

| Fase | Intervallo | Operatori invocati | Righe |
|---|---|---|---|
| **creative** | 90 min | 2b generazione sogno → 2c valutazione insight | `465-477` |
| **light** | 20 min | 2c valutazione, 2g audit correzioni, 2i ipotesi trasversali, propagazione provenienza | `478-485` |
| **maintenance** | 24 h | audit verbatim, utility shadow, 2f, plausibility (off), 2h, cleanup insight, cleanup memorie stantie, 2d pruning, 2e consolidamento (≥24h), propagazione provenienza | `486-511` |

**[C]** Ordine di esecuzione nel ciclo: creative → light → maintenance. Ogni fase è
racchiusa in `try/except` che logga e prosegue: **il fallimento di un operatore non
ferma gli altri**, e nessuno stato di fallimento viene registrato sul documento.

**[C]** `_run_dream_cycle` (`513-577`) è un percorso **separato** usato da
`force_full_cycle.py`: esegue gli stessi operatori in un ordine proprio e numerato.
Due orchestratori distinti sullo stesso insieme di operatori.

> **Finding 1 — doppio orchestratore.** L'ordine forzato (`513`) e l'ordine idle
> (`407`) non coincidono. Differisce anche la **molteplicità**: quando creative e
> light sono entrambe dovute, il runtime invoca 2c due volte (una dentro creative e
> una dentro light), mentre il ciclo forzato lo invoca una volta sola. Ogni misura
> ottenuta con `force_full_cycle.py` descrive quindi una sequenza e un dosaggio che
> in esercizio non avvengono. Rilevante per l'ablazione: l'arm "forzato" non è
> l'arm "reale".

### 1.2 Matrice sintetica

| Op | Nome | Fase | Popolazione letta | Mutazione | Distruttivo |
|---|---|---|---|---|---|
| 2a | Reflection | on-idle (fuori Dream) | memorie di sessione + correlate | crea nodo `reflection` | no |
| 2b | Dream generation | creative | 2 memorie random di 2 domini | crea `dream` + `insight` candidate | no |
| 2c | Insight evaluation | creative + light | `@status:{candidate}` | candidate→hypothesis→promoted | no |
| 2d | Death-row gate | maintenance | `euri:memory:*` in scadenza ≤7g | estende TTL **o elimina** | **sì** |
| 2e | Consolidation | maintenance (≥24h) | cluster stesso dominio, recall ≥3 | crea nodo `loop2e` + marca foglie | no |
| 2f | Contradiction | maintenance | `euri:memory:*` + KNN dominio | `superseded_by` | no (soft) |
| 2g | Correction audit | light | correzioni pending | `requires_verification`, verdetto | no |
| 2h | Self-observation | maintenance | coppie superseded dal 2f | reflection narrativa **o ripristino** | no |
| 2i | Cross-episode | light | episodi ripetuti | crea insight `hypothesis` | no |
| — | Provenance propagation | light + maintenance | nodi derivati | `provenance_stale`, `requires_verification` | no |
| — | Plausibility gate | maintenance (**OFF**) | — | `plausibility_flag` | no |
| — | Cleanup insight | maintenance | insight per età/richiamo | demozione **o cancellazione** | **sì** |
| — | Cleanup memorie stantie | maintenance | sorgenti effimere mai richiamate | **cancellazione** | **sì** |
| — | Cleanup post-2a | idle (**NO-OP**) | finestra di scadenza impossibile | nessuna | no |

---

## 2. Operatori

### Loop 2a — Reflection

- **File/righe [C]:** `core/brain.py:1149` (`generate_reflection`), policy in
  `core/reflection_policy.py`.
- **Trigger e frequenza [C]:** invocato fuori dal Dream Engine da
  `voice_daemon.py:_consolidation_loop`. Poll ogni 60 s; richiede almeno 5 min di
  idle, almeno 30 min dall'ultimo tentativo, almeno 3 memorie selezionate e nessun
  input/audio lock. Prima della pubblicazione verifica che lo snapshot di attività
  non sia cambiato: un nuovo turno annulla il commit della reflection.
- **Popolazione e scope [C]:** memorie della sessione corrente più memorie correlate
  dall'archivio. La sessione è delimitata da un **checkpoint durevole**
  (`LOOP2A_CHECKPOINT_KEY`) con gap di 30 minuti, non da una finestra temporale
  globale. Escluse le sorgenti `campus`, `web`, `reflection`
  (`LOOP2A_EXCLUDE_SOURCES`).
- **Input [C]:** testo formattato `[source] contenuto[:120]` — **il contenuto è
  troncato a 120 caratteri** per memoria.
- **Giudizio [C]:** una chiamata LLM (`OLLAMA_MODEL`, temp 0.4, `num_predict` 3000,
  `think=True`). Contratto: restituisce `NO_COHERENT_PATTERN` se non trova pattern.
- **Mutazioni [C]:** nessuna sul nodo di input. Il chiamante (`voice_daemon.py:3488`)
  salva il testo come memoria `source=reflection`, `category=riflessione`, con
  scadenza esplicita a **7 giorni**.
- **Provenienza [C]:** **conservata, e meglio di quanto supponessi.** Il salvataggio
  registra `source_memory_ids` (via `reflection_parent_ids(session_mems, related)`) e
  `session_memory_ids`. La catena di provenienza **non ha un buco all'origine**.
- **Transizione epistemica [C]:** nessuna sui nodi esistenti. Crea un nodo nuovo che
  **nasce già sospetto**: `requires_verification=True` e
  `epistemic_status="internal_reflection"`. Una sintesi interna non viene mai
  presentata come fatto acquisito. È una delle scelte più coerenti del sistema.
- **Nota sul TTL [C]:** `_TTL_BY_SOURCE` assegna 90 giorni alle reflection, ma qui il
  chiamante passa un `expires_at` esplicito a 7 giorni. Le due politiche coesistono e
  la seconda vince. `da verificare`: se sia intenzionale.
- **Consumatori [C]:** RAG (le reflection entrano nel contesto), 2b come seme, 2c via
  convergenza, `_active_domains` (le reflection contano come sorgente operativa).
- **Effetto atteso [I]:** dare a Euri una sintesi di sessione riutilizzabile invece di
  costringere il RAG a ricomporre ogni volta i frammenti.
- **Misurato [M]:** dedup latest-wins su doppioni **diretti** (commit `4bcb6cc`);
  verificata la mancata copertura delle **catene**. Nessuna misura dell'effetto sul
  richiamo o sulla qualità della risposta.
- **Metrica falsificante [H]:** con 2a spento, il RAG risponde peggio alle domande che
  richiedono più memorie della stessa sessione? Se no, 2a è ridondante rispetto al
  retrieval.
- **Dipendenze [C]:** produce il materiale per 2b/2c/2e. **A monte di quasi tutto.**
- **Failure mode [C]:** il troncamento a 120 caratteri può tagliare la parte
  informativa; la sintesi compete poi nel RAG con la fonte da cui deriva.
- **Effetto osservatore [C]:** alto. Ogni test conversazionale genera memorie di
  sessione che 2a sintetizza, e la sintesi entra nel substrato.
- **Prior art:** Generative Agents §reflection (albero di riflessioni da memory
  stream). *Differenza verificabile:* la selezione delle fonti parte da un checkpoint
  di sessione durevole invece che da una finestra di importanza.
  Confronti aggiuntivi verificati in §7.

### Loop 2b — Generazione del sogno

- **File/righe [C]:** `_generate_dream` `766-851`, `_run_single_dream_generation`
  `862-1057`.
- **Trigger [C]:** fase creative, 90 min, solo se esistono ≥2 domini.
- **Popolazione [C]:** una memoria casuale per ciascuno di due domini distinti, via
  `_get_random_memory_from_domain` (`679`, query con `@memory_scope:{personal}` e
  filtro sorgenti ammesse).
- **Mutazioni [C]:** crea `euri:dream:{id}` con **TTL 7 giorni** e
  `euri:insight:{id}` con `status=candidate`.
- **Provenienza [C]:** conservata — il dream document registra
  `memory_a_id`/`memory_b_id`; l'insight registra le due premesse in
  `source_memory_ids`.
- **Transizione [C]:** nessuna sui semi. Nasce un nodo `candidate`.
- **Consumatori [C]:** 2c.
- **Effetto atteso [I]:** produrre connessioni cross-dominio che il retrieval non
  troverebbe perché i due domini non co-occorrono mai in una query.
- **Misurato [M]:** **fedeltà di premessa** attiva e strumentata
  (`_ensure_premise_fidelity`, es. `1.0 (SI/SI)`); **qualità del ponte** classificata
  (`hypothesis (0.5)`). Esiste il disegno appaiato `dream_trace` V2 (stesso seme con e
  senza residuo), con almeno un caso scartato per assenza di righe conformi.
- **Metrica falsificante [H]:** gli insight nati da 2b vengono mai *usati* (lineage
  `used_in_response`) più di un baseline di memorie casuali? Se no, 2b produce solo
  materiale per 2c.
- **Dipendenze [C]:** consuma memorie di qualsiasi origine, comprese le reflection di
  2a e i consolidati di 2e — **quindi può sognare su sintesi di sintesi**.
- **Failure mode [C]:** costo. Log reale: generazione 112 s, di cui 36,6 s di trace.
- **Effetto osservatore [C]:** medio-alto — gli insight entrano nel RAG e possono
  essere richiamati, il che li protegge dalla demozione.
- **Prior art:** Generative Agents (reflection cross-memoria). *Differenza:* la scelta
  dei semi è esplicitamente cross-dominio e casuale, non guidata dalla salienza.
  Confronti aggiuntivi verificati in §7.

### Loop 2c — Valutazione e promozione degli insight

- **File/righe [C]:** `_evaluate_insights` `1881-2287` (**406 righe, il più grande**).
- **Trigger [C]:** fase creative *e* fase light → gira sia ogni 90 min sia ogni 20 min.
- **Popolazione [C]:** `Query("@status:{candidate}")`, paging 500.
- **Query [C]:** per ogni candidato, KNN 4 su `idx:insights` fra candidati;
  `score` è **distanza** (shortlist sotto `CONVERGENCE_VECTOR_SHORTLIST_MAX_DISTANCE`
  = 0.40).
- **Giudizio [C]:** ogni coppia in shortlist passa da un judge LLM con cache
  (`_cached_same_insight_judgement`), con budget per ciclo
  (`CONVERGENCE_JUDGE_BUDGET` = 6). Budget separati per fedeltà premesse (5) e
  validità del ponte (3).
- **Mutazioni [C]:** `status` → `hypothesis` (con `hypothesis_at`) oppure `promoted`
  (con `promoted_at`), `convergence_count`, `requires_verification`; cancellazione di
  chiavi sentinella.
- **Soglia [C]:** promozione se `convergences >= DREAM_INSIGHT_MIN_CONVERGENCES` = **3**
  (alzata da 2 per ridurre le promozioni facili).
- **Transizione epistemica [C]:** `candidate → hypothesis → promoted`. È la
  transizione più esplicita del sistema.
- **Consumatori [C]:** RAG (gli insight promossi entrano nel contesto), cleanup
  insight (demozione), Initiative.
- **Effetto atteso [I]:** un'intuizione diventa conoscenza solo se converge con altre
  intuizioni indipendenti.
- **Misurato [M]:** **la distribuzione dei claim è piatta — 1 su 146 sopra soglia**;
  promozioni ~6/giorno costanti in idle. Il timbro anisotropo è **confermato** su
  trace live 13/07. In produzione, 306 giudizi su 309 arrivano dalla cache; le 3
  chiamate reali costano 186,7 s.
- **Metrica falsificante [H]:** se la convergenza a livello di claim è piatta, la
  soglia 3 sta selezionando *qualcosa*? Falsificazione: randomizzare l'assegnazione
  della convergenza e verificare se il tasso di promozione cambia. Se non cambia, la
  soglia non discrimina.
- **Dipendenze [C]:** **fortemente accoppiato** con cleanup insight (demozione) e con
  2b (produce i candidati). Girare in due fasi diverse rende la frequenza effettiva
  variabile.
- **Failure mode [C]:** la cache del judge (306/309) significa che il comportamento
  osservato è in gran parte **storia congelata**, non giudizio corrente.
- **Effetto osservatore [C]:** alto.
- **Prior art:** MemoryBank (forza della memoria), Generative Agents (importanza).
  *Differenza verificabile:* la promozione richiede convergenza fra nodi generati
  indipendentemente, non un punteggio assegnato alla scrittura.
  Confronti aggiuntivi verificati in §7.

### Loop 2d — Death-row gate (pruning)

- **File/righe [C]:** `_pruning_pass` `3642-3716`.
- **Popolazione [C]:** `scan_iter("euri:memory:*")` — **scansione completa**, non
  indice. Filtra su `expires_at` compreso fra adesso e +7 giorni, e su sorgenti
  presenti in `_TTL_BY_SOURCE` (`core/memory_manager.py:37`):

  ```
  passive 90g · reflection 90g · conversation 90g · episode 7g · web 60g
  user, teach, obsidian_vault → NESSUN TTL
  ```

  **Conseguenza [C]:** ciò che Stefano ha detto o insegnato esplicitamente è
  **fuori dalle politiche automatiche di scadenza e non passa dal death-row**.
  Può ancora essere superseded o rimosso manualmente: chiamarlo «immortale»
  sarebbe più forte di quanto garantisca il codice. 2d giudica soltanto materiale
  ambientale e derivato. È comunque un invariante forte: la mortalità automatica
  è riservata a ciò che Euri ha raccolto, non a ciò che le è stato dato.
- **Giudizio [C]:** due rami. `recalled_count >= MEMORY_KEEP_IF_RECALLED` (=3) →
  estende il TTL **senza chiamare l'LLM**. Ogni valore sotto soglia — quindi
  `0`, `1` o `2`, non soltanto zero — passa al giudice LLM KEEP/DROP. La docstring
  parla ancora di `== 0`, ma il controllo implementato è `< 3`. Errore LLM →
  **conserva** (fail-safe verso la conservazione).
- **Mutazioni [C]:** `expires_at` + `expireat` (TTL Redis = verità, `expires_at` =
  mirror di audit), oppure **`delete`**.
- **Provenienza [C]:** **persa** sul ramo DROP. Nessun tombstone: il nodo sparisce e i
  derivati che lo citavano restano orfani (li intercetta la propagazione di
  provenienza, ma dopo).
- **Consumatori [C]:** tutto il retrieval.
- **Misurato [M]:** `recalled_count` **monotòno satura con l'età** — risolto 19/06
  gating per recency a 30 giorni. Residuo aperto sul `recalled_count` degli insight.
- **Metrica falsificante [H]:** le memorie cancellate dal ramo DROP vengono mai
  richieste dopo? Serve un tombstone per poterlo misurare — **oggi non è misurabile**.
- **Failure mode [C]:** scan completa su 1509 chiavi a ogni manutenzione; il verdetto
  LLM su memoria mai richiamata è un giudizio senza evidenza d'uso.
- **Prior art:** MemoryBank (decadimento à la Ebbinghaus). *Differenza:* il decadimento
  è governato dall'**uso** (`recalled_count`), non dal solo tempo.
  Confronti aggiuntivi verificati in §7.

### Loop 2e — Consolidamento

- **File/righe [C]:** `_consolidation_pass` `3801-4070`.
- **Trigger [C]:** maintenance, ma **non più di una volta ogni 24 h** (contatore
  separato `_consolidation_last_run`).
- **Popolazione [C]:** memorie dello stesso dominio con `recalled_count >= 3`
  (`MIN_RECALLED`), cluster minimo 3 (`MIN_CLUSTER`), massimo 3 consolidamenti per
  ciclo (`MAX_PER_CYCLE`). Gate con massimo 30 tentativi e 3 fallimenti di parsing.
- **Mutazioni [C]:** crea una memoria nuova con `source="loop2e"`; marca ogni foglia
  con `consolidated_into = mid`. **Le foglie restano nel retrieval** (opzione A).
- **Provenienza [C]:** **conservata in entrambe le direzioni** — `consolidated_from`
  sul nodo nuovo, `consolidated_into` sulle foglie. È il punto più solido della catena.
- **Transizione [C]:** nessuna cancellazione. Additiva.
- **Misurato [M]:** l'opzione A (foglie conservate) è stata scelta rispetto alla B
  (supersede delle foglie), parcheggiata. Gating per recency introdotto dopo il
  finding sulla saturazione di `recalled_count`.
- **Metrica falsificante [H]:** il nodo consolidato viene recuperato *al posto* delle
  foglie, o *in aggiunta*? Se in aggiunta, 2e aumenta la ridondanza del contesto
  invece di ridurla — misurabile sulla lineage.
- **Dipendenze [C]:** dipende da `recalled_count` (quindi dal RAG), è **accoppiato con
  2f** (entrambi ragionano su memorie dello stesso dominio) e con la propagazione di
  provenienza, che gira apposta *dopo* nello stesso ciclo.
- **Prior art:** Generative Agents (riflessione gerarchica), Zep/Graphiti
  (comunità/summary). *Differenza:* le foglie restano recuperabili, quindi il
  consolidamento non è una compressione con perdita.
  Confronti aggiuntivi verificati in §7.

### Loop 2f — Risoluzione delle contraddizioni

- **File/righe [C]:** `_contradiction_resolution_pass` `2728-2913`.
- **Popolazione [C]:** `scan_iter("euri:memory:*")` come semi; per ogni seme, KNN 6
  nello stesso dominio con `@memory_scope:{personal}`. Massimo 15 coppie per ciclo.
- **Soglia [C]:** `MIN_CONFLICT_SCORE = 0.28` (distanza) → similarità > 0.72.
- **Giudizio [C]:** `_llm_classify_pair` classifica la relazione fra i due nodi.
- **Guardia [C]:** se il perdente ha `recalled_count >= LOOP2F_RECALL_GUARD` (=5),
  **si tengono entrambi** (paraurti N3).
- **Mutazioni [C]:** `superseded_by` sul perdente. **Nessuna cancellazione.**
- **Provenienza [C]:** conservata e *arricchita*: l'arco è la traccia della revisione.
- **Transizione epistemica [C]:** questa è la transizione TMS per eccellenza — un
  fatto non diventa falso, diventa *superato da*.
- **Consumatori [C]:** retrieval (onora `superseded_by`), **2h** (racconta gli archi),
  propagazione di provenienza.
- **Misurato [M]:** il retrieval onora `superseded_by` — **verificato 04/07** sul nodo
  sentinella `01d1b73d`. Il paraurti N3 è chiuso e mergeato. Le correzioni **non
  ripuliscono i nodi vecchi**, che è comportamento voluto.
- **Metrica falsificante [H]:** quante supersessioni sono corrette? Serve ground truth
  su un campione — **non ancora fatto**.
- **Dipendenze [C]:** **2h dipende interamente da 2f** e può annullarne l'arco
  (ripristino su verdetto RELATED/DIFFERENT). La dipendenza è asimmetrica: un arm
  `2f senza 2h` è artificiale ma utile per misurare il contributo incrementale della
  rete di sicurezza; `2h senza 2f` non ha invece nuovi archi su cui operare.
- **Failure mode [C]:** soglia 0.28 fissa su uno spazio anisotropo; scan completa.
- **Prior art:** **Truth Maintenance Systems** (Doyle 1979), belief revision AGM,
  Zep/Graphiti (edge invalidation con `invalid_at`). *Differenza verificabile:* la
  supersessione è proposta da un giudizio LLM su coppie vicine, non da una regola di
  integrità dichiarata; ed è **reversibile** da 2h.
  Confronti aggiuntivi verificati in §7.

### Loop 2g — Audit delle correzioni

- **File/righe [C]:** `_audit_corrections_pass` `3108-3296`.
- **Popolazione [C]:** correzioni pending, massimo 10 per ciclo.
- **Giudizio [C]:** `_llm_classify_correction` produce un verdetto.
- **Mutazioni [C]:** `audit_flag` (init con `nx=True`), `requires_verification=True`
  sul bersaglio, `status`, `verdict`, `proposed_verdict`, `analyzed_at`; può
  **salvare una nuova memoria** via `save_memory`. Emette sul pulse.
- **Provenienza [C]:** parziale. Sul ramo `bad_memory` la correzione resta nel
  documento-segnale e il bersaglio viene marcato senza essere riscritto. Sul ramo
  `bad_reasoning`, però, la lezione nuova è salvata come `source=reaction` senza un
  riferimento strutturato al documento di correzione d'origine: qui la catena ha
  un buco.
- **Transizione epistemica [C]:** porta un nodo in `requires_verification` — è
  l'operatore che rende esplicito il dubbio.
- **Misurato [M]:** **BUG aperto (26/06)**: una contro-domanda dell'utente viene
  consumata come *risposta* al verdetto, producendo `requires_verification` indebito.
  Manca il concetto di "turno-non-risposta".
- **Metrica falsificante [H]:** frazione di `requires_verification` indebiti su un
  campione etichettato.
- **Dipendenze [C]:** alimenta la propagazione di provenienza (che propaga
  `requires_verification` ai derivati) — quindi **un falso positivo qui si diffonde**.
- **Prior art:** TMS (giustificazioni ritirate). Confronti aggiuntivi verificati
  in §7.

### Loop 2h — Self-observation

- **File/righe [C]:** `core/self_observation.py` (classe `SelfObservation`),
  invocato da `dream_engine.py:359` e `500`.
- **Popolazione [C]:** coppie superseded prodotte da 2f, massimo 10 per ciclo. Le
  reflection già generate da 2h **non possono** alimentare nuove self-observation.
- **Giudizio [C]:** classificazione LLM a contratto JSON domain-agnostic con quattro
  esiti: `SAME` → reflection di evoluzione; `RELATED` → **ripristina** le due entità e
  dichiara la somiglianza sul pulse; `DIFFERENT` → **ripristina** senza inventare
  ponti; `UNKNOWN` → fail-closed, nessuna modifica, ritenta.
- **Mutazioni [C]:** crea una reflection narrativa **oppure annulla l'arco di 2f**.
- **Transizione epistemica [C]:** è l'unico operatore che può **retrocedere** una
  transizione altrui.
- **Effetto atteso [I]:** rendere esplicita la traiettoria del pensiero invece di
  lasciarla nascosta nel soft-delete.
- **Misurato [M]:** nessuna misura causale dell'effetto. Snapshot Redis read-only
  del 29/07: 234 elementi in `euri:loop2h:narrated`, 3 in
  `euri:loop2h:non_evolution`; i contatori non sono confrontabili storicamente
  perché il secondo esiste solo dall'hardening del 26/07.
- **Metrica falsificante [H]:** il tasso di ripristino. Se è alto, 2h sta soprattutto
  **correggendo errori di 2f**, e allora il suo valore è come rete di sicurezza, non
  come narrazione. Sarebbe una riclassificazione importante.
- **Dipendenze [C]:** totale su 2f. Prima misura ecologica in coppia; poi confronto
  `2f` contro `2f+2h` per isolarne il contributo.
- **Drift documentale [C]:** il commento di `_run_dream_cycle` descrive ancora 2h
  come “additivo” e incapace di modificare 2f. È falso rispetto al codice corrente,
  che su RELATED/DIFFERENT può ripristinare l'arco.
- **Prior art:** TMS (retrazione), Generative Agents (riflessione). *Differenza:* la
  narrazione in prima persona come artefatto di memoria, con divieto esplicito di
  auto-alimentazione.
  Confronti aggiuntivi verificati in §7.

### Loop 2i — Ipotesi trasversali

- **File/righe [C]:** `_cross_episode_hypothesis_pass` `2422-2525`.
- **Popolazione [C]:** casi cross-episodio raccolti da `_collect_cross_episode_cases`
  (`2314`, query con `@memory_scope:{personal}`), minimo `CROSS_EPISODE_MIN_CASES` = 2.
- **Mutazioni [C]:** crea un insight con `cognitive_trace_id`; sentinella di episodi
  già visti con TTL 180 giorni.
- **Transizione [C]:** nasce un nodo in stato **ipotesi**, cioè dichiaratamente non
  fatto.
- **Effetto atteso [I]:** generare **domande, non fatti**.
- **Misurato [M]:** nulla.
- **Metrica falsificante [H]:** le ipotesi vengono mai confermate o smentite
  dall'utente? Senza un ciclo di chiusura, 2i produce nodi che nessuno risolve.
- **Prior art:** Generative Agents (domande generate per la riflessione).
  Confronti aggiuntivi verificati in §7.

### Propagazione di provenienza

- **File/righe [C]:** `_provenance_propagation_pass` `579-637`. Gira una volta nella
  fase light, se dovuta, e una volta in coda alla maintenance. Nella pratica una
  maintenance dopo 24 ore rende normalmente dovuta anche light, ma non è una
  garanzia della singola funzione `_maintenance_cycle`.
- **Mutazioni [C]:** `provenance_stale`, `consolidation_risk`, `rv_by_provenance`,
  `requires_verification` — impostabili **sia a True sia a False** (è reversibile).
- **Transizione epistemica [C]:** un nodo derivato la cui fondamenta è caduta diventa
  sospetto e viene declassato nel ranking, **senza essere cancellato**.
- **Effetto atteso [I]:** invariante A della primitiva cognitiva.
- **Misurato [M]:** nessuna misura d'effetto.
- **Metrica falsificante [H]:** i nodi marcati `provenance_stale` sono
  effettivamente più spesso sbagliati di quelli non marcati? Verificabile su
  campione etichettato.
- **Dipendenze [C]:** posizionata **dopo** 2e e 2f di proposito, per valutare nello
  stesso ciclo le supersessioni e i consolidamenti appena fatti. Questo è
  accoppiamento d'ordine: eseguirla prima cambierebbe l'esito.
- **Prior art:** TMS (propagazione della non-credenza lungo le giustificazioni) —
  è la corrispondenza più stretta di tutto il sistema. Confronti aggiuntivi
  verificati in §7.

### Plausibility gate — ARCHIVIATO

- **File/righe [C]:** `_plausibility_gate_pass` `3410-3506`. Kill switch
  `PLAUSIBILITY_GATE_ENABLED`, **spento**; il codice resta nel repo.
- **Misurato [M]:** **negative result esplicito** — 1 vero positivo contro 3 falsi
  positivi su gemme di dominio vere, anche con il contesto operativo attivo.
- **Lezione registrata [M]:** il contesto inquadra ma non sopprime il ragionamento; il
  dominio non è iniettabile via prompt.
- **Nota per il paper:** è il caso più pulito di meccanismo *rimosso su evidenza*, e va
  raccontato. Un sistema che ha smontato un proprio loop misurandolo è più credibile di
  uno che li ha solo accumulati.

### Cleanup insight

- **File/righe [C]:** `_cleanup_expired_insights` `3508-3595`.
- **Popolazione [C]:** `@status:{promoted} @recalled_count:[0 0]` più vecchi di
  `INSIGHT_DEMOTE_DAYS` (14 g) → demozione; `candidate` e `hypothesis` più vecchi di
  `INSIGHT_TTL_DAYS` (30 g) → cancellazione.
- **Mutazioni [C]:** `status → candidate`, `convergence_count → 1`,
  `demoted_once → True`; oppure **`delete`**.
- **Misurato [M]:** **finding APERTO (02-03/07)** — la demozione è **aggirata per
  reincarnazione**: un insight neonato assorbe i demoti come convergenza e viene
  promosso alla nascita. Sentinella `591fec05`.
- **Metrica falsificante [H]:** frazione di promozioni che sono in realtà
  reincarnazioni. **Questo mina direttamente il senso della soglia di 2c** e va
  misurato prima di qualunque conclusione sulla convergenza.
- **Dipendenze [C]:** accoppiamento circolare con 2c: 2c promuove, il cleanup demota,
  la demozione rientra come convergenza in 2c.

> **Finding 2 — ciclo chiuso non dichiarato.** 2c e il cleanup insight formano un
> anello con retroazione (promozione → demozione → convergenza → promozione).
> Il gate `_repromotion_block_reason` impedisce al demoted inutilizzato di essere
> rivalutato come **seme**, ma la query KNN di un altro candidate include ancora
> tutti i `status=candidate`: il demoted può quindi contribuire come **vicino** alla
> convergenza di un neonato. Per una prima misura ecologica vanno trattati come
> sottosistema composto. Restano separabili con un disegno fattoriale controllato,
> purché l'arm venga descritto come intervento artificiale e non come ciclo naturale.

### Cleanup memorie stantie

- **File/righe [C]:** `_cleanup_stale_memories` `3597-3640`.
- **Popolazione [C]:** `scan_iter("euri:memory:*")`, solo
  `_EPHEMERAL_SOURCES = {passive, reflection, conversation}` (`dream_engine.py:3609`),
  solo con `recalled_count == 0` e `expires_at` già passato. Anche qui `user` e `teach`
  sono fuori portata.
- **Mutazioni [C]:** `delete`. **Provenienza persa, nessun tombstone.**
- **Nota [C]:** il commento nel codice dichiara che `recalled_count` **non è
  indicizzato in RediSearch**, perciò la scansione è necessaria. È un vincolo di
  schema che condiziona tre operatori (2d, 2e, cleanup).

### Cleanup incorporato nel Loop 2a — NO-OP

- **File/righe [C]:** `voice_daemon.py:3555-3564`.
- **Intenzione [I]:** dopo la reflection, eliminare memorie già scadute come safety
  net.
- **Comportamento reale [C]:** chiama
  `get_expiring_memories(days_ahead=0)`. La funzione in
  `core/memory_manager.py:774-792` accetta soltanto documenti per cui
  `now_ts < exp <= cutoff`; con `cutoff == now_ts` la condizione è impossibile.
  Il cleanup restituisce sempre una lista vuota e non cancella nulla.
- **Conseguenza:** non è un mietitore attivo, ma codice morto che può far credere
  esista una protezione aggiuntiva. La scadenza effettiva resta affidata al TTL
  Redis e agli altri cleanup.

---

## 3. La forma comune regge? Parzialmente — ed è un finding

Forma proposta:

> **stato + nuova evidenza → transizione + provenienza + conseguenza downstream**

Applicandola a tutti gli operatori emergono **tre famiglie**, non una.

### Famiglia A — Aggiornatori di evidenza (la forma regge)

`2c`, `2d`, `2f`, `2g`, `2h`, propagazione di provenienza.

| Operatore | Stato | Nuova evidenza | Transizione | Provenienza | Downstream |
|---|---|---|---|---|---|
| 2c | `candidate` | convergenza giudicata | → `hypothesis`/`promoted` | `convergence_count` | RAG, cleanup |
| 2d | memoria in scadenza | `recalled_count` / verdetto LLM | TTL esteso o **morte** | **persa se DROP** | retrieval |
| 2f | coppia in conflitto | classificazione LLM | → `superseded_by` | arco creato | retrieval, 2h |
| 2g | correzione ricevuta | verdetto LLM | → `requires_verification` | verdetto registrato | propagazione |
| 2h | arco di 2f | classificazione SAME/RELATED/… | narra **o ritira l'arco** | reflection o ripristino | retrieval |
| prov. | nodo derivato | stato dei genitori | → `provenance_stale` | riferimento ai genitori | ranking |

Qui la forma regge, ma **non tutta la famiglia è già un TMS formale**.
2f, 2h e la propagazione di provenienza sono fortemente *TMS-like*: stato +
giustificazione → revisione della credenza + propagazione. 2c e 2d aggiornano
anch'essi stato e priorità, ma non mantengono insiemi espliciti di
giustificazioni né una chiusura di consistenza. La lettura TMS è quindi un
posizionamento architetturale utile, non ancora un'equivalenza formale.

### Famiglia B — Generatori (la forma NON regge)

`2a`, `2b`, `2i`, e per metà `2e`.

Questi operatori **non aggiornano lo stato di nulla**. Leggono nodi e ne creano di
nuovi. Non c'è transizione, non c'è evidenza nuova sul nodo di input: c'è produzione.
Forzarli nella forma comune richiederebbe di chiamare "transizione" il fatto che
esista un figlio, che è una descrizione vuota.

La forma che regge per loro è diversa:

> **nodi esistenti + criterio di selezione → nuovo nodo + provenienza verso i genitori**

`2e` è a cavallo: crea un nodo (famiglia B) **e** marca le foglie con
`consolidated_into` (famiglia A). È l'unico che appartiene a entrambe, e non è un caso
che sia anche quello con la provenienza migliore.

### Famiglia C — Mietitori (la forma si rompe sul terzo termine)

`2d` ramo DROP, cleanup insight ramo delete, cleanup memorie stantie.

Qui c'è stato ed evidenza, ma la transizione è **terminale** e la provenienza non è
conservata: **è distrutta**. La forma comune presuppone che dopo la transizione ci sia
ancora un nodo di cui parlare. Qui non c'è. I log possono contare le cancellazioni,
ma senza tombstone il controfattuale non è ricostruibile dal solo stato corrente:
non si può rieseguire il sistema chiedendo quante volte quel contenuto sarebbe stato
recuperato o utile. Per misurarlo servono una tombstone, una quarantena/copia di
audit oppure un braccio di controllo che conservi il contenuto.

Esiste inoltre una transizione terminale esterna a questi operatori:
l'espirazione nativa Redis dei nodi con TTL. Anche senza un `delete` esplicito, la
scadenza può rimuovere il documento senza lasciare nel grafo cognitivo una
tombstone equivalente. Va inclusa nel censimento prima di attribuire ai soli
mietitori espliciti gli effetti di mortalità.

> **Finding 3 — la primitiva unica del 15/06 non esiste in questa forma.** Esiste una
> primitiva forte per la famiglia A (evidence-update in senso TMS), una seconda forma
> per la famiglia B (generazione con provenienza), e una terza famiglia che rompe
> l'invariante di provenienza. Non è un fallimento del progetto: è un risultato
> strutturale, ottenuto per lettura e non per congettura.
>
> **Conseguenza operativa:** unificare A è realistico e probabilmente utile. Unificare
> A con B sarebbe forzatura. La famiglia C andrebbe **resa osservabile** (tombstone,
> quarantena o copia di audit invece della sola cancellazione) prima ancora di
> essere unificata; altrimenti resta fuori dalla portata di un'ablazione
> controfattuale affidabile.

---

## 4. Accoppiamenti da rispettare nell'ablazione

| Coppia | Natura | Vincolo sperimentale |
|---|---|---|
| **2c ↔ cleanup insight** | anello con retroazione | prima misura ecologica come composto; poi fattoriale per isolare seme, vicino demoto e cleanup |
| **2f → 2h** | proposta e possibile ritiro | dipendenza asimmetrica: 2h richiede gli archi di 2f; spegnere 2h misura comunque il suo contributo incrementale |
| **2e ↔ propagazione** | ordine | la propagazione gira apposta dopo, per valutare i consolidati dello stesso ciclo |
| **2g → propagazione** | diffusione | un `requires_verification` indebito si propaga ai derivati |
| **2a → 2b/2c/2e** | a monte | 2a produce il materiale che tutti gli altri consumano |
| **2d, 2e, cleanup** | vincolo di schema | dipendono da `recalled_count`, non indicizzato → scan completa |

**Prima sequenza ablabile consigliata:** `{2c + cleanup insight}` come comportamento
ecologico, poi fattoriale controllato; `{2f}` contro `{2f + 2h}`; `{2e}` contro
`{2e + propagazione}`. `{2a}` è ablabile da solo, ma spegnerlo riduce nel tempo
l'ingresso di più operatori a valle: non lo azzera necessariamente, perché essi
possono continuare a consumare memoria preesistente.

---

## 5. Rischio di effetto osservatore, per operatore

| Livello | Operatori | Motivo |
|---|---|---|
| **Alto** | 2a, 2b, 2c | ogni test conversazionale crea memorie che diventano semi e insight |
| **Medio** | 2d, 2e, cleanup | dipendono da `recalled_count`, che i test incrementano |
| **Basso** | 2f, 2g, 2h, propagazione | operano su relazioni già esistenti |

Il rischio non è teorico: è già documentato che test e re-probe creano memorie
vere-ma-artificiali che il Dream riusa. Per gli operatori ad alto rischio,
l'ablazione richiede il disegno appaiato, non l'on/off su sistema vivo.

---

## 6. Cosa manca a questo documento

Onestà su ciò che resta da verificare o misurare:

1. Se la doppia politica di TTL sulle reflection (90 giorni da tabella, 7 giorni dal
   call site) sia intenzionale.
2. I conteggi storici comparabili di 2h (SAME contro RELATED/DIFFERENT). Al
   29/07 Redis contiene 234 elementi in `euri:loop2h:narrated` e 3 in
   `euri:loop2h:non_evolution`, ma i due insiemi non coprono lo stesso periodo:
   `non_evolution` è stato introdotto solo con l'hardening del 26/07. Il rapporto
   grezzo suggerisce una funzione prevalentemente narrativa, ma non può ancora
   riclassificare causalmente l'operatore.
3. La provenienza terminale delle scadenze Redis: quali nodi sono realmente
   scomparsi per TTL e quale sarebbe stato il loro uso successivo.
4. Le metriche preregistrate e i bracci appaiati con cui sottoporre ad ablazione
   ciascuna famiglia senza contaminare il substrato vivo.

Quattro lacune dichiarate nella prima stesura sono state chiuse leggendo il codice
e sono finite nel testo: cadenza/guardie di 2a; provenienza di 2a
(`source_memory_ids` + `session_memory_ids`); provenienza di 2b
(`memory_a_id`/`memory_b_id` e `source_memory_ids`); tabelle delle sorgenti
soggette a scadenza automatica (`_TTL_BY_SOURCE`, `_EPHEMERAL_SOURCES`).
Il primo confronto verificato con RMM, MemSifter, SF-AMS e MemLineage è in §7.

---

## 7. Audit Codex del prior art — prima mappa verificata

Questa sezione non assegna automaticamente equivalenze: distingue ciò che il
lavoro citato rende vicino a Euri da ciò che resta diverso e quindi misurabile.
Le fonti sotto sono paper o pagine editoriali primarie, verificate il 29/07/2026.

| Operatore/famiglia Euri | Confronto verificato | Sovrapposizione | Differenza da non gonfiare |
|---|---|---|---|
| 2a reflection | Generative Agents; CoALA | riflessione su un memory stream e trasformazione di esperienza in memoria più astratta | il checkpoint durevole, la provenienza dei genitori e lo stato `internal_reflection` sono scelte di Euri, ma non rendono nuova la reflection in sé |
| 2b/2i generatori | Generative Agents; Letta/MemGPT | creazione di memoria derivata e calcolo fuori dal turno immediato | il Dream Engine va valutato per la qualità/uso dei ponti, non rivendicato come novità perché gira in idle |
| 2c lifecycle insight | MemoryBank; SF-AMS | decadimento, sopravvivenza e aggiornamento della memoria usando segnali di importanza/uso/tempo | promozione per convergenza e retrocessione per mancato richiamo è una combinazione specifica; la reincarnazione mostra che oggi il ciclo non realizza ancora pulitamente l'intenzione |
| 2d death-row | MemoryBank; SF-AMS | retention/deletion policy dipendente dal valore stimato della memoria | Euri usa richiamo osservato e judge locale, ma il `delete` senza tombstone impedisce di dimostrarne il beneficio controfattuale |
| 2e consolidamento | Mem0; A-MEM | estrazione, dedup/consolidamento e collegamento di note | la provenienza foglie→consolidato è buona; resta da misurare se il consolidato aiuta più di quanto affolli il retrieval |
| 2f/2h revisione | Doyle TMS; AGM; Graphiti/Zep | revisione di credenze/relazioni, invalidazione e storia temporale | è la zona più chiaramente TMS-like; non è ancora un TMS formale con justification set espliciti |
| 2g + propagazione | TMS; database provenance | correzione e propagazione dello stato dei genitori ai derivati | la scelta additiva e reversibile è forte, ma la sua utilità downstream non è ancora stata isolata |
| lineage/utilità | RMM; MemSifter; MemLineage | segnali ricavati dall'uso nell'output, attribuzione del contributo, tracce di derivazione | “recuperato/usato” non equivale ancora a “causalmente utile”; serve la leave-one-out appaiata sugli stessi prompt/semi |
| cronologia e supersede | Graphiti/Zep; database bi-temporali | valid time/transaction time, invalidazione senza perdita della storia | Euri possiede parte dello schema (`event_start`, `asserted_at`, `superseded_by`) ma la data-evento è poco popolata e il recente richiamo cronologico ne ha mostrato il costo |
| intero sistema | CoALA; survey 2026 | architettura composta da memoria, ragionamento e azione | la novità non può essere “avere più tipi di memoria e reflection”; deve essere una proprietà dell'anello che sia falsificabile |

### Rivendicazione che il prior art consente ancora di testare

RMM, MemSifter, SF-AMS e MemLineage rendono non difendibile una rivendicazione
generica secondo cui Euri sarebbe il primo sistema a usare utilità, esiti o lineage
per gestire memoria. La formulazione più stretta e falsificabile è:

> **Euri tenta di chiudere il ciclo di vita della memoria tramite l'uso osservato
> degli output e di conservare gli errori come storia epistemica, invece di curare
> retroattivamente l'archivio.**

La frase contiene due ipotesi, non due risultati già dimostrati:

1. l'anello output→lineage→ri-pesatura/promozione/demozione produce un miglioramento
   causale rispetto allo stesso sistema senza quell'anello;
2. conservare correzioni, supersessioni e provenienza migliora aggiornamento,
   astensione e ricostruibilità rispetto a riscrittura/cancellazione.

Entrambe possono fallire. Per la prima, “un nodo era nel prompt” prova esposizione,
non utilità: il test minimo è leave-one-out sul nodo, stesso contesto residuo,
stesso seed e scoring cieco. Per la seconda, i rami `delete` e il TTL Redis devono
prima lasciare una copia/tombstone di audit; altrimenti il braccio eliminato non è
più ricostruibile.

### Fonti primarie

- Sumers et al., [*Cognitive Architectures for Language Agents
  (CoALA)*](https://arxiv.org/abs/2309.02427), 2023.
- Park et al., [*Generative Agents: Interactive Simulacra of Human
  Behavior*](https://arxiv.org/abs/2304.03442), 2023.
- Packer et al., [*MemGPT: Towards LLMs as Operating
  Systems*](https://arxiv.org/abs/2310.08560), 2023.
- Zhong et al., [*MemoryBank: Enhancing Large Language Models with
  Long-Term Memory*](https://arxiv.org/abs/2305.10250), 2023.
- Rasmussen et al., [*Zep: A Temporal Knowledge Graph Architecture for
  Agent Memory*](https://arxiv.org/abs/2501.13956), 2025.
- Wu et al., [*LongMemEval: Benchmarking Chat Assistants on Long-Term
  Interactive Memory*](https://arxiv.org/abs/2410.10813), ICLR 2025.
- [*In Prospect and Retrospect: Reflective Memory Management for Long-term
  Personalized Dialogue Agents*](https://aclanthology.org/2025.acl-long.413/),
  ACL 2025 (RMM).
- [*MemSifter: Outcome-Driven Memory Selection for Long-Term
  Agents*](https://arxiv.org/abs/2603.03379), 2026.
- [*SF-AMS: Self-Forgetting Agent Memory System*](https://arxiv.org/abs/2607.22562),
  2026.
- [*MemLineage: A Lineage-First Framework for Agent
  Memory*](https://arxiv.org/abs/2605.14421), 2026.
- [*From Storage to Experience: A Survey on the Evolution of Agent
  Memory*](https://arxiv.org/abs/2605.06716), Findings of ACL 2026.
- Doyle, [*A Truth Maintenance System*](https://doi.org/10.1016/0004-3702(79)90008-0),
  1979.
- Cheney, Chiticariu e Tan, [*Provenance in Databases: Why, How, and
  Where*](https://doi.org/10.1561/1900000006), 2009.

### Verdetto dell'audit

La tabella non dimostra ancora che i loop migliorino Euri; dimostra qualcosa di
precedente e necessario:

- gli operatori non appartengono a una sola primitiva;
- una parte può essere descritta e misurata come aggiornamento di evidenza;
- i generatori richiedono metriche diverse dagli aggiornatori;
- le cancellazioni, esplicite o via TTL, devono diventare osservabili;
- il percorso forzato non riproduce fedelmente l'orchestrazione viva;
- alcune dipendenze impongono l'ordine degli arm, ma non rendono impossibile
  isolarne il contributo.

Il prossimo passo scientifico non è un refactor generale. È preregistrare, per
ciascuna famiglia, una conseguenza downstream attesa e un criterio di rimozione,
poi eseguire la prima ablazione appaiata su un sottosistema circoscritto.
