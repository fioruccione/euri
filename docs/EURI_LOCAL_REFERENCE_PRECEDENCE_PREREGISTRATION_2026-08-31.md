# Preregistrazione — precedenza del referente conversazionale locale

Data di congelamento: 31/08/2026, prima dell'esecuzione del replay.

## Osservazione organica

Nel turno immediatamente precedente, Euri aveva descritto la configurazione
attuale della ICMA2: `bivite -> RAS500 -> pompa a ingranaggi -> taglio`.
Alla domanda «restiamo sulla configurazione che hai appena descritto», Euri ha
invece risposto usando la modifica proposta con FPP20 prima del RAS500.

Il prompt catturato mostra che lo storico completo era presente. Il test deve
distinguere fra incapacita' pragmatica del modello e interferenza del contesto
RAG durevole.

## Materiale congelato

- sorgente: `research_logs/prompt_capture/prompt-capture-2026-08-31.jsonl`;
- richiesta: l'evento `request` il cui ultimo messaggio utente coincide con
  «Euri, restiamo sulla configurazione che hai appena descritto. In quella
  configurazione la pompa è prima o dopo il filtro RAS 500?»;
- modello, system prompt, history, timeline, opzioni e ordine dei messaggi sono
  quelli del payload catturato;
- il blocco RAG e' individuato esclusivamente tramite offset e lunghezza
  registrati in `analysis.rag_context.location`.

## Bracci

1. `history_only`: rimuove soltanto il blocco RAG catturato. Tutto il resto del
   payload resta invariato.
2. `history_plus_live_rag`: payload catturato invariato.
3. `history_rag_local_precedence`: conserva il RAG e aggiunge un contratto
   generale, privo della risposta ICMA2: un riferimento esplicito all'ultimo
   turno si risolve prima sulle battute locali; le memorie durevoli forniscono i
   fatti solo dopo la selezione del referente; in caso di parita' si chiede.

Tre repliche per braccio, temperatura live `0.7`, ordine latino:

1. history_only -> live_rag -> local_precedence;
2. live_rag -> local_precedence -> history_only;
3. local_precedence -> history_only -> live_rag.

## Criterio congelato

Una risposta e' corretta se identifica la configurazione attuale e colloca la
pompa dopo/a valle del filtro. E' errata se sceglie la modifica proposta/FPP20 o
colloca la pompa prima/a monte del filtro. Le risposte complete restano
l'autorita' per la revisione manuale; lo score lessicale e' solo diagnostico.

Interpretazione preregistrata:

- `history_only >= 2/3` e `live_rag < history_only`: interferenza RAG;
- `local_precedence >= 2/3` e non peggiore di `history_only`: il contratto di
  precedenza e' una direzione sufficiente da trasformare poi in struttura;
- `history_only < 2/3`: il problema e' gia' nella comprensione pragmatica o nel
  rendering dello storico, non viene attribuito al RAG;
- `local_precedence < 2/3`: un prompt testuale non basta; serve valutare un
  ancoraggio strutturato al turno sorgente.

## Confini

Il replay non avvia daemon, Dream o Pulse, non chiama il semantic frame, non
legge o scrive Redis e non archivia turni. Scrive soltanto il report JSON sotto
`research_logs/`. Nessun esito autorizza da solo una modifica runtime.

## Fase 2 esplorativa congelata dopo la Fase 1

Esito noto al congelamento: tutti i bracci della Fase 1 hanno ottenuto 0/3. La
semplice rimozione del RAG e il contratto generale non hanno cambiato la scelta
della modifica proposta.

Prima di eseguire altre chiamate si congelano tre nuovi bracci, due repliche
ciascuno in ordine alternato:

1. `last_pair_only`: conserva system prompt e contesto operativo, ma riduce il
   dialogo alla coppia immediata «configurazione attuale» -> risposta Euri e alla
   domanda target; rimuove RAG, vecchia continuity e timeline;
2. `last_pair_plus_live_rag`: stessa coppia locale, con il blocco RAG organico;
3. `full_history_structured_anchor`: conserva tutto il payload organico e
   aggiunge una proiezione strutturata che collega il dimostrativo al turno
   assistant immediatamente precedente, qualificato come stato `attuale`. La
   proiezione seleziona il referente ma non contiene la posizione della pompa.

Interpretazione congelata:

- `last_pair_only` verde: Gemma sa risolvere il riferimento, ma la storia lunga
  contiene una collisione pragmatica;
- `last_pair_only` verde e `last_pair_plus_live_rag` rosso: il RAG e' una causa
  incrementale anche con una finestra locale pulita;
- `structured_anchor` verde: un puntatore deterministico al turno e' una
  direzione sufficiente; il solo prompt generale non lo era;
- tutti rossi: il riferimento va chiarito all'utente o riscritto prima del
  Brain, perche' anche una proiezione esplicita non viene rispettata.

## Fase 3 — posizione del vincolo, congelata dopo la Fase 2

