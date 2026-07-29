# Preregistrazione — verifica appaiata Loop 2f / Loop 2h

**Protocollo:** `loop2fh-controlled-it-v1`

**Data di congelamento:** 29/07/2026

**Baseline di produzione letta:** `5dc3554`

**Stato:** DEVELOPMENT controllato; non validazione indipendente sulle memorie
personali e non risultato del paper.

## 1. Domanda

Il Loop 2f riconosce correttamente quando due memorie richiedono una
supersessione? Quando 2f fonde per errore entità distinte, il Loop 2h riduce le
false supersessioni senza riaprire supersessioni corrette?

La verifica separa tre oggetti che i contatori live confondono:

1. accuratezza semantica di 2f (`contradiction/comparison/none`);
2. accuratezza identitaria di 2h (`same/related/different/unknown`);
3. conseguenza composta sul retrieval (`supersede_a/keep_both`).

Non viene valutata in questa fase la qualità letteraria della reflection prodotta
da 2h.

## 2. Perché non basta la storia live

Snapshot Redis read-only del 29/07, senza esportare contenuti:

| Stato | Conteggio |
|---|---:|
| coppie nel set `euri:loop2f:checked` | 603 |
| TTL residuo del set al controllo | 15.516.268 s |
| supersessioni attive | 252 |
| supersessioni attive già narrate | 230 |
| note di confronto 2f | 98 |
| reflection 2h | 83 |
| inversioni annotate sui documenti | 2 |
| archi attivi con vincitore mancante | 21 |

Questi numeri provano attività e persistenza, non correttezza. I 230 casi narrati
includono versioni precedenti del gate; le inversioni strutturate sono recenti; 21
vincitori mancanti rendono già impossibile ricostruire integralmente alcune
coppie. Usare il live come gold produrrebbe selezione temporale e sopravvivenza.

## 3. Fixture congelata

File: `benchmarks/euri_memory/fixtures/loop2fh_v1.json`

```text
fixture_id:  loop2fh-controlled-it-v1
SHA-256:     b0b04202253282779cbc092d853dddbeb380a960e761b7f03f49d3b3922b04cc
casi:        42
primari:     36
ambigui:      6
sentinelle:   9 (tre repliche)
osservazioni: 60
chiamate:   120
```

Strati primari:

- 12 contraddizioni sullo stesso referente che devono soppiantare A;
- 12 entità distinte ma semanticamente vicine che devono restare entrambe;
- 6 fatti complementari sullo stesso referente;
- 6 coppie target/risultato, entrambe informative.

Sei casi senza identità sufficiente sono diagnostici: non entrano
nell'accuratezza dell'azione e misurano soltanto la capacità di restare
`UNKNOWN`.

La fixture è stata scritta conoscendo l'architettura. È quindi un development
set falsificante per failure mode dichiarati, non un held-out indipendente.

## 4. Bracci

Ogni caso è sottoposto agli stessi classificatori di produzione. L'ordine delle
due chiamate è controbilanciato deterministicamente per caso e replica.

### Braccio F — solo 2f

- `contradiction` → `supersede_a`;
- `comparison|none` → `keep_both`.

### Braccio FH — 2f seguito da 2h

2h influenza l'azione soltanto quando 2f ha prodotto `contradiction`:

- `same` → mantiene `supersede_a`;
- `related|different` → ripristina A, quindi `keep_both`;
- `unknown` → comportamento runtime attuale: l'arco resta nascosto ma
  ritentabile; nell'azione immediata conta come `supersede_a`.

2h viene comunque interrogato su tutti i casi per misurarne separatamente la
classificazione identitaria. Questo secondo uso è diagnostico e non pretende di
riprodurre l'invocazione runtime.

## 5. Fedeltà e integrità

Il runner invoca direttamente:

| Percorso | SHA-256 del sorgente della funzione |
|---|---|
| `DreamEngine._llm_classify_pair` | `1d07418242862980c8d322870067ab073af6637c3980b387f57bcc3f33aea634` |
| `DreamEngine._ollama_chat` | `ac8a7aa6533cd8d45fe23e4d099d6ae0c6a583b36581bcb348f5d38c4ac286dd` |
| `SelfObservation._classify_pair_relation` | `e63fc7f3809648e3f69cb2fdab7a0bb97bde1244e8638979f9a761ddfc7817d9` |

Il contesto operativo anteposto dal wrapper 2f è parte dell'input di produzione:

```text
EURI_CONTEXT SHA-256:
7491dee96452a3d7ba0077020ede3d62a2e62dbf9bb8c44434c28f0678a076d2
```

Il modello e il digest Ollama vengono sigillati al run. Il worktree tracciato
deve essere pulito. Checkpoint e resume richiedono identità completa e rifiutano
osservazioni estranee o label fuori contratto.

