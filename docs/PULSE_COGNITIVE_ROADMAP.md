# Pulse cognitivo — roadmap persistente

Stato aggiornato: 2026-07-23 12:00

Fase corrente: **1 — lineage osservazionale**

Prossima azione esatta: **attendere il primo evento cognitivo naturale, verificarne la trace, poi Fase 2**

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

### Fase 1 — Envelope v2 e timeline cognitiva: implementata, attende verifica runtime

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

Da verificare dopo il riavvio:

1. log `Cognitive Projector: in ascolto durevole su euri:pulse`;
2. il backlog legacy viene ACKato come telemetria senza effetti;
3. nuovi eventi cognitivi compaiono una sola volta in `euri:cognitive:events`;
4. trace e causation sono continue tra seed, candidate e promozione/reaction;
5. il projector non cambia conteggio o contenuto di memorie e insight.

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

### Fase 2 — Lineage dell’uso reale: da fare

Strumentare, senza cambiare ranking o risposte:

- `memory/recalled` con query/turno e posizione nel retrieval;
- `insight/recalled`;
- `memory/used_in_response` e `insight/used_in_response`, distinti dal semplice recall;
- turno utente/risposta come confine di trace;
- `correction/proposed` distinto da `correction/applied`;
- chiamate modello con componente, latenza, coda e outcome, senza prompt sensibili.

Decisione da preservare: “recuperato” non significa “usato”; “usato” non significa
“vero”; una risposta del modello non è evidenza esterna.

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

## Protocollo di ripresa

Quando il lavoro riparte:

1. leggere questo file e la testa di `CODEX.md`;
2. controllare branch, worktree e ultimo commit;
3. non saltare la “prossima azione esatta” senza documentarne il motivo;
4. eseguire prima test unitari, poi verifica Redis read-only;
5. aggiornare questa roadmap nello stesso commit del passo concluso;
6. lasciare una sola fase “corrente” e una prossima azione concreta.
