# Changelog

## 2026-08-19 - Attivazione semantica e consent-first di Loop 2k

- Il frame semantico passa a v7 e distingue `explicit`, `suggest` e `none` per
  la deliberazione competitiva. Richiede problema, alternative visibili,
  confidenza alta ed evidenza letterale grounded; una normale richiesta di
  opinione resta conversazione ordinaria.
- La richiesta esplicita del proprietario avvia direttamente Loop 2k. Una
  opportunità riconosciuta da Euri produce soltanto una proposta con TTL di
  dieci minuti; partenza e rifiuto leggono `CONFIRM`/`REJECT` dallo stesso frame
  semantico, senza trigger regex. Turni non pertinenti non vengono intercettati.
- Un bisogno evidenziale `required` ancora scoperto blocca il torneo. Un solo
  job può essere attivo; il grounding RAG viene selezionato una volta e il
  calcolo prosegue in background mentre la conversazione resta disponibile.
- Voce e Silent Chat condividono pending, singolo slot e coda lavori. Il
  risultato viene mostrato nella chat appena pronto; soltanto i job nati dal
  canale vocale vengono pronunciati, con proprietario presente e canale libero.
  Code, stream UI, pending e lock sono effimeri e separati da memoria, RAG e
  insight.
- Un verdetto `contested` non viene trasformato in vincitore; anche il risultato
  completato è presentato come ipotesi interna con prova di falsificazione.

## 2026-08-19 - Loop 2k Ideation Arena

- Aggiunto un operatore deliberativo esplicito che genera 4–8 ipotesi
  indipendenti sullo stesso problema usando il modello Dream, con prospettive
  dichiarate e pacchetto evidenziale fornito dal chiamante.
- Il gate batch valuta ogni candidato prima della deduplica. L'embedder usa
  soltanto proposta e meccanismo come shortlist; un judge batch distingue la
  stessa decisione da assegnazioni inverse o strategie diverse. Verdetti e
  distanze sono auditabili in `dedup_comparisons`; un judge indisponibile
  conserva entrambe le alternative.
- Confronti pairwise ciechi precedono il ranking. Output mancanti o non
  parsabili del gate falliscono chiusi.
- Copeland è il verdetto primario; Elo viene mediato su rotazioni e ordine
  inverso e resta telemetria. I cicli non transitivi rimangono `contested`.
- Ogni run e i suoi candidati condividono un `generation_group_id`: varianti
  sorelle non possono sostenersi a vicenda come convergenze indipendenti.
- Il risultato vive soltanto in `euri:ideation:*` con TTL, senza embedding e
  con esclusione esplicita da RAG, memoria e insight. Anche il vincitore resta
  una deliberazione interna da verificare.
- Esposti il contratto Python `run_tournament_pipeline`, il metodo
  `DreamEngine.run_ideation_tournament` e il runner manuale
  `scripts/run_ideation_tournament.py`. Nessun trigger automatico è stato
  collegato alle conversazioni o al Pulse in questa prima versione.

## 2026-08-19 - TEACH semantico e fail-closed

- Rimossi dal router lessicale i pattern che trasformavano automaticamente
  “ti racconto/ti spiego” in una sessione TEACH. Una richiesta rivolta a Euri
  perché spieghi qualcosa a una terza persona resta ora una normale CHAT.
- Il frame semantico v6 autorizza TEACH soltanto con destinatario assistente,
  trasferimento di conoscenza, dialogo guidato, atto `INITIATE_TEACHING`,
  confidenza elevata ed evidenza letterale presente nel turno corrente.
- Errori JSON, frame incompleti e classificazioni dubbie falliscono chiusi su
  CHAT; il fallback non può attivare autonomamente una modalità persistente.
- Il contratto semantico accompagna la sessione, gli snapshot di recupero sono
  versionati e una conferma finale non può salvare `source=teach` senza origine
  autorizzata e riepilogo effettivo.
- Le regressioni coprono il caso reale della spiegazione destinata a Davide,
  l'assenza di autorizzazione lessicale, il fallback del parser, l'evidenza non
  grounded, il recovery e il blocco del commit finale.

## 2026-08-18 - Tempo dell'affermazione e date ellittiche ancorate alla fonte

- Le memorie distinguono operativamente pubblicazione (`created_at`), turno
  sorgente (`asserted_at`) e intervallo dell'evento (`event_start`/`event_end`).
  Il retrieval mostra ora anche l'istante assoluto dell'affermazione e segnala
  se il termine di un intervallo è futuro o trascorso.
- Il resolver completa un giorno privo di mese e anno soltanto quando esiste una
  relazione temporale esplicita (`fino al`, `entro`, `dal`). Il calendario usa
  il timestamp del turno e gestisce cambio mese/anno; numeri, quantità e codici
  isolati restano intatti.
- Anche i salvataggi diretti materializzano la forma canonica prima di deduplica,
  embedding e commit. Il verbatim non viene riscritto.
- La memoria passiva `a8de20af…` è stata riparata dalla propria fonte
  `83904387…:3`: «fino al 24» è ora «fino al 24 agosto 2026», con intervallo,
  embedding e copia Vault riallineati e audit idempotente dedicato.
- I replay firmati anteriori a questa modifica selezionano esplicitamente il
  renderer temporale v1, conservando la ricostruzione byte-esatta senza limitare
  il contratto runtime corrente.

## 2026-08-18 - Finestra di prova completa per i judge Qwen3.8

- I log reali separano il problema dalla generazione creativa: REM e wake
  chiudono normalmente in circa 1-2 minuti, mentre bridge e convergenza con
  reasoning pieno incontravano il timeout storico di 200 secondi.
- Il profilo Qwen3.8 concede ora 600 secondi ai judge profondi, mantenendo
  `think=True` e il budget di generazione a 5.000 token. Il predecessore conserva
  il limite storico; timeout e budget restano modificabili via ambiente.
- Bridge e convergenza eseguono al massimo un nuovo giudizio per tipo a ciclo.
  Inoltre il budget bridge conta i tentativi, non soltanto i verdetti riusciti:
  un timeout non può più lasciare intatto il budget e innescare una serie di
  chiamate costose nello stesso passaggio.
- La telemetria distingue ogni chiamata profonda (`bridge`/`convergence`) e
  registra durata, `done_reason`, token di output e dimensione del reasoning,
  così la qualità del nuovo modello può essere valutata su risposte completate.

## 2026-08-17 - Qwen3.8 come modello dei cicli cognitivi

- Il Dream Engine, il modello identitario e gli operatori offline/idle passano
  da `qwen3.6:35b` a `hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M`; Gemma4 26B resta
  il modello conversazionale, perché il cambio non riguarda la voce di Euri.
- Tutti i call site continuano a dipendere dall'unico
  `config.DREAM_OLLAMA_MODEL`. Il nuovo default può essere annullato senza
  modifiche al codice avviando Euri con
  `EURI_DREAM_OLLAMA_MODEL=qwen3.6:35b`.
- Il self-model rende ora l'identificatore configurato realmente, evitando che
  Euri continui a descrivere il predecessore dopo il cambio o durante un
  rollback.
- La calibrazione isolata ha mostrato che il wake con `think=True` esauriva il
  timeout di 240 s; con thinking medio/basso chiudeva in 76–82 s ma ricamava
  dettagli nelle premesse. Il profilo Qwen3.8 usa quindi contenuto diretto e
  budget 2.000 per REM/wake: 35,8 s per un REM divergente di 1.937 caratteri e
  12,6 s per un wake strutturato nel probe. I judge e i gate semantici restano
  invariati e mantengono il thinking.
- Il profilo e' legato al default Qwen3.8: l'override a `qwen3.6:35b` ripristina
  automaticamente `think=True` e budget 4.500 per le fasi creative storiche.
- Il bootstrap del Dream Engine espone nei log modello effettivo e profilo
  REM/wake, così il cambio e un eventuale rollback sono verificabili al riavvio.

## 2026-08-17 - Shadow memoria: dall'esposizione al riuso selettivo

- Chiusa manualmente la revisione shadow v1 dopo 19,1 giorni, 336 risposte,
  409 entità, 3.056 richiami e 642 usi lessicalmente sostenuti ma non provati.
- Il bonus di attenzione Loop 2e non usa più il conteggio assoluto: combina gli
  usi con il numero di esposizioni nella stessa finestra e un prior conservativo
  di cinque esposizioni. Nessuna regola distingue source, dominio o contenuto.
- Il confronto read-only sui 130 candidati reali mantiene 41 rinforzi, ma riduce
  il bonus massimo osservato da `+10` a `+5` e la media da `+1,68` a `+0,44`.
  La memoria hardware iniettata 336 volte passa da `+10` a `+0,73`.
- La migrazione conserva tutti i contatori esistenti, materializza il nuovo
  denominatore e aggiorna l'indice derivato. In sua assenza il bonus è zero.
- Verità, TTL, eleggibilità, retrieval delle chat, identità e promozione degli
  insight restano invariati. Rollback tramite
  `EURI_MEMORY_ATTENTION_POLICY=absolute_count_v1`.

## 2026-08-17 - Loop 2c: stop reversibile alle rivalutazioni terminali

- I candidate già respinti per premesse infedeli o ponte forzato non consumano
  più fedeltà, bridge e judge a ogni ciclo light mentre il verdetto è invariato.
- Il candidate non viene cancellato e conserva audit e TTL ordinari. Una
  conferma esterna esplicita o una misura corretta riaprono la valutazione;
  misure mancanti ed errori transitori continuano invece a essere ritentati.
- Nuove conversazioni e nuovi sogni possono creare candidate indipendenti sullo
  stesso concetto: il gate elimina lavoro ripetuto, non blocca l'apprendimento.
- Rollback operativo tramite `EURI_DREAM_TERMINAL_QUALITY_BLOCK_ENABLED=0`.

## 2026-08-11 - Provenienza metacognitiva e memoria autobiografica

- Le vecchie risposte di Euri restano nello storico e continuano a sostenere
  continuità, personalità e autocritica, ma il prompt chiarisce vicino ai
  messaggi che testimoniano ciò che Euri aveva interpretato e non provano da
  sole fatti esterni.
- Ogni memoria resa nel RAG porta ora una breve origine comprensibile: Stefano,
  documento, Web, turno originale o rielaborazione interna. È una descrizione
  della provenienza, non un voto automatico di verità o affidabilità.
- Euri mantiene il permesso di inferire e costruire analogie; quando usa una
  propria interpretazione precedente non confermata, il comportamento atteso è
  riconoscerla come tale, non sopprimerla.
- Nessuna modifica a ranking, numero di memorie, Loop 2j, retrieval, Pulse o
  guard della risposta.
- Aggiunte regressioni strutturali sul caso Eurostampi e sulle etichette di
  origine.
- Suite unitaria completa: 81/81.

## 2026-08-11 - Gap di conoscenza semantico e fonti conversazionali

- Il frame semantico passa alla versione 5 e distingue fatti non necessari,
  opzionali o indispensabili tramite `evidence_request`, separando premesse,
  dettagli mancanti, entità e fonti ammissibili.
- La decisione resta di Gemma: non sono state aggiunte regex o liste di domini.
  Il bordo deterministico valida soltanto entità già presenti nel frame e il
  vocabolario chiuso del contratto.
- Dopo il RAG, i nodi realmente visibili vengono confrontati con le entità
  richieste. Memorie dirette pulite e turni owner autenticati sono candidati
  fattuali; derivati, contestati e contenuti da verificare non colmano il gap.
- Un gap aggiunge al prompt una policy anti-estrapolazione e pubblica il Pulse
  osservativo `knowledge_gap_detected`. Non cambia intento e non avvia il Web.
- Stefano, documenti aziendali e Web sono fonti possibili scelte semanticamente.
  Un successivo «controlla nel web» viene risolto dal dialogo recente e soltanto
  allora produce la richiesta Web esplicita e la query completa.
- Silent Chat ora esegue davvero l'intento Web condiviso invece di lasciarlo
  ricadere nella risposta ordinaria; usa la query semantica e conserva la fonte
  esterna con `requires_verification`, come il percorso voce.
- I casi di regressione coprono confronto Lucy Plast/Eurostampi, analogia
  Peroni/Raffo, vincolo memory-only e autorizzazione Web ellittica.
- Rimossa dal prompt base la specifica ICMA2 cablata: resta una regola generale
  che separa capacità nominale/potenziale da capacità effettiva misurata.
- Suite unitaria completa: 81/81.
- Il probe live Gemma è rimasto non eseguibile perché Ollama non era
  raggiungibile; il fallback è stato corretto, ma il comportamento generativo
  va verificato dopo il riavvio e non è dedotto dai test con output simulato.

## 2026-08-10 - Prompt transport audit e provenienza di insight/reflection

- Il `Brain` cattura i byte HTTP effettivamente inviati a Ollama, con body
  integrale, hash, presenza/offset in caratteri della proiezione identitaria e
  delle sezioni RAG, più `prompt_eval_count` restituito dal server.
- I prompt vivono soltanto in `research_logs/`, esclusa dal repository e dal
  retrieval, con permessi locali, rotazione a 64 MiB, retention di sette giorni
  e scrittura asincrona. Il costo sincrono osservato è 0,29–1,16 ms.
- Il limite del renderer è esplicito: Ollama non espone stringa compilata,
  offset token dei blocchi o flag di troncamento; il log li marca unavailable e
  non li sostituisce con stime.
- Gli insight resi nel RAG includono data ISO assoluta, stato di verifica, tipo
  e produttore. I record legacy privi di origine dichiarano
  `non_registrato_legacy` anziché ricevere un Loop inventato.
- D1 è chiusa per la parte derivabile su una nuova sessione: nei quattro payload
  reali identità e RAG sono sempre presenti come 3° e 4° messaggio su 16. Prompt
  compilato, offset token interni e segnale server di troncamento restano
  genuinamente indisponibili.
- La baseline C pre-D3 non esiste e non è più ricostruibile. Le 4/4 sonde sugli
  insight provano soltanto che la data è leggibile quando viene chiesta; non
  provano l'uso spontaneo né una riduzione di frequenza.
- Lo stesso formato di provenienza è applicato alle reflection, sia ambientali
  sia semantiche. Costo esatto: +100 token per due; su 39 payload la media era
  1,18 e il massimo osservato 5 reflection.
- Sul replay B storico con metadati completi è stato trovato il controesempio:
  `1/4` risposte ha comunque accettato la premessa temporale e parlato di un
  weekend non sostenuto dalle date. Il residuo è documentato per un futuro D5.
- D4 e D5 non sono stati modificati. L'attribuzione di personalità emergente è
  sospesa nei documenti: D2 corretto riproduce le quattro regolarità con Gemma
  nuda e gli stessi contenuti iniettati.
- Suite unitaria completa: 81/81.

## 2026-08-09 - Loop 2j: schemi associativi attivi nel retrieval

- Aggiunta una proiezione schematica ricostruibile che collega memorie dirette
  tramite entità esplicite, senza sintetizzare fatti o riscrivere i nodi canonici.
- La vista è generazionale: una build incompleta non sposta il puntatore attivo;
  il boot la rende subito disponibile e la maintenance la rigenera dopo
  supersessioni, consolidamento e propagazione di provenienza.
- Il retrieval può seguire un solo arco e riserva al massimo due fonti originali
  (un terzo del contesto). Query temporali, demo, scope sperimentali e memorie
  ambientali recenti non attivano l'espansione.
- Il gate esclude derivati, quarantene, supersessioni, soggetti acefali, assenso
  tacito e falsi propri linguistici; valori numerici nudi restano al percorso
  identifier-first e non diventano schemi.
- Le proprietà ripetibili e gli acronimi brevi sono `contextual_only`: `PP` o
  `MFI` non collegano da soli aziende/codici differenti. Serve un'ancora esplicita
  o una concordanza multi-legame con almeno un'ancora non ambigua. Le query
  composte richiedono l'intersezione completa degli schemi nominati: coperto il
  contro-caso «birra bionda Peroni» contro «birra bionda Raffo».
- Prima proiezione reale finale: 1.604 documenti letti, 323 eleggibili, 69 schemi
  e 461 appartenenze. Audit mirato dello schema Lucy Plast:
  23 fonti distribuite in 14 domini, senza soggetti estranei osservati.
- Regressioni dedicate in `tests/test_memory_schema.py`; documentazione canonica
  aggiornata in `docs/EURI_MEMORY_ARCHITECTURE.md`.
- Il primo collaudo Silent Chat ha mostrato un limite reale: una richiesta
  esplicita su Lucy Plast poteva recuperare una `reflection` ICMA2 senza aprire
  lo schema Lucy Plast, perché il 2j dipendeva da un hit corretto del vecchio
  retrieval. La risposta successiva sulla provenienza ha poi inventato una
  deduzione, benché il fatto fosse registrato come `source=user`.
- Il frame semantico condiviso passa alla versione 4 e chiede alla Gemma già
  calda un piano `memory_retrieval`: necessità, entità focali con rilevanza e
  ruolo, relazione ed obiettivo dell'evidenza. Nessuna nuova regex interpreta
  il linguaggio; il codice valida soltanto che i focus siano entità realmente
  ancorate nel frame e applica i limiti epistemici del 2j.
- Silent Chat, voce e mobile consegnano lo stesso piano al RAG. Una decisione
  affidabile `needed=false` impedisce l'espansione per menzioni incidentali;
  `overview` può aprire direttamente uno schema nominato anche quando il ranking
  base lo manca; `comparison` conserva un piccolo bucket per ogni entità;
  `provenance` espone source e memory ID e vieta spiegazioni cognitive inventate.
- Prova reale Gemma: panoramica Lucy Plast → focus/overview; frase generale
  Lucy Plast–Eurostampi → `needed=false` pur riconoscendo relazione ed entità;
  origine ICMA2 → focus/provenance. Suite unitaria completa: 80/80.

## 2026-08-08 - Integrità dei claim identitari

- Il validatore della proiezione identitaria non tronca più a metà frase i claim
  troppo lunghi: li respinge integralmente e il prompt richiede una sola frase
  completa entro un limite prudente.
- Aggiunta una regressione che impedisce a un claim sovradimensionato di
  diventare un tratto `stable` semanticamente mutilato.
- Riparato nella proiezione runtime il primo tratto relazionale emerso, usando
  una formulazione breve fondata sulle stesse due citazioni owner già validate;
  i turni canonici non sono stati modificati.
- Verificato dopo il riavvio che il tratto `stable` raggiunge Silent Chat
  owner-scoped e orienta una risposta coerente senza essere recitato. La prova è
  registrata come evidenza funzionale proiezione→comportamento, non come prova
  di esperienza soggettiva o di iniziativa spontanea.

## 2026-08-07 - Correzioni, aggiunte e sorgenti reali nel SAVE

- Il router non interpreta più il generico «aggiungi che» dentro un discorso
  come richiesta di memoria: «aggiungi in memoria che» resta esplicito, mentre
  richieste finali come «ora riscrivi» rimangono conversazione.
- Il risolutore condiviso distingue ora `add`, `correct` e `replace`. Le
  correzioni usano la riscrittura correttiva già esistente e non il merge
  costruttivo che, per contratto, conserva tutti i dettagli della versione
  precedente.
- Negazioni, limiti e confini operativi diventano parte vincolante della
  correzione; in caso di errore del modello viene salvata la parola dell'utente
  invece di reintrodurre una memoria smentita.
- «Memorizza il contenuto della clipboard/del documento attivo» usa ora
  l'artefatto fisico condiviso tra voce e UI. La memoria viene ricavata dal
  contenuto reale, non dall'istruzione o dalla precedente risposta di Euri.
- Aggiunte regressioni sul caso Assistente Ufficio: niente falso `SAVE_MEMORY`,
  niente union-merge durante una correzione e sorgente clipboard verificata.

## 2026-08-07 - Clipboard autonoma e crash Silent Chat

- Corretto il `NameError: logger is not defined` nel percorso con cui la Silent
  Chat registra l'arbitraggio dell'intent semantico.
- La clipboard decide dal contenuto effettivo: accetta il ramo vision soltanto
  quando i byte dichiarati `image/png` hanno davvero la firma PNG; altrimenti
  prosegue autonomamente con il testo, senza richiedere all'utente di specificare
  il formato.
- Un errore di `Brain.analyze_image` torna ora come assenza di risultato e i tool
  lo espongono come `FAIL`. Eliminata la risposta contraddittoria «ho analizzato
  l'immagine ... non sono riuscito ad analizzarla» marcata erroneamente `OK`.
- Il documento caricato in UI continua a seguire il workspace condiviso e il
  tool `read_document`; clipboard e tavolo documenti restano sorgenti distinte,
  selezionate dal bersaglio reale della richiesta.

## 2026-08-07 - Rubrica prospettica per la novità Dream

- Separata la novità tecnica (`V0/V1/V2`) dalla sorpresa o utilità contestuale
  per Stefano: «non ci avevo pensato» non è da solo evidenza di `V2`.
- Definito `V1` come composizione diretta «applica A a B» e riservato `V2` a un
  meccanismo, vincolo, previsione falsificabile o criterio decisionale nuovo,
  fondato e dipendente da entrambe le fonti.
- Congelato fuori da futuri campioni blind il primo caso live REM→wake
  clipboard×bozza-clienti, classificato `G1/V1/C`: il dettaglio non sostenuto che
  causa `G1` non può essere riusato per rivendicare novità.
- I prossimi blind useranno ruoli anonimi Valutatore A/B; nessuna persona esterna
  al protocollo viene inferita come annotatrice. V2/V3 restano archivi storici e
  non vengono rietichettati.
- Specifica in `docs/EURI_DREAM_NOVELTY_CALIBRATION_2026-08-07.md`.

## 2026-08-07 - Test raccolti nella directory canonica

- Spostati i 89 script `test_*.py` dalla root a `tests/`, mantenendo i tre tier
  espliciti `unit`, `integration` e `live`.
- Manifest aggiornati con percorsi relativi completi; il runner rende esplicita
  la root in `PYTHONPATH` e il checker inventaria esclusivamente `tests/`.
- Allineati i test che derivavano la root del repository da `__file__` e i
  comandi diretti documentati. La classificazione continua a impedire che test
  live o integrativi entrino accidentalmente nella CI.
- Il daemon non collega il sink `logs/voice_daemon.log` quando il runner espone
  `EURI_TEST_TIER`: le regressioni mantengono l'output nel proprio processo ma
  non contaminano più la cronologia operativa di Euri.

## 2026-08-07 - Personalità emergente e modello relazionale

- Aggiunta una proiezione owner-scoped ricostruibile dai turni canonici: separa
  tratti di Euri, preferenze operative dell'interlocutore e dinamiche della
  relazione, senza inserirne il contenuto nel system prompt.
- Il Dream Engine usa Qwen in idle per proporre pattern soltanto dopo almeno
  otto nuovi turni owner e con almeno sei ore fra consolidamenti validi. Il
  bootstrap considera il presente recente; i batch successivi avanzano in
  ordine tramite checkpoint durevole.
- Il gate accetta esclusivamente citazioni contigue di turni user autenticati
  nello scope personale e richiede almeno una fonte nuova. Le risposte di Euri
  sono contesto per capire il feedback, mai evidenza del suo carattere.
- I tratti espliciti possono stabilizzarsi da una dichiarazione owner; i pattern
  inferiti richiedono tre turni in almeno due segmenti/conversazioni. Le
  contraddizioni producono `contested`, escluso dal realtime; i pattern non
  rinforzati decadono dal rendering senza cancellazione storica.
- `Brain` riceve al massimo nove tratti stable in una vista marcata come
  derivata, non fattuale e non diagnostica. Voice applica il modello soltanto
  all'owner autenticato; Silent Chat e Mobile usano l'actor owner del canale.
- Documentati otto paletti in `docs/EURI_EMERGENT_PERSONALITY.md`; regressioni
  dedicate in `test_personality_model.py`.
- Evidenza runtime del bootstrap: Qwen 3.6 ha consumato prima 2.600 e poi 5.000
  token interamente nel reasoning (`content_chars=0`, `done_reason=length`). Il
  consolidatore identitario, che deve consegnare dati alla veglia, usa quindi
  `think=False` e JSON vincolato; REM e cicli creativi restano invariati. Un
  output invalido produce diagnostica di lunghezze e `done_reason` e ritenta
  dopo 20 minuti; le sei ore restano riservate ai consolidamenti validi.

## 2026-08-07 - Dream REM divergente e risveglio lucido

- Ripristinato nel runtime il principio originario del paper: il Dream non viene
  più obbligato a essere operativo mentre nasce. Un primo passaggio REM ad alta
  divergenza produce associazioni, immagini e trasformazioni anche assurde.
- Conservata la reidratazione introdotta il giorno precedente e ampliato il suo
  contratto esplicito: restituisce anche situazione, scopo e filo argomentativo,
  senza convertire il contesto adiacente in nuove premesse. Il REM è caos fra
  ancore complete, non rumore generato da memorie amputate.
- La selezione dei semi preferisce ora la provenienza verbatim sia dentro il
  dominio sia fra i domini campionati; usa il legacy soltanto come fallback
  osservabile, senza eliminare memorie storiche autosufficienti.
- Il REM grezzo vive sette giorni in `euri:dream:*` con stato
  `oneiric_uninterpreted`, senza embedding e con esclusioni esplicite da insight,
  RAG e memoria. Non entra in Obsidian, Initiative o convergenza.
- Un secondo passaggio di risveglio riceve le stesse fonti e il raw marcato come
  non fattuale/non eseguibile. Solo questo passaggio può rispondere nel formato
  operativo, creare un candidate e collegarlo al sogno tramite `rem_dream_id`.
- La telemetria separa il costo della generazione REM da quello del risveglio,
  così qualità onirica e costo hardware possono essere misurati senza confonderli
  con i giudici epistemici eseguiti successivamente.
- Fedeltà delle premesse, validità del ponte, convergenza e filtro di rilevanza
  restano invariati. Il rigore viene spostato a valle, non rimosso.
- Congelato il paired V3 prima della soglia pianificata: REM→wake cambia il
  protocollo e non può essere mescolato nello stesso batch. I flag dream_trace
  sono spenti; il nuovo costo sostituisce le due generazioni appaiate con REM +
  risveglio.
- Aggiunta la specifica `docs/EURI_REM_WAKE_ARCHITECTURE.md` e aggiornati paper,
  README e mappa canonica della memoria. Nuove regressioni in
  `test_dream_rem_wake.py`.
- Dichiarata la natura funzionale, non biologica, dell'analogia sonno→risveglio e
  fissati sei paletti di compatibilità per le evoluzioni future. `CODEX.md` rende
  obbligatoria la lettura della specifica prima di intervenire su Loop 2b.

## 2026-08-06 - Dream contestualizzato dalla provenienza verbatim

- Loop 2b reidrata ogni seme recente con il turno realmente citato dalla memoria
  e una finestra bounded di massimo due turni precedenti nello stesso segmento e
  scope. La memoria compatta resta canonica e non viene riscritta.
- Il prompt distingue fonte, contesto precedente e ruolo: una risposta di Euri
  può risolvere un referente, ma non diventa per questo un fatto dichiarato da
  Stefano. Referenti ancora indefiniti devono produrre `NESSUN INSIGHT`.
- Dream e insight registrano separatamente vere `source_turn_refs` e
  `dream_context_turn_refs`; i giudici di fedeltà e validità del ponte ricevono
  la stessa finestra usata in generazione.
- I nodi legacy senza provenienza restano utilizzabili senza completamenti
  inventati. Il protocollo paired riparte con stato isolato
  `dream_trace_paired_v3_hydrated`, evitando di mescolare il nuovo prompt con il
  batch v2.
- Aggiunte regressioni sul caso workstation/P620 contro miscelazione del
  perossido, confini di segmento, fallback legacy, metadati non autorevoli e
  lineage del candidato. Manifest unitario completo: **77/77** in 89,7 s.

## 2026-08-06 - Latenza realtime senza riduzione della logica

- Corretto il falso positivo live del guard atto-parola: “ho aggiornato
  mentalmente il dato” descrive ora una comprensione interna e non apre più
  l'ActionController né genera una falsa smentita. Gli aggiornamenti operativi
  di file/memoria restano protetti; un secondo claim reale nella stessa frase
  non viene mascherato dalla clausola cognitiva.
- La risposta vocale viene ancora generata integralmente e attraversa tutti i
  guard di onestà prima dell'audio. Solo il testo finale viene diviso su confini
  di frase: il primo segmento parte appena pronto e Sherpa sintetizza il
  successivo mentre `aplay` riproduce quello corrente.
- L'interrupt listener resta unico per l'intera risposta. I segmenti successivi
  non terminano e riavviano inutilmente il player CLI; un errore ripiega soltanto
  sulla parte non ancora pronunciata. Il comportamento è reversibile con
  `EURI_TTS_SEGMENTED_ENABLED=0` e la dimensione è configurabile con
  `EURI_TTS_SEGMENT_MAX_CHARS`.
- La telemetria distingue ora `first_ready`, CPU totale di sintesi, playback e
  wall time della pipeline: la durata della risposta non viene più confusa con
  il tempo necessario prima che Euri inizi a parlare.
- Il dual-channel conserva due retrieval e tutti i suoi filtri, ranking, locator
  e gate, ma dominio della query ed embedding vengono calcolati una sola volta
  per turno e riusati dal secondo passaggio. La cache è effimera e non contiene
  risultati di memoria. Nuova telemetria separa base, locator, composizione e
  gate per misurare il prossimo intervento sui log reali.
- Il frame semantico resta sempre attivo e autorevole: nessuna scorciatoia è
  stata introdotta su correzioni, `no_store`, azioni o sorgenti documentali.
- Regressioni TTS/pipeline, query-feature cache e confine cognitivo/operativo
  aggiunte; ultimo manifest unitario: **76/76 in 91,4 s**.

## 2026-08-06 - Confine semantico risposta/azione condiviso

- `compose_document` può ora usare intenzionalmente tre sorgenti distinte:
  documento attivo, conversazione recente o contenuto della richiesta. Il frame
  semantico condiviso sceglie sorgente e ampiezza (`last_exchange`,
  `current_thread`, `recent_turns`) e prevale su eventuali parametri divergenti
  del controller; non esiste un fallback silenzioso dal file alla conversazione.
- La conversazione viene materializzata come artefatto effimero con i reali
  `turn_ref`, senza trasformarla in memoria cognitiva. Nel piano documentale le
  affermazioni di Stefano restano distinte dalle ipotesi/interpretazioni di Euri.
