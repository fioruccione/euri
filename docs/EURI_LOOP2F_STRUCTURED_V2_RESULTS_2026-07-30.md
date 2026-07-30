# Loop 2f structured v2 — risultato development

Data di esecuzione: 30/07/2026  
Verdetto preregistrato: **NO_GO_DEV**

## Esito

La policy `loop2f-structured-affirmative-v2` non diventa autorità runtime.
Il classificatore legacy distribuito resta attivo.

Il test appaiato ha eseguito 152 chiamate sul modello locale, corrispondenti a
76 osservazioni e 66 casi indipendenti. Nessun accesso o modifica a Redis.

| Insieme | Braccio | Accuratezza | False supersession | Recall supersession vere |
|---|---:|---:|---:|---:|
| regressione v1, n=36 | legacy | 33/36 (91,7%) | 1/24 (4,2%) | 10/12 (83,3%) |
| regressione v1, n=36 | structured | 30/36 (83,3%) | 0/24 | 6/12 (50,0%) |
| challenge v2, n=30 | legacy | 27/30 (90,0%) | 2/22 (9,1%) | 7/8 (87,5%) |
| challenge v2, n=30 | structured | 26/30 (86,7%) | 0/22 | 4/8 (50,0%) |

Sul challenge appaiato, structured migliora due casi e ne peggiora tre fra i
cinque discordanti; McNemar esatto a due code `p=1,0`. Il campione controllato
non è una popolazione casuale e il p-value resta diagnostico.

## Cosa funziona

- Il contratto JSON è valido in 76/76 chiamate structured, contro 62/76 del
  legacy.
- Structured non produce alcuna falsa supersessione nei 46 casi `keep_both`
  dei due insiemi.
- Zero supersessioni nei casi target/requisito/previsione contro risultato.
- Zero supersessioni nelle identità insufficienti.
- Le cinque sentinelle sono deterministiche in entrambi i bracci.

Quindi la scomposizione semantica è utile come strumento diagnostico e rende
esplicita la base della decisione. Non è però sufficiente come autorità.

## Perché fallisce

La supersessione richiede cinque requisiti logici derivati da sei campi:
stessa entità, stesso claim, due tipi noti e concordi, esclusione reciproca e
sostituzione esplicita. `useful_comparison`, il settimo campo del contratto,
appartiene soltanto al ramo `comparison`. Il modello riconosce spesso l'entità
e il claim ma nega uno fra accordo di tipo, esclusione reciproca o sostituzione
esplicita anche quando il testo rappresenta un aggiornamento reale. La policy
deterministica, correttamente fail-closed, conserva allora entrambe le memorie.

L'effetto desiderato — eliminare le false supersessioni — viene ottenuto
scendendo dal `83–88%` al `50%` di recall sugli aggiornamenti veri. Questo
fallisce sia i gate di regressione sia quelli del challenge.

Il risultato non riabilita il refuso del parser legacy e non dimostra che la
pipeline legacy sia ottimale. Dimostra una cosa più stretta: la congiunzione
dei cinque requisiti è troppo severa su questo banco e perde aggiornamenti che
Euri deve riconoscere.

## Addendum campo-per-campo post-hoc

Questa analisi è stata eseguita dopo aver visto i risultati. Genera ipotesi,
non modifica il verdetto e non costituisce validazione.

Sulle 20 supersessioni vere, `entity_relation` e `claim_relation` sono corretti
20/20; i tipi sono noti 20/20 e concordi 17/20; `explicit_replacement=yes`
compare 17/20. Il collo di bottiglia è `mutually_exclusive=yes`, presente
soltanto 13/20: 10/10 per `current_state/current_state`, 3/3 per
`measurement/measurement`, ma 0/7 quando compare un target o i tipi divergono.
Sette dei dieci falsi negativi falliscono una sola clausola.

Sette casi dichiarano insieme `explicit_replacement=yes` e
`mutually_exclusive=no`; sono tutti supersessioni vere e nessuno dei 46
`keep_both` dichiara una sostituzione esplicita. Non è necessariamente
un'incoerenza: due target storici possono essere entrambi veri nel tempo,
mentre uno solo resta operativamente valido.

I falsi negativi si dividono post-hoc in due percorsi disgiunti:

1. stessa entità + stesso claim + sostituzione esplicita, senza richiedere
   accordo di tipo o esclusione reciproca: recupera 7/10;
2. stessa entità + stesso claim + stesso tipo noto + esclusione reciproca, in
   assenza di sostituzione esplicita: recupera i restanti 3/10 ma introdurrebbe
   una falsa supersessione su `v2_amb_01`.

Sul banco ormai aperto l'unione raggiungerebbe 20/20 aggiornamenti veri e una
falsa supersessione. È un tetto fittato sugli stessi failure, non una stima di
prestazione. Un eventuale v3 richiede un challenge nuovo e resta bloccato dalla
validazione opportunity-first di Loop 2h.

## Decisione

1. `_llm_classify_pair` e il Loop 2f runtime restano sul classificatore legacy.
2. `_llm_assess_pair`, la policy pura, il challenge e l'harness rimangono nel
   repository come risultato negativo riproducibile e base per un eventuale
   v3.
3. Nessuna memoria viene migrata, riscritta o rivalutata.
4. Loop 2h resta invariato e ancora non validato come rete di sicurezza.

Un eventuale v3 dovrà essere preregistrato su un nuovo challenge. La pista
emersa dai failure non è aggiungere altri campi, ma separare i due percorsi
descritti nell'addendum. Questa è un'ipotesi post-hoc, non un fix autorizzato
dai risultati presenti.

## Integrità e limiti

- commit eseguito: `5f5c4c05ed8c8c912384be548986ee6762bdec21`;
- protocollo: `b851139f7d79558dfe06e2be54ac0cfc4f44533324638136713dd63fb4c234ee`;
- modello: `qwen3.6:35b`;
- digest modello:
  `07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522`;
- risultati:
  `audit_output/loop2f_structured_v2_validation/results.json`;
- analisi:
  `audit_output/loop2f_structured_v2_validation/analysis.json`.

Il challenge è development, costruito conoscendo i failure mode ma congelato
prima dell'implementazione. La regressione v1 era già aperta. Il risultato
vale per questo modello locale e non costituisce una stima di generalizzazione
sulla memoria personale.
