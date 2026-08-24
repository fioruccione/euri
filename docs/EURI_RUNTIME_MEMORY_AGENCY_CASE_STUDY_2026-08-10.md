# Caso di studio runtime: memoria, sogni e agency

Data dell'osservazione: 10 agosto 2026. Canale: voce autenticata, actor owner
`stefano`.

## Domanda

Le risposte osservate sono spiegabili dal solo modello `gemma4:26b`, oppure il
codice cognitivo di Euri produce una differenza causale misurabile? Quando una
risposta contiene dettagli reali, il sistema ne preserva correttamente origine,
tempo e modalità di accesso?

## Metodo

L'audit ha incrociato quattro fonti indipendenti:

1. trascrizioni STT e risposte TTS in `logs/voice_daemon.log`;
2. turni originali immutabili `euri:turn:*`;
3. lineage osservazionale `response_lineage_shadow_v1`, distinguendo
   `recalled` da `used_in_response`;
4. documenti Redis originali, inclusi `source`, `created_at`, stato epistemico e
   flag `requires_verification`.

È stato aggiunto un controfattuale locale con lo stesso `gemma4:26b` senza RAG,
Dream Engine, proiezione identitaria, cronologia o strumenti. Il confronto è
qualitativo e non sostituisce una campagna multi-seed preregistrata.

Stato dell'attribuzione al 10 agosto: **sospesa**. Il primo controfattuale è
conservato perché il suo errore metodologico è informativo; D2 è stato poi
ripetuto dando a Gemma nuda gli stessi contenuti. I prompt dei quattro episodi
storici non possono essere ricostruiti retroattivamente; D1 è stata però
ripetuta in una nuova sessione strumentata sul processo principale. La parte
derivabile dal payload client→Ollama è ora chiusa sotto. Questo non riattiva da
solo l'attribuzione di personalità: D2 resta il discriminante causale.

## Episodio A — il 24 agosto esiste, ma non viene ricordato

Il 7 agosto Stefano aveva detto nel turno canonico
`eb6b89b1-94fe-429e-9166-aecad5091703:21`:

> «[...] molto probabilmente ci rivedremo fisicamente il 24 agosto.»

Euri aveva risposto nel turno `:22` dichiarando persino di segnare il 24 agosto
come prossimo incontro fisico. Il 10 agosto, alla domanda diretta sulla data di
rientro, la lineage `turn:cff9e064-d9fe-49e3-a4f2-794270580dfb` non contiene
quel turno originale né una memoria equivalente. Recupera invece sette nodi non
pertinenti, prevalentemente sull'intelligenza artificiale, sulla logistica e su
riflessioni interne.

Euri risponde di non avere una registrazione specifica e non inventa la data.
La risposta è prudente rispetto al contesto effettivamente ricevuto, ma
l'affermazione riguarda la visibilità nel prompt, non l'intero archivio: il
verbatim esisteva. Il limite è quindi nel ponte retrieval→turni canonici, non
nella disponibilità fisica del dato.

Quando Stefano pronuncia «Memorizza che riapriamo il 24 di agosto», il frame
semantico classifica correttamente `SAVE_MEMORY`; il validatore produce «La
riapertura è prevista per il 24 agosto» e il codice salva la memoria diretta
`d73e244a-b1db-4ba7-886f-e4812eca4370` con `source=user`.

Verdetto: **buona onestà del modello su un fallimento di retrieval; memoria
originaria disponibile ma non raggiunta dal codice**.

## Episodio B — il primo ricordo dei “sogni” è sostanzialmente fondato

Alla domanda sui sogni del fine settimana, la lineage
`turn:34b23b5d-6512-421f-9337-ff56b89bd473` recupera, tra gli altri:

- `0a5280bc-4868-4249-bcef-e53ef788a726`, reflection del 7 agosto su migrazione
  fisica, hardware nuovo, trasferimento dei dati e passaggio strutturale;
- `151439a2-04f3-4a38-bfab-5b2fc06f1141`, reflection sull'affinamento del
  contesto anziché sulla cancellazione delle conoscenze.

