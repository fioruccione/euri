# Euri — test A/B speculative decoding su Gemma 4

Data: 23 agosto 2026
Stato: test iniziale e replica con thinking conclusi; nessuna modifica alla
configurazione runtime di Euri

## Obiettivo

Misurare, sulla workstation X99 con 2× RTX 4060 Ti 16 GB, se un draft DFlash o
MTP specifico per Gemma 4 26B-A4B accelera la generazione rispetto allo stesso
target eseguito senza speculative decoding.

Il test ha mantenuto invariati target, prompt, contesto, quantizzazione della
KV cache, split GPU, temperatura, seed e numero di token. P2P è rimasto
disabilitato.

## Artefatti verificati e conservati

- Target: `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`
  - dimensione: 16.947.541.728 byte
  - SHA-256: `f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f`
- Draft DFlash: `dflash-gemma-4-26B-A4B-it-Q8_0.gguf`
  - dimensione: 472.433.824 byte
  - SHA-256: `b353b64a95f4a498ce036969df21a15191cc85800722a3e1f62f6110b91f01b2`
- Draft MTP: `mtp-gemma-4-26B-A4B-it.gguf`
  - dimensione: 461.766.816 byte
- È conservato anche il checkpoint DFlash BF16 ufficiale in formato
  `safetensors`, per eventuali esperimenti futuri.
- Runtime: `llama.cpp` commit
  `95b8e33e16bb9a60de780a70930ebf729db6a90a`, compilato localmente con CUDA
  per compute capability 8.9.

Gli artefatti sono sotto `models/gemma4_speculative/`, percorso escluso da Git.
Le nove risposte grezze sono in
`models/gemma4_speculative/benchmark_20260823/`.

Nota: il blob GGUF del modello Ollama `gemma4:26b` già usato da Euri non è
caricabile da questo `llama.cpp`: il loader corrente si aspetta 1.014 tensori e
nel blob ne trova 658. Per il benchmark è stato quindi usato il target upstream
aggiornato indicato sopra. Questa differenza impedisce di interpretare il test
come confronto diretto e isolato tra Ollama e llama.cpp.

## Configurazione comune

- target interamente in GPU (`-ngl all`)
- split a layer su entrambe le GPU (`--split-mode layer --tensor-split 1,1`)
- contesto: 32.768 token, un solo slot
- KV cache K/V: `q8_0`
- Flash Attention attiva
- output: 512 token
- temperatura: 0
- seed: 42
- thinking disabilitato nel template chat
- tre repliche per configurazione dopo il caricamento

## Risultati

| Configurazione | tok/s repliche | Mediana | Accettazione draft | VRAM dopo il caricamento | Esito |
|---|---:|---:|---:|---:|---|
| A — target senza draft | 57,28 / 57,55 / 57,21 | **57,28** | — | 9.607 / 8.890 MiB | baseline |
| B — DFlash Q8 | 33,46 / 33,83 / 33,77 | **33,77** | 3,82–4,05% sul prompt lungo | 9.939 / 10.386 MiB | **−41,0%** |
| C — MTP | 57,53 / 57,29 / 56,84 | **57,29** | 39,77–40,65% sul prompt lungo | 9.905 / 9.516 MiB | **+0,03%** |

Il draft DFlash peggiora nettamente la velocità: il costo del draft non viene
compensato perché quasi tutte le proposte sono rifiutate. MTP ha una buona
percentuale di accettazione, ma su questo hardware il lavoro aggiuntivo annulla
il vantaggio: la differenza dalla baseline è rumore sperimentale.

Le risposte delle tre configurazioni sono semanticamente coerenti. Tuttavia,
gli output speculative non sono identici byte per byte alla baseline, anche con
temperatura zero e seed uguale. Quindi, oltre all'assenza di accelerazione, non
abbiamo ottenuto equivalenza stretta dell'output.

## Replica DFlash con thinking attivo

