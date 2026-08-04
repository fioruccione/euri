# Euri — registro unico dei lavori aperti

Aggiornato: 04/08/2026

Base documentale: V2.22

Regola: una voce si chiude soltanto con evidenza; se cambia direzione, resta
nello storico con il motivo. Questo file è l'indice operativo. I report
specialistici conservano i dettagli, ma la prossima azione deve comparire qui.

## Prossima azione unica

**PRESENT-01 — completare il collaudo live attraverso un riavvio.**

Il primo pass live ha popolato la capsule e ha verificato il learner, ma ha
esposto focus spostato da `no_store`, pending Initiative solo RAM e perdita di
modalità nel passivo: tutti corretti. Ora riavviare con una domanda ancora
aperta e rispondere senza rispiegare il soggetto. Verificare restore del pending,
focus sostanziale, identità canonica, zero duplicati e zero memoria dal contesto
reidratato. Poi COG-01 torna la prossima azione del filone cognitivo di memoria.

## Registro

| ID | Priorità | Stato | Evidenza attuale | Trigger / prossimo passo | Criterio di chiusura |
|---|---|---|---|---|---|
| PRESENT-01 | P0 | primo pass live riuscito, restart finale da validare | capsule v1 popolata; focus frame-based; turni diretti Initiative context-only; pending TTL; passivo usa identità/modalità del frame; bonifica Gio Style append-only; regressioni workspace 8/8 e unit 70/70 | domanda Initiative o assistant ancora aperta → stop/restart → risposta ellittica; ispezionare restore e nuove memorie | pending/focus ripresi al primo turno, fonti visibili, 0 duplicati e 0 apprendimento dal restore; scadenza e scope fail-closed |
| COG-01 | P0 | correzione implementata, validazione aperta | il vecchio 2h usava una label senza prova e dava `UNKNOWN` 0/6; il nuovo contratto richiede claim subject, base esplicita ed estratti verificabili, con backoff degli irrisolti. Diagnostica sul banco aperto: 0/12 vere supersessioni riaperte, 12/12 soggetti distinti riconosciuti, 6/6 ambigui senza mutazione | preregistrare challenge opportunity-first nuovo; non riusare 42 casi come validazione | effetto incrementale misurato su false e vere supersessioni; zero danno oltre il gate congelato |
| MEM-01 | P0 | osservazione con sentinella | `gather_grounded_evidence` usa una finestra non ordinata di 800; bacino personale **668/800** al 30/07 | riaprire a **750**; confrontare in shadow document-frequency RediSearch contro calcolo legacy, senza alzare 800 | selezione completa o errore quantificato sull'intero corpus, con rollback |
| BENCH-01 | P0 | da progettare | tutte le 10 conversazioni LoCoMo sono ormai development set aperto | collegare LongMemEval, privilegiando aggiornamento, temporalità, provenienza e astensione | primo campione indipendente congelato prima di risultati e tuning |
| OPS-01 | P1 | difetto verificato | `_run_due_idle_cycles` e `_run_dream_cycle` hanno ordine diverso; il forzato chiama due volte entrambi i cleanup | rendere il percorso forzato composizione delle stesse fasi del runtime, prima di altre ablazioni dei loop | stessa sequenza/moltitudine provata da regressione |
| COG-02 | P1 | bloccato da COG-01 | structured v2: contratto 76/76 e zero false supersessioni, ma recall vero 50%; post-hoc: `mutually_exclusive` 13/20 e due percorsi disgiunti | solo dopo COG-01 e su challenge nuovo: A=`same entity+claim+replacement`; B=`same entity+claim+same known kind+exclusive`; sul banco aperto A+B è 20/20 con una falsa sup., soltanto tetto fittato | batte il legacy sui gate congelati senza riuso del challenge v2 come validazione |
| COG-03 | P1 | da preregistrare | `used_in_response` prova esposizione sostenuta, non utilità causale | leave-one-out appaiato sui nodi élite, stesso contesto residuo e stesso seed, scoring cieco | effetto causale o risultato negativo attribuibile |
| MEM-02 | P1 | debito di osservabilità | Loop 2d e cleanup possono cancellare; nessun tombstone. Il cleanup Loop 2a con `days_ahead=0` è un no-op verificato | rimuovere/segnalare il codice morto; prima delle ablazioni lifecycle rendere le eliminazioni ricostruibili | ogni mietitore lascia tombstone/copia audit e il ramo eliminato è riproducibile |
| PULSE-01 | P1 | specificato, non iniziato | Fasi 0–2 attive; Fase 3 è descritta in `PULSE_COGNITIVE_ROADMAP.md` | consumer osservazionale per ricorrenza interna, evidenza esterna, cache e tempi lifecycle; nessuna mutazione | metriche durevoli + Control Room, zero policy automatica |
| MEM-03 | P2 | raccolta automatica | utility shadow: 102 risposte, 963 nodi richiamati, 122 usi sostenuti; età osservazione 0,36 giorni, `review_due=false` | attendere ≥14 giorni e ≥100 risposte, oppure 30 giorni; reminder durevole già presente | revisione umana registrata; nessun auto-tuning implicito |
| MEM-04 | P2 | audit pulito | 2.171 turni verbatim, 6 referenziati, 2.165 recenti non referenziati, 0 orfani, 0 riferimenti mancanti | attendere il grace period di 180 giorni; reminder durevole già presente | decisione esplicita cold/pinned/orphan, reversibile; niente cancellazione automatica |
| ARCH-01 | P2 | guardrail presente | test AST: 14 query runtime scopate, una Control Room esente; lo scope dipende ancora dai call site | progettare un handle di ricerca che richieda lo scope e non esponga l'indice grezzo | query runtime non scopata impossibile per costruzione |
| MEM-05 | P2 | finding aperto | estrazione passiva accurata ma costosa e frammentata; il canale passivo ora vale soprattutto come locator | ablazione costo/frammentazione senza perdere provenienza e copertura | meno chiamate/nodi con recall e astensione non peggiori |
| COG-04 | P2 | coppia da trattare insieme | promozione 2c e demozione/cleanup formano l'anello della “reincarnazione” | non ablarli separatamente; definire conseguenza downstream e criterio di rimozione | ciclo misurato come operatore composto o ridisegnato |
| SEM-01 | P2 | rollout osservazionale | frame semantico unico attivo su voce/Mobile/Silent Chat; live: alias `Joe Style→Gio Style`, bootstrap owner senza wake e policy passiva riusciti. Un recall erroneamente `ACTION_REASONING` è stato corretto con contratto risposta≠azione e guard su effetto concreto | osservare casi organici di recall/azione, addressedness e `candidate/ephemeral/no_store`; nessuna regex su nomi o frasi | nessuna azione contestuale priva di effetto grounded e nessuna memoria effimera nei pass osservati, senza perdere richieste operative vere |
| VOICE-01 | P2 | evidenza singola | un follow-up owner è stato rifiutato con speaker similarity `0,645` contro soglia `0,65`, nonostante la sessione fosse aperta; la ripetizione ha ottenuto `0,778` | raccogliere altri near-miss prima di decidere isteresi o fusione con volto/sessione; non abbassare la soglia su un solo caso | policy calibrata su falsi rifiuti e falsi accept, con guest isolation invariata |
| PV-01 | P2 | parcheggiato intenzionalmente | PlastVision è operativo e il T340 raccoglie decine di milioni di letture; Euri e PlastVision affrontano memoria industriale e osservabilità da prospettive complementari. Decisione e fotografia in `EURI_PLASTVISION_DECISION_2026-07-30.md` | riaprire soltanto dopo il collegamento dei PLC/MES mancanti e un periodo di raccolta stabile; pilot shadow su snapshot giornaliero, memoria separata e zero scritture | due settimane appaiate mostrano informazione incrementale con fonti visibili, senza falsi allarmi o autorità operativa |

## Decisioni già prese — non riaprire senza nuova evidenza

- Il Loop 2f legacy resta autorità runtime.
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

## Governance

1. Aggiornare questo registro nello stesso commit che apre o chiude una voce.
2. Una nuova attività deve indicare ID, evidenza, rischio, trigger e criterio di
   chiusura.
3. Un esperimento deve congelare protocollo e gate prima dei risultati.
4. Un reminder automatico già durevole non va duplicato con promemoria umani:
   qui si registra soltanto che esiste e cosa lo fa scattare.
5. La testa di `CODEX.md` e il README devono puntare sempre a questo file.