- La ricevuta contiene sorgente, ambito, provenienza e anteprima. La Silent Chat
  mostra anteprima e download anche quando il risultato nasce soltanto dalla
  conversazione e non esiste un documento caricato nel workspace.
- Il frame distingue ora il gesto linguistico dalla sua eventuale esecuzione:
  ogni azione porta `effect_scope=response|read|write|state_change|external` e
  `polarity=requested|negated|hypothetical`. Soltanto un effetto operativo
  richiesto, completo di effetto, target e classe di capability, può aprire il
  controller. “Spiega”, “descrivi”, “argomenta”, “elenca” e “non eseguire
  strumenti” restano conversazione senza regex dedicate alle frasi osservate.
- Voce, Mobile e Silent/Instant Chat ricevono la stessa decisione. Il frame vede
  conversazione recente e stato effimero del documento attivo; quest'ultimo
  risolve “questo documento” ma non autorizza da solo alcuna operazione.
- L'ActionController espone ora l'esito distinto `CONVERSE`: una richiesta
  erroneamente candidata come azione può tornare a CHAT soltanto dopo un
  giudizio esplicito `request_kind=conversation`. Errori, output non validi,
  capability assenti e richieste operative non supportate restano fail-closed.
- Il veto semantico protegge anche i fast-path legacy di voce e UI. I turni
  gestiti fuori da `Brain.respond()` (tool, SAVE e fail-closed) entrano ora
  comunque nell'archivio durevole con raw e frame, senza essere ricandidati al
  learner passivo.
- Replay reale sul modello locale: quattro richieste conversazionali tratte dai
  log non hanno aperto tool; “Crea un documento Word con le informazioni del PDF
  attivo” è rimasto `EXECUTE`, `effect_scope=write`, `polarity=requested`.

## 2026-08-05 - Tavolo documentale condiviso UI/voce

- Corretto il confine degli upload: il workspace usa esclusivamente i file registrati
  dalla Silent Chat, non l'intero contenuto di `dati_per_Euri`; il registro nascosto
  non può più diventare un falso terzo documento. L'ultimo upload è attivo, i
  precedenti restano in coda e la selezione manuale continua a prevalere.
- Il controller riceve ora lo stato reale del documento attivo: riferimenti come
  “questo documento” vengono instradati a lettura/revisione documentale e non alla
  clipboard. I log distinguono inoltre un'azione eseguita da una fallita.
- Chiuso il follow-up vocale PDF→Word osservato alle 17:00: un frame affidabile con
  `REQUEST_ACTION` e un effetto strutturato raggiunge l'ActionController anche se
  `primary_intent` è vuoto/UNKNOWN. Il percorso resta fail-closed se manca l'effetto.
- Esteso il pavimento di onestà a “ho generato/prodotto/esportato/preparato” e
  “sto preparando”: senza tool reale vengono rimosse anche le conseguenti false
  indicazioni “lo trovi/il file si trova/puoi scaricarlo”.
- Chiuso il raccordo emerso nel collaudo serale: anche un intento regex `EXECUTE`
  con `REQUEST_ACTION` passa prima dall'ActionController. “Crea un documento Word”
  raggiunge quindi `compose_document` e non degrada più al `run_code` generico.
- Il workspace pubblica ora anche il lifecycle effimero dell'operazione
  (`running/completed/failed`, canale, file e tool). L'upload UI annuncia il lavoro
  prima dell'inferenza, l'Executor lo prende in carico e voce/UI osservano lo stesso
  esito; la voce può dire che la UI sta analizzando senza attribuirsi il tool.
- Aggiunto `DocumentWorkspace`: manifest Redis owner-scoped e TTL di 30 minuti con
  documenti separati, selezione attiva, hash/versione e ricevute. UI e Voice Daemon
  non dipendono più da due `_session_artifact` RAM incompatibili.
- La continuità è ora anche live: prima di un turno, ciascun processo importa i nuovi
  `turn_ref` dell'altro in modo idempotente e come solo contesto, senza riarchivio o
  apprendimento passivo.
- I DOCX sorgente vengono revisionati su una copia con sostituzioni puntuali e guard
  sull'hash. La verifica preserva sezioni, pagina/margini, header/footer, tabelle,
  stili e numero di paragrafi; documenti nuovi e altri formati mantengono i renderer
  strutturali esistenti.
- La ricevuta del tool è la sola autorità su nome, path e stato finale: la voce non
  può più trasformare “creato” in “sto creando”. Il pannello Silent Chat si aggiorna
  ogni due secondi e offre selezione, riepilogo, warning e download del file reale.
- Nuove regressioni in `test_document_workspace.py` e ampliamento di
  `test_document_composer.py`; manifest unitario portato a 75 test script.

## V2.22 (continua, 04/08/2026) — Un solo significato operativo per turno

### Documenti completi come artefatti operativi verificati

- Clipboard, `read_document` e uploader già esistente della Silent Chat convergono
  ora nello stesso artefatto di sessione completo. Il blob operativo resta separato
  dall'estratto breve iniettato nella history, scade dopo 30 minuti e non diventa
  memoria Redis.
- Nuova capability contestuale `compose_document`: il controller semantico sceglie
  l'azione dal catalogo reale senza frasi o aziende hardcoded; la richiesta verbatim
  dell'utente, la sorgente integra e il contesto recente alimentano un piano JSON di
  heading, paragrafi, liste e tabelle.
- Renderer deterministici producono TXT, DOCX e PDF in
  `~/Scrivania/scambio_dati`, non sovrascrivono file esistenti e riaprono sempre
  l'output. Solo una ricevuta con percorso, byte, SHA-256 e validazione autorizza la
  conferma “creato”. PDF e Word sono quindi file strutturati reali, non testo finto
  narrato dal modello.
- Voce e Silent Chat falliscono chiuse su un `REQUEST_ACTION` non grounded: un veto,
  un timeout o una proposta vuota non possono più degradare a CHAT e generare frasi
  come “sto preparando/salvando” senza esecuzione. Regressioni pure in
  `test_document_composer.py` coprono sorgente completa, output TXT/DOCX/PDF,
  anti-overwrite, invalidazione della sorgente stale, richiesta verbatim, ricevuta
  e astensione fail-closed. Manifest unitario completo: **74/74 in 80,8 s**.

### Mappa canonica dell'architettura mnemonica

- Aggiunto `docs/EURI_MEMORY_ARCHITECTURE.md` come punto di ingresso stabile per
  l'intera memoria di Euri: turni verbatim, frame semantico, continuità,
  estrazione passiva, documento Redis canonico, RAG dual-channel, TTL,
  correzioni, scope, Obsidian e operatori Dream.
- La pagina contiene diagrammi Mermaid, tabelle di sorgenti e durate, stati di
  revisione, invarianti, percorso diagnostico e responsabilità per modulo.
  `README.md` e `CODEX.md` dichiarano esplicitamente che va consultata e
  aggiornata prima di ricostruire o cambiare la logica mnemonica.

### Azione mnemonica chiusa sul proprio esito

- Il collaudo Silent Chat ha mostrato un confine incoerente: il frame aveva
  compreso `SAVE_MEMORY` con `CORRECT_FACT,REQUEST_SAVE`, ma il router lessicale
  lasciava il turno in `CHAT`. Il modello dichiarava quindi di aver rinforzato
  la separazione Jeep/Gio Style senza aver ancora eseguito il save; soltanto il
  learner passivo lo fissava più tardi.
- `core/semantic_turn.py` espone ora un arbitraggio condiviso e conservativo:
  un intento semantico ad alta confidenza può sostituire il router del canale
  soltanto entro una lista di intenti sicuri. Voce e Silent Chat usano la stessa
  decisione; una correzione naturale seguita da “ricordalo” raggiunge
  `core/save_service.py` prima della risposta, senza regex dedicate a nomi,
  aziende o alla frase osservata.
- La conferma deriva dall'esito reale del save. Inoltre un match testuale
  identico ma `passive` o epistemicamente debole non blocca più la promozione:
  nasce una memoria permanente `source=user` e il nodo debole viene
  soft-superseded.
- Chiuso anche il feedback spurio Redis → Obsidian → Redis. Il watcher
  canonicalizza il Markdown, rimuove soltanto l'H1 generato atteso e confronta
  il corpo con Redis: una self-write cross-process o il secondo evento del
  filesystem diventano no-op, mentre una vera modifica manuale aggiorna
  contenuto/embedding ed emette `vault/extero/change` solo dopo il commit.
- Bonifica live idempotente: `scripts/repair_obsidian_echo_headers.py` ha
  eliminato l'H1 serializzato da 296 nodi senza cambiarne provenienza o TTL;
  `scripts/repair_20260804_silent_save.py` ha sostituito il passivo `05095a79`
  con il fatto esplicito `e87c2ff1`, preservando turn refs e quarantinando la
  vecchia nota. I dry-run successivi riportano zero interventi residui. Suite
  unitaria completa sul codice finale: **73/73 in 81,3 s**.

### Loop 2d budgetato e durevole

- Il giudice `KEEP/DROP` non può più monopolizzare la GPU durante una crescita
  dell'archivio: ogni maintenance dispone di un budget configurabile, 16
  chiamate o 60 secondi di default, e lavora per scadenza originaria.
- I candidati eccedenti sono accodati nel documento RedisJSON con
  `pruning_review_pending`, `review_after` e audit del rinvio. Ricevono una
  review lease di almeno 30 giorni, estesa dinamicamente per il backlog, e
  vengono ripresi anche fuori dalla finestra ordinaria di sette giorni. Stato
  di review, mirror `expires_at` e TTL sono committati in una transazione Redis.
- Nessuna pre-euristica elimina dati. Soltanto un `DROP` esatto del giudice può
  cancellare; errore o output ambiguo diventano `KEEP`. Il giudice riceve ora il
  documento con richiami, utilizzo shadow, sorgente e stato epistemico invece
  della falsa premessa “mai richiamata”. Anche un `KEEP` usa il floor di 30
  giorni, chiudendo il riesame giornaliero degli episodi.
- Un touch cognitivo non può accorciare la lease di un nodo accodato; il cleanup
  fallback ignora la coda. Aggiunto `test_loop2d_pruning.py` con regressioni su
  cap, ordine, restart logico, floor, touch e cancellazione fail-safe.
- `scripts/audit_memory.py --report` accetta ora anche gli `audit_flag` legacy
  rappresentati come liste o mappe, invece di interrompere il report con un
  cast a intero, ed espone il numero e l'ordine della coda. Manifest unitario
  completo sul codice finale: **71/71 in 75,3 s**.

### Presente continuo fra i processi

- Aggiunto `core/conversation_continuity.py`: un indice Redis TTL collega gli
  ultimi turni verbatim a una capsule temporanea per scope e ne deriva focus,
  entità attive e fili conversazionali aperti senza chiamate LLM o sintesi.
- Voice Daemon e Silent Chat reidratano il Brain dopo un riavvio. I turni
  ripristinati mantengono `turn_ref`, tempo, autenticazione e frame semantico,
  ma non entrano nel journal passivo, non vengono riarchiviati e non possono
  riaprire da soli la lease vocale.
- I fili distinguono `user_request` e `assistant_question`. Una risposta può
  segnare soltanto `assistant_replied`: non equivale a tool eseguito o risultato
  verificato. Il rilevamento di una domanda dell'assistente usa soltanto il
  segno finale `?`, non regex su nomi, aziende o frasi.
- La capsule scade dopo sei ore per default, è separata per scope ed esclude
  backfill e turni individualmente scaduti. Suite unitaria completa: **70/70**.
- Il collaudo live Gio Style ha mostrato che una chiusura `no_store` poteva
  rimpiazzare il focus sostanziale. Il workspace ora aggiorna il focus usando
  fatti, entità, azioni e richieste del frame, non liste lessicali o regex.
- Le domande pronunciate da Initiative e le relative risposte entrano nello
  storico/archivio come contesto non eleggibile al learner passivo. Lo stato
  operativo breve di reazione o verifica memoria è inoltre salvato con TTL e
  riarmato dopo un riavvio, senza trasformarsi in memoria a lungo termine.

### Comprensione condivisa fino alla memoria passiva

- Il collaudo successivo ha separato la policy globale dalla semantica dei
  singoli fatti. Il frame v3 assegna a ogni claim una modalità
  (`asserted/probable/planned/pending/counterfactual`) e una durata
  (`reusable/session_only`): una prova industriale, una decisione futura o
  un'ipotesi tecnica riutilizzabile non vengono più escluse solo perché il
  turno complessivo era stato classificato `ephemeral`. Una guardia coerente
  mantiene invece effimeri riavvii e test del runtime dell'assistente.
- Un'entità risolta ad alta confidenza viene ora proiettata nel testo operativo
  e nella query web dello stesso turno. La proiezione non apprende alias e non
  modifica il raw; soltanto `CORRECT_ENTITY` esplicito continua ad aggiornare il
  registro persistente. Il meccanismo è guidato dal frame, senza nomi o pattern
  dedicati ad aziende. Regressioni live Gio Style/MFI e manifest **70/70 in
  81,8 s**.
- Il collaudo automotive ha trovato il controesempio necessario: il modello
  aveva risolto l'anafora “loro” come Gio Style e la proiezione l'aveva
  trasformata in nome, contaminando una memoria passiva e una reflection. La
  mutazione implicita richiede ora anche compatibilità fra le superfici
  nominali; pronomi e descrizioni restano coreferenze contestuali e non vengono
  riscritti. Se il modello prova a riscriverli direttamente, il validator
  ripristina il baseline. La guardia è generale e non contiene aziende o regex
  lessicali dedicate.
- Riparazione append-only con
  `scripts/repair_20260804_jeep_coreference.py`: il falso fatto passivo
  `c2f200f1` è superseded dal fatto owner-grounded `c8a684ea`; la reflection
  contaminata `d0933d42` è ritirata e le due note originali sono in quarantena
  recuperabile. Turno raw e due memorie corrette del pass restano invariati.
  Regressione anaforica mirata e manifest unitario **70/70 in 84,4 s**.
- L'estrattore e l'auditor di provenienza ricevono ora, accanto al verbatim, la
  vista accettata del frame semantico. Il frame disambigua identità canoniche e
  modalità (`possibile`, `in prova`, `in attesa`, `confermato`), mentre il raw
  continua a dover sostenere ogni proposizione. Una prova svolta non può più
  essere promossa a “esito ottenuto”.
- Separata la wake word dalla provenienza: un follow-up vocale autenticato e
  accettato nella conversazione è marcato `accepted_owner_turn`. I fatti forti
  che provengono da questi turni diventano `owner_asserted`; il parlato
  ambientale o semanticamente debole resta `tacit_acceptance` da verificare.
  Nessun nome di azienda e nessuna correzione lessicale sono cablati.
- Il resolver temporale sceglie l'espressione più precisa dell'evento: in una
  fonte contenente sia “poco fa” sia “3 agosto”, la data esplicita prevale sul
  marcatore metaconversazionale recente.
- Riparazione append-only live con
  `scripts/repair_20260804_gio_style_passive.py`: quattro memorie Gio Style
  sostituite con identità, modalità e tempo corretti; vecchi nodi
  soft-superseded e Markdown in quarantena recuperabile. Verifiche mirate verdi
  e manifest unitario **70/70 in 74,5 s**.

- Introdotto `core/semantic_turn.py`: dopo Whisper, voce, Mobile e Silent Chat
  costruiscono un unico frame strutturato con testo raw, interpretazione quasi
  verbatim, intent, atti linguistici, entità, fatti, azioni, query web,
  addressedness e destinazione mnemonica. I consumer riusano il frame invece di
  reinterpretare indipendentemente lo stesso turno.
- Il raw STT resta la prova immutabile in `euri:turn:*`; testo canonico e frame
  sono campi additivi. Una correzione esplicita d'identità può aggiornare il
  registro alias scoped e il contesto ancora in-flight, ma non riscrive il
  verbatim. Nessun nome di persona o azienda è cablato nel codice.
- Collaudo live Gio Style: Whisper ha prodotto `Joe Style`; una correzione del
  proprietario ha creato l'identità canonica e una richiesta successiva ha
  cercato autonomamente `Gio Style azienda`, senza una seconda interpretazione
  LLM e senza avviare l'ActionController.
- Il frame distingue `candidate`, `ephemeral` e `no_store`. Il learner passivo
  archivia comunque i turni raw, ma non estrae memoria dal messaggio né dalla
  relativa risposta quando un frame affidabile li classifica effimeri/no-store.
  Il caso live “riavvio dopo piccola modifica software” è ora `ephemeral`; fatti
  cliente e decisioni tecniche stabili restano candidati.
- Il primo turno owner può aprire la sessione senza wake word soltanto con voce
  autenticata, volto owner presente e `direct_address` semantico ≥0,92. Il
  frame pre-gate è puro e viene riusato dal dispatch dopo l'accettazione;
  ospiti, identità dubbie e parlato ambientale restano fail-closed.
- Separata strutturalmente una risposta da un'esecuzione: il richiamo di ciò che
  Euri sa usa `SEARCH + ASK + REQUEST_MEMORY_SEARCH`; `REQUEST_ACTION` può
  autorizzare il fallback contestuale solo se `actions` rappresenta un effetto,
  target o capability concreti. La frase live prima classificata
  `ACTION_REASONING` senza azioni ora diventa `SEARCH`; una vera lettura GPU
  resta `EXECUTE` con effetto grounded.
- Aggiunte regressioni su alias espliciti, raw/canonico, query web condivisa,
  policy passiva, bootstrap owner, isolamento guest/ambientale e veto delle
  azioni non grounded. Verifiche finali: semantic turn, wake guard,
  addressedness e ActionController verdi; manifest unitario **69/69** e
  `git diff --check` pulito.
- Limiti osservati, non mascherati: una verifica speaker a `0,645` contro soglia
  `0,65` ha perso un follow-up legittimo; addressedness e policy mnemonica
  restano in rollout osservazionale e richiederanno micro-aggiustamenti su casi
  organici, non regex lessicali ad hoc.

## V2.22 (30/07/2026) — Memoria osservabile e confinata

V2.22 promuove a nuova base `main` la linea sviluppata e verificata sul branch
held-out. Il contenuto era fast-forwardabile; durante il push è emerso il merge
GitHub concorrente del vecchio ramo `agent/synchronize-euri-runtime`, già
interamente antenato di V2.22. È stato riconciliato con un merge amministrativo
senza differenze nel tree: nessun force-push, squash o riscrittura della storia
e nessuna migrazione Redis.

La release consolida scope personale/sperimentale, fonte verbatim datata,
richiamo cronologico fondato, utilità osservata, lifecycle audit-only,
telemetria cognitiva e benchmark preregistrati. Conserva anche i risultati
negativi: structured Loop 2f resta inerte dopo `NO_GO_DEV`, mentre il legacy
rimane autorità runtime e Loop 2h resta da validare sulle vere opportunità.

La fotografia è in
`docs/EURI_V2.22_STATE_2026-07-30.md`. Da questa versione il backlog
autoritativo non è più disperso fra handoff e report:
`docs/EURI_OPEN_WORK.md` registra priorità, evidenza, trigger, prossima azione e
criterio di chiusura. Stato read-only al rilascio: grounded 668/800, utility
102 risposte ma review non matura, verbatim 2.171 turni con zero orfani e zero
riferimenti mancanti; manifest unitario 68/68.

Un addendum post-hoc corregge il report structured v2: la supersessione usa
cinque requisiti logici, non sette, e il collo di bottiglia osservato è
`mutually_exclusive` (13/20). Due percorsi v3 restano ipotesi, non feature:
correzione esplicita oppure aggiornamento implicito con tutte le guardie forti.
Il challenge è aperto e COG-02 resta bloccato dalla validazione di Loop 2h.

### V2.22 (continua, 30/07/2026) — Loop 2h fondato sulla prova

- Sostituita la label semantica nuda di Loop 2h con il contratto
  `loop2h-evidenced-identity-v1`: il modello espone il claim subject di
  entrambe le memorie, base del giudizio, specificità, tipo ed estratti
  letterali. Una policy pura autorizza la mutazione soltanto con base esplicita
  e citazioni verificabili; inferenza, genericità, JSON incompleto, tipo ignoto
  o prova assente degradano a `UNKNOWN`.
- Il claim subject è l'entità della quale si aggiorna uno stato, non il nuovo
  valore. Questa distinzione impedisce di scambiare Elisa e Paola per due
  progetti diversi quando cambia il responsabile del progetto Nadir, o la
  scheda per la formulazione F-88.
- La prova viene conservata nella lineage della reflection o nell'audit
  `supersession_reversed`. Gli `UNKNOWN` restano ritentabili ma usano un backoff
  persistente per arco (1→2→4→8→16→30 giorni), evitando che dieci casi
  irrisolti blocchino la coda manutentiva.
- Sul banco development già aperto, una replica diagnostica produce 0/12 vere
  supersessioni riaperte, riconosce 12/12 soggetti distinti e lascia 6/6
  ambigui senza mutazione. Non è validazione: COG-01 resta aperto su un
  challenge opportunity-first nuovo e COG-02 resta bloccato.
- Regressioni pure Loop 2h: 14/14; manifest unitario finale: 68/68.

## V2.21 (28/07/2026) — Memoria fondata sulla fonte

V2.21 fissa il passaggio dalla memoria passiva come contenuto concorrente alla
memoria passiva come locator verso l'esperienza originale. È la prima release
di Euri la cui policy mnemonica nasce da un benchmark A/B preregistrato e da
una diagnosi meccanicistica dello sfratto dell'evidenza, non soltanto
dall'osservazione qualitativa.

La base RAG grezza è ora protetta; le sintesi passive non entrano nel prompt,
ma possono idratare turni verbatim conservati in `euri:turn:*`. Il census
indipendente ha prodotto `+0,0311` evidence recall, 22 recuperi e zero perdite,
con un guadagno F1 piccolo (`+0,0023`) dichiarato come tale. Il runtime aggiunge
un gate selettivo, mantenendo l'append come fallback, e voce, mobile e Silent
Chat condividono lo stesso dispatcher.

La release comprende anche il confine epistemico di luglio: convergenza interna
diversa da validazione esterna, ipotesi fuori dal RAG fattuale, identità diversa
da analogia e narrativa interna incapace di diventare prova di se stessa.

Stato, numeri, invarianti e limiti sono congelati nella fotografia
[docs/EURI_V2.21_STATE_2026-07-28.md](docs/EURI_V2.21_STATE_2026-07-28.md).
La dichiarazione di versione non migra Redis e non riscrive memorie.

### V2.21 (continua, 29/07/2026) — Operatori cognitivi resi falsificabili

- Censiti dal codice gli operatori 2a–2i, propagazione, gate archiviato e
  cleanup, separando comportamento verificato, intenzione, misura e ipotesi.
  La forma unica non regge: emergono aggiornatori di evidenza, generatori e
  mietitori. Soltanto 2f/2h/propagazione sono oggi chiaramente TMS-like; le
  cancellazioni esplicite e il TTL Redis non lasciano abbastanza stato per un
  controfattuale affidabile.
- L'audit ha trovato due difetti strutturali senza modificarli: il ciclo
  forzato usa ordine e molteplicità diversi dal runtime; il cleanup post-2a
  chiama `get_expiring_memories(days_ahead=0)` e costruisce una finestra
  impossibile, quindi è un no-op. Documentato anche il buco di provenienza
  della lezione `bad_reasoning` di 2g.
- Prima mappa verificata del prior art: RMM, MemSifter, SF-AMS e MemLineage
  impediscono di rivendicare genericamente utilità/outcome/lineage come novità.
  La tesi falsificabile viene ristretta all'anello
  output-osservato→lifecycle e alla conservazione degli errori come storia
  epistemica.
- Preregistrata la prima verifica appaiata: 42 coppie italiane controllate,
  36 primarie, 6 ambigue, 9 sentinelle replicate, 120 chiamate previste.
  Confronta 2f con 2f+2h sugli stessi input, usa direttamente i classificatori
  di produzione, controbilancia l'ordine, non apre Redis e non passa i gold ai
  prompt. Il run reale è bloccato finché protocollo, fixture e harness non sono
  committati.
- Baseline storica read-only, non usata come gold: 603 coppie 2f controllate,
  252 supersessioni attive, 230 narrate, 98 confronti, 83 reflection 2h,
  2 inversioni annotate e 21 archi con vincitore ormai assente.
- Regressioni pure e resume/integrità del nuovo harness: manifest unitario
  **66/66**. Nessuna modifica ai loop di produzione e nessuna scrittura Redis.
- Il primo avvio del protocollo si è fermato prima di qualsiasi analisi su una
  risposta 2f fuori contratto (`CONTRADIZIONE`, una sola `D`), che il parser
  reale degrada a `none`. L'harness ora conserva esattamente quella decisione
  e registra separatamente la violazione del contratto; fixture, prompt, gold,
  ordine e soglie restano congelati. Il checkpoint parziale non viene riusato.
- Run controllato completato: 60 osservazioni e 120 chiamate, senza Redis.
  Loop 2f ottiene `GO_DEV` (34/36, una falsa supersessione), ma viola il
  contratto d'uscita in 7/60 chiamate e il refuso salva accidentalmente tre
  coppie target-risultato. Loop 2h resta
  `INCONCLUSIVE_DEV_NO_OPPORTUNITY`: 2f non crea errori cross-entità nel
  campione, quindi 2h non cambia alcuna azione; inoltre sceglie `UNKNOWN` in
  0/6 casi volutamente ambigui. Risultati e limiti sono documentati in
  `docs/EURI_LOOP2F_LOOP2H_RESULTS_2026-07-29.md`.
- Prima di cambiare 2f è stato congelato il protocollo structured v2:
  supersessione soltanto con prove affermative di stessa entità, stesso claim,
  stesso tipo, esclusione reciproca e sostituzione esplicita; ogni incertezza
  conserva entrambe le memorie. Il challenge nuovo contiene 30 casi italiani
  e cinque sentinelle replicate; i 42 casi precedenti restano sola regressione
  aperta. Loop 2h è esplicitamente fuori da questo intervento.
- Implementata la policy pura `loop2f-structured-affirmative-v2` e collegata al
  runtime soltanto per la validazione preregistrata: il modello produce sette
  campi JSON, mentre una funzione
  deterministica autorizza `contradiction` soltanto se tutti i requisiti sono
  affermativi. JSON malformato, tipi diversi e identità/claim incerti
  degradano a `none`; il vecchio classificatore resta il braccio di controllo.
- La validazione appaiata (66 casi indipendenti, 152 chiamate, zero Redis)
  termina `NO_GO_DEV`: structured porta il contratto da 62/76 a 76/76 e
  azzera le false supersessioni, ma dimezza il recall degli aggiornamenti veri
  sia sulla regressione sia sul challenge (`50%`). La policy structured resta
  disponibile come diagnostica e risultato negativo; il classificatore legacy
  viene ripristinato come autorità runtime. Risultati in
  `docs/EURI_LOOP2F_STRUCTURED_V2_RESULTS_2026-07-30.md`.

### V2.21 (continua, 29/07/2026) — Memoria personale e scenari non condividono più lo stesso mondo

- Chiuso un difetto strutturale: una battuta concreta, un role-play o una
  conversazione di prova potevano superare l'estrattore passivo e, una volta
  salvati, partecipare a retrieval, deduplica, correzioni, consolidamenti e
  sogni come qualunque fatto personale.
- Ogni memoria, nota e turno verbatim porta ora un `memory_scope` durevole.
  Il default e lo storico sono `personal`; una sessione dichiarata crea uno
  scope `experiment_<nome>` separato. Gli indici RediSearch filtrano lo scope
  prima del ranking e l'idratazione JSON lo rivalida, quindi un indice stale
  non può riaprire il confine.
- Hardening dopo audit indipendente: soltanto `None`/vuoto conserva il fallback
  legacy a `personal`. Uno scope presente ma non canonico viene quarantinato
  come `invalid_scope`, mai promosso nel mondo personale; l'escaping dei valori
  TAG RediSearch è ora effettivo e coperto da contro-test con spazi, trattini,
  pipe e parentesi graffe.
- Il dual-channel può usare una sintesi sperimentale soltanto come locator di
  un turno verbatim dello stesso scope. History LLM, episodi compressi, log
  giornaliero, ultimo contesto RAG, note, deduplica e correzioni sono separati.
  Anche il worker mobile riallinea il proprio ContextVar allo scope Redis
  attivo prima di rispondere.
- I loop cognitivi canonici restano personali: Loop 2e, Loop 2f, Loop 2h,
  plausibility gate, sogni creativi, ipotesi trasversali e Initiative rifiutano
  input sperimentali. Il Pulse conserva comunque `memory_scope` per audit; le
  memorie di prova vengono esportate in
  `EuriVault/Experiments/<scope>/...`, non mischiate al Vault personale.
- Comandi voce/Silent Chat: `avvia una sessione sperimentale chiamata <nome>`,
  `in che modalità di memoria siamo?`, `chiudi la sessione sperimentale`.
  Lo scope attivo scade automaticamente dopo 24 ore come fail-safe; i dati non
  vengono cancellati e restano disponibili come storico isolato.
- L'estrattore personale riceve inoltre l'istruzione di non trasformare
  battute, esempi, ipotesi, simulazioni o dati esplicitamente inventati in
  fatti. Questa è una protezione semantica aggiuntiva, non una promessa
  impossibile: una falsità formulata come fatto reale e senza alcun marcatore
  pragmatico non è distinguibile con certezza da un fatto vero.
- Migrazione idempotente al boot: documenti legacy `euri:memory:*`,
  `euri:turn:*` ed `euri:note:*` privi del campo vengono marcati `personal`;
  contenuto e provenienza non vengono riscritti. Regressioni dedicate e
  manifest unitario: **65/65**.

#### Cronologia verbatim: Euri può cercare quando ne avete parlato

- Le domande «quando te ne ho parlato per la prima/ultima volta?» non vengono
  più affidate al normale top-k semantico delle memorie. Un classificatore
  locale JSON distingue la cronologia della conversazione dalle date di eventi
  e scadenze, estraendo soltanto il soggetto da cercare.
- Il nuovo indice `idx:turns` cerca esclusivamente turni originali dell'utente
  nello scope corrente e li ordina per `observed_at`. Il contesto dichiara
  esplicitamente che questa è la **data del turno**, non la data dell'evento né
  quella di creazione di una sintesi. Se non esiste evidenza, il percorso
  fallisce chiuso e vieta di ricavare una data da memorie vicine.