Il primo test aveva disabilitato il thinking. Poiché DFlash è stato addestrato
e pubblicamente misurato soprattutto su generazioni con thinking attivo, è
stata eseguita una seconda prova mantenendo invariati target, draft, contesto,
cache, split GPU, temperatura, seed e limite di 512 token. È cambiata soltanto
la modalità di ragionamento nel template.

Il prompt originale del primo benchmark non era stato persistito insieme alle
risposte; è stato quindi usato un prompt tecnico equivalente sullo stesso
dominio. I risultati sono confrontabili come misura del regime operativo, ma
non costituiscono un A/B byte-identico sul prompt.

| Configurazione | tok/s repliche | Mediana | Accettazione | Rispetto a DFlash senza thinking |
|---|---:|---:|---:|---:|
| DFlash + thinking, 512 token | 51,73 / 51,22 / 51,91 | **51,73** | 10,77–10,88% | **+53,2%** |

Con 512 token il modello usa tutto il budget per il reasoning e non arriva alla
risposta visibile. Una prova qualitativa separata da 2.048 token ha prodotto
5.836 caratteri di reasoning e 2.585 caratteri di risposta finale, rimanendo
troncata al limite. In questa prova la velocità è stata 45,94 tok/s e
l'accettazione 8,73%.

Il thinking rende quindi DFlash molto meno penalizzante e porta la velocità
breve vicino alla baseline senza thinking (51,73 contro 57,28 tok/s), ma non
dimostra ancora un'accelerazione rispetto allo stesso target con thinking e
senza draft. Per isolare il contributo del draft serve quest'ultima baseline.

## Simulazione paired su un prompt reale di Euri

È stato quindi usato un prompt finale realmente catturato dal runtime di Euri:
11.699 token comprendenti identità, regole epistemiche, contesto aziendale,
memorie recuperate e continuità conversazionale. In coda è stata aggiunta la
stessa domanda sulla possibile adozione di DFlash nei loop. Entrambe le prove
avevano thinking attivo, temperatura 0,7, seed 42 e limite di 3.000 token;
l'unica differenza era la presenza del draft.

| Misura | Thinking senza draft | Thinking + DFlash |
|---|---:|---:|
| Prompt | 11.699 token | 11.699 token |
| Prompt processing | 3,14 s | 7,14 s |
| Token generati | 1.911 | 2.643 |
| Generazione | **49,57 tok/s** | **45,63 tok/s** |
| Tempo totale | **41,68 s** | **65,03 s** |
| Accettazione draft | — | 9,81% |
| Caratteri reasoning | 5.744 | 6.840 |
| Caratteri risposta | 1.228 | 2.820 |
| Terminazione | completa | completa |

DFlash è più lento del 7,9% per token e più che raddoppia il tempo di prompt
processing. Il tempo end-to-end cresce del 56%; una parte dell'aumento dipende
anche dal fatto che la traiettoria generativa con draft produce 732 token in
più. La risposta DFlash è più estesa, ma non è automaticamente migliore: in
particolare ha definito il draft «estremamente efficace» senza conciliare
questa affermazione con l'accettazione del 9,81% e senza avere ancora davanti
la baseline paired. Il thinking ha quindi prodotto una narrazione convincente
oltre ciò che l'evidenza autorizzava.

Per questo carico reale sulla X99, DFlash non accelera il thinking e non è
adatto al percorso voice sincrono. I risultati grezzi sono conservati come
`EURI_SIM_BASE_THINK.json` e `EURI_SIM_DFLASH_THINK.json`.

## Decisione

**Non attivare ancora DFlash né MTP nel runtime principale di Euri.**

- DFlash è un no-go nella modalità senza thinking, ma la replica con thinking
  giustifica una seconda fase di verifica.
- MTP occupa memoria e aggiunge complessità senza un guadagno misurabile.
- La modalità standard rimane l'unica modalità attiva; nessun parametro di
  Euri è stato cambiato.

