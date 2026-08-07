# Note hardware per chi lavora su Euri — 2 agosto 2026

Messaggio per me stesso in una sessione futura, o per chiunque altro metta mano
a questo codice. Nasce da una giornata di misure fatta su ds4 (altro progetto,
stessa macchina), durante la quale abbiamo guardato anche Euri.

**Misure complete e metodologia**: `/home/fio/ds4/MISURE_2026-08-02.md`,
sezione 7-ter per la parte che riguarda Euri.

**Baseline latenze pre-migrazione**: vedi in fondo, sezione "Baseline della
pipeline vocale". È l'ancoraggio per il confronto dopo l'arrivo della P620.

---

## La cosa più importante: Euri non ha un problema di prestazioni

Se arrivi qui guardando la telemetria della GPU e vedi utilizzo basso e
consumi bassi, **non è un collo di bottiglia e non c'è niente da ottimizzare**.

Durante l'uso di Euri le GPU risultano:

```
GPU0: 43.6 W medi, sm 27.4% medio (max 100%), mem_util 15.6% medio (max 74%)
GPU1: 38.8 W medi, sm 18.3% medio (max 48%), mem_util 13.5% medio (max 45%)
```

Sembra una GPU affamata. **Non lo è.** Il motore di inferenza, misurato da
solo con una richiesta diretta a ollama senza Euri di mezzo:

```
gemma4:26b   generation: 58.25 t/s   prefill: 307.15 t/s
```

Cinquantotto token al secondo. Le raffiche con lunghe pause in mezzo sono il
**ritmo della conversazione umana**: la GPU genera quando l'utente ha parlato e
Euri risponde, e sta ferma mentre l'utente ascolta o pensa. Per un assistente
vocale quel duty cycle è corretto per definizione.

I bassi watt sono inoltre normali nel decode a batch 1: la GPU legge i pesi
dalla VRAM e fa pochissime operazioni per token, quindi non scalda. Bassi watt
e 58 t/s convivono senza contraddizione.

---

## Due errori già commessi — non rifarli

Durante la sessione del 2 agosto ho dedotto due cose sbagliate su Euri, prima
di guardare il codice. Le scrivo perché sono trappole naturali:

1. **"Il processo Python al 311% di CPU con 463 thread è il client LLM che
   serializza le richieste."** Falso. È `voice_daemon.py`, la pipeline vocale
   in tempo reale — non è nel percorso di generazione dei token. Lavora in
   continuo perché resta in ascolto sul microfono.

2. **"Il pattern a raffiche della GPU è serializzazione da ottimizzare nel
   codice."** Falso, per il motivo spiegato sopra.

I processi reali sono:

```
hardware_monitor.py   64 thread, tutti in sleep
streamlit ui/app.py   porta 8501
voice_daemon.py       463 thread, ~311-328% CPU, 3816 MiB VRAM (Whisper)
```

`voice_daemon.py` (4614 righe):
`microfono → VAD → STT → intent_router → [branch] → brain → TTS → speaker`

**I 463 thread non sono un leak**: verificati stabili a 463 sia a 3 minuti che
a 13:40 di uptime, 455 in sleep e 8 attivi. È un pool allocato in partenza.

---

## Esperimento aperto: governor `performance`

**Non ancora provato.** Da fare quando c'è tempo di usare Euri normalmente per
qualche minuto.

Stato attuale della macchina:

```
frequenza media dei core:  1398 MHz  (min 1196, max osservato 2500)
massimo consentito:        3600 MHz
governor:                  schedutil
```

I core stanno al 39% della frequenza massima. Con `schedutil` salgono sotto
carico sostenuto, ma **ogni risveglio breve parte da 1200 MHz** e paga il
ritardo di rampa. Una pipeline vocale è fatta di migliaia di risvegli brevi
(callback audio, frame VAD, loop di eventi), quindi è il caso peggiore.

```bash
sudo cpupower frequency-set -g performance    # applica
sudo cpupower frequency-set -g schedutil      # torna indietro
```

**Come valutarlo**: non con un benchmark. Si imposta, si usa Euri qualche
minuto come al solito, e si guarda la **reattività percepita** — il tempo fra
la fine della frase dell'utente e l'inizio della risposta. Se non cambia nulla,
si torna indietro: costa circa un centinaio di watt a riposo su questo dual
Xeon.