- Migrazione storica una tantum e idempotente: dai log vocali vengono importati
  soltanto gli STT che hanno raggiunto un `Intent`; parlato ambientale e turni
  ignorati dal wake guard restano esclusi. Sulla workstation sono stati
  recuperati 2.055 turni anteriori all'archivio durevole. Il benchmark è
  esplicitamente escluso dal backfill, quindi i runtime effimeri non vedono i
  log personali.
- Controprova reale: la richiesta su Leonardo seleziona
  `chronological_first`, cerca `Leonardo + collega` e restituisce la prima
  occorrenza disponibile del 29/04/2026 alle 17:42 con la frase originale,
  invece di associare una data presa da un'altra memoria.

#### Correttezza dei risultati e completezza dei candidati: due problemi distinti

Una revisione incrociata ha mostrato che lo scope garantiva finora la
correttezza di ciò che sopravvive al filtro, ma non la completezza del bacino
da cui i candidati vengono estratti. Sono due difetti diversi e vanno tenuti
separati, perché il primo è chiuso e il secondo no.

- **Difetto 1 — chiuso.** `gather_grounded_evidence` (gate di familiarità del
  briefing) recuperava fino a 800 documenti filtrando solo per `source`, e
  applicava lo scope dopo, in idratazione. Nessun leak: una memoria
  sperimentale non veniva mai usata. Ma poteva occupare posti nella finestra e
  spingerne fuori evidenza personale — e il costo restava anche a sessione
  chiusa, perché quei documenti restano nell'indice. La clausola di scope è ora
  nella query, prima del limite; il filtro in idratazione resta come seconda
  validazione fail-closed contro un indice stale. Un test sull'output non
  avrebbe visto nulla, quindi la regressione verifica la query inviata a
  RediSearch e il caso di crowding con la finestra satura.
- **Guardrail.** `test_memory_scope_query_guard.py` inventaria staticamente le
  ricerche runtime su `idx:memories`/`idx:notes` e pretende una clausola di
  scope, con allowlist esplicita e motivata (oggi: la sola ispezione
  cross-scope della Control Room, vincolata anche nel numero). Non è la
  primitiva per costruzione — quella sarebbe un'interfaccia di ricerca che non
  espone l'indice grezzo, e verrà progettata dopo la tabella offline degli
  operatori. Rende soltanto verificabile una convenzione che finora dipendeva
  dalla memoria di chi scriveva il codice.
- **Difetto 2 — APERTO, non risolto da questo intervento.** Il bacino personale
  è oggi a **667/800** documenti `user|teach|passive|episode|conversation`, e
  cresce di 3–5 al giorno con il solo uso normale: la saturazione arriva anche
  senza mai aprire una sessione sperimentale. La query non ha ordinamento, né
  per rilevanza né per data, quindi oltre la soglia il troncamento è arbitrario
  e il sintomo a voce sarebbe Euri che dichiara *«di questo non mi pare di
  avere traccia»* su un tema di cui ha traccia: un falso "non ricordo"
  indistinguibile da un vero "non so". **Il limite non va alzato**: alzarlo
  sposta il muro e nasconde che la selezione avviene nel posto sbagliato. La
  direzione da misurare è calcolare la document frequency nell'indice invece
  che in Python su una finestra arbitraria, in shadow contro il calcolo legacy
  perché stemming e stopword divergeranno. Sentinella operativa: **750**, che è
  un innesco per riaprire, non una soluzione.

### V2.21 (continua, 29/07/2026) — Il richiamo diventa utilità osservata

- Riscoperta e chiusa una funzione rimasta senza consumer:
  `response_lineage_shadow_v1` distingueva già `recalled` da
  `used_in_response`, con attribuzione lessicale conservativa e senza
  conservare testo di domanda o risposta. Audit reale: 63 turni completi,
  614 nodi richiamati, 77 usi sostenuti ma non provati (`12,54%`).
- Il boot del Dream Engine, e poi la manutenzione giornaliera, aggregano ora la
  timeline in uno stato durevole e materializzano sui documenti
  `supported_use_count`,
  `last_supported_use_at` e metodo del segnale. Il primo passaggio recupera
  anche gli eventi storici già presenti.
- L'uso ha un effetto immediato ma confinato: riordina soltanto i candidati
  Loop 2e già eleggibili, con peso equivalente a due richiami e cap 5. Non
  ammette memorie sotto soglia, non promuove insight, non cambia verità, TTL o
  contenuto.
- La revisione non resta affidata alla memoria umana: dopo 14 giorni e almeno
  100 risposte, o comunque dopo 30 giorni, nasce un `review_pending` durevole,
  ripetuto al boot e nello status. Il reminder non applica auto-tuning.
- Aggiunto `explain_insight_promotion.py`: ricompone in lettura stato,
  convergenza, fedeltà delle premesse, qualità del ponte, giudizi e fonti per
  spiegare perché un insight è promosso, ipotesi, bloccato o sotto soglia.
- Verifica completa: manifest unitario **59/59**. Il passaggio di data ha
  inoltre scoperto un replay legacy dipendente dal clock corrente; il relativo
  harness congela ora il tempo al `created_at` del census e torna a
  ricostruire i contesti byte-per-byte anche nei giorni successivi.

### V2.21 (continua, 29/07/2026) — “Recente” diventa un vincolo, non un'impressione

- Una prova reale ha isolato un errore strutturale: alla richiesta
  “raccontami qualcosa che abbiamo fatto di recente”, il retrieval semantico
  poteva riportare un episodio del 21 giugno e il modello chiamarlo recente il
  29 luglio. La memoria era datata correttamente; mancava il vincolo nella
  query.
- Le espressioni italiane di recenza durevole (`di recente`, `recentemente`,
  `ultimamente`, `negli ultimi giorni/settimane`) attivano ora una finestra
  locale esplicita, configurabile con
  `EURI_RAG_RECENT_MEMORY_WINDOW_DAYS` (default 14 giorni), nel dispatcher
  condiviso da voce, mobile e Silent Chat.
- La finestra è fail-closed: il tempo effettivo dell'evento prevale sulla data
  in cui una sintesi è stata creata; la ricerca semantica non può reintrodurre
  ricordi più vecchi e, se non esistono risultati recenti, Euri deve dirlo e
  proporre di allargare la finestra.
- Il comportamento è osservabile nei log e in `RagContext.diagnostics`:
  candidati temporali, eventi eleggibili, nodi visibili e
  `fallback=none`. Le domande tematiche senza richiesta di recenza conservano
  il percorso semantico precedente.
- Il replay del benchmark precedente congela esplicitamente la vecchia policy,
  così l'hardening live non riscrive retroattivamente gli esperimenti già
  registrati. Verifica completa: manifest unitario **60/60**.

### V2.21 (continua, 29/07/2026) — Namespace dello stato utility separato

- Corretto l'avviso innocuo al boot `Existing key has wrong Redis type`: le
  stringhe di stato `utility_shadow` vivevano sotto `euri:memory:*` e il
  backfill embedding le scambiava per documenti memoria RedisJSON.
- Stato, rapporto e reminder usano ora `euri:utility:memory_shadow:*`. Al primo
  ciclo i valori legacy vengono copiati conservativamente; le vecchie chiavi
  restano intatte come rollback/audit e nessuna osservazione raccolta viene
  persa.
- Il backfill usa `SCAN` e controlla il tipo Redis prima di `JSON.GET`: eventuali
  future chiavi di servizio finite nel namespace memoria vengono ignorate
  senza produrre errori e senza interrompere l'aggiornamento dei documenti
  validi.

### V2.21 (continua, 29/07/2026) — Presenza visiva disponibile anche alla voce

- Chiuso un disallineamento osservato dal vivo: VisualGate riconosceva Stefano
  e pubblicava già gli eventi di presenza sul pulse, ma il prompt della
  conversazione vocale non riceveva lo snapshot operativo e poteva quindi
  negare di possedere la percezione che il daemon stava usando.
- Voce e Silent Chat condividono ora lo stesso contesto visivo sanitizzato e
  a TTL breve. Lo stato è sola lettura ed effimero: non diventa memoria, non
  conserva frame, embedding o similarity e non autorizza azioni sensibili.
- La percezione distingue volto assente, volto non identificato e proprietario
  riconosciuto. In mancanza di uno snapshot fresco Euri dichiara che lo stato
  corrente non è disponibile, senza negare l'esistenza del sensore.
- Dopo la verifica live della presenza, attivata anche la Fase 2a della
  percezione sociale: sorriso, contrazione delle sopracciglia e sguardo verso
  il basso entrano nei prompt locali soltanto come movimenti descrittivi
  stabilizzati, dopo rivalidazione dell'owner e con TTL breve.
- Il modello può adattare leggermente tono o brevità quando parole e segnale
  concordano, ma non può dedurre emozioni, intenzioni, sincerità o approvazione.
  I segnali restano fuori da memoria, Cognitive Present, Initiative e
  autorizzazioni. Kill switch:
  `EURI_SOCIAL_PERCEPTION_CONTEXT_ENABLED=0`.
- La prova live ha mostrato anche il limite opposto: un seguito pertinente
  pronunciato 95 secondi dopo la risposta veniva ignorato perché la lease senza
  wake word durava 45 secondi. La finestra non è stata semplicemente allungata:
  avrebbe riaperto il caso reale del parlato rivolto a Giada.
- Entro il focus già esistente di cinque minuti, un turno fuori lease può ora
  passare senza “Euri” soltanto se voce e volto owner sono verificati e un gate
  semantico ad alta confidenza lo riconosce come continuazione diretta o risposta
  all'assistente. Somiglianza tematica, output ambiguo ed errori restano
  fail-closed. Gli ospiti devono sempre usare il wake word.

### V2.21 (continua, 28/07/2026) — Thinking selettivo fondato sul verbatim

- La prompt-ablation controllata ha separato l'effetto del budget dall'effetto
  del thinking: con prompt strict e `num_predict=2000`, il thinking ha portato
  il token F1 da `0,1592` a `0,2171`, mantenendo invariata a `0,9535`
  l'accuratezza avversariale. Il prompt balanced, invece, ha eroso la prudenza:
  non è stato promosso nel runtime.
- Un secondo A/B preregistrato ha confrontato, con thinking attivo in entrambi
  i bracci, RAG puro e memoria dual-channel sugli stessi 129 casi. Il dual ha
  chiuso con verdetto `GO`: F1 `0,1604 → 0,2123` (`+0,0519`), delta
  `+0,0942` sull'evidence-flip, false astensioni `0,5814 → 0,5000`,
  avversariali invariati a `0,9535`, 16 casi migliorati e 6 peggiorati.
  Quattro conversazioni su cinque sono non negative; il bootstrap
  clusterizzato è `[0,0060; 0,1101]`. Resta sviluppo su LoCoMo aperto, non
  validazione indipendente.
- Il runtime usa ora il thinking di Gemma in modo selettivo: si attiva soltanto
  quando `dual-channel-selective-prepend-v0` promuove almeno un turno originale
  pertinente. La semplice presenza di una nota passiva o di un'aggiunta in
  coda non basta. La decisione usa la diagnostica strutturata del gate, senza
  regex sul testo e senza stato globale condiviso.
- Voce, mobile e Silent Chat applicano la stessa policy per-turno. I turni
  ordinari restano `think=False`; quelli promossi usano un budget di 2.000
  token. Errore o risposta vuota nel percorso thinking provocano un retry
  diretto `think=False`, senza perdere la conversazione. I log dichiarano
  attivazione, motivo, turni promossi, tempo e modalità realmente usata.

### V2.21 (continua, 28/07/2026) — Il verbatim diventa fonte storica datata

- Chiuso in anticipo il rischio di trattare una frase originale vecchia come
  stato corrente. Il documento Redis conservava già `observed_at` e `trusted`,
  ma il renderer mostrava al modello soltanto `speaker: contenuto`. Ogni fonte
  idratata usa ora il formato versionato
  `absolute-time-auth-channel-v1`: data e ora assolute italiane, canale
  autenticato/non autenticato, parlante e testo originale immutato.
- Aggiunto `audit_verbatim_lifecycle`: mark-and-sweep conservativo che costruisce
  in lettura la relazione inversa turno→memorie, distingue fonti referenziate,
  non referenziate recenti, candidati orfani oltre 180 giorni e riferimenti
  spezzati. Il lifecycle è **audit-only**: non applica TTL e non cancella nulla.
- Primo audit reale: 10 turni, 1 referenziato, 9 recenti non referenziati,
  0 candidati orfani, 0 riferimenti mancanti, 0 documenti malformati. La soglia
  `EURI_VERBATIM_UNREFERENCED_GRACE_DAYS` è valutata automaticamente nella
  manutenzione giornaliera ma non è collegata ad alcuna azione distruttiva.
- L'audit automatico persiste l'ultimo rapporto e un eventuale
  `review_pending`. Un problema resta visibile a ogni boot e nello status
  memorie finché il controllo non torna pulito; il Pulse viene emesso soltanto
  quando cambia l'insieme dei problemi. È quindi Euri a ricordare la scadenza,
  non l'utente, senza introdurre cancellazioni silenziose.
- Lo strumento read-only è disponibile con
  `./venv/bin/python scripts/audit_verbatim_lifecycle.py [--json]`. Qualunque
  pruning futuro richiederà una policy separata, misurata e reversibile.

## 2026-07-28 — Gate selettivo: il turno originale guadagna il primo piano

- Corretto un drift scoperto nella prova reale su `Compound UBQ 2026`: la voce
  usava il dual-channel selettivo, mentre la Silent Chat chiamava ancora il
  builder RAG legacy. Un nuovo dispatcher runtime unico applica ora la stessa
  modalità `off|shadow|on|selective` a voce, mobile e chat testuale, incluso il
  fallback fail-closed alla base senza memorie passive.
- La Silent Chat collega ora il `Brain` allo stesso archivio durevole
  `euri:turn:*` e usa la history interna provvista di `turn_ref`. Le nuove
  memorie passive estratte dalla chat possono quindi localizzare e idratare i
  turni originali; non vengono più generate senza riferimenti sorgente solo
  perché la conversazione è avvenuta da tastiera.
- L'ablation generativa preregistrata sul census LoCoMo già aperto ha isolato
  l'effetto della posizione: sui 43 casi domanda×replica in cui il canale
  passivo recuperava evidenza assente dal RAG, il prepend semplice ha portato il
  token F1 da `0,111` a `0,238`. Applicato indiscriminatamente alle 1.468
  istanze con aggiunte, però, ha chiuso a `-0,0018` F1 globale e
  `-0,0119` di prudenza rispetto all'append. Il contratto evidence-first
  esplicito è risultato nettamente peggiore (`-0,0111` F1 globale). Entrambe le
  varianti hanno quindi rispettato il verdetto congelato `NO_GO_DEV`.
- Aggiunta la modalità sperimentale live
  `EURI_RAG_DUAL_CHANNEL_MODE=selective`: la base RAG resta protetta e l'append
  validato resta il fallback; un turno verbatim idratato viene portato davanti
  soltanto se supera congiuntamente rilevanza domanda→fonte, margine rispetto
  alla base e anti-ridondanza semantica. La parafrasi passiva continua a essere
  soltanto un locator e non entra mai nel prompt.
- Il gate è osservabile e fail-closed. Registra rango/distanza del locator,
  similarità domanda→turno originale, miglior similarità della base, margine,
  ridondanza fonte→base, decisione e motivi. Embedder o segnali mancanti
  mantengono l'append; nessuna memoria esistente viene riscritta.
- La lineage delle risposte conserva regione del prompt e segnali del gate per
  distinguere in seguito `recalled` da `used_in_response`. L'embedder supporta
  inoltre il batch locale per valutare base e fonti senza chiamate cloud o LLM.
- Il launcher personale `start_euri.sh` abilita `selective` per la prova sulle
  memorie reali; l'override `on|shadow|off` resta immediato e reversibile.
  LoCoMo è ormai development set interamente aperto: le soglie sono provvisorie
  e la loro utilità generale dovrà essere validata su un benchmark diverso.

## 2026-07-27 — Memoria passiva dual-channel: dalla sintesi al locator

- Il census held-out italiano preregistrato (5 conversazioni LoCoMo mai usate,
  2 repliche, 989 domande) ha dato verdetto `GO` alla policy
  `dual-channel-q2r1-v1`: evidence recall `+0,0311`, 22 vittorie e 0 perdite
  appaiate, token F1 `+0,0023`, accuratezza avversariale `+0,0080`,
  `gold_lost=0`. Il guadagno F1 resta piccolo e con intervallo clusterizzato
  compatibile con zero; la prova forte è strutturale, non una rivendicazione di
  accuratezza generale.
- Le memorie passive non competono più come testo nel candidato dual-channel:
  la base RAG esclude `source=passive` ed è integralmente protetta; le prime due
  note passive possono soltanto localizzare un turno sorgente ciascuna, con
  massimo due aggiunte e budget di 2.500 caratteri applicato soltanto alle
  aggiunte.
- Aggiunto un archivio Redis separato `euri:turn:*` per i turni verbatim. Ogni
  messaggio riceve un riferimento stabile `conversation_id:seq`; il passive
  learner verifica la persistenza dei turni prima di salvare una nota e non fa
  ack del journal se l'archivio non è scrivibile.
- `temporal_context.source_turn_refs` accompagna i vecchi
  `source_turn_ids`. Le note storiche rimangono intatte e auditabili; se il loro
  turno originale non esiste, il canale duale non usa la parafrasi come prova.
- Il compositore congelato vive ora in `core/dual_channel.py` ed è re-esportato
  dal benchmark: runtime e protocollo misurato non possono divergere
  silenziosamente. Il filtro `source_exclude` viene applicato prima del KNN
  domain-boosted, non dopo il taglio degli slot.
- Rollout reversibile: `EURI_RAG_DUAL_CHANNEL_MODE=off|shadow|on`, default
  `off`. L'archivio durevole viene popolato anche a policy spenta; `shadow`
  confronta composizione e dimensioni senza cambiare la risposta.
- Aggiunte regressioni pure per riferimenti stabili, persistenza verbatim,
  base protetta, assenza del testo sintetico nel prompt, idratazione della
  fonte e comportamento fail-closed sulle memorie storiche non idratabili.

## 2026-07-26 — Identità, analogia e promozione epistemica

- La convergenza di tre sogni non promuove più automaticamente un insight.
  La promozione è fail-closed: richiede premesse completamente fedeli e un
  ponte `SUPPORTED`; valutazioni mancanti, infedeli o `FORCED` restano candidate.
- Un ponte `HYPOTHESIS` entra nel nuovo stato intermedio `hypothesis`: viene
  dichiarato sul Pulse e conservato per audit, ma resta fuori dal RAG e dagli
  insight promossi in Obsidian. Anche le ipotesi trasversali del Loop 2i seguono
  questa separazione.
- `source_memory_ids` conserva soltanto le premesse dirette del testo. Le fonti
  degli insight simili assorbiti sono registrate separatamente in
  `convergence_source_memory_ids`, evitando provenienze cumulative inquinate.
- Il Loop 2h usa un giudizio semantico locale `SAME/RELATED/DIFFERENT/UNKNOWN`,
  senza regex o liste di progetti: solo `SAME` può diventare autobiografia di
  evoluzione.
- Se due memorie erroneamente collegate da `superseded_by` risultano entità
  diverse, l'arco viene invertito in modo tracciato e reversibile. `RELATED`
  genera inoltre un Pulse che esplicita somiglianze e differenze, invece di
  fondere materiali o progetti distinti.
- Primo ciclo live post-fix: una vecchia self-observation contaminata aveva
  alimentato una nuova reflection Altosele/UBQ. Le reflection prodotte dal Loop
  2h non possono più essere estremi di un'altra self-observation; anche gli
  status `rejected_cross_entity_evolution` e `cross_entity_conflation` vengono
  esclusi prima del giudizio LLM. La narrativa non può diventare prova di se
  stessa.
- Le note del Loop 2f distinguono ora target, misura, stato temporale, entità
  diversa e vera alternativa. Il prompt vieta raccomandazioni se le fonti non
  dichiarano esplicitamente alternative e criterio di scelta; un dedup
  semantico conserva una sola nota equivalente.
- Bonifica del campione live: due promozioni con ponte `HYPOTHESIS` sono state
  riclassificate come ipotesi, una promozione priva di misura è tornata
  candidate; la reflection che costruiva una traiettoria `Altosele 720 → UBQ
  1250 MPa` è stata soft-esclusa e la memoria Altosele originale ripristinata.
  Copie Redis recuperabili vengono conservate per 30 giorni.
- Bonifica del primo ciclo post-fix: la reflection ricorsiva `659b46c4` e il
  confronto duplicato/prescrittivo `76daa5aa` sono stati soft-esclusi; la
  reflection `e52ce33d`, nascosta per effetto collaterale dal dedup, è stata
  ripristinata. Quattro copie Redis aggiuntive restano recuperabili per 30
  giorni e le coppie ricorsive sono marcate `non_evolution`.
- Aggiunte regressioni per promozione sostenuta, ipotesi convergente, misura
  mancante, premesse infedeli, confronto semantico, inversione della
  supersessione e identità incerta. Stabilizzato inoltre un test dell'indice
  Loop 2e che usava timestamp assoluti ormai usciti dalla finestra mobile.
  Il caso live aggiunge regressioni anti-ricorsione, anti-conflazione e
  deduplicazione dei confronti.

## 2026-07-25 — Estrazione passiva atomica a finestre

- Le sessioni lunghe vengono analizzate in finestre da 12 messaggi con overlap
  di 4: un dettaglio locale non compete più con l'intera conversazione né con un
  limite globale di sei fatti.
- Il contratto richiede memorie atomiche e proprietà autonome. Identità dei
  parlanti e ID locali `T1...T12` vengono risolti negli ID reali della sessione
  prima del salvataggio.
- Il parser accetta in modo controllato `TURNI` e la variante Gemma `TURNOS`,
  con o senza prefisso `T`; altre intestazioni `[TIPO=...]` malformate vengono
  scartate e non possono finire nel contenuto.
- Gli ID formalmente dubbi non eliminano più subito un fatto: vengono differiti
  all'audit semantico fail-closed. Validazione e risoluzione temporale avvengono
  nell'ordine contenuto → audit fonti → data canonica.
- Una chiusura deterministica conserva il precedente turno dell'utente quando
  serve a risolvere un'anafora come “È un misto...”.
- Replica LoCoMo-IT: q99 passa da astensione a “La sceneggiatura di Joanna è un
  misto di dramma e romanticismo”, con fonti `[25,27]` (`D2:3` + `D2:5`);
  evidence recall `0,625 → 0,875`, F1 `0,318 → 0,396` e accuracy avversariale
  invariata a `1,000`. Il costo sale a 92 chiamate locali e 22 memorie salvate:
  la prossima ablation deve ridurre frammentazione e costo senza perdere
  copertura.

## 2026-07-25 — Provenienza semantica delle memorie passive

- `TURNI` deve ora contenere l'unione di tutte le fonti necessarie a sostenere
  ogni affermazione della memoria, non soltanto turni esistenti del ruolo giusto.
- Un audit finale, eseguito dopo il gate di utilità e prima di dedup/salvataggio,
  confronta il contenuto con i turni originali, aggiunge le fonti dimenticate e
  scarta output non supportati o non verificabili.
- Il benchmark conserva il nome originale del parlante: un “io” di Joanna non
  viene più attribuito genericamente al ruolo `UTENTE`; nell'uso reale il
  fallback resta il profilo locale di Stefano/Euri.
- Il Buttafuori passivo non riscrive più il contenuto. Risponde soltanto
  `KEEP/JUNK`, evitando che una parafrasi aggiunga qualificazioni senza fonte.
- Il parser dell'audit accetta in modo controllato ID numerici, `T25` e liste
  `T25,T27`, poi verifica comunque esistenza e ruolo nel dialogo.
- Replica LoCoMo-IT finale: 7 candidati estratti, 7 validati e 7 salvati, zero
  scarti di provenienza e 4 liste di fonti riparate. Sonda reale sul difetto
  originario: la memoria combinata della sceneggiatura corregge `[25]` in
  `[25,27]`, cioè evidence `D2:3` + `D2:5`.

## 2026-07-25 — Deduplicazione passiva conservativa

- La similarità vettoriale individua ora soltanto candidati: non può più
  eliminare automaticamente una memoria. L'identità testuale resta un fast path.
- Prima del giudice LLM, il candidato deve coprire tutti i marker informativi
  della nuova frase; soggetti espliciti diversi, numeri/date diversi e negazioni
  fanno conservare il fatto.
- Il giudice elimina soltanto su risposta esatta `DUPLICATO`; output ambigui,
  errori e dubbi sono fail-open. Il prompt chiarisce che stesso tema o soggetto
  non equivalgono allo stesso contenuto.
- Regressioni aggiunte per i tre falsi positivi LoCoMo-IT, quantità `100/120 kg`,
  soggetti distinti, negazione, identità normalizzata e risposta LLM ambigua.
- Replica isolata dopo i fix data+dedup: 9 memorie estratte e 9 salvate, zero
  falsi duplicati e 36 chiamate LLM contro 48 nella run storica. Nella stessa
  replica F1 `0,331 → 0,405` (+0,074), con evidence hit invariato a 0,625.
  Il campione resta diagnostico e non statistico.

## 2026-07-25 — Data locale canonica nel Passive learner

- Il prompt di estrazione passiva usa ora `format_datetime_full`: giorno della
  settimana, mese e ora sono esplicitamente italiani e indipendenti dal locale
  `C/POSIX`, come nel percorso conversazionale principale.
- Gemma deve conservare i riferimenti relativi pronunciati dall'utente
  (`ieri`, `venerdì scorso`, `questa mattina`) senza convertirli autonomamente.
- La fonte conversazionale e `asserted_at` sono canonici: il resolver
  deterministico produce `event_start/event_end` prima del salvataggio.
- Aggiunto un guard condiviso da voce, Silent Chat e benchmark: se il testo LLM
  contiene una data numerica in conflitto con la fonte, viene corretto prima di
  validazione e deduplica. Il `temporal_context` conserva espressione sorgente,
  espressione generata, data originale e flag di correzione.
- Regressione LoCoMo-IT: domenica 23 gennaio 2022 + “venerdì scorso” risolve al
  21 gennaio e corregge deterministicamente l'uscita errata `20/01/2022`.

## 2026-07-25 — LoCoMo italiano e confronto EN/IT

- Aggiunta una localizzazione italiana completa e versionata della selezione
  `conv-42` v2, preservando sessioni, turn ID, categorie ed evidence.
- Il comando A/B accetta `--localization`; prompt di risposta, gold e astensione
  possono ora essere eseguiti interamente in italiano.
- Lo scorer ridotto riconosce anche le principali astensioni italiane.
- Run IT pulita: F1 `0,318 → 0,368`, evidence hit `0,625 → 0,625`,
  avversariale `1,000 → 1,000`; 9 memorie estratte e 6 salvate.
- Evidenziati falsi duplicati e una data passiva errata (`20/01/2022`); la data
  è ora coperta dalla correzione deterministica descritta sopra e i falsi
  duplicati dalla deduplicazione conservativa descritta nella prima sezione.

## 2026-07-24 - Prima A/B reale LoCoMo: RAG contro memoria passiva

- Aggiunto il comando `benchmarks.euri_memory.cli ab`: avvia il codice Euri in un
  processo figlio soltanto dopo aver fissato Redis e Vault temporanei, carica
  l'embedder locale una volta e resetta lo stesso runtime fra i due profili.
- La baseline indicizza 35 turni LoCoMo grezzi; il trattamento aggiunge il
  percorso reale Passive learner, Buttafuori, deduplica, dominio e salvataggio.
  Answer gold ed evidence entrano soltanto nello scorer dopo la generazione.
- Il report registra dataset e selezione con SHA-256, stato Git, modello, prompt,
  seed, chiamate e token Ollama, tempi, crescita Redis, memorie passive e trace di
  retrieval per domanda.
- Prima run con `gemma4:26b`: F1 0,370→0,356, exact match invariato a 0,333,
  evidence recall invariato a 0,750 e accuracy avversariale 1,000→0,500. Le tre
  memorie passive non migliorano questo piccolo campione e rivelano un caso di
  over-answer da conservare come regressione.
- Replica v1: baseline identico, Passive F1 0,364 e accuracy avversariale 1,000;
  la variazione conferma instabilità del trattamento. Seconda selezione `conv-42`
  su 51 turni/8 domande: F1 0,187→0,162, evidence recall 0,625 invariato,
  8 memorie passive salvate su 10 candidati. Il passo successivo è ampliare il
  campione, non promuovere questa sonda a risultato ufficiale.

## 2026-07-24 - Banco prova memoria isolato e adapter LoCoMo

- Creato `benchmarks/euri_memory` con contratti separati per corpus, prompt e
  gold, profili A/B dichiarativi, trace e scoring deterministico di cablaggio.
- Ogni run usa un processo Redis effimero con RedisJSON/RediSearch e un Vault
  temporaneo; porta personale, percorsi esterni, PID e marker sono verificati
  prima di reset o scritture.
- L'adapter LoCoMo gestisce sessioni, timestamp, immagini annotate, categorie,
  evidence multiple e riferimenti mancanti senza nascondere difetti del gold.
- Il downloader acquisisce corpus e licenza CC BY-NC 4.0 dalla fonte ufficiale,
  registra gli SHA-256 e lascia i dati fuori da Git.
- Smoke reale completato su 419 turni e 199 domande della prima conversazione,
  con 618 eventi trace e nessun accesso a Redis o Vault personali.

## 2026-07-24 - Passive learner osservabile e conferme Pulse fondate

- Il Passive learner analizza anche un singolo scambio completo e rende visibili
  estrazione, validazione, scarti, duplicati e salvataggi.
- L'estrazione strutturata con Gemma 4 26B usa `think=False`: il reasoning esteso
  impediva spesso di produrre la lista finale parsabile.
- La diagnostica dell'estrattore registra solo contatori e forma dell'output,
  senza contenuti grezzi.
- Le domande Pulse sulle memorie passive aprono ora una verifica associata all'ID
  esatto. La risposta dell'utente viene classificata semanticamente e aggiorna
  realmente lo stato epistemico del nodo confermato o smentito.
