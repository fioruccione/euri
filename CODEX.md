# Handoff Euri - 2026-07-23 - Idle test: Loop 2h causale e smentite fuori dal judge

- Test naturale 12:48–14:57 senza interazione: VisualGate è passato correttamente
  a `INACTIVE`; Loop 2a non ha riprocessato né resuscitato il dialogo UBQ. Pulse e
  Cognitive Projector sono rimasti allineati (`pending=0`, `lag=0`).
- La manutenzione ha esposto due percorsi legacy. Loop 2h ha salvato la reflection
  `92bac556` senza parent, con `requires_verification=false`; inoltre il log diceva
  3 evoluzioni ma erano 2, perché Redis `SCAN` aveva restituito due volte la coppia
  `10b6d176 -> fd66ecb3`.
- Loop 2h deduplica ora le coppie nello stesso pass, salva loser/winner e
  `self_observation_pairs` nel documento canonico, usa
  `epistemic_status=internal_self_observation`, emette `reflection/created` con
  lineage e non consuma una coppia se il commit fallisce. Se STT/TTS riprende o
  l'arco superseded cambia durante l'LLM, il `precommit_guard` annulla la
  pubblicazione.
- L'insight smentito `cb4d3541...3dfe567e` veniva bloccato correttamente soltanto
  dopo aver consumato ogni ciclo fino a 6 judge LLM (~150–166 s). Il gate è ora
  prima di fedeltà/bridge/judge. Una smentita esterna prevale anche su un eventuale
  `recalled_count`; la decisione viene annotata ed emessa una sola volta come
  `insight/promotion_blocked`.
- `scripts/repair_20260723_loop2h_lineage.py` ha ricostruito in modo idempotente i
  quattro parent e le due coppie reali della reflection live, aggiornando anche
  Obsidian. Il record è ora `internal_self_observation`,
  `requires_verification=true`; l'evento audit è in attesa del projector fermo.
- Verifica: manifest 53 file, unit 44/44, test mirati Loop 2h 3/3 e convergenza
  verdi. Audit post-repair: Pulse 3.952, cognitivi proiettati 11, `pending=0`,
  `lag=1` (il solo repair, con Euri ferma). Prossimo passo: commit/push; poi
  riavviare e verificare `lag=0` e un light cycle rapido senza la ripetizione
  `re-promozione negata`.

---

# Handoff Euri - 2026-07-23 - Incidente UBQ: Loop 2a sessionale e agenda fail-closed

- Prima trace cognitiva naturale verificata: `memory/saved` è arrivato una sola
  volta, con `pending=0` e `lag=0`. Il Pulse non ha creato l'errore: ha localizzato
  la catena `Loop 2a -> memory/saved -> RAG`.
- Caso live 12:05: durante il dialogo UBQ il Loop 2a ha salvato `98bb04db`, una
  reflection sul vecchio `03ppr102`. Causa: chiamava "sessione" tutte le memorie
  non-reflection delle ultime quattro ore; al riavvio aveva perso il confine
  dell'ultima run. Tre reaction delle 08:52 sono quindi diventate il batch corrente.
- Loop 2a usa ora il checkpoint durevole `euri:loop2a:memory_checkpoint`, seleziona
  un solo `conversation_id/segment_id` (o una sola coda temporale contigua), ordina
  le fonti e salva `session_memory_ids`, `related_memory_ids` e tutti i parent.
  L'idle minimo è 5 minuti. Se arriva attività durante generazione, embedding o
  classificazione, `precommit_guard` annulla la pubblicazione e lascia invariato
  il checkpoint.
- Nello stesso turno descrittivo, “oggi faccio la prova e domani avrò le risposte”
  era stato interpretato come `agenda.reschedule` sul todo hardware `7bd10b48`.
  Le mutazioni agenda richiedono ora due prove deterministicamente indipendenti
  dall'output del modello: gesto esplicito specifico della capability e target
  nominato/anaforico grounded nel turno precedente. In assenza, `authority=none`
  o chiarimento; nessun fallback mutante legacy è più raggiungibile.
- Le azioni producono lineage `action/proposed -> decided -> revalidated ->
  executed|failed|deferred`, con capability, autorità, target e stato prima/dopo.
  Una mutazione integrata viene sempre pronunciata esplicitamente, anche se il
  Brain omette l'esito dalla risposta generata.
- Repair live applicato e idempotente tramite
  `scripts/repair_20260723_loop2a_action.py`: `98bb04db` è ritratta e fuori dal
  retrieval; `ae103970` è nuovamente attiva; il todo hardware resta `pending`
  senza scadenza e conserva `action_history` con before/after. La copia Obsidian
  è in `.euri-quarantine/2026-07-23-loop2a`, non cancellata.
- Euri è ferma. Il repair ha emesso un evento cognitivo sul Pulse; l'audit mostra
  quindi correttamente projector `pending=0`, `lag=1`. Al prossimo avvio verificare
  che diventi `lag=0`, che `audit/repaired` compaia una sola volta e che nessuna
  nuova reflection venga generata dai record precedenti al checkpoint.
