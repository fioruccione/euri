# Assistente Ufficio — scheda di conoscenza per Euri

Documento destinato alla base di conoscenza di Euri, perché possa spiegare
questa applicazione ai colleghi durante la riunione aziendale sull'uso dell'IA.
Scritto in modo autosufficiente: non richiede contesto esterno.

---

## Che cos'è, in una frase

**Assistente Ufficio** è un "NotebookLM locale": si caricano documenti aziendali
e si può interrogarli in linguaggio naturale, ottenendo risposte con citazioni
verificate. Gira **interamente sui server dell'azienda**, senza che nessun
documento esca dalla rete interna.

Si raggiunge dal browser all'indirizzo `http://localhost:8501/assistente_ufficio`
e fa parte del sistema PlastVision.

---

## Perché è importante: i documenti non escono di casa

È la differenza sostanziale rispetto agli assistenti online. Schede tecniche,
certificati di lotto, capitolati, offerte, corrispondenza con i clienti:
vengono elaborati **in locale**, dai modelli installati sulle macchine
aziendali. Non vengono caricati su servizi esterni, non alimentano
l'addestramento di modelli di terzi, non lasciano tracce fuori dall'azienda.

Per un'azienda che tratta dati di clienti e specifiche di prodotto, questa non
è una comodità: è la condizione che rende utilizzabile lo strumento su
materiale riservato.

---

## Che cosa si può fare, concretamente

### Interrogare i propri documenti
Si caricano uno o più file e si fanno domande in italiano. L'assistente
risponde citando i punti precisi da cui ha tratto l'informazione, indicando
documento e pagina.

### Citazioni verificate — la protezione contro le invenzioni
Ogni citazione prodotta dal modello viene **controllata automaticamente**
contro le fonti realmente caricate. Se il modello cita un documento o una
pagina che non esistono, il sistema lo rileva e lo segnala come citazione non
verificata.

È la risposta al problema più noto degli assistenti IA: inventare riferimenti
credibili ma falsi. Qui non si chiede all'utente di fidarsi — si controlla.

### Leggere anche le immagini dei PDF
I disegni tecnici, gli schemi e le tabelle complesse dentro i PDF vengono
estratti e descritti da un modello di visione. Esiste una modalità **"Visione
Completa"** pensata per tabelle e impaginazioni difficili, dove il testo da
solo non basta.

### Riassumere ed estrarre
- **Riassumi** — sintesi del documento
- **Punti chiave** — estrazione dei passaggi principali
- **Estrai tabella** — recupero di dati tabellari in forma strutturata

### Confrontare documenti
Con più file caricati, la funzione **Confronta** evidenzia differenze e
corrispondenze. Utile fra due revisioni di un capitolato, due offerte di
fornitori, due certificati di lotto.

### Tradurre
Traduzione dei contenuti in **inglese, tedesco, francese, spagnolo e cinese** —
le lingue dei clienti e dei fornitori.

### Ricerca web controllata
È possibile cercare fonti su internet, **selezionare quali importare** nel
contesto e rimuoverle quando non servono più. Anche i riferimenti web vengono
validati. La ricerca non è automatica né silenziosa: è l'utente a decidere cosa
entra.

### Ascoltare invece di leggere
Ogni risposta può essere **letta ad alta voce** dal sistema di sintesi vocale.
Esiste inoltre l'**Audio Overview**, che genera una sintesi parlata in forma di
podcast a partire dai documenti — pensata per essere ascoltata in auto o mentre
si fa altro.

### Sessioni che restano
Le conversazioni sono **persistenti**: si riprende il lavoro dove lo si era
lasciato. Ogni sessione può essere esportata in formato Markdown per essere
archiviata o condivisa.

---

## Come è organizzato l'accesso

L'applicazione ha un **sistema di autenticazione con account personali** e due
ruoli: `user` e `admin`. L'amministratore può creare account, assegnare ruoli e
attivare o disattivare utenti.

Ogni utente ha uno **spazio dati separato**: documenti e sessioni sono legati
al proprio identificativo e non sono visibili agli altri. L'interfaccia mostra
un indicatore dello stato di isolamento del database, così è sempre chiaro in
quale spazio si sta lavorando.

---

