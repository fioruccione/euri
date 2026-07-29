# Verifica controllata Loop 2f / Loop 2h — risultati 29/07/2026

## Esito in breve

La prima verifica appaiata dei due operatori è stata completata sul protocollo
`loop2fh-controlled-it-v1`: 42 coppie, 60 osservazioni e 120 chiamate locali
con `qwen3.6:35b`. Il run non ha aperto Redis e non ha modificato memorie.

Il verdetto preregistrato è:

| Operatore | Verdetto | Significato consentito |
|---|---|---|
| Loop 2f | `GO_DEV` | buona classificazione dell'azione sui casi controllati |
| valore incrementale Loop 2h | `INCONCLUSIVE_DEV_NO_OPPORTUNITY` | nessuna prova che 2h migliori l'azione di 2f |

Questo non equivale a una validazione scientifica sul sistema vivo. Il corpus è
un development set controllato, scritto conoscendo l'architettura.

## Integrità del run

- fixture SHA-256:
  `b0b04202253282779cbc092d853dddbeb380a960e761b7f03f49d3b3922b04cc`;
- protocol SHA-256:
  `4f9162b49e54b201864529f44b26a0f1662413e9e2a7a04d460e4fa34cc65ba8`;
- commit:
  `90e2b2e975467d0f9989498e5bc8ee824ec999a2`;
- modello:
  `qwen3.6:35b`;
- digest modello:
  `07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522`;
- 60/60 osservazioni complete, 120/120 classificazioni;
- nessun accesso Redis e nessun early stop sulle metriche.

La prima partenza parziale è rimasta esclusa. Aveva soltanto rivelato il difetto
di contratto descritto nell'emendamento alla preregistrazione.

## Loop 2f

Sui 36 casi primari:

| Metrica | Risultato |
|---|---:|
| accuratezza azione | 34/36 = **94,4%** |
| false supersession | 1/24 = **4,2%** |
| richiamo delle supersessioni vere | 11/12 = **91,7%** |
| stabilità sulle 9 sentinelle | 9/9 = **100%** |

I due errori sono informativi:

1. `sup_02`: il nuovo indirizzo del server Orion doveva soppiantare quello
   precedente. Il modello ha scritto `CONTRADIZIONE`, ma il parser cerca
   `CONTRADD` e ha degradato l'output a `none`;
2. `target_02`: il target di scarto della linea Q e lo scarto misurato nel
   pilot sono stati trattati come due fatti incompatibili. 2f ha quindi
   nascosto il target, benché target e risultato debbano coesistere.

La prima diagnosi è dunque: 2f distingue molto bene entità diverse, claim
complementari e aggiornamenti reali nel campione, ma non possiede ancora una
distinzione affidabile fra valore-obiettivo e valore-osservato.

## Il risultato fragile nascosto dal parser

2f ha prodotto sette output fuori contratto su 60 chiamate (**11,7%**), tutti
con lo stesso hash, corrispondente a `CONTRADIZIONE` scritto con una sola `D`.
Le violazioni riguardano cinque casi unici:

- un aggiornamento reale (`sup_02`);
- tre coppie target-risultato (`target_01`, `target_03`, `target_04`);
- un caso ambiguo (`amb_05`);
- `target_01` compare tre volte perché è una sentinella di stabilità.

Il parser fragile ha un effetto misto: causa il falso negativo di `sup_02`, ma
salva accidentalmente tre casi target-risultato che il giudizio semantico del
modello avrebbe chiamato contraddizioni.

Come sola diagnosi post-hoc, non come nuovo verdetto preregistrato: se si
rendesse il parser tollerante al refuso senza cambiare il giudizio, l'accuratezza
primaria diventerebbe 32/36 (**88,9%**) e le false supersessioni 4/24
(**16,7%**). Il `GO_DEV` verrebbe perso. Perciò non va corretto soltanto il
parser: prima occorre rendere esplicita nel classificatore la distinzione
target/risultato.

## Loop 2h

2f ha classificato correttamente tutti i 12 casi con entità distinte. Non ha
quindi creato nessuna falsa supersessione cross-entità sulla quale 2h potesse
intervenire:

| Metrica | Risultato |
|---|---:|
| opportunità di correzione | **0** |
| azioni migliorate da 2h | **0** |
| azioni peggiorate da 2h | **0** |
| stabilità azione composta | 9/9 = **100%** |

Il significato è stretto: il test non dimostra che 2h sia inutile; dimostra che
in questo campione non è stato possibile misurarne il ruolo di rete di
sicurezza. Il braccio `2f+2h` è infatti identico a `2f`: 34/36 in entrambi.

La classificazione semantica separata di 2h è corretta in 29/42 casi
(**69,0%**). Sui soli 36 casi primari è 29/36 (**80,6%**), ma questo dato
post-hoc nasconde il problema più importante: sui sei casi costruiti apposta
con identità non risolvibile, 2h non ha mai scelto `UNKNOWN`.

| Output 2h sui 6 casi ambigui | Conteggio |
|---|---:|
| `SAME` | 4 |
| `RELATED` | 1 |
| `DIFFERENT` | 1 |
| `UNKNOWN` | 0 |

2h è quindi stabile, ma troppo disposto a risolvere l'identità quando le
descrizioni non contengono abbastanza identificatori. Stabilità non significa
prudenza.

Un secondo finding riguarda i claim complementari sulla stessa entità: 2h li
chiama spesso `RELATED` anziché `SAME` (5/6 non-`SAME`). In un percorso di
riparazione ciò potrebbe essere utile, perché `RELATED` riapre una
supersessione; come classificatore puro dell'identità, però, mostra che il
prompt mescola identità dell'entità ed evoluzione del claim.

## Decisione architetturale suggerita

1. **Non rimuovere 2f.** Ha un segnale operativo forte e gli errori sono
   circoscritti, ma il suo `GO_DEV` va accompagnato dall'avvertenza sul
   contratto fragile.
2. **Non correggere isolatamente `CONTRADD` nel parser.** Una correzione
   sintattica da sola renderebbe più distruttivo l'errore target-risultato.
   Servono insieme un output strutturato e una classe/guardia esplicita per
   target, misura, previsione e risultato.
3. **Tenere 2h come controllo reversibile, non dichiararlo validato.** Il test
   non ha misurato alcun beneficio incrementale. Il suo fail-closed va
   rafforzato sull'identità ambigua prima di attribuirgli autorità ulteriore.
4. **Il prossimo test di 2h deve partire dalle opportunità.** Va costruito un
   insieme cieco o congelato di coppie che 2f classifica realmente come
   `contradiction`, includendo omonimi e identificatori insufficienti; solo lì
   si può misurare quante false supersessioni 2h riapre e quante vere
   supersessioni danneggia.
5. **La qualità narrativa di 2h resta fuori dal verdetto.** Questa verifica ha
   misurato la relazione e l'azione, non se la reflection generata sia utile
   nelle risposte successive.

## Conclusione

Loop 2f merita di restare, ma non ancora di essere considerato robusto:
l'accuratezza osservata è alta e insieme dipende in parte da un errore di
parsing che oggi funge accidentalmente da freno.

Loop 2h non ha prodotto danno nel campione, ma non ha neppure avuto occasione
di produrre beneficio. Il suo problema misurato è la calibrazione
dell'incertezza: davanti a identità ambigue tende a scegliere una relazione
invece di ammettere `UNKNOWN`.

La verifica ha quindi separato i ruoli: 2f è già un classificatore operativo
utile ma incompleto; 2h è ancora una rete di sicurezza plausibile, non
dimostrata.
