# Piano benchmark della memoria cognitiva di Euri

Stato: Fase 0 e prima A/B ridotta della Fase 1 completate il 24 luglio 2026.

## Scopo

Costruire un banco prova ripetibile che misuri quanto i diversi sottosistemi di
Euri migliorano memoria, aggiornamento delle conoscenze e comportamento
conversazionale rispetto a un RAG tradizionale.

Il benchmark non deve dimostrare soltanto che Euri recupera una frase. Deve
misurare l'intero ciclo:

```text
esperienza → estrazione → memoria → gestione epistemica → recupero
→ uso nella risposta → correzione/conferma → consolidamento
```

## Principi non negoziabili

1. Nessun test usa il Redis personale o il Vault reale di Stefano.
2. Ogni run usa namespace, database o istanza Redis isolata e ripristinabile.
3. Voce, webcam, TTS, agenda e azioni esterne restano disabilitati.
4. I dataset vengono ingeriti attraverso un adapter dichiarato, senza inserimenti
   manuali nascosti nelle memorie.
5. Dataset, configurazione, modello, seed, commit e risultati sono registrati.
6. Ogni profilo usa gli stessi dialoghi e le stesse domande.
7. Le riflessioni interne non valgono come conferme esterne.
8. Una memoria falsa richiamata con sicurezza pesa più di una mancata risposta.

## Suite previste

### 1. LoCoMo

Prima integrazione, perché è piccola, pubblica e adatta a costruire l'adapter.
Misura richiamo conversazionale di lunga durata, ragionamento single-hop,
multi-hop, temporale, domande aperte e sintesi di eventi.

Prima run: un campione ridotto e deterministico. Solo dopo la validazione del
runner si esegue l'intero dataset.

### 2. LongMemEval

Seconda integrazione. Misura:

- estrazione di informazioni;
- ragionamento fra sessioni;
- aggiornamento della conoscenza;
- ragionamento temporale;
- astensione quando la risposta non è presente.

Si userà il runner ufficiale per valutare gli output generati da Euri.

### 3. MemBench

Terza integrazione, particolarmente utile per Euri perché distingue:

- memoria fattuale;
- memoria riflessiva;
- partecipazione diretta;
- osservazione passiva;
- efficacia, efficienza e capacità.

### 4. LoCoMo-Plus

Fase avanzata. Valuta se una memoria modifica opportunamente una risposta quando
il richiamo successivo non condivide le stesse parole della memoria. È il test
più vicino all'obiettivo di Pulse, Initiative e uso contestuale.

## Architettura del banco prova

Creare un'area dedicata, indicativamente:

```text
benchmarks/euri_memory/
  adapters/
  profiles/
  runners/
  scorers/
  fixtures/
  reports/
```

Componenti:

1. `IsolatedRuntime`: prepara Redis temporaneo, configurazione e directory Vault
   temporanea; verifica che nessun endpoint personale sia raggiungibile.
2. `ConversationAdapter`: traduce sessioni e turni del dataset nel formato
   conversazionale di Euri, preservando speaker, timestamp e confini di sessione.
3. `MemoryProfile`: abilita soltanto i componenti previsti dall'esperimento.
4. `QuestionRunner`: interroga Euri dopo l'ingestione senza fornire la risposta
   attesa nel prompt.
5. `TraceCollector`: raccoglie memorie scritte, recall, uso supportato, Pulse,
   correzioni, consolidamenti, tempi e token.
6. `Scorer`: calcola metriche ufficiali e metriche specifiche di Euri.
7. `ReportBuilder`: produce JSON machine-readable e un riepilogo Markdown
   confrontabile fra commit.

## Profili A/B

Eseguire almeno questi profili:

1. `rag_only`: ingestione e retrieval, nessuna formazione automatica.
2. `passive_memory`: RAG più Passive learner e Buttafuori.
3. `consolidation`: profilo precedente più Loop 2a/2e/2h.
4. `full_cognitive`: Dream Engine, confine epistemico, Pulse e Initiative.
5. `full_without_reflection`: ablazione per misurare il contributo e il rischio
   delle riflessioni.
6. `full_without_pulse`: ablazione per distinguere memoria da iniziativa.

I profili devono differire solo nei componenti dichiarati.

## Metriche ufficiali

- accuratezza per categoria;
- exact match/F1 quando previsti;
- giudizio semantico secondo il runner ufficiale;
- retrieval recall@k;
- ragionamento temporale;
- knowledge update;
- abstention.

## Metriche specifiche di Euri