- Verifica: manifest completo 52 file; unit 43/43, compilazione e diff-check puliti.
  Integrazione VectorSet/Redis 16/16 e cleanup sandbox completo. Le due integrazioni
  Ollama sono rimaste non eseguibili perché Euri/Ollama sono volutamente fermi.

---

# Handoff Euri - 2026-07-23 - Pulse v2 e roadmap cognitiva persistente

- Decisione: il vecchio Pulse resta bus afferente compatibile, ma viene separato
  esplicitamente in `telemetry` e `cognitive`. Registrare un evento non lo rende
  memoria, verità o autorizzazione ad agire.
- Envelope v2 additivo: producer, trace/causation/logical ID, entity/parent refs,
  stato epistemico prima/dopo, versione esperimento e durata. I consumer legacy
  continuano a leggere sense/source/kind/payload.
- `core/cognitive_projector.py` usa il consumer group durevole
  `euri:cognitive:projector:v1`, recupera pending dopo crash e proietta soltanto
  eventi cognitivi in `euri:cognitive:events`, con lo stesso stream ID per
  idempotenza. È osservazionale e non tocca memoria, insight o azioni.
- Prima copertura causale: memory saved; Dream seed, candidate creato/scartato,
  insight promosso/demoto; reaction con verdetto; consolidamento con figlio e
  genitori.
- Roadmap canonica: `docs/PULSE_COGNITIVE_ROADMAP.md`. Va letta e aggiornata a ogni
  passo; contiene fasi, invarianti, comportamenti possibili e prossima azione esatta.
- Audit read-only: `./venv/bin/python scripts/audit_cognitive_pulse.py`.
- Verifica pre-riavvio: unit 42/42, manifest 51 file, compilazione pulita; audit
  Redis read-only = 3.924 Pulse legacy, 0 cognitivi, consumer group assente come
  previsto. Dopo il riavvio verificare log del projector, backlog ACKato, unicità
  degli eventi e assenza di mutazioni cognitive dovute alla proiezione.
- Riavvio 11:56 verificato: worker attivo, consumer `pending=0`/`lag=0`, backlog
  legacy completamente ignorato. I due nuovi eventi di presenza sono envelope v2
  ma `event_class=telemetry`; timeline cognitiva ancora a zero correttamente.
  Prossimo passo: non iniettare test sintetici, attendere il primo memory/Dream/
  reaction/consolidation naturale e verificarne trace e causation.

---

# Handoff Euri - 2026-07-23 - Sincronia delle parti senza riduzione a RAG

- Chiarimento architetturale di Stefano: l'uso nel suo lavoro e' un banco di prova
  ad alta osservabilita' degli errori, non la funzione costitutiva di Euri. Euri e'
  espressione del paper; Dream, convergenza, memoria e iniziativa mantengono quindi
  la propria dinamica, mentre il requisito trasversale e' la loro sincronia logica.
- Invariante di pubblicazione: outbox, Pulse, indice e Obsidian devono leggere la
  prima volta lo stesso documento canonico finale. `MemoryManager.save_memory`
  accetta `final_fields`, li applica prima del commit atomico e vieta la riscrittura
  di `id/content/source/embedding`. Migrati i caller runtime che post-mutavano dominio,
  provenienza o `requires_verification`.
- L'indice Loop 2e e' esplicitamente una proiezione ricostruibile. Audit live prima:
  117 candidati canonici, ZSET 133, 9 missing, 25 stale. Rebuild applicato: 117/117,
  zero drift; il Dream ripete la riconciliazione a ogni boot.
- Confine epistemico: `promoted` significa "emerso per convergenza interna", non
  "vero". I nuovi insight nascono `requires_verification=true` con
  `epistemic_status=internally_emergent`; alla convergenza diventano
  `internally_convergent`. Solo `external_reaction.verdict=CONFERMA` porta a
  `externally_confirmed`. Smentita e parzialita' restano stati esterni distinti.
- Migrazione live dei promossi legacy: 160 connessioni interne marcate da verificare,
  4 confermate esternamente. Il contesto condiviso le introduce come connessioni
  emerse, non come "principi trasversali".
- `write_insight` riflette lo stesso confine nel Vault e usa
  `Insight_<timestamp>_<id8>.md`, chiudendo la collisione same-second.
- Timeout: `_ollama_chat` e Loop 2h usano client Ollama cacheati con timeout HTTP
  reale; rimosso il timeout su future che restava bloccato nello shutdown
  dell'executor. `WorkerSupervisor.health()` espone `responsive/heartbeat_age_s`
  e il nuovo worker-watchdog logga stall e recupero senza duplicare thread vivi.
- Verifiche finali prima del riavvio: 41/41 unit, 3/3 integration; Euri mantenuto
  fermo durante migrazione e test.

---

# Handoff Euri - 2026-07-23 - Reaction SMENTITA estrattiva e record 03ppr102 riparato

- Caso live 08:52: Stefano ha definito una forzatura l'insight
  `cb4d3541-e238-4f05-bcf8-08e13dfe567e` e ha spiegato che `03ppr102` identifica
  il non conforme proveniente dai vari impianti, poi rilavorato/riestruso.