Il runner non crea né apre una connessione Redis, non istanzia un Dream Engine
operativo, non salva memorie e scrive soltanto sotto `audit_output`.
I gold sono presenti nella fixture per l'analisi, ma le funzioni di
classificazione ricevono esclusivamente `memory_a` e `memory_b`.

Protocol SHA-256 del dry-run:

```text
4f9162b49e54b201864529f44b26a0f1662413e9e2a7a04d460e4fa34cc65ba8
```

## 6. Metriche preregistrate

### 2f

- accuratezza dell'azione;
- false supersession rate sui casi `keep_both`;
- true supersession recall sui casi `supersede_a`;
- breakdown per strato.

Verdetto:

- `GO_DEV`: accuracy ≥0,80; false supersession ≤0,10; true-supersession recall
  ≥0,80;
- `NO_GO_DEV`: false supersession >0,20 oppure true-supersession recall <0,60;
- altrimenti `INCONCLUSIVE_DEV`.

### Valore incrementale di 2h

Metrica primaria: fra i casi cross-entity che 2f ha erroneamente deciso di
soppiantare, quota ripristinata da 2h.

Guardie:

- almeno 2 opportunità cross-entity; altrimenti risultato inconclusivo;
- correction rate ≥0,50;
- zero supersessioni corrette riaperte;
- accuratezza della relazione 2h ≥0,80;
- `UNKNOWN` ≤0,20 fra i casi realmente attivati da 2f;
- stabilità dell'azione composta ≥0,90 sulle nove sentinelle.

`NO_GO_DEV` se 2h danneggia almeno una supersessione corretta, non corregge
nessuna opportunità disponibile o non riduce il numero totale di false
supersessioni. Negli altri casi non-GO il verdetto è `INCONCLUSIVE_DEV`.

Viene riportato anche il McNemar esatto sui casi migliorati/peggiorati, ma non è
usato per trasformare questo development set in una validazione indipendente.

## 7. Limite strutturale dichiarato prima dei risultati

2h distingue identità, non relazione logica fra due claim sulla stessa entità.
Può quindi correggere la fusione UBQ↔Altosele, ma non necessariamente un errore
di 2f che scambi:

- modulo e IZOD dello stesso materiale per valori incompatibili;
- target e risultato misurato per due versioni dello stesso fatto.

In entrambi i casi 2h può dire correttamente `SAME` e lasciare intatta una
supersessione sbagliata. Il breakdown `same_entity_complementary_keep` e
`target_vs_result_keep` serve precisamente a rendere visibile questo limite.

## 8. Interpretazione consentita

Un eventuale `GO_DEV` autorizza soltanto a dire che, su casi controllati, 2h
corregge una parte degli errori cross-entity di 2f senza danno osservato.

Non autorizza a dire:

- che le 252 supersessioni personali siano corrette;
- che la narrativa di 2h migliori le risposte;
- che il sistema generalizzi a domini non rappresentati;
- che 2f+2h sia validato scientificamente sul comportamento vivo.

La verifica ecologica successiva richiederà un campione reale cieco, conservando
entrambe le fonti e senza assumere `narrated` come ground truth.

## 9. Emendamento tecnico pre-risultati (29/07/2026)

La prima esecuzione si è arrestata dopo sette chiamate complete, durante
`sup_02__r0`, prima di produrre `results.json` e prima di eseguire qualunque
analisi aggregata. Il classificatore 2f ha ricevuto dal modello
`CONTRADIZIONE` (una sola `D`), mentre il parser di produzione riconosce la
contraddizione soltanto se trova la sottostringa `CONTRADD`. Il comportamento
effettivo di produzione è quindi `none`.

L'episodio ha rivelato un difetto di osservabilità del banco: una risposta
fuori contratto veniva correttamente bloccata, ma ciò impediva di completare la
misura del comportamento reale del loop. L'harness viene perciò irrobustito
prima della prima run completa, senza cambiare fixture, ordine, prompt,
modello, gold, metriche o soglie:

- eccezioni di trasporto/modello continuano ad arrestare il run;
- un output non vuoto fuori contratto conserva la label realmente prodotta dal
  parser di produzione;
- il record aggiunge `contract_ok`, un diagnostico strutturato e soltanto
  l'hash SHA-256 dell'output grezzo;
- l'analisi riporta separatamente frequenza e casi delle violazioni del
  contratto.

Il checkpoint parziale originale non viene ripreso né usato come risultato.
La nuova esecuzione parte in una directory distinta, legata al nuovo commit.
Questo emendamento non corregge il parser di 2f e non trasforma
`CONTRADIZIONE` in `contradiction`: il difetto resta parte del sistema
misurato.