## Che cosa NON fa (da sapere prima della dimostrazione)

- **La chat vocale in ingresso è disattivata.** Il riconoscimento del parlato
  dentro Assistente Ufficio è temporaneamente disabilitato per
  un'incompatibilità fra la libreria `faster-whisper` e la versione di
  PyTorch/CUDA installata. Si scrive da tastiera; la voce funziona in uscita
  (lettura delle risposte), non in ingresso.
- Non è collegato ai gestionali aziendali: lavora sui documenti che gli vengono
  forniti.
- Non decide nulla al posto delle persone: propone, riassume, cita. La
  valutazione resta di chi legge.

---

## Come presentarlo ai colleghi

Il messaggio utile non è l'elenco delle funzioni, ma il cambio di gesto: invece
di cercare un'informazione aprendo dieci file, **si fa una domanda e si riceve
una risposta con l'indicazione esatta di dove è scritta**. Il documento resta
la fonte di verità; l'assistente è il modo più rapido di arrivarci.

Tre punti da tenere in primo piano:

1. **I documenti restano in azienda.** Nessun caricamento su servizi esterni.
2. **Le citazioni sono verificate.** Se il sistema inventa un riferimento, viene
   segnalato invece che passare per buono.
3. **Ognuno lavora nel proprio spazio.** Account personali e dati separati.

E un'avvertenza onesta da dire in riunione: l'assistente **può sbagliare**, come
qualunque sistema di questo tipo. Per questo esistono le citazioni — servono a
rendere ogni affermazione verificabile in pochi secondi, non a garantire che
sia sempre esatta. Chi lo usa deve leggere la fonte prima di decidere.

---

# Parte seconda — Il quadro normativo e la scelta del modello locale

## L'AI Act europeo: cosa è già in vigore

Il Regolamento UE 2024/1689, noto come **AI Act**, è entrato in vigore il
1° agosto 2024 e si applica per fasi successive:

| data | cosa si applica |
|---|---|
| 2 febbraio 2025 | divieti sulle pratiche a rischio inaccettabile; obbligo di **alfabetizzazione in materia di IA** |
| 2 agosto 2025 | obblighi sui modelli per finalità generali (GPAI), governance, quadro sanzionatorio |
| **2 agosto 2026** | **obblighi di trasparenza (art. 50), vigilanza, sanzioni esigibili** |
| 2 dicembre 2027 | sistemi ad alto rischio dell'Allegato III *(rinviati)* |
| 2 agosto 2028 | sistemi ad alto rischio integrati in prodotti soggetti a normativa di sicurezza *(rinviati)* |

Il rinvio degli obblighi sui sistemi ad alto rischio è stato disposto dal
**Digital Omnibus**, pubblicato in Gazzetta UE il 24 luglio 2026. La ragione
dichiarata è pratica: alla scadenza originaria non erano disponibili le norme
tecniche armonizzate necessarie a rendere applicabili quegli adempimenti.

Dal 2 agosto 2026 le violazioni sono sanzionabili con ammende fino al **3% del
fatturato mondiale annuo o 15 milioni di euro**, se superiore.

## Dove si colloca Assistente Ufficio

**Non è un sistema ad alto rischio.** Le categorie ad alto rischio riguardano
ambiti come decisioni di assunzione, valutazione scolastica, accesso al
credito, servizi essenziali, forze dell'ordine, infrastrutture critiche. Un
assistente che risponde a domande sui documenti aziendali non vi rientra: non
sono richiesti valutazione di conformità, marcatura o documentazione tecnica.

**Attenzione a un solo confine.** L'Allegato III include i sistemi usati per
assunzioni, assegnazione di compiti, **monitoraggio o valutazione delle
prestazioni dei lavoratori**. Finché l'assistente aiuta le persone a lavorare,
siamo fuori. Se un domani venisse usato per valutare o sorvegliare chi lavora,
la classificazione cambierebbe e con essa gli obblighi.

## Gli obblighi che ci riguardano davvero

**Trasparenza (art. 50).** Chi interagisce con il sistema deve sapere che sta
parlando con un'intelligenza artificiale. È un'informazione da dare, non un
adempimento complesso.

**Alfabetizzazione (art. 4), in vigore da febbraio 2025.** Chi impiega sistemi
di IA deve assicurare un livello adeguato di competenza a chi li utilizza.

