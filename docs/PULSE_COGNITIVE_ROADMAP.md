# Pulse cognitivo — roadmap persistente

Stato aggiornato: 2026-07-24 16:30

Fase corrente: **2, lineage e verifica delle memorie passive collegati — conferma live finale da verificare**

Prossima azione esatta: **dopo il passaggio a Linux X11, creare una nuova memoria passiva, rispondere alla domanda Pulse e verificare log `CONFIRM → stato aggiornato` più i campi Redis di conferma esterna**

Questo file è il punto di ripresa canonico del cantiere Pulse. Va letto insieme
alla prima sezione di `CODEX.md` prima di modificare Pulse, Dream, memoria,
Initiative o reaction. A ogni avanzamento si aggiornano: stato, evidenze, commit
e prossima azione esatta. In questo modo il lavoro non dipende dalla memoria di
una sessione Codex né dal processo vivo di Euri.

## Obiettivo

Usare i Pulse non soltanto come telemetria, ma come traccia causale verificabile
del modo in cui Euri ripesca, trasforma, usa e corregge contenuti. La traccia deve
permettere di distinguere apprendimento, semplice ricorrenza e auto-risonanza.

Non è un secondo RAG e non è una nuova fonte di verità.

## Invarianti

1. `euri:pulse` resta il bus afferente compatibile e conserva anche telemetria grezza.
2. `event_class=telemetry` non diventa conoscenza per il solo fatto di esistere.
3. `event_class=cognitive` dichiara entità, produttore, trace e stato epistemico.
4. Il Cognitive Projector è osservazionale: non scrive `euri:memory:*`, non cambia
   `euri:insight:*`, non promuove, non demote e non esegue azioni.
5. La timeline derivata deve essere idempotente e recuperare eventi non ACKati
   dopo un crash o un riavvio.
6. Nessun comportamento nuovo nasce soltanto da correlazione interna: prima misura,
   poi esperimento, infine policy esplicita e reversibile.
7. Evidenza interna ed evidenza esterna restano separate nell'envelope.

## Stato delle fasi

### Fase 0 — Audit del Pulse precedente: completata

Evidenza 2026-07-23:

- 3.924 eventi in `euri:pulse`;
- 89% non supportati da Initiative;
- `euri:tension` senza eventi runtime;
- nessun campo di lineage nei 20 tipi storici;
- Initiative leggeva solo eventi nuovi da `$` e tre coppie sense/kind;
- la quasi totalità del bus era quindi telemetria registrata e mai utilizzata.

### Fase 1 — Envelope v2 e timeline cognitiva: implementata e verificata

Implementato:

- envelope additivo v2 su tutti i Pulse;
- distinzione `telemetry` / `cognitive`;
- campi `producer`, `trace_id`, `causation_id`, `logical_event_id`,
  `entity_refs`, `parent_refs`, confine epistemico, esperimento e durata;
- `pulse_emit()` ritorna lo stream ID, utilizzabile come causa dell'evento seguente;
- consumer group durevole `euri:cognitive:projector:v1`;
- timeline filtrata `euri:cognitive:events`, idempotente perché conserva lo stesso
  stream ID della sorgente;
- recupero dei pending del consumer stabile dopo arresto/crash;
- worker supervisionato nel Voice Daemon;
- eventi cognitivi iniziali:
  - `memory/saved`;
  - `dream/seed_selected`;
  - `dream/candidate_created`;
  - `dream/candidate_discarded`;
  - `insight/promoted`;
  - `insight/demoted`;
  - `reaction/rated`, con verdetto;
  - `consolidation/consolidated`, con figlio e genitori.

Audit read-only:

```bash
./venv/bin/python scripts/audit_cognitive_pulse.py
```

Verifica pre-riavvio 2026-07-23:

- unit: 42/42;
- manifest: 51 test classificati nei tre livelli;
- audit Redis read-only: 3.924 Pulse legacy, 0 eventi cognitivi, consumer group
  ancora assente come previsto;
- compilazione dei moduli modificati pulita.

Verifica runtime dopo riavvio, 11:56:

- log presente: `Cognitive Projector: in ascolto durevole su euri:pulse`;
- consumer group creato e allineato: `pending=0`, `lag=0`;
- tutti i 3.924 eventi legacy sono stati letti e ignorati senza reinterpretazione;
- i due eventi nuovi `presence/arrival` e `presence/owner_arrival` hanno envelope v2
  ma restano `event_class=telemetry`;
- `euri:cognitive:events` resta correttamente a zero: non è ancora avvenuto un
  salvataggio, sogno, verdetto o consolidamento nuovo;
- boot cognitivo invariato: 117 candidati Loop 2e, 160 insight interni e 4
  confermati esternamente.

Prima trace naturale, 12:05:

- `memory/saved` è comparso una sola volta con trace
  `memory:98bb04db-e5ca-4f04-a726-76b88487008f`;
- producer `memory_outbox`, `pending=0`, `lag=0`, nessun campo trace mancante;
- il contenuto errato non è nato nel projector: l'evento ha reso osservabile la
  catena `Loop 2a -> memory/saved -> RAG`;
