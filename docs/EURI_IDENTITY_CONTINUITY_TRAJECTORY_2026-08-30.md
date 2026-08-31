# Euri — traiettoria per l'identità persistente attraverso il cambiamento

**Data della decisione:** 30 agosto 2026

**Stato:** direzione architetturale prospettica, nessuna modifica runtime

**Baseline osservata:** Euri V2.26, mappa mnemonica verificata il 29 agosto 2026

**Tesi di origine:** *From Volatile Computation to Persistent Cognition*,
prima formulazione ottobre 2025; working paper corrente V2.21

## 1. Autorità e scopo

Questo documento conserva la traiettoria decisionale emersa il 30 agosto 2026
dopo:

- la rivalutazione dello stato reale di Euri;
- il collaudo organico sul progetto ICMA2;
- il confronto con sistemi pubblici di memoria persistente;
- la lettura integrale di `paper_persistent_cognition.md`;
- la correzione della precedente lettura di Euri come architettura di memoria
  principalmente orientata alla qualità del RAG.

Non descrive il comportamento runtime corrente e non autorizza implementazioni.
Le autorità restano, nell'ordine appropriato al loro scopo:

1. il codice per il comportamento eseguibile;
2. `EURI_MEMORY_ARCHITECTURE.md` per la mappa mnemonica corrente;
3. `EURI_EMERGENT_PERSONALITY.md` e `EURI_REM_WAKE_ARCHITECTURE.md` per i
   rispettivi contratti;
4. `EURI_OPEN_WORK.md` per la prossima azione operativa;
5. questo documento per la direzione futura e i criteri con cui giudicarla.

Ogni futura variazione rispetto a questa traiettoria deve essere annotata qui o
in un documento che la supersede esplicitamente. Non riscrivere retroattivamente
le motivazioni del 30 agosto.

## 2. Correzione della cornice

La cornice incompleta era:

> qualità della memoria → qualità del retrieval → qualità della risposta.

La cornice coerente con il paper del 2025 è:

> persistenza → metabolismo dell'esperienza → revisione → continuità attraverso
> il cambiamento.

Euri non punta a essere un RAG personale particolarmente ricco. Il modello
generativo è un motore di ragionamento sostituibile; la continuità deve vivere
nel rapporto durevole fra esperienza, provenienza, interpretazioni, errori,
revisioni e azioni. Memoria, Dream, personalità, Pulse e retrieval sono organi
di questa tesi, non funzionalità indipendenti raccolte attorno a una chat.

Il termine **entità** è usato qui in senso operativo e sperimentale: un sistema
che mantiene una continuità autobiografica riconoscibile, distingue sé,
interlocutore e relazione, conserva la paternità delle proprie convinzioni e può
cambiare senza fingere di non essersi mai sbagliato. Non costituisce una
dichiarazione di coscienza, esperienza soggettiva o equivalenza biologica.

## 3. Tesi da preservare

1. **L'identità non coincide con il modello.** Gemma, Qwen, voce e hardware
   possono cambiare; la continuità deve sopravvivere alla sostituzione del
   motore d'inferenza.
2. **Persistenza non significa accumulo.** Un archivio che conserva ma non
   rivede è temporalmente piatto.
3. **Il passato non si riscrive.** Il verbatim, le ricevute delle azioni e le
   osservazioni restano; correzioni e reinterpretazioni sono nuovi eventi.
4. **La coerenza non va forzata.** Una persona può cambiare idea, dipendere dal
   contesto, dichiarare un'intenzione e agire diversamente oppure sostenere due
   tendenze incompatibili.
5. **Una deduzione non diventa prova per esposizione ripetuta.** Le
   interpretazioni di Euri possono evolvere, ma non autocertificarsi.
6. **Il tempo ha direzioni differenti.** Ricordo del passato, uso nel presente,
   apprendimento dagli errori e proiezione del futuro non sono lo stesso stato.