> Vale la pena dirlo esplicitamente in riunione: **questo incontro è esattamente
> una misura di alfabetizzazione**. Spiegare cosa fa lo strumento, cosa non fa,
> quali sono i suoi limiti e come verificarne le risposte non è una formalità —
> è l'adempimento stesso.

**Informativa sul trattamento dei dati.** Le persone che usano il sistema
devono sapere quali dati vengono trattati, dove restano e per quanto tempo.

---

## Perché un modello locale invece di uno online

La differenza non è di qualità, è di **perimetro**: dove finiscono i dati.

### 1. I documenti non lasciano l'azienda

Con un servizio online, ogni documento caricato viene trasmesso a un fornitore
esterno. Questo rende quel fornitore un **responsabile del trattamento** ai
sensi del GDPR, con tutto ciò che comporta: accordo di nomina, verifica delle
garanzie, gestione dei trasferimenti fuori dall'Unione Europea, valutazione dei
rischi.

Con un modello locale quel passaggio **non esiste**. Non c'è trasferimento,
quindi non c'è nulla da regolare. È la posizione di conformità più semplice
possibile: non si gestisce meglio il rischio, lo si elimina.

### 2. Il segreto industriale resta segreto

Formulazioni, parametri di processo, specifiche dei clienti, listini,
condizioni contrattuali. Una volta usciti, sono fuori dal nostro controllo
anche quando il fornitore promette di non usarli per addestrare i propri
modelli. La promessa può essere sincera e la protezione comunque insufficiente:
restano copie, log, backup, e le condizioni di servizio possono cambiare.

### 3. L'effetto mosaico

È il rischio meno intuitivo e il più concreto. Nessuna singola informazione
sembra sensibile: il nome di un collega, un codice articolo, il riferimento a
una commessa, una lamentela di un cliente. Ma **frammenti innocui, accumulati e
ricomposti, formano un quadro** che nessuno avrebbe consapevolmente
condiviso.

Il pericolo non sta nella singola domanda: sta nel fatto che ogni domanda passa
per lo stesso posto, e quel posto accumula.

### 4. Indipendenza e continuità

Un modello installato in azienda non cambia da un giorno all'altro, non viene
dismesso, non modifica il proprio prezzo e continua a funzionare anche senza
connessione. Le risposte restano confrontabili nel tempo: se un documento
viene analizzato oggi e fra sei mesi, il comportamento è lo stesso.

### 5. Verificabilità

Tutto ciò che avviene resta su macchine nostre. È possibile sapere con
esattezza cosa è stato elaborato e quando — cosa che, in caso di verifica o di
contestazione, si può dimostrare invece di dover chiedere a terzi.

---

## I limiti del modello locale: dirli è più utile che nasconderli

**È meno capace dei modelli online più avanzati.** I modelli di frontiera girano
su hardware da centro dati e sono più bravi nei ragionamenti complessi. Il
modello locale è pienamente adeguato a cercare, riassumere, confrontare, citare
e tradurre — che è il lavoro per cui lo usiamo — ma non è lo strumento giusto
per ogni compito.

**Ha un costo in hardware e manutenzione.** Serve una macchina adeguata e
qualcuno che se ne occupi. Non c'è un abbonamento, ma non è gratis.

**La sicurezza è nostra responsabilità.** Se i dati non escono, la protezione
delle macchine su cui restano diventa un problema interno — accessi, backup,
aggiornamenti.

## La regola pratica che ne deriva

Non è "locale sì, online no". È:

> **Materiale riservato — documenti dei clienti, specifiche, dati aziendali —
> resta in locale. Per il resto si può usare il servizio più capace
> disponibile.**

Il criterio è la **sensibilità del dato**, non la difficoltà del compito. È la
distinzione che regge nel tempo: se si sceglie in base a "quanto è difficile la
domanda", prima o poi una domanda difficile sarà anche riservata, e la
difficoltà vincerà sulla riservatezza.

---

**Avvertenza**: sintesi divulgativa a fini informativi, non consulenza legale.
Il Digital Omnibus è di luglio 2026 e la materia è in assestamento; per
decisioni con effetti concreti serve una verifica professionale aggiornata.
