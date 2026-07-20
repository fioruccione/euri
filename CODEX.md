# Handoff Euri — 2026-07-14

## Punto aperto: analisi clipboard e persistenza — 2026-07-20

- Oggi `clipboard_analyze` significa per contratto "analizza e salva": la sintesi
  prodotta dal modello entra subito in Redis come `source=teach`, anche quando
  Stefano ha chiesto soltanto di analizzare gli appunti.
- Non cambiare il comportamento durante la calibrazione della percezione sociale.
  Riesaminarlo in seguito separando attenzione temporanea e apprendimento permanente:
  `analizza` usa il contenuto solo nella sessione; `analizza e salva` crea la memoria;
  in alternativa Euri chiede conferma esplicita al termine dell'analisi.
- Rischio da evitare: documenti temporanei, testi di terzi o descrizioni di Euri
  possono diventare `teach` e rientrare nel RAG come conferma autorevole, creando
  contaminazione o circuiti autoreferenziali. Il punto e' di policy epistemica,
  non un guasto osservato del tool.

## Punto aperto: self-model e autoreferenza — 2026-07-20

- Non sterilizzare la capacita' di Euri di parlare e ragionare su se stessa: un
  self-model e' necessario per continuita', limiti operativi e personalita'.
- Separare in futuro quattro piani con provenienza esplicita: stato operativo
  verificabile, descrizione progettuale fornita da Stefano, valutazione soggettiva
  dell'utente e interpretazione di Euri. Una descrizione recuperata dal RAG non e'
  prova che Euri abbia verificato internamente il proprio funzionamento.
- Rischio da controllare: documento su Euri -> memoria autorevole -> recupero RAG ->
  nuova affermazione su Euri. Preservare le interpretazioni, ma impedire che questo
  ciclo diventi auto-certificazione. Nessuna modifica finche' la policy non sara'
  ragionata esplicitamente.

## Percezione sociale visiva Fase 0 — 2026-07-20

- `voice/social_perception.py` riusa i frame del VisualGate e misura localmente
  sorriso, contrazione sopracciglia, sguardo verso il basso e posa grezza della
  testa. Baseline, mediana, isteresi e persistenza evitano reazioni al singolo frame.
- Il recettore profila solo Stefano dopo un match SFace recente. Non salva immagini,
  non chiama LLM, non crea memorie e non modifica ancora tono o Initiative.
- Redis conserva lo snapshot effimero in `euri:social:latest`, una baseline numerica
  campionata in `euri:social:baseline` e le transizioni a bassa salienza nel Pulse.
- `scripts/audit_social_perception.py` rende verificabili vita del recettore,
  distribuzioni numeriche, stati e transizioni senza rileggere alcuna immagine.
- Gemma multimodale resta una Fase 2: uso occasionale in pausa con un frame volatile
  e contesto dialogico, mai scansione continua. Il flag predisposto e' spento.
- Primo protocollo guidato: sorriso lieve e marcato distinti; brow/gaze non validati.
  La posa MediaPipe ora usa assi Euler corretti e conserva coefficienti grezzi
  selezionati per il prossimo audit, senza trasformarli in stati o azioni.
- Secondo protocollo controllato: screen pitch `9.5°`, camera `5.8°`, keyboard
  `17.6°`; `eyeLookUp` vale circa `0.45` sulla tastiera contro `0.05-0.07` sul piano
  schermo/webcam. La direzione semantica e' invertita rispetto al nome blendshape:
  `gaze_down` e' ora derivato dalla coppia empiricamente corretta (gate 0.25/0.15).

## Identita' ospite e conferma differita — 2026-07-20

- Il daemon usa un verdetto vocale tri-state. `INDETERMINATE` e' assenza di prova,
  non autenticazione: il volto owner o una conversazione aperta da voce verificata
  possono risolvere una clip breve; altrimenti l'attore e' `unknown`.
- Un attore `unknown` deve pronunciare la wake word e non attraversa `_dispatch`:
  `respond_to_guest` non riceve SYSTEM_PROMPT privato, RAG, history di Stefano o tool.
  Anche la history tra ospiti ignoti e' vietata, per non assumere che siano la stessa
  persona. Il ring conversazionale usa il ruolo `Ospite non identificato`.
- `core/guest_claims.py` e' il confine intenzionale da preservare: i claim sono
  documenti Redis separati sotto `euri:guest_claim:*`, indicizzati solo dalla coda
  bounded `euri:guest_claims:pending`, con TTL 30 giorni. Non devono diventare nodi
  `euri:memory:*` prima della conferma owner.
