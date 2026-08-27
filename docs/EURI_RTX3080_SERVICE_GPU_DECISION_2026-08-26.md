# Euri — decisione MTP e valutazione RTX 3080 come GPU di servizio

Data: 26 agosto 2026
Stato: analisi conclusa; nessuna modifica al runtime

## Decisioni operative

1. La strada MTP/speculative decoding su Gemma4 e' chiusa per ora.
   Sui prompt reali di Euri non produce un vantaggio end-to-end e, anche nel
   miglior caso osservato, resta meno conveniente del target-only. Non eseguire
   altre patch, strumentazioni profonde o ottimizzazioni MTP senza una nuova
   evidenza esterna materialmente diversa.
2. La RTX 3080 10 GB non deve partecipare al modello principale. Le due RTX
   4060 Ti 16 GB restano dedicate a Gemma4.
3. La RTX 3080 e' interessante soltanto se ospita un control-plane LLM compatto
   e validato. Il semplice offload di Whisper, embedding o TTS vale meno di
   circa 0,6 secondi complessivi e non giustifica da solo la terza GPU.

Il criterio di successo e' sempre:

`fine fisica della voce utente -> primo audio utile di Euri`

Il throughput isolato di un singolo modello non e' un criterio sufficiente.

## Configurazione di riferimento

- Lenovo ThinkStation P620;
- Threadripper PRO 3975WX;
- 128 GiB ECC;
- 2x RTX 4060 Ti 16 GB dedicate a Gemma4;
- RTX 3080 10 GB disponibile come eventuale GPU di servizio;
- P2P non richiesto e non assunto affidabile;
- Ollama 0.30.7;
- Gemma4 26B A4B come modello realtime;
- faster-whisper `large-v3` FP16;
- Redis 8.8 con RediSearch e RedisJSON;
- Piper Paola/Lessac tramite sherpa-onnx su CPU.

Durante l'audit del 26 agosto la 3080 non era installata. `lspci` mostrava
soltanto le due 4060 Ti e `nvidia-smi` non riusciva a interrogare il driver.
Le latenze provengono quindi dalla sessione P620 completa del 25 agosto; le
stime di VRAM e del guadagno sulla 3080 non sono ancora misure a tre GPU.

## Baseline reale della pipeline vocale

Campione: 15 turni completi `CHAT`/`SEARCH` tra le 18:00 e le 19:00 del
25 agosto 2026.

Il report storico `scripts/measure_euri_performance.py` somma soltanto le fasi
gia' strumentate. Una seconda ricostruzione basata sui timestamp di parete ha
mostrato ritardi condizionali non inclusi nella somma, in particolare
ActionController e Retrieval Strategy.

| Metrica | Media | Mediana | P95 | Massimo |
|---|---:|---:|---:|---:|
| Somma delle fasi strumentate | 18,756 s | 18,025 s | 23,651 s | 29,171 s |
| Parete dopo `speech_end` VAD -> TTS pronto | 20,561 s | 18,040 s | 34,185 s | 40,890 s |
| Fine fisica stimata -> TTS pronto | **22,561 s** | **20,040 s** | **36,185 s** | **42,890 s** |

L'ultima riga aggiunge i 2.000 ms configurati nel VAD per dichiarare terminato
il parlato. Il timestamp `TTS first-ready` precede di poco l'avvio effettivo di
`aplay`, il cui piccolo overhead non e' loggato.

### Fasi sempre visibili

| Fase | n | Media | Mediana | P95 | Massimo |
|---|---:|---:|---:|---:|---:|
| STT | 15 | 0,845 s | 0,715 s | 1,451 s | 1,696 s |
| Semantic Turn Gemma4 | 15 | **8,116 s** | **8,027 s** | **9,744 s** | 9,992 s |
| RAG dual-channel | 15 | 3,587 s | 2,950 s | 7,582 s | 14,903 s |
| Brain Gemma4 | 15 | 5,662 s | 5,535 s | 7,493 s | 8,293 s |
| TTS primo segmento | 15 | 0,545 s | 0,550 s | 0,783 s | 0,822 s |

### Percorsi condizionali non rappresentati dalla vecchia somma

- Retrieval Strategy: 12,33 s osservati in un turno `entity_recall`.
- ActionController: 11,70 s osservati in un turno classificato poi come
  conversazione.