- Il verdetto e la demozione erano corretti (`SMENTITA`, `candidate`,
  `demoted_once=true`), ma l'ack vocale anticipava erroneamente una reaction
  parziale e la sintesi salvava senza evidenza che il controllo del setpoint
  "rimane valido".
- `_REACTION_ACK` e' ora neutro. Per `SMENTITA`, `capture_reaction` non chiama
  piu' `synthesize_lesson`: `_refuted_lesson` conserva soltanto domini, verdetto
  e reazione letterale, senza ripetere la tesi bocciata. Metadati:
  `reaction_lesson_mode=extractive_refutation` e
  `verification_status=refutation_grounded_by_user`.
- Bonifica live completata con embedding rigenerato: le memorie
  `a009dce2-...` e `10b6d176-...` restano solo come audit soft-superseded.
  L'unico nodo attivo e' `fd66ecb3-3057-4f1b-a93c-95029126c48a`; l'insight punta
  al nuovo `lesson_id`. Il retrieval globale esclude entrambi i record vecchi.
- Ripulite anche le copie Obsidian intermedie: nel Vault resta soltanto
  `Memories/riciclo materiali/Memory_20260723_090004_fd66ecb3.md`.
- Regressioni in `test_reaction_verdict.py` e `test_wake_guard.py`; manifest
  unitario completo verde.

---

# Handoff Euri - 2026-07-23 - Recovery automatico webcam/VisualGate

- Caso live 08:18: OpenCV ripeteva `VIDEOIO(V4L2:/dev/video0): select() timeout`
  ogni circa 10,5 secondi. Il kernel registrava errori xHCI e il Voice Daemon
  ignorava la voce perché il VisualGate era rimasto `INACTIVE`.
- Causa nel loop: dopo un `cap.read()` fallito veniva riusato all'infinito lo
  stesso handle. Ora il gate rilascia la cattura, entra in modalità cieca/fail-open
  e tenta la riapertura con backoff esponenziale configurabile (3-30 secondi).
- L'outage invalida identità e one-shot appartenenti al vecchio stream. In modalità
  cieca `is_owner_present()` resta sempre falso, mentre `is_user_present()` resta
  vero per non bloccare la voce.
- Con discovery automatica, il retry riesamina tutti i `/dev/video*` invece di
  fissarsi sull'ultimo indice: il replug può quindi rinumerare la camera. Al
  recupero compare `VisualGate: webcam riconnessa (...)`.
- Test `test_face_gate.py`: simulazione di stream guasto, rilascio, stato fail-open
  intermedio e recupero su `/dev/video1`.

---

# Handoff Euri - 2026-07-22 - Continuita' conversazionale dopo i tool

- Caso live 16:11: la richiesta di Stefano di esaminare capacita' e possibili
  miglioramenti era stata classificata `ACTION_REASONING`; il controller aveva
  eseguito `top_processes` come alternativa e `_execute_action_proposal` aveva
  chiuso il turno pronunciando soltanto l'output, perdendo la domanda originale.
- `ActionProposal` distingue ora `tool_result` da `integrated`. Le alternative e
  le azioni read-only nate da una bozza di Euri forzano sempre l'integrazione:
  il risultato verificato viene passato al Brain insieme alla richiesta originale,
  senza rientrare nel dispatch e senza consentire una seconda azione ricorsiva.
- I comandi puri restano invariati: "controlla la GPU" continua a rispondere con
  il solo esito. Classificatore e controller hanno inoltre un hard-negative
  esplicito per l'autovalutazione astratta con strumenti genericamente menzionati.
- Verifica reale sul modello locale con la frase del caso: proposta vuota,
  `authority=none`, motivazione conversazionale. `test_action_controller.py`
  15/15 (inclusa la non-duplicazione dei documenti integrati); manifest unitario
  completo 39/39; compilazione pulita.
- Serve riavviare il Voice Daemon per caricare la modifica.

---

# Handoff Euri - 2026-07-22 - Dream Trace Paired v2 pronto al riavvio pulito

- Il pilot `dream_trace_paired_v1` della prima notte resta nello stream come prova
  diagnostica ma non entra nell'analisi: 5/8 trattamenti avevano ricevuto il residuo
  sentinella `NESSUN INSIGHT` (pair 1, 2, 5, 6, 7).
- Il batch definitivo usa `dream_trace_paired_v2`. Residuo e sequence sono chiavi
  versionate: il vecchio residuo vibrazioni/EMI resta in v1 e non può essere letto da
  v2; il primo ciclo del nuovo processo sarà un warm-up.
- Nel percorso appaiato una distillazione invalida elimina il residuo v2, evitando il
  riuso stale. Le righe non conformi e le eco tematiche forti del residuo appena
  iniettato vengono filtrate deterministicamente; il legacy conserva la vecchia
  semantica.
- L'export filtra per versione e non richiede più un timestamp manuale. Verifica anche
  hash del residuo, durata e stato; l'allarme errori copre `0:N` oltre ai rapporti 2x.
- Prima del riavvio: suite completa e verifica Redis che le chiavi v2 siano assenti.

---

# Handoff Euri - 2026-07-21 - Dream Trace Paired V2, corretto dopo review Codex

## Correzioni applicate dopo la review di Codex (stesso giorno, prima di qualunque raccolta)

