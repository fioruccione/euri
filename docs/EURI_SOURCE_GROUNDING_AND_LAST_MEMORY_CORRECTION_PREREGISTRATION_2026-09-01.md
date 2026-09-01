# Euri — preregistrazione grounding sorgente e correzione dell'ultima memoria

**Data:** 1 settembre 2026

**Stato iniziale:** preregistrato prima della modifica runtime

**Esito:** trattamento completato; manifest unitario 92/92 in 79,4 secondi

## 1. Casi organici congelati

### CORR-02 — correzione dell'ultima memoria

Turno owner, frame schema 9, confidence 1.0, `CHAT` con
`speech_acts=[CORRECT_FACT]`:

> Euri, una precisazione, no, sull'ultima memoria la vite bimetallica è di
> serie solo sulla Yizumi, mentre sulla Changzong è un extra da 27.000€.

Il frame contiene due fatti asserted/reusable e chiede retrieval sul confronto.
Il turno è però rimasto CHAT. Il correction signal `72d49f84` è stato poi
chiuso `dismissed/not_a_correction`; la memoria esplicita `dd2b94b5` è rimasta
attiva con l'affermazione falsa che entrambe le macchine abbiano la vite
bimetallica.

### SRC-01 — conclusioni da una sorgente mai letta

Turno STT: `Euri analizzata da Clipboard.` Il router e il frame lo hanno
classificato CHAT/INFORM; nessun Executor è partito. La risposta ha dichiarato
`Ho analizzato il testo...` e ha prodotto un preventivo Bini inesistente di
2357 caratteri. Il commit `76a3da8` ha successivamente esteso il guard
atto-parola ai verbi di lettura, ma il trattamento corrente elimina soltanto la
frase che rivendica l'azione: le conclusioni fabbricate nelle frasi successive
possono sopravvivere.

## 2. Ipotesi preregistrate

H1. Una correzione fattuale che nomina esplicitamente `ultima memoria` è già un
atto di modifica mnemonica sufficientemente grounded. Lasciarla al Loop 2g
perde sia il bersaglio sia l'autorità espressa dall'owner.

H2. Il frame contiene abbastanza evidenza per autorizzare la route SAVE, ma la
route non deve essere aperta da `CORRECT_FACT` da solo: correzioni ordinarie e
precisazioni conversazionali devono restare CHAT finché non nominano la memoria
da correggere.

H3. Quando una risposta rivendica una lettura/analisi non eseguita, tutte le
conclusioni che seguono nello stesso draft sono epistemicamente dipendenti da
quella sorgente assente. Conservare le frasi successive è meno sicuro che
scartare l'intero draft.

## 3. Trattamenti congelati

1. Il contratto semantico condiviso voce/UI riconosce una correzione della
   memoria soltanto se il frame è affidabile, contiene `CORRECT_FACT` o
   `CORRECT_ENTITY`, contiene almeno un fatto durevole e il testo nomina
   esplicitamente l'ultima/precedente memoria o il ricordo appena salvato.
2. In quel solo caso CHAT viene arbitrato a SAVE_MEMORY. Il save service forza
   l'operazione `correct`, usa il resolver bounded esistente e mantiene il suo
   comportamento fail-closed: bersaglio ambiguo → domanda all'owner, nessun
   supersede.
3. `CORRECT_FACT` senza referente mnemonico esplicito resta CHAT e non acquista
   autorità di scrittura.
4. Un claim non supportato di lettura, consultazione, esame o analisi elimina
   l'intero draft dipendente. Se il recupero read-only riesce, la risposta viene
   invece rigenerata dall'esito verificato del tool come oggi.
5. Nessun cambiamento al gate del thinking documentale: il risultato di una
   sola ablazione resta osservazione e richiede un secondo banco preregistrato.

## 4. Test congelati

- C1: il frame organico `sull'ultima memoria` arbitra CHAT → SAVE_MEMORY.
- C2: una normale precisazione fattuale senza referente mnemonico resta CHAT.
- C3: anche se il resolver semantico propone `operation=add`, il contratto
  esplicito dell'ultima memoria forza `correct` e usa il link atomico esistente.
- C4: il draft Bini con `Ho analizzato...` perde sia il claim sia tutte le
  specifiche/prezzi inventati quando nessun tool ha agito.
- C5: una risposta tecnica senza claim di accesso sorgente resta invariata.
- C6: regressioni correction resolver, semantic turn, act-word, SAVE e workspace
  documentale tutte verdi.

## 5. Riparazione organica

La riparazione dati deve essere append-only e idempotente: backup integrali dei
nodi coinvolti, nuova memoria user corretta, `correction_of` e `superseded_by`
collegati atomicamente, raw turn invariato. La reflection derivata
`b0d5bd4a` non viene cancellata: resta auditabile e viene ritirata o marcata
derivata da premessa superseded secondo le primitive già disponibili.

## 6. Esito del trattamento

I test C1-C5 sono stati osservati rossi prima della modifica e verdi dopo il
trattamento. La route è condivisa da `arbitrate_routable_intent`; voce e UI
passano lo stesso frame al save service. Il caso SRC-01 era già parzialmente
contenuto dal commit `76a3da8`, ma la nuova regressione organica ha dimostrato
che le conclusioni successive sopravvivevano: ora l'intero draft dipendente
viene ritirato.

La riparazione `scripts/repair_20260901_press_offer_correction.py` ha creato la
memoria user `3f76e2d3-2357-4626-93ec-239636268495`, collegata atomicamente a
`dd2b94b5`. La reflection `b0d5bd4a` è stata soft-deleted verso la versione
corretta; il signal `72d49f84` è `resolved/explicit_fact_correction`. Backup,
raw turn e quarantena Markdown preservano la reversibilità.

Il gate del thinking documentale non è stato modificato.
