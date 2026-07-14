# Handoff Euri — 2026-07-14

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

Note:

- Il fix 1 come idea e' corretto: misurare da `_prev_activity_ts` chiude il bug
  principale del wake-word guard.
- Il fix 2 va nella direzione giusta: bwrap e' la difesa corretta rispetto allo
  scanner AST. Va stretto su env/preflight/kill.
- Fontconfig per `matplotlib` e' rimandabile: non e' blocco sicurezza.
- Mobile resta conservativo: senza `trusted` esplicito finisce debole. Se il
  mobile e' considerato autenticato, va deciso a parte.

## Regole operative da mantenere

- Non intervenire sul punto 5 / Dream promozione: e' dentro esperimento
  `dream_trace` pre-registrato gestito da Claude.
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
