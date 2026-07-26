# Banco prova memoria di Euri

Stato: Fase 1 LoCoMo ridotta completata.

## Perché LoCoMo

Il dataset esistente è pertinente ed è già il primo corpus reale collegato al
banco prova. LoCoMo contiene conversazioni multisessione, domande single-hop,
temporali, multi-hop, open-domain e avversariali: misura quindi bene il richiamo
conversazionale di lungo periodo.

La fixture sintetica non sostituisce LoCoMo. Serve soltanto a collaudare in pochi
secondi isolamento, reset, adapter, trace e scorer senza avviare modelli.

## Confini

- Ogni run avvia un nuovo `redis-server` con RedisJSON e RediSearch.
- Redis usa una porta loopback casuale diversa da `6379`.
- PID del processo, marker segreto e directory temporanee vengono verificati
  prima di ogni reset o scrittura.
- Redis, Vault e report intermedi vivono sotto una sola directory temporanea.
- Il corpus passato all'ingestore non contiene domande o annotazioni gold.
- Il question runner riceve soltanto ID, testo e categoria della domanda; answer
  ed evidence restano nello scorer.
- Osservazioni, session summary ed event summary LoCoMo non vengono ingeriti
  implicitamente.

## Comandi

Test puri, adatti alla CI:

```bash
./venv/bin/python test_memory_benchmark.py
```

Smoke integration con Redis effimero:

```bash
./venv/bin/python test_memory_benchmark_integration.py
```

Acquisizione ufficiale LoCoMo:

```bash
./venv/bin/python -m benchmarks.euri_memory.fetch_locomo
```

Smoke sulla prima conversazione reale:

```bash
./venv/bin/python -m benchmarks.euri_memory.cli smoke \
  --source benchmarks/euri_memory/data/locomo10.json \
  --limit 1 \
  --output /tmp/euri-locomo-phase0-smoke.json
```

Primo confronto reale, con RAG di Euri e Passive learner:

```bash
./venv/bin/python -m benchmarks.euri_memory.cli ab \
  --output /tmp/euri-locomo-reduced-ab.json
```

Confronto italiano sugli stessi turni, domande, categorie ed evidence ID della
selezione v2:

```bash
./venv/bin/python -m benchmarks.euri_memory.cli ab \
  --selection benchmarks/euri_memory/fixtures/locomo_reduced_v2.json \
  --localization benchmarks/euri_memory/fixtures/locomo_reduced_v2_it.json \
  --output /tmp/euri-locomo-reduced-v2-it-ab.json
```

La selezione versionata usa 35 turni delle prime due sessioni di `conv-26` e
otto domande dichiarate prima della run. Il worker gira in un processo separato
perché configurazione e client Euri devono essere importati soltanto dopo aver
fissato Redis e Vault temporanei. Entrambi i profili indicizzano gli stessi turni
grezzi; `passive_memory` aggiunge esclusivamente il percorso reale estrattore →
Buttafuori → dedup → salvataggio.

Il corpus e la licenza restano locali e ignorati da Git. Il downloader registra
commit sorgente, URL, timestamp e SHA-256 in `data/source_manifest.json`. Il
rilascio è fissato al commit ufficiale `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`;
LoCoMo usa la licenza Creative Commons Attribution-NonCommercial 4.0.

## Prima run A/B

La run del 24 luglio 2026 ha usato `gemma4:26b` e
`intfloat/multilingual-e5-large`, entrambi locali:

| metrica | `rag_only` | `passive_memory` | delta |
|---|---:|---:|---:|
| token F1 medio | 0,370 | 0,356 | -0,015 |
| exact match | 0,333 | 0,333 | 0 |
| accuracy avversariale | 1,000 | 0,500 | -0,500 |
| evidence recall | 0,750 | 0,750 | 0 |
| chiamate LLM locali | 16 | 27 | +11 |

Il Passive learner ha estratto e salvato tre memorie. Su questo campione minimo
non ha migliorato il retrieval e una memoria episodica sull'estate ha causato
una risposta eccessiva a una domanda avversariale, invece dell'astensione. È una
regressione utile da conservare, non una conclusione statistica: il campione è
un solo dialogo con otto domande e lo scorer
`locomo_reduced_deterministic_v1_not_official` non è quello ufficiale LoCoMo.

## Replica e seconda selezione

La replica v1 ha lasciato invariato il baseline: il delta Passive è passato da
F1 -0,015 a -0,007 e l'accuracy avversariale è tornata a 1,000. È evidenza di
variabilità del trattamento, non di un miglioramento.

La selezione v2 (`locomo-conv42-s1-s2-q8-v1`) usa 51 turni e copre tutte le cinque
categorie: F1 0,187 → 0,162, exact match 0 → 0, evidence recall 0,625 → 0,625,
accuracy avversariale 1,000 → 1,000. Il Passive learner ha salvato 8 memorie su
10 candidati e ha richiesto 55 chiamate locali contro 16. Il prossimo confronto
è il LoCoMo completo o un campione stratificato più grande.

## Confronto italiano

La localizzazione `locomo-conv42-s1-s2-q8-it-v1` traduce e versiona tutti i 51
turni e le 8 coppie domanda/risposta. ID dei turni, evidence, sessioni e categorie
restano invariati. La run pulita del 25 luglio 2026 ha prodotto:

| lingua e profilo | token F1 | exact | avversariale | evidence hit | chiamate LLM |
|---|---:|---:|---:|---:|---:|
| EN `rag_only` | 0,187 | 0 | 1,000 | 0,625 | 16 |
| EN `passive_memory` | 0,162 | 0 | 1,000 | 0,625 | 55 |
| IT `rag_only` | 0,318 | 0 | 1,000 | 0,625 | 16 |
| IT `passive_memory` | 0,368 | 0 | 1,000 | 0,625 | 48 |