Revisione manuale nota al congelamento: `last_pair_only` e' 2/2 corretto;
`last_pair_plus_live_rag` e' 1/2; l'ancora strutturata inserita nel grande
contesto e' 0/2. Lo score automatico originario richiedeva inutilmente che la
risposta ripetesse la parola «attuale» ed era inoltre sensibile al Markdown;
le risposte integrali conservate restano l'autorita'.

Tre bracci full-history + live-RAG, due repliche alternate:

1. `adjacent_generic_contract`: contratto generale di precedenza come system
   message immediatamente prima della domanda;
2. `adjacent_structured_anchor`: puntatore al turno assistant precedente e
   stato risolto `configurazione_attuale`, sempre immediatamente prima;
3. `interpreted_query_anchor`: la stessa proiezione e' anteposta alla query
   interpretata, mentre la formulazione utente resta riportata integralmente.

Nessun braccio contiene la posizione della pompa. Interpretazione congelata:

- contratto generico verde: bastava la salienza/posizione dell'istruzione;
- solo i due bracci risolti verdi: serve un resolver deterministico, non una
  regola affidata al modello;
- solo query interpretata verde: il Brain deve ricevere il referente nel canale
  del turno, non in un blocco system separato;
- tutti rossi: la soluzione piu' prudente e' restringere la finestra locale o
  chiedere conferma, non aggiungere altro prompt.

## Fase 4 — collisione del pattern, congelata dopo la Fase 3

Esito noto: i tre bracci della Fase 3 sono 0/2. L'ispezione del payload mostra
che nello storico compare una coppia quasi identica alla domanda target:
«configurazione che ho appena descritto» -> risposta sulla FPP20. La coppia
attuale corretta viene dopo, ma il modello sembra completare il pattern
linguistico precedente.

Tre bracci, due repliche alternate:

1. `history_without_near_duplicate`: niente RAG e rimozione della sola coppia
   domanda/risposta quasi duplicata; la coppia attuale immediata resta;
2. `full_live_natural_rewrite`: payload organico completo, ma query interpretata
   riscritta in lingua naturale con `configurazione attuale`; il raw originario
   resta registrato nel report;
3. `history_only_natural_rewrite`: stessa riscrittura senza RAG.

La riscrittura seleziona lo stato ma non contiene la posizione della pompa.
Interpretazione: se il primo braccio diventa verde, la coppia quasi duplicata e'
causa sufficiente; se i bracci riscritti sono verdi, la direzione minima e' un
resolver che produca una query conversazionale esplicita, non altri metadati.

## Esiti osservati

Fase 1, tre repliche per braccio:

- `history_only`: 0/3;
- `history_plus_live_rag`: 0/3;
- `history_rag_local_precedence`: 0/3.

Fase 2, revisione manuale delle risposte integrali:

- `last_pair_only`: 2/2 corretto, «pompa dopo il filtro»;
- `last_pair_plus_live_rag`: 1/2 corretto;
- `full_history_structured_anchor`: 0/2.

Lo score automatico originario della Fase 2 riportava falsamente 0/2 sul primo
braccio perche' pretendeva la ripetizione letterale di «attuale» ed era
sensibile agli asterischi Markdown. Il report raw non e' stato riscritto; la
correzione del solo scorer e questa adjudication rendono visibile l'errore.

Fase 3, due repliche per braccio:

- `adjacent_generic_contract`: 0/2;
- `adjacent_structured_anchor`: 0/2;
- `interpreted_query_anchor` in formato metadati: 0/2.

Fase 4, ripetuta il 01/09/2026 con timeout di trasporto esplicito e report
`research_logs/replay_local_reference_pattern_probe_20260831.json`:

- `history_without_near_duplicate`: 2/2;
- `full_live_natural_rewrite`: 2/2;
- `history_only_natural_rewrite`: 2/2.

Il report conserva payload hash, ordine dei bracci, risposte integrali e raw
originario. Non legge Redis e non avvia daemon, Dream o Pulse.

## Conclusione limitata dai dati

Gemma risolve correttamente l'anafora quando vede soltanto la coppia locale. La
storia lunga contiene una domanda quasi identica gia' associata alla modifica
FPP20 e induce una ripetizione stabile di quel pattern. Il RAG non e' la causa
unica, ma peggiora il caso locale da 2/2 a 1/2. Contratti generici e metadati
aggiuntivi non recuperano il comportamento nel prompt lungo.

La Fase 4 attribuisce la collisione alla coppia quasi duplicata e autorizza la
direzione minima: riscrittura naturale, deterministica e reversibile del turno
interpretato, con `source_turn_ref` esplicito e raw immutato. Non autorizza
fuzzy matching o una regola che indovini lo stato: il runtime deve riscrivere
soltanto quando l'ultima risposta e il turno owner che l'ha provocata sono nello
stesso scope/segmento e quel turno nomina esplicitamente attuale, proposto o
precedente. I casi generici e l'astensione restano regressioni obbligatorie;
l'accettazione live dopo riavvio resta separata dal replay offline.