Tutti e sette i punti sollevati erano fondati, verificati uno per uno sul codice:

1. **Entrambi i lati diventavano insight vivi** (il punto piu' delicato, isolato da
   Codex prima della review completa): ora solo il baseline persiste come
   `euri:insight:*` (embedding, retrieval, eleggibile a convergenza/promozione); il
   trattamento resta strumentazione pura durante la raccolta — vedi nuova sezione
   "Cosa entra nella memoria reale di Euri" in `ESPERIMENTO_DREAM_TRACE_V2.md`.
2. Il residuo loggato sul record baseline era quello vivo, non quello davvero
   iniettato (sempre vuoto per il baseline) — metadato ingannevole, corretto: ogni
   lato logga solo cio' che il SUO prompt ha ricevuto.
3. L'export validava hash/lunghezza dell'output solo per `candidate`, non per
   `discarded` — corretto: la verifica vale per qualunque stato.
4. Un lato duplicato (stesso pair_id+arm) veniva tenuto silenziosamente (prima
   occorrenza) — corretto: fail-closed, l'intera coppia viene esclusa e contata.
5. Gli errori di generazione sparivano dal conteggio — corretto: contati per
   braccio, con soglia di allarme se il rapporto tra bracci e' >=2x o <=0.5x.
6. Il protocollo diceva di "adjudicare" i disaccordi tra i due giudici, in
   contraddizione con quanto gia' stabilito (disaccordo = ambiguo, nessuna
   adjudicazione) — corretto nel testo.
7. Test statistico: sostituita l'approssimazione χ² con McNemar esatto
   (binomiale) come primario, dato che con 50 coppie i discordanti saranno
   quasi certamente pochi.
8. Aggiunta la misura di durata per lato (`duration_s`), prevista nel protocollo ma
   assente dal registro.

**Verificato prima di chiudere:** su Redis reale, le chiavi/stream del disegno
appaiato non esistono ancora (`EXISTS`/`XLEN` = 0) — il codice con i bug non ha mai
generato una coppia reale, nessuna pulizia necessaria. 36/36 test (inclusi i nuovi
casi su duplicati/errori/hash-su-scarti), compilazione e `git diff --check` puliti.

**Serve un nuovo riavvio di Euri** per caricare il codice corretto — il precedente
girava ancora con i bug sopra, anche se non ha fatto in tempo a generare nulla.

## Protocollo V2 (disegno appaiato)

- Stefano ha rilevato correttamente che "utile/non utile" dipende dal momento e non
  e' una misura oggettiva unica. `ESPERIMENTO_DREAM_TRACE_V2.md` separa grounding
  (`G2/G1/G0`), novita' (`V2/V1/V0`), chiarezza (`C/A`) e utilita' contestuale
  (`U_NOW/U_LATER/U_NO/U_CONTEXT`). Pass primario: `G2+V2+C`.