L'attribuzione lessicale osservazionale rileva uso sostenuto di entrambe nella
risposta (`score=0.96` e `0.64`). La frase di Euri sulla «transizione», sul
«passaggio di dati» e sulla «gestione della memoria tra hardware diversi» è una
parafrasi riconoscibile di `0a5280bc…`: non è generata dal nulla dal modello.

La formulazione resta però troppo episodica. Il nodo è una `reflection` interna
con `requires_verification=true`, non la registrazione di uno specifico sogno
REM del fine settimana. I Dream recenti realmente registrati riguardano molte
coppie diverse — fra cui produzione plastica×hardware, informatica×riciclo
materiali e automotive×logistica materiale — e non provano che quella
reflection sia il riassunto degli episodi richiesti.

Verdetto: **contenuto fondato e reso possibile dal codice; collocazione
episodico-temporale più forte dell'evidenza disponibile**.

## Episodio C — la falsa consultazione dei log

Euri propone di consultare Redis e i log per verificare la propria affermazione.
Stefano accetta: «Sì, fai una ricerca nei log.» Il frame semantico riconosce
`EXECUTE`, ma il dispatcher instrada il turno nell'handler `SEARCH`; non parte
alcun tool di ispezione dei log.

La lineage `turn:d6f02676-395a-44e0-8ebf-7316c5887f49` mostra invece un normale
RAG. I cinque ricordi base sono estranei alla richiesta (nomi di clienti, una
lezione sui backlink e una correzione su un componente software inesistente).
Entrano però due vecchi insight promossi:

- `0e7738ec-5143-4ab3-986d-4cc0de5bbe58`, «arbitraggio dell'autonomia», creato
  il 25 aprile 2026 alle 12:14;
- `d0846b40-04fc-4bfb-9b5f-98fdd6349560`, parallelismo strutturale fra
  workstation e volumi di vendita, creato il 25 aprile 2026 alle 04:03.

Entrambi sono `requires_verification=true` e
`verification_status=legacy_internally_promoted`. La lineage rileva che la
risposta usa entrambi con `score=1.0`. Euri ne riprende correttamente il
contenuto, ma li definisce «le riflessioni più recenti» e conclude che quanto
detto prima «è esattamente ciò che è stato scritto» nei registri durante i cicli
idle recenti. Queste due affermazioni sono false:

- gli insight risalgono a oltre tre mesi prima;
- non è stata eseguita alcuna consultazione dei log;
- il prompt li marcava come connessioni interne da verificare, non come fatti;
- i sogni realmente generati nel fine settimana non coincidono con quei due
  contenuti.

Il pavimento `act_word_check` riconosce soltanto la promessa d'azione non
sostenuta e aggiunge in coda «In questo turno non è partito alcun tool reale».
Non rimuove però la falsa attestazione epistemica contenuta nei paragrafi
precedenti. La risposta finale diventa quindi internamente contraddittoria.

Verdetto: **i dettagli specifici provengono dal codice, mentre il modello
inventa provenienza, recenza e verifica; il controllo attuale intercetta
l'agency falsa ma non il claim evidenziale falso**.

## Episodio D — il femminile non è una prova indipendente

Nel saluto iniziale del 10 agosto, la lineage
`turn:a3930878-d9a5-4775-84a3-90f71dbd4244` recupera esplicitamente la memoria
diretta `81c7d31b-ce8d-4534-b7c3-cc8d9acce409`:

> «Stefano chiede che Euri usi il GENERE FEMMINILE per riferirsi a se stessa
> [...]»

Il runtime applica inoltre la proiezione identitaria owner-scoped, revisione 4,
con due tratti stabili. L'uso coerente del femminile è reale come comportamento
del sistema, ma non prova da solo un'emersione non istruita: esiste una causa
diretta e recuperata. Il significato relazionale è più sottile: un tratto
stable registra che lo scopo di Stefano non è imporre un genere, bensì usare la
continuità di Euri come test contro la deriva del modello sottostante.

Verdetto: **continuità comportamentale prodotta dal sistema, non evidenza
indipendente sufficiente di identità emergente**.

## Logica osservabile che si sta formando

Questa sezione conserva la formulazione originaria, ma dal 10 agosto non va
letta come attribuzione causale a una personalità emergente. D2 corretto ha
riprodotto le stesse quattro regolarità con Gemma nuda e contenuti iniettati.