- La promozione scrive una memoria esplicita con testo che distingue chi ha riferito
  il dato da chi lo ha confermato e conserva nei metadati `origin_actor_id`,
  `confirmed_by_actor_id`, `guest_claim_id` e `guest_reported_at`. Rifiuto e rinvio
  non producono memoria cognitiva.
- Limite consapevole: mobile e Silent Chat restano canali assunti come autenticati
  dall'accesso all'interfaccia e continuano a rappresentare Stefano. Il tri-state
  introdotto qui riguarda il microfono locale.

## Provenienza della memoria continua — 2026-07-20

- Invariante: una memoria passiva `semantic_fact` deve essere sostenuta da
  `source_turn_ids` che puntano esclusivamente a turni `role=user`. Il modello puo'
  leggere le risposte di Euri per capire il tema, ma non usarle come evidenza del fatto.
- La mancata contestazione non e' conferma. I vecchi nodi
  `passive_support=tacit_acceptance` restano conservati e fuori dal Loop 2e, ma il RAG
  li etichetta come ipotesi storiche di Euri non confermate.
- `conversation_episode` conserva la continuita' separando parole di Stefano,
  contributi di Euri e fili aperti. E' memoria contestuale non fattuale e non puo'
  diventare evidenza di consolidamento.
- Le lezioni estratte dal Loop 2g dopo una correzione sono
  `source=reaction`, `memory_kind=reaction_lesson`: sono interpretazioni operative di
  Euri fondate sul feedback, non fatti passivi attribuibili a Stefano. Lo script
  `scripts/migrate_correction_lessons.py` ha riclassificato 35 nodi storici e rimosso
  il loro vecchio TTL da passive; e' idempotente e senza `--apply` opera in dry-run.

## Instrumentazione costo Dream — 2026-07-17

- Il log live ha mostrato picchi VRAM 96-97% durante i cicli creativi, con ritorno
  spontaneo al 31-72%: pressione di workload, non emergenza. Le soglie hardware e i
  riflessi restano invariati durante la baseline.
- Il Dream emette ora `[TIMING]` per ciclo idle, generazione, trace e
  `evaluate[creative|light]`, con breakdown fidelity/bridge/judge e cache/model call.
- Osservazione strutturale da non perdere: se `creative_due` e `light_due` coincidono,
  `_evaluate_insights` gira una volta in ciascun sotto-ciclo. Non e' stato deduplicato
  durante Dream Trace perche' cambierebbe budget e numero di misure. Valutarlo dopo la
  raccolta usando i nuovi tempi, non come ottimizzazione presunta.

## Falso workflow Poseidon e modalita' delle previsioni — 2026-07-17

- Caso live 11:23: una normale spiegazione tecnica ha prodotto
  `summarize -> draft -> save_for_review`. Causa deterministica: `legg\w*` matchava
  `leggero`; `controllato` forniva il secondo hit e il planner inventava il resto.
- `looks_like_workflow` e' ora precision-first: comando corrente esplicito, artefatto
  testuale e due famiglie di capability distinte. Nessun effetto viene inferito dalla
  storia recente. Il turno reale e' una regressione in `test_workflow_planner.py`.
- Il prompt principale distingue certezza della provenienza e certezza del contenuto:
  una frase realmente pronunciata puo' contenere una previsione. Euri deve preservare
  `stimo/probabilmente/dovrebbe/non ho controllato` e non promuoverli a risultati.

## Interocezione hardware Fase 0 — 2026-07-17

- `hardware_monitor.py` e' un processo indipendente avviato da `start_euri.sh`; il
  singleton lock `/tmp/euri-hardware-monitor.lock` evita doppi recettori ai restart.
- Contratto in `core/hardware_interoception.py`: lettura -> stato stabilizzato ->
  transizione. Nessun LLM, nessuna azione protettiva e nessuna memoria cognitiva.
- Redis: `euri:hardware:latest` e `euri:hardware:state` hanno TTL 30s; la loro assenza
  segnala recettore non vivo. `euri:hardware:baseline` conserva circa 14 giorni a un
  punto/minuto. `euri:hardware:events` contiene soltanto cambi, reminder e fault.
- Le transizioni vengono replicate nel Pulse come `sense=hardware`, `source=intero`.
  L'attuale Initiative non elegge questo sense: Euri non parla spontaneamente per
  un warning hardware e nessun modello riceve il flusso grezzo.
- Prima della Fase 1 raccogliere una baseline che includa chat, Whisper, Dream,
  maintenance e model load. Calibrare i falsi positivi e definire una matrice esplicita
  evento -> azione reversibile. Un riflesso dovra' rivalidare uno snapshot fresco,
  degradare prima il lavoro differibile e non uccidere processi da un singolo sensore.
- Specifica completa: `SPEC_HARDWARE_INTEROCEPTION.md`. Test:
  `test_hardware_interoception.py`; baseline unit 29/29.