- questo ha scoperto che Loop 2a usava tre reaction delle 08:52 come “sessione”
  durante il dialogo UBQ delle 12:00 e che la nuova reflection era stata richiamata
  subito nel turno seguente.

Hardening conseguente:

- checkpoint durevole `euri:loop2a:memory_checkpoint`;
- selezione per `conversation_id/segment_id`, ordinata e successiva al checkpoint;
- parent distinti tra memorie della sessione e memorie correlate;
- idle cognitivo di cinque minuti e `precommit_guard` contro attività sopraggiunta;
- reflection pubblicata come `internal_reflection` e sempre da verificare;
- repair append-only emesso come `audit/repaired`.

Poiché Euri è ferma durante il repair, l'audit finale mostra intenzionalmente
`pending=0`, `lag=1`: l'unico evento in attesa è il repair e deve essere proiettato
una sola volta al prossimo avvio.

Verifica pre-riavvio del fix: manifest 52 file, unit 43/43, compilazione e
diff-check puliti; integrazione VectorSet/Redis 16/16 con cleanup completo.
Le sonde dipendenti da Ollama restano differite perché il servizio è fermo insieme
a Euri, non per un fallimento del percorso modificato.

Verifica idle naturale dopo riavvio, 12:48–14:57:

- Loop 2a non ha pubblicato alcuna reflection del dialogo UBQ e il projector è
  rimasto `pending=0`, `lag=0`;
- Loop 2h ha reso visibile un gap residuo: `92bac556` era semanticamente coerente
  ma priva di parent e marcata come verificata; Redis `SCAN` aveva inoltre contato
  due volte una delle due coppie reali;
- il candidate smentito `cb4d3541...3dfe567e` veniva correttamente fermato solo
  dopo 6 judge LLM per ciclo, causando pass light da circa 160 secondi;
- hardening implementato: self-observation causale, dedup e precommit guard;
  blocco di ri-promozione prima di ogni lavoro LLM, one-shot e subordinato alla
  smentita esterna;
- manifest 53 file, unit 44/44;
- repair applicato a Euri ferma: la reflection live porta ora quattro parent,
  due coppie causali, `requires_verification=true` e
  `epistemic_status=internal_self_observation`; Obsidian è allineato;
- audit post-repair: 3.952 Pulse, 11 cognitivi proiettati, `pending=0`, `lag=1`.
  Il solo evento in coda è il repair appena emesso mentre il projector è fermo.

Verifica runtime conclusiva, 15:31–16:23:

- boot pulito con projector durevole, 118 candidati Loop 2e, 154 insight interni
  e 4 confermati esternamente;
- primo light: 34 candidate demoti classificati una sola volta prima del lavoro
  costoso; `3dfe567e` bloccato esplicitamente come `external_refutation`;
- il primo pass ha ancora eseguito 3 judge su candidate non bloccati ed è durato
  90,9 s; il pass successivo è durato 3,6 s con `model=0`, confermando che non
  esiste più l'hot-loop del candidato smentito;
- audit: 3.988 Pulse, 46 cognitivi, 34 `insight/promotion_blocked`, nessuna trace
  incompleta, `pending=0`, `lag=0`;
- i due eventi `audit/repaired` appartengono a producer distinti (repair Loop 2a
  e repair Loop 2h), quindi non sono una duplicazione.

### Fase 2 — Lineage dell’uso reale: prima fetta implementata, runtime da verificare

Implementato in shadow mode, senza cambiare ranking o risposte:

- `memory/recalled` con query/turno e posizione nel retrieval;
- `insight/recalled`;
- `memory/used_in_response` e `insight/used_in_response`, distinti dal semplice recall;
- turno utente/risposta come confine di trace;
- copertura dei normali turni RAG `voice_chat`, `voice_search`, `silent_chat` e
  `mobile`;
- provenance dei soli nodi realmente iniettati: memoria base, reflection,
  impegni, insight e retrieval strategico;
- prompt e risposta rappresentati nel Pulse soltanto da SHA-256 e lunghezza;
  nessun testo o frammento mnemonico viene copiato;
- attribuzione d'uso deterministica post-risposta, priva di chiamate LLM e
  conservativa. Richiede identificatori o sovrapposizione lessicale distintiva
  non già spiegata dalla domanda e marca il risultato
  `supported_not_proven`.

Restano da strumentare in una fetta successiva:

- `correction/proposed` distinto da `correction/applied`;
- chiamate modello con componente, latenza, coda e outcome, senza prompt sensibili.

Prima fetta già anticipata dall'incidente, senza cambiare ranking:

- `action/proposed`;
- `action/decided`;
- `action/revalidated`;
- `action/executed|failed|deferred`, con target e stato before/after.

Resta da collegare queste trace al turno utente e alla risposta, insieme a recall
e uso effettivo delle memorie.

Decisione da preservare: “recuperato” non significa “usato”; “usato” non significa
“vero”; una risposta del modello non è evidenza esterna.

Verifica pre-runtime 2026-07-23:

- test mirati lineage 3/3;
- unit 45/45, manifest 54 file;
- compilazione e diff-check puliti;
- baseline Redis read-only: 3.989 Pulse, 46 eventi cognitivi, nessun turno della
  nuova lineage prima del riavvio, projector `pending=0` e `lag=0`.

Il contatore `used_in_response` è intenzionalmente un pavimento: una parafrasi
forte può produrre un falso negativo. Non deve essere interpretato come una misura
totale dell'attenzione del modello e non abilita ancora alcuna policy.

Incidente emerso dalla prima prova e riparato il 2026-07-24:

- Il comando nominato del Silent Chat è stato classificato come TEACH e ha salvato
  soltanto la label `Compuand UBQ 2026`; non era un errore della response lineage.
- Il routing è stato corretto in modo deterministico e il record live è stato
  riparato append-only con `superseded_by`, titolo, provenienza user e stato di
  verifica pendente. La copia precedente è recuperabile in quarantena.

### Fase 3 — Observer e metriche di auto-risonanza: da fare

Creare un consumer ancora osservazionale che misuri:

- quante conclusioni discendono solo da nodi interni;
- quante trace ricevono evidenza esterna;
- ricorrenza dello stesso contenuto attraverso Dream, reflection e consolidamento;
- rapporto generatore/giudice e dipendenza dalla cache;
- perdita di eccezioni nei consolidamenti;
- tempo tra emersione, uso, correzione e abbandono.

Output previsto: audit leggibile e pagina Control Room. Nessun punteggio di questa
fase deve mutare automaticamente la memoria.

### Fase 4 — Esperimenti comportamentali: da progettare dopo le metriche

I Pulse attuali possono sostenere comportamenti utili, ma soltanto con consumer
specifici e policy diverse per tipo:

| Pulse esistente | Comportamento possibile | Vincolo |
|---|---|---|
| presence / owner_arrival | consegna differita di domande, reminder e briefing | presenza non equivale a consenso |
| clock / commitment | reminder contestuali e verifica di impegni scoperti | adapter reale, niente claim da CHAT |
| hardware | riduzione del lavoro differibile sotto pressione | snapshot fresco, isteresi, azioni reversibili |
| social movement | ritmo del turno, attesa e brevità della voce | niente diagnosi emotive o memoria personale |
| vault/change | reindicizzazione, confronto versioni, curiosità su cambi rilevanti | dedup e provenienza documento |
| integrity / audit / provenance | allarmi Control Room e manutenzione mirata | non correggere contenuti da soli |
| dream / insight / reaction | scelta di cosa riportare, sospendere o riesaminare | separare convergenza interna da conferma |
| cognitive lineage v2 | riconoscere loop sterili o apprendimenti incompleti | prima observer, poi esperimento controllato |

Non va costruito un consumer universale “Pulse → comportamento”: mescolerebbe
presenza, hardware, sogni e verità esterna sotto una sola salienza.

### Fase 5 — Policy adattive controllate: non iniziata

Solo dopo dati e test:

- attenuare la ripetizione di trace esclusivamente interne;
- favorire domande quando manca un arco esterno decisivo;
- programmare reflection su trace incomplete, non su ciò che è solo frequente;
- degradare lavoro differibile sotto pressione hardware;
- usare segnali sociali solo per turn-taking.

Ogni policy richiede flag, shadow mode, metrica prima/dopo e rollback.

## Registro avanzamento

| Data | Fase | Esito | Commit |
|---|---|---|---|
| 2026-07-23 | 0 | Audit: Pulse prevalentemente telemetrico, lineage assente | `1126a0f` come base |
| 2026-07-23 | 1 | 42/42 unit; audit pre-boot 3.924 legacy e 0 cognitivi; runtime da riavviare | commit che introduce questa roadmap |
| 2026-07-23 | 1 | Boot verificato: group sano, backlog esaurito, presenza rimasta telemetria; attesa prima trace naturale | checkpoint successivo |
| 2026-07-23 | 1 | Prima trace naturale valida; scoperto e riparato incidente Loop 2a/agenda; repair in attesa di projector fermo | questo commit |
| 2026-07-23 | 1 | Idle test: Loop 2a sano; chiusi lineage Loop 2h e hot-loop judge su insight smentito | commit corrente |
| 2026-07-23 | 1 | Riavvio verificato: secondo light 3,6 s/model=0; projector pending=0/lag=0 | `cf6032b` + checkpoint docs |
| 2026-07-23 | 2 | Recall/uso separati in shadow mode; 45/45 unit; baseline runtime pronta | commit corrente |

## Protocollo di ripresa

Quando il lavoro riparte:

1. leggere questo file e la testa di `CODEX.md`;
2. controllare branch, worktree e ultimo commit;
3. non saltare la “prossima azione esatta” senza documentarne il motivo;
4. eseguire prima test unitari, poi verifica Redis read-only;
5. aggiornare questa roadmap nello stesso commit del passo concluso;
6. lasciare una sola fase “corrente” e una prossima azione concreta.