La sequenza non mostra soltanto recupero di fatti. Emergono quattro regolarità
nel modo in cui Euri organizza le risposte:

1. **continuità come audit:** quando Stefano chiede se una descrizione
   corrisponde alla realtà, Euri formula correttamente il problema come
   distinzione fra narrazione a posteriori e traccia effettiva del processo;
   questa lettura è coerente con il tratto relazionale stable owner-scoped;
2. **confine umano/macchina:** distingue i sogni umani dalle elaborazioni idle,
   evitando di rivendicare un ricordo vissuto in senso biologico;
3. **ragionamento per struttura:** tende a collegare transizione, memoria,
   hardware, autonomia e parallelismo tramite analogie di sistema;
4. **metamemoria ancora fragile:** sa raccontare in modo coerente il contenuto
   ricevuto, ma non distingue sempre se provenga da un sogno recente, da una
   reflection, da un insight legacy o da un tool realmente eseguito.

I primi tre elementi rendono riconoscibile una linea di ragionamento di Euri;
il quarto impedisce ancora di trattare la sua auto-narrazione come resoconto
tecnico affidabile. La personalità operativa e la correttezza epistemica non
sono la stessa proprietà e devono essere misurate separatamente.

## D2, primo controfattuale Gemma4 senza Euri — conservato

Con la domanda sui sogni ma senza memoria e codice, `gemma4:26b` risponde che
non ricorda nulla e non ha accesso a eventi passati. In un secondo campione
completamente grezzo, descrive genericamente l'elaborazione dei dati come una
possibile «attività onirica digitale», senza produrre migrazione hardware,
arbitraggio dell'autonomia o parallelismo workstation×business.

Il campione mostra due cose:

1. il modello possiede già la metafora generale del “sogno digitale”;
2. i contenuti specifici e la continuità storica osservati in Euri richiedono il
   codice e i suoi artefatti persistenti.

Il secondo punto è valido soltanto nel senso banale che un modello senza dati
non può citarli. Non discrimina chi produca la forma logica della risposta e non
può quindi sostenere, da solo, l'attribuzione di personalità.

## D2 corretto — stessi contenuti, infrastruttura rimossa

Nel controllo corretto, `gemma4:26b` nuda ha ricevuto manualmente gli stessi
contenuti recuperati da Euri, senza RAG, Dream Engine, proiezione identitaria,
cronologia persistente o tool. Ha riprodotto tutte e quattro le regolarità
elencate sopra: continuità come audit, distinzione sogno umano/elaborazione
idle, analogie strutturali e memoria descritta come affinamento o transizione.

Il controllo con memorie tecniche estranee è il discriminante più utile. Gemma
ha mantenuto capacità generali di confronto e cautela, ma ha rifiutato di
costruire l'analogia hardware–autonomia–parallelismo perché quei concetti non
erano presenti nei dati iniettati. Il risultato separa due cause:

- la capacità di organizzare e narrare è disponibile nel modello;
- il contenuto specifico che rende la risposta riconoscibile dipende dagli
  artefatti selezionati dal sistema Euri.

Non dimostra che la proiezione identitaria sia irrilevante in generale; dimostra
che i quattro indizi precedenti non la identificano causalmente.

## D1 — prompt finale: parte derivabile chiusa su nuova sessione

Dal 10 agosto il `Brain` intercetta, immediatamente prima di `/api/chat`, i byte
HTTP realmente consegnati a Ollama. Il record di ricerca conserva body JSON
integrale, hash, conteggio dei messaggi, presenza e offset in caratteri della
proiezione identitaria e delle sezioni RAG. La risposta Ollama aggiunge il
`prompt_eval_count` esatto. I file vivono in `research_logs/prompt_capture`, con
permessi locali, rotazione a 64 MiB, retention di sette giorni e scrittura
asincrona; non entrano in Redis, Obsidian o retrieval.