In italiano il delta Passive sul token F1 è +0,050, ma retrieval ed evidence hit
non migliorano. Il valore F1 fra lingue non è direttamente comparabile: cambiano
tokenizzazione e formulazione delle risposte gold. Il confronto qualitativo ha
trovato due regressioni concrete: il deduplicatore ha eliminato fatti distinti
come se fossero duplicati e l'estrattore ha trasformato “venerdì scorso” nella
data errata `20/01/2022`.

Entrambe le regressioni sono ora corrette. Il Passive learner usa l'àncora
italiana completa, conserva le espressioni relative e demanda la data canonica
al resolver deterministico. La deduplicazione usa la cosine solo per trovare
candidati, richiede copertura completa dei marker del nuovo fatto e un verdetto
LLM `DUPLICATO` esatto; nel dubbio conserva la memoria.

Replica isolata dopo i due fix:

| profilo | token F1 | exact | avversariale | evidence hit | chiamate LLM |
|---|---:|---:|---:|---:|---:|
| `rag_only` | 0,331 | 0 | 1,000 | 0,625 | 16 |
| `passive_memory` | 0,405 | 0 | 1,000 | 0,625 | 36 |

Il trattamento ha estratto e salvato 9 memorie su 9, contro 6 su 9 nella run
storica: i tre fatti prima eliminati sono stati recuperati e le 12 chiamate del
vecchio giudice di dedup sono scomparse. “Venerdì scorso” è rimasto nel testo e
ha prodotto `event_start=21/01/2022`. Il delta F1 della replica è +0,074, ma
exact ed evidence hit non cambiano: otto domande non bastano per una conclusione
statistica e run diverse di Gemma non vanno confrontate come se fossero
deterministiche.

Il limite di provenienza emerso in questa replica è stato poi corretto. Il
contratto dell'estrattore richiede l'unione di tutti i turni che sostengono le
diverse clausole; un audit semantico finale può riparare `source_turn_ids` prima
del salvataggio. Il Buttafuori passivo è ora un gate `KEEP/JUNK` senza
riscrittura, perché una parafrasi intermedia può introdurre dettagli non
pronunciati.

Nella replica finale del fix sono stati salvati 7 candidati su 7, senza rifiuti
di provenienza; 4 liste di turni sono state riparate. Una sonda reale sul caso
originario ha trasformato `[25]` in `[25,27]`, corrispondenti a `D2:3` (fine e
stampa della sceneggiatura) e `D2:5` (dramma e romanticismo). In quella specifica
replica l'estrattore aveva però prodotto un nodo separato con sola fine/stampa,
quindi il nodo salvato citava correttamente soltanto `D2:3` e la domanda sul
genere restava senza risposta.

La replica finale ha prodotto F1 `0,318 → 0,325` (+0,007), evidence hit invariato
a 0,625 e 39 chiamate locali nel trattamento. La differenza rispetto alla run
precedente (+0,074) conferma la variabilità di estrazione e generazione di Gemma:
la correzione va valutata sui record di provenienza, non attribuendole il delta
di un singolo punteggio.

## Replica con estrazione atomica a finestre

Il 25 luglio 2026 l'estrattore è stato separato in finestre sovrapposte da 12
messaggi (overlap 4), senza limite globale di sei fatti. Ogni finestra usa ID
locali, poi ricondotti ai turni della sessione; proprietà aggiunte in seguito
diventano memorie autonome. ID dubbi arrivano all'audit semantico invece di
essere scartati dal solo formato, mentre metadata temporali e salvataggio
restano successivi al verdetto.

Replica conclusiva sulla stessa fixture italiana:

| profilo | token F1 | avversariale | evidence hit | chiamate LLM |
|---|---:|---:|---:|---:|
| `rag_only` | 0,318 | 1,000 | 0,625 | 16 |
| `passive_memory` | 0,396 | 1,000 | 0,875 | 92 |

Il trattamento ha estratto 24 candidati, ne ha salvati 22, ne ha respinti 2
(uno per provenienza) e ha riparato 6 liste di fonti. La memoria “La
sceneggiatura di Joanna è un misto di dramma e romanticismo” conserva
`source_turn_ids=[25,27]`; q99 passa dall'astensione alla risposta corretta.
q207, che sostituisce falsamente la sceneggiatura con un romanzo breve, resta in
astensione. Il risultato dimostra copertura ed evidence migliori, ma anche un
costo elevato e frammentazione: è una replica diagnostica, non una stima
statistica.

Il report storico resta immutato per non riscrivere a posteriori il risultato
osservato.

La run usa un Redis temporaneo su una porta casuale registrata nel report,
non la porta personale `6379`. Il marker benchmark risulta assente dal Redis
personale anche dopo la prova. Un'istanza Euri attiva può condividere CPU, RAM,
GPU e Ollama, incidendo sui tempi; non condivide Redis, Vault o dati del
benchmark.

## Costi e licenze

La run non richiede API cloud o software a pagamento. Euri, Ollama, Redis,
Gemma e l'embedder vengono eseguiti localmente. “Gratuito” non significa però
che tutte le licenze siano identiche: LoCoMo è limitato all'uso non commerciale
da CC BY-NC 4.0 e Gemma è soggetto ai termini Google, pur essendo utilizzabile
localmente senza tariffa.

## Interpretazione dello smoke

`keyword_smoke` è solo una sonda deterministica della tubazione. Non è il RAG di
Euri e il suo `phase0_deterministic_not_official` non va confrontato con risultati
pubblicati. Il comando `ab` usa invece i percorsi reali, ma la metrica ridotta
resta interna e non va presentata come risultato LoCoMo ufficiale.
