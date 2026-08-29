# Recupero sistemico — preregistrazione 2026-08-29

Stato: **protocollo congelato prima dell'implementazione**

## Domanda

Il percorso corrente possiede gia' frame semantico, continuita', retrieval a tre
livelli, dual-channel e Loop 2j. Il caso live ICMA2 del 29 agosto ha mostrato pero'
che un follow-up anaforico puo' perdere il soggetto fra questi componenti e che una
richiesta di ricerca associativa interna puo' essere instradata sul Web.

L'intervento verifica se un contratto bounded di continuita' del recupero aumenta
la disponibilita' delle fonti dirette senza trasformare la history in una nuova
fonte fattuale, senza aprire schemi da entita' inventate e senza contaminare la
memoria con ricerche esterne fuori tema.

## Ipotesi

**H1 — continuita' del soggetto.** Quando il turno corrente richiede memoria e non
contiene un focus utilizzabile, l'ultima entita' nominale di un turno owner
affidabile nello stesso scope/segmento permette a identifier-first e Loop 2j di
recuperare almeno una fonte pertinente in piu' rispetto alla query anaforica raw.

**H2 — conservativita'.** Un piano affidabile `needed=false`, un cambio di scope,
una coreferenza pronominale non nominale o l'assenza di una richiesta mnemonica
non ereditano entita' e non cambiano la query.

**H3 — confine Web.** `WEB_SEARCH` e' eseguibile soltanto quando il frame conserva
una richiesta esplicita con evidenza letterale nel turno corrente. Un risultato
Web che non sostiene le entita' nominali della query puo' essere mostrato come
esito esterno fragile, ma non entra in `euri:memory:*`.

## Trattamento congelato

1. Il frame corrente resta immutato e continua a essere la sola interpretazione
   del turno.
2. Un resolver puro costruisce una vista effimera `retrieval_resolution`:
   query raw, query effettiva, piano corrente, eventuali focus contestuali,
   `source_turn_ref`, scope e motivo della decisione.
3. I focus contestuali sono ammessi soltanto se:
   - il turno corrente ha un piano affidabile `needed=true` oppure lo speech act
     affidabile `REQUEST_MEMORY_SEARCH`;
   - il focus corrente e' vuoto;
   - il turno sorgente e' l'ultimo turno owner con entita' nominali nello stesso
     scope e segmento;
   - l'entita' e' sostenuta dalla superficie nominale del turno sorgente, non da
     una risoluzione pronominale isolata.
4. La query effettiva alimenta ricerca a tre livelli, schema 2j e priorita' della
   fonte diretta. Raw, turni, frame, memoria e provenance non vengono riscritti.
5. Il Web richiede un contratto semantico grounded nel turno corrente. Il gate
   decide soltanto l'autorizzazione; non decide la verita' dei risultati.
6. Il salvataggio Web richiede che almeno un'entita' nominale della query sia
   sostenuta dai risultati restituiti. Il fallimento non cancella la risposta e
   non crea memoria cognitiva.

## Casi di sviluppo preregistrati

| ID | Caso | Esito atteso |
|---|---|---|
| R1 | query esplicita `ICMA2/FPP20` | query invariata, nessuna eredita' |
| R2 | `nelle tue memorie dovresti avere i dettagli` dopo ICMA2 | query effettiva include ICMA2 e recupera la fonte diretta |
| R3 | piano affidabile `needed=false` | nessuna query estesa, schema chiuso |
| R4 | ultimo turno nominale appartiene a un nuovo tema | eredita' solo dal turno piu' recente, non dal tema precedente |
| R5 | entita' disponibile soltanto in altro scope/segmento | nessuna eredita' |
| R6 | `loro -> Gio Style` risolto solo come coreferenza | nessuna ancora contestuale mutante |
| R7 | frame assente o a bassa confidenza | percorso legacy invariato |
| W1 | `cerca sul Web ...` con evidenza grounded | route Web autorizzata |
| W2 | `ricerca associativa nelle memorie` classificata Web dal modello | route Web negata |
| W3 | risultati Web senza l'entita' nominale della query | risposta consentita, memoria Web non salvata |
| W4 | risultati Web che sostengono l'entita' nominale | lifecycle Web corrente invariato |

## Metriche e criteri

- recall strutturale: presenza dell'ID diretto atteso nel pacchetto RAG;
- precisione del carry-over: zero entita' ereditate nei controcasi R3-R7;
- autorita': nessuna reflection o memoria derivata promossa a fonte diretta;
- provenienza: ogni focus ereditato conserva `source_turn_ref` e non compare nel
  frame o nel documento mnemonico;
- isolamento Web: W2 e W3 producono zero nuovi documenti memoria;
- regressioni: manifest unitario completo verde;
- latenza: nessuna chiamata LLM aggiuntiva nel resolver; una query effettivamente
  estesa puo' perdere il riuso del prefetch raw, evento che deve essere visibile
  nella diagnostica e misurato nel successivo collaudo live.

## Stop e rollback

- Se un controcaso eredita un soggetto, il trattamento e' NO-GO.
- Se il resolver richiede un LLM o muta frame/history/memorie, il trattamento e'
  fuori protocollo.
- Se la suite esistente perde un caso di alias, scope, retrieval temporale o
  Web esplicito, non si attiva il runtime.
- Rollback comportamentale previsto tramite un solo flag di configurazione;
  nessuna migrazione Redis e nessuna riparazione delle memorie esistenti fanno
  parte di questo intervento.

## Limite dichiarato

Questa fase migliora il recupero e il confine Web. Non dichiara risolta la
frammentazione del learner passivo: il legame strutturato fra fatto atomico e
progetto sorgente richiede un'ablation separata sotto `MEM-05`, perche' cambia la
forma dei nuovi documenti e non va fittato sul solo episodio ICMA2.

## Esito pre-runtime — 29 agosto 2026

Stato: **GO alle regressioni, collaudo live ancora aperto**.

- R1-R7 verdi: focus esplicito invariato, follow-up ICMA2 esteso, ultimo tema
  nominale selezionato, `needed=false`, scope/segmento, pronomi, bassa confidenza
  e rollback restano conservativi.
- W1-W4 verdi: autorizzazione Web grounded preservata, ricerca mnemonica
  misinstradata chiusa su SEARCH, risultato senza entità non persistito e
  risultato pertinente conservato col lifecycle Web preesistente.
- Nessuna chiamata LLM è stata aggiunta dal resolver; nessuna migrazione o
  riparazione Redis è stata eseguita.
- Manifest unitario completo: 90/90 in 76,6 secondi. Compilazione dei moduli e
  controlli di whitespace verdi.

Il daemon già in esecuzione non è stato riavviato e usa ancora il codice caricato
prima dell'intervento. L'accettazione organica e la misura del costo di fallback
del prefetch restano quindi il passo successivo, non un risultato già osservato.