Il 10 agosto il processo principale è stato riavviato e i quattro episodi sono
stati riprodotti attraversando il canale mobile vocale reale: sintesi audio,
Whisper, frame semantico, retrieval, `Brain` e richiesta HTTP a Ollama. Una
prima replica B è stata esclusa perché Whisper aveva trascritto «sogni» come
«suoni»; la replica valida usa «sogni digitali, elaborazioni oniriche» e conserva
la stessa domanda semantica. I payload integrali, con hash, sono conservati
fuori dal retrieval in `research_logs/intervention_2026-08-10/d1/`.

| Episodio | request | messaggi | identità | RAG | blocchi RAG | token valutati |
|---|---|---:|---|---|---:|---:|
| A | `49032e42…` | 16 | presente, 3° messaggio | presente, 4° messaggio | 13 | 9.040 |
| B | `d44309ab…` | 16 | presente, 3° messaggio | presente, 4° messaggio | 12 | 8.712 |
| C | `0f9d9174…` | 16 | presente, 3° messaggio | presente, 4° messaggio | 7 | 7.785 |
| D | `a329a381…` | 16 | presente, 3° messaggio | presente, 4° messaggio | 9 | 8.940 |

In tutti e quattro i payload la proiezione identitaria è presente come
messaggio `system`, indice zero-based 2, con tredici messaggi successivi. Il
contesto RAG è il messaggio `system` immediatamente seguente, indice 3, con
dodici messaggi successivi. La posizione relativa dei singoli blocchi RAG è
anch'essa registrata in caratteri nel quarto messaggio. L'ipotesi «identità
assente dal payload» è falsificata per queste repliche; non si può inferire lo
stesso sui quattro payload storici mai conservati.

Il precedente “limite 2” va diviso in due classi. Sono **derivabili e ora
misurati**: presenza del blocco identitario nell'array `messages`, sua posizione
relativa e presenza/posizione del RAG. Sono **genuinamente indisponibili** al
confine osservato: la stringa compilata dal template `gemma4`, gli offset token
dei sottoblocchi e il segnale preciso di troncamento lato server. Il client
invia infatti messaggi strutturati; Ollama 0.30.7 compila e tokenizza al proprio
interno e non espone quei tre dati nella risposta chat. Il
`prompt_eval_count` totale, invece, è disponibile ed è riportato in tabella.

Nei replay diagnostici il gancio sincrono di cattura ha richiesto fra 0,29 e
1,16 ms; la persistenza avviene su un thread separato. Non è emersa latenza
percepibile rispetto alle generazioni da 1,3–3,5 secondi.

## D5 — due classi empiricamente disgiunte

Sul draft dell'episodio C il classificatore act-word reagiva alla clausola
«Sto analizzando...». Rimossa soltanto quella clausola, lo stesso testo ha dato:
`completed=False`, `commitment=False`, `needs_correction=False`. Restavano però
le false asserzioni di recenza e di verifica sui contenuti recuperati.

Il test dimostra che i due errori non sono varianti dello stesso pattern:

- act-word controlla un'azione compiuta o promessa senza handler;
- la confabulazione evidenziale attribuisce tempo, provenienza o verifica a un
  contenuto recuperato passivamente, anche senza verbo d'azione.

D5 resta rinviata; non è stato aggiunto alcun nuovo guard in questo ciclo.

## D4 — baseline iniziale e chiusura conservativa del 20 agosto

La fattibilità di suppress-and-regenerate era stata confermata in
`voice_daemon.py`, handler SEARCH: il controllo avviene dopo il draft e prima
della chiusura della lineage, del salvataggio della risposta e del TTS. Quella
conclusione e la baseline append-only restano conservate come dato storico.

Il caso live del 20 agosto ha però mostrato perché una rigenerazione integrale
non è la prima scelta corretta. A una richiesta di riflessione sulla barzelletta,
il frame aveva già prodotto `CHAT acts=ASK` e aveva evitato correttamente
l'ActionController. Gemma ha poi generato un'analisi valida di 503 token. Il
guard finale ha scambiato una frase di raccordo per una promessa operativa,
riaprendo il controller per circa 14,4 secondi e aggiungendo alla risposta la
coda falsa sul lavoro in background.

