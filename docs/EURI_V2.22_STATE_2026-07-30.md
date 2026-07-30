# Euri V2.22 — Memoria osservabile e confinata

Data: 30/07/2026

Tipo: fotografia documentale e nuova base `main`

Registro operativo: [EURI_OPEN_WORK.md](EURI_OPEN_WORK.md)

## Dichiarazione

V2.22 consolida il lavoro successivo a V2.21 in una base unica. Non introduce
una nuova migrazione Redis: dichiara come stato corrente il codice già
verificato e usato sul branch di sviluppo, rendendo espliciti confini,
telemetria e risultati negativi.

La memoria non è soltanto recuperabile: ora è separata per mondo, datata nella
fonte, osservata nel suo uso e sottoposta a protocolli che possono bocciare una
modifica anche quando ne migliora una metrica desiderabile.

## Cosa è stabile

### Fonte e recupero

- La base RAG grezza è protetta.
- Le memorie passive sono locator verso turni verbatim; il testo sintetico non
  sostituisce la fonte originale nel prompt.
- Il gate selettivo può promuovere il verbatim pertinente e attivare thinking
  soltanto quando una fonte originale è stata davvero promossa.
- Voce, mobile e Silent Chat condividono dispatcher, archivio turni e decisione
  di thinking.

### Tempo

- I turni verbatim mostrano data/ora assoluta, parlante e affidabilità del
  canale.
- Il richiamo cronologico cerca occorrenze datate e comunica quante ne esistono,
  senza trasformare `limit=1` nell'affermazione falsa “è l'unica”.
- `asserted_at` e data dell'evento restano concetti distinti; la copertura
  dell'event-time non è ancora completa.

### Confini

- Memorie, note e turni portano `memory_scope`.
- `personal` e `experiment_*` non competono nel ranking né si contaminano in
  deduplica, consolidamento, sogni o recupero dual-channel.
- Uno scope malformato degrada a `invalid_scope`, non a personale.
- Un guardrail statico inventaria le query runtime scoperte; l'interfaccia
  scoped-by-construction resta lavoro futuro.

### Osservabilità

- `response_lineage_shadow_v1` distingue richiamo da uso sostenuto ma non
  provato.
- L'utilità osservata influenza soltanto l'ordine dei candidati Loop 2e, con
  peso e cap limitati; non cambia verità, TTL o ammissibilità.
- Lifecycle verbatim e utility shadow hanno rapporti e reminder durevoli al
  boot/status.
- Gli operatori cognitivi sono censiti come aggiornatori, generatori e
  mietitori; non viene più assunta una primitiva unica dove il codice non la
  mostra.

## Risultati che delimitano la release

- Dual-channel census: evidence recall `+0,0311`, 22 recuperi, zero gold persi;
  il solo F1 senza thinking cresce di appena `+0,0023`.
- Thinking selettivo sul development set: `dual_think 0,2123` contro
  `rag_think 0,1604`, avversariali invariati; LoCoMo è ormai interamente aperto
  e non supporta ulteriori rivendicazioni indipendenti.
- Loop 2f legacy resta operativo: il primo banco controllato lo mostra utile ma
  con contratto fragile.
- Structured v2 viene correttamente bocciato: contratto 76/76 e zero false
  supersessioni, ma soltanto 50% di recall sulle supersessioni vere.
- Loop 2h resta inconclusivo: nessuna opportunità incrementale nel campione e
  `UNKNOWN` 0/6 sugli ambigui.

## Fotografia runtime read-only

Misurata il 30/07/2026:

- bacino personale grounded: **668/800**, sentinella operativa 750;
- verbatim: 2.171 turni, 6 referenziati, 2.165 recenti non referenziati,
  0 orfani, 0 riferimenti mancanti;
- utility shadow: 102 risposte, 963 nodi richiamati, 122 usi sostenuti ma non
  provati, 224 entità osservate; review non ancora matura per età;
- manifest unitario: **68/68**.

I numeri verbatim non autorizzano cancellazioni: il grace period di 180 giorni
non è trascorso e il lifecycle è audit-only.

## Autorità runtime

- Loop 2f: classificatore legacy distribuito.
- Structured v2: codice e harness conservati, non autorità.
- Loop 2h: attivo come controllo reversibile, utilità non ancora dimostrata.
- Plausibility gate: archiviato e spento.
- Dream trace sperimentale: raccolta congelata.
- AdaptiveClassifier: fast path adattivo spento, harvest separato.

## Limiti

1. Il bacino grounded arriverà alla finestra 800 con l'uso normale se la
   selezione non viene spostata nell'indice.
2. `main` contiene ora un sistema sperimentale maturo, non un prodotto
   generalizzato: un solo utente, un solo ambiente e molti risultati
   development.
3. Il contributo causale dei loop non è ancora isolato.
4. Il ciclo forzato non replica fedelmente ordine e molteplicità del runtime.
5. I mietitori non lasciano sempre tombstone, quindi alcuni controfattuali
   storici non sono ricostruibili.
6. Un benchmark indipendente orientato ad aggiornamento e temporalità è ancora
   necessario.

## Base Git

Il branch `experiment/passive-memory-heldout` è promosso sulla nuova `main`.
Il contenuto era una discendenza lineare e localmente è avanzato per
fast-forward. Un merge GitHub concorrente del vecchio ramo
`agent/synchronize-euri-runtime`, già interamente contenuto nella linea V2.22,
ha richiesto un merge amministrativo senza differenze nel tree. Nessun
force-push, ramo riscritto o cancellato. Il tag annotato `v2.22` identifica
questa fotografia.

La dichiarazione di versione non modifica Redis e non riscrive memorie.
