# Euri V2.21 — Memoria fondata sulla fonte

**Data della fotografia:** 28 luglio 2026

**Stato:** piattaforma sperimentale locale operativa; non prodotto finito

**Versione precedente dichiarata:** V2.20, 12 giugno 2026

**Perimetro:** stato del sistema dopo il ciclo di misura e hardening della memoria
di luglio 2026

## Perché una nuova versione

V2.20 aveva fissato l'onestà di ground-truth: non trasformare una correzione
fantasma in lezione, non dichiarare azioni mai eseguite e non lasciare che una
sintesi infedele cancelli un fatto molto usato.

V2.21 cambia il contratto con cui Euri usa i ricordi. Una sintesi non è più
considerata un sostituto equivalente dell'esperienza originale. Può aiutare a
localizzarla, ma la risposta deve essere fondata, quando possibile, sul turno
verbatim da cui la sintesi deriva.

Il salto di versione non dichiara risolto il problema generale della memoria.
Fissa un avanzamento architetturale misurato, i suoi limiti e il punto esatto da
cui proseguire.

## Cosa è cambiato

### 1. La memoria viene misurata dall'esterno

È stata costruita una pipeline locale e isolata per LoCoMo in italiano:

- Redis e archivio turni effimeri, separati dalle memorie personali;
- confronto A/B appaiato tra RAG grezzo e memoria passiva;
- manifest firmati, seed, checkpoint/resume e arresti solo tecnici;
- bootstrap clusterizzato per conversazione e analisi avversariale;
- protocollo preregistrato prima dell'apertura dei risultati.

La prima configurazione completa ha mostrato che le note passive occupavano il
`63,4%` degli slot nel braccio misto. Conservavano spesso il puntatore alla
fonte, ma espellevano il testo originale e peggioravano il substrato della
risposta. Il RAG grezzo non conteso copriva il gold nel `71,2%` dei casi; il pool
competitivo raw+passive scendeva al `65,7%`. Il problema non era un contesto più
lungo: era la competizione per posti fissi.

### 2. Dual-channel: la sintesi diventa un locator

La policy `dual-channel-q2r1-v1` separa i ruoli:

- la base RAG esclude `source=passive` ed è protetta integralmente;
- le prime due note passive possono soltanto indicare una fonte;
- per ogni nota viene idratato al massimo un turno originale;
- entrano al massimo due turni aggiuntivi, entro un budget separato;
- il testo della sintesi passiva non entra mai nel prompt;
- una fonte mancante produce astensione del canale, non uso della parafrasi.

I turni originali vivono nell'archivio durevole `euri:turn:*`, con
`turn_ref=conversation_id:seq`. Le nuove memorie passive conservano
`source_turn_refs`; le memorie storiche prive della fonte rimangono auditabili,
ma non vengono promosse a evidenza.

### 3. Risultato indipendente e limite del risultato

Sul census preregistrato di cinque conversazioni LoCoMo allora intatte, due
repliche e 989 domande:

- evidence recall: `+0,0311`;
- recuperi esclusivi: `22`, evidenze perse: `0`;
- token F1: `+0,0023`;
- accuratezza avversariale: `+0,0080`;
- `gold_lost=0`;
- verdetto preregistrato: `GO`.

Il GO è strutturale: la memoria passiva può aggiungere evidenza senza sfrattare
la base. Il guadagno F1 è piccolo e l'intervallo clusterizzato è compatibile con
zero; non dimostra un miglioramento generale della qualità delle risposte.

### 4. Presentazione selettiva dell'evidenza

Una successiva ablation ha mostrato che il turno originale messo davanti al
contesto aiuta nei soli casi in cui porta evidenza nuova:

- flip-set: F1 `0,111 → 0,238`;
- prepend indiscriminato: `-0,0018` F1 globale e `-0,0119` di prudenza;
- contratto `evidence_first`: `-0,0111` F1 globale.

Il runtime personale usa quindi
`dual-channel-selective-prepend-v0`. Un turno viene promosso davanti soltanto
quando supera insieme:

1. rilevanza domanda→fonte;
2. margine rispetto al miglior nodo della base;
3. anti-ridondanza rispetto alla base.

Segnali mancanti o errore dell'embedder mantengono l'append validato. Le soglie
sono provvisorie e osservabili nei log e nella response lineage.

### 5. Un solo percorso per voce, mobile e Silent Chat

La prova reale su `Compound UBQ 2026` ha scoperto che la Silent Chat usava ancora
il builder legacy. V2.21 introduce un dispatcher runtime unico per le modalità
`off|shadow|on|selective` e collega anche la chat testuale all'archivio dei turni.

Dopo il fix, la richiesta reale ha recuperato dalla memoria utente originale:

- modulo a trazione `1000 → 1250 MPa`;
- IZOD `4,5 → 3,8`;
- allungamento a rottura `20% → 9%`.

Il log ha mostrato la memoria user nella base protetta, il passaggio
`RAG dual-channel` e zero aggiunte passive necessarie. È una verifica qualitativa
del percorso condiviso, non un nuovo risultato di benchmark.

### 6. Confini epistemici più netti

Nel medesimo ciclo di sviluppo:

- convergenza interna e validazione esterna sono state separate;
- un ponte `HYPOTHESIS` resta ipotesi e non entra nel RAG come fatto;
- identità, somiglianza e differenza tra progetti vengono giudicate
  semanticamente prima di consolidare o raccontare un'evoluzione;
- una reflection di self-observation non può diventare prova ricorsiva di una
  nuova self-observation;
- correzioni, provenienza fragile e memorie passive incerte restano
  quarantinabili, reversibili e osservabili;
- Pulse e Cognitive Projector conservano eventi e causalità, ma gli eventi non
  diventano automaticamente credenze o memoria recuperabile.

## Invarianti di V2.21

1. **La fonte primaria non viene sfrattata dal canale passivo.**
2. **Una sintesi passiva è un indice, non la prova che sostituisce la fonte.**
3. **Solo un turno originale idratabile può essere aggiunto come evidenza.**
4. **Fonte assente, errore o segnale ambiguo producono un fallback conservativo.**
5. **Analogia non significa identità; convergenza non significa verità esterna.**
6. **La parola esplicita dell'utente prevale sulle derivazioni interne.**
7. **Voce, mobile e Silent Chat condividono policy e fallback del retrieval.**
8. **Le modifiche cognitive restano locali, auditabili e per quanto possibile
   reversibili.**

## Stato dei sottosistemi

| Area | Stato V2.21 |
|---|---|
| Memoria esplicita e RAG | Operativi |
| Passive learner | Operativo, con provenienza e verifica |
| Dual-channel | Operativo in modalità personale `selective` |
| Archivio turni verbatim | Operativo per i nuovi turni |
| Dream Engine e Loop 2a–2i | Operativi con gate epistemici |
| Pulse | Bus afferente operativo |
| Cognitive Projector | Timeline osservazionale operativa |
| Initiative | Operativa su una classe ristretta di eventi |
| Percezione sociale | Fase osservazionale, nessun giudizio LLM |
| AdaptiveClassifier | Sospeso; raccolta dati prima della riattivazione |

## Limiti dichiarati

- Tutte le dieci conversazioni LoCoMo sono ormai state aperte: LoCoMo è un
  development set esaurito per questa linea di esperimenti.
- Il gate selettivo è calibrato su dati aperti; serve un benchmark indipendente.
- Le memorie passive storiche senza `source_turn_refs` non possono idratare il
  dialogo originale.
- Presenza dell'evidenza nel prompt non garantisce che il generatore la usi.
- Il token F1 sottostima alcune parafrasi corrette, specialmente nella
  localizzazione italiana.
- Euri può ancora aggiungere deduzioni non richieste anche quando ricorda il
  fatto corretto: è un limite di generazione, non sempre di memoria.
- Pulse registra ciò che accade, ma non costruisce ancora un quadro situazionale
  temporaneo da iniettare nella conversazione.
- Latenza, complessità operativa e alcuni punti storicamente duplicati tra
  canali restano debito da ridurre.

## Valutazione di maturità

Alla data di questa fotografia:

- **8/10 come piattaforma sperimentale di cognizione persistente locale**;
- **6,5/10 come prodotto finito generalizzabile**.

La forza principale non è l'assenza di errori, ma la possibilità di distinguere
se un errore nasce da retrieval, fonte, sintesi, ranking, presentazione o
generazione e di sottoporre il meccanismo a una prova ripetibile.

## Prossimo confine

1. validare il dual-channel e il gate selettivo su un benchmark nuovo;
2. osservare per alcune settimane l'uso sulle memorie reali;
3. misurare separatamente `recalled` e `used_in_response`;
4. continuare a unificare i percorsi dei diversi canali;
5. progettare dal Pulse un quadro situazionale effimero, senza trasformare
   telemetria e interpretazioni interne in memoria durevole.

## Traccia implementativa

I passaggi principali che delimitano questa versione sono:

- `4c89fa2` — integrazione del dual-channel validato;
- `9e515c2` — ablation sull'utilizzo del prompt;
- `4e464ac` — gate selettivo live;
- `68af3a6` — parità dual-channel della Silent Chat.

La dichiarazione V2.21 è documentale: non migra Redis, non riscrive memorie e non
modifica da sola il comportamento runtime.