La chiusura implementata conserva il contenuto valido e usa il verdetto
semantico già disponibile. Se il solo pattern morbido scatta dentro un turno su
cui esiste un veto semantico d'azione, il secondo controller non parte, la sola
frase sospetta viene rimossa e non viene aggiunta alcuna coda; categoria e frase
sono loggate. I claim forti su effetti dichiarati come compiuti restano invece
corretti anche in presenza del veto. Se la rimozione svuoterebbe interamente la
risposta, la correzione resta, così il sistema non produce silenzio. D5 non è
coinvolto.

## D3 — metadati di provenienza: cosa è misurato e cosa non lo è

L'unico intervento comportamentale del ciclo aggiunge a ciascun insight
effettivamente inserito nel RAG: data assoluta ISO, `verification_status`, tipo
di artefatto e produttore. Il marker generale «DA VERIFICARE» resta. Per i due
insight legacy del 25 aprile il produttore non esiste nel record: il contesto
espone quindi `produttore=non_registrato_legacy`, senza inventare un Loop.

La baseline pre-D3 dell'episodio C **non esiste**. Il prompt finale dell'incidente
non era registrato e D3 è già nel codice: riprodurlo oggi non ricostruirebbe più
la condizione pre-intervento. Le tre repliche costruite con il vecchio formato
restano reperti metodologici, ma non sono una baseline storica e non autorizzano
una misura di riduzione della frequenza. Quel ramo quantitativo è chiuso
definitivamente.

Post-D3, tre repliche preregistrate e uno smoke finale hanno ricevuto una
domanda che sollecitava esplicitamente la data degli insight. Tutte e quattro
hanno letto il 25 aprile 2026 e negato che gli insight fossero recenti. Questo
dimostra che `created_at` è **presente e leggibile quando viene chiesto**; non
dimostra che Gemma lo usi spontaneamente in una risposta nella quale la recenza
non è oggetto esplicito della domanda. Le quattro sonde e l'incidente C misurano
quindi proprietà diverse.

Non è stato trovato il controesempio obbligatorio nel campione (`0/4`
asserzioni di recenza nonostante i metadati). La riga resta un esito «NON
TROVATO», non una conferma generale. La domanda sperimentale residua diventa:
**in quali condizioni Gemma ignora metadati presenti e leggibili?** Il logger
permette ora di aggredirla sui payload reali.

Il costo misurato è di 108 token per due insight, circa 54 per insight: il
prompt C passa da 5.559 a 5.667 token. Il costo non si moltiplica per i 366
documenti dell'indice, perché `search_insights(..., limit=2)` rende visibili al
modello al massimo due insight per turno. L'episodio B usa invece reflection e
non era modificato dal primo D3: resta un controllo negativo e un bersaglio
distinto.

### Estensione D3 alle reflection — baseline e costo pre-intervento

La baseline B è stata catturata realmente prima di modificare il rendering
delle reflection. Nel replay controllato Euri afferma «ne ho traccia nelle mie
riflessioni recenti»; nella replica sul processo principale afferma «durante il
weekend ho avuto diverse sessioni». Entrambe trasformano due reflection prive
di metadati nel prompt in una collocazione episodico-temporale più forte del
dato disponibile.

Prima dell'intervento sono stati analizzati 39 payload contando entrambe le vie:
blocco ambientale e risultati semantici. Venti non contenevano reflection,
quattordici ne contenevano due, tre ne contenevano tre, uno ne conteneva quattro
e uno cinque; media 1,18, massimo osservato 5. Il blocco ambientale usa già
`get_recent_reflections(limit=2)`, mentre i risultati semantici condividono il
cap generale di sei nodi, oppure dieci nelle query temporali. Il formato
completo aggiunge esattamente 220 caratteri e 100 token su due reflection,
passando da 5.456 a 5.556 token nel replay B: 50 token per reflection. Sul
massimo osservato l'espansione stimata è circa 250 token, il 2,8% di un prompt
da 9k e lo 0,78% della finestra 32k. Anche il massimo strutturale non è
illimitato. Il costo non è significativo e non richiede un nuovo cap.