Il nuovo target Gemma 4 e il runtime nativo non vengono eliminati. La baseline
nativa di circa 57,3 tok/s è interessante rispetto alle precedenti misure
Ollama, ma va trattata come un esperimento separato: prima di sostituire il
modello attuale occorre verificare parità di template, tool calling, vision,
thinking selettivo, API e comportamento conversazionale dentro Euri.

## Prossimo test sensato

Non inserire DFlash nel percorso voice. Conservare gli artefatti e ripetere
eventualmente il confronto paired sulla P620, dove CPU, PCIe e prompt
processing cambieranno. Per i loop idle, l'eventuale adozione richiede prima
una valutazione della qualità a parità di problema e un vantaggio misurabile
nel tempo end-to-end; sulla X99 tale vantaggio non è presente. Separatamente si
potrà preparare un backend nativo opzionale e reversibile, senza draft, per
richieste reali di Euri.

## Addendum diagnostico — 24 agosto 2026

È stata ripetuta la prova senza thinking per separare il comportamento del
draft dalla modalità di ragionamento. Il target, il prompt, il seed, la
temperatura, il limite di output, il contesto, lo split GPU e la cache del
target sono rimasti invariati. La cache K/V del draft è stata invece portata
da `q8_0` a `f16`, in seguito alla verifica del difetto upstream llama.cpp
`#25725` relativo al crollo dell'accettazione DFlash con cache draft
quantizzata.

La sola cache `f16` non risolve il problema su Gemma 4, ma migliora la
prestazione. Il parametro dominante risulta la lunghezza massima del blocco
proposto:

| DFlash, no thinking | Generazione | Accettazione | Nota |
|---|---:|---:|---|
| cache draft `f16`, blocco 15 | 38,80 tok/s | 5,39% | singola prova a freddo |
| cache draft `f16`, blocco 7 | 46,37 tok/s | 11,67% | singola prova a freddo |
| cache draft `f16`, blocco 3 | 51,81 tok/s | 22,48% | prima prova a freddo |
| cache draft `f16`, blocco 3 | 55,33 / 55,78 tok/s | 25,64% | due repliche a caldo |
| target senza draft | 57,90 / 58,00 / 58,32 tok/s | — | stessa batteria |

Sulle repliche a caldo, il blocco da 3 arriva a circa 55,56 tok/s contro una
mediana baseline di 58,00 tok/s: resta quindi circa il 4,2% più lento. La
riduzione del blocco fa convergere DFlash verso la baseline, ma in questa
prova non produce un punto di sorpasso.

È stata inoltre tentata l'allocazione del draft soltanto su `CUDA0`. La build
ha abortito durante l'inizializzazione perché `token_embd.weight`, condiviso
con il target, era preallocato su `CUDA1` e non risultava eseguibile dal grafo
del draft. La separazione del draft su una sola GPU non è quindi utilizzabile
con questo target e questa build senza una correzione del runtime.

Nel controllo qualitativo breve, entrambe le configurazioni hanno rispettato
la distinzione tra osservazioni e ipotesi e hanno prodotto una risposta
coerente. Non hanno però scelto la stessa ipotesi principale: la baseline ha
preferito un problema meccanico della coclea, DFlash una riduzione del flusso
di alimentazione. Non emerge un degrado qualitativo evidente, ma un solo caso
non dimostra equivalenza semantica.

### Interpretazione aggiornata

Il risultato iniziale di −41% non era rappresentativo della migliore
configurazione DFlash disponibile: cache draft `f16` e blocchi più corti
recuperano gran parte della perdita. Tuttavia, sul carico tecnico in prosa e
sulla X99, il costo del draft continua a superare il lavoro risparmiato.
Il limite non è la VRAM e non è causato dal thinking; deriva dalla combinazione
tra bassa prevedibilità dei blocchi lunghi, overhead DFlash e percorso
multi-GPU del runtime.

La decisione operativa resta quindi invariata: non attivare DFlash nel runtime
principale. La conclusione scientifica viene però precisata: DFlash non è
«rotto» né incompatibile, ma non fornisce ancora un'accelerazione su questo
carico e con questa implementazione.