- Il tempo nascosto aveva mediana 16 ms, ma P95 11,91 s e massimo 12,35 s.

Queste chiamate spiegano la coda lunga: la mediana della vecchia somma era
quasi corretta, il P95 no.

## Pipeline attuale

```text
audio
  -> Silero VAD: attesa 2 s di silenzio
  -> SpeakerAuth Resemblyzer su CPU
  -> faster-whisper large-v3 su una 4060 Ti
  -> SemanticTurnService con Gemma4 sulle due 4060 Ti
  -> intent regex e gate deterministici
  -> eventuale Addressedness/ActionController con Gemma4
  -> domain classification con Gemma4
  -> E5-large su CPU + Redis/RediSearch/RedisJSON
  -> eventuale Retrieval Strategy con Gemma4
  -> brain Gemma4 sulle due 4060 Ti
  -> risposta completa + guard
  -> Piper/sherpa-onnx su CPU
  -> audio
```

Gemma4 non e' usato soltanto come brain. Viene chiamato piu' volte come piano
di controllo prima della risposta finale:

- frame semantico su ogni turno accettato;
- addressedness nei follow-up senza wake word;
- classificazione del dominio RAG;
- strategia di retrieval nei casi aperti/non specifici;
- proposta dell'ActionController nei casi potenzialmente operativi;
- risposta finale.

Questo e' il principale risultato della radiografia.

## Tabella dei componenti

| Componente | Implementazione e hardware attuale | Latenza | Candidato 3080 | VRAM stimata | Guadagno realistico | Parallelizzabile |
|---|---|---:|---|---:|---:|---|
| VAD / endpoint | Silero VAD, CPU | 2.000 ms fissi | no | 0 | nessuno dalla GPU | durante l'audio |
| Speaker authentication | Resemblyzer GE2E, CPU | benchmark locale: 18 ms mediana, 44 ms max | non utile | <0,1 GB | trascurabile | si', ma oggi precede STT |
| STT | faster-whisper `large-v3`, FP16, CUDA:1 su 4060 Ti | 0,845 s media; 1,451 s P95 | si' | 3,5-5 GB | 0,2-0,5 s medi | non oggi: serve il testo finale |
| Semantic/intent | SemanticTurnService, Gemma4 26B, 2x4060 Ti | 8,116 s media; 9,744 s P95 | solo con modello compatto sostitutivo | 2-6 GB | 4-7 s stimati | con embedding, dominio e proposal |
| Intent regex | router deterministico CPU | 0 ms mediana; 11 ms max | no | 0 | nessuno | si' |
| Embedding | `multilingual-e5-large`, CPU FP32 | 69 ms media nel benchmark; 85 ms max | si', ma poco utile | 1,2-1,8 GB FP16; 2,2-2,8 GB FP32 | 40-60 ms/query | si', appena finisce STT |
| RAG | Redis + dual-channel + domain gating Gemma4 | 3,587 s media; 7,582 s P95 | soltanto il domain LLM | condivisa col service LLM | 1,7-2,5 s tipici | in buona parte |
| Domain classification | Gemma4, output massimo 30 token | contributo mediano circa 2,08 s quando attivo | si' | condivisa | circa 1,8-2,3 s | con Semantic Turn |
| Reranking | sort epistemico deterministico CPU; nessun CrossEncoder | <1 ms stimato, incluso nel RAG | no | 0 | nessuno | irrilevante |
| Retrieval Strategy | Gemma4, JSON, `think=True`, condizionale | 12,33 s osservati | si', con modello compatto | condivisa | 8-11 s sui turni attivati | puo' partire in parallelo |
| ActionController | Gemma4, JSON, condizionale | 11,70 s osservati | si', con golden set safety | condivisa | 8-11 s sui turni attivati | proposal si'; esecuzione no |
| Brain | Gemma4 26B A4B, 2x4060 Ti | 5,662 s media; 7,493 s P95 | no per decisione progettuale | 17,99 GB di modello + KV, circa 9 GB di soli pesi/GPU | non applicabile | convive con servizi sulla 3080 |
| TTS | Piper Paola/Lessac, sherpa-onnx CPU, 4 thread | 0,545 s media; 0,783 s P95 | non col runtime attuale | modelli da 63 MB ciascuno | 0,2-0,4 s massimi | oggi solo synth successiva/playback |
| Visione | Gemma4 vision sulle due 4060 Ti | nessun campione nei log | si', con VLM separato | 4-9 GB | solo sui turni vision | si', senza P2P |