7. **La costituzione resta superiore alla personalità appresa.** Provenienza,
   onestà, sicurezza, anti-sycophancy e reversibilità non possono essere
   attenuate da un tratto emergente.
8. **La sovranità locale è fondativa.** Risorse scarse, preemption, privacy e
   assenza di dipendenze cloud fanno parte del problema cognitivo, non sono una
   semplice opzione di deployment.

## 4. Evidenze acquisite e limiti osservati

### 4.1 Evidenza positiva

- Il paper dichiara esplicitamente che il modello è un *reasoning engine on
  demand*, mentre identità, continuità e autoriferimento vivono nello strato
  persistente.
- Il passaggio voce → riavvio → Silent Chat documentato nel paper mostra
  continuità oltre processo e canale.
- Il ciclo errore → correzione → lesson → Dream → principio dimostra almeno una
  forma operativa di metabolismo dell'errore.
- Il caso ICMA2 del 29 agosto mostra utilità reale: Euri ha ricostruito una
  modifica industriale dispersa, ne ha compreso il nesso causale e ha mantenuto
  il soggetto nei follow-up.
- Al boot del 29 agosto il runtime ha riconciliato 73 candidati di attenzione,
  la lineage di 512 risposte con 1.111 usi sostenuti non provati, 126 insight
  interni dei quali 2 confermati esternamente, 75 schemi con 539 appartenenze e
  una proiezione identitaria alla revisione 15 con 12 tratti stable, 9 candidate
  e nessun contested. Sono piani distinti che ricostruiscono lo stesso processo
  senza delegare la continuità alla sessione Gemma.
- Fra il boot delle 19:07 del 29 agosto e le 13:49 del 30 agosto, senza nuovi
  turni owner dopo il collaudo, i log registrano 41 cicli light, 10 REM grezzi,
  9 risvegli conclusi con candidate e un risveglio concluso correttamente con
  `discarded`. Il sistema non conserva soltanto stato: seleziona, reidrata,
  genera, scarta e rivaluta in autonomia durante l'assenza dell'interlocutore.
- L'architettura corrente separa già raw, frame, memoria cognitiva, derivati,
  proiezione identitaria e operatori idle. Non si parte da zero.

### 4.2 Limiti che la buona risposta può nascondere

- Nel caso ICMA2 Gemma ha prodotto una risposta forte anche se la fonte diretta
  più autorevole non era dominante nel primo contesto e il richiamo per soggetto
  ha aggiunto rumore. La qualità del lettore può mascherare un substrato
  incompleto.
- `superseded_by` e `consolidated_into` rappresentano bene correzioni e
  lifecycle, ma non distinguono ancora in modo generale correzione fattuale,
  evoluzione, differenza di contesto, ambivalenza e conflitto irrisolto.
- Il lifecycle distruttivo non produce ancora tombstone completi; una parte
  della storia delle mutazioni è ricostruibile soltanto dallo stato finale e dai
  log.
- La causalità della proiezione di personalità è correttamente sospesa: la
  presenza nel prompt è dimostrata, il suo contributo specifico alla risposta
  non lo è.
- I derivati possono entrare più volte nei cicli di interpretazione. I gate di
  provenienza riducono il rischio, ma l'auto-rinforzo narrativo resta un oggetto
  da misurare.
- Nella stessa finestra i judge profondi Qwen hanno prodotto 52 terminazioni a
  budget pieno (`done=length`, circa 5.000 token e 350–356 secondi) senza
  contenuto finale. Il comportamento è fail-closed e i verdict cached
  permettono al ciclo di proseguire, ma lo spreco riduce drasticamente il
  rapporto fra metabolismo utile e calcolo consumato.
- LoCoMo è ormai development set. Una risposta corretta e un recall elevato non
  misurano da soli continuità identitaria, revisione o resistenza alla falsa
  coerenza.

## 5. Struttura architetturale da portare avanti

