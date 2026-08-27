# Euri — costo dei reload Ollama al cambio `num_ctx`

**Data:** 26 agosto 2026, 08:52 CEST
**Macchina:** Lenovo ThinkStation P620, Threadripper PRO 3975WX, 128 GiB ECC,
2x RTX 4060 Ti 16 GB
**Runtime:** Ollama 0.30.7, `gemma4:26b`
**Stato Euri:** fermo; GPU inizialmente vuote

## Esito

Il cambio dello stesso Gemma4 fra runner `num_ctx=4096` e `num_ctx=32768`
forza un riavvio completo del runner. Sulla P620 ogni cambio costa in mediana
**10,368 s**, contro **0,641 s** per una richiesta identica a contesto
invariato.

Una coppia operativa `32768 -> 4096 -> 32768` aggiunge quindi circa **19,2 s**
rispetto a due chiamate eseguite sul runner gia' compatibile. `keep_alive=-1`
non evita il problema: mantiene vivo il runner corrente, ma Ollama lo
sostituisce quando cambia la configurazione di contesto.

Questo finding ha priorita' rispetto a RAM-disk/page-cache e governor CPU nei
turni che attraversano componenti con `num_ctx` diversi. Il preload dei pesi
puo' ridurre il cold disk, ma non impedisce la ricostruzione del runner e le
copie RAM/VRAM osservate qui.

## Protocollo

Tutte le richieste hanno usato:

- endpoint `/api/chat`;
- prompt identico: `Rispondi soltanto: OK`;
- `temperature=0`, `seed=42`, `num_predict=1`, `think=false`;
- `keep_alive=-1`;
- unica variabile: `num_ctx=4096` oppure `num_ctx=32768`.

Sequenza:

1. caricamento iniziale a 32768 e controllo a caldo;
2. tre alternanze complete 4096/32768;
3. quattro controlli consecutivi a 32768;
4. ingresso a 4096 e quattro controlli consecutivi a 4096;
5. ripristino finale del runner a 32768.

I tempi wall sono stati misurati dal client. `load_duration`, prefill e decode
provengono dalla risposta nativa Ollama. Il journal di Ollama e' stato usato
come verifica indipendente della creazione dei runner.

## Risultati aggregati

| Condizione | n | Wall mediana | Wall media | `load_duration` media | Restart nel journal |
|---|---:|---:|---:|---:|---|
| Cambio 4096/32768 | 8 | **10,368 s** | **10,244 s** | **10,236 s** | si', ogni volta |
| Contesto invariato | 9 | **0,641 s** | **0,649 s** | **0,603 s** | no |

Differenza media direttamente osservata per singolo cambio: **9,595 s**.

Il caricamento iniziale da GPU vuote a 32768 ha richiesto 10,922 s, ma non e'
una misura valida di cold disk: i blob erano verosimilmente ancora nella page
cache Linux per l'uso precedente. Va tenuto separato dall'A/B sul contesto.

## Misure grezze

| Etichetta | `num_ctx` | Wall ms | Total Ollama ms | Load ms | Prefill ms |
|---|---:|---:|---:|---:|---:|
| cold32 | 32768 | 10922 | 10910,413 | 8466,958 | 2441,271 |
| hot32 | 32768 | 667 | 658,992 | 612,150 | 45,046 |
| alt4a | 4096 | 9965 | 9956,923 | 9792,683 | 162,257 |
| alt32a | 32768 | 10429 | 10419,765 | 10251,291 | 166,512 |
| alt4b | 4096 | 10022 | 10013,958 | 9850,877 | 160,496 |
| alt32b | 32768 | 10372 | 10363,332 | 10197,429 | 164,024 |
| alt4c | 4096 | 10035 | 10026,703 | 9864,741 | 160,213 |
| alt32c | 32768 | 10395 | 10386,855 | 10217,281 | 167,920 |
| ctrl32a | 32768 | 673 | 664,885 | 614,402 | 48,584 |
| ctrl32b | 32768 | 635 | 626,544 | 595,039 | 29,752 |
| ctrl32c | 32768 | 637 | 627,548 | 595,758 | 30,198 |
| ctrl32d | 32768 | 641 | 631,528 | 599,175 | 30,591 |
| enter4 | 4096 | 10368 | 10360,389 | 10196,481 | 161,907 |
| ctrl4a | 4096 | 667 | 658,880 | 608,349 | 48,791 |
| ctrl4b | 4096 | 649 | 639,667 | 606,786 | 31,239 |
| ctrl4c | 4096 | 637 | 628,526 | 596,145 | 30,914 |
| ctrl4d | 4096 | 638 | 630,661 | 598,427 | 30,463 |
| restore32 | 32768 | 10368 | 10359,663 | 10193,119 | 164,786 |

Il decode di un solo token e' trascurabile in tutte le righe. Il costo della
condizione alternata e' quasi interamente attribuito a `load_duration`.

## Conferma dal journal

Per ogni alternanza Ollama ha registrato un nuovo comando `llama-server`:

```text
... --model ...gemma4... -c 4096  -b 512  -ub 512 --context-shift --keep 4
... --model ...gemma4... -c 32768 -b 1024 -ub 1024
```

Le richieste di controllo a contesto invariato non hanno prodotto alcuna riga
`starting llama-server`.

## Origine nel runtime Euri

Nel codice corrente convivono configurazioni diverse dello stesso modello:

- il runner principale/warm-up usa 32768;
- `core/action_controller.py` richiede esplicitamente 4096;
- `core/workflow_planner.py` usa 4096 e, in un altro percorso, 16384;
- alcuni strumenti documentali usano 16384 o 32768.

Il journal della sessione reale del 25 agosto mostra gia' sequenze alternate
4096/32768, ciascuna accompagnata da un nuovo caricamento Gemma. Il test del
26 agosto dimostra causalmente che il solo cambio di `num_ctx`, a prompt e
modello invariati, e' sufficiente a produrre il costo.

## Provenienza storica e classificazione del difetto

Il `git blame` mostra che i valori non derivano da una successiva ottimizzazione
hardware:

- `num_ctx=4096` nasce con il Workflow Planner nel commit `0852fc52` del
  26 giugno 2026;
- `num_ctx=16384` nasce nello stesso componente per generazioni documentali piu'
  lunghe ed e' conservato dal fix anti-troncamento `de53e6ad` del 3 luglio;
- l'ActionController nasce gia' con `num_ctx=4096` nel commit `1a2f2123` del
  21 luglio e quel valore non e' mai stato rivalutato.

I messaggi di commit e il changelog motivano contratto JSON, sicurezza,
fail-open e budget di output. Non documentano invece una misura di VRAM,
latenza o scheduler che giustifichi il cambio del runner. Il 4096 e' coerente
con un dimensionamento locale del prompt corto; il 16384 con testi piu' lunghi.
Il difetto e' l'assenza di una valutazione globale del ciclo di vita del modello:
risparmiare memoria su una singola chiamata sostituisce il runner condiviso.

La ricostruzione non dipende dalla migrazione P620. Il journal del 21 luglio,
con lo stesso Ollama 0.30.7, mostra durante lo sviluppo dell'ActionController
sequenze Gemma `32768 -> 4096 -> 32768`, ciascuna con un nuovo
`starting llama-server`. Il problema era quindi presente dal primo giorno sulla
X99 ed e' rimasto invisibile.

Le ragioni principali per cui i test non lo hanno intercettato sono verificabili:

- le suite di ActionController e Workflow Planner iniettano `FakeChat`, quindi
  provano parsing, policy e sicurezza ma non lo scheduler Ollama;
- il probe live dell'ActionController stampa proposta e decisione, ma non
  registra `load_duration` ne' conta i restart;
- i tempi di caricamento erano inglobati nella latenza complessiva della
  chiamata LLM e potevano sembrare semplice inferenza lenta sulla X99.

Il contesto piccolo ha un beneficio reale ma sproporzionato: nel test P620 il
runner 4096 proietta 16.672 MiB di device memory contro 18.079 MiB del 32768,
circa 1.407 MiB risparmiati. Entrambi offloadano 31/31 layer e il runner 32768
entra con ampio margine nelle due 4060 Ti. Nel runtime condiviso attuale quel
risparmio non compensa circa 9,6 s di penalita' per ogni sostituzione.

Classificazione: **parametro localmente ragionevole, errore di integrazione e
prestazioni a livello di sistema**. Non ci sono evidenze di una scelta
consapevole di pagare il reload; ci sono invece evidenze che il reload fosse
gia' presente e non misurato.

## Decisione e correzione applicata

Il confronto qualitativo A/B fra 4096 e 32768 non e' stato eseguito: il valore
32768 e' un superset del contesto corto, entra gia' interamente nelle due GPU e
il difetto sistemico era stato dimostrato causalmente. Il 26 agosto e' stata
quindi applicata direttamente la correzione minima:

- `CHAT_OLLAMA_NUM_CTX=32768` e' l'unica configurazione del runner realtime;
- `RealtimeClient` forza centralmente quel valore per ogni chiamata a
  `gemma4:26b`, anche quando il call site lo omette o tenta di usarne un altro;
- ActionController, Workflow Planner/Engine, Brain, warm-up e strumenti
  documentali usano esplicitamente la stessa costante;
- Dream/Qwen e gli script di benchmark restano separati dalla policy realtime;
- un test di regressione verifica aggiunta, override senza mutazione delle
  opzioni del chiamante e isolamento degli altri modelli.

Validazione automatica: **87/87 test unitari superati in 75,3 s**.

Smoke test reale, con Euri fermo e runner Gemma gia' residente:

- ActionController ha interpretato correttamente «Puoi controllarla adesso?»
  come `executor.gpu_usage`, senza eseguire lo strumento;
- Ollama ha servito la richiesta in 3,110 s con `n_ctx_slot=32768`;
- nel journal non compare alcun `starting llama-server`;
- prima e dopo il test `ollama ps` riporta lo stesso modello, ID
  `5571076f3d70`, 18 GB, 100% GPU, contesto 32768, `Forever`.

Resta da fare soltanto il collaudo organico nella pipeline voce con alternanza
CHAT/RAG/ACTION. Non aumentare `OLLAMA_MAX_LOADED_MODELS`: due runner Gemma
duplicano pesi e buffer e non sono compatibili con il margine VRAM attuale.

## Stato delle altre due ipotesi sistemistiche

- **Page cache Gemma + Qwen:** fattibile in 128 GiB e utile per cold-start o
  cambio modello; da misurare separatamente. Non risolve questo reload.
- **CPU performance:** test A/B ancora da fare. Puo' ridurre micropassaggi e
  jitter, ma non puo' recuperare un riavvio runner da circa 10 s.