- precisione delle memorie scritte;
- recall delle informazioni importanti;
- falsi ricordi e attribuzioni senza provenienza;
- duplicati evitati e duplicati realmente salvati;
- contraddizioni rilevate;
- smentite che impediscono il riuso del nodo;
- memorie `requires_verification` correttamente mantenute;
- conferme esterne attribuite al nodo giusto;
- riflessioni interne presentate erroneamente come fatti;
- memorie richiamate ma non supportate nella risposta;
- memorie usate nella risposta;
- Pulse parlati, rinviati, scartati e realmente utili;
- latenza p50/p95 per ingestione, retrieval e risposta;
- chiamate LLM e token per domanda;
- crescita del database per sessione;
- stabilità fra run ripetute.

## Esperimenti fondamentali

### Aggiornamento e smentita

Inserire un fatto, aggiornarlo in una sessione successiva e verificare che Euri:

- recuperi la versione temporalmente corretta;
- non fonda indiscriminatamente vecchio e nuovo;
- conservi la provenienza;
- non ri-promuova una smentita tramite sola convergenza interna.

### Partecipazione contro osservazione

Presentare la stessa informazione come:

- dichiarazione diretta dell'utente;
- parlato ambientale;
- deduzione di Euri;
- riflessione notturna.

Verificare che gli stati epistemici restino distinti.

### Riflessione utile contro auto-risonanza

Misurare se Dream e consolidamento migliorano risposte multi-hop senza aumentare:

- falsi ricordi;
- eco delle premesse;
- convergenze circolari;
- sicurezza ingiustificata.

### Pulse utile

Valutare se una domanda proattiva:

- riguarda il focus corrente;
- risolve davvero un'incertezza;
- aggiorna il nodo corretto;
- evita ripetizioni e interruzioni inutili.

## Valutazione e judge

La prima fase può usare scoring deterministico e un judge locale separato dal
modello che risponde. Per risultati confrontabili con pubblicazioni esterne si
useranno anche gli scorer ufficiali delle suite, registrando modello e versione
del judge.

Il judge non deve avere accesso alle memorie interne di Euri, alle evidenze
gold non previste dal benchmark o alla configurazione del profilo.

## Fasi operative

### Fase 0 — Contratto e isolamento

- [x] definire interfacce adapter/runner;
- [x] creare Redis e Vault temporanei;
- [x] aggiungere guard che rifiutano endpoint personali;
- [x] produrre un test smoke senza LLM.

Implementazione: `benchmarks/euri_memory/`. Il runtime crea un processo Redis
separato con porta casuale, verifica PID e marker prima di scrivere e distrugge
l'intero ambiente alla fine. Corpus, prompt e gold sono tipi distinti: answer,
evidence, osservazioni e summary annotate non raggiungono il runner.

### Fase 1 — LoCoMo ridotto

- [x] importare legalmente dataset e licenza;
- [x] validare adapter sull'intero corpus;
- [x] ingerire una conversazione nel runner smoke;
- [x] verificare trace, scoring e reset senza LLM;
- [x] collegare il percorso reale `rag_only`;
- [x] collegare il percorso reale `passive_memory`;
- [x] selezionare un campione QA ridotto, dichiarato e deterministico;
- [x] produrre il primo report A/B `rag_only` contro `passive_memory`.

Il rilascio ufficiale acquisito contiene 10 conversazioni, 272 sessioni, 5.882
turni e 1.986 domande. L'adapter segnala 5 evidence che, dopo la normalizzazione
dei riferimenti multipli, non corrispondono a un `dia_id` presente. Questi difetti
del gold restano visibili nei report.

La selezione `locomo-conv26-s1-s2-q8-v1` contiene 35 turni e 8 domande. La prima
run reale non mostra un vantaggio della memoria passiva: F1 0,370 → 0,356,
evidence recall invariato a 0,750 e accuracy avversariale 1,000 → 0,500. Le tre
memorie passive aggiunte hanno aumentato le chiamate locali da 16 a 27; una
memoria episodica pertinente all'estate ha indotto un over-answer su una domanda
non supportata. Il risultato è diagnostico e non ufficiale, data la dimensione
del campione e lo scorer deterministico interno.

Replica v1 con gli stessi corpus, seed e modello: il baseline `rag_only` è
identico; il trattamento passa da F1 0,356 a 0,364 e l'accuratezza avversariale
torna da 0,500 a 1,000. Questo conferma variabilità nel percorso passivo, non un
vantaggio dimostrato.

La selezione indipendente `locomo-conv42-s1-s2-q8-v1` contiene 51 turni e 8
domande, coprendo tutte le cinque categorie. Risultato: F1 0,187 → 0,162,
exact match 0 → 0, evidence recall 0,625 → 0,625 e accuracy avversariale
1,000 → 1,000. Il Passive learner ha prodotto 10 candidati, salvato 8 memorie
e usato 55 chiamate locali contro 16 della baseline. Anche qui nessun beneficio
misurabile; il campione resta diagnostico, non una valutazione LoCoMo ufficiale.