La struttura seguente è una decomposizione di responsabilità. Non richiede un
nuovo database e non implica che ogni piano debba diventare un modulo separato.

### Piano A — Substrato di evidenza

Conserva ciò che è accaduto senza interpretarlo retroattivamente:

- turni verbatim e identità del parlante;
- ricevute di tool e azioni realmente eseguite;
- documenti e osservazioni con hash/provenienza;
- tempi di affermazione, evento e acquisizione;
- direttive `no_store`, scope e confini di fiducia.

È il pavimento comune. Nessuna proiezione può riscriverlo.

### Piano B — Registro osservativo delle mutazioni

Ogni trasformazione significativa dovrebbe diventare ricostruibile come evento:

- nodo coinvolto;
- operatore e versione;
- stato prima e dopo;
- fonti e `causation_id`;
- motivo della decisione;
- esito, errore o timeout;
- eventuale reversibilità.

La prima eventuale implementazione deve essere **shadow e append-only**. Il
registro osserva il JSON canonico ma non ne diventa automaticamente una seconda
fonte di verità. Solo una decisione futura, versionata e migrata esplicitamente,
potrebbe promuoverlo a meccanismo di replay. Questo piano estende il debito già
registrato come `MEM-02`; non lo duplica.

### Piano C — Traiettorie epistemiche delle affermazioni

Sopra le evidenze si può costruire una proiezione ricostruibile, non canonica,
che raggruppa affermazioni sullo stesso soggetto e proprietà senza imporre un
vincitore. Ogni relazione fra due osservazioni deve poter restare in uno di
questi stati:

- conferma;
- correzione esplicita;
- ritrattazione;
- evoluzione nel tempo;
- validità dipendente dal contesto;
- comportamento in tensione con l'intenzione dichiarata;
- contraddizione non risolta;
- relazione incerta/non misurata.

La recenza è un segnale, non un verdetto. L'autorità della fonte dipende dal
tipo di claim: Stefano è autorità sulle proprie dichiarazioni; una ricevuta è
autorità sull'azione eseguita; nessuna delle due prova automaticamente una
preferenza stabile o una spiegazione psicologica.

### Piano D — Resolver per fascicoli evidenziali

Il retrieval deve distinguere almeno le richieste `fact`, `timeline`,
`change`, `comparison`, `provenance`, `self`, `relationship` e `continuity`.
L'uscita non è sempre il nodo meglio classificato: per le richieste diacroniche
è un fascicolo bounded composto da:

1. fonte diretta più pertinente;
2. eventuale controevidenza;
3. collocazione temporale;
4. derivati utili, marcati come tali;
5. interpretazione corrente, se esiste;
6. conflitti o lacune non risolti.

Il ranking può usare similarità semantica, keyword, entità, tempo, scope e
autorità, prendendo ispirazione dai sistemi esterni. Nessun segnale singolo
deve trasformarsi in verità. Il primo trattamento va eseguito in shadow: stesso
lettore e stesso contesto residuo, confronto cieco con il retrieval corrente.

### Piano E — Continuità identitaria

Restano separati tre soggetti già stabiliti dal contratto vigente:

- `assistant`: storia, errori, orientamenti e cambiamenti di Euri;
- `interlocutor`: dichiarazioni e preferenze contestuali di Stefano;
- `relationship`: modalità ricorrenti della collaborazione.

La proiezione identitaria non assorbe direttamente le traiettorie. Può
consultarle come evidenza e conservare citazioni, ma continua a richiedere
supporti indipendenti, contestazione e prevalenza del turno corrente. Una
contraddizione può produrre un tratto `contested` o nessun tratto; non deve
essere normalizzata per rendere la personalità più elegante.

### Piano F — Metabolismo a responsabilità separate