Nota: la distinzione non è "benchmark contro uso reale", è **sostenuto contro
bursty**:

- **Carichi sostenuti** (percorso CPU di ds4: 40 thread al lavoro continuo):
  `schedutil` sale subito e resta su, il governor non cambia nulla.
- **Carichi bursty** (pipeline vocale di Euri, ma anche il percorso GPU di
  ds4): risvegli brevi e frequenti su core scesi a 1200 MHz, ognuno paga la
  rampa. È qui che l'effetto, se c'è, si manifesta.

Il percorso GPU di ds4 rientra nella seconda categoria: durante il decode il
processo usa **85.4% di CPU**, cioè meno di un core — il thread host lancia il
trasferimento, si blocca sul DMA, si risveglia, ricomincia.

### Esito della metà misurabile (2 agosto, sera)

Il test su ds4 **è stato fatto** e il governor rende:

| | prefill | generation |
|---|---|---|
| `schedutil` | 8.94 | 1.34 |
| `performance` | **10.15** (+13.5%) | 1.37 (rumore) |

Frequenze da 1398 MHz medi a **3289 MHz** su tutti gli 80 core. Conferma che
l'ipotesi sui risvegli è fondata almeno per i carichi bursty: il prefill di
ds4, che ha lo stesso profilo della pipeline vocale, guadagna il 13.5%.

### Esito su Euri — verifica successiva, dai log

Analisi di `logs/voice_daemon.log` e `logs/hardware_monitor.log`, confronto
contro la baseline del **30 luglio** (`schedutil`). Governor confermato
`performance` su 80/80 core, frequenza media ~3.31 GHz contro ~1.40 GHz.

| stadio | con `performance` | baseline 30/07 | delta | natura del carico |
|---|---|---|---|---|
| classificatore rapido (mediana) | 1.48 s | 2.35 s | **−37%** | brevissimo, bursty |
| `brain.respond` | 3.22 s | 4.29 s | **−25%** | breve, bursty |
| handler CHAT | 18.4 s | 25.7 s | −28% | ⚠ contaminato dalla lunghezza risposta |
| TTS normalizzato | — | — | −7% | sintesi sostenuta |
| STT normalizzato per durata audio | 72 ms/s | 77 ms/s | ~0% | **gira su GPU** |

**Il dato più forte non sono le percentuali, è il gradiente.** I miglioramenti
sono ordinati esattamente come prevede il meccanismo: più uno stadio è breve e
frequente, più guadagna; gli stadi lunghi o che girano su GPU non guadagnano
nulla. L'STT piatto è la migliore prova di controllo disponibile — Whisper sta
sulla GPU, la frequenza dei core non lo tocca, e infatti non si muove. Un
confondente (turni diversi, risposte più corte) produrrebbe un miglioramento
uniforme o casuale, non quell'ordinamento.

**Conferma indipendente**: su ds4, stesso giorno, +13.5% sul prefill (lavoro
CPU bursty) e ~0% sulla generazione (attesa su PCIe). Stessa firma, carico
completamente diverso.

**Riserve**:
- Il −28% dell'handler CHAT va quasi del tutto scartato: dipende dalla
  lunghezza della risposta, è la metrica più contaminata del gruppo.
- Baseline di un giorno diverso (30 luglio), non un A/B a parità di sessione.
  Il gradiente compensa in parte, ma resta il limite principale.
- Campione piccolo, turni non identici.

**Restano due picchi del classificatore da 15.4-15.5 s**, legati al fallback
LLM. Il governor non poteva toccarli: quel percorso è vincolato dalla velocità
di generazione del modello, non dalla frequenza dei core. Coerente col resto.

**Termica**: finestra 08:15-08:32, CPU media 67 °C / max 71 °C (soglia 90 °C),
GPU max 68/59 °C, zero fault, nessun WARNING/CRITICAL. Il costo reale non è il
calore, sono i ~100 W continui in più.

### ⚠ Il governor NON è persistente

Al reboot torna a `schedutil` e il guadagno sparisce. Per renderlo permanente
serve un'unità systemd (o `cpufrequtils`). **Non ancora fatto.**

---

## Interazione con ds4 sullo stesso hardware