- Checkpoint operativo: 72 ore dal primo avvio. Eseguire
  `./venv/bin/python scripts/audit_hardware_baseline.py`; il report e' read-only e
  stabilisce se durata/copertura sono sufficienti, non abilita automaticamente riflessi.

## Paternita' delle interpretazioni e bridge observer — 2026-07-17

- Decisione di prodotto: non sterilizzare le interpretazioni di Euri. Anche una lettura
  incompleta contribuisce alla personalita'; il vincolo e' non archiviarla o riproporla
  come dichiarazione/fatto di Stefano.
- Tutte le memorie `source=reflection` sono etichettate nel contesto come interpretazioni
  interne, inclusi i nodi storici. Il Loop 2a deve formulare la terza frase come
  `Ipotesi di Euri:` e non inventare piani, certificazioni o intenzioni dell'utente.
- I dieci sogni della notte 16→17/07 hanno prodotto zero promozioni: il judge semantico
  ha rimosso quasi tutte le false convergenze della vecchia scorciatoia. Resta misurato un
  rischio: claim forzati ma simili possono essere `SAME` pur non essendo fondati.
- `bridge_observer_v1` misura quindi la terza riga rispetto alle due fonti reali:
  `supported`, `hypothesis`, `forced`. Solo candidate nuovi, budget 3/ciclo, thinking
  ampio, campi nella convergence trace. E' strumentazione read-only e non e' un gate.
- Il wake guard al primo turno usa distanza infinita internamente ma non logga piu'
  l'epoch come durata; consenso ambientale invariato e fail-closed.

## Grounding temporale conversazionale — 2026-07-15

- Caso origine: alle 17:19 Euri ha detto "come ti dicevo poco fa" riferendosi al turno
  delle 10:56. La history aveva ordine ma nessun timestamp; una memoria passiva aveva
  inoltre trasformato l'apertura del tema IZOD in un fatto condiviso da Stefano ed Euri.
- I turni Brain hanno ora tempo, conversazione e segmento. Dopo 30 minuti nasce un nuovo
  segmento senza cancellare il precedente; il modello vede distanze qualitative e un
  contratto che vieta di narrarle salvo utilita' conversazionale.
- Schema memoria additivo e retrocompatibile: `memory_kind`, `asserted_at`, `event_start`,
  `event_end`, `temporal_context` con provenienza dei turni. `created_at` resta il momento
  della scrittura canonica. Il recall temporale indicizzato considera tutti e tre i tempi.
- Il passive learner distingue fatti da `conversation_anchor`. Un anchor ricorda che un
  filo e' stato aperto/riaperto e quali dettagli mancano; non fonda Dream, consolidamenti,
  ipotesi cross-episode o reaction. Il RAG lo etichetta esplicitamente come non-fattuale.
- Il resolver temporale vale per tutte le sorgenti e copre parti del giorno, oggi/ieri,
  N giorni/ore fa e date testuali o numeriche. Silent Chat ed episodi compressi propagano
  lo stesso contratto.
- Verifica: unit 28/28, integration 3/3, `py_compile` e diff-check puliti. Sonda Ollama sul
  dialogo IZOD: `memory_kind=episode`, turni 1-4, nessun valore tecnico inventato.
- Test live delle 18:38: la riapertura IZOD ha recuperato correttamente solo il filo e i
  dettagli mancanti. Due risposte successive hanno pero' copiato il prefisso `[tempo interno]`:
  la timeline e' ora un blocco system separato dai messaggi storici e `_clean` applica un
  ultimo scrub deterministico. Il contratto risolve i valori ellittici sul tema aperto e
  non consente valutazioni tecniche di numeri privi di unita'/metodo/riferimento.
- Alle 18:42 un turno Poseidon di 60 secondi e' stato trascritto integralmente ma scartato
  dal wake guard (`fuori finestra (69s)`), perche' la lease veniva verificata dopo VAD+STT.
  Il daemon ricava ora l'inizio fisico dalla durata del segmento: quello decide il consenso;
  fine STT continua a decidere il rinnovo dell'activity. Regressione e contro-caso ambient
  inclusi in `test_wake_guard.py`; unit ancora 28/28.
- Caso live 16/07 08:35: "a proposito delle prove sul Poseidon ... cosa hai in memoria?"
  e' finito in `AUDIT_MEMORY`: ha completato correttamente dopo 782689 ms (13m03s), ma ha
  occupato il main thread con 542 chiamate Ollama seriali e prodotto 350 candidati-rumore,
  poi non cancellati su risposta esplicita di Stefano. Il routing ora separa per operazione,
  non per soggetto: recall generico o tematico=`SEARCH`, conteggio/stato=`STATUS`, qualita',
  rumore, duplicati e pulizia=`AUDIT_MEMORY`. La distinzione RAM/memoria cognitiva e'
  esplicita; una frase ambigua non puo' avviare manutenzione. L'audit e' limitato a 40
  candidati prioritari, batch da 10, interrompibile e onesto sul campione; nessuna anchor
  episodica e' candidata.