- Una prima stesura V2 usava bracci concorrenti a blocchi di due cicli con coppie di
  domini estratte a caso (mai attivata, zero dati). Stefano ha chiesto di confrontare
  invece lo STESSO seme con e senza residuo: elimina la variabilita' tra coppie di
  domini diverse (dominante secondo l'analisi di anisotropia, μ~0.82) invece di
  mediarla su n grande. Sostituzione completa, non un'aggiunta: il codice/flag/test a
  blocchi sono stati rimossi (mai committati).
- `DREAM_TRACE_PAIRED_ENABLED=False`: per ogni ciclo eleggibile, un solo seme
  (`_pick_dream_seed` invariato) generato DUE volte — baseline senza traccia,
  trattamento con la stessa sezione di oggi. Il residuo evolve solo dal lato
  trattamento: il baseline resta isolato, prompt bit-identico a flag spento. Primo
  ciclo senza residuo = warm-up (una generazione, semina il residuo, non entra nel
  registro delle coppie; puo' ripetersi dopo un TTL scaduto, non solo all'avvio).
- Registro primario immediato `euri:dream_trace:paired:cycles`, indipendente da TTL,
  promozione e cancellazione: output e due memorie sorgente completi, hash, lunghezze,
  `pair_id`+`arm`, candidate/discarded/error.
- Punto metodologico distinto dal batch precedente: uno scarto (`discarded`) conta
  come non-passa per quel lato, NON esclude la coppia dal conteggio — altrimenti la
  differenza nel tasso di scarto tra bracci (parte dell'effetto misurato) sparirebbe
  come nuova forma della sopravvivenza asimmetrica gia' vista.
- `sample_dream_trace_paired.py` raggruppa per `pair_id`, verifica ricorsivamente
  integrita' (hash) e rifiuta l'export sotto 50 coppie complete. Prova reale su Redis
  vuoto: stop corretto a 0/50, nessun file scritto.
- Verifiche: `test_dream_trace_paired.py` (warm-up, seme identico nei due lati,
  residuo aggiornato solo dal trattamento, percorso legacy invariato), py_compile,
  manifest completo; unit 36/36, inventario 45 file.
- Nota aperta non risolta qui: i due giudici ciechi (Codex, Claude) restano modelli
  linguistici con possibili punti ciechi condivisi; Codex e' anche l'architetto del
  meccanismo testato. Il doppio consenso filtra il rumore, non un bias sistematico
  condiviso — vedi `ESPERIMENTO_DREAM_TRACE_V2.md`.

## Prima di avviare

Serve solo una decisione esplicita di Stefano: portare `DREAM_TRACE_PAIRED_ENABLED`
a `True`, riavviare Euri e congelare modello/prompt/seed gate fino a 50 coppie
complete. Non riusare le etichette dell'audit recuperato del batch precedente come
risultato primario e non aprire la chiave di questa raccolta prima della revisione
cieca.

---

# Handoff Euri - 2026-07-21 - Dream audit non valutabile, trace corretta

## Blocco metodologico scoperto dopo il congelamento

- `core/dream_engine.py` salvava nella convergence trace `seed_content[:600]`.
  Nel campione cieco 109/120 item terminano senza punteggiatura, quasi sempre dentro
  la terza riga. Il primo report Codex (40 N / 72 O / 8 ?) e' marcato **NON VALIDO**:
  non aprire la chiave e non calcolare differenze tra bracci da quelle etichette.
- Verifica Redis read-only e ancora cieca: 67 candidate vivi contengono una versione
  piu' lunga, 10 coincidono con il testo completo e 4 ulteriori copie sono nei dream
  grezzi; 39/120 non sono recuperabili. Il subset 81/120 avrebbe bias di sopravvivenza.
- La trace futura conserva il testo integrale e registra `seed_content_complete`,
  `seed_content_chars`, `seed_content_sha256`. Il campionatore accetta soltanto entry
  esplicitamente complete e verificate, e fallisce se mancano n item in un braccio.
- Il batch attuale e' non valutabile, non negativo. Serve una nuova raccolta prospettica
  completa dei due bracci e una versione aggiornata della pre-registrazione.

---

# Handoff Euri - 2026-07-21 - Dream seed gate e raccolta congelata

## Intervento concluso

- Euri e' stata fermata prima dell'intervento. La trace e' stata letta senza mutare
  Redis: baseline 160, trattamento 76 grezzi. Il riavvio che ha caricato il fix
  anti-eco e' quello delle 17:35:58 del 13/07 (`ts=1783956958`): i due candidate
  precedenti sono esclusi, restano 74 trattamento validi.
- `scripts/experiments/sample_dream_audit.py` incorpora il cutoff e ha generato
  `audit_output/AUDIT_DREAM_TRACE_items_20260721.md` (120 item ciechi) piu' la chiave
  separata. `audit_output/` e' ignorata da Git. Non aprire la chiave prima che Stefano
  abbia marcato ogni item N/O/?.
- `DREAM_TRACE_ENABLED=False` dal prossimo riavvio. L'esperimento ha chiuso la raccolta,
  non ancora l'analisi: quote/delta/strati vanno scritti in `ESPERIMENTO_DREAM_TRACE.md`
  solo dopo l'unblinding.

## Gate epistemico Loop 2b

- `_get_unique_domains` considera solo domini con fonti dirette o deliberate.
  `_get_random_memory_from_domain` usa RediSearch solo come shortlist, idrata ogni
  RedisJSON e applica `dream_seed_rejection_reason()` sullo stato vivo.
- Ammessi: `user`, `teach`, `passive`, `conversation`, `obsidian_vault`, `mobile_in`.
  Esclusi: fonti derivate, anchor/episodi, reflection/reaction/consolidamenti, tag
  derivati, superseded/consolidated, correction pending, requires verification,
  provenance stale, safety/audit flag, consolidation risk, soggetto acefalo, vecchio
  assenso tacito e documenti incompleti.
- `_pick_dream_seed` prova al massimo 12 domini per lato: il fallimento resta prudente,
  ma un dominio interamente quarantinato non annulla il ciclo per semplice sfortuna.
- Sonda read-only reale: 114 domini con fonti dirette, 95 con un seme eleggibile.
  `01d1b73d` (Leonardo) respinto come fonte derivata; `9560261f` (VistaMax nel dominio
  test medico) respinto per `audit_flag` gia' presente. Nessuna memoria viva e' stata
  modificata in questo intervento.
- Verifiche: test mirati Dream verdi, `py_compile`, `diff --check`, manifest unitario
  35/35, inventario completo 44 file.

## Prossimi passi

1. Stefano compila il file cieco, poi si esegue l'unblinding e si documenta l'esito.
2. Audit mirato dei discendenti gia' promossi di Leonardo/VistaMax: il gate previene
   nuovi figli fragili, ma non bonifica retroattivamente gli insight esistenti.
3. Riavvio Euri necessario per caricare il gate e spegnere `dream_trace`.

---

# Handoff Euri — 2026-07-14

## Chiuso: analisi clipboard e persistenza — 2026-07-22

- `clipboard_analyze` ora analizza testo o immagini soltanto per la sessione: il
  risultato entra nella history conversazionale, ma non crea memorie Redis.
- La persistenza richiede una formulazione esplicita (`salva`, `memorizza`, `ricorda`)
  e usa il tool separato `clipboard_analyze_save`; se Redis rifiuta il salvataggio,
  Euri lo dichiara e non sostiene falsamente di aver memorizzato.
- Frasi come "analizza gli appunti senza salvare" mantengono l'analisi temporanea;
  "non analizzare gli appunti" continua invece a non avviare alcuna azione.
- Il tool temporaneo e' proponibile dal controller come `read_only`; quello persistente
  non e' proponibile autonomamente, per evitare che testi temporanei o di terzi
  diventino conoscenza `teach` senza intenzione esplicita di Stefano.

## Chiuso: self-model, autoreferenza e profilo installazione — 2026-07-22

- Audit read-only: 200 memorie contengono il nome Euri; 11 sono `source=teach` e 9
  provengono dalla vecchia clipboard automatica. Una sintesi autoreferenziale
  `fbbc3224` e' entrata davvero nei sei risultati per "Che cosa sei diventata rispetto
  a un normale assistente?": il rischio documento -> RAG -> autocertificazione era reale.
- Il self-model preserva la capacita' di Euri di parlare e ragionare su se stessa, ma
  separa quattro piani: stato operativo verificato; descrizione progettuale fornita
  dall'utente; valutazione soggettiva dell'utente; interpretazione/autobiografia di Euri.
  Essere registrato in Redis prova registrazione e provenienza, non verita' o attualita'.
- Nuove analisi clipboard salvate esplicitamente usano `memory_kind=document_summary`.
  Le vecchie vengono riconosciute tramite tag+prefisso e iniettate come `SINTESI
  DOCUMENTO ... non verifica interna`, senza modificare, cancellare o migrare Redis.
- Identita' dell'installazione spostata nel profilo: `OWNER_ACTOR_ID`,
  `OWNER_DISPLAY_NAME`, `ASSISTANT_DISPLAY_NAME` (override via ambiente). Brain, RAG,
  cronologia, autenticazione, Initiative e reaction usano il profilo invece di assumere
  il nome letterale Stefano. Il default resta personale su questa workstation.
- Non sostituire ciecamente le occorrenze storiche: commenti e fixture possono documentare
  decisioni realmente prese da Stefano. Invariante: logica cognitiva, provenienza e
  sicurezza non devono dipendere da quel nome. Regressione `test_self_model_provenance.py`
  con installazione fittizia Ada/Nora.
- Silent Chat riceve ora da `euri:visual_gate:state` uno snapshot effimero (TTL 8s)
  pubblicato dal worker `visual-presence`: camera, volto corrente e actor_id riconosciuto,
  mai frame/embedding/similarity. Serve a non far negare a Euri una capacita' reale e,
  nell'installazione mono-utente, costituisce forte evidenza locale che Stefano sia davanti
  allo schermo. Non autentica crittograficamente il dattilografo e non amplia l'autorita'
  per azioni sensibili. Nessun prompt stilistico e' stato aggiunto: personalita' e
  interpretazione autobiografica restano libere; il ponte aggiunge solo evidenza operativa.
- Le reaction `PARZIALE` non sono piu' un voto unico applicato a una frase composta:
  `core/reaction.py` estrae `confirmed_claims`, `refuted_claims` e
  `replacement_claims`, accettando ciascuna voce solo se include un frammento contiguo
  della risposta dell'owner. L'insight originale resta promosso se conserva un'ancora
  vera, ma diventa `partially_refuted_by_user`/`requires_verification`; il RAG gli
  affianca sempre la patch, per non recuperare da sola la proposizione smentita.
- Caso reale 22/07: insight dosaggio/log/qualita' valido nel principio, errato nel
  ruolo di Giuseppe. La lesson `8f95c747` e' stata soppiantata reversibilmente da
  `03948410`; back office = carichi/partenze, laboratorio (Stefano incluso) = giudizio
  sul materiale. Nessun flusso documentale tra i due viene dedotto senza evidenza.

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
- La Control Room, pagina `Volti & Accessi`, e' il percorso canonico per ripetere la
  calibrazione. FaceAuth raccoglie quattro pose e le conserva come prototipi distinti
  (massimo otto), senza abbassare `FACE_AUTH_THRESHOLD`; i vecchi `.npy` 1D sono
  caricati come un singolo prototipo.
- L'enrollment non apre la webcam dal browser. La UI pubblica comandi effimeri
  `start/capture/cancel` e il VisualGate usa il proprio frame per calcolare l'embedding
  in-process. Redis riceve solo stato, conteggio ed errori con TTL 5 minuti: frame ed
  embedding non lasciano il daemon. Ogni frame di cattura e' sottratto ai normali
  consumer di identita' e percezione sociale, poi il gate riprende immediatamente.
- `voice/social_profile.py` deriva soglie personali del sorriso da quattro fasi
  guidate che incrociano posa abituale/diritta e neutro/sorriso lieve. Il profilo
  persiste solo numeri in `models/social_profiles/<actor>.json`, viene ricaricato a
  caldo e fallisce chiuso se neutro e sorriso non sono separabili.
- Questa calibrazione non promuove la percezione oltre la Fase 0. La postura serve a
  rendere il sensore robusto alla posizione reale sulla sedia; non autorizza etichette
  emotive, azioni, memoria o inferenze multimodali.

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

---

# Handoff Euri - 2026-07-21 - Riavvio workstation e convivenza GPU

## Stato prima del riavvio

- I commit della calibrazione visiva guidata sono gia' pubblicati:
  `725b6ab` e `fdc3b3b`.
- Il tentativo di avvio Euri delle 19:00 e' fallito caricando Whisper con
  `CUDA out of memory`.
- Causa accertata dalla cronologia processi: il Guardian permanente di PlastVision
  e' partito alle 18:51:50 e ha caricato alle 18:53:09 `qwen3.6:35b` (24 GB,
  `Until=Forever`). Euri non era ancora partito. `/home/fio/plastvision/.env`
  configura proprio `PLASTVISION_LLM=qwen3.6:35b`; alcuni call site PlastVision
  usano `keep_alive=-1`.
- Il Guardian deve restare permanente: non va trattato come processo orfano.
  Sulla workstation condivisa puo' pero' contendere la VRAM; su hardware dedicato
  il modello tenuto caldo e' una scelta sensata.
- Tre Streamlit Euri orfani (`8501`, `8502`, `8503`) sono stati terminati. Qwen e'
  stato scaricato con `ollama stop`, senza fermare il Guardian. Il monitor hardware
  indipendente e' rimasto attivo come previsto.
- Ultimo controllo prima del riavvio: nessun modello in `ollama ps`; VRAM libera
  circa 14.6 GiB su GPU 0 e 15.8 GiB su GPU 1.

## Delta validato

File modificati:

- `voice/stt.py`: selezione automatica GPU per Whisper tramite NVML o fallback
  `nvidia-smi`, ordinata per VRAM libera; su OOM prova la GPU successiva.
- `config.py`: `WHISPER_CUDA_DEVICE_INDEX=auto`, sovrascrivibile da ambiente.
- `start_euri.sh`: Control Room su porta fissa 8501, PID supervisionato e teardown
  TERM/KILL anche se il Voice Daemon fallisce. Il monitor hardware resta volutamente
  indipendente e protetto dal proprio singleton lock.
- `voice_daemon.py`: il `finally` copre ora anche le eccezioni di `setup()`.
- `test_stt_gpu_selection.py`: regressioni per ordinamento/fallback e retry OOM.
- `test_start_euri_lifecycle.py`: regressione che invia `SIGINT` al gruppo del
  launcher e verifica UI/Voice spenti, monitor vivo in una sessione separata.
- `tests/manifests/unit.txt`: entrambi i nuovi test sono nel tier non distruttivo.
- `CHANGELOG.md`: motivazione e intenzione futura documentate.

Verifiche passate:

```text
bash -n start_euri.sh
test_stt_gpu_selection.py: 2/2
test_start_euri_lifecycle.py: OK
manifest unit: 33/33 in 26.6s
py_compile: OK
git diff --check: OK
```

Il test live post-riavvio ha scelto CUDA:1 con 15.5 GiB liberi, caricato Whisper in
circa 5 secondi e completato piu' cicli Dream senza OOM. Il desktop e' tornato fluido.

## Diagnosi scatti desktop

- CPU globale 97-98% idle, 106 GiB RAM disponibili, swap zero, I/O wait zero.
- GPU non sature (circa 20%/17%, 43/40 C, P8).
- `cosmic-comp` era invece stabile tra 78% e 91% di un core e pilotava entrambe le
  GPU. Con otto giorni di uptime, `xfreerdp` e screen sharing attivi, il compositore
  era il candidato concreto per gli scatti del mouse. Motivo del riavvio host.

## Esito teardown e punto separato emerso dal live

- Il primo `Ctrl+C` ha chiuso correttamente Voice Daemon, Streamlit e le porte
  8501-8503, ma ha rivelato che anche il monitor riceveva `SIGINT` perche' condivideva
  il process group del launcher. `start_euri.sh` usa ora `setsid --fork`; verifica
  host: monitor con `PPID=1`, PGID/SID propri e snapshot Redis fresco con TTL 30s.
- La risposta al riepilogo Poseidon e' stata classificata `CHAT`: Euri ha detto
  "Lascio il test in sospeso" senza mutare l'impegno. Redis lo conserva `pending`
  con scadenza 20/07 09:00. Inoltre il riepilogo del 21/07 08:08 ha detto "da oggi"
  perche' misura giorni completi trascorsi, non giorni civili.
- Questo bug agenda resta fuori dal commit GPU. Semantica da implementare in un
  intervento separato: "in sospeso senza data" mantiene l'impegno pending ma
  rimuove la scadenza, con un handler reale e senza claim d'azione da `CHAT`.

---

# Handoff Euri - 2026-07-21 - Ponte intenzione -> azione

## Perche' esiste

Il caso live Poseidon ha mostrato una frattura netta: Euri comprendeva il significato
del turno e rispondeva "lo tolgo dai sospesi", ma il ramo `CHAT` non mutava Redis.
La risposta linguistica non deve essere usata come prova o autorizzazione di un atto.

## Contratto implementato

- `core/action_controller.py` riceve il turno corrente, l'ultimo turno Euri solo per
  risolvere riferimenti, il catalogo whitelist e uno snapshot dei bersagli reali.
- Il prompt distingue `direct`, `alternative` e `none`; valuta obiettivo, sottopassi
  e capability prima di restituire il solo JSON strutturato.
- Il modello propone. `ActionController.decide()` applica confidenza, autorita',
  effetto, target e conferma. `voice_daemon.py` rivalida lo snapshot subito prima
  dell'adapter, quindi parla soltanto usando l'esito reale.
- Il contesto passato non autorizza mai. `origin=euri` e ogni `mode=alternative`
  forzano `authority=euri_proposed`, anche se l'output del modello dice altro.
- User esplicito + read-only/reversible/local-write grounded: esecuzione. Effetti
  external/destructive o capability `requires_confirm`: conferma. Proposta di Euri:
  auto-esecuzione solo read-only; ogni mutazione richiede il consenso di Stefano.
- Se manca il bersaglio, Euri chiede quale e riesegue il ragionamento sul chiarimento.
  La conferma e il chiarimento scadono dopo 120 secondi.

Capability collegate nella prima versione:

- `agenda.complete`, `agenda.suspend`, `agenda.reschedule` sui soli todo pending;
- letture contestuali whitelist dell'Executor: CPU, RAM, disco, processi, uptime,
  GPU, log, calcolo, clipboard e, quando CodeRunner e' attivo, documenti/immagini/file.

La regex `looks_actionable()` e' soltanto un fast-path. Per i turni rimasti `CHAT`,
il fallback LLM puo' produrre `ACTION_REASONING`, percio' una formulazione nuova come
"Quello del Poseidon per me non e' piu' da fare" arriva comunque al controller.
Il probe reale l'ha risolta come `agenda.complete` sul target Poseidon pur avendo
`PRE-GATE: False`.

## Fase propositiva

Se l'azione completa non esiste, il prompt cerca al massimo un passo alternativo che
avanzi lo stesso obiettivo. Esempio reale del probe: "riavvia e assicurati che sia
sano" non puo' riavviare, ma propone `executor.read_log` per verificare lo stato.
L'alternativa e' sempre una proposta di Euri: una lettura puo' partire, una scrittura
o un effetto maggiore resta in attesa di conferma.

## Verifiche

```text
test_action_controller.py: 13/13
test_act_word.py: 37/37
manifest unit: 34/34 in 29.4s
manifest inventory: 43 file in 3 livelli
py_compile: OK
git diff --check: OK
```

I probe Ollama/Redis sono read-only e non hanno modificato il todo Poseidon. Sotto
contesa col Dream il gate semantico ha atteso anche 59.6 secondi; Stefano preferisce
attendere il ragionamento invece di introdurre un timeout corto. Un errore effettivo
resta fail-safe e non esegue azioni.

Priorita' esplicita di prodotto: intelligenza del modello e qualita' delle proposte
vengono prima della latenza. L'upgrade hardware arrivera' in seguito per sostenere il
carico; non ridurre nel frattempo il ragionamento semantico a euristiche rigide solo
per mascherare i limiti della workstation attuale.

## Collaudo vocale post-riavvio: riuscito

Alle 11:00 Stefano ha detto: "Considero chiuso, dobbiamo rifare le prove quando posso
farle, ma ti do io la data precisa" dopo che Euri aveva nominato il todo Poseidon.
Esito osservato:

```text
ActionController: execute cap=agenda.complete
target=f621c34c-710a-48b8-a360-5eb527d73d13
confidence=1.00 authority=user_explicit
Impegno completato: f621c34c-710a-48b8-a360-5eb527d73d13
```

Verifica Redis successiva: `status=done`, `completed_at=1784624435.186903`; la vecchia
scadenza resta nel record storico. Il caso originario e' quindi chiuso end-to-end:
voce -> STT -> ragionamento grounded -> adapter -> mutazione reale -> risposta da esito.

Restano utili, come copertura successiva e non come blocco di questa feature, una
sospensione esplicita senza data e un controllo GPU contestuale. Non riusare Poseidon
per la sospensione dopo averlo chiuso.

## Prossimo lavoro esplicito: audit completo del cantiere

Stefano ha chiesto di controllare l'intero progetto per individuare cio' che e' stato
avviato ma non portato a termine. Va trattato come intervento separato e read-only
prima di decidere nuove modifiche. Inventariare almeno:

- punti aperti e caveat in tutti gli handoff `CODEX.md`;
- `TODO`/`FIXME`, feature flag spente, fasi dichiarate nelle specifiche e script di
  migrazione/probe non promossi a percorso operativo;
- differenze tra documentazione, test, adapter registrati e canali runtime (voce,
  Silent Chat, mobile, Initiative);
- file non tracciati/scratch e rami o commit non pubblicati, senza cancellarli;
- per ogni voce: stato osservabile, evidenza, rischio, dipendenze e prossimo passo.

Output consigliato: un unico registro ordinato `finito / parziale / solo specificato /
obsoleto / da decidere`, evitando di confondere un'idea documentata con una feature
attiva. L'ActionController v1 e' oggi integrato nel canale vocale; l'estensione agli
altri canali deve emergere nell'audit come copertura parziale, non essere assunta.