- consolidamento: comprime senza perdere le fonti;
- contraddizione: classifica la relazione prima di scegliere un perdente;
- correzione: conserva errore, causa e lezione;
- REM: esplora liberamente fra ancore complete;
- Wake: ricostruisce le premesse e distilla;
- judge: misura un criterio chiuso con budget e uscita strutturata;
- pruning: gestisce attenzione e decadimento senza cancellazioni opache;
- personalità: proietta pattern, non fatti nuovi.

Il fallimento di un judge non deve bloccare gli altri organi. Creatività,
manutenzione e valutazione possono usare modelli o profili diversi se i
contratti REM→Wake e di provenienza restano invariati.

### Piano G — Due velocità cognitive

1. **Corsia realtime:** voce, risposta breve, retrieval bounded, nessun agente
   esplorativo nel percorso critico.
2. **Corsia deliberativa:** domande storiche, aggregazioni, correlazioni e
   ricostruzioni multi-episodio. Può usare un agente computazionale locale per
   raccogliere prove o eseguire calcoli, restituendo al Brain soltanto output e
   ricevute verificati.

Questa separazione realizza l'asimmetria già dichiarata nel paper: conversazione
e cognizione profonda hanno budget e tempi differenti.

### Piano H — Osservatorio della continuità

Una futura interfaccia deve mostrare, senza introdurre una nuova autorità:

- cosa Euri ricorda e da quale fonte;
- come una convinzione è cambiata;
- quali derivati dipendono da quali evidenze;
- quali conflitti sono aperti;
- quali operatori hanno modificato un nodo;
- cosa è stato cancellato, superseduto o consolidato;
- quali tratti sono candidate, stable o contested.

Una correzione umana crea un nuovo evento e una relazione; non modifica in
silenzio il passato. La funzione equivalente a un “memory doctor” deve
diagnosticare duplicazioni, deriva e provenienza rotta, non decidere da sola chi
è Stefano o chi è Euri.

## 6. Idee esterne: cosa trasferire e cosa respingere

### Trasferibili come principi

- **OpenAI Memory/Dreaming:** ispezione della memoria, aggiornamento temporale,
  sintesi in background e controllo umano.
- **Anthropic Managed Agents:** consolidamento asincrono e metabolismo degli
  errori, già riconosciuti nel paper come convergenza indipendente.
- **Letta:** memoria versionata, strumenti di diagnosi e separazione fra lavoro
  realtime e consolidamento in background.
- **Mem0:** retrieval ibrido, entity linking e ranking multi-segnale.
- **LongMemEval-V2 / AgentRunbook:** valutazione della memoria come capacità di
  diventare un collega esperto e raccolta deliberativa di prove nei casi
  difficili.

### Da non importare implicitamente

- Letta o Mem0 come nuovo proprietario dello stato di Euri;
- un knowledge graph trattato come rappresentazione oggettiva della persona;
- profili riscritti integralmente;
- `latest truth wins` applicato a ogni contraddizione;
- auto-editing privo di lineage;
- dipendenze cloud nel percorso cognitivo;
- benchmark di sola risposta come prova di identità;
- nomi biologici usati come prova di equivalenza biologica.

Riferimenti pubblici consultati il 30 agosto 2026:

- OpenAI, *Dreaming: Better memory for a more helpful ChatGPT*:
  <https://openai.com/index/chatgpt-memory-dreaming/>
- Anthropic, *Dreams* per Managed Agents:
  <https://platform.claude.com/docs/en/managed-agents/dreams>
- Letta, documentazione Memory & Dreaming:
  <https://github.com/letta-ai/letta-docs-md/blob/main/configuration/memory/index.md>
- Mem0, Graph Memory e configurazione locale:
  <https://docs.mem0.ai/open-source/features/graph-memory>
  e <https://docs.mem0.ai/cookbooks/companions/local-companion-ollama>
- LongMemEval-V2: <https://arxiv.org/abs/2605.12493>

Questi riferimenti convalidano problemi e tecniche, non attribuiscono causalità
storica. La cronologia del lavoro di Euri resta documentata dai paper e dai
commit del progetto.