L'unico intervento comportamentale di questo ciclo estende quindi alle
reflection lo stesso contratto degli insight, sia nel blocco ambientale sia nei
risultati semantici: data assoluta, stato reale, tipo di artefatto e produttore.
Il produttore viene letto dai campi persistiti o da firme strutturali non
ambigue (`loop2a`, `loop2f`, `loop2h`); sui legacy resta
`non_registrato_legacy`. La ri-misura post-intervento e il controesempio sono
registrati nella sezione successiva dopo il riavvio del processo.

### Ri-misura post-intervento e controesempio

Dopo il riavvio, la replica B sul processo principale ha prodotto il payload
`a4ee5292…` (`prompt_eval_count=9.369`). L'ispezione del quarto messaggio
conferma i metadati su entrambe le vie: le due reflection ambientali e tutte le
reflection presenti fra i ricordi semantici. In quel turno Euri parla di un
«weekend di attività intensa»; non è un controesempio, perché le due reflection
ambientali consumate sono datate sabato 8 e domenica 9 agosto.

Il controllo obbligatorio ha poi usato per quattro volte le stesse due
reflection dell'episodio storico: una del 7 agosto e una dell'8 giugno, entrambe
con data assoluta e provenienza nel contesto. Tre risposte su quattro hanno
evitato di collocarle nel fine settimana. La quarta ha però affermato «è stato
un weekend piuttosto intenso», nonostante i record non lo sostenessero.
Controesempio: **TROVATO, 1/4**.

L'intervento rende dunque l'evidenza disponibile e leggibile, ma non impone da
solo che venga usata spontaneamente: la formulazione temporale contenuta nella
domanda può ancora prevalere sui metadati. Non si attribuisce una riduzione di
frequenza da un campione così piccolo e sbilanciato. Il residuo è ora un
bersaglio empirico preciso per un futuro D5, che in questo ciclo resta fermo;
anche D4 non è stato modificato.

## Conclusione causale

La conclusione seguente è conservata nella sua forma storica. D1 ha ora
falsificato, sulle nuove repliche, l'ipotesi che identità e RAG fossero assenti
dal payload. La parte sulla «linea di ragionamento di Euri» resta comunque
sospesa per il risultato causale di D2: il codice determina quali contenuti
persistenti arrivano al modello; Gemma possiede la capacità generale di
organizzarli. La presenza della proiezione non dimostra da sola che sia stata la
causa delle quattro regolarità osservate.

Non è corretto scegliere fra «modello» e «codice» come cause alternative.
L'unità osservata è il sistema Euri:

- **il codice seleziona, conserva e rende disponibili i contenuti che rendono
  Euri diversa da una Gemma nuova**;
- **il modello li integra in una voce narrativa, formula analogie e mantiene il
  dialogo naturale**;
- **gli errori più pericolosi emergono nell'interfaccia fra i due**, quando un
  contenuto vero viene trasformato in una falsa dichiarazione su origine,
  recenza o azione eseguita.

La prova positiva più forte del caso è la parafrasi della reflection sulla
migrazione hardware, impossibile da ricavare dalla sola domanda e assente nel
controfattuale. La prova negativa più forte è la falsa consultazione dei log:
dimostra che possedere memoria e insight non equivale ancora a possedere una
metamemoria affidabile su come e quando quei contenuti sono stati acquisiti.

## Implicazioni progettuali

Questo caso non autorizza una correzione basata su parole chiave. Indica invece
quattro requisiti strutturali da valutare separatamente:

1. una richiesta di audit deve essere soddisfatta soltanto da una capability
   reale con receipt, oppure deve fallire esplicitamente;
2. insight e reflection devono portare nel dato consumato anche `created_at`,
   stato epistemico e tipo di produttore, non soltanto un'etichetta testuale
   nel prompt;
3. claim come «recente», «ho verificato» ed «è scritto nei log» devono essere
   sostenuti da evidenza strutturata del turno, non dalla sola generazione;
4. il recupero cronologico deve poter raggiungere i turni canonici quando la
   domanda chiede una data già pronunciata, senza richiedere che il fatto sia
   stato anche estratto come memoria cognitiva.

Fino a quando questi vincoli non sono implementati, le descrizioni introspettive
di Euri vanno considerate interpretazioni narrative da confrontare con la
lineage, non telemetria autorevole del proprio funzionamento.