## Cognitive Present runtime v1 — 2026-07-15

- Attivato per decisione esplicita di Stefano dopo il caso live IZOD: Initiative
  aveva interrotto il discorso con un insight protocollo/progetto e aveva poi
  catturato la continuazione sull'IZOD come reazione al sogno.
- Il presente mantiene una lease vocale dalla fine effettiva del TTS e un focus
  breve di 5 minuti costruito solo dagli ultimi turni utente accettati. La lease
  autorizza il follow-up senza wake word; il focus non autorizza parlato ambient.
- Durante un focus, il gate LLM conservativo distingue `EXTENDS`, `RELATED` e
  `UNRELATED`: solo `EXTENDS` puo' intervenire, dopo 8 secondi e con cooldown
  contestuale. La proposta riceve un token e viene rivalidata prima del TTS.
- Dal primo trigger VAD fino alla fine di autenticazione, STT e dispatch, un turno
  vocale in volo blocca Initiative anche se il testo non e' ancora disponibile.
- Le repliche a una domanda Initiative sono ora `ANSWER`, `CLARIFICATION` oppure
  `OFF_TOPIC`: quest'ultimo chiude la cattura reaction e restituisce il turno al
  dispatch normale. Pending reaction ridotto da 30 a 5 minuti.
- Il verdetto reaction precede la sintesi; una lesson `DA_VALUTARE`/`PARZIALE`
  eredita `requires_verification` e non puo' narrarsi come conferma.
- Sonda Ollama sul caso reale: insight protocollo/progetto=`RELATED`, candidato
  condizionamento/posizionamento IZOD=`EXTENDS`, replica frigorifero=`OFF_TOPIC`.
- Test: unit 27/27, integration 3/3. Nessuna modifica al Dream Engine o a
  `dream_trace`.

## Correzione convergenza insight — 2026-07-15

- Stefano ha autorizzato esplicitamente la correzione durante `dream_trace`: tre
  promozioni live avevano `n_certain=2`, ma i vicini a distanza 0.12–0.14 erano claim
  scollegati. Il full-text/template non può più decidere una convergenza.
- Policy `claim_judge_v2`: il vettore fa solo shortlist (<0.40); ogni coppia richiede
  giudizio conservativo SAME/RELATED/DIFFERENT. Solo SAME conta ed è assorbito.
- Cache simmetrica content-addressed, budget 6 nuove coppie/ciclo e fail-closed su
  budget, errore o output non parsabile. Trace estesa con policy e metriche del judge.
- Benchmark Ollama locale su 6 coppie etichettate: thinking 6/6 corrette e parsabili
  (116s), no-thinking 5/6 (7,5s), con un falso SAME su AST-vs-controllo qualità.
  Conservato `think=True`, `num_predict=5000`: il tetto evita finali troncati; il budget
  per ciclo limita il costo e la cache evita di ripeterlo sulla stessa coppia.
- Checkpoint esperimento documentato in `ESPERIMENTO_DREAM_TRACE.md`: baseline 149,
  trattamento 16, ambiguo 2. L'audit primario resta per-candidate ma va stratificato
  pre/post policy per i possibili effetti indiretti sul retrieval.
- Aggiunto `test_convergence_policy.py`; il live `test_insight_repromotion.py` simula
  ora il contratto semantico invece di affidarsi alla distanza zero.

## Cognitive Present preparatorio — 2026-07-14

- Aggiunti `core/cognitive_present.py` e `SPEC_COGNITIVE_PRESENT.md`, senza import
  dal daemon, Redis o prompt: il runtime e l'esperimento `dream_trace` restano invariati.
- Il contratto separa stato epistemico, disponibilita' sensoriale, fase/canale,
  domanda pendente e lease conversazionale; la lease parte dalla fine del TTS.
- `scripts/experiments/audit_cognitive_present.py` ha analizzato 71.980 righe del
  log corrente: 12 turni fuori finestra, uno solo follow-up certo entro 45s dalla
  fine TTS (14/07 16:55), seguito 21,4s dopo da Initiative; tre auto-descrizioni
  assolute della webcam confliggono con la capability configurata.
- Replay Active Focus raffinato: `reaction_raw` puo' solo rinforzare, la sintesi
  `reaction` non entra. Esito: NO-GO per copertura (5 giorni campionabili, nessun
  focus vivo al 14/07). Non riaprire alle sintesi interne; manca una sorgente diretta
  e replayabile dei turni utente accettati.
