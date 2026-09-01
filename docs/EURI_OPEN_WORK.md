# Euri — registro unico dei lavori aperti

Aggiornato: 01/09/2026

Base documentale: V2.27

Regola: una voce si chiude soltanto con evidenza; se cambia direzione, resta
nello storico con il motivo. Questo file è l'indice operativo. I report
specialistici conservano i dettagli, ma la prossima azione deve comparire qui.

## Prossima azione unica

**RETR-03 — chiudere il replay del referente locale con timeout.**

Il live ha mostrato che un referente risolvibile dall'ultima risposta può essere
sovrascritto da una coppia quasi identica presente poco prima nello storico.
Replay conclusi: ultima coppia 2/2 corretta; ultima coppia + RAG 1/2; storia
completa 0/3 anche senza RAG e 0/2 con contratti/metadati aggiunti. Ripetere la
Fase 4 di `EURI_LOCAL_REFERENCE_PRECEDENCE_PREREGISTRATION_2026-08-31.md` con
timeout di trasporto; nessuna modifica runtime prima del risultato.

## Registro

| ID | Priorità | Stato | Evidenza attuale | Trigger / prossimo passo | Criterio di chiusura |
|---|---|---|---|---|---|
| RETR-01 | P0 | implementato, regressioni verdi, live aperto | resolver puro con provenienza del focus; route Web fail-closed; persistenza Web entity-gated; R1-R7 e W1-W4 verdi; manifest 90/90 in 76,6 s | dopo riavvio manuale, ripetere follow-up ICMA2 e ricerca associativa interna; misurare anche il fallback sincrono quando la query effettiva invalida il prefetch raw | stessi soggetti recuperati nei follow-up senza contaminazione fra scope/segmenti; zero Web non autorizzato o fuori soggetto persistito; costo osservato e rollback verificabile |
| RETR-02 | P0 | implementato, prova read-only reale verde, live runtime aperto | protocollo C1-C8; gate post-RAG senza seconda LLM; domanda generica ICMA2 chiarita, attuale/proposta risposte direttamente, follow-up breve risolto; 92/92 unit in 78,7 s | al prossimo riavvio manuale usare la voce senza nominare il progetto, rispondere poi con una delle alternative e ispezionare frame/verbatim/assenza passiva | domanda solo sull'ambiguo, risposta diretta sul grounded, follow-up non sequestrato e zero memoria passiva dello scambio irrisolto; rollback dei due flag verificabile |
| RETR-03 | P0 | controesempio live riprodotto, trattamento non autorizzato | history completa presente ma referente locale perso; last-pair 2/2, last-pair+RAG 1/2, full-history e prompt/metadati 0; Fase 4 sospesa senza report | rieseguire la riscrittura naturale e l'ablazione della coppia quasi duplicata con timeout esplicito, poi congelare regressioni generiche | attribuzione causale distinta tra collisione history e RAG; resolver eventualmente verde su casi generici senza mutare raw o chiedere quando il referente è già grounded |
| PRESENT-01 | P0 | protocollo congelato, prova live da eseguire | 90/90 unit; memoria ICMA2 diretta ripristinata con backup; history+Redis fusi sulle richieste durevoli; source authority Loop 2f; preemption Dream e reason code voce | eseguire in ordine i sette casi di `EURI_LIVE_ACCEPTANCE_2026-08-28.md` dopo un avvio manuale | 7/7 osservati con fonti corrette; pending/focus ripresi una volta; 0 duplicati, 0 falsa esecuzione e nessuna reflection usata come autorità diretta |
| COG-01 | P0 | correzione implementata, validazione aperta | il vecchio 2h usava una label senza prova e dava `UNKNOWN` 0/6; il nuovo contratto richiede claim subject, base esplicita ed estratti verificabili, con backoff degli irrisolti. Diagnostica sul banco aperto: 0/12 vere supersessioni riaperte, 12/12 soggetti distinti riconosciuti, 6/6 ambigui senza mutazione | preregistrare challenge opportunity-first nuovo; non riusare 42 casi come validazione | effetto incrementale misurato su false e vere supersessioni; zero danno oltre il gate congelato |
| MEM-01 | P0 | osservazione con sentinella | `gather_grounded_evidence` usa una finestra non ordinata di 800; bacino personale **668/800** al 30/07 | riaprire a **750**; confrontare in shadow document-frequency RediSearch contro calcolo legacy, senza alzare 800 | selezione completa o errore quantificato sull'intero corpus, con rollback |
| BENCH-01 | P0 | da progettare | tutte le 10 conversazioni LoCoMo sono ormai development set aperto | collegare LongMemEval, privilegiando aggiornamento, temporalità, provenienza e astensione | primo campione indipendente congelato prima di risultati e tuning |
| OPS-01 | P1 | difetto verificato | `_run_due_idle_cycles` e `_run_dream_cycle` hanno ordine diverso; il forzato chiama due volte entrambi i cleanup | rendere il percorso forzato composizione delle stesse fasi del runtime, prima di altre ablazioni dei loop | stessa sequenza/moltitudine provata da regressione |
| PERF-01 | P1 | correzione implementata, collaudo voce aperto | unificato `num_ctx=32768` con guard centrale `RealtimeClient`; 87/87 unit; smoke ActionController reale corretto in 3,110 s, stesso runner prima/dopo e zero `starting llama-server`. Il cambio precedente costava in mediana 10,368 s; protocollo e raw data in `EURI_OLLAMA_CONTEXT_RELOAD_2026-08-26.md` | usare Euri normalmente alternando CHAT/RAG/ACTION e osservare journal, fluidita' e stabilita' VRAM | un solo runner 32768 durante uso organico, nessuna regressione dei contratti e scomparsa dei lag da circa 10 s |
| COG-02 | P1 | bloccato da COG-01 | structured v2: contratto 76/76 e zero false supersessioni, ma recall vero 50%; post-hoc: `mutually_exclusive` 13/20 e due percorsi disgiunti | solo dopo COG-01 e su challenge nuovo: A=`same entity+claim+replacement`; B=`same entity+claim+same known kind+exclusive`; sul banco aperto A+B è 20/20 con una falsa sup., soltanto tetto fittato | batte il legacy sui gate congelati senza riuso del challenge v2 come validazione |
| COG-03 | P1 | da preregistrare | `used_in_response` prova esposizione sostenuta, non utilità causale | leave-one-out appaiato sui nodi élite, stesso contesto residuo e stesso seed, scoring cieco | effetto causale o risultato negativo attribuibile |
| MEM-02 | P1 | debito di osservabilità | Loop 2d e cleanup possono cancellare; nessun tombstone. Il cleanup Loop 2a con `days_ahead=0` è un no-op verificato | rimuovere/segnalare il codice morto; prima delle ablazioni lifecycle rendere le eliminazioni ricostruibili | ogni mietitore lascia tombstone/copia audit e il ramo eliminato è riproducibile |
| PULSE-01 | P1 | specificato, non iniziato | Fasi 0–2 attive; Fase 3 è descritta in `PULSE_COGNITIVE_ROADMAP.md` | consumer osservazionale per ricorrenza interna, evidenza esterna, cache e tempi lifecycle; nessuna mutazione | metriche durevoli + Control Room, zero policy automatica |
| IDENT-01 | P1 | traiettoria architetturale fissata, nessuna implementazione autorizzata | il paper del 2025 colloca identità e continuità nello strato persistente; ICMA2 mostra utilità reale ma anche retrieval rumoroso; l'attribuzione causale della personalità resta sospesa | dopo RETR-01 e PRESENT-01, preregistrare il challenge correzione/evoluzione/contesto/contraddizione descritto in `EURI_IDENTITY_CONTINUITY_TRAJECTORY_2026-08-30.md`; primo trattamento solo offline/shadow | distingue correzione, cambiamento e conflitto irrisolto senza riscrivere il raw, promuovere derivati ad autorità o perdere gli invarianti attraverso un cambio di modello |
| CORR-01 | P0 | implementato, 91/91 unit, riparazione organica applicata, nuovo live aperto | resolver bounded esclude duplicati, legge in isolamento antecedenti quarantinati e si astiene sull'ambiguo; link atomico chiude vecchio/nuovo/signal; memoria completa RAS500 `8696bc28…`, quattro backup Redis e quarantena Markdown verificati | al prossimo avvio ripetere una correzione+save nuova in scope controllato; nessuna generalizzazione a cambi di opinione | relazione `correction_of`/`superseded_by` e signal `resolved` osservati nel normale runtime; nessun duplicato passivo, falsa dichiarazione di commit o supersede estraneo |
| MEM-03 | P2 | raccolta automatica | utility shadow: 102 risposte, 963 nodi richiamati, 122 usi sostenuti; età osservazione 0,36 giorni, `review_due=false` | attendere ≥14 giorni e ≥100 risposte, oppure 30 giorni; reminder durevole già presente | revisione umana registrata; nessun auto-tuning implicito |
| MEM-04 | P2 | audit pulito | 2.171 turni verbatim, 6 referenziati, 2.165 recenti non referenziati, 0 orfani, 0 riferimenti mancanti | attendere il grace period di 180 giorni; reminder durevole già presente | decisione esplicita cold/pinned/orphan, reversibile; niente cancellazione automatica |
| ARCH-01 | P2 | guardrail presente | test AST: 14 query runtime scopate, una Control Room esente; lo scope dipende ancora dai call site | progettare un handle di ricerca che richieda lo scope e non esponga l'indice grezzo | query runtime non scopata impossibile per costruzione |
| MEM-05 | P2 | finding aperto | estrazione passiva accurata ma costosa e frammentata; il canale passivo ora vale soprattutto come locator | ablazione costo/frammentazione senza perdere provenienza e copertura | meno chiamate/nodi con recall e astensione non peggiori |
| COG-04 | P2 | coppia da trattare insieme | promozione 2c e demozione/cleanup formano l'anello della “reincarnazione” | non ablarli separatamente; definire conseguenza downstream e criterio di rimozione | ciclo misurato come operatore composto o ridisegnato |
| SEM-01 | P2 | rollout osservazionale | frame unico attivo; alias esplicita, bootstrap owner, risposta≠azione e fatti v3 verificati. Controesempio live: `loro→Gio Style` ha mostrato che la coreferenza non può autorizzare una proiezione mutante; falso passivo riparato e reflection ritratta. Il gate richiede ora compatibilità nominale e ripristina il baseline sulle riscritture non superficiali; 70/70 unit in 84,4 s | osservare casi organici di recall/azione, addressedness, proiezioni canoniche e `candidate/ephemeral/no_store`; verificare sia varianti STT corrette sia pronomi lasciati intatti. Nessuna regex su nomi o frasi | nessuna azione contestuale priva di effetto grounded, nessuna alias/coreferenza implicita persistente e nessuna memoria effimera nei pass osservati, senza perdere fatti riutilizzabili o richieste operative vere |
| VOICE-01 | P2 | evidenza singola | un follow-up owner è stato rifiutato con speaker similarity `0,645` contro soglia `0,65`, nonostante la sessione fosse aperta; la ripetizione ha ottenuto `0,778` | raccogliere altri near-miss prima di decidere isteresi o fusione con volto/sessione; non abbassare la soglia su un solo caso | policy calibrata su falsi rifiuti e falsi accept, con guest isolation invariata |
| PV-01 | P2 | parcheggiato intenzionalmente | PlastVision è operativo e il T340 raccoglie decine di milioni di letture; Euri e PlastVision affrontano memoria industriale e osservabilità da prospettive complementari. Decisione e fotografia in `EURI_PLASTVISION_DECISION_2026-07-30.md` | riaprire soltanto dopo il collegamento dei PLC/MES mancanti e un periodo di raccolta stabile; pilot shadow su snapshot giornaliero, memoria separata e zero scritture | due settimane appaiate mostrano informazione incrementale con fonti visibili, senza falsi allarmi o autorità operativa |