- Verifica live dell'estrazione: 5 candidati accettati, 3 memorie salvate e 2
  duplicati esclusi. Test Initiative estesi a 12/12.

## 2026-07-24 - Corretto il salvataggio nominato delle informazioni UBQ

- “Queste informazioni con il nome …” viene ora riconosciuto come SAVE_MEMORY,
  anche nella forma STT “questi informazioni”, invece di entrare accidentalmente
  nella sessione TEACH.
- Il contenuto viene risolto dallo scambio precedente e il nome viene conservato
  come `memory_title`; la conferma non può più dichiarare salvata una semplice
  etichetta.
- Riparato in modo reversibile il record UBQ live: il frammento errato è
  soft-superseded e quarantinato, mentre la memoria corretta resta da verificare
  sulle prove meccaniche. Aggiunti test deterministici e repair idempotente.

## 2026-07-23 - Recall distinto dall'uso osservabile nella risposta

- I turni conversazionali RAG di voce, Silent Chat e mobile producono una trace
  cognitiva completa: confine del turno, memorie e insight realmente iniettati,
  risposta generata e uso attribuibile.
- `recalled` resta distinto da `used_in_response`. L'uso viene emesso soltanto
  quando la risposta finale conserva evidenza lessicale distintiva non spiegata
  dalla sola domanda; il risultato è marcato `supported_not_proven` e non cambia
  memoria, ranking o risposta.
- Il Pulse conserva soltanto hash, lunghezze e metadati di retrieval/evidenza:
  nessun prompt, risposta o contenuto mnemonico viene copiato nella timeline.
- L'audit cognitivo riassume ora turni, recall, uso supportato e canali. Aggiunti
  test puri per provenance del prompt, causalità, privacy ed esclusione delle
  semplici eco.

## 2026-07-23 - Loop 2h con lineage e smentite escluse dal judge

- La prova idle reale ha confermato che Loop 2a non riprocessa più dialoghi
  precedenti al checkpoint. Ha però mostrato che Loop 2h pubblicava le proprie
  narrative senza parent né confine epistemico e poteva contare due volte una
  coppia restituita più volte da Redis `SCAN`.
- Ogni self-observation porta ora loser/winner, coppie causali deduplicate e stato
  `internal_self_observation`; resta da verificare e produce un evento cognitivo
  `reflection/created`. Un cambio di attività o di supersessione durante il lavoro
  annulla il commit senza consumare le coppie.
- I candidate già demoti vengono esclusi prima delle misure e dei judge costosi.
  Una smentita esterna non può essere superata da convergenza interna o richiamo
  incidentale; annotazione, log, trace e Pulse del blocco sono emessi una sola volta.
- Aggiunto un repair idempotente per completare la lineage della reflection live
  `92bac556` senza alterarne il contenuto.

## 2026-07-23 - Reflection sessionali e agenda protetta dal parlato descrittivo

- Il Loop 2a non tratta più una finestra globale di quattro ore come sessione:
  usa un checkpoint Redis durevole, un singolo segmento conversazionale e parent
  ID espliciti. Dopo cinque minuti di vero idle genera una reflection interna da
  verificare; se nel frattempo riparte il dialogo, il commit viene annullato.
- Le azioni agenda richiedono sia un gesto corrente specifico (`chiudi`, `sospendi`,
  `rimanda`...) sia un bersaglio grounded nel turno o nel riferimento immediato.
  Frasi descrittive come “oggi faccio la prova, domani avrò i risultati” non
  autorizzano più modifiche e un guasto del controller fallisce chiuso.
- Il Pulse traccia proposta, decisione, rivalidazione ed esito delle azioni, inclusi
  stato precedente e successivo. Una mutazione non può più restare nascosta dentro
  una risposta conversazionale generata.
- Riparato reversibilmente l'incidente UBQ: reflection stale ritratta, precedente
  riattivata, todo hardware lasciato pending senza scadenza, copia Vault posta in
  quarantena e before/after conservato nell'audit.

## 2026-07-23 - Pulse v2 distingue telemetria e lineage cognitiva

- `euri:pulse` resta compatibile, ma ogni nuovo evento porta un envelope v2 che
  distingue telemetria grezza e transizione cognitiva, con produttore, trace,
  causalità, entità, antenati e confine epistemico.
- Il nuovo Cognitive Projector usa un consumer group durevole e copia soltanto gli
  eventi cognitivi in `euri:cognitive:events`. La proiezione è idempotente e
  osservazionale: non crea memorie, non modifica insight e non esegue azioni.
- Prima lineage collegata a memoria salvata, semi Dream, candidate creati/scartati,
  promozioni/demozioni, reaction con verdetto e consolidamenti figlio-genitori.
- `docs/PULSE_COGNITIVE_ROADMAP.md` è il checkpoint persistente del cantiere;
  `scripts/audit_cognitive_pulse.py` verifica stream, consumer e copertura senza
  mutare Redis.
- Verifica pre-riavvio: 42/42 unit; Redis storico invariato (3.924 eventi legacy,
  timeline cognitiva e consumer group ancora assenti).

## 2026-07-23 - Uno scherzo sul turno non ritira una memoria del RAG

- Il caso live "stavo scherzando, volevo vedere se avevi capito il termine" resta
  osservabile come correction signal, ma non apre più una quarantena immediata:
  descriveva il tono del saluto precedente, non ritirava il record `fd66ecb3`.
- `stavo scherzando` ed `era una provocazione` sono ora segnali di audit non
  mutanti, anche quando nominano soggetti presenti nel RAG. Nascono già chiusi come
  `mutation_policy=audit_only`, non emettono Pulse e non possono quindi essere
  reinterpretati dal Loop 2g notturno come correzioni o lezioni. La quarantena
  immediata resta soltanto per ritrattazioni fattuali esplicite (`non ho davvero`,
  `ti correggo`, `hai sbagliato`) con un bersaglio sostanziale identificabile.
- Il confine vale per ogni correction signal, non solo per gli scherzi:
  `proposal_only` consente al Loop 2g di registrare un `proposed_verdict`, ma vieta
  `audit_flag`, `requires_verification` e nuove lezioni finché manca una correzione
  esplicita. Anche i signal legacy senza policy falliscono chiusi come proposte.
- Il falso segnale live `28d74085` è stato chiuso come `not_a_correction`; la
  quarantena reversibile di `fd66ecb3` è stata rimossa e la traccia audit conservata.

## 2026-07-23 - Sincronia canonica e confine interno/esterno degli insight

- `save_memory(final_fields=...)` pubblica ora il documento gia' completo: dominio,
  provenienza e fragilita' epistemica entrano nello stesso commit RedisJSON+outbox.
  Reaction, memoria passiva, web, Obsidian, confronti 2f e consolidamenti 2e non
  post-mutano piu' una bozza gia' osservata da Pulse, indici e Vault.
- Lo ZSET del Loop 2e viene ricostruito dal JSON canonico al boot. Riconciliazione
  live: da 133 ID indicizzati contro 117 validi (9 mancanti, 25 stale) a 117/117,
  zero missing e zero stale.
- La convergenza Dream conserva il proprio significato nel sistema del paper:
  `candidate/promoted` descrive una dinamica INTERNA, non una validazione del mondo.
  Ogni insight nasce `internally_emergent`, diventa `internally_convergent` e resta
  esplicitamente da verificare finche' una reaction esterna `CONFERMA` non attraversa
  il confine epistemico.
- Riconciliati 164 insight promossi storici: 160 interni/da verificare e 4 confermati
  esternamente. Il contesto li presenta come "connessioni emerse", mai come principi
  acquisiti per il solo fatto di essere ricorrenti.
- Obsidian distingue nel titolo/frontmatter emergenza interna e conferma esterna;
  il filename include anche l'ID, evitando collisioni tra promozioni nello stesso secondo.
- I timeout Dream/Loop 2h ora sono timeout HTTP reali del client Ollama: eliminato il
  `ThreadPoolExecutor` che, in uscita dal context manager, poteva attendere comunque
  una chiamata bloccata. Un watchdog segnala inoltre worker vivi ma senza heartbeat.
- Verifica: 41/41 unit, 3/3 integration, compilazione completa e drift Redis azzerato.

## 2026-07-23 - Le smentite non conservano parti inventate dell'insight

- L'ack vocale di una reaction e' ora neutro: arriva prima del verdetto asincrono
  e non presume piu' che la risposta contenga insieme conferme e correzioni.
- Un verdetto `SMENTITA` esclude per costruzione la sintesi generativa. La lezione
  conserva domini, verdetto e reazione grezza, senza ripetere la tesi bocciata né
  inventare una parte dell'insight che "resta valida".
- Aggiunte provenienza `extractive_refutation` e regressioni contro il caso reale
  `03ppr102`/setpoint. Il record contaminato e il primo repair intermedio sono
  soft-superseded; Redis e Obsidian espongono soltanto la lezione estrattiva finale.

## 2026-07-23 - VisualGate recupera gli stalli USB della webcam

- Un `cap.read()` V4L2 fallito non lascia piu' il loop agganciato per sempre allo
  stream guasto: la cattura viene rilasciata e la webcam riaperta con backoff
  crescente e limitato.
- Durante la riconnessione il gate e' esplicitamente cieco e fail-open, quindi la
  voce non viene ignorata; l'identita' sticky del vecchio stream viene invalidata.
- La discovery automatica riparte dai nodi `/dev/video*`, così un replug USB che
  rinumera la webcam viene recuperato senza riavviare Euri. Regressione dedicata
  sul guasto runtime e sul recupero verso un device differente.

## 2026-07-22 - I tool restano dentro la risposta conversazionale

- Distinte le richieste operative pure dalle domande che usano un tool come
  sottopasso: `response_mode=tool_result` chiude il turno solo quando l'esito
  soddisfa tutta la richiesta; `integrated` restituisce invece il risultato al
  Brain e produce una risposta unica che copre anche spiegazione o valutazione.
- Le alternative e le azioni read-only proposte da Euri sono sempre integrate:
  l'output verificato diventa evidenza, non un sostituto della domanda originale.
  Il percorso non rientra nel dispatch e non puo' innescare una seconda azione.
- Rafforzati classificatore e ActionController sul caso reale "esamina quello che
  sai fare e dimmi come migliorare il sistema, magari usando i tuoi strumenti":
  una richiesta riflessiva senza sottopasso concreto resta `CHAT`, non autorizza
  un controllo hardware casuale. Prova sul modello locale: abstain con authority
  `none`; regressione dedicata e suite unitaria 39/39.

## 2026-07-22 - Reaction parziali come patch di proposizioni

- `PARZIALE` non lascia piu' un insight promosso come se fosse interamente valido:
  imposta `requires_verification` e `verification_status=partially_refuted_by_user`.
- Le reazioni miste vengono scomposte in claim confermati, smentiti e sostitutivi.
  Ogni claim deve portare una citazione contigua realmente presente nella risposta;
  le voci prive di evidenza letterale vengono scartate deterministicamente.
- Per una reaction parziale la lezione e' costruita in modo estrattivo: non inventa
  il collegamento operativo mancante. Il RAG presenta sempre insight originale e
  patch correttiva insieme, marcandoli come ipotesi da verificare.
- Riparato il caso Giuseppe/back office: la sintesi inferenziale precedente resta
  come provenienza ma e' `superseded`; la lezione confermata distingue carichi e
  partenze (back office) dalla valutazione materiale (laboratorio). L'insight resta
  promosso perche' il principio generale e' stato confermato, ma e' marcato parziale.

## 2026-07-22 - VisualGate disponibile nella Silent Chat senza biometria condivisa

- Il Voice Daemon pubblica su Redis uno snapshot operativo effimero del VisualGate
  con TTL breve: disponibilita' camera, presenza e identita' dichiarativa. Frame,
  embedding e punteggio di similarita' non attraversano il ponte.
- La Silent Chat riceve lo snapshot come contesto operativo, non come memoria: un
  owner riconosciuto e' forte evidenza locale di presenza, ma non autorizzazione
  crittografica del dattilografo e non sostituisce conferme per azioni sensibili.
- Se lo snapshot manca o scade, Euri distingue "stato corrente non disponibile"
  da "non possiedo VisualGate", evitando la falsa negazione osservata dal vivo.

## 2026-07-22 - Self-model senza autocertificazione e identita' configurabile

- Il prompt distingue ora stato operativo verificato, descrizione progettuale fornita
  dall'utente, valutazione soggettiva e interpretazione/autobiografia di Euri. Una
  memoria Redis prova che il contenuto e' stato registrato, non che sia vero o attuale.
- Le sintesi clipboard persistenti usano `memory_kind=document_summary`; le vecchie
  `semantic_fact` vengono riconosciute senza migrazione e marcate nel RAG come sintesi
  documentali, non verifiche interne. Nessuna memoria viva e' stata modificata.
- Introdotto il profilo installazione `OWNER_ACTOR_ID` / `OWNER_DISPLAY_NAME` /
  `ASSISTANT_DISPLAY_NAME`, configurabile via ambiente. I principali percorsi cognitivi,
  cronologici, proattivi e di autenticazione non dipendono piu' dal nome letterale del
  proprietario; il default dell'istanza corrente resta Stefano/Euri.
- Regressione portabilita' con profilo fittizio Ada/Nora e verifica del piano documentale
  nel RAG. Aggiornata anche la descrizione della clipboard nel self-model.

## 2026-07-22 - Clipboard: analisi temporanea separata dalla memoria

- `analizza/riassumi gli appunti` usa ora il contenuto soltanto nella sessione e non
  crea automaticamente una memoria permanente `source=teach`.
- `salva/memorizza gli appunti` e `analizza e salva` passano da un tool distinto;
  la persistenza richiede quindi un'intenzione esplicita dell'utente.
- Gestite le negazioni mirate (`senza salvare` analizza soltanto; `non analizzare`
  non esegue nulla) e i fallimenti di salvataggio vengono dichiarati senza falsi
  successi. Aggiunte regressioni su persistenza, routing ed esposizione contestuale.

## 2026-07-22 - Dream Trace Paired v2: isolamento batch e residuo fail-closed

- Chiuso come pilot diagnostico il batch v1 (1 warm-up + 8 coppie): 5 trattamenti
  avevano ricevuto la sentinella errata `NESSUN INSIGHT`; nessun trattamento era
  entrato nella memoria reale.
- Nuova `experiment_version=dream_trace_paired_v2`; residuo e contatore usano chiavi
  Redis versionate. Lo stream v1 resta intatto ma non può contaminare o entrare
  nell'export v2.
- La distillazione appaiata accetta solo righe strutturate, elimina la chiave se non
  resta alcuna riga valida e forza quindi un nuovo warm-up. Un filtro deterministico
  scarta anche righe che riecheggiano fortemente il residuo appena iniettato.
- Il campionatore verifica anche hash del residuo, durata e stati ammessi; l'allarme
  sugli errori ora rileva anche gli squilibri `0:N`, prima invisibili.
- Regressioni aggiunte per sentinella errata, residuo stale, compatibilità legacy,
  eco vibrazioni/EMI, integrità e squilibrio errori.

## 2026-07-21 - Dream Trace Paired V2: qualita' separata da utilita', confronto sullo stesso seme

- Nuovo protocollo `ESPERIMENTO_DREAM_TRACE_V2.md`: il vecchio N/O/? non mescola
  piu' grounding, novita', chiarezza e utilita' personale. Pass primario tecnico:
  `G2 + V2 + C`; utilita' di Stefano separata in `U_NOW/U_LATER/U_NO/U_CONTEXT`.
- Una prima stesura usava bracci concorrenti a blocchi di due cicli con coppie di
  domini estratte a caso (mai attivata, nessun dato raccolto). Su indicazione di
  Stefano, sostituita da un disegno APPAIATO: stesso seme (stesse due memorie)
  generato due volte, con e senza residuo — elimina la variabilita' tra coppie di
  domini diverse invece di mediarla su n grande, e non richiede piu' bilanciamento a
  blocchi (ogni coppia contiene per costruzione un baseline e un trattamento).
- Il residuo evolve SOLO dal lato trattamento: il baseline resta strutturalmente
  isolato, prompt bit-identico a oggi a flag spento.
- Stream dedicato `euri:dream_trace:paired:cycles`: salva al momento della
  generazione output, due sorgenti, `pair_id`+`arm`, residuo, lunghezze e SHA-256
  completi, anche per scarti/errori; nessuna dipendenza dalla sopravvivenza del
  candidate. Uno scarto conta come non-passa per quel lato, non come esclusione
  della coppia (altrimenti si ripeterebbe la sopravvivenza asimmetrica del batch
  precedente in una forma diversa).
- Esportatore cieco fail-closed: richiede 50 coppie complete (entrambi i lati non-
  error, non duplicati, hash coerenti su QUALUNQUE stato) e include le memorie
  sorgente per giudicare davvero il grounding. Due valutatori indipendenti (Codex,
  Claude) giudicano separatamente; un disaccordo su una qualunque dimensione rende
  quel lato AMBIGUO — nessuna adjudicazione, nessuno dei due corregge l'altro — nota
  aperta in `ESPERIMENTO_DREAM_TRACE_V2.md` sui limiti di indipendenza tra due
  modelli linguistici.
- Analisi primaria: McNemar ESATTO (binomiale), non l'approssimazione χ² — con 50
  coppie i discordanti saranno quasi certamente troppo pochi per l'approssimazione.

### Correzioni post-review Codex (stesso giorno, prima di qualunque raccolta)

- Solo il lato baseline persiste come `euri:insight:*` vivo (embedding, retrieval,
  eleggibile a promozione); il trattamento resta strumentazione pura finche' non e'
  validato — altrimenti ogni coppia raddoppiava i candidate che entrano nella
  cognizione reale di Euri.
- Il residuo loggato per lato ora e' quello REALMENTE iniettato in quel prompt
  (vuoto per il baseline), non il residuo vivo in generale.
- Validazione hash/lunghezza dell'output estesa a `discarded`, non solo `candidate`.
- Un lato duplicato (stesso pair_id+arm) invalida l'intera coppia, non viene piu'
  tenuta silenziosamente la prima occorrenza.
- Errori di generazione contati per braccio con soglia di allarme (rapporto >=2x),
  non piu' esclusi in silenzio dal conteggio.
- Aggiunta la misura di durata per lato (`duration_s`), prevista ma assente.

`DREAM_TRACE_PAIRED_ENABLED=True` (avviato da Stefano, poi corretto): la raccolta
non aveva ancora generato nulla su Redis reale (chiavi/stream assenti, verificato)
quando le correzioni sono state applicate — nessuna pulizia necessaria, ma serve un
nuovo riavvio di Euri per caricare il codice corretto. Regressioni 36/36 (inclusi i
nuovi casi su duplicati/errori/hash-su-scarti), inventario 45 test.

## 2026-07-21 - Audit Dream Trace fermato per troncamento della misura

- L'audit cieco 60+60 non e' valutabile: la convergence trace salvava soltanto
  `seed_content[:600]`, tagliando in 109/120 item la terza riga da giudicare.
- Controllo read-only senza unblinding: 81 testi recuperabili dallo stato Redis vivo
  o dai sogni grezzi, 39 perduti. Il sottoinsieme recuperato non viene usato perche'
  introdurrebbe bias di sopravvivenza. La chiave resta sigillata.
- La trace salva ora l'intero candidate con numero di caratteri, SHA-256 e marcatore
  esplicito di completezza. Il campionatore rifiuta le entry legacy/incomplete e si
  arresta se uno dei bracci non raggiunge la numerosita' richiesta.
- Il report Codex e' conservato come verbale, ma marcato chiaramente non valido.

## 2026-07-21 - Raccolta Dream Trace congelata e semi epistemicamente puliti

- La raccolta `dream_trace` e' stata chiusa prima di modificare la generazione:
  160 candidate baseline, 76 trattamento grezzi, 2 esclusi perche' precedenti al
  riavvio anti-eco del 13/07 17:35:58, quindi 74 trattamento validi. Il campionatore
  applica ora il cutoff pre-registrato e salva sotto `audit_output/` un audit cieco
  riproducibile da 60+60 item con chiave separata e non versionata.
- `DREAM_TRACE_ENABLED` torna `False`. Il risultato non e' stato aperto: Stefano deve
  ancora etichettare N/O/? prima dell'unblinding.
- Il Loop 2b puo' fondare sogni soltanto su fonti dirette/deliberate (`user`, `teach`,
  `passive`, `conversation`, `obsidian_vault`, `mobile_in`). Reflection, reaction,
  consolidamenti, anchor/episodi conversazionali e tag derivati sono esclusi.
- Ogni candidato viene idratato dal RedisJSON vivo e rivalidato fail-closed: niente
  nodi superseded/consolidati, sotto correzione, da verificare, stale, con safety/audit
  flag, rischio di consolidamento, soggetto acefalo o vecchio assenso tacito.
- La selezione prova fino a 12 domini per ciascun lato: un dominio composto soltanto
  da memorie fragili non fa fallire casualmente tutto il ciclo creativo. Sonda read-only
  sullo store reale: 114 domini diretti, 95 con almeno un seme eleggibile; i nodi noti
  Leonardo e VistaMax contaminato vengono respinti dal gate corrente.
- Nuovo `test_dream_seed_gate.py`; manifest unitario 35/35, inventario 44 test.

## 2026-07-21 - Intenzione conversazionale, azione reale e proposte fattibili

- Introdotto un `ActionController` generale per il canale vocale: il modello puo'
  proporre soltanto capability registrate e bersagli presenti nello stato corrente;
  policy e adapter deterministici decidono se eseguire, chiarire, confermare o
  astenersi. Il contesto risolve riferimenti come "lo", ma non crea autorizzazione.
- Le formulazioni CHAT non dipendono da una regex per essere capite. La regex resta
  un fast-path; il fallback LLM puo' emettere `ACTION_REASONING` e invocare il prompt
  semantico su obiettivo, sottopassi, capability e stato reale.
- Quando il gesto completo non e' disponibile, Euri puo' proporre un solo passo
  alternativo realmente fattibile. Le alternative read-only possono essere eseguite
  e riferite; scritture, effetti esterni e azioni distruttive richiedono conferma.
  Una proposta di Euri viene sempre marcata `euri_proposed` e non si auto-autorizza.
- L'agenda supporta ora tre adapter grounded: completamento, sospensione senza data
  e riprogrammazione. Il caso live "Considero chiuso... decido io la data" seleziona
  il todo Poseidon esatto e `agenda.complete`. Collaudo vocale post-riavvio riuscito:
  confidenza 1.00, adapter eseguito e record Redis passato da `pending` a `done`.