Questa macchina è un **dual socket** (2× Xeon E5-2673 v4, 2 nodi NUMA):

```
node0: CPU 0-19,40-59   ← entrambe le GPU sono qui
node1: CPU 20-39,60-79
```

Gli script di ds4 (`run_ds4.sh`, `start_server.sh`, `chat.sh`) sono ora pinnati
sul **socket 0** con `taskset -c 0-19,40-59`, perché il percorso CPU di ds4
perde il 25% se i thread attraversano i socket.

`voice_daemon` consuma stabilmente >3 core con thread liberi ovunque, quindi
usando i due insieme si contendono il socket 0. Se serve separarli:

```bash
taskset -c 20-39,60-79 ./venv/bin/python voice_daemon.py   # Euri sul socket 1
```

Il daemon paga qualcosa sul modello Whisper (le GPU sono sul nodo 0) ma lascia
il socket 0 libero. **È una scelta di priorità**: se il carico che conta è
Euri, lasciarlo dov'è e semmai togliere il pinning a ds4.

---

## Se la macchina cambia

È in valutazione un Lenovo P620 (Threadripper PRO, WRX80): **dominio NUMA
singolo, PCIe 4.0, 8 canali di memoria, core Zen 3 a 3.6 GHz di base**.

Cosa cambierebbe per Euri:

- **Tutto il pinning `taskset` va rimosso** — su dominio singolo non serve e
  può solo far danno.
- L'esperimento sul governor perde gran parte del senso: i core partono da
  3.6 GHz invece che da 1.2.
- Il guadagno atteso è sul **percorso vocale** (STT, VAD, cancellazione d'eco,
  Python interprete): tutto lavoro CPU single-thread, dove Zen 3 vale circa
  2-2.5× rispetto a Broadwell.
- **Nessun guadagno atteso sulla generazione dei token**: quella è già a
  58 t/s e il limite è la banda VRAM della 4060 Ti (288 GB/s), che non cambia.

---

## Baseline della pipeline vocale — 6 agosto 2026, pre-migrazione

Da rimisurare sulla P620 con gli stessi criteri. **Confrontare solo turni di
lunghezza simile**: TTS e `brain.respond` scalano con i caratteri generati, un
turno più corto sembra migliorato anche senza modifiche.

### Prima delle ottimizzazioni (turno singolo, 1263 caratteri)

```
STT ......................  1.824 ms
Turno semantico ..........  9.668 ms
Intent regex .............      1 ms
RAG (due passaggi) .......  ~3.000 ms
brain.respond() Ollama ...  8.887 ms
TTS synth ................  3.607 ms
                            ─────────
prima sillaba a ..........  ~27 secondi
Handler CHAT totale ......  82.108 ms   (include la riproduzione audio)
```

Nota: `Handler CHAT` **non** è la latenza percepita — comprende il tempo di
riproduzione dell'audio. La metrica che conta è il tempo alla prima sillaba.

### Dopo le ottimizzazioni (TTS segmentato + riuso feature RAG)

Interventi applicati:
- **TTS a segmenti**: nascosti ~3.4 s medi di sintesi, fino a ~6 s sul turno
  da 2.016 caratteri. Nessun errore audio, nessuna interruzione fra segmenti.
- **RAG con `query_features_reused=True`**: locator a 0.38-2.1 s, evitati
  1.5-3.2 s di classificazione/embedding duplicati.
- Suite di test: **76/76**.

Intervalli osservati su cinque turni:

| stadio | intervallo | note |
|---|---|---|
| frame semantico | **5.4 - 20.4 s** | collo di bottiglia principale |
| risposta Gemma | 4.1 - 12.8 s | limitato da GPU/banda VRAM |
| RAG ordinario | 2.5 - 7.4 s | |
| **TTS → prima sillaba** | **0.45 - 1.03 s** | era 3.607 ms |

Il frame semantico si è dimostrato **necessario**, non solo costoso: in un
turno ha corretto un falso `SAVE_TODO` prodotto dalla regex, riportandolo a
`CHAT`. Non va rimosso per guadagnare latenza.

### Ottimizzazioni identificate e NON applicate (scelta deliberata)