## Decisioni già prese — non riaprire senza nuova evidenza

- MTP/speculative decoding su Gemma4 e' chiuso per ora: sui prompt reali di
  Euri non migliora il tempo end-to-end e non vanno sviluppate altre patch MTP
  senza un cambiamento upstream materialmente nuovo. Analisi e condizioni di
  riapertura: `EURI_RTX3080_SERVICE_GPU_DECISION_2026-08-26.md`.
- La RTX 3080 non va aggiunta per il solo offload di Whisper, E5 o TTS: il
  beneficio complessivo stimato e' inferiore a circa 0,6 s. Riaprire soltanto
  per un control-plane LLM compatto, isolato e validato su golden set, lasciando
  Gemma4 esclusivamente sulle due 4060 Ti.
- Il classificatore Loop 2f legacy resta autorità runtime; la scelta del
  perdente applica `source-authority-v1` prima della recency.
- `loop2f-structured-affirmative-v2` è diagnostico e **NO-GO**, non una feature
  dormiente da riaccendere.
- Loop 2h usa `loop2h-evidenced-identity-v1`, ma resta una rete di sicurezza
  candidata finché COG-01 non è chiuso su un challenge nuovo.
- LoCoMo può essere usato per sviluppo e diagnosi, non per una nuova
  validazione indipendente.
- Il limite 800 di `gather_grounded_evidence` non va semplicemente aumentato.
- Il lifecycle verbatim resta audit-only: 180 giorni è una soglia di revisione,
  non una scadenza di cancellazione.
- L'utilità osservata non cambia verità, TTL o gate di promozione.
- Non costruire un consumer universale `Pulse → comportamento`.
- Non fondere Euri e PlastVision prima del trigger di `PV-01`; la sospensione è
  deliberata e documentata, non un'attività dimenticata.
- Non importare Letta, Mem0 o un graph database come nuovo proprietario dello
  stato di Euri. Le tecniche esterne restano candidate da preregistrare e
  adattare ai contratti locali; una contraddizione umana non equivale
  automaticamente a un record da sostituire.

## Governance

1. Aggiornare questo registro nello stesso commit che apre o chiude una voce.
2. Una nuova attività deve indicare ID, evidenza, rischio, trigger e criterio di
   chiusura.
3. Un esperimento deve congelare protocollo e gate prima dei risultati.
4. Un reminder automatico già durevole non va duplicato con promemoria umani:
   qui si registra soltanto che esiste e cosa lo fa scattare.
5. La testa di `CODEX.md` e il README devono puntare sempre a questo file.