- Nessuna attivazione runtime prima della chiusura e dell'audit cieco Dream Trace.
- Review successiva: valori osservati congelati ricorsivamente, update sensoriali
  fuori ordine ignorati e transizione `finish_processing` aggiunta per canali senza
  TTS. `test_insight_repromotion.py` spostato nel tier live: la sua chiamata a
  `_evaluate_insights()` non e' isolata al prefisso test e puo' mutare candidate reali.
- Integration: plausibility e SAVE resolver verdi. Tool VectorSet esponeva la baseline
  13/16 gia' documentata come failure; i tre casi ambigui ora attendono correttamente
  `None` (fallback LLM) invece di pretendere che il fast path forzi un intent.
- Review `068e706`: fix READ_BACK corretto; la cronologia viene ristabilita solo nel
  gesto di audit, senza indebolire il ranking RAG. Review `worker_supervisor`: nessun
  finding bloccante su restart/shutdown; limite dichiarato da conservare, e' un
  supervisore di exit/error e health, non un watchdog per thread vivi ma bloccati.
- Verifiche post-review: unit 26/26, integration 3/3, spot-check live read-only
  `plane_guard` 4/4 e retrieval strategy 5/5. Tier live completo NON eseguito:
  consolidation e insight repromotion mutano lo stato cognitivo, memory pipeline
  produce side effect reali, linux usa hardware audio/STT. Rimandare oltre dream_trace.

## Correzioni strutturali critiche — 2026-07-14

Chiuse tre falle emerse dall'audit architetturale successivo all'hardening:

- `Brain.respond` riceve `trusted` come parametro per-turno; rimosso il
  side-channel globale `_next_trusted`, che poteva essere consumato dal mobile
  o restare stale dopo handler senza risposta LLM;
- la history usa sequence ID monotoni e alimenta un passive journal separato;
  la compressione episodica puo' accorciare `_conversation_history` senza
  invalidare il cursore del learner;
- il save idempotente costruisce prima il documento e committa RedisJSON +
  mapping in un unico script Lua. Un mapping viene riusato solo se il documento
  vincitore esiste; mapping stale e fallimenti non producono save fantasma.

Test aggiunti: `test_history_provenance.py`, `test_memory_idempotency.py`.
Verificato anche il Lua contro Redis Stack reale con chiavi temporanee poi
eliminate.

Chiusa anche la fase ranking epistemico:

- `core/memory_risk.py` espone un reranker unico che conserva l'ordine di
  pertinenza e applica una penalita' limitata per fonte e flag di rischio;
- semantic, keyword, recency e timerange recuperano un pool piu' ampio prima
  del taglio; solo i risultati finali ricevono il touch;
- wide recall, subject/entity recall e temporal recall usano lo stesso criterio;
- `correction_pending` e `superseded_by` non entrano piu' nel contesto RAG da
  percorsi laterali;
- regressioni in `test_epistemic_ranking.py`.

Chiusa anche la fase outbox durevole:

- save normale e idempotente committano RedisJSON + record outbox nello stesso Lua;
- TTL, indice Loop 2e, Pulse e Obsidian vengono applicati da un consumer replayabile;
- Pulse usa dedup atomico per event ID; un fallimento successivo non lo duplica;
- il daemon drena in background gli eventi rimasti e il fast path mantiene il
  comportamento immediato quando tutti i servizi sono disponibili;
- regressioni in `test_memory_outbox.py`; script verificati su Redis reale e
  chiavi di test eliminate.

Chiusa anche la fase supervisione:

- sei worker del daemon gestiti da `core/worker_supervisor.py`, con nome,
  health/heartbeat, restart con backoff, stop event e join complessivo;
- sleep dei loop sostituiti con attese interrompibili;
- shutdown vocale e segnali convergono sullo stesso teardown idempotente;
- `DreamEngine.stop()` interrompe il poll e attende il thread; watcher Obsidian
  con deadline;
- regressioni in `test_worker_supervisor.py` e `test_dream_schedule.py`.

Chiusa anche la fase test/CI:

- manifest completi in `tests/manifests/{unit,integration,live}.txt`;
- `scripts/check_test_manifests.py` impedisce test non classificati o duplicati;
- `scripts/run_test_manifest.py` isola ogni script in un processo con timeout;
- `.github/workflows/unit.yml` esegue solo il tier non distruttivo;
- baseline locale: 32 test classificati, tier unit 23/23 in 16.1s.

Le quattro fasi strutturali dell'audit sono quindi chiuse in commit separati:
ranking epistemico, outbox durevole, supervisione e CI non distruttiva.