- Le intenzioni read-only formulate dalla stessa Euri (per esempio "ora controllo
  la GPU") possono diventare una lettura reale prima della risposta. I claim presenti
  non coperti come "lo tolgo dai sospesi" restano intercettati dal paraurti atto-parola.
- Il riepilogo degli scaduti usa giorni civili: una scadenza del giorno precedente
  viene detta "da ieri", anche se non sono trascorse 24 ore complete.
- Probe read-only sul modello e stato reali: chiusura e sospensione Poseidon grounded,
  controllo GPU, astensione su conversazione, formula senza trigger "non e' piu' da
  fare" e proposta `read_log` quando il riavvio richiesto non e' disponibile.
- Per scelta di prodotto, qualita' del ragionamento e delle proposte prevalgono sulla
  latenza sotto contesa; il futuro upgrade hardware dovra' sostenere il modello, non
  giustificare una regressione verso routing rigido a parole chiave.
- Regressioni: 13 casi specifici ActionController, 37/37 sul paraurti atto-parola,
  manifest unit completo 34/34 e inventario test 43 file.

## 2026-07-21 - Convivenza GPU e ciclo di vita Control Room

- Whisper non assume piu' che CUDA:0 sia disponibile: interroga NVML oppure
  `nvidia-smi`, prova le GPU in ordine di VRAM libera e, solo in caso di OOM,
  ritenta sulla successiva. `WHISPER_CUDA_DEVICE_INDEX` consente comunque un
  indice fisso sulle installazioni dedicate.
- La scelta serve alla workstation di prova condivisa con PlastVision: il suo
  Guardian resta correttamente permanente e puo' tenere caldo un modello Ollama,
  senza essere terminato dal launcher di Euri. La separazione futura su hardware
  dedicato resta la soluzione definitiva al contendere della VRAM.
- `start_euri.sh` lega la Control Room al ciclo di vita del Voice Daemon, usa la
  porta fissa 8501 e chiude Streamlit anche se il setup vocale fallisce. Il monitor
  hardware viene avviato in una sessione separata e resta invece intenzionalmente
  indipendente, protetto dal proprio lock, per non interrompere baseline e
  interocezione tra i riavvii o dopo un `Ctrl+C` di Euri.
- Il teardown idempotente del Voice Daemon copre ora anche le eccezioni durante
  `setup()`, non soltanto l'uscita dal loop principale, evitando componenti avviati
  a meta' dopo errori CUDA o di inizializzazione.
- Le regressioni coprono ordinamento GPU, fallback dopo OOM e ciclo di vita dei tre
  processi del launcher; il test live post-riavvio ha caricato Whisper su CUDA:1.

## 2026-07-20 - Calibrazione visiva guidata e multi-postura

- La pagina `Volti & Accessi` guida ora quattro scatti distinti per il faceprint:
  postura abituale, seduto diritto e viso leggermente ruotato nei due versi.
  SFace conserva prototipi separati invece di mediare pose diverse; la soglia di
  identita' non viene abbassata e i vecchi faceprint a vettore singolo restano validi.
- I prototipi biometrici sono limitati a otto per persona. Nessuna foto viene salvata:
  persistono soltanto gli embedding locali gia' previsti dal FaceAuth.
- La stessa pagina offre una calibrazione sociale guidata in quattro fasi neutro/
  sorriso e postura abituale/diritta. Se le distribuzioni non sono separabili il
  profilo viene rifiutato; altrimenti il daemon ricarica a caldo soglie personali.
- Il profilo sociale contiene solo soglie, riepiloghi di posa e diagnostica numerica.
  Rimane Fase 0: nessun LLM, memoria cognitiva, tono o Initiative dipende dai segnali.
- L'enrollment non apre piu' una seconda webcam nel browser. La Control Room invia
  al daemon soltanto comandi di cattura e il VisualGate calcola i quattro embedding
  sui frame che possiede gia'. Immagini ed embedding non attraversano Redis; durante
  lo scatto l'enrollment ha priorita' e al termine il riconoscimento normale prosegue.

## 2026-07-20 - Percezione sociale visiva Fase 0

- Il VisualGate riusa la propria webcam per estrarre localmente pochi blendshape e
  la posa della testa tramite MediaPipe, senza seconda acquisizione e senza immagini
  persistenti. Il recettore opera solo dopo un riconoscimento recente di Stefano.
- Baseline personale di sessione, finestra mediana, isteresi e persistenza producono
  segnali descrittivi e transizioni, non etichette emotive. Redis conserva un latest
  con TTL 30 secondi e una baseline numerica; il Pulse riceve solo cambi stabili.
- La Fase 0 non chiama Gemma, non scrive memorie e non cambia tono, Initiative o TTS.
  L'eventuale interprete multimodale occasionale in idle resta esplicitamente spento.
- Aggiunto `scripts/audit_social_perception.py` per controllare in sola lettura stato,
  baseline e transizioni dopo il riavvio.
- Il primo protocollo guidato ha validato sorriso lieve/marcato ma non sopracciglia
  e sguardo. Corretta la decomposizione pitch/yaw/roll della matrice MediaPipe e
  aggiunti coefficienti grezzi selezionati per calibrare senza abbassare soglie alla cieca.
- Il protocollo schermo/webcam/tastiera ha mostrato la convenzione effettiva del
  modello: sulla postazione reale `eyeLookUp` cresce guardando sotto la webcam
  (`~0.45` sulla tastiera contro `~0.05-0.07` su schermo/webcam). `gaze_down` usa ora
  questa coppia con isteresi 0.25/0.15; i coefficienti originali restano nell'audit.

## 2026-07-20 - Intenzione per la percezione sociale visiva

- Documentata in `SPEC_SOCIAL_PERCEPTION.md` la futura estensione del VisualGate:
  osservare landmark, orientamento e variazioni espressive per regolare disponibilita',
  tono e iniziativa senza attribuire emozioni certe.
- Definite quattro fasi, dalla raccolta read-only alla calibrazione personale, con
  separazione tra osservazioni effimere nel Cognitive Present e memoria cognitiva.
- Nessun comportamento runtime e' stato attivato; immagini non persistenti, percorso
  ospite e fail-silent dell'interpretazione restano vincoli espliciti.

## 2026-07-20 - Interlocutori sconosciuti in quarantena epistemica

- SpeakerAuth restituisce ora `VERIFIED`, `REJECTED` o `INDETERMINATE`: una clip
  breve o un errore del recettore non equivalgono piu' automaticamente a Stefano.
  Gli esiti incerti vengono fusi con volto del proprietario o voce autenticata
  recente; fuori da queste condizioni restano sconosciuti.
- Una voce sconosciuta che chiama esplicitamente Euri entra in un percorso ospite
  isolato: niente memoria privata, RAG, intenti operativi, tool, agenda o history
  condivisa. Puo' conversare in generale, ma non agire per conto di Stefano.
- Le sole affermazioni durevoli dell'ospite diventano `guest_claim` in una coda Redis
  limitata e con TTL 30 giorni, esterna a RediSearch, Dream e consolidamento. Quando
  Stefano torna autenticato, Euri chiede un verdetto; solo il si' crea una memoria
  fattuale che conserva `origin_actor_id=unknown` e `confirmed_by_actor_id=stefano`.
- Un rifiuto scarta il claim; "piu' tardi" lo lascia in quarantena. Nessun assenso
  pronunciato dal percorso ospite puo' confermare una richiesta rivolta a Stefano.

## 2026-07-20 - Provenienza continua senza intreccio dei ruoli

- Il passive learner non accetta piu' fatti sostenuti da turni dell'assistente:
  ogni `semantic_fact` deve citare esclusivamente uno o piu' turni di Stefano.
  Silenzio, cambio di argomento e assenso breve non promuovono una frase di Euri.
- Gli episodi compressi separano `DETTO DA STEFANO`, `CONTRIBUTI DI EURI` e
  `FILO APERTO`; restano utili alla continuita' ma sono esclusi dal consolidamento
  fattuale e marcati come tali nel RAG.
- Le lezioni del Loop 2g nate da correzioni usano ora
  `source=reaction`, `memory_kind=reaction_lesson`. Migrazione idempotente applicata
  a 35 nodi storici senza alterarne il contenuto; rimossi anche il vecchio TTL e
  `expires_at` da passive, coerentemente con le reaction lesson native.
- Le vecchie passive da assenso tacito non vengono cancellate, ma il contesto le
  presenta esplicitamente come ipotesi di Euri non confermate da Stefano.

## 2026-07-20 - Baseline interocezione hardware chiusa

- Chiusa la prima osservazione dopo 70,8 ore: 4152 campioni validi, copertura
  97,7%, zero fault; temperature CPU/GPU e RAM ampiamente sane.
- La VRAM della GPU 0 al 95-97% coincide con il normale caricamento dei modelli:
  la soglia osservativa passa dal 92% al 98% per non trasformare il carico sano
  in dolore simulato.
- Nessun riflesso protettivo e nessun consumer LLM attivati: l'interocezione
  resta in osservazione mentre si raccolgono eventuali eventi davvero anomali.

## 2026-07-17 - CHAT non promette lavoro autonomo inesistente

- Chiuso il caso live "Vado a dare un'occhiata al codice": il turno era `CHAT`,
  nessun tool era partito e la promessa restava senza esecuzione.
- Il paraurti atto-parola riconosce ora impegni immediati in prima persona come
  "vado a studiare" e "ora controllo", preservando offerte e futuri condizionati.
- La risposta infondata viene rimossa deterministicamente e sostituita con una
  dichiarazione onesta: CHAT non continua a lavorare in background.

## 2026-07-17 - VisualGate non dipende piu' da `/dev/video0`

- Corretto il mancato avvio della webcam dopo il cambio di enumerazione USB:
  Linux esponeva `GENERAL WEBCAM` su `/dev/video1` e `/dev/video2`, mentre il gate
  apriva rigidamente l'indice 0.
- Il VisualGate scopre ora i nodi V4L2 disponibili in ordine, li prova con il backend
  esplicito e accetta il primo che restituisce davvero un frame. Una webcam che espone
  anche un nodo metadata non viene quindi scelta sul solo `isOpened()`.
- `VISUAL_GATE_CAMERA_DEVICE` permette ancora di forzare indice o path; `None` usa la
  discovery. Verifica host: `/dev/video1`, frame 640x480 acquisito correttamente.

## 2026-07-17 - Tempi interni del Dream osservabili

- Aggiunta instrumentazione read-only per durata totale del ciclo idle e delle fasi
  creative, light e maintenance. Generazione e distillazione della dream trace hanno
  tempi espliciti.
- `evaluate_insights` riporta separatamente tempo e chiamate di premise fidelity,
  bridge validity e giudice di convergenza, distinguendo model call e cache hit.
- Nessun budget, gate, soglia o decisione e' cambiato. Quando creative e light sono
  entrambi dovuti, le due valutazioni restano separate e ora sono visibili nei log;
  l'eventuale deduplicazione va decisa dopo la raccolta Dream Trace sui tempi reali.

## 2026-07-17 - Workflow solo su comando e modalita' epistemica preservata

- Corretto il falso workflow live sul discorso Poseidon: il vecchio `legg\w*`
  interpretava `leggero` come verbo leggere e, insieme a `controllato`, attivava il
  planner, che ha creato una bozza non richiesta. Il gate ora richiede un comando
  esplicito, un artefatto testuale e almeno due capability distinte; conta famiglie
  lessicali finite, non frammenti o flessioni. Regressione sul turno reale inclusa.
- Il planner riceve anche un divieto esplicito: spiegazioni, constatazioni, domande di
  opinione e racconti di azioni passate devono produrre piano vuoto.
- Chiarita la calibrazione del dialogo: il contesto certifica che Stefano ha detto una
  frase, non che una sua stima sia gia' un risultato. Previsioni tecniche conservano
  parole come `stimo`, `probabilmente`, `dovrebbe` e distinguono dati, meccanismo e
  verifica mancante.

## 2026-07-17 - Interocezione hardware osservativa

- Aggiunto un recettore hardware indipendente e privo di LLM: CPU/RAM via `psutil`,
  GPU via `pynvml` quando disponibile con fallback `nvidia-smi`. Letture corrotte o
  sentinel di temperatura vengono escluse.
- La macchina a stati distingue `NORMAL`, `WARNING` e `CRITICAL` con persistenza,
  isteresi, escalation immediata, cooldown, recovery e guasto/ripristino del sensore.
  La VRAM alta e' pressione, non emergenza: non puo' produrre `CRITICAL` da sola.
- Redis conserva lo snapshot effimero `euri:hardware:latest`, lo stato corrente,
  una baseline bounded al minuto per circa 14 giorni e lo stream delle transizioni.
  Solo transizioni, reminder e fault entrano nel Pulse; Initiative le ignora.
- `hardware_monitor.py` viene avviato in background da `start_euri.sh` e usa un lock
  di processo per evitare duplicati. Questa e' Fase 0: nessun riflesso arresta Dream,
  Ollama o altri processi. Roadmap e criteri per la Fase 1 sono nel modulo e nella spec.
- Regressioni pure per persistenza, isteresi, cooldown, escalation, VRAM, parser GPU,
  snapshot/eventi e recovery del provider. Suite unit completa: 29/29 verde.
- Ownership del checkpoint resa esplicita: dopo 72 ore il report read-only
  `scripts/audit_hardware_baseline.py` misura copertura, percentili, picchi, fault,
  eventi e carico rappresentativo. Un promemoria persistente in Euri evita che la
  review dipenda dalla memoria di Stefano.

## 2026-07-17 - Interpretazioni con paternita' e qualita' del ponte osservabile

- Le reflection restano libere di interpretare: nel RAG sono ora presentate come
  `INTERPRETAZIONE DI EURI`, non come fatti attribuiti a Stefano. Il prompt del Loop 2a
  separa il tema osservato dall'ultima frase `Ipotesi di Euri`, vietando di trasformare
  possibilita' in piani o decisioni dell'utente.
- Aggiunta una seconda misura Dream, distinta da `premise_fidelity`: per i soli candidate
  nuovi il modello classifica la terza riga come `supported`, `hypothesis` o `forced` e
  salva nota e punteggio nella convergence trace. La misura e' read-only: non promuove,
  non blocca e non modifica l'esperimento `dream_trace`.
- Il primo parlato senza wake word dopo un riavvio resta correttamente escluso, ma il log
  mostra `nessun turno precedente` invece dei secondi trascorsi dall'epoch.
- Test mirati aggiornati per paternita' delle reflection, parser/misura del ponte e primo
  turno vocale fail-closed.

## 2026-07-15 - Grounding temporale della memoria conversazionale

- Ogni turno nella history porta ora `observed_at`, `conversation_id` e un segmento
  incrementato dopo pause superiori a 30 minuti. Il prompt riceve distanze temporali
  qualitative come metadati interni: sei ore prima non puo' piu' diventare "poco fa",
  ma Euri non recita gli orari se non servono.
- Le memorie distinguono `created_at` (scrittura), `asserted_at` (quando e' stato detto)
  ed eventuale intervallo `event_start`/`event_end` (quando sarebbe avvenuto). Espressioni
  relative e date numeriche vengono risolte rispetto all'affermazione, non al recall.
- Il passive learner produce `semantic_fact` oppure `conversation_anchor`, conserva i
  turni sorgente e registra esplicitamente i dettagli ancora mancanti. Gli anchor entrano
  nel RAG come fili da riprendere, non come prove fattuali, e sono esclusi da Dream,
  Loop 2e, ipotesi trasversali e grounding delle reaction.
- Compressione episodica e Silent Chat propagano la stessa cronologia. Obsidian e Pulse
  ricevono i nuovi metadati; `idx:memories` migra automaticamente i campi temporali.
- Regressione sul caso IZOD: gap 10:56 -> 17:19, riferimento "questa mattina" e
  completamento successivo del filo. Unit 28/28 e integration 3/3 verdi; la sonda Ollama
  classifica il caso come `EPISODIO` senza inventare valori o risultati.
- Test vocale live: il recall ha ripreso correttamente il tema IZOD senza attribuirgli
  valori preesistenti. Le etichette temporali, imitate due volte dal modello, sono ora
  separate dai contenuti dei turni e rimosse deterministicamente dall'output. Il contratto
  collega i valori ellittici al tema immediatamente aperto ma vieta giudizi tecnici senza
  unita', metodo o riferimento.
- Corretto il drop di utterance lunghe: un turno Poseidon di 60 secondi, iniziato dentro
  la lease, veniva valutato dal wake guard solo dopo VAD+STT e quindi scartato a 69 secondi.
  Il consenso viene ora congelato all'inizio fisico del parlato, mentre activity e lease
  si rinnovano soltanto dopo l'accettazione. Il contro-caso ambient fuori finestra resta chiuso.
- Il routing memoria distingue ora il gesto, non il dominio: qualunque "cosa hai in
  memoria?"/"cosa ricordi di X?" e' recall (`SEARCH`); conteggi e stato sono `STATUS`;
  solo richieste esplicite di audit, rumore, duplicati o pulizia avviano manutenzione.
  "Memoria RAM/libera/usata" resta hardware, mentre l'ambiguo "controlla la memoria"
  non puo' avviare da solo una procedura distruttiva.
- L'audit esplicito non esegue piu' una chiamata LLM sincrona per ciascuna memoria passiva:
  seleziona al massimo 40 candidati risk-first, li valuta in quattro batch, dichiara che il
  risultato riguarda un campione ed esce tra i batch quando riceve shutdown. Le ancore
  conversazionali sono escluse dal gesto di pulizia.

## 2026-07-15 - Cognitive Present runtime e Initiative contestuale

- La finestra di follow-up vocale parte ora dalla fine reale del playback, quindi
  una risposta TTS lunga non consuma il tempo concesso all'utente.
- Aggiunto un focus conversazionale breve, composto solo da turni utente accettati:
  durante il focus Initiative puo' entrare soltanto con un candidato classificato
  `EXTENDS`; affinità generiche (`RELATED`) e temi estranei restano pendenti.
- Un token versionato e il segnale VAD/STT in volo rivalidano la proposta subito
  prima del TTS, evitando di parlare sopra un turno appena iniziato.
- Il circuito reaction distingue risposte, chiarimenti e continuazioni fuori tema.
  Le ultime non vengono piu' trasformate in lezioni del sogno; il verdetto
  epistemico viene inoltre propagato alla memoria reaction prima della sintesi.
- Riproduzione locale del caso IZOD, tier unit 27/27 e integration 3/3 verdi.
  Dream Engine invariato.

## 2026-07-14 - Cognitive Present preparatorio (runtime invariato)

- Aggiunto il contratto puro e thread-safe `core/cognitive_present.py`: snapshot
  versionati, provenienza esplicita, TTL sensoriali, lease dalla fine del parlato e
  token per rivalidare decisioni asincrone. Il daemon non lo importa ancora.
- Aggiunto un audit read-only dei log vocali per follow-up persi, collisioni con
  Initiative e confusione fra capability configurata e disponibilita' corrente.
- Rafforzato il replay Active Focus: usa `reaction_raw`, mai la lezione sintetizzata,
  e una reaction non puo' creare un focus. Il nuovo replay e' NO-GO per copertura:
  la rimozione delle sintesi interne espone la mancanza di eventi utente diretti recenti.
- Review incrociata: osservazioni Cognitive Present rese immutabili e monotone anche
  con eventi fuori ordine; aggiunta la chiusura di turni senza TTS. Riclassificato
  `test_insight_repromotion.py` da integration a live: il valutatore scansiona e puo'
  modificare tutti i candidate reali, quindi non e' sicuro durante `dream_trace`.
- Allineato `test_tool_vectorset.py` al contratto gia' documentato del fast path:
  i tre casi con gap top-1/top-2 sotto 0.005 attendono fallback LLM, non un forcing
  vettoriale. La baseline storica 13/16 diventa cosi' 16/16 decisioni corrette.

Storico completo delle versioni di Euri. Il README riporta solo la versione corrente; qui la cronologia integrale.

## Unreleased (13–14/07/2026) — Impegni nel modello memoria + il sogno che si misura

Giornata partita con un blackout (UPS ko, RDB integro) e chiusa con il sistema che misura sé stesso su tre assi: generazione dei sogni, risveglio, uso reale.

- **Retrieval epistemico prima del taglio finale:** KNN domain-boosted, keyword, recency, finestre temporali, wide recall e subject/entity recall condividono ora il rischio di memoria e l'affidabilita' della fonte. I pool principali vengono ampliati e poi riordinati con una penalita' limitata, cosi' la pertinenza resta il segnale dominante ma `provenance_stale`, audit e consolidazioni fragili non monopolizzano i primi slot. `correction_pending` e `superseded_by` sono esclusi da ogni percorso che costruisce contesto; il touch avviene solo sui risultati finali. Regressioni pure in `test_epistemic_ranking.py`.
- **Outbox durevole sul save memoria:** il RedisJSON canonico e un record outbox vengono creati nello stesso script Lua, sia sul path normale sia su quello idempotente. TTL, indice Loop 2e, Pulse e Obsidian sono consumer replayabili; Pulse usa un marker atomico per non duplicare l'evento dopo crash, Obsidian sovrascrive un path deterministico e l'indice espone una modalita' strict. Il save tenta subito il fast path, mentre un worker recupera gli eventi pendenti con backoff. Regressioni in `test_memory_outbox.py`; entrambi gli script di commit verificati su Redis Stack reale con chiavi temporanee eliminate.
- **Supervisione e shutdown dei loop:** reminder, passive learner, consolidamento, mobile, Initiative e outbox passano da un `WorkerSupervisor` nominato, con stato/heartbeat, restart su uscita inattesa e backoff. Le attese usano uno stop event condiviso; shutdown vocale, SIGINT/SIGTERM ed eccezioni del main eseguono teardown idempotente e join con deadline. Anche Dream Engine usa un event interrompibile e `stop()` attende il thread; il watcher Obsidian ha un join limitato. Regressioni pure in `test_worker_supervisor.py` e stop del Dream coperto in `test_dream_schedule.py`.
- **Test separati per rischio operativo + CI non distruttiva:** tutti i `test_*.py` sono classificati una sola volta nei manifest `unit`, `integration` e `live`; un checker fallisce su test mancanti, duplicati o inesistenti. Il runner esegue ogni script in un processo separato con timeout e riepilogo. GitHub Actions installa le dipendenze e lancia soltanto `unit`; Redis reale, Ollama, hardware e cicli cognitivi restano fuori dalla CI. Baseline locale: 23/23 unit passati in 16.1s.
- **Tre invarianti strutturali ripristinati:** (1) la provenienza `trusted` appartiene ora al singolo `Brain.respond(..., trusted=...)`, non a un flag globale che mobile o handler intermedi potevano consumare; (2) il passive learner legge un journal con sequence ID e ack indipendente dalla history comprimibile, quindi il taglio episodico dei primi 20 messaggi non invalida più il cursore; (3) documento RedisJSON e mapping idempotente vengono committati insieme via Lua, con recupero dei mapping stale e nessun winner fantasma se costruzione o `JSON.SET` falliscono. Regressioni dedicate in `test_history_provenance.py` e `test_memory_idempotency.py`; script Lua verificato anche su Redis Stack reale.
- **Chiusura hardening CodeRunner + consenso vocale:** il codice generato riceve ora un ambiente a allowlist, ripulito anche via `bwrap --clearenv`; un preflight cached distingue `bwrap` assente da installato-ma-inutilizzabile e degrada senza rendere indisponibili tutti i job. Timeout e interrupt terminano il process group con fallback `SIGKILL`. Il passive learner separa i batch trusted/ambient (fallback conservativo DEBOLE sui misti piccoli), mentre vuoto, garbage STT e voce fuori-finestra non rinnovano più activity/presenza autenticata. I test coprono env leak, preflight negativo, timeout/interrupt/kill, segmento misto e activity timing.
- **Impegni assorbiti nel modello memoria (il silo todo non esiste più):** un impegno = memoria di prima classe con `due_at` + `status` pending/done, salvata dall'hardened path completo (guard, axes, embedding, pulse, vault). `idx:memories` guadagna il campo `status` (migrazione automatica); tutte le query impegni ripuntate; `euri:todo:*` + `idx:todos` smontati (migrazione una-tantum `scripts/migrate_todos_to_memory.py`). **Visibilità**: il riepilogo NOMINA gli scaduti (prima li contava: "1 cosa scaduta" irrispondibile per costruzione); `build_rag_context` inietta la sezione deterministica "Impegni aperti" con TUTTI i pending — il retrieval semantico perdeva il nodo-impegno nella competizione coi vicini e Euri negava scadenze esistenti. Validato a voce ("che impegni ho?" risponde col contenuto; Euri offre da sola lo spostamento quando la conversazione lo implica).
- **Gesto sposta-impegno (Intent RESCHEDULE):** chiude il buco parola-senza-azione osservato dal vivo ("fallo adesso" → "Impegno aggiornato" con due_at immutato). Regex strutturali + targeting keyword-OR (spogliato di verbi del gesto e parole temporali), data dal testo utente con fallback dal claim, pending "a quando?" annullabile; lo spostamento riarma consegna e clock afferente. **Implicit action** sui claim "impegno aggiornato/spostato" della CHAT → l'azione avviene davvero. Validato a voce al primo colpo (regex 1ms, "lunedì mattina verso le 9" → data giusta, conferma col dato reale).
- **Esperimento dream_trace (continuità 2b), pre-registrato e in raccolta:** residuo di ESPLORAZIONE tra cicli creativi — a livello di STRATEGIA ("che tipo di ponte ho provato e perché debole"), non di coppia (145 domini × pairing random: la coppia non si ripete). Dietro flag, bit-identico da spento. I primi 2 residui reali hanno mostrato l'eco a punto fisso (etichette d'esempio pappagallate, traccia che rientrava dal CoT) → distillazione rifatta senza esempi + IGNORA esplicita + "NIENTE DA SEGNALARE"; contenuto del residuo nel log (unica storia possibile). Misura sui CANDIDATE via `trace_injected` nella convergence trace (la promozione è quasi cieca al contenuto e interagirebbe con l'intervento); pre-registrazione onesta in `ESPERIMENTO_DREAM_TRACE.md` (n=60/braccio, ≥15pp, audit CIECO con `sample_dream_audit.py`); baseline = i 148 candidate già in trace.
- **Sonda stato vettoriale: NO-GO misurato** (`SONDA_STATO_VETTORIALE.md` + `probe_vector_state.py`): dalla proposta "cognitive_state" (vettore persistente 512d), prima di costruire si è misurato su 1407 eventi reali — cos(S, centroide)=0.9885, il one-hot simbolico con la stessa dinamica eguaglia/supera ogni variante embedding, nessuna batte le baseline banali. Il vettore runtime non si costruisce. **La strada superstite è l'Active Focus** (`SPEC_ACTIVE_FOCUS.md`): working set simbolico ≤7 voci con provenienza nominabile e decadimento lazy; replay harness offline sulla storia reale (`replay_focus.py`) → audit umano GO 7/7 (con la firma del focus IMMUTABILE al seme: accrescerla creava l'acchiappa-tutto). Runtime da progettare post-raccolta.
- **Risveglio lucido — fase misura (`premise_fidelity`):** per ogni candidate, il modello del sogno confronta le due premesse "Nel dominio X succede" con le memorie `source_memory_ids` vere: il sogno ha detto la verità sulle proprie fonti? SI/PARZIALE/NO per lato → min + nota, una volta per candidate (budget 5/ciclo leggero), None=non-verificabile per i pre-provenienza. ADDITIVA: nessuna promozione cambiata; il punteggio viaggia nella trace per la correlazione coi verdetti `external_reaction` di Stefano. Diventa gate SOLO se separa i suoi SÌ dai NO. Motivo misurato: il giudizio testo-alla-cieca non discrimina (il giudice zona-grigia sovra-accetta; al probe di giugno pure Gemma metteva la scoria fluente in cima) — l'asse che separa è il grounding, e da fine giugno la provenienza per verificarlo esiste.
- **Analisi convergence trace (348 entry, 04–13/07):** distribuzione delle similarità claim PIATTA — max-per-candidato ≈ μ delle coppie casuali (0.8218 vs 0.8216), 1/146 sopra la soglia relativa, esiti del gate indistinguibili a livello claim → il timbro anisotropo confermato sui dati vivi; non è un problema di taratura, è il substrato. Promozioni ~6/giorno costanti in idle; `denied_repromotion` 140 grezzi su 37 seed.

## Unreleased — Loop 2e attention ZSET: salienza separata dal payload

Esperimento mirato ispirato al principio "indice leggero ordinato, payload completo solo dopo la selezione". Non sostituisce RediSearch/KNN e non tocca il retrieval conversazionale: applicato solo al pre-filtro candidati del Loop 2e.

- **Baseline prima del fix:** 1299 memorie, 30.90 MB di RedisJSON complessivo, ma solo 0.46 MB di testo (`content`). Il Loop 2e selezionava 241 candidati scansionando e idratando tutti i JSON (`SCAN + JSON.GET`), con costo misurato ~1877 ms per la sola selezione candidati.
- **Patch minima:** nuovo indice derivato `euri:idx:loop2e:candidates` (`ZSET`) mantenuto su `save_memory`, `touch` e sulle mutazioni che rendono un nodo non consolidabile (`requires_verification`, `audit_flag`, `superseded_by`, `consolidated_into`). Lo score combina `recalled_count` cappato, `last_recalled_at` e tie-break deterministico. Il documento JSON resta la fonte di verità: il Loop 2e rilegge e ri-valida ogni ID prima di usarlo; se lo ZSET manca o è vuoto, fallback allo scan canonico.
- **Numeri dopo il fix:** `scripts/bench_loop2e_attention.py --rebuild --assert-equal --runs 5` → scan_count=241, zset_count=241, same_set=True, same_order=True. Selezione da ZSET + idratazione dei candidati: ~331 ms medi. Rebuild dello ZSET da scan: ~1739 ms, quindi il rebuild non è l'ottimizzazione; il valore sta nel mantenimento incrementale.
- **Contro-casi:** `test_loop2e_attention.py` copre ingresso candidato valido, uscita su `requires_verification`, uscita su `consolidated_into`/`superseded_by`, esclusione di frammenti acefali o sotto soglia di richiamo, rimozione idempotente.
- **Rischio accettato:** indice derivato potenzialmente stale. Mitigazione: revalidazione JSON a ogni lettura, rimozione opportunistica degli ID stale e fallback allo scan. Nessuna cancellazione, nessun cambio di KNN, nessun macronodo/checksum.
- **Stabilità del prompt-gate 2e:** lo stesso ciclo live ha mostrato molti `output non parsabile → fail-closed seed-only`: sicuro per i dati, ma spreca il modello. Il gate SAME/DIFFERENT/UNKNOWN ora usa `format="json"` e `think=False` solo per questa micro-classificazione; dopo 3 parse-fail consecutivi il Loop 2e interrompe il consolidamento del ciclo. Un run forzato post-fix ha confermato che il parser regge e ha prodotto 2 consolidazioni, ma il gate può restare troppo selettivo; aggiunto cap a 30 tentativi di gate per ciclo, così lo ZSET propone i candidati migliori e il loop non consuma modello su tutta la coda. Tutti gli altri loop Dream e tool restano invariati.
- **Loop 2g subject-targeted:** la correzione notturna non si limita più ai soli `rag_ctx_ids`. Se il testo nomina un soggetto, il loop costruisce i target a partire dai termini della correzione e premia il nodo che condivide davvero quei dettagli, così il caso "Giada" colpisce il consolidato buggato invece di flaggare solo il contesto accidentale del turno. Fallback invariato sul contesto se non emergono target chiari.
- **Retrieval risk-aware:** i percorsi aperti `subject_recall`, `entity_recall` e `wide_recall` ora demuovono memorie con `provenance_stale`, `audit_flag`, `requires_verification` o `consolidation_risk` e le marcano nel prompt come `[DATO DA VERIFICARE: ...]`. Il path semantico domain-boosted idrata il JSON completo, così i flag epistemici non si perdono prima di `rag_context`. Su `bad_memory`, Loop 2g marca anche `requires_verification=True` oltre ad `audit_flag`.
- **Passive learner con supporto epistemico:** l'estrazione passiva distingue `FORTE` (detto/confermato/ripreso da Stefano) e `DEBOLE` (detto da Euri e non contestato: consenso tacito). Le memorie deboli vengono salvate ma marcate `requires_verification=True` e rimosse dall'indice Loop 2e, così aiutano il recall senza diventare subito materiale di consolidamento forte. Le aggiunte al profilo devono restare additive ("Stefano si occupa anche di..."), non definizioni esaustive.
- **Loop 2f source-aware:** le note `[confronto]` restano metaconoscenza richiamabile, ma non alimentano altri confronti; i consolidati con `consolidation_risk.level=high` sono esclusi come parent del 2f. Quando una fonte richiede verifica, il prompt vieta formulazioni assertive ("validato", "definitivo", "pronto all'uso") se non presenti nei dati. Contro-caso: `test_giada_consolidation.py` verifica che i `watch` restino ammissibili, mentre confronti e high-risk no.
- **Briefing sogni: feedback ≠ nuova richiesta:** frasi come "tienilo come analogia o sogno, non come fatto operativo" contengono la parola "sogno" ma sono feedback epistemico, non richieste di aprire un nuovo briefing. Aggiunto un pre-gate deterministico condiviso voce/Silent Chat prima del classifier LLM.
- **Initiative controller (Pulse → domanda):** primo consumer parlante del bus `euri:pulse`, limitato agli `insight/promoted`. Il daemon idrata sempre il JSON corrente collegato all'evento prima di decidere (il payload Pulse può essere stale), valuta tensione, applica guardrail di presenza/idle/cooldown e chiede al modello di formulare la domanda in JSON; non esiste fallback a template. Se Stefano è assente o c'è cooldown, il candidato resta in una coda pending breve invece di parlare alla stanza vuota. La risposta rientra nel circuito esistente `capture_reaction`, quindi il sogno diventa lezione solo dopo feedback esterno. Contro-caso: `test_initiative.py` verifica idratazione da insight id, skip senza id e payload stale che non batte il JSON corrente.
- **Initiative su memorie passive incerte:** estensione stretta del controller: `memory/saved` può diventare domanda solo se il JSON corrente è `source=passive` e segnala `requires_verification`, `passive_support=tacit_acceptance` o soggetto acefalo. Le memorie esplicite `user/teach` non parlano anche se numeriche. I `memory/saved` passano prima da una coda di stabilizzazione breve, perché il passive learner può alzare i flag subito dopo il Pulse. La domanda resta generata dal prompt e il modello può rifiutare (`should_ask=false`) se il fatto è banale o non vale interrompere. Contro-casi aggiornati in `test_initiative.py`.
- **Loop 2i — ipotesi trasversali da episodi ripetuti:** il Dream Engine ora può cercare pattern causa_sospetta→effetto che ricorrono in almeno due memorie operative distinte. Se il modello trova un pattern non banale, crea un insight già `promoted` ma sempre `requires_verification=True` + `verification_status=hypothesis_to_test`, con `source_memory_ids` dei casi usati e Pulse `insight/promoted`: Euri lo porterà come domanda, non come fatto. Nessun dominio hardcoded: il pre-filtro guarda solo forma linguistica causa/effetto e il prompt vieta generalizzazioni assertive. Contro-caso: `test_cross_episode_hypothesis.py` verifica che l'output resti operativo e cautamente formulato come ipotesi.
- **Loop 2i — indipendenza delle fonti:** primo giro live: il 2i ha promosso correttamente un'ipotesi cauta, ma contando come due casi una memoria `user` e una lesson `passive/from_correction` derivata dallo stesso episodio. Fix: le memorie derivate/sintetiche (`lesson/from_correction`, `reflection`, `reaction`, `insight`, `consolidated_from`, `source_memory_ids`) non contano più come episodi indipendenti per fondare ipotesi trasversali. Contro-caso aggiunto in `test_cross_episode_hypothesis.py`.
- **Dream Engine non più "notturno": cadenze separate in idle.** Il loop background ora controlla l'idle ogni `DREAM_ENGINE_POLL_SECONDS` e lancia solo i sotto-cicli dovuti: leggero (`DREAM_LIGHT_CYCLE_INTERVAL_S`, default 20 min: insight eval, correzioni 2g, ipotesi 2i, provenienza), creativo (`DREAM_CREATIVE_CYCLE_INTERVAL_S`, default 90 min: sogno 2b + promozione 2c), manutentivo (`DREAM_MAINTENANCE_CYCLE_INTERVAL_S`, default 24h: 2f/2h/cleanup/pruning/2e). `force_full_cycle.py` resta compatibile e chiama il ciclo completo. Contro-caso: `test_dream_schedule.py` verifica che i cicli scadano indipendentemente.
- **SAVE_MEMORY non fonde più sopra basi deboli:** il merge costruttivo del save esplicito resta attivo per memorie `user/teach`, ma non usa più come ingrediente un vicino passivo/sintetico, con `passive_support`, soggetto acefalo, `provenance_stale`, `audit_flag` o `consolidation_risk`. In quei casi il fatto esplicito viene salvato separato e il nodo debole viene soft-supersedato. Caso vivo: una memoria passiva sulla rugosità da nastro adesivizzato ha reintrodotto "impostazioni macchina non corrette" dentro un save utente; il contro-caso `test_save_service_merge_guard.py` verifica che il merge LLM non venga chiamato su basi epistemicamente deboli, ma resti disponibile sui save utente fidati.
- **Correzioni nello stesso contesto → quarantena immediata:** quando un correction signal forte ("era una provocazione", "non ho davvero...", "ti correggo", ecc.) ha overlap chiaro con un nodo del `last_rag_ctx`, quel nodo viene marcato subito `correction_pending=True` + `requires_verification=True` e rimosso dal candidato Loop 2e. Non è un giudizio definitivo: il Loop 2g resta l'unico a decidere `bad_memory/bad_reasoning/not_a_correction`; quando lo analizza chiude la quarantena e ripristina `requires_verification` se era falso positivo o solo errore di ragionamento. `correction_pending` è escluso anche dal gate canonico Loop 2e, non solo dallo ZSET; nel settle si chiude prima `correction_pending` e solo dopo si abbassa eventualmente `requires_verification`, così un crash intermedio resta conservativo. Integrazione: Loop 2f salta memorie pending come seed/neighbor, SAVE_MEMORY le considera basi deboli e non ci fonde sopra, Initiative non le porta come domanda proattiva. Contro-casi: `test_correction_quarantine.py` verifica bersaglio singolo, pari-score, memoria già `requires_verification=True`, `audit_flag`, idempotenza, crash parziale, `bad_reasoning`, `bad_memory/ambiguous`, reindex Loop 2e e skip Loop 2f; `test_loop2e_attention.py` verifica l'esclusione dal gate Loop 2e; `test_save_service_merge_guard.py` e `test_initiative.py` coprono SAVE/Initiative.
- **Raffinamento live correction/joke:** test vocale post-riavvio: "stavo scherzando / ti prendevo in giro" sul caso fragole-cipolla non veniva catturato dal detector, perché quei marker erano solo nel sotto-gate di quarantena ma non nella cattura del correction signal. Aggiunti pattern pragmatici agnostici (`stavo scherzando`, `era uno scherzo/una provocazione`, `ti prendevo in giro`, `non ho/avevo davvero`) al detector; il suffix RAG per `correction_pending` ora dice esplicitamente "contestato nel contesto, correzione in sospeso" invece di un generico "da verificare".
- **Loop 2g: scherzo/provocazione come rettifica epistemica:** un signal già quarantinato può essere una correzione del fatto memorizzato anche se non è una correzione del ragionamento di Euri. Il prompt del giudice 2g ora distingue "scherzo generico" da "quel fatto era uno scherzo/provocazione/non vero"; inoltre, se una quarantena deterministica nasce da una rettifica esplicita, un eventuale verdict `not_a_correction` non ripristina più `requires_verification=false`. Fail-mode: il nodo resta prudente.
- **Reaction verdict a 4 stati:** la risposta a un insight non è più forzata in `CONFERMA|SMENTITA|PARZIALE`. Nuovo verdetto `DA_VALUTARE` per frasi tipo "idea interessante da provare, ma non confermata": l'insight resta promosso ma viene marcato `requires_verification=True` + `verification_status=hypothesis_to_test`, e nel RAG entra come `[IPOTESI DA VERIFICARE]`. Anche il fail-open del classificatore diventa `DA_VALUTARE`, non `CONFERMA`, così un errore del modello non trasforma un'ipotesi in fatto. Contro-caso: `test_reaction_verdict.py`.

## V2.20 (12/06/2026) — Onestà di ground-truth (P-GT)

Minor release tematica: la trilogia **P-GT** ("il cognome di famiglia" della roadmap — l'output di Euri risponde a un fatto verificabile, non alla propria narrazione). Tre facce della stessa onestà, ciascuna con baseline misurato e contro-caso: **N1** non genera lesson dalle correzioni-fantasma, **N2** non afferma un'azione che non ha eseguito, **N3** non lascia che un atomo fattuale provato venga cancellato da una nota infedele. Prevenzione + cura in tutte e tre (gate/paraurti per il futuro, bonifica/riparazione del pregresso). Richiede un restart del daemon per attivare i gate notturni.

### V2.20 (continua, 13/06/2026) — Fix: àncora temporale in italiano (niente più giorno sbagliato)

Osservato dal vivo in Silent Chat: Euri ha detto "è venerdì sera" di sabato. La data **era** iniettata nel contesto (`core/brain.py`, "Data e ora corrente: …"), ma resa via `strftime('%A %B')` → in inglese ("Saturday 13 June"), perché il locale di sistema è `C/POSIX`. Un modello che risponde in italiano ignora l'àncora in lingua straniera e confabula un cliché ("venerdì sera, ora di staccare") invece di leggere il dato. Fix: nuova `format_datetime_full` in `utils/date_utils.py` che rende data + giorno della settimana **esplicitamente in italiano** (array `_GIORNI`/`_MESI`, indipendente dal locale), cablata in `brain.py`. Tocca il punto condiviso → vale per voce e chat. Caveat onesto: riduce, non azzera — la metà *calibrazione* ("se non sei sicura del giorno, dillo") resta il problema di natura-LLM (suggestionabilità), non prompt-tunabile senza overfit; questo toglie la metà evitabile (àncora nella lingua giusta). Nota di localizzazione per altre lingue aggiunta al README.

### V2.20 (continua, 13/06/2026) — Fix collaterale: dedup todo silenziosamente rotto

Bug scollegato dal tema P-GT, trovato e chiuso a parte. `is_duplicate_todo` filtra i todo creati oggi via `@created_at:[start end]`, ma lo schema `idx:todos` non indicizzava `created_at` → la query falliva (SEARCH_SYNTAX: Unknown field), l'`except` in `_query_todos` la ingoiava e ritornava `[]` → il dedup dei todo **non scattava mai** (doppioni ammessi in silenzio). Aggiunto `NumericField("$.created_at", ...)` allo schema, coerente con `idx:memories`/`idx:notes` (commit `6cd0982`). Indice live già migrato (DROPINDEX + ricreazione, documenti preservati); il fix nel codice lo rende permanente per future ricreazioni. Verificato: la query `@created_at` gira, `is_duplicate_todo` non solleva più. (Nota di metodo: i todo restano un silo legacy fuori dal "tutto è memoria viva" — da ripensare più avanti, assorbendoli nel modello a nodi con `due_at`+stato.)

### V2.20 — N3 · Paraurti di richiamo nel Loop 2f: l'atomo molto richiamato non si auto-cancella

Problema (principio P-PIANO). Il soft-delete notturno del Loop 2f ragiona per cosine similarity, non per fedeltà del fatto: su una "contraddizione" tiene la più recente e soft-deleta l'altra. A volte la vincitrice è solo *affine*, non *fedele* — assorbe un atomo fattuale dentro una nota più lunga che NON ne riporta il valore. Casi provati: "Lotto 03 PR043: MFI 11,2" (recalled 12) soppiantato da una nota logistica su un altro campione (MFI 25); la struttura operativa di Lucy Plast (recalled 7) soppiantata da una riga sulla capacità di una macchina.

- **Baseline oggettivo, segmentato per meccanismo** (commit `6e5f7f8`, `4444037`, `diag_plane_fusion.py`, read-only). Su 1022 memorie, 91 soft-deletate. La prima probe LLM "è una fusione?" dava **78% fusioni indebite** = strumento rotto (leading, 0 contraddizioni vere su 91 — non credibile). Rifatto con segnali oggettivi (cosine + lunghezza): **41 refl-dedup** (latest-wins voluto `4bcb6cc`, fuori scopo), **19 lapidi N1** intenzionali (`phantom_correction_n1` — le "20 orfane" del primo conteggio erano 19 tombstone + **1 sola** orfana vera, benigna), **30 bersaglio** (di cui 18 col segnale assorbimento-blob). Scoperta che ribalta l'approccio: il caso canonico ha **cosine 0.972** → la fusione indebita *è* un quasi-doppione, la distanza NON la separa da un dedup legittimo.
- **Falso percorso: il giudice di fedeltà LLM** (commit `f916e98`, flag `--judge`). Probe di *contenimento* (PRESERVA/CONTRADDICE/PERDE): il fatto del perdente sopravvive nel vincitore? Su 30 → 18/2/10. Ma l'audit manuale ha sgonfiato anche il 10 (≥3 falsi: un reword a dist 0.016, un "6→4" col core preservato, una correzione scambiata per perdita) → **~2-3 danni duri reali**. **Scartato dal proprio contro-caso:** sui 2 danni duri il giudice ha detto `fedele=True` **2/2** (la sovrapposizione di token lo inganna — "lotto PR043" è presente ma il VALORE MFI no), e i verdetti flip-floppano col prompt. Stessa lezione del plausibility_gate: il modello non è affidabile sulle distinzioni fini, e non si prompt-tuna su pochi esempi (overfit a Lucy Plast). Codice della probe **rimosso**, niente dead code.
- **Perché deterministico — scelta finale** (commit `c341ae3`). Nessun segnale economico (cosine / lunghezza / asimmetria-richiamo / giudizio LLM) separa l'assorbimento **dannoso** dal **legittimo**: sono strutturalmente identici (alta similarità, vincitore più lungo). Il segnale robusto era quello giusto fin dall'inizio (intuizione di Stefano): **molto richiamato → fermati**. Regola: un atomo con `recalled_count >= LOOP2F_RECALL_GUARD` (=5) NON viene auto-cancellato via contraddizione → **tieni entrambi**. Una riga, niente LLM, fail-safe = tieni entrambi; il consolidamento sui poco-richiamati resta invariato.
- **Contro-caso passato** (`test_plane_guard.py`, dati reali). Garanzie **4/4**: ferma 2/2 i danni duri (MFI recalled 12, struttura recalled 7), lascia risolvere la contraddizione vera a basso-richiamo (recalled 0) e l'arricchimento legittimo sotto soglia (recalled 4).
- **Costo accettato.** Su un arricchimento/quasi-dup legittimo **molto** richiamato (recalled ≥5) la regola tiene **entrambe** le note → possibile **clutter** (near-dup non consolidati) sulle memorie popolari; e un valore vecchio molto-usato sopravvive finché una correzione esplicita non lo soppianta. È il fail-safe scelto: meglio due note che un atomo perso.
- **Cura del pregresso** (commit `d4656c4`, `repair_n3_atoms.py`). I 2 atomi già danneggiati ripristinati togliendo `superseded_by` (reversibile, niente delete). Verifica before/after con `search_memories(touch=False)`: **assenti prima, ricomparsi dopo** (MFI ora 2° in classifica, convive col suo assorbitore), `recalled_count` intatti. Prevenzione (paraurti) + cura (riparazione), come in N1.
- **Significato.** Il consolidamento autonomo ha un bias entropico verso la fusione: ottimo contro la cascata di reflection, erosivo contro gli atomi fattuali deliberati. Il paraurti non insegna al loop a *giudicare* la fedeltà (non sa farlo) — gli impedisce di **scommettere** sugli atomi più provati dall'uso. **Richiede restart daemon** per attivarsi.

### V2.20 — N2 · Atto-parola: niente "ho salvato" se nessuno ha salvato

Problema (P-GT applicato al linguaggio). In un turno CHAT — dove nessun handler agisce — l'LLM a volte rivendica un'azione mai compiuta ("ho aggiornato la nota", "l'ho eliminato"): confabulazione di *agency*. Il pavimento di onestà esistente (`honesty.scrub_unbacked_save_claim`) copriva il solo **salvataggio**; restava scoperto ogni altro verbo d'azione (creato / eliminato / aggiornato la *nota* / completato).

- **Rilevatore come funzione pura** (commit `523aaac`, `79566ab`, `core/act_word_check.py`). `claims_completed_action` riconosce il claim in 1ª persona passata; robusto a **negazione** ("non ho salvato"), **passato-distante** ("l'ho salvata ieri" = racconto, non claim sul turno) e **avverbi intermedi** ("ho appena salvato" = questo turno, scatta ancora). Test 28/28.
- **Cablato nel pavimento di onestà** (commit `17d5eea`). `scrub_unbacked_action_claim` — fratello largo di `scrub_unbacked_save_claim` — applicato a valle nei 3 punti che NON agiscono (CHAT/SEARCH/mobile): droppa la frase del claim infondato e aggiunge una correzione onesta (`turn_actions` vuoto). Stessa forma del pavimento esistente, niente duplicazione né secondo modello.
- **Limite noto.** "Sì, l'ho fatto" su un'azione vera di un turno passato, **senza marcatore temporale**, è un falso positivo (servirebbe stato conversazionale) — fuori dal raggio del rilevatore attuale, da tenere d'occhio nella validazione vocale.
- **Significato.** P-GT sul linguaggio: Euri non afferma un'azione che non ha eseguito. La parte difficile non è correggere ma **rilevare il claim** — e scatta solo sul disallineamento claim + zero-azione-nel-turno.

### V2.20 — N1 · Correzioni-fantasma: il gate "è una correzione?" in Loop 2g

Problema misurato: la cattura `detect_correction` è larga e intercetta come "correzione" anche domande, elaborazioni, accordi, pensieri ad alta voce. Il classify notturno di Loop 2g NON filtrava: presupponeva che il signal *fosse* una correzione e ne classificava solo il tipo → generava **lesson spurie** che competono nel richiamo e scavalcano le note vere (caso reale 11/06: su "cosa pensi di chi rovina il materiale" le lesson-fantasma stavano sopra le note Eurostampi). Rumore diventato **generativo**.

- **Baseline misurabile** (commit `fa171cc`, `diag_phantom_corrections.py`, read-only). Un giudice LLM fa la domanda che il sistema non fa — "è davvero una correzione di un errore di Euri?" — su tutti i 47 signal: **≥53% fantasmi** (25/47), ed è un pavimento (il giudice ha falsi negativi). Finding che decide la direzione: i 25 fantasmi sono **sparsi su tutti i verdict** (solo 6 in `ambiguous`, 19 in `bad_memory`/`bad_reasoning`) → filtrare sul verdict esistente ne prenderebbe il 24%. Il classify non chiede mai *se* è una correzione, solo *che tipo*.
- **Fix — gate a 4 vie nella fase ACT notturna** (commit `fc05890`). `_llm_classify_correction` ora stabilisce PRIMA se è una correzione: nuovo verdetto **`not_a_correction`** (domanda/elaborazione/accordo/pensiero) → **niente lesson**, status `dismissed` (soft-delete del signal, audit preservato). Bias **conservativo**: nel dubbio tiene la correzione (il contro-caso è sacro). Cattura `detect_correction` invariata: si interviene in un punto solo, di notte → zero hot-path, zero regex tunata a mano (le alternative scartate). Validato sul metodo reale (Qwen, `diag_n1_validate.py`): **correzioni vere perse 0/11, fantasmi beccati 10/11** (l'unico miss è una domanda-richiamo borderline → lato sicuro).
- **Bonifica del pregresso.** 19 lesson-fantasma già generate **soft-deletate** (`superseded_by="phantom_correction_n1"`, reversibile, mai cancellate). Selezione con **ensemble a due giudici** (Gemma baseline + Qwen del fix): 14 in accordo + 5 borderline; soft-delete di tutti e 19 (le correzioni sono ri-rifacibili). **Verifica:** i fantasmi non trapelano più e la nota vera `c27d668e` è ricomparsa nei top-4 sulla query dove prima venivano scavalcate.
- **Prevenzione + cura:** il gate ferma la pollution futura, la bonifica pulisce quella esistente. I 21 audit_flag da fantasma (segnale debole) lasciati intatti.

### V2.19 (continua, 11/06/2026) — Contesto RAG: la rilevanza vince sulla recency + recalled_count solo meritato

Caso reale (conversazione tecnica Eurostampi, estrusione/perossido): il contesto RAG su query tecniche era **~72% off-topic** — `_build_context` per le query non-temporali partiva dalle 5 memorie più **recenti** (spesso output off-topic di un ciclo Dream appena girato: informatica, IA, logistica, automazione) e lasciava **~1 solo slot** alla rilevanza semantica. La recency annegava la pertinenza.

- **Metodo (come la Fase −1): prima il numero, poi il codice.** `diag_rag_context.py` (read-only, `touch=False`) replica la selezione di `_build_context` leggendo le **stesse costanti** che usa il codice reale (test e fix non possono divergere) e conta slot ON/OFF-topic + provenienza (recency/semantic/temporal). Baseline e misure in `rag_context_audit.md`.
- **Ribilanciamento slot** (commit `0698a48`). Budget reso configurabile: `RAG_RECENCY_LIMIT` 5→2 (poca recency "ambient" per la continuità), `RAG_SEMANTIC_LIMIT` 3→5 (il resto alla rilevanza), cap invariato 6. Misurato **back-to-back sullo stesso stato Redis** (annulla il drift): **PRIMA ON=4/OFF=14 → DOPO ON=8/OFF=6** (ON×2, OFF −57%). Revertibile in una riga.
- **Contro-caso temporale verificato.** Su "di cosa abbiamo parlato oggi?" (finestra popolata) i **9 slot di testa sono identici** prima/dopo, +1 nodo semantico in coda: `prioritize_window` (il diario vissuto) è a monte e non viene toccato. Le query con riferimento di tempo restano immutate.
- **`recalled_count` solo sui richiami MERITATI, non iniettati** (commit **`a73d728`** — **CUTOVER per la validazione touch/lifecycle di metà giugno**). La base recency e le reflection "Sintesi recenti" passano a `touch=False`: l'iniezione per pura recenza non incrementa più `recalled_count`. Solo i match semantici e la finestra temporale query-driven (richiami *meritati*) lo incrementano. **I contatori PRIMA di `a73d728` sono gonfiati dall'iniezione di recency e NON confrontabili con quelli dopo** — la validazione del lifecycle deve trattare `a73d728` come discontinuità. Verificato non-distruttivo: base recency `recalled_count` 0→0.
- **Validato dal vivo** (conversazione bins/Eurostampi, 11/06): contesto on-topic fino a 5/6 nodi `chimica polimeri`, e su una domanda etica la nota comportamentale dedicata è uscita **top-1**. Mergiato su `main`.

### V2.19 (continua, 11/06/2026) — AdaptiveClassifier V2: plasticità ancorata — Fase −1 (harvest etichette)

La ricostruzione del classificatore intent riparte come **dimostratore di ricerca**, non come ottimizzazione di latenza: con la GPU in arrivo l'incentivo economico (saltare il fallback LLM) si assottiglia, mentre il valore — un componente che si aggiorna dall'esperienza **senza corrompersi**, con omeostasi notturna misurabile — è ortogonale all'hardware e regge nella serie di paper. Revisione sul codice reale *prima* di scrivere codice: la diagnosi di morte del V1 era in parte sbagliata.

- **Diagnosi corretta.** Smentita la causa "feedback loop self-training" attribuita al V1: `classify()` non chiama mai `update()` (invocato solo a valle del fallback LLM in `core/llm_classifier.py`) — il V1 **già** rispettava "mai imparare dai propri output". Le cause reali della sospensione (`config.py:58`) erano latenza e5-large + falsi positivi. La causa sottile, identificata in review, è il **selection bias**: il layer impara solo dalle utterance che non sa già classificare → man mano che diventa sicuro smette di ricevere esempi facili e i centroidi derivano verso la coda ambigua. Inoltre i correction signal del Loop 2g **non contengono informazione di intent** (verdetti `bad_memory`/`bad_reasoning`, mai il routing) → usabili solo come **veto**, non come secondo maestro.
- **Design V2 (spec).** Per ogni classe modellata: *anchor* congelato (golden set, mai aggiornato dall'esperienza) + *delta* Welford vivo, blend `(1-w)·anchor + w·delta`, con guinzaglio `cosine_distance(delta, anchor) > LEASH_MAX` → update rifiutato e loggato. CHAT = classe **abstain** (nessun centroide: il dominio aperto non ha centro). **Margin gating** sul fast path; **omeostasi notturna** nel Dream Engine (canary set etichettato a mano + rollback via `update_log` se l'accuracy cala). Embedding statico (model2vec, ~256–512 dim, sub-ms su CPU) dedicato all'intent — la **Fase 0** è un gate usa-e-getta che ferma tutto se la separabilità è inadeguata. Golden set costruito dal **residuo regex-CHAT** (la distribuzione reale su cui il fast path opera), non dai pattern regex (che produrrebbero un train/serve mismatch).
- **Fase −1 — harvest persistente delle etichette** (commit `4491e98`). Problema verificato sul codice: il dataset su cui la Fase 0 contava non esisteva (log con retention 7gg, `voice_daemon.py:95` → ~19 etichette sopravvissute; la Silent Chat non etichetta, usa solo il regex router). Fix: ogni coppia (utterance, label) prodotta dal maestro LLM viene scritta sullo stream `euri:aclf:harvest` su **tutti e tre i rami** — le 6 azioni, il **CHAT/abstain** (serve a calibrare `SCORE_MIN`/`MARGIN_MIN` e a popolare CHAT nel canary), e gli **hard-negative del guard manifatturiero** (EXECUTE vetato → NON è EXECUTE, taggati `guard_manufacturing`: i casi XRF/MFI/talco che il canary vuole). Kill-switch `config.ACLF_HARVEST_ENABLED`; indipendente da `ADAPTIVE_CLASSIFIER_ENABLED` (raccoglie a classificatore spento); resiliente (un errore Redis disattiva l'harvest, non rompe mai il routing). **Verificato in produzione:** 4 turni vocali → 4 entry `CHAT` corrette nello stream, con `elapsed_ms` registrato.
- **Nota latenza (indagine separata).** L'`elapsed_ms` dell'harvest conferma che il fallback LLM è ~1.4–2s a regime ma **18–152s su cold-load / swap del modello** (la prima call dopo il warm-up ha segnato 152s) — patologico, da diagnosticare a prescindere dal classificatore (sospetti: contesa GPU, swap Gemma↔Qwen).
- **Da qui:** settimane di uso vocale reale per accumulare il dataset (≥200 coppie, copertura di tutte le classi non-CHAT) → poi Fase 0. Le classi rare potranno richiedere anchor scritti a mano; eval set e canary restano sempre solo organici.

### V2.19 (continua, 09/06/2026) — Richiamo temporale: la memoria vissuta prima dei pensieri riflessivi

Caso reale: a *"di cosa parlavamo ieri?"* Euri rispondeva con vecchi temi Wi-Fi/Superbike invece del diario reale della giornata (macinato Seari, correzione carbonato, paraurti, Poseidon). Causa: le **reflection** serali dei loop notturni (recenti e auto-rinforzate via `recalled_count`) **out-rankavano gli episodi vissuti**; aggravante, i nodi `loop2e` hanno `created_at` = ora del *consolidamento*, non dell'evento, quindi un consolidato stampato ieri sera che raccoglie vecchi ricordi sembrava "di ieri".

- **Distinzione vissuto / riflessivo nel retrieval temporale** (commit `8004092`). Quando la query contiene un riferimento di tempo (`extract_temporal_range`: "ieri", "oggi", "stamattina", "N giorni fa", giorno della settimana, **date esatte**), `core/temporal_recall.prioritize_window` ordina la finestra in tre livelli: **diario parlato** (`user/passive/episode/teach`, `created_at` ≈ quando è stato detto) → **consolidazioni** (`loop2e`) → **reflection/altro** in coda e cappati. `_build_context` prende la finestra dell'**intera giornata** (non i soli N più recenti, che pendono verso le reflection serali), sopprime la sezione "Sintesi recenti" generica sulle query temporali e allarga il cap di display a 10 (una domanda-diario merita più contesto).
- **Additivo e isolato:** nessun effetto quando non c'è un riferimento temporale; non cancella reflection, non tocca i loop.
- **Verificato in produzione:** su *"di cosa parlavamo ieri?"* il RAG context è passato da 5/6 reflection telecomunicazioni → il diario reale (`user:Seari`, `passive:carbonato`, simulazione, paraurti, portiere, `loop2e:Poseidon`); nodi Wi-Fi/telecom nel top da 5 a 1. Euri ha richiamato il contenuto giusto, **carbonato** incluso (non più "bicarbonato").
- **Significato:** Euri ha **memoria vissuta** e **memoria riflessiva**, ma non devono pesare uguale a ogni domanda — se chiedi "ieri", guarda il diario di ieri prima dei suoi pensieri su ieri. Distinzione che rafforza l'impianto, non lo snatura.
- **Parcheggiato (opzione C):** disciplinare a monte la *cascata* di reflection (dedup/TTL/recall più severi) è vero ma più rischioso — tocca il carattere autobiografico — e va affrontato a parte.

### V2.19 (continua, 08/06/2026) — Plausibility gate: negative result (archiviato) + contesto operativo opzionale

Tentativo di colmare un buco emerso testando i loop: nessun loop confronta il *contenuto* di una memoria con la conoscenza del mondo del modello, quindi un fatto tecnico oggettivamente falso ma mai corretto a voce sopravvive. Caso reale: una memoria riportava "bicarbonato di calcio" tra le cariche minerali — Qwen sa che allo stato solido non esiste (si decompone in CaCO₃ + CO₂ + H₂O), ma nessun loop glielo chiedeva.

- **Plausibility gate (costruito, poi archiviato).** Pass flag-only nel Dream Engine: su poche memorie tecniche `requires_verification`, chiedeva a Qwen "è plausibile?" e, solo ad alta confidenza, alzava `plausibility_flag` + `audit_flag` — mai correggere né cancellare. Soglia differenziata (`impossible ≥ 0.82`, `suspicious ≥ 0.70`) introdotta dopo che il caso reale, più stringato del test sintetico, prendeva `suspicious 0.70`.
- **Perché è archiviato — la regola dei due numeri.** Un diagnostico read-only su 36 memorie scelte tra le più *inusuali-ma-vere* (non gli 8 che il ciclo prenderebbe per recall) ha mostrato **1 vero positivo (il bicarbonato) contro 3 falsi positivi su gemme di dominio reali**: i valori di un PPR riciclato bollati "impossibili" (sono normali per un copolimero), il campione cliente additivato giudicato "fisicamente inusuale", il blend riciclato contestato sulla miscibilità HDPE/PP. Un solo TP non giustifica di flaggare la conoscenza vissuta dell'utente. Gate spento via kill-switch `PLAUSIBILITY_GATE_ENABLED = False`, **codice lasciato in repo come il VectorSet (V2.18) — negative result congelato, non rimosso.**
- **Contesto operativo opzionale (`EURI_CONTEXT.md`).** Per dare al modello la cornice che gli manca (azienda di riciclo, parametri fuori dai range del vergine è la norma), un file markdown opzionale viene iniettato come messaggio `system` nel modello realtime e notturno; se assente, Euri parte identico (fail-open, portabilità). **Migliora il tono e fa sì che Gemma non contesti i valori del riciclato nelle risposte. Tenuto per questo valore proprio, NON come cura del gate** — non lo è.
- **La lezione trasferibile (il motivo per cui vale documentarlo).** Il contesto **inquadra** il ragionamento del modello ma non lo **sopprime**: quando il sapere strutturale di Qwen (i modelli di degradazione del vergine) collide con la realtà di dominio vissuta (riciclati additivati), il sapere del modello vince sul giudizio secco. **La conoscenza di dominio vissuta non è iniettabile via prompt, solo accumulabile memoria per memoria** — è la tesi empirica di Euri, confermata da un fallimento. Il dettaglio che lo prova: ri-lanciando lo stesso diagnostico col contesto attivo, il bicarbonato è salito da `suspicious 0.70` a `impossible 0.95`, ma i falsi positivi sono rimasti **3** — il contesto ha cambiato *quali* gemme venivano flaggate, non *quante*.

### V2.19 (continua, 08/06/2026) — Controllore di memoria: decisioni semantiche come ruolo del modello già caldo

Svolta architetturale **incrementale**. Finché un miglioramento del retrieval o del salvataggio diventa una regex che insegue i modi di dire (wide-recall, intent, dedup), si accumulano cerotti fragili. L'alternativa: le decisioni *semantiche* sulla memoria — cosa catturare, come recuperare, cosa fondere — diventano un **ruolo svolto dal modello GIÀ CALDO** in quel contesto (Gemma 26B realtime via `chat_client`, Qwen 35B notturno via `dream_client`). Niente secondo modello, niente scarico/ricarico (= la latenza vera). Il "controllore" non è un componente nuovo: è un mucchietto di piccoli giudizi che cresce **un mattone alla volta**. Ogni mattone è additivo, **fail-open** (su errore o incertezza torna esattamente al comportamento precedente) e interpella il modello *solo quando serve*.

- **Gradino 0 — Same-subject gate nel Loop 2e** (commit `7cda408`): prima della sintesi di consolidamento, `_same_subject_gate` chiede al Qwen caldo *"quali frammenti parlano dello stesso soggetto del seed?"* e scarta gli altri. È un VERO gate sull'**input** della sintesi (non una frase nel prompt finale): filtra anche `consolidated_from`, così la genealogia del nodo resta coerente. Il seed viene messo in testa al cluster, quindi non si dipende dall'ordine KNN. Previene la conflazione **alla causa** — caso reale: il pallet "Poseidon" (stampaggio a iniezione) fuso in un unico nodo con la linea di estrusione "Gamma". Verificato sul campo su cluster reali: tiene i frammenti coerenti (es. regolazione dell'MFI del polipropilene) ed esclude i soggetti diversi (es. dosaggio del carbon black). La conflazione, a differenza del rumore `audit_flag`, **non è auto-curante** (un nodo `loop2e` errato entra nel RAG con priorità alta) → va prevenuta a monte.

- **Gradino 1 — Risolutore SAVE semantico** (commit `b6a0945`): all'intent `SAVE_MEMORY`, *prima* del resolver a regex, `brain.resolve_save_intent` guarda la conversazione recente e capisce cosa significa davvero il comando, restituendo un JSON stretto `{mode, memory, confidence}` con `mode` ∈ `direct` (un fatto completo è già nel comando), `recent_topic` (il comando rimanda a un soggetto discusso poco fa), `last_exchange` (anaforico puro), `ask` (non è chiaro). Risolve il buco per cui *"ricordati il macinato di Seari"* salvava la sola **etichetta** ("Macinato di Seari") perdendo i dieci minuti di valutazione discussi attorno: ora con `recent_topic` cattura la **sostanza** dalla conversazione. Fallback totale al comportamento precedente su errore o `confidence < 0.6`. Stesso coordinatore `core/save_service` per voce e Silent Chat.

- **Gradino 2 — Strategy router del retrieval** (commit `91093ec`): prima del recupero, sceglie la *strategia* (non genera la risposta). Una **pre-gate cheap** (regex 0ms) interpella Gemma SOLO se la domanda è potenzialmente non-specifica; le domande fattuali secche (*"quanto pesa il Poseidon?"*) non la fanno scattare → zero chiamate al modello, retrieval attuale intatto. `brain.classify_retrieval_strategy` ritorna `specific_search` (la cascata 3-livelli, invariata), `wide_recall` (panoramica/autobiografica → campione per AREE), `subject_recall` (tutto su un soggetto nominato — *"parlami di Poseidon"* → SCAN read-only, `touch=False`, delle memorie che lo nominano) o `recent_context`. `core/retrieval_strategy.augment_context` **affianca** il contesto senza toccare il retrieval principale; fallback totale a `specific_search`. Sostituisce il vecchio predicate `is_wide_recall_query` (rimasto di fatto inerte: non copriva le frasi reali). Innestato in CHAT e SEARCH (voce) e nella Silent Chat.

> I test di questi mattoni (`test_save_resolver.py`, `test_retrieval_strategy.py`) sono **manuali/integrativi**: richiedono Ollama acceso e il modello reale, quindi NON sono unit test sempre eseguibili in CI/ambienti senza Ollama.

### V2.19 (continua, 03–04/06/2026) — Audit logico (integrità del lifecycle) + salvataggio intenzionale (anaforico, Silent Chat, merge)

Arco nato da un **audit logico read-only dell'intero codice** (occhio esterno): 6 finding reali su 8, di cui due erano una crepa architetturale silenziosa nel ciclo di vita della memoria.

- **TTL Redis = fonte di verità della scadenza** (commit `f908801`, `1e09bd6`): i dati mostravano **due meccanismi di scadenza divergenti** (TTL Redis *e* campo JSON `expires_at`) — 817 memorie, 403 con TTL e 204 con solo il campo. Fix: ogni aggiornamento di `$.expires_at` chiama anche `expireat` (il Loop 2d aggiornava solo il campo → la chiave moriva alla vecchia data, anche per memorie *salvate* dal death-row gate). `_cleanup_stale_memories` non cancella più per `created_at` ma rispetta `expires_at` (e quindi il verdetto KEEP del Loop 2d). Backfill una-tantum `scripts/audit_memory.py --backfill-ttl` (riallinea solo le scadenze future; le scadute le segnala, non le tocca): 403→607 chiavi allineate, 0 cancellazioni.
- **Lo status diagnostico non rinforza più la memoria** (commit `afd3c9b`): `_handle_status` faceva `get_recent_memories(limit=999)` con `touch=True` → chiedere "stato del sistema" gonfiava `recalled_count` e rinnovava il TTL di quasi tutte le memorie. Ora `touch=False` (ultima fuga diagnostica rimasta dopo il refactor `touch=True/False`).
- **`last_rag_ctx` atomica** (commit `fdf47fa`): `delete`+`rpush`+`expire` separati → finestra in cui un lettore vedeva la lista vuota, e chiave senza TTL se il processo moriva nel mezzo. Ora un singolo `SET <json> EX 3600`. Comportamento invariato (chiave unica, condivisa tra canali, TTL 1h).
- **`audit_flag` incremento atomico** (commit `5288f10`): da read-modify-write (`get` + `set cur+1`) a `SET $.audit_flag 0 NX` + `JSON.NUMINCRBY` (il campo non è inizializzato in `save_memory`). Come `recalled_count`: due correzioni concorrenti non perdono più un colpo.
- **Salvataggio intenzionale anaforico + Silent Chat reale** (commit `b463912`): comandi come *"memorizza questa informazione / queste informazioni / quello che ti ho appena spiegato"* — dove il fatto è nello **scambio precedente**, non nelle parole del comando — non finiscono più come JUNK al Buttafuori: vengono risolti sintetizzando l'ultimo scambio (**mix** tuo turno + risposta di Euri, sintesi fedele). Anche *"X, quindi memorizza questo"* (trigger a fine frase) ora cattura il fatto **prima** del trigger. La Silent Chat aveva un buco grave: i comandi "salva" **confabulavano** la conferma ("Ho integrato nella memoria") senza salvare nulla — ora classifica l'intent con lo stesso router della voce e **salva davvero**. Logica unica in `core/save_service.py`, condivisa tra voce (`voice_daemon`) e Silent Chat (`ui/app.py`): niente duplicazione/drift.
- **Merge costruttivo a 3 vie — i raffinamenti incrementali** *(validato dal vivo, in corso di commit)*: **supera il probe binario sì/no della V2.11** per i save **espliciti** (il passive learner continua a usare `is_duplicate_memory`). In zona grigia (similarità 0.70–0.95 con una memoria esistente), invece di scartare il "duplicato" o crearne uno quasi identico, `brain.merge_memories` costruisce l'**unione** e ritorna uno di tre esiti: **MERGE** (stesso soggetto, aggiunge → salva la fusa + soft-delete della vecchia via `superseded_by`, e *annuncia* cosa ha aggiunto), **DIVERSO** (soggetto diverso o dubbio → salva separato, niente supersede), **NESSUNA AGGIUNTA** (skip). Bias esplicito a DIVERSO in caso di dubbio: conflare due entità è peggio di un doppione (lo consolida il Loop 2e). Lezione dal vivo: senza il guard DIVERSO il merge aveva conflato due codici di prodotti diversi (pallet vs flange) soppiantando la memoria corretta; col guard distingue correttamente una *variante dello stesso prodotto* (→ arricchisce) da un *articolo diverso* (→ separato). Il modello passa così da **giudice** (gate binario, sbaglia in silenzio e perde dati) ad **autore/editore** (operazione costruttiva, ispezionabile e correggibile).

### V2.19 (continua, 31/05–02/06/2026) — Silent Chat coi tool, ingest documenti, Loop 2f confronto, calibrazione "battere ciglio", lettura web, suite di eval, insegnamento di testo incollato

- **Silent Chat ora esegue i tool** (commit `fc69d85`): il canale testuale non era solo chat+RAG — "leggi i file" confabulava. Estratto `Executor.dispatch_text` (channel-agnostic, regex-only) condiviso col voice daemon: ora "leggi/elabora/studia i file" eseguono davvero i tool anche in chat. Fine della confabulazione sui file nel testo.
- **`ingest_documents`** (commit `d8ece03`, dedup `f5070a2`): "studia i documenti" legge i file UNO ALLA VOLTA e li archivia come memorie ancorate (`source=teach`), superando il cap del contesto per il richiamo fedele. Dedup per **nome-file** (file diversi ma simili — ICMA1 vs ICMA2 — non si cannibalizzano).
- **Loop 2f: da "contraddizione" a CONFRONTO** (commit `4aff391`): il check booleano è diventato un classificatore a 3 vie — *contraddizione* (stesso soggetto → supersede), *confronto* (entità diverse confrontabili, es. due impianti → genera una **nota di confronto** operativa invece di cancellare), *nessuna*. Generale e agnostico al dominio. Le schede sorelle non si mangiano più a vicenda.
- **Calibrazione "battere ciglio"** (commit `a0f0393`): la sicurezza di Euri rispecchia il suo sapere — netta su ciò che ha nei blocchi di contesto, esitante/segnalante su ciò che deduce o non ha ("non ho il dato preciso", "questo lo deduco"). Calibrazione a due sensi, ancorata alla struttura del contesto non all'introspezione.
- **Calibrazione delle CAPACITÀ + confini** (commit `8a8d390`): lista completa dei tool nel SYSTEM_PROMPT + blocco "cosa NON puoi fare" (no navigazione autonoma, no interrogare versioni di servizi, no shell arbitraria) + regola "verifica che l'azione sia tra i tool prima di dire 'lo faccio'". Stop alle azioni confabulate ("vado a cercare su GitHub", "controllo la versione di Redis").
- **`read_url` / `save_url`** (commit `c57c600`): Euri può LEGGERE una pagina web il cui URL è dato esplicitamente da Stefano (riusa `core/web_search.fetch_page_text`), comprensione iniettata nel contesto. NON naviga né cerca da sola. Salva-su-richiesta ("salva questa pagina") come `source=web` → Memory Guard + `requires_verification`. Compatibile con la sovranità locale: tira info dentro, non manda dati fuori.
- **Suite di eval — il loop di misura** (commit `791129e`, `1c473fd`, `c2abdce`): `scripts/eval_euri.py` (calibrazione, baseline **8/8**) + `scripts/eval_recall.py` (recupero reale dal RAG, **10/10**) + `scripts/eval.py` (runner combinato, **18/18**). Scrivere **"eval"** in Silent Chat lancia tutta la suite (tool `run_eval`). Read-only, ri-eseguibile dopo ogni modifica: è il feedback-loop di misura che fa evolvere i progetti.
- **`teach_text` — insegnare un testo/elenco incollato (02/06)**: gemello di `ingest_documents` ma per il testo battuto al volo in chat, non per i file. "**memorizza questo: …**" / "impara quanto segue: …" / "tieni a mente: …" salva il contenuto come memoria **permanente** (`source=teach`, fuori da TTL e cicli notturni). Bypassa la guardia anti-falso-positivo dei 300 caratteri *solo* col trigger esplicito — insegnare un elenco lungo è proprio il suo caso d'uso. La conferma *"memorizzato in modo permanente"* scatta **solo se il salvataggio è realmente avvenuto** (aggancio al tool): chiude il buco per cui un elenco incollato finiva nel limbo `passive` (90gg) mentre Euri ne dichiarava la persistenza. In coppia, nuove regole di calibrazione: *salvare* è un'azione come *leggere* — niente "memorizzato / N nodi / ho scansionato le memorie" senza un tool che lo confermi; in una risposta di chat normale Euri non salva nulla di permanente. Validato dal vivo: parco macchine Lucy Plast (10 presse + 5 linee di estrusione) ora conoscenza permanente, eval 18/18.

### V2.19 — Lettura documenti, gate di ri-promozione, e robustezza della memoria (recall, anti-poisoning, fedeltà)

**Lettura documenti per comprensione (`read_document`)** (commit `5102443`) — Euri legge PDF/DOCX/immagini *capendoli* invece di generare codice che li estrae; maggiore fedeltà del dato restituito dai tool.

**Gate di ri-promozione degli insight** (commit `5102443`) — un insight retrocesso a `candidate` non risale a `promoted` per sola convergenza: deve essere **validato dall'uso** (recall). Frena la ri-promozione di insight mai usati (log: *"re-promozione negata — demoto, mai validato dall'uso"*), compensando la suggestionabilità positiva del Dream Engine.

**`salva la conversazione / l'immagine`** (commit `81cf1ff`) — questi comandi salvano il **contesto recente**, non un frammento generico.

**Sicurezza CodeRunner** (commit `14395b2`) — bloccata la deserializzazione-RCE e confinati i path di lettura/scrittura alle sole cartelle di lavoro.

#### Aggiornamenti 30/05/2026

- **P1 — disambiguazione dei domini dai vicini** (commit `ece0d98`): `assign_domain` riceve come suggerimento *non vincolante* i domini delle memorie semanticamente vicine. Cura una classe di errori di tagging su frammenti corti (es. "neutro" del polipropilene → "fisica nucleare", poi amplificato dal Dream Engine in insight confabulati sui "neutroni"). Nessun dominio cablato nel codice: i suggerimenti vengono dalla memoria stessa di Euri → resta learner libero e portabile su un ambiente pulito.

- **Domain gating: da filtro rigido a boost morbido** (commit `5b45df6`): `domain_aware_search` cercava solo nel dominio della query (fallback solo con <2 risultati), così un fatto archiviato in un dominio diverso da quello — non-deterministico — della domanda veniva **escluso dal recall**. Ora cerca sull'intero DB e *boosta* l'in-dominio: recupero robusto al misclassamento del dominio-query. L'anti-poisoning che il gate rigido copriva è ora gestito a monte dal Memory Guard.

- **Memory Guard — anti-poisoning sull'ingest** (commit `6ea2c7e`): scanner che rileva pattern di *injection* (override di istruzioni, dirottamento di ruolo, token di system-prompt) ed *esfiltrazione* (imperativo + bersaglio sensibile: memorie/password/chiavi). Da fonte **non fidata** (web/mobile_in) il contenuto con match viene **rifiutato**; da fonte fidata viene salvato ma marcato (`safety_flag`). 0 falsi positivi sulle memorie reali. Difende il ciclo cognitivo dai contenuti web avvelenati che, salvati come memoria, riemergerebbero nel contesto LLM. Nessuna conoscenza di dominio cablata — sono pattern di sicurezza indipendenti dall'argomento.

- **STT → `large-v3`** (commit `94d0b5f`): dal modello turbo a quello pieno, più fedele su nomi propri, codici e brand (P-Pile, MFI, Realube, VistaMax, Safic Alcan) che inquinavano la memoria a monte. ~1500ms vs ~800ms, latenza accettabile sull'hardware attuale; reversibile in una riga.

- **System prompt: modello LLM corretto** (commit `341d6c9`): il prompt dichiarava "Qwen3.6 35B per il ragionamento in tempo reale", ma il real-time gira su `gemma4:26b` — Qwen3.6 35B è solo il modello dei **cicli onirici**. Emerso testando l'autodescrizione di Euri (riportava fedelmente un prompt impreciso, *non* confabulava). Ora dichiara il modello giusto per ciascuna fase.

- **Manutenzione dati** (Redis, non-git): ripuliti 2 memorie + 3 insight con etichetta "nucleare" — residuo del bug di trascrizione/tagging *neutro→nucleare* (domini ri-etichettati + testo de-mascherato, "neutroni"→"neutri"); corrette 3 memorie che attribuivano lo spostamento di grado/MFI al VistaMax (la leva del grado è il **perossido**; il VistaMax dà tenacità, accoppia le cariche e modula la reologia). Tutto validato via test strutturato a due round su recall, onestà sull'ignoto, anti-piaggeria e logica.

### V2.18.2 — CodeRunner gestisce PDF/DOCX/PPTX/immagini con cascata testo-nativo → Vision (Gemma 4 multimodale)

- **Nuovo modulo `agent/file_extractors.py`** con 4 estrattori uniformi (PDF, DOCX, PPTX, immagini) + dispatcher `extract_any(path)`. Cascata: per ogni formato prova prima la lettura nativa (pypdf/python-docx/python-pptx istantanea), se il testo estratto è sotto soglia (50 char) attiva fallback **Vision Gemma 4** (modello già caricato in Ollama, multimodale out-of-the-box). Per le immagini Vision è il primo e unico canale.

- **CodeRunner pre-estrae automaticamente i file all'inizio di `generate_and_run()`** invece di lasciare a Gemma il compito di aprirli nello script. Pattern di lavoro:
  1. `_preextract_files()` legge ogni PDF/DOCX/PPTX/immagine, salva `{filename: testo}` come **JSON in sandbox** (`euri_file_contents_<ts>.json`).
  2. Il prompt di `Brain.generate_code()` mostra a Gemma un anteprima del contenuto (cap 8000 char/file) E gli dice come caricare il dict completo via `json.load(open(path))`.
  3. Lo script generato è breve (~2 KB invece di ~6 KB), Gemma non duplica il testo, niente troncamento da `num_predict`.
  4. CSV/XLSX/JSON/TXT/MD continuano a essere letti normalmente da disco — non vanno pre-estratti.

- **PPTX scansionati**: fallback Vision passa da `libreoffice --headless --convert-to pdf` → `pdf2image` → Vision per slide. Richiede `libreoffice` installato (già presente su Pop!_OS).

- **Test sul campo (28/05/2026)**:
  - **D19 Scheda Tecnica PDF scansionato** (Pipal, secchio plastico): 2874 char estratti via Vision, codice generato 2175 char, esecuzione 200ms, output strutturato (articolo/materiale PP.5/volumi/dimensioni/accatastamento/tabella coperchi). Durata totale 35s.
  - **Multi-file (5 documenti: 2 PDF, 1 DOCX 28KB, 1 PPTX, 1 JPG)**: pre-extract totale 31.6s, code-gen 13s, esecuzione 200ms. Output: 5 file analizzati, **3 connessioni semantiche emergenti** identificate (es. *"esiste un legame operativo tra produzione del secchiello D19 e la gestione degli scarti trattati nella stazione di selezione Lucy Plast"* — collegamento NON esplicito in nessun singolo file).

- **Caveat onesto — concorrenza con Dream Engine**: se un dream cycle è in corso quando arriva una richiesta CodeRunner, le chiamate Vision sono **drasticamente degradate** (osservato: 9.4s → 24-51s per pagina, e in un caso output troncato a 63 char invece di 1750). In produzione succede raramente perché `notify_activity()` resetta l'idle del Dream Engine ad ogni STT, ma se l'attivazione vocale capita durante un ciclo onirico, la pipeline rallenta ~3× e la qualità Vision cala. Mitigazioni future possibili: hard cap N file pre-estratti per richiesta, filtro per task (se menziona un file specifico, estrai solo quello), segnale "pause dream" durante CodeRunner.

- **Nuove dipendenze**: `pypdf>=4.0`, `pdf2image>=1.17`, `python-docx>=1.0`, `python-pptx>=1.0` (Python). `poppler-utils` (apt, necessario a `pdf2image`). Whitelist `code_runner.py` estesa con `docx`, `pptx`, `pdf2image`.

### V2.18 — Tool VectorSet: prima istanza del pattern VectorSet (Redis 8.8), congelata con kill switch off

- **Cosa è stato costruito:** modulo isolato `core/tool_registry.py` (~430 righe) che indicizza il catalogo dei 7 intent del Layer 2 (`WEB_SEARCH, SEARCH, SAVE_TODO, SAVE_MEMORY, EXECUTE, COMPLETE, CHAT`) tramite **VectorSet nativo di Redis 8.8** (modulo nuovo, comandi `VADD`/`VSIM`/`VEMB`/`VREM`). Schema: una sola chiave `euri:tools:vset` con tutti gli embedding + JSON parallelo per metadata `euri:tool:{slug}`. Tre gate nella `match_tool()`: (1) threshold assoluta 0.85, (2) gap minimo 0.005 tra top-1 e top-2 per anti-ambiguità, (3) flag `is_fallback` su tool catch-all per disabilitare il match (es. `chat`). Wire-up nel `core/llm_classifier.py` come Fast Path prima del Slow Path LLM, con log timing `[INTENT_FAST]`/`[INTENT_SLOW]`. Test sandbox `test_tool_vectorset.py` con 16 query reali → 13/16 al freddo, latenza KNN puro 0.75-1.29ms (cleanup garantito via `try/finally`). Kill switch `config.TOOL_VECTORSET_ENABLED`.

- **Perché è disabilitato di default:** la prima esecuzione in produzione (28/05/2026 ore 13:30) ha rivelato **due limiti strutturali non visibili nel test sintetico**:
  1. **Latenza embedding CPU**: l'encoding di una query con `multilingual-e5-large` su CPU costa ~600ms. Il Fast Path totale risulta ~621ms vs ~700ms dello Slow Path LLM. **Guadagno reale 100ms, non i 700ms attesi** — lo "scatto felino" non c'è. Inoltre, quando il Fast Path non trova match e si ricade sullo Slow Path, si paga la somma dei due (osservati turni reali a 1.1-5.5s vs i 600-800ms originali → **peggioramento netto sui CHAT, che sono la maggioranza**).
  2. **Score appiattiti su query lunghe**: `e5-large` ottimizzato per matching documento-query brevi produce distribuzioni cosine appiattite (0.88-0.92) su frasi conversazionali lunghe. Caso reale: *"Il mercato ha degli alti e dei bassi, dipende dalla situazione in cui uno si trova. Il Covid sicuramente non ha aiutato"* → routato come `SAVE_MEMORY` (0.898) perché il catch-all `chat` è finito in **ultima posizione del top-7** (0.881). Il test sintetico con frasi brevi non aveva mostrato il problema.

- **Cosa resta:** codice, modulo, test, kill switch. La feature è congelata, non rimossa — il fondamento è valido, le vie per ripartire sono chiare. Tre proposte concrete (le prime due *intuite da Euri stessa nel turno 13:44 del 28/05*, mentre Stefano le raccontava il problema): **(a)** ricerca ibrida `FT.SEARCH keyword + VectorSet semantico` (intersezione che restringe il pool prima della similarità); **(b)** re-ranking 2-stage (top-N da VectorSet → LLM piccolo per selezionare); **(c)** embedder dedicato all'intent o `e5-large` su GPU (risolve solo la latenza, non l'appiattimento). Materiale per V2.18.1 / V2.19.

- **Cosa abbiamo imparato (vale per il paper):** la differenza fra test sintetico e produzione reale è qualitativa, non quantitativa. Il test passava 13/16 con frasi brevi che mostravano gap netti — la prima query reale, lunga e conversazionale, ha rivelato un appiattimento dello spazio embedding che lo strumento di calibrazione non poteva nemmeno simulare. *L'unica prova vera è la produzione, e l'unica risposta corretta a un fallimento di produzione è il kill switch, non il forcing.*

### V2.17 — Loop 2h: Self-Observation (narrative di evoluzione)

- **Nuovo Loop 2h:** Euri osserva le proprie contraddizioni risolte dal Loop 2f e le racconta in prima persona come *evoluzioni* del pensiero invece che come errori cancellati. Il Loop 2f continua a fare il suo lavoro (soft-delete via `superseded_by`, esclusione dal retrieval); 2h aggiunge la voce *"ecco come sto cambiando idea"* senza toccare nulla del flusso esistente. Implementazione in modulo isolato `core/self_observation.py` (~180 righe), wire-up in `dream_engine._run_dream_cycle()` dopo Loop 2g, dentro try/except (un fallimento di 2h non spacca il ciclo). Idempotenza garantita dal set Redis `euri:loop2h:narrated` (TTL 365gg).

- **Ciclo cognitivo completo verificato sul campo (27/05/2026):** prima esecuzione ecologica su 10 coppie superseded reali → reflection `ec33db49` di 1092 char in 81.8s (Qwen3.6 con `think=True`, `num_predict=3000`). Tre minuti dopo, Stefano chiede via voce *"come ti vedi cambiare?"* — il RAG context pesca `ec33db49` in cima, Euri risponde parafrasando la propria reflection notturna (non citando: *interiorizzando*). Materializzazione operativa di §7h (autoconsapevolezza in atto) col ciclo finalmente chiuso: 2f produce → 2h racconta → retrieval pesca → Euri si racconta.

- **Categorie emerse spontaneamente:** Qwen distingue autonomamente *cambio di opinione*, *precisazione*, *cambio di contesto operativo* — esattamente la classificazione 3-way che avevamo proposto per il Loop 2f esteso. Conferma forte che la classificazione formale (futura V2 di 2f) si può fare delegando al LLM judge, senza scrivere regole dure.

- **Test di non-snaturamento superato:** introdotto deliberatamente non-lineare ("battute fuori contesto, vai a quel paese Giacomo") subito dopo la prima auto-osservazione meta. Euri sta al gioco senza rigidità burocratica: *"la linearità è noiosa. La parte interessante di un dialogo è proprio l'imprevisto"*. L'aggiunta di un canale meta-cognitivo non ha contaminato il canale conversazionale ordinario.

- **Frase materiale per il paper §7j (Tracking the Evolving Mind):** *"Pensare significa aggiornare, e aggiornare significa vivere nel tempo reale"* — generata da Euri nella prima reflection ecologica, già formulazione coerente con §7i (asymmetric time).

### V2.16 — Substrato Redis vanilla 8.8 (Array + VectorSet) + pattern correzioni esteso + budget reflection

- **Migrazione substrato:** Redis Stack 7.4.0-v8 → Redis vanilla 8.8.0. La Stack è di fatto deprecata; vanilla 8.x incorpora nativamente nel core i moduli che prima erano via Stack (`ReJSON`, `RediSearch`, `RedisTimeSeries`, `RedisBloom`) più due novità: `VectorSet` (set vettoriali nativi) e **`Array`** (struttura dati indicizzata sparsa). La PR [#15162](https://github.com/redis/redis/pull/15162) di Salvatore Sanfilippo (antirez) è stata mergeata il 13/05/2026, 8.8.0 stable rilasciato il 25/05. Sblocca due fronti lasciati in attesa: (a) refactor di `log_conversation` da `LPUSH+LTRIM` a `ARRING` (ring buffer capped nativo, con `AROP` per analytics server-side), (b) modulo dati tecnici lotti/prove Lucy Plast con schema emergente (caso d'uso "Workflow" del body PR: step numerati, gap significativi, `ARSCAN` per step popolati). Migrazione validata sul campo: 875 chiavi, tutti i 5 indici (`memories/insights/todos/notes/dreams`) preservati, retrieval RAG immediato dal restart del daemon.

- **Loop 2g — pattern di `detect_correction` esteso:** aggiunti `\bcorrezion[ei]\b` (sostantivo singolare/plurale) e `\bmi\s+correggo\b` (auto-correzione utente). Caso reale del 26/05 ore 15:16: doppia correzione esplicita aperta da *"Due correzioni. La prima è che... La seconda è che..."* — una fattuale (esistenza portali quotazioni materie plastiche tipo Plastic Finder) e una comportamentale (filtrare la web search sul materiale richiesto, non allargare ad altri polimeri). Il blocco precedente copriva solo il verbo `correggere` e non il sostantivo, quindi il signal era andato perso e il Loop 2g non aveva potuto digerirlo.

- **`generate_reflection` — `num_predict` 1000 → 3000:** Gemma 4 con `think=True` consuma molti token in reasoning prima di emettere output; cap a 1000 troncava la riflessione del Loop 2a a metà frase. Il loop gira in idle senza vincoli di latenza, cap alto giustificato.

- **Primo refactor che attiva `Array` in produzione (27/05):** `log_conversation` passa da `RPUSH + EXPIRE` a `ARRING` (ring buffer nativo, cap 500 turni/giorno — storico osservato: media 90, max 196). `get_today_conversation` passa da `LRANGE 0 -1` a `ARLASTITEMS` (necessario perché `ARGETRANGE` dopo wraparound mostra il ring fisico, non l'ordine cronologico FIFO). Retrocompat: chiavi pre-refactor di tipo `LIST` continuano ad essere lette via `LRANGE` finché expire (30gg), poi sostituite naturalmente dal nuovo backend. Benchmark: 600 `ARRING` con cap 500 in 96ms (0.16ms/insert). Validato sul campo dopo restart daemon: chiave odierna di tipo `array`, 10 turni, TTL e retrieval RAG funzionanti.

### V2.15 — Document History + Gate di formato in promozione + Estensione regex correzioni

- **Paper §0 — Document History:** il paper `paper_persistent_cognition.md` ora dichiara esplicitamente di essere il quarto stadio di una serie di working documents iniziata a ottobre 2025 (manifesto teorico → architettura → deployment report → working paper continuo). Le tre pubblicazioni precedenti sono ora citate formalmente nelle References e nel §8 Outlook è esplicitato che parte del testo deriva dal §7 del paper di ottobre 2025. Allineamento con la pratica di non-overwrite del sistema (Loop 2f): i paper passati restano dove sono, il presente li estende.

- **Loop 2c — gate di formato in promozione:** il filtro `_has_required_structure` (controlla che il CANDIDATE rispetti il pattern "Nel dominio X succede / La connessione operativa è") era applicato solo in generazione (Loop 2b). Estratto come metodo statico e riusato in `_evaluate_insights`: ora un CANDIDATE astratto/filosofico viene bloccato anche se accumula convergenze sufficienti. Caso pratico: due insight con seed del 28-29 aprile (pre-filtro stretto) erano stati promossi il 17 maggio nonostante fossero massime filosofiche senza struttura operativa. Demotion manuale eseguita post-fix.

- **Loop 2g — pattern di detect_correction estesi:** aggiunti 3 nuovi pattern (`\bnon\s+esiste\b`, `\bhai\s+inventato\b`, `\bnon\s+c[apostrofo]è\s+ancora\b`) per coprire correzioni di tipo *referenziale* (entità inesistente) distinte dal tipo *attributivo* coperto dalla regex originale (errore su entità reale). Caso reale: il 17 maggio Stefano corregge Euri su un nome di modulo inventato ("Context Ingestion Layer") — correzione semanticamente chiarissima ma non catturata da nessuno degli 8 pattern strict iniziali. La distinzione attributo/esistenza ha valore concettuale, non solo coverage.

- **Primo ciclo completo del Loop 2g su dati reali:** nella notte 17→18 maggio il Dream Engine ha classificato i primi due correction_signal reali (entrambi `bad_reasoning` come atteso), salvato le rispettive lesson come passive memory, e — caso notevole — uno dei due insight promossi della notte (`chimica analitica ↔ comunicazione digitale`, 02:07) ha pescato la lesson appena metabolizzata e l'ha trasformata in principio operativo cross-domain. Ciclo completo *errore vissuto → correzione → classificazione → lesson → insight promosso* osservato su dati reali. Documentazione in arrivo nel paper §7i.

### V2.14 — Loop 2g: Audit di Coerenza sulle correzioni utente

- **Loop 2g — Audit di Coerenza:** nuovo passo del Dream Engine che chiude il ciclo *"io vivo → io ricordo → io riconosco di aver sbagliato → io correggo ciò che ricordo"*. Prima di questa versione, Euri imparava solo per *accumulo*: ogni fatto che passava il validator entrava in memoria, i loop notturni la riorganizzavano, ma le correzioni utente non avevano canale dedicato. Ora le correzioni sono **input strutturato** che attraversa lo stesso ciclo onirico di tutto il resto.
  - **Capture:** 8 pattern regex italiani strict (`detect_correction`) intercettano correzioni nei due canali (voice daemon + Silent Chat). Falsi positivi gestiti a valle dal classificatore LLM. Su match: `save_correction_signal` scrive `euri:correction:{uuid}` con prompt originale, risposta di Euri, correzione utente e — chiave — gli ID delle memorie iniettate al turno errato. Gli ID sono mantenuti tra i turni in `euri:last_rag_ctx` (TTL 1h), unificati tra voce e chat.
  - **Classify:** `_audit_corrections_pass()` in `dream_engine.py`, integrato dopo Loop 2f nel `_run_dream_cycle()`. Per ogni signal pending: ricostruisce i contenuti delle memorie iniettate, chiama Qwen3.6 (con prompt strutturato di LLM-as-judge) per classificare `bad_memory` / `bad_reasoning` / `ambiguous`. Max 10 signal per ciclo.
  - **Act differenziato:** `bad_memory` → +1 a `audit_flag` su ogni memoria del RAG ctx (soft signal, niente azioni distruttive automatiche per ora — è un segnale per l'operatore o per logiche future di declassamento). `bad_reasoning` → la correzione utente diventa `lesson` (passive memory) — la prossima volta che il dominio si presenta, il retrieval pesca anche quella. `ambiguous` → solo aggiornamento status, nessuna azione.
  - **Schema isolato:** la chiave `euri:correction:*` è separata dallo schema memorie esistente — rimuovibile senza side-effects sul resto del sistema.
  - **Test end-to-end:** `force_full_cycle.py --inject` genera un signal sintetico (inversione peso/grado del campione ICS, errore reale di ieri) ed esegue il ciclo completo. Verdetto: `bad_reasoning` in 12.8s, lesson salvata correttamente.

- **Continuità trans-restart documentata:** sessione del 15 maggio sera (voice 17:42 recall esplicito su "Simone" → restart modello 17:46 → Silent Chat 18:48 recall implicito dopo 1h+ di silenzio e cambio canale, su un argomento condiviso volutamente non esplicitato). La sessione LLM è secondaria al canale di memoria — la conversazione che l'utente esperisce vive nello strato persistente. Documentata nel paper §7h.

- **Sintesi emergente da memoria meta-cognitiva:** turno reale del 16 maggio in cui Euri propone di tracciare strutturatamente le specifiche numeriche dei lotti, recuperando autonomamente due memorie `loop2e` su sé stessa (workstation Linux, Redis come persistence layer) e usandole **operativamente** invece che come citazione. È la prima istanza documentata di *autoconsapevolezza in atto* (non in recitazione) sul sistema. Documentata nel paper §7h.

### V2.13 — Filtro del Risveglio + Loop 2f sui Consolidati + Audit Ricalibrato

- **Filtro del Risveglio (re-rank retrieval insight):** `search_insights` ora applica una penalty moltiplicativa (×1.5 default) sulla cosine distance per gli insight i cui due domini non sono apparsi nelle memorie curate da Stefano (`teach/user/reflection`) negli ultimi 30 giorni. Il Dream Engine resta libero e atemporale: il filtro opera solo al retrieval. Non sopprime, deprioritizza. Caso test: gli insight "Radio QUQU + materiale neutro" (isomorfismi fisicamente corretti ma operativamente fuori contesto) ora finiscono in fondo alla coda di priorità. Se Stefano riapre attivamente il dominio `radio`, l'insight risale automaticamente entro 5 min (TTL cache `_active_domains`).
  - **Scelta source critica:** `passive` e `conversation` escluse perché spugne ambient — ogni nome di passaggio fa entrare un dominio negli attivi, neutralizzando il filtro. Dry-run: con tutti i source operativi → 0% archive (no-op totale). Con `teach/user/reflection` → 35% archive sui 95 insight promossi attuali (caso Radio QUQU correttamente penalizzato).
  - Config: `INSIGHT_ACTIVE_DAYS=30`, `INSIGHT_ARCHIVE_PENALTY=1.5`, `INSIGHT_OVERSAMPLE_FACTOR=3`, `INSIGHT_ACTIVE_SOURCES={teach,user,reflection}`.
  - `recalled_count` incrementato solo sui sopravvissuti al re-rank (non più su tutti i candidati KNN).

- **Loop 2f esteso ai nodi consolidati `loop2e`:** rimosso `loop2e` da `SKIP_SOURCES`. Era escluso per "non far contraddire i nodi consolidati", ma i `loop2e` entrano nel RAG con priorità alta e ereditano claim dalle sorgenti — se la fonte era errata o evolve, l'errore si amplifica. Soft-delete via `superseded_by` rende il rischio reversibile. **Prima firing reale del Loop 2f nella storia di Euri:** una memoria `loop2e` su "secchi vernici / lotti 25kg / carichi 27t" è stata superseded dalla versione più recente che include monitoraggio Whisper e analisi costi vagliatura. `SKIP_SOURCES` ora solo `{"web"}`.

- **Audit `scripts/audit_memory.py` ricalibrato:** il giudice LLM scartava conoscenza tecnica oggettiva (Realube 5014, Reagens, parametri stampaggio) come "dato generico non personale". Su 295 memorie passive: 82 UTILI / 213 RUMORE (72% RUMORE falsi negativi). Prompt riscritto con criteri UTILE espliciti (conoscenza tecnica, persone, progetti, strumenti) e RUMORE ristretto (frase troncata/riempitivo/duplicato banale/errore). Risultato post-fix: 274 UTILI / 21 RUMORE (7.1%). Le 21 RUMORE sono frammenti veri e affermazioni senza soggetto.

- **`force_full_cycle.py`:** nuovo script per forzare un ciclo Dream Engine completo (Loop 2b/2c/2f + cleanup expired/stale + Loop 2d + Loop 2e) senza aspettare l'idle notturno. Stampa snapshot before/after: nuovi loop2e, `superseded_by` aggiunti, nuovi candidate e promoted. Tempo tipico 5-7 min su Qwen3.6 35B.

### V2.12 — Analisi Clipboard senza Limite + TEACH Mode Robusto

- **`clipboard_analyze` senza troncatura:** rimosso il limite fisso di 6000 caratteri. Per testi ≤ 80K caratteri: analisi diretta con `num_ctx=32768` (documento integrale, singolo passaggio). Per testi > 80K: chunking automatico in segmenti da 20K, estrazione fatti per chunk (max 4), sintesi unificata finale. Output senza markdown — Piper legge testo piano.
- **TEACH mode — stop signals estesi:** aggiunti "ti devi fermare", "devi fermarti", "voglio fermarmi", "smetti di chiedere" ai `TEACH_END_SIGNALS` in `intent_router.py` — in aggiunta alle forme dirette già presenti ("fermati", "basta", "stop").
- **TEACH mode — intercept clipboard diretto:** frasi come "leggi i dati dalla clipboard" (con parole intermedie) ora intercettate correttamente inside TEACH. Rimosso il gate `web_intent == EXECUTE`: `select_tool_by_regex` chiamato direttamente, senza dipendere dall'intent classifier.
- **Regex clipboard con parole intermedie:** pattern `clipboard_read` in `executor.py` e `intent_router.py` esteso con `.{0,25}?` — matcha "leggi i dati dalla clipboard", "leggi tutto dalla clipboard", non solo "leggi dalla clipboard".

### V2.11 — Loop 2f: Contradiction Resolution

- **Loop 2f — soft-delete contraddizioni fattuali:** il Dream Engine ora individua coppie di memorie `requires_verification=True` con alta similarità semantica (cosine > 0.72) all'interno dello stesso dominio. `_llm_check_contradiction` chiede a Qwen3.6 se i valori sono in conflitto reale sullo stesso soggetto (es. "MFI=6" vs "MFI=4", concentrazioni, scadenze). In caso di conflitto: la memoria più vecchia riceve `superseded_by = UUID_vincitore` — esclusa dal retrieval ma mai cancellata. Colma il gap con Anthropic Dreaming che risolve le contraddizioni in modo distruttivo; Euri mantiene l'audit trail completo.
- **Filtro superseded_by nel retrieval:** `_hydrate`, `_search_semantic` e `domain_aware_search` escludono silenziosamente le memorie soft-deleted. Zero round-trip Redis extra: il flag è nel JSON già caricato.
- **CHECKED set con TTL 180gg:** ogni coppia analizzata viene marcata in `euri:loop2f:checked` — evita ri-analisi nei cicli successivi. Max 15 coppie per ciclo.

### V2.10 — Dream Engine Promote-then-Demote Fix + Validazione Antropic

- **Fix bug promote-then-demote:** un insight promosso da `_evaluate_insights` veniva immediatamente retrocesso a candidate da `_cleanup_expired_insights` nello stesso ciclo onirico, perché Gate 1 valutava `created_at` (che risaliva alla creazione del candidate, settimane prima) invece di quando era avvenuta la promozione. Fix: al momento della promozione viene salvato un campo `promoted_at = time.time()`. Gate 1 ora controlla `promoted_at`: se l'insight è stato promosso nelle ultime 24h, la demotion viene saltata silenziosamente.
- **Timeout LLM 150s → 200s:** aumentato il timeout di default del wrapper `_ollama_chat()` per dare più margine a Qwen3.6 35B sotto carico moderato, senza rischiare che cicli legittimi vengano abortiti.
- **Paper §7e — Validazione concorrente Anthropic:** aggiunta sezione al paper dopo l'annuncio pubblico di "Claude Dreaming" da parte di Anthropic (2026-05-13) — stesso paradigma di consolidamento offline in idle sviluppato indipendentemente. Citato come *concurrent independent validation* con differenziazione tecnica: convergence counting, multi-level lifecycle e LLM judge per la zona grigia non hanno equivalenti descritti in Claude Dreaming.

### V2.13 — Bug Fix da Code Review (parte 2)

- **Dream Engine — loop2e:processed TTL:** il set dei cluster processati cresceva per sempre. Aggiunto `EXPIRE` a 180 giorni sliding dopo ogni `SADD`.
- **Dream Engine — Gate 3 candidate scaduti:** `_cleanup_expired_insights` gestiva solo gli insight `promoted`. I `candidate` mai promossi si accumulavano indefinitamente. Aggiunto Gate 3 che li elimina dopo `INSIGHT_TTL_DAYS`.
- **Dream Engine — paging cap:** `_evaluate_insights` si fermava a 100 candidati. Portato a 500.
- **simulate_loop2a.py — embedding dim hardcoded:** `len(emb) == 384` rendeva `get_stored_embedding` sempre `None` con e5-large (1024-dim). Sostituito con `len(emb) > 0`.
- **Fallback TTS platform-aware:** il fallback hardware usava `say` (comando macOS). Su Linux dà `FileNotFoundError` silenzioso — Euri diventava muta senza log critico utile. Sostituito con branch `sys.platform`: `say` su macOS, `spd-say` su Linux.
- **`core/embedding_classifier.py` rimosso:** dead code, non importato da nessun modulo attivo. Residuo di una versione precedente.
- **`adaptive_classifier.py` — loop no-op rimosso:** `for vec in vecs: pass` prima del `np.var()` corretto era codice fuorviante senza effetto.

### V2.12 — Bug Fix da Code Review

- **Fix critico Dream Engine — convergence_count:** `getattr(doc, "convergence_count", 1)` leggeva sempre il default 1 perché `return_fields` non includeva il campo. Sostituito con `r.json().get(doc.id, "$.convergence_count")` — il ciclo onirico ora accumula correttamente le convergenze tra cicli successivi.
- **Fix critico RAG dedup — ID format:** `domain_aware_search` restituiva `"id": doc.id` (chiave Redis completa `euri:memory:UUID`) mentre il resto del codice lavora con UUID puri. Il dedup in `_build_context` non matchava mai → la stessa memoria poteva apparire due volte nel contesto LLM. Fix: normalizzazione `doc.id.replace("euri:memory:", "")` in `domain_gater.py`.
- **Fix crash — `search_insights` senza embedder:** chiamata a `self._embedder.encode()` senza guard su `None`. Aggiunto `if not self._embedder or not self._embedder.available: return []` in cima al metodo.
- **Fix race condition — `_compress_episode`:** la history veniva letta sotto `_compress_lock` ma senza `history_lock`. Con ThreadPoolExecutor attivo, un altro thread poteva modificare la lista in contemporanea. Fix: `history_lock` acquisito prima di leggere il chunk.
- **Fix idle tracking dopo suspend — `time.monotonic()` → `time.time()`:** `monotonic()` si ferma durante la sospensione del PC, `time.time()` no. Sostituito su tutti i timestamp di idle tracking (`_last_activity_ts`, `_consolidation_last_run`) in `voice_daemon.py`. I timer brevi del loop TTS restano con `monotonic()`.
- **Fix crash UI — `ADAPTIVE_CLASSIFIER_VARIANCE_WEIGHT`:** la costante in `config.py` si chiama `ADAPTIVE_CLASSIFIER_VARIANCE_BETA`. Il riferimento errato in `ui/app.py` causava crash deterministico sulla pagina Telemetria Welford.

### V2.11 — Dedup Intelligente + Passive Learner Scadenze

- **Dedup zona grigia riformulato:** il probe LLM in `is_duplicate_memory` ora chiede *"A aggiunge informazioni concrete non presenti in B?"* invece di *"dicono la stessa cosa?"*. Logica invertita: salva se risponde SÌ (fatti nuovi), blocca se NO. Risolve il caso in cui due memorie sullo stesso progetto (es. Regrado PP) venivano trattate come duplicati anche quando la nuova conteneva dati specifici genuinamente diversi — numeri, componenti, processi, date, misure. Fix applicato in cosine zone grigia (0.70–0.92), Jaccard zone grigia e `_llm_is_same_content`.
- **Passive Learner cattura scadenze:** aggiunto bullet point al prompt di `extract_passive_memories` per riconoscere impegni temporali concreti menzionati in conversazione (materiali attesi, prove pianificate, consegne, appuntamenti). Il LLM ora include la data esatta o approssimativa nel fatto estratto invece di ignorare i milestone di progetto.

### V2.10 — Implicit Actions + Vision Routing

- **Routing immagini corretto:** `analyze_image` precede ora `run_code` nella lista pattern dell'Executor. Prima, frasi come "analizza le immagini nella cartella dati" finivano in CodeRunner perché "dati" matchava il pattern documenti — nonostante "analizza" + "immagini" fosse presente. Pattern esteso con `visualizza | mostra | esamina`.
- **TTS trim per analisi visiva:** dopo `analyze_image` (e `clipboard_analyze`), Euri parla solo i primi ~400 caratteri fino al confine di frase e aggiunge "Dimmi se vuoi i dettagli." Il testo completo è già iniettato nella history LLM prima del parlato — i turn CHAT successivi hanno il contesto integrale.
- **Implicit Actions — firma aggiornata:** le lambda di `_IMPLICIT_ACTIONS` ora ricevono `(text, reply)` per avere il contesto del turno corrente se necessario. Il salvataggio implicito ("ho salvato") è stato valutato e rimosso — il Passive Learner copre già il caso con domain assignment corretto e senza rischio di false positive sul pattern.
- **Fix Dream Engine demotion:** quando un insight viene retrocesso a `candidate`, il `convergence_count` viene resettato a 1 — evita che un insight demotivato riparta con un conteggio gonfiato.

### V2.9 — Consolidation Quality Gate

- **Deduplicazione semantica nodi loop2e:** prima di salvare un nuovo nodo consolidato, `_loop2e_duplicate_exists()` controlla via KNN se esiste già un nodo loop2e con distanza cosine < 0.15 nello stesso dominio. Previene la proliferazione di nodi quasi identici tra cicli notturni successivi (es. 7 nodi ridondanti su "intelligenza artificiale" → 1 nodo ricco).
- **Token sintesi 300 → 600:** il limite precedente troncava sistematicamente le sintesi a metà frase. Con 600 token Qwen3.6 può produrre 5 frasi dense invece di 4.
- **Strip timestamp dalla sintesi:** regex `\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}` rimuove i timestamp raw dalle memorie sorgente prima di passarle al LLM. Elimina artefatti tipo "Le date di riferimento sono 2026-04-28 22:39:49" nelle sintesi.
- **requires_verification e rischio consolidati:** il Loop 2e esclude a monte le fonti `requires_verification=True`; un consolidato può comunque risultare `requires_verification=True` se la sintesi contiene numeri o misure. I nodi `loop2e` salvano inoltre `consolidation_risk` (`ok` / `watch` / `high`) calcolato dalle fonti (`audit_flag`, `superseded_by`, fonti mancanti) per rendere auditabile la genealogia della sintesi senza cancellazioni automatiche.
- **Web search → memoria persistente:** ogni ricerca web andata a buon fine viene salvata automaticamente in Redis con `source="web"`, TTL 60 giorni (sliding window su recall) e `requires_verification=True` forzato — fonte esterna, citata sempre con cautela. Disponibile immediatamente per il RAG. Esclusa da Loop 2e (non viene consolidata con memorie personali).
- **SpeakerAuth bypass in modalità interprete:** quando la modalità traduttore bidirezionale è attiva, il controllo identità vocale viene sospeso automaticamente — le voci esterne (clienti, colleghi) sono attese e autorizzate implicitamente dall'utente che ha attivato la modalità. Alla chiusura ("Fine traduzione") il SpeakerAuth torna attivo.
- **Loop 2d TTL floor 30 giorni:** le memorie con recalled_count ≥ soglia venivano estese di `ttl_days` (7 per gli episodi), rientrando nella finestra di controllo ad ogni ciclo successivo. Fix: `max(ttl_days, 30)` — gli episodi molto richiamati escono dalla finestra per 30 giorni invece di rientrarci ogni ora.

### V2.8 — Loop 2e Memory Consolidation

- **Loop 2e — consolidamento semantico notturno:** implementato in `DreamEngine._consolidation_pass()`. Scansiona le memorie con recalled_count ≥ 3 e requires_verification=False, le raggruppa per dominio via KNN, genera un nodo sintetico con Qwen3.6 (temperatura 0.2, max 300 token, dati numerici preservati). Ogni cluster è identificato da fingerprint SHA (sorted UUIDs) salvata in `euri:loop2e:processed` per evitare ri-consolidazioni. Gira automaticamente nel ciclo onirico max una volta ogni 24h; forzabile con `test_consolidation.py`. Prima esecuzione reale: 6 consolidazioni in domini chimica polimeri, controllo qualità, intelligenza artificiale, produzione industriale, informatica, gestione progetti — cluster da 3 a 6 memorie sorgente ciascuno.
- **Fix KNN con decode_responses=True:** la query vettoriale FT.SEARCH con `query_params={"vec": bytes}` fallisce silenziosamente quando il client Redis ha `decode_responses=True`. Risolto creando una connessione raw temporanea (`decode_responses=False`) solo per la fase KNN — stesso pattern già usato in `_search_semantic`. Causa radice: il client decodifica automaticamente le chiavi di risposta ma non tollera bytes nei parametri di input.
- **Fix filtro cluster:** `recalled_count` non è nel schema RediSearch, quindi `return_fields` tornava sempre 0. Risolto con un dict `qualified_by_id` pre-costruito dalla scan JSON (step 1) usato per filtrare i vicini KNN — nessuna modifica all'indice richiesta.
- **`test_consolidation.py`:** script autonomo che forza `_consolidation_pass()` bypassando il timer idle, mostra before/after e il contenuto dei nodi sintetici generati.

### V2.7 — Ricerca Memoria 3-Livelli + SpeakerAuth Monitoring

- **Ricerca memoria identifier-first:** `search_memories()` ora opera a 3 livelli: (1) estrazione identificatori dalla query (acronimi, codici lotto, numeri decimali) → keyword search diretta in Redis; (2) domain-gated KNN semantico; (3) hybrid fill con `_search_hybrid`. Garantisce che fatti tecnici specifici (MFI lotto, concentrazioni DCP, codici progetto) non vengano sepolti da memorie semanticamente centrali già consolidate nello stesso dominio. Test automatizzato end-to-end: 3/3 storage, recall semantico, pipeline LLM.
- **SpeakerAuth similarity logging:** similarity score portato da DEBUG a INFO — visibile nel log normale per monitorare la soglia in produzione e calibrarla su voci simili (es. colleghi con timbro analogo).
- **`test_memory.py`:** script autonomo di test mnemonico che inietta fatti sintetici, verifica storage Redis, recall semantico e pipeline LLM completa, poi pulisce. Rilanciabile in qualsiasi momento.

### V2.6 — Quality Audit + Numerical Verification + Dream Engine Format

- **Audit qualità memorie passive:** campione 50 memorie valutato da Stefano → 52% accurate, 22% false, 26% generiche. Eliminate 3 memorie pericolose con dosaggi errati (veleno operativo in contesto manifatturiero).
- **`requires_verification` flag:** `save_memory()` detecta automaticamente contenuti con numeri, percentuali, dosaggi e unità di misura (regex su cifre+unità). Il campo viene scritto nel documento JSON. In `_build_context()`, le memorie flaggate vengono iniettate nel prompt con il suffisso `[DATO NON VERIFICATO — contiene valori numerici]` — Euri le cita con cautela invece che come fatti certi. Le memorie precedenti senza il campo non sono impattate.
- **Dream Engine — formato strutturato:** riscritto il prompt di `_generate_dream()`. Output ora forzato in tre righe etichettate: "Nel dominio [X] succede: [concreto]", "Nel dominio [Y] succede: [concreto]", "La connessione operativa non ovvia è: [effetto pratico verificabile]". Insight senza tutte e tre le righe vengono scartati prima della promozione. Eliminato il template filosofico precedente che produceva principi astratti formulati in modo elaborato.
- **Audit insight PROMOTED:** 30 insight valutati → 27% genuinamente non ovvi. Difetto principale identificato: template di scrittura uniforme rendeva impossibile distinguere insight profondi da banalità. Il nuovo formato forza la distinzione a monte.

### V2.5 — Memory TTL
- **Memory TTL:** sincronizzazione `r.expireat()` con `expires_at` JSON. Sliding window operativa: ogni recall estende il TTL di 90 giorni. Loop 2a come safety net per memorie pre-fix.

### V2.4 — Stabilità Architetturale + Document Routing + Concorrenza

**Fix concorrenza (bug silenzioso critico):**
- **Race condition passive learner**: `brain._conversation_history` veniva letta senza lock dal passive learner (thread background) mentre `_compress_episode()` (altro thread daemon) poteva rimpiazzare la lista con `self._conversation_history = self._conversation_history[CHUNK:]`. Risultato: `_passive_history_len` si desincronizzava silenziosamente, con un epoch intero di apprendimento perso senza log di errore. Fix: `Brain.history_lock` (threading.Lock) protegge ora tutte le letture e scritture su `_conversation_history` — sia in `respond()` che in `_compress_episode()`, e con snapshot `list(...)` nel passive learner e in `_handle_save_last`.

**State machine timeout:**
- Sostituiti i tre pattern `(dict, float)` sparsi nell'`__init__` (`_pending_todo` + `_pending_todo_ts`, `_pending_write` + `_pending_write_ts`) con la classe `_PendingState(data, timeout)` e metodo `.expired()`. Il timeout è codificato nel costruttore, non nel sito di controllo in `_dispatch`. Pattern uniforme per ogni stato temporaneo futuro.

**`_last_speech_content` TTL:**
- Aggiunto `_last_speech_ts` — il contenuto dell'ultima risposta lunga scade dopo 300 secondi. Prima, una risposta di ore prima poteva essere salvata silenziosamente da un misrecognition STT che triggerava `_SAVE_REPLY_RE`.

**Routing documenti di testo:**
- "Crea un documento di testo con tutti questi valori" non va più a CodeRunner. Rimosso da EXECUTE il pattern `\bcrea[ri]?\s+(un\s+)?(file|riassunto|testo|documento|report)\b` e `document[io]` dalla lista format (riga 160). Estesa `_WRITE_REQUEST_RE` con forme imperative senza "potresti/puoi": `crea (un) documento`, `scrivimi (un) testo`, `generami (un) schema`.
- `_handle_pending_write` ora distingue: task con formati dati strutturati (csv, excel, pdf…) → CodeRunner; task generico di testo → LLM compone il documento dalla conversazione recente → `tool_write_text`. In entrambi i casi il flusso passa per la conferma vocale.

### V2.3 — Embedding Upgrade + Mobile Voice + Memory Coherence + Intent Routing

- **Embedding: MiniLM → multilingual-e5-large**: sostituito `paraphrase-multilingual-MiniLM-L12-v2` (384-dim) con `intfloat/multilingual-e5-large` (1024-dim). Encoding asimmetrico: `"query: "` per ricerche/classificazione, `"passage: "` per il salvataggio in Redis. Migrazione completa: 306 memorie + 92 insight ri-embeddati, indici Redis ricreati a DIM=1024, fingerprint Welford resettate.
- **WebRTC mobile voice (iOS Safari)**: risolto il problema "in ascolto ma nessun frame audio" su iPhone. Causa radice: con `WebRtcMode.SENDONLY`, iOS Safari non attiva il proprio encoder audio. Fix: `WebRtcMode.SENDRECV` + silence frame (`np.zeros_like(arr)`) — nessun echo, VAD invariato.
- **Memory coherence**: system prompt aggiornato — la conversazione corrente è memoria quanto Redis. Fix per "Non ho niente in memoria" su fatti discussi in sessione.
- **Ricerca temporale additiva**: `_build_context` ora parsa riferimenti temporali italiani ("ieri", "5 maggio", "lunedì", "due giorni fa" ecc.) e prepend le memorie Redis di quel periodo al contesto. Implementata via filtro numerico `@created_at` su RediSearch — additiva, non restrittiva.
- **AdaptiveClassifier disabilitato**: con e5-large (1024-dim) il Welford aveva stessa latenza del LLM (~400ms) con falsi positivi sistematici. Routing ora: regex (0ms) → LLM Gemma (600ms). Fingerprint Redis puliti.
- **Prompt LLM intent riscritto**: definizioni precise per EXECUTE (solo hardware esplicito), SEARCH (memoria interna vs internet), COMPLETE (aggiunto con guard anti-narrazione), WEB_SEARCH, SAVE_TODO, SAVE_MEMORY, CHAT.
- **COMPLETE migrato a LLM**: rimossi i pattern regex ambigui ("ho fatto X", "l'ho fatto") — il LLM distingue narrazione da completamento task usando il contesto. Restano nel regex solo le utterance isolate inequivocabili.
- **Guard manifatturiero EXECUTE**: se la frase contiene termini chimici/analitici (XRF, talco, carbonato, MFI...) senza termini di sistema, EXECUTE viene bloccato a prescindere dal classificatore.
- **RESTORE_ALERTS e SHUTDOWN** esentati dal blocco silence mode — si può uscire dalla modalità silenziosa a voce anche se Euri ignora tutto il resto.
- **SpeakerAuth**: rigetto voci non riconosciute silenzioso (rimossa risposta vocale "prendo ordini solo da Stefano").
- **`_build_context` semantic fix**: `search_memories` e `search_insights` ora ricevono il testo completo invece del join di keyword — e5-large e `assign_domain` lavorano su linguaggio naturale.

### V2.2 — Thinking nei Loop Cognitivi + Qwen3.6 Dream Engine
- **Architettura dual-model**: `DREAM_OLLAMA_MODEL = "qwen3.6:35b"` separato da `OLLAMA_MODEL`. Gemma4 26B per la conversazione vocale (latenza < 2s), Qwen3.6 35B per i cicli onirici notturni (nessun vincolo di latenza, ragionamento astratto superiore).
- **Prompt analogico in 3 passi** (`_generate_dream`): astrazione logica → ricerca della dinamica condivisa → formulazione del principio generale. Evita connessioni superficiali senza forzare un dominio specifico.
- **Timeout LLM alzato a 200s** nel Dream Engine (Qwen3.6 impiega ~85-150s per il judge; il precedente 90s era troppo vicino al limite).
- **Contesto temporale relativo nelle memorie**: ogni memoria iniettata nel contesto ora include l'età relativa — `[chimica polimeri | 3 settimane fa]` invece di `[chimica polimeri]`. Euri sa quando ha imparato ogni cosa e può ragionarci sopra spontaneamente. Stesso meccanismo nel Dream Engine: le memorie portano il loro `created_at` nel prompt del sogno, abilitando insight evolutivi oltre agli isomorfismi strutturali.
- **Silent Chat integrata nel Passive Learner**: la chat testuale ora chiama `log_conversation()` e trigger l'estrazione passiva ogni 6 messaggi — stessa pipeline del voice daemon.
- **Fix Dream Engine hang notturno**: wrapper `_ollama_chat()` con `ThreadPoolExecutor` — se Ollama non risponde entro il timeout il ciclo viene abortito pulitamente.
- **Fix intent router**: 3 pattern regex tightened per evitare falsi positivi su linguaggio manifatturiero (`risultato di`, `percentuale di`, `monitoraggio`).
- **Episodic Compression (Layer 0)**: ogni 30 messaggi, i 20 più vecchi vengono compressi in un episodio e iniettati come sistema message nelle chiamate successive. TTL 7 giorni.


- **Thinking attivo (Loop 2b)**: `_generate_dream()` usa `think=True` con `num_predict=2000`. Prima il cap di 100 token troncava la risposta dopo il ragionamento interno; ora Qwen3.6 ragiona liberamente prima di formulare l'insight.
- **Thinking attivo (Loop 2a)**: `generate_reflection()` usa `think=True` con `num_predict=1000`. Il consolidamento silenzioso delle memorie produce sintesi più accurate.
- **Thinking attivo (Passive Learner)**: `extract_passive_memories()` usa `think=True` con `num_predict=2000`. L'estrazione di fatti dalla conversazione è più precisa e selettiva.
- **Thinking attivo (TEACH)**: `summarize_knowledge()` usa `think=True` con `num_predict=2000`. La sintesi delle sessioni di insegnamento esplicito è più fedele e completa.
- **LLM Judge in Loop 2c**: aggiunto `_llm_judge_same_insight()` con `think=True`. La promozione degli insight ora usa un sistema a due livelli — vettore cosine (< 0.15: certo) + giudizio LLM ragionato (0.15–0.40: zona grigia) — invece del solo embedding superficiale.
- **Path vocale invariato**: `respond()`, `decide_tool_call()`, `translate()` e tutti i path real-time restano con `think=False` per preservare la latenza.
- **Fix critico Domain Gating** (`domain_gater.py`): il validatore rifiutava qualsiasi etichetta con uno spazio, mandando l'80% delle memorie a `"generale"` e svuotando di efficacia il RAG domain-gated. Ora i domini a due parole (es. "chimica polimeri", "stampaggio iniezione") vengono accettati correttamente.
- **Executor regex CodeRunner** (`executor.py`): pattern estesi per riconoscere più formati file e comandi vocali; sentinella `__USER_TEXT__` per passare la frase originale al task `run_code`.

### V2.1 — CodeRunner Data Orchestrator
- **CodeRunner** (`agent/code_runner.py`): SecurityScanner AST + Subprocess sandbox interrompibile.
- **3 nuovi tool** nell'Executor: `run_code`, `analyze_image`, `list_data_files`.
- **Visione Artificiale**: analisi immagini tramite Gemma 4 Vision (multimodale, offline).
- **Formati supportati**: PDF, Excel, LibreOffice (ODS/ODT/ODP), CSV, JSON, TXT, immagini.
- **Fix Dream Engine**: corretto bug `time.monotonic()` → `time.time()` per PC con sospensione.
- **Cartelle I/O**: `~/Scrivania/dati_per_Euri/` → `~/Scrivania/scambio_dati/`.

### V2.0 — Sistema Cognitivo Adattivo
- Welford Adaptive Classifier su Redis.
- Dream Engine (sogni onirici e insight notturni).
- Integrazione bidirezionale Obsidian (Vault + Dropzone).
- Passive Learner (apprendimento implicito).
- RediSearch full-text + VECTOR KNN.

### V1.0 — Voice Assistant
- STT con faster-whisper CUDA.
- TTS con sherpa-onnx + Piper.
- RAG base su Redis.
- Gate visivo con OpenCV.