Rinviate dopo la migrazione: intervengono sui confini delicati del sistema
(routing e pipeline audio), dove un errore è visibile all'utente mentre la
latenza è solo fastidiosa. Con 76/76 verde e l'hardware in sostituzione, non
valeva il rischio.

1. **Frame semantico in parallelo invece che in serie.** La regex risponde in
   1 ms e ha ragione quasi sempre; il frame la corregge di rado. Avviando
   subito il percorso della regex e lasciando girare il frame in parallelo si
   nascondono i suoi 5-20 s nel caso comune, pagando lavoro buttato solo
   quando dissente. Esecuzione speculativa: ramo previsto quasi sempre giusto,
   costo dell'errore basso.

2. **Streaming di Gemma direttamente nel chunker TTS.** Oggi il TTS è
   segmentato ma si attende comunque la fine di `brain.respond` (4.1-12.8 s)
   prima di sintetizzare. Prendendo i token man mano e sintetizzando a frase
   completa, la prima sillaba arriverebbe dopo ~1 s di generazione.

Stima combinata: da ~24 s a prima sillaba a **sotto i 5 s**, a parità di
risposte e di hardware.

### Da aggiungere prima di raccogliere log d'uso reale

**Loggare i token di prompt e completion della chiamata del frame semantico.**
È pura osservabilità, nessun cambio di comportamento. Senza quei due numeri i
log diranno *che* a volte impiega 20 s invece di 5, non *perché* — e la causa
cambia completamente la soluzione:

- prompt grossi → dargli meno contesto RAG
- generazione lunga → modello diverso
- contesa con Gemma sulla stessa GPU → scheda dedicata

### Ipotesi registrata prima della migrazione

Scritta **prima** di avere i dati, così vale come previsione e non come
spiegazione a posteriori.

> Ipotesi pre-migrazione: una parte rilevante della latenza di coda attuale
> deriva da contesa CPU/GPU, attraversamenti NUMA e variabilità di scheduling.
> Dopo la migrazione alla P620 ci aspettiamo che p95 e massimo **normalizzati**
> diminuiscano più del minimo, comprimendo la dispersione.
>
> Predizione alternativa: se minimo, mediana e p95 diminuiscono nella stessa
> proporzione, il beneficio deriva principalmente dalla piattaforma più veloce
> e non dalla rimozione della contesa. Se il tempo di decode normalizzato per
> token resta invariato, il pavimento continua a essere determinato dalla banda
> VRAM.
>
> Il confronto dovrà registrare per ciascuno stadio: numero di campioni,
> minimo, mediana, p95, massimo e, per Ollama, token di prompt/completion e
> millisecondi per token. Andranno confrontati sia turni reali di lunghezza
> simile sia un piccolo replay di prompt fissi a modello caldo.

**Perché servono entrambe le misure**: i turni reali dicono com'è l'esperienza
d'uso, il replay a prompt fissi elimina per costruzione la varianza dovuta
alla lunghezza delle risposte e isola ciò che è cambiato nel sistema. È la
stessa struttura usata su ds4 (`bench_prompt.txt` fisso più uso reale).

**Perché la coda conta più della media**: un turno da venti secondi pesa
psicologicamente molto più di tre turni rapidi. La sensazione che Euri sia
"viva" la decide il caso peggiore, non il valore tipico.

**L'evidenza a supporto dell'ipotesi**, già visibile nei dati attuali: ogni
stadio oscilla di un fattore 3-4× (frame 5.4-20.4, Gemma 4.1-12.8, RAG
2.5-7.4). Uno stesso modello con lo stesso prompt non impiega 5 s una volta e
20 la successiva per ragioni interne: quella dispersione è contesa. E i
colpevoli sulla macchina attuale sono documentati — `voice_daemon` a 328% di
CPU che vaga fra due socket, Whisper e Gemma sulle stesse schede, e un NUMA in
cui aggiungere thread *peggiorava* le prestazioni del 13%.

### Cosa la P620 NON risolverà su questo percorso

I due costi dominanti — frame semantico e `brain.respond` — sono chiamate a
modelli, quindi limitate da GPU e banda VRAM, **non dalla CPU**. Stessa
categoria dei 58 t/s di Gemma. La P620 migliorerà STT, VAD, RAG e
orchestrazione Python: utile, ma è la fetta piccola del percorso critico.