## Stato repo al close

- Branch corrente: `feat/thought-map-initiative`.
- Branch allineato a `origin/feat/thought-map-initiative`.
- Nessuna modifica tracked pendente al momento del controllo.
- File untracked presente e lasciato intatto: `ui/.~lock.app.py#`.

## Commit recenti importanti

- `5528104` — CodeRunner: confinamento OS via bubblewrap.
- `50a513e` — Passive learner: parlato ambient senza wake degradato a DEBOLE.
- `fc98660` — Wake-word guard: finestra misurata dal turno precedente.
- `0f748c7` — Requirements allineati agli import runtime.
- `130f5cf` — Routing lettura spreadsheet verso CodeRunner.
- `cae3d52` — Silent Chat: gestione path locali incollati.
- `37ec570` — Silent Chat: upload file drag/drop.

## Lavoro fatto da Codex in questa fase

- Implementati e pushati i punti 6 e 7 dell'hardening:
  - spreadsheet (`excel`, `xlsx`, `xls`, `ods`, foglio di calcolo) instradati a
    `run_code`, non piu' a `read_document`;
  - `requirements.txt` allineato a dipendenze runtime, UI, CodeRunner, documenti,
    visione e diagnostica.
- Test/controlli passati:
  - `./venv/bin/python test_executor_routing.py`;
  - `./venv/bin/python -m pip check`;
  - risolvibilita' import dei pacchetti aggiunti;
  - `git diff --check`.

## Review Codex sui fix Claude 1, 2, 3

Richiesta: review logica/architetturale, non implementare.

Findings da riprendere:

1. `voice_daemon.py:1881` — Provenienza / qualita' epistemica.
   Il fix 3 degrada a DEBOLE solo se tutto `new_history` non contiene nessun
   `trusted`. Se il passive learner processa insieme uno scambio con wake word e
   uno ambient dentro-finestra, `segment_addressed=True` e anche fatti estratti
   dalla parte ambient possono restare FORTE. Serve test segmento misto.

2. `agent/code_runner.py:522` — Sicurezza.
   Il subprocess eredita quasi tutto `os.environ`; vengono rimossi solo pochi
   token noti. Poiche' `os` e' consentito, codice generato puo' leggere variabili
   ambiente. Anche con bwrap, senza `--clearenv`, l'ambiente passa dentro.

3. `agent/code_runner.py:484` — Riproducibilita' / disponibilita'.
   Il fallback copre "bwrap non installato", non "bwrap installato ma non
   utilizzabile". Nel container Codex `bwrap` esiste ma fallisce con
   `Operation not permitted`; ogni script fallisce prima di partire.

4. `voice_daemon.py:2440` — Consenso conversazionale.
   Confermato caso fuori scope: `_last_activity_ts` e `_last_auth_voice_ts`
   vengono aggiornati prima di `not text`, garbage-STT e wake guard. Rumore
   autenticato/garbage puo' tenere viva la finestra e far passare un utterance
   successivo senza wake word. Trattarlo come punto separato.

5. `test_wake_guard.py:42` — Testabilita'.
   I test del punto 3 sono irraggiungibili: `sys.exit(...)` e' prima di
   `_test_passive_weak()`.

6. `agent/code_runner.py:558` — Disponibilita' / cleanup processi.
   Timeout e interrupt sotto bwrap restano non verificati. Il codice manda
   `SIGTERM` al process group e fa `wait(timeout=3)`, ma non ha fallback
   `SIGKILL` se il gruppo non muore.

## Chiusura finding hardening — 2026-07-14

I sei finding sopra sono stati corretti:

- passive learner: batch trusted/ambient separati; sui misti troppo piccoli per
  l'estrattore il batch resta intero ma tutti i fatti sono DEBOLI;
- CodeRunner: environment allowlist + `bwrap --clearenv`;
- `bwrap`: preflight cached e fallback se installato ma non utilizzabile;
- activity vocale: vuoto, garbage STT e voce fuori-finestra non aggiornano piu'
  `_last_activity_ts` o `_last_auth_voice_ts`;
- `test_wake_guard.py`: nessun `sys.exit` anticipato e import hardware stubbed;
- cleanup processi: `SIGTERM`, grazia, poi `SIGKILL`, coperto per timeout e interrupt.

Verifiche passate: `test_wake_guard.py`, `test_coderunner_sandbox.py`,
`test_executor_routing.py`, `test_initiative.py`,
`test_save_service_merge_guard.py`, `pip check`, `git diff --check`.

