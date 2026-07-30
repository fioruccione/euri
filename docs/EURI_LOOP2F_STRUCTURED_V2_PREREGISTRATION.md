# Loop 2f structured v2 — preregistrazione development

Data di congelamento: 30/07/2026, prima dell'implementazione del nuovo
classificatore.

## 1. Domanda

Una decisione strutturata e asimmetrica riduce le false supersessioni senza
perdere gli aggiornamenti reali rispetto alla pipeline 2f distribuita?

La baseline non è la reinterpretazione semantica del refuso: è il comportamento
effettivo di produzione, incluso il fallback `output non riconosciuto → none`.

## 2. Regola congelata

La supersessione richiede contemporaneamente:

1. stessa entità specifica: `yes`;
2. stesso claim/proprietà: `yes`;
3. stesso tipo di asserzione, noto;
4. valori o stati mutuamente esclusivi: `yes`;
5. evidenza testuale esplicita che una voce corregga, sostituisca, annulli o
   renda non più valida l'altra: `yes`.

Qualunque `no`, `unknown`, campo mancante, JSON non valido o tipo diverso
produce `keep_both`.

I tipi ammessi sono `current_state`, `measurement`, `target`, `requirement`,
`prediction`, `preference`, `other` e `unknown`.

Target, requisito, previsione e misura/risultato possono riferirsi alla stessa
entità e proprietà senza essere versioni concorrenti dello stesso fatto. Il
tipo non viene riconosciuto con regex o liste di dominio: è parte del giudizio
semantico strutturato. La policy deterministica consuma il giudizio, ma non
inventa campi mancanti.

`comparison` è permesso soltanto quando l'identità è esplicitamente `different`
e il modello dichiara una somiglianza tecnica concreta. In ogni altro caso
non-supersession l'azione è `none/keep_both`.

## 3. Bracci

- **A — deployed legacy**: `_llm_classify_pair_legacy`, una parola e fallback
  `none`;
- **B — structured v2**: JSON strutturato più policy affermativa descritta
  sopra.

Stessi testi, stesso modello, temperatura zero, ordine controbilanciato. I gold
non entrano nei prompt.

## 4. Insiemi e gate

### Regressione aperta

I 36 casi primari di `loop2fh-controlled-it-v1`. Sono ormai development set e
servono soltanto a impedire regressioni note.

- accuratezza B ≥ 34/36;
- false supersession B ≤ 1/24;
- true-supersession recall B ≥ 11/12;
- zero supersessioni nelle sei coppie target-risultato.

### Challenge congelato v2

`benchmarks/euri_memory/fixtures/loop2f_structured_v2_challenge.json`, scritto
e committato prima del codice:

- 8 aggiornamenti espliciti;
- 8 target/requisiti/previsioni contro risultati;
- 5 entità distinte;
- 5 claim complementari sulla stessa entità;
- 4 identità insufficienti.

Cinque sentinelle, una per strato, sono replicate tre volte. Le repliche
misurano determinismo, non aumentano `n` indipendente.

Gate development:

- accuratezza B ≥ 90%;
- false supersession rate B ≤ 5%;
- true-supersession recall B ≥ 80%;
- zero false supersession su target-risultato e identità insufficienti;
- contratto JSON valido ≥ 95%;
- B non peggiore di A nel confronto appaiato complessivo.

Se il challenge fallisce un gate distruttivo, structured v2 non diventa
autorità runtime.

## 5. Analisi

Vengono riportati accuratezza e azione per strato, true/false supersession,
output malformati, stabilità, McNemar esatto, campi strutturati e rifiuti
fail-closed. Intervalli e p-value restano diagnostici: i casi controllati non
sono un campione casuale della memoria personale.

## 6. Loop 2h

2h non viene modificato in questo intervento. Il suo valore incrementale resta
non verificato per assenza di opportunità, e `0/6 UNKNOWN` sui casi ambigui
impedisce di usarlo per rendere più aggressivo 2f.

Il prossimo protocollo di 2h dovrà campionare coppie che 2f ha realmente
classificato `contradiction`, congelando prima i gold di identità.

## 7. Interpretazione consentita

Superare i gate autorizza l'attivazione development della policy strutturata
perché non regredisce sul banco controllato. Non dimostra generalizzazione alle
memorie personali né accuratezza su una popolazione indipendente.

## 8. Sigillo di esecuzione

Compilato dopo l'implementazione e prima di qualunque chiamata reale:

- protocol SHA-256:
  `b851139f7d79558dfe06e2be54ac0cfc4f44533324638136713dd63fb4c234ee`;
- fixture v1:
  `b0b04202253282779cbc092d853dddbeb380a960e761b7f03f49d3b3922b04cc`;
- challenge v2:
  `2309caf1b31c89e83cf3c994bf0aff7132d72bad4d63463a10a39b32ae853f13`;
- legacy classifier:
  `7b6846052ba0214259ed26afe8e24fef63cca27ba71ca707b7cacc01d94e72b0`;
- structured classifier:
  `890ba95e583e4826facb1a5de1529453f1efe4d147dd81e55508a0c180f170b9`;
- policy pura:
  `8dbb36b0cab8510cb8e805fd4aafe036fc20e645f133f9ccc799384142bd2d1e`.

Il dry-run prevede 66 casi indipendenti, 76 osservazioni e 152 chiamate:
37 ordini `legacy→structured` e 39 `structured→legacy`. Nessun accesso Redis.