## RAM e VRAM osservate o stimate

- Gemma4 Ollama: blob modello da 17.987.569.344 byte. Diviso sulle due 4060 Ti,
  sono circa 9 GB di soli pesi per GPU, oltre a KV cache e buffer.
- faster-whisper large-v3: cache modello da circa 3,09 GB; stima operativa
  3,5-5 GB di VRAM con workspace.
- E5-large: cache da circa 2,2 GB; benchmark completo a 2,66 GB RSS. In FP16
  richiederebbe circa 1,2-1,8 GB di VRAM.
- TTS: Paola e Lessac hanno pesi da circa 63 MB ciascuno.
- Resemblyzer: checkpoint da circa 17 MB.
- Il consumo live di Redis e la VRAM a tre GPU non erano disponibili durante
  l'audit e non devono essere trasformati in numeri inventati.

## Perche' spostare soltanto E5 non serve

Il secondo canale RAG, con dominio ed embedding gia' in cache, richiede
tipicamente 0,4-0,7 s. E5 codifica una query in circa 67 ms di mediana sulla
CPU della P620. La parte dominante del primo canale e' `assign_domain()`, che
chiama Gemma4.

Non esiste un reranker neurale nel percorso attuale: `_rank_epistemically()`
applica un ordinamento CPU stabile basato su rischio, provenienza e pertinenza.
Un eventuale CrossEncoder sarebbe un esperimento di qualita', non una cura per
la latenza attuale.

## Sovrapposizioni tecnicamente sensate

### Dopo lo STT

Appena esiste il testo finale si potrebbero lanciare in parallelo:

- Semantic Turn;
- embedding E5;
- classificazione del dominio;
- eventuale proposal dell'ActionController;
- eventuale classificazione della strategia di retrieval.

La proposal dell'ActionController resterebbe soltanto speculativa: nessuna
azione dovrebbe essere eseguita prima del veto/consenso semantico e dei gate
deterministici.

### STT incrementale

Non esiste oggi. Il VAD accumula l'intero segmento, attende 2 secondi e poi
chiama Whisper. Avviare semantic/embedding su trascrizioni parziali richiede
una pipeline streaming e introduce il rischio di usare testo ancora
correggibile. Non e' un semplice uso della 3080.

### TTS durante la generazione

Non esiste oggi. Il brain usa una chiamata Ollama non streaming; la risposta
viene completata e sottoposta ai guard prima della sintesi. La segmentazione
TTS sovrappone soltanto la sintesi del segmento successivo alla riproduzione
del precedente.

Lo streaming LLM -> guard incrementale -> TTS potrebbe ridurre sensibilmente
il tempo percepito, ma e' indipendente dalla 3080 e cambia il contratto di
sicurezza/qualita' della risposta.

## Massimo tre impieghi della RTX 3080

### 1. Control-plane LLM residente

E' l'unico impiego ad alto beneficio atteso. Un modello compatto separato
potrebbe produrre Semantic Turn, domain classification, addressedness,
Retrieval Strategy e proposal dell'ActionController.

Modelli compatti gia' presenti localmente:

| Modello | Dimensione del blob |
|---|---:|
| `qwen3.5:0.8b` | 1,04 GB |
| `granite3.3:2b` | 1,55 GB |
| `qwen2.5:3b` | 1,93 GB |
| `llama3.2:3b` | 2,02 GB |

La loro presenza non dimostra che siano abbastanza affidabili per il
contratto semantico di Euri. Prima di qualunque adozione devono superare un
golden set su:

- intent e speech act;
- negazioni e ipotesi;
- correzioni di identita' e fatti;
- `candidate/ephemeral/no_store`;
- bisogno di memoria e fonti mancanti;
- risposta contro esecuzione;
- autorizzazione, capability e target dei tool;
- astensione fail-closed.

Stima da verificare: 5-8 secondi di riduzione sulla mediana e 8-12 secondi sui
turni che oggi attivano i controller lenti. La mediana fisica potrebbe passare
indicativamente da circa 20 secondi a 12-15 secondi.

### 2. Whisper large-v3 dedicato

E' il trasferimento piu' semplice e meno rischioso. Mantiene lo stesso modello
e la stessa qualita' di trascrizione, isola il contesto CUDA dalle GPU target e
puo' ridurre lo STT di circa 0,2-0,5 secondi medi. Da solo non giustifica la
3080; e' un complemento al control-plane.