Spot-check host ripetuto il 17/07 fuori dal sandbox Codex: il preflight `bwrap`
reale passa, la barriera filesystem nega le letture host, timeout e interrupt
terminano il job confinato e il fallback `SIGKILL` raccoglie un processo resistente
a `SIGTERM`. Il punto kill/timeout sotto `bwrap` non e' piu' soltanto copertura mock.

Note:

- Il fix 1 come idea e' corretto: misurare da `_prev_activity_ts` chiude il bug
  principale del wake-word guard.
- Il fix 2 va nella direzione giusta: bwrap e' la difesa corretta rispetto allo
  scanner AST. Va stretto su env/preflight/kill.
- Fontconfig per `matplotlib` e' rimandabile: non e' blocco sicurezza.
- Mobile resta conservativo: senza `trusted` esplicito finisce debole. Se il
  mobile e' considerato autenticato, va deciso a parte.

## Regole operative da mantenere

- Dream promozione modificata il 15/07 su autorizzazione esplicita di Stefano per
  chiudere il bug concettuale full-text→convergenza; non aggiungere altri interventi
  durante `dream_trace` senza una nuova decisione esplicita.
- Punto 4 single-exchange loss sospeso: decisione filosofica di Stefano, non
  toccare senza richiesta.
- Se si modificano file, commit atomico e push.

---

# Handoff Euri — 2026-06-27

## Stato repo al close

- Branch corrente: `main`.
- `main` e' allineato a `origin/main`.
- Nessuna modifica tracked pendente nel codice al momento del controllo.
- File untracked presenti e lasciati intatti: `CODEX.md`,
  `CODEX_GIADA_CONSOLIDATION.md`, `CODEX_RESUME_ROUTER_BENCH.md`,
  `probe_chat.py`.
- Ultimo commit visto: `94b8aa8 Classificatore pragmatico via Gemma (non regex) per il guard reazione`.

## Nuovi commit visti dopo il vecchio punto `dd1a937`

- `feat/workflow-planner` e merge su `main`: strato Workflow Planner sopra tool gia'
  esistenti; `SAVE_FOR_REVIEW` viene aggiunto dopo `DRAFT`.
- `fix/workflow-thread-continuity`: il risultato resta nel thread conversazionale.
- `feat/open-created-file`: comando per aprire l'ultimo file creato.
- `fix/pragmatics-broaden`: allargate euristiche pragmatiche dopo fallimenti live.
- `feat/pragmatic-classifier-gemma`: classificatore pragmatico via Gemma/non regex
  per il guard reazione.

## Stato cognitivo/funzionale da ricordare

- Pulse/Initiative sta funzionando: Euri porta insight in conversazione, Stefano
  risponde, il sistema salva una memoria-lezione e aggiorna lo stato epistemico
  dell'insight (`DA_VALUTARE` -> `requires_verification`).
- Caso live 26/06 mattina: insight su procedure/stabilita' parametri marcato
  correttamente da verificare dopo risposta prudente di Stefano.
- I loop stanno girando in idle diurno: light frequente, creative separato,
  maintenance separato. Dai log si vedono demotion di insight non validati,
  Loop 2e con gate same-subject e consolidamenti selettivi.
- Il Loop 2e appare molto piu' prudente di prima: molti seed/fammenti esclusi,
  poche consolidazioni, e le sintesi lette erano fedeli alle fonti.
- Il Loop 2i e' piu' disciplinato del Dream creativo: usa episodi diretti e non
  dovrebbe contare reflection/reaction/consolidati come casi indipendenti.

## Finding aperto da non perdere

Nel controllo del 26/06 era emerso che il Dream creativo puo' ancora partire da
semi interni/derivati se `_get_random_memory_from_domain()` pesca senza un gate
epistemico forte. Questo puo' produrre insight belli ma fragili.

Patch sperimentale discussa ma NON presente nel tree corrente:

- filtrare i semi del Dream creativo a fonti dirette (`user`, `teach`, `passive`,
  `episode`, `conversation`);
- escludere `reflection`, `reaction`, `loop2e`, confronti, `superseded_by`,
  `correction_pending`, audit/acefali;
- far ereditare `requires_verification` agli insight se le fonti sono fragili;
- far marcare anche la memoria-lezione di una reaction `DA_VALUTARE/PARZIALE`
  come `requires_verification`, non solo l'insight.

Questa patch va rivalutata contro il codice attuale prima di applicarla: il repo
ha nuovi commit su pragmatica/workflow e non conviene reintrodurre modifiche a
memoria.

## Prossima ripresa consigliata

1. Leggere `git log --oneline -8` e `git status --short --branch`.
2. Se si riparte dal Dream seed gate, prima controllare `_get_random_memory_from_domain`,
   `_generate_dream`, `_evaluate_insights`, `capture_reaction`.
