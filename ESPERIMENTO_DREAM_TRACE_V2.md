# Dream Trace Paired V2 — pre-registrazione proposta

> **Stato storico: chiuso il 6 agosto 2026.** I record
> `dream_trace_paired_v2` non devono essere mescolati con il successore
> [`ESPERIMENTO_DREAM_TRACE_V3_HYDRATED.md`](ESPERIMENTO_DREAM_TRACE_V3_HYDRATED.md),
> che introduce contesto verbatim bounded in entrambi i bracci.

**Stato storico:** pilot v1 chiuso per deviazione; protocollo v2 archiviato e
sostituito dalla v3 reidratata prima di completare il batch pianificato.
**Versione:** `dream_trace_paired_v2`.
**Unità primaria:** la COPPIA (stesso seme, due condizioni), non il candidate isolato.

## Storia della revisione (perché questa non è la prima V2)

Una prima versione V2 usava bracci concorrenti a blocchi di due cicli, ciascuno con
una coppia di domini estratta a caso — corretta rispetto al bug di troncamento e alla
sopravvivenza asimmetrica del batch di maggio/luglio, ma non rispetto a un problema
più a monte: baseline e trattamento non condividevano MAI lo stesso seme, quindi ogni
differenza osservata confondeva l'effetto del residuo con quanto due domini estratti
a caso siano "connettibili" — variabilità già misurata come dominante nell'analisi di
anisotropia (μ coppie ~0.82). Su indicazione di Stefano (21/07), il disegno cambia da
gruppi indipendenti a **coppie appaiate sullo stesso seme**: elimina quella variabilità
alla radice invece di mediarla su n grande, e non richiede più bilanciamento a blocchi
(ogni coppia contiene per costruzione un baseline e un trattamento, sempre).

## Intervento

Per ogni ciclo creativo eleggibile, **UNA sola estrazione del seme** (due memorie da
domini diversi, stesso meccanismo di sempre — `_pick_dream_seed`), generata **due
volte** con lo stesso prompt di base:

- **Baseline:** nessuna sezione di traccia nel prompt.
- **Trattamento:** la stessa sezione di traccia (residuo dell'ultimo ciclo scartato)
  che userebbe il meccanismo esistente, iniettata identica a com'è oggi.

Il residuo che alimenta il trattamento **non è mai aggiornato dal lato baseline**:
si distilla solo dal CoT del lato trattamento (o dall'unico lato generato durante il
warm-up). Questo tiene il lato baseline strutturalmente isolato dall'esperimento — il
suo prompt è bit-identico a quello che gira oggi a flag spento.

- **Warm-up:** il primo ciclo eleggibile, quando non esiste ancora un residuo, genera
  UNA volta sola (senza confronto possibile), semina il primo residuo e non entra nel
  registro delle coppie. Può ripetersi ogni volta che il residuo è scaduto per
  inattività (TTL) oppure la distillazione precedente è stata rifiutata: non è un
  evento singolo.
- Pairing domini, modello, temperatura (0.6), prompt base, seed gate e lifecycle
  restano identici a oggi. Durante la raccolta non si modificano questi componenti.

## Cosa entra nella memoria reale di Euri (punto isolato in revisione)

Le due generazioni di una coppia sono scritte per intero nello stream sperimentale
(vedi sotto), ma **solo il lato baseline diventa un `euri:insight:*` vivo** —
embedding, ingresso in retrieval, eleggibile a convergenza e promozione. Il lato
trattamento, finché la raccolta è in corso, resta strumentazione pura: il suo testo
è integrale e verificabile nello stream, ma non esiste come nodo di memoria.

**Perché non entrambi.** Un ciclo produrrebbe altrimenti due candidate reali dallo
stesso seme, raddoppiando il carico su Loop 2c e la RAG rispetto a oggi, e — più
grave — lascerebbe che un meccanismo non ancora validato (l'iniezione del residuo)
plasmi la memoria reale di Euri prima che l'esperimento dica se produce connessioni
migliori o solo confabulazioni più elaborate. Il gate di promozione è quasi cieco al
contenuto (convergenza-per-ripetizione): un candidate trattamento scadente potrebbe
essere promosso prima che qualcuno lo legga.

**Perché il baseline e non il trattamento.** Il baseline è generazione bit-identica
a quella che gira oggi a flag spento — "cosa entrerebbe comunque nella memoria di
Euri se questo esperimento non esistesse". La raccolta quindi non cambia il
comportamento reale del sistema durante l'esperimento, aggiunge solo una generazione
ombra per il confronto. Se l'esperimento conclude che il trattamento è meglio, la
decisione di rendere il residuo parte del comportamento reale resta comunque
successiva ed esplicita di Stefano, non un effetto collaterale della raccolta dati.

## Registro primario

Ogni singola generazione (due per coppia) viene scritta immediatamente nello stream
dedicato `euri:dream_trace:paired:cycles`, indipendente dalla promozione o
sopravvivenza del candidate. Il record contiene integralmente:

- `pair_id` (contatore atomico, condiviso dalle due generazioni della stessa coppia)
  e `arm` (`baseline`/`trattamento`);
- output del modello, lunghezza e SHA-256 — verificati per QUALUNQUE stato, non
  solo `candidate`: uno scarto con output corrotto o troncato deve poter emergere
  dal controllo hash tanto quanto un candidate, non solo essere ignorato perché
  "tanto non c'è nulla da giudicare";
- entrambe le memorie sorgente, domini e SHA-256;
- il residuo REALMENTE iniettato in QUESTO lato (vuoto per il baseline, il testo per
  il trattamento — mai il residuo "in generale", che apparirebbe anche sul baseline
  pur non essendo mai entrato nel suo prompt) e SHA-256;
- se questo lato è diventato un insight vivo (`insight_persisted`, vedi sopra: solo
  il baseline);
- durata della generazione in secondi;
- stato `candidate`/`discarded`/`error`.

L'export fallisce se manca un campo, un hash non coincide, o una coppia non ha
entrambi i lati completi, non-`error` e non duplicati (un lato con lo stesso
`pair_id`+`arm` visto due volte invalida l'intera coppia — non si tiene
silenziosamente la prima occorrenza). Nessun recupero post-hoc dai documenti vivi
è ammesso — la lezione del batch di luglio (troncamento a 600 caratteri scoperto
dopo la raccolta) resta la ragione di questo vincolo.

**Errori di generazione per braccio vanno confrontati, non solo esclusi.** Una
coppia con un lato in errore è esclusa dal conteggio (non è misurabile), ma il
TASSO di errore per braccio va comunque riportato: se il trattamento fallisce
sistematicamente più spesso del baseline (es. prompt più lungo che va in timeout),
è un effetto reale e negativo del residuo che sparirebbe silenziosamente se si
guardassero solo le coppie riuscite.

## Scarto come esito, non come esclusione

**Punto critico, diverso dal disegno a blocchi precedente.** Una coppia entra nel
conteggio anche se un lato (o entrambi) produce `discarded` (nessun isomorfismo o
formato incompleto) — non solo quando entrambi i lati sono `candidate`. Il motivo:
se il residuo cambia il TASSO di scarto (es. il trattamento scarta più spesso perché
evita di ripetere un tipo di ponte debole, o meno spesso perché lo guida verso un
formato valido), filtrare solo le coppie "doppio candidate" nasconderebbe proprio
l'effetto che si sta misurando — la stessa famiglia di errore della sopravvivenza
asimmetrica del batch precedente, in una forma diversa. Un lato `discarded` conta
come **non-passa** per costruzione in quel lato, senza bisogno di lettura cieca (non
c'è un ponte da giudicare). Solo i lati `candidate` producono testo da mandare ai
valutatori.

## Valutazione tecnica cieca — primaria

Per ogni coppia con almeno un lato `candidate`, il testo di quel lato entra nel file
cieco mescolato con tutti gli altri (nessuna informazione su braccio o pairing). Due
valutatori indipendenti (Codex e Claude, in sessioni senza visibilità sui giudizi
reciproci — vedi nota sotto) leggono memorie originali e candidate, senza conoscere
il braccio né sapere che gli item sono in coppia, e assegnano tre dimensioni:

1. **Grounding:** `G2` fondato; `G1` ipotesi incompleta; `G0` inventato o contraddetto.
2. **Novità:** `V2` non ovvio; `V1` incrementale; `V0` ovvio o analogia decorativa.
3. **Chiarezza:** `C` chiaro; `A` ambiguo/non giudicabile.

Il **pass** di un lato è: `discarded` → non-passa automaticamente; `candidate` →
passa solo se `G2 + V2 + C` su ENTRAMBI i valutatori. **Un disaccordo tra i due
valutatori su una qualunque delle tre dimensioni classifica quel lato come
AMBIGUO — non passa, e non c'è adjudicazione**: nessuno dei due modelli rilegge
per correggere o convincere l'altro, e Stefano non arbitra (coerente con quanto
già stabilito: il disaccordo è una misura dell'ambiguità del meccanismo di
giudizio, non un problema da risolvere a ogni costo). Il TASSO di ambiguità va
riportato: se alto, la misura non è affidabile anche se il delta tra bracci
sembra netto.

**Nota sull'indipendenza dei due valutatori (aperta, non risolta qui):** Codex e
Claude restano entrambi modelli linguistici, potenzialmente con punti ciechi
condivisi (es. sensibilità al template a tre righe più che al contenuto); Codex è
inoltre l'architetto del meccanismo testato e ha già espresso un'aspettativa
direzionale sui risultati del batch recuperato del 21/07. Il doppio consenso filtra
il rumore idiosincratico, non un bias sistematico condiviso. Un controllo a campione
da un terzo valutatore umano resta un miglioramento possibile, non incluso qui.

## Valutazione di Stefano — secondaria e contestuale

Solo sui candidate che hanno già superato il pass tecnico, Stefano assegna, anche in
momenti diversi:

- `U_NOW`: utile e applicabile nel contesto attuale;
- `U_LATER`: plausibilmente utile, ma non ora;
- `U_NO`: non utile per il suo lavoro;
- `U_CONTEXT`: mancano informazioni o lucidità per decidere ora.

`U_LATER` non conta come fallimento e `U_CONTEXT` non viene forzato in sì/no.
L'utilità non decide da sola l'esito causale dell'esperimento.

## Analisi ed esito — a livello di coppia

Per ogni coppia completa, l'esito è categorico: entrambi passano, nessuno dei due
passa, solo il trattamento passa, solo il baseline passa. Le ultime due categorie
(le uniche informative) alimentano un test di McNemar: `b` = coppie dove passa solo
il trattamento, `c` = coppie dove passa solo il baseline.

**Test esatto (binomiale), non l'approssimazione χ².** Con un obiettivo di 50 coppie,
`b+c` sarà quasi certamente piccolo (regola empirica: sotto ~25 discordanti
l'approssimazione χ² con `(b-c)²/(b+c)` è inaffidabile). Il test primario è quindi
il McNemar esatto: sotto l'ipotesi nulla `b ~ Binomiale(b+c, 0.5)`, si calcola il
p-value a due code dalla distribuzione binomiale esatta. L'approssimazione χ² può
essere riportata come riferimento secondario solo se `b+c ≥ 25`.

Le coppie concordanti (entrambi passano o nessuno passa) non contribuiscono al test
ma vanno riportate: se sono la quasi totalità, il segnale è debole indipendentemente
dal p-value su `b`/`c`.

## Numerosità e criteri

**Nessun calcolo di potenza precompilato**: non abbiamo dati pregressi sul tasso di
discordanza per QUESTO disegno appaiato (il batch precedente era a gruppi
indipendenti, non confrontabile direttamente). Si parte con un obiettivo pilota:

1. **50 coppie complete** (100 generazioni totali, warm-up esclusi).
2. A quel punto: calcolare `b`, `c`, il test di McNemar e la quota di coppie
   concordanti. Se `b+c` è troppo piccolo (es. <10) per dire qualunque cosa, si
   raccoglie un secondo batch di pari dimensione prima di concludere — stesso
   spirito del "5-15pp indicazione debole" della pre-registrazione precedente,
   applicato qui alla numerosità dei discordanti invece che al delta.
3. Ambiguità (`A`, cioè disaccordo tra i due valutatori) sopra il 15% in un braccio,
   o accordo tra valutatori basso: la misura si ferma e si analizza, senza
   trasformare l'incertezza in un esito.
4. Tasso di errore di generazione molto diverso tra i due bracci (rapporto ≥2x in
   una direzione o l'altra): si ferma e si indaga prima di continuare — potrebbe
   essere un effetto reale del residuo (es. prompt più lungo che va in timeout),
   non rumore da ignorare.
5. Coppie escluse per lato duplicato: se sono una quota non trascurabile del totale
   (indicativamente >5%), è un sintomo di un problema a monte (retry, crash) da
   correggere prima di fidarsi del resto del batch.

## Misure secondarie

- Tasso di `discarded`/`candidate` per braccio, separatamente (è già parte
  dell'esito primario per come è definito sopra, ma va riportato anche da solo:
  un residuo che riduce drasticamente gli scarti senza cambiare la qualità dei
  candidate superstiti è un risultato diverso da uno che aumenta la qualità a
  parità di tasso di scarto).
- Distribuzioni complete G/V/C per lato, non solo il pass binario.
- Utilità `U_NOW/U_LATER/U_NO/U_CONTEXT` di Stefano sui soli candidate passati.
- Lunghezza e tempo di generazione per lato, per rilevare effetti collaterali del
  residuo sulla forma della risposta.

## Attivazione

Il codice resta dietro `DREAM_TRACE_PAIRED_ENABLED`; il processo è fermo e il nuovo
batch v2 non è ancora partito. Prima dell'avvio:

1. test unitari e prova su Redis di test;
2. verificare che le nuove chiavi versionate
   `euri:dream_trace:paired:dream_trace_paired_v2:*` siano assenti; lo stream storico
   condiviso può contenere v1 perché ogni record porta `experiment_version`;
3. verificare il flag a `True` e riavviare Euri;
4. non cambiare generazione, modello o seed gate fino al congelamento del batch.

Il vecchio flag `DREAM_TRACE_AB_V2_ENABLED` e il codice a blocchi che descriveva sono
stati rimossi (mai attivato, nessun dato raccolto sotto quel disegno) — sostituiti
interamente da quanto sopra.

## DEVIAZIONE DOCUMENTATA (22/07/2026) — eco della sentinella sbagliata nella distillazione

Prima notte di raccolta (21/07 17:40 → 22/07 08:31, riavvio con `DREAM_TRACE_PAIRED_
ENABLED=True`): 1 warm-up + 8 coppie reali generate correttamente (candidate 77→86,
sempre +1 per coppia mai +2 — il fix su "solo il baseline persiste" ha retto, verificato
sui log, nessuna memoria reale danneggiata: gli otto baseline sono normali memorie da
comportamento standard, i trattamenti sono rimasti ombra).

**Bug trovato leggendo i log, non dal codice:** su 9 distillazioni, 5 hanno scritto
letteralmente `NESSUN INSIGHT` come residuo (warm-up + coppie #1, #4, #5, #6). Per lo
sfalsamento di un ciclo, la sentinella è stata poi INIETTATA nei trattamenti delle
coppie #1, #2, #5, #6 e #7. Causa in
`core/dream_engine.py` (`_update_dream_trace`): il controllo cercava solo la sentinella
esatta `NIENTE DA SEGNALARE` (propria del prompt di distillazione); il modello risponde a
volte con `NESSUN INSIGHT` (la sentinella dell'ALTRO prompt, quello della generazione
principale) — confusione tra i due compiti, non intercettata. Per più di metà delle
coppie della notte, il lato trattamento ha quindi ricevuto un'istruzione "evita di
ripercorrere la strategia 'NESSUN INSIGHT'" che non significa nulla.

**Osservazione secondaria, più debole:** nella coppia #8 (produzione×controllo qualità)
la prima riga del residuo distillato ("ponte infrastrutturale generico (vibrazioni/EMI)")
ripete tematicamente il residuo della coppia #7 (manutenzione×telecomunicazioni, dove
vibrazioni/EMI erano davvero pertinenti), nonostante l'istruzione esplicita di ignorare
la traccia iniettata — la stessa famiglia di eco a punto fisso diagnosticata il 13/07,
qui in forma parziale (2 righe su 3 restavano pertinenti al proprio ciclo). Un solo
caso osservato: da riguardare dopo il fix, non ancora un pattern confermato.

**Fix v2 applicato:** il residuo accetta soltanto righe strutturate nella forma
`ho <tentativo>: debole perché <ragione>`, scartando qualunque risposta non conforme,
inclusa `NESSUN INSIGHT`. Le righe con forte sovrapposizione tematica deterministica
rispetto al residuo appena iniettato vengono escluse come eco; le altre righe valide
restano. Se non rimane alcuna riga, la chiave del residuo appaiato viene eliminata e il
ciclo successivo torna a warm-up: nessun residuo vecchio viene riutilizzato. Il legacy
mantiene invece la sua semantica precedente. Test di regressione in
`test_dream_trace_paired.py` coprono sentinella, residuo stale, righe miste ed eco reale
vibrazioni/EMI.

**Decisione (Codex + Claude, confermata da Stefano): correggere e riavviare
formalmente**, non escludere le 5 coppie difettose e proseguire — con più di metà del
piccolo batch iniziale già compromesso, il resto non è comunque un campione di cui
fidarsi senza controllo ulteriore. I dati v1 della prima notte NON vengono cancellati
da Redis e restano come prova diagnostica. Il nuovo batch usa
`experiment_version=dream_trace_paired_v2`, con residuo e contatore in chiavi Redis
versionate: il campionatore seleziona v2 e ignora v1 senza affidarsi a un timestamp
manuale. Il primo ciclo v2 è quindi necessariamente un warm-up pulito.
