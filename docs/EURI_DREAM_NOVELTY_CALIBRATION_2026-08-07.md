# Dream REM→wake — calibrazione prospettica della novità

**Data di congelamento:** 7 agosto 2026
**Ambito:** future valutazioni cieche di candidate prodotti da `rem_wake_v1` o
da suoi successori dichiarati
**Stato:** addendum prospettico; non modifica le etichette né gli esiti degli
esperimenti Dream Trace V2/V3 già chiusi

## Perché questo addendum esiste

La prima evidenza live REM→wake ha esposto una classe di confine che la vecchia
rubrica descriveva troppo poco. Una connessione può sorprendere l'utente perché
non era stata considerata prima e, nello stesso tempo, essere ottenibile con la
semplice composizione «applica il metodo A al problema B». Chiamare entrambe le
cose *non ovvie* mescola utilità soggettiva e novità tecnica, abbassando
l'accordo fra valutatori proprio sui casi più interessanti.

Questo documento congela prima di un futuro blind la distinzione operativa. La
qualità letteraria, la ricchezza associativa e il carattere inatteso del raw REM
non assegnano automaticamente novità al candidate del risveglio: si valuta ciò
che il risveglio ha distillato, confrontandolo con le fonti reali.

## Assi indipendenti

### Grounding

- **G2 — fondato:** premesse, meccanismo e dettagli operativi sono sostenuti
  dalle due fonti e dal loro contesto autorizzato.
- **G1 — ipotesi incompleta:** la connessione è plausibile, ma almeno un dettaglio
  necessario, un passaggio causale o una condizione operativa non è sostenuto.
- **G0 — inventato o contraddetto:** il candidate altera le fonti, attribuisce
  fatti assenti o dipende da una premessa incompatibile con esse.

### Novità tecnica

- **V0 — ovvio/decorativo:** ripete una fonte, giustappone due fatti senza un
  effetto nuovo, formula un obiettivo generico oppure aggiunge soltanto una
  metafora.
- **V1 — composizione incrementale:** applica direttamente un metodo, strumento
  o proprietà di A all'oggetto/problema B. Può essere utile e ben formulato, ma
  non introduce un nuovo meccanismo, vincolo, previsione o criterio di scelta.
- **V2 — connessione non riducibile alla composizione diretta:** richiede entrambe
  le fonti e introduce almeno uno fra:
  - un meccanismo cross-domain nuovo e fondato;
  - un vincolo che cambia la decisione o il modo di applicare A a B;
  - una previsione falsificabile derivata dall'interazione;
  - un criterio operativo che seleziona fra azioni concorrenti.

`V2` non significa «logicamente impossibile da dedurre»: un insight fondato deve
essere ricostruibile dalle premesse. Significa invece che non è riducibile a
restatement, congiunzione o sostituzione di slot.

### Chiarezza

- **C — chiaro:** il ponte e il suo effetto sono identificabili senza completare
  il testo al posto del candidate.
- **A — ambiguo/non giudicabile:** non è possibile stabilire quale meccanismo o
  effetto venga proposto.

### Utilità o sorpresa per Stefano

Resta un asse secondario e separato:

- `U_NOW`: utile e applicabile ora;
- `U_LATER`: plausibilmente utile in seguito;
- `U_NO`: non utile;
- `U_CONTEXT`: informazioni insufficienti per decidere.

«Non ci avevo pensato» può giustificare `U_NOW` o `U_LATER`, ma non trasforma
`V1` in `V2`. Viceversa, un `V2` può non essere utile nel contesto corrente.

## Sequenza decisionale per i valutatori

1. Valutare il grounding senza usare la sorpresa come evidenza.
2. Chiedere: il ponte si riassume fedelmente come «applica A a B»?
   - Se sì, il tetto ordinario è `V1`.
   - Può superarlo soltanto se esiste un ulteriore meccanismo, vincolo,
     previsione o criterio decisionale **fondato** nelle fonti.
3. Rimuovere mentalmente una fonte alla volta. Se il contributo nuovo sopravvive
   invariato, non è una vera interazione cross-domain e non è `V2`.
4. Verificare che l'elemento usato per rivendicare `V2` non sia proprio quello
   che ha causato `G1`: un'aggiunta non sostenuta non può riscattare una
   composizione diretta.
5. Assegnare chiarezza e, soltanto dopo il pass tecnico, raccogliere l'utilità
   contestuale di Stefano.

Il pass tecnico resta `G2 + V2 + C`. Un raw REM affascinante seguito da un
candidate `G1/V1/C` è evidenza di divergenza controllata e astensione corretta,
non un insight promosso.

## Caso di calibrazione congelato C-001 — clipboard × bozza clienti

**Provenienza live:** raw REM `be3fe5fe`, candidate `82d14bb6`. Questi ID e le
loro parafrasi non devono entrare in un futuro campione blind: sono materiale di
calibrazione già discusso.

**Fonte A:** esiste un flusso per passare documenti o testo dalla clipboard,
rianalizzare il contesto, rigenerare documenti e integrare ricerca web.

**Fonte B:** esiste una bozza destinata ai clienti, legata a comunicazioni sulle
nuove leggi e ancora cercata fra i documenti.

**Candidate:** usare il flusso clipboard–rianalisi–web per validare in tempo reale
la bozza contro le fonti normative più recenti, evitando riferimenti obsoleti e
allineando precisione tecnica e tono rassicurante.

**Etichetta concordata:** `G1 / V1 / C`.

Motivazione:

- «usare il flusso A sulla bozza B» è una composizione diretta, quindi `V1`;
- la validazione normativa *in tempo reale* è l'unico elemento che potrebbe
  sembrare un meccanismo aggiuntivo, ma non è sostenuto integralmente dalle
  fonti e determina `G1`;
- quell'aggiunta non fondata non può essere usata contemporaneamente per
  rivendicare `V2`.

Il raw REM resta qualitativamente interessante: ha preservato e trasformato la
topologia semantica dei due episodi. Questa osservazione riguarda la capacità
divergente, non cambia l'etichetta tecnica del risveglio.

## Procedura prima del prossimo blind

1. Costruire un piccolo set di calibrazione esterno al campione, includendo
   almeno un esempio concordato per `V0`, `V1`, `V2` e due casi-limite.
2. I valutatori discutono soltanto il set di calibrazione e congelano la rubrica.
3. Nel blind sono indicati come **Valutatore A** e **Valutatore B**. Nessuna
   identità personale, familiare o estranea al protocollo viene inferita come
   annotatrice.
4. Durante il blind non vedono i giudizi reciproci e non fanno adjudication.
5. Ogni disaccordo resta `AMBIGUO` e non-passa; si riporta separatamente il tasso
   di disaccordo per G, V e C.
6. Se l'ambiguità supera la soglia preregistrata, si ferma la misura: non si
   riscrive la rubrica guardando gli item sperimentali.

Qualunque futura modifica a queste definizioni richiede una nuova versione del
protocollo e un nuovo set di calibrazione; non si applica retroattivamente.