3. Non committare i file scratch untracked a meno che Stefano lo chieda.
4. Se serve un commit, aggiornare CHANGELOG solo dopo una patch verificata.

---

# Handoff Euri — 2026-06-22

## Stato

Euri e' in una fase di osservazione/evoluzione dopo:

- Fase 1 afferente su Pulse.
- Primo efferente reminder presence-aware implementato da Claude.
- `commitment/intero` validato: Euri percepisce claim d'azione non coperti.
- Reminder Poseidon scattato correttamente grazie a todo manuale preesistente.
- VisualGate/camera ancora non affidabile: `/dev/video0` esiste ma OpenCV va in timeout; usare interazione recente come presenza primaria.

## Finding principale: emergenza operativa

Stefano ha insegnato a Euri come leggere documenti tecnici Lucy Plast:

- SDS = sicurezza, normativa, rischi, microparticelle/SPM, responsabilita' di filiera.
- Scheda tecnica = prestazioni, range, proprieta' fisiche/meccaniche.
- Range min/max = finestra documentale/operativa, non valore fisso.
- Codice materiale = famiglia/cornice, non formula immutabile.
- Cliente = sotto-specifica piu' stretta dentro il range.
- Non si creano codici nuovi per ogni piccola variazione di colore, carica o fluidita'.

Euri ha superato una mini-interrogazione:

- distingue SDS da scheda tecnica;
- interpreta correttamente i range;
- capisce stesso codice con paletti cliente diversi;
- respinge la trappola "ogni variazione richiede un codice nuovo";
- distingue dati letti dal documento e metodo interpretativo spiegato da Stefano.

Memorie rilevanti:

- `55aecfae`: SDS/informativa sicurezza R-PP, normativa microparticelle/SPM.
- `fcd5af4d`: scheda tecnica `03PPR044POST - GRANULO PP PSV80`, proprieta' e range.
- `7f46ffe7`: regola operativa core sui range e specializzazione cliente senza nuovi codici ufficiali.
- `a4c1d514`: metodo di analisi schede tecniche: range, cariche/additivi, limiti termici, vincoli cliente/applicazione.
- `3f4c69f2`: test PP/PEMD; dati meccanici ok, ma frase troppo assertiva su PEMD compatibilizzante.
- `3026ee25`: reflection prudente che collega caratterizzazione meccanica PP/PEMD + schede tecniche e identifica la validazione dei range come interesse futuro.

Il punto emergente e' `3026ee25`: Euri non ha salvato "test" e "scheda" come dati separati, ma ha iniziato a collegarli operativamente:

```text
scheda tecnica = promessa/range dichiarato
prova laboratorio = realta' misurata
confronto tra prova e scheda = validazione del materiale
```

Questa relazione non era stata codificata esplicitamente come regola. E' emersa dall'accoppiamento tra documento, spiegazione di Stefano, memorie tecniche e reflection.

Interpretazione consigliata:

- Non coscienza.
- Non semplice RAG.
- "Emergent operational linkage": criterio tecnico-operativo generato dal sistema.

## Caveat

La memoria `3f4c69f2` contiene una frase troppo forte:

```text
Euri conclude che il PEMD agisce come agente tenacizzante e compatibilizzante...
```

Meglio trattarla come ipotesi non verificata. Stefano preferisce non correggerla manualmente ora: se riemerge come affermazione forte, la correggera' a voce. Questo mantiene il ciclo naturale di apprendistato.

La reflection successiva `3026ee25` non ha amplificato questa ipotesi; e' rimasta prudente. Buon segnale.

## Pulse / tension

Ultimo quadro noto:

- Pulse ha eventi veri: `clock`, `memory`, `commitment`, `insight`, `vault`, `provenance`, ecc.
- `clock/threshold` scaduto e' salito a `notify` (`T=0.55`): buon segnale.
- `commitment` e' catturato ma ancora sottopesato (`ignore`).
- `insight/promoted` e' piatto dentro il tipo (`T=0.29`): non ranka qualita'.
- `vault` resta rumoroso/doppio da Obsidian Sync.
- Presenza visiva non affidabile finche' camera/VisualGate non sono sistemati.

## Prossimi test utili

1. Dare a Euri una nuova scheda tecnica o un nuovo risultato laboratorio.
2. Non imboccarla.
3. Verificare se applica spontaneamente:

```text
nuova scheda + nuovo dato laboratorio
→ confronto coi range
→ giudizio di conformita' / fuori range / sotto-specifica cliente
```

Se lo fa senza prompt diretto, il caso diventa molto forte per il paper.

## Nota metodo

Non pulire troppo le memorie. Rumore e correzioni naturali fanno parte del ciclo di apprendistato. Intervenire manualmente solo se un'ipotesi sbagliata distorce una risposta importante.