### 3. Visione futura isolata

Un encoder o VLM compatto potrebbe analizzare immagini sulla 3080 senza
occupare le GPU target. E' utile per isolamento e parallelismo, ma non riduce
la latenza delle normali conversazioni vocali. Un VLM da 7-9 GB sarebbe inoltre
incompatibile con la residenza simultanea di Whisper e del control-plane.

## Compatibilita' e isolamento necessari

- RTX 3080 Ampere e RTX 4060 Ti Ada sono compatibili in linea di principio con
  driver 610.43.02, CTranslate2 4.7.1, Torch CUDA 13 e Ollama 0.30.7.
- P2P non serve: audio, testo, JSON ed embedding passano via host e sono piccoli.
- Un riser PCIe 4.0 x4/x8 e' sufficiente dopo il caricamento dei modelli; restano
  da verificare alimentazione, 320 W della 3080, airflow e integrita' del link.
- L'onnxruntime installato espone soltanto `CPUExecutionProvider`: il TTS non e'
  oggi trasferibile su CUDA senza cambiare pacchetto/build.
- Gemma4 pesa piu' dei 10 GB della 3080: gli attuali controller non possono
  essere spostati identici sulla terza GPU.

Per impedire che Ollama distribuisca Gemma4 anche sulla 3080 servirebbero due
istanze isolate:

1. istanza realtime visibile soltanto alle due 4060 Ti;
2. istanza service su un'altra porta, visibile soltanto alla 3080;
3. selezione preferibilmente tramite UUID GPU, non indice numerico;
4. client separato per i soli classificatori di servizio.

Una singola istanza Ollama visibile a tutte e tre le GPU non soddisfa il
requisito di isolamento.

## Condizioni per riaprire il lavoro

### MTP

Non riaprire per:

- provare un altro `n_max` sugli stessi prompt;
- eliminare qualche copia host/device;
- inseguire il throughput isolato;
- spostare MTP su una terza GPU.

Riaprire soltanto se un aggiornamento upstream o un nuovo runtime dimostra, su
prompt reali di Euri e a parita' di risposta, un vantaggio end-to-end superiore
al rumore e senza regressioni di qualita'.

### RTX 3080

Non installarla per il solo STT, E5 o TTS. Riaprire il filone prestazionale
soltanto con questo protocollo:

1. modello control-plane scelto e bloccato;
2. golden set congelato prima del benchmark;
3. confronto di qualita' contro Gemma4;
4. due istanze Ollama isolate per GPU;
5. misura `fine voce fisica -> primo audio` su turni organici;
6. almeno mediana e P95, inclusi ActionController e Retrieval Strategy;
7. rollback immediato se cala la fedelta' semantica o la safety dei tool.

## Verdetto finale

Senza un control-plane LLM compatto, la RTX 3080 non e' utile a Euri in misura
significativa: il guadagno teorico complessivo resta sotto circa 0,6 secondi.

Con un control-plane compatto che mantenga la qualita', la 3080 potrebbe invece
ridurre la latenza percepita di diversi secondi e soprattutto eliminare le code
da 10-12 secondi prodotte oggi dai classificatori Gemma4 seriali. Questo e'
l'unico percorso che giustifica un futuro test a tre GPU.

## Riferimenti nel repository

- `config.py`: modelli, VAD, semantic turn, TTS e feature flag;
- `voice/stt.py`: faster-whisper e selezione GPU;
- `core/semantic_turn.py`: frame semantico Gemma4;
- `core/domain_gater.py`: classificazione dominio Gemma4;
- `core/retrieval_strategy.py`: strategia di recupero condizionale;
- `core/action_controller.py`: proposta operativa Gemma4;
- `core/embedder.py`: E5-large su CPU;
- `core/memory_risk.py`: reranking epistemico deterministico;
- `core/brain.py`: brain non streaming e vision;
- `voice/tts.py` e `voice/tts_pipeline.py`: TTS CPU segmentato;
- `scripts/measure_euri_performance.py`: parser delle fasi strumentate;
- `docs/EURI_GEMMA4_SPECULATIVE_AB_2026-08-23.md`: storico speculative decoding;
- `docs/EURI_P620_PERFORMANCE_RESULTS_2026-08-24.md`: baseline migrazione P620.