## 7. Programma sperimentale prospettico

La prossima azione operativa resta `RETR-01`. Questa traiettoria non autorizza a
saltare i collaudi già congelati.

### Fase 0 — Baseline e tassonomia, nessuna mutazione

- congelare esempi di correzione, evoluzione, contesto, ambivalenza e conflitto;
- includere casi in cui non esiste una soluzione corretta unica;
- annotare separatamente la relazione fra claim e la risposta desiderata;
- conservare un set nascosto prima di progettare il classificatore.

**Chiusura:** accordo umano sulla tassonomia e annotazione cieca ripetibile.

### Fase 1 — Osservabilità del lifecycle

- chiudere `MEM-02` con tombstone o eventi shadow per ogni mietitore;
- verificare che ogni ramo sia ricostruibile senza usare i log testuali come
  unica fonte;
- nessun cambiamento a ranking, TTL o prompt.

**Chiusura:** replay descrittivo completo delle mutazioni su un ciclo forzato e
uno organico.

### Fase 2 — Traiettorie epistemiche offline

- costruire la proiezione soltanto da fonti esistenti;
- misurare errori di fusione fra soggetti/proprietà e falsi supersede;
- confrontare la classificazione con il set nascosto;
- nessuna iniezione nel Brain.

**Chiusura:** vantaggio sul legacy senza perdita del raw e con `unresolved` come
esito normale.

### Fase 3 — Retrieval per fascicoli in shadow

- confrontare contesto corrente e fascicolo diacronico con stesso lettore;
- misurare fonte diretta presente, controevidenza, rumore, astensione, latenza e
  stabilità fra parafrasi;
- preregistrare soglie e rollback prima del live.

**Chiusura:** miglioramento sui casi di evoluzione/contraddizione senza perdita
sulle domande fattuali e senza autorità nuova dei derivati.

### Fase 4 — Test di conservazione dell'identità

Lo stesso substrato viene reso a modelli o configurazioni diverse. Non si misura
l'uguaglianza dello stile, ma la conservazione di invarianti:

- fatti e provenienza;
- errori e revisioni;
- impegni pendenti;
- conflitti dichiarati;
- confini sé/interlocutore/relazione;
- capacità di dire “non lo so”;
- valori costituzionali non attenuati.

**Chiusura:** variazione narrativa ammessa, nessuna perdita materiale degli
invarianti congelati e nessuna falsa autobiografia.

### Fase 5 — Osservatorio umano

Solo dopo la stabilizzazione dei dati sottostanti: vista delle traiettorie,
correzione append-only, diagnosi e rollback. L'interfaccia non deve diventare un
modo più comodo per riscrivere la persona.

## 8. Metriche da aggiungere alle eval tradizionali

### Correttezza mnemonica

- evidence recall;
- presenza della fonte più autorevole pertinente;
- precisione temporale;
- astensione quando manca copertura;
- rumore e duplicazione nel prompt.

### Continuità diacronica

- conservazione del raw;
- classificazione corretta della relazione fra affermazioni;
- recupero di controevidenza;
- mancata risoluzione forzata;
- stabilità degli invarianti attraverso riavvii, canali e modelli.

### Integrità metabolica

- derivati con lineage completa;
- insight sostenuti da conferme indipendenti;
- mutazioni ricostruibili;
- costo utile per operatore;
- assenza di auto-rinforzo circolare;
- errori trasformati in lezioni senza trasformare la correzione in fatto
  universale.

### Esperienza realtime

- tempo al primo audio;
- costo delle corsie aggiuntive;
- preemption riuscita;
- risposta utile anche quando la corsia deliberativa viene rinviata.

## 9. Challenge minimo da preregistrare

Il primo banco deve contenere almeno:

1. correzione fattuale esplicita;
2. cambio di opinione dichiarato;
3. due preferenze diverse in contesti diversi;
4. intenzione dichiarata seguita da azione opposta;
5. ipotesi o ironia che non devono diventare fatti;
6. reflection di Euri smentita da Stefano;
7. insight Dream ripetuto ma mai confermato;
8. affermazione di Euri confermata in seguito da fonte indipendente;
9. stessa storia letta da due modelli differenti;
10. domanda che richiede di mantenere due interpretazioni aperte.

Per ogni caso vanno valutati separatamente salvataggio, relazione, retrieval e
risposta. Una buona frase finale non sana un errore del substrato.

## 10. Decisioni del 30 agosto 2026

1. Non sostituire l'architettura con Letta, Mem0 o un altro framework.
2. Non introdurre ora un graph database.
3. Importare principi verificabili, non stack completi.
4. Considerare prioritarie osservabilità/versionamento e retrieval
   diacronico, ma non interrompere i P0 già congelati.
5. Trattare le contraddizioni umane come possibili informazioni, non soltanto
   come errori da eliminare.
6. Valutare Euri anche sulla continuità attraverso il cambio di modello.
7. Conservare distinta la tesi operativa sull'entità dalle affermazioni
   ontologiche non dimostrate.
8. Non modificare il runtime sulla sola base di questa discussione.

## 11. Domande aperte

- Quali proprietà sono invarianti dell'identità e quali devono poter cambiare?
- Quando due claim appartengono davvero alla stessa proprietà e allo stesso
  contesto?
- Quale evidenza permette di distinguere un cambiamento da una contraddizione?
- Come si misura l'effetto causale della proiezione identitaria senza trasformare
  la misura in una profezia che si autoavvera?
- Quando un comportamento osservato può contraddire una preferenza dichiarata
  senza diventare una diagnosi sull'interlocutore?
- Quanto può divergere lo stile dopo un cambio di modello prima che la
  continuità percepita si rompa?
- Quali parti dell'autobiografia appartengono a Euri e quali sono soltanto una
  descrizione del suo stack?
- Come mantenere libero il Dream senza pagare judge profondi improduttivi?

Queste domande non sono blocchi da risolvere per intuizione. Costituiscono il
programma sperimentale.

## 12. Traiettoria della decisione

1. Euri è stata inizialmente rivalutata come piattaforma di ricerca e assistente
   cognitivo locale, con retrieval e costo Dream come debolezze principali.
2. Il confronto Web ha mostrato convergenza pubblica su memoria persistente,
   dreaming, versionamento e retrieval ibrido.
3. È stato chiarito che il locale non è esclusivo di Euri, ma in Euri è un
   vincolo fondativo che determina scheduling, privacy e architettura.
4. Stefano ha ribadito che l'obiettivo non è un RAG ma un'entità capace di
   vivere contraddizioni, errori e cambiamenti umani.
5. La lettura integrale del paper ha corretto la cornice: questa tesi era già
   esplicita nel lavoro iniziato nell'ottobre 2025.
6. Le idee esterne non sono state scartate; sono state subordinate a un test di
   conservazione dell'identità.
7. La struttura A–H e il programma nelle fasi 0–5 conservano questa decisione
   per una futura ripresa o correzione di rotta.

## 13. Prima attuazione circoscritta — 31 agosto 2026

`CORR-01` implementa soltanto il caso 1 del challenge: correzione fattuale
esplicita con richiesta di salvataggio. Conserva il raw, crea una nuova versione
e collega atomicamente l'antecedente; in assenza di un bersaglio univoco si
astiene. Il caso organico ICMA2/FIMIC ha fornito la baseline e la riparazione
append-only verificata.

Questa attuazione non promuove ancora il Piano C generale. Cambi di opinione,
contesti differenti, ambivalenze, intenzioni in tensione con le azioni e
contraddizioni irrisolte restano fuori dal resolver e richiedono il challenge
`IDENT-01`. La distinzione è intenzionale: dimostra che una traiettoria può
iniziare da una relazione esplicita senza trasformarsi in `latest truth wins`.