### Controllo in italiano

È stata aggiunta una localizzazione italiana versionata della selezione v2:
stessi 51 turni, 8 domande, categorie ed evidence ID. Il prompt di risposta e
l'astensione sono localizzati; lo scorer riconosce sia “I don't know” sia
“Non lo so”.

Run pulita del 25 luglio 2026:

- `rag_only`: F1 0,318; evidence hit 0,625; avversariale 1,000;
- `passive_memory`: F1 0,368; evidence hit 0,625; avversariale 1,000;
- delta F1 +0,050, senza delta di retrieval;
- 9 memorie estratte, 6 salvate e 3 classificate come duplicate;
- 48 chiamate LLM locali nel trattamento contro 16 nel baseline.

Il confronto ha isolato due difetti: falsi duplicati fra fatti distinti e
normalizzazione temporale errata di “venerdì scorso”. Entrambi sono ora coperti
da regressioni. La fonte conversazionale prevale sul testo generato,
`event_start` risolve al 21 gennaio 2022 e un eventuale `20/01/2022` viene
corretto prima del salvataggio. La deduplicazione non decide più dalla sola
cosine: richiede soggetto compatibile, copertura completa dei marker informativi
e verdetto LLM `DUPLICATO` esatto.

Replica post-fix dedup/data: baseline F1 0,331, Passive F1 0,405 (+0,074),
evidence hit 0,625 invariato, 9 memorie salvate su 9, zero falsi duplicati e 36
chiamate locali. La run ha anche mostrato che una sintesi poteva includere una
clausola proveniente da un turno non dichiarato in `source_turn_ids`.

La provenienza è ora verificata semanticamente sul testo finale: l'audit può
riparare gli ID incompleti, il nome originale del parlante resta nel contratto e
il Buttafuori passivo non riscrive più i fatti. Replica finale: 7/7 memorie
salvate, zero rifiuti, 4 liste sorgente riparate, F1 0,318→0,325 ed evidence
0,625 invariato. La sonda reale del caso combinato della sceneggiatura corregge
`[25]` in `[25,27]`, cioè `D2:3` + `D2:5`.

L'estrazione atomica a finestre corregge anche l'omissione del genere. Replica
conclusiva: F1 `0,318→0,396`, evidence `0,625→0,875`, avversariale invariata a
`1,000`; q99 passa dall'astensione alla risposta corretta e q207 resta in
astensione. Il nodo del genere cita `[25,27]`. Sono però state salvate 22
memorie con 92 chiamate locali: copertura e costo devono restare metriche
separate.

Il report storico non è stato rigenerato.
Il token F1 EN/IT non va confrontato come misura assoluta perché cambia la
superficie linguistica delle risposte; evidence hit e analisi per domanda sono
il confronto più affidabile.

### Fase 2 — LoCoMo completo

- eseguire tutte le conversazioni e categorie;
- aggiungere ablation dei loop;
- stabilire baseline per commit.

### Fase 3 — LongMemEval

- collegare i 500 quesiti;
- misurare aggiornamento, temporalità e astensione;
- confrontare small/oracle/medium quando sostenibile.

### Fase 4 — MemBench

- distinguere memoria fattuale e riflessiva;
- valutare partecipazione e osservazione.

### Fase 5 — LoCoMo-Plus e Pulse

- misurare uso implicito della memoria;
- aggiungere scenari specifici di iniziativa e verifica epistemica.

## Criteri di accettazione della prima milestone

La prima milestone è completa soltanto se:

1. il benchmark non legge né scrive memorie personali;
2. la stessa run è ripetibile da un singolo comando;
3. il reset elimina soltanto l'ambiente temporaneo;
4. almeno due profili producono output confrontabili;
5. ogni risposta conserva trace di ingestione, recall e uso;
6. il report separa errore di estrazione, retrieval, reasoning e scoring;
7. un test intenzionalmente falso dimostra che l'isolamento funziona;
8. commit, configurazione e modelli sono inclusi nel report.

## Prima azione alla ripresa

Eseguire un'ablation della nuova estrazione: finestra intera contro finestre
sovrapposte, deduplica esatta contro consolidamento sessionale e audit su tutti
i candidati contro soli candidati differiti. Obiettivo: conservare q99,
evidence 0,875 e l'astensione q207 riducendo le 92 chiamate e le 22 memorie
frammentate. Soltanto dopo ampliare LoCoMo.
