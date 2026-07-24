# Piano benchmark della memoria cognitiva di Euri

Stato: intenzione approvata da Stefano il 24 luglio 2026.

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

- definire interfacce adapter/runner;
- creare Redis e Vault temporanei;
- aggiungere guard che rifiutano endpoint personali;
- produrre un test smoke senza LLM.

### Fase 1 — LoCoMo ridotto

- importare legalmente dataset e licenza;
- ingerire una conversazione;
- eseguire poche domande rappresentative;
- verificare trace, scoring e reset;
- produrre il primo report A/B `rag_only` contro `passive_memory`.

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

Non scaricare subito tutti i dataset. Prima creare il contratto
`IsolatedRuntime + ConversationAdapter + QuestionRunner`, con fixture sintetiche
minime e test di non contaminazione. Solo dopo collegare LoCoMo.

