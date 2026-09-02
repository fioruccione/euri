# Euri — preregistrazione chiusura owner delle correzioni proposte

**Data:** 1 settembre 2026

**Stato iniziale:** preregistrato prima di modifiche runtime

## 1. Finding organico congelato

Il 1 settembre il Loop 2g ha ricevuto otto correction signal. Prima della
riparazione manuale delle presse lo stato era: cinque `proposed`, due
`dismissed`, uno `analyzed`. Il ramo `proposal_only` conserva correttamente il
verdetto senza mutare memorie, ma nessun componente legge
`requires_owner_confirmation=true`: dopo l'analisi il signal non è più
`pending`, il 2g non lo riprende e il TTL di trenta giorni può eliminarlo.

La diagnosi non è «Loop 2g morto». Il signal `4b1926a3`, autorizzato come
`explicit_correction`, ha creato la reaction lesson `045762c2`. Il difetto è il
solo arco mancante `proposed → domanda owner → decisione → commit/chiusura`.

Corpus organico congelato:

| signal | proposta 2g | natura osservata |
|---|---|---|
| `93528e0c` | `bad_memory` | GPU presentata erroneamente come fase Regrado PP |
| `b1af1a40` | `bad_reasoning` | ICMA 2: bivite, non monovite |
| `079752d1` | `bad_memory` | UBQ, non OBQ |
| `71015624` | `bad_reasoning` | progetto UBQ, non BQ |
| `7300a04d` | `bad_memory` | preventivo Bini fabbricato senza sorgente |
| `4b1926a3` | `bad_reasoning`, mutante | lezione sulla dotazione Yizumi già creata |
| `ebab7470` | `not_a_correction` | domanda sulle barzellette, vero negativo |
| `72d49f84` | falso negativo, poi riparato | vite bimetallica Yizumi/Chen Hsong |

Al momento della preregistrazione lo stato corrente è cinque `proposed`, uno
`dismissed`, uno `analyzed`, uno `resolved`: il cambiamento riguarda soltanto
`72d49f84`, riparato append-only da Codex.

## 2. Ipotesi

H1. `proposal_only` è il confine corretto prima della parola dell'owner: il
giudice notturno può proporre, mai concedersi autorità retroattiva.

H2. La proposta può essere chiusa con una domanda deterministica che mostri la
correzione originale e, quando disponibile, l'esatto nodo candidato. Non serve
un altro giudice LLM per formulare il significato della domanda.

H3. Una risposta dell'owner autorizza una sola di quattro transizioni:

- `APPLY`: correggere l'esatto antecedente mostrato;
- `SEPARATE`: registrare la correzione come memoria user indipendente;
- `DISMISS`: chiudere il signal senza scrivere memoria;
- `LATER`: mantenere `proposed` e rinviare la domanda.

H4. Voce e Silent Chat devono competere sulla stessa lease Redis. Una proposta
non può essere chiesta contemporaneamente nei due canali né sopravvivere come
pending in-memory non auditabile.

## 3. Trattamento congelato

1. Un servizio condiviso seleziona soltanto signal `proposed`, nello scope
   personale corrente, con `requires_owner_confirmation=true`, contratto owner
   corrente e senza rinvio attivo. L'ordine è oldest-first. La versione del
   contratto viene apposta dal 2g prima di rendere il signal `proposed`: i signal
   storici non versionati restano replayabili, ma non entrano nella coda runtime.
2. La claim è un `SET NX` con TTL breve e token casuale. Soltanto il possessore
   del token può applicare, rinviare o chiudere la proposta.
3. Il resolver bounded esistente cerca un antecedente. Se ne trova uno, la
   domanda cita un estratto del nodo e offre `APPLY` oppure `SEPARATE`; se si
   astiene, offre `SEPARATE` oppure `DISMISS`. Nessuna memoria cambia qui.
4. `APPLY` rilegge il nodo e verifica scope, assenza di supersessione e hash del
   contenuto mostrato. Solo allora crea la versione user pending e usa
   `link_correction`, che collega vecchio e nuovo atomicamente.
5. Se la riscrittura coincide con il contenuto già attivo, nessun duplicato:
   il signal si chiude `resolved/already_present`.
6. Signal e audit vengono marcati soltanto dopo l'effetto. Un errore lascia la
   proposta recuperabile; nessun successo viene dichiarato prima del commit.
7. Il nuovo circuito non modifica classificatore 2g, Dream 2c/2f/2h, learner,
   ranking RAG o gate di promozione.

## 4. Test congelati

- C1: prima della risposta owner, claim e domanda non mutano alcuna memoria.
- C2: due canali non possono ottenere la stessa lease.
- C3: un target risolto produce una domanda con correzione e anteprima esatte.
- C4: `APPLY` collega atomicamente il target mostrato e risolve il signal nella
  stessa transazione Redis.
- C5: target modificato o già superseded → zero scritture e proposta riapribile.
- C6: `SEPARATE` crea una memoria user senza supersedere alcun nodo.
- C7: `DISMISS` chiude senza memoria; `LATER` conserva `proposed` con backoff.
- C8: una correzione già presente non genera un doppione.
- C9: signal `pending`, `dismissed`, `analyzed`, `resolved` o di altro scope non
  sono selezionabili.
- C10: parser delle risposte non tratta un turno non classificabile come consenso.
- C11: regressioni correction resolver, quarantine, Initiative, voice pending e
  manifest unitario completo verdi.
- C12: un target KNN estraneo alla provenienza del signal non viene offerto come
  antecedente; una memoria separata richiede una nuova frase owner esatta.
- C13: i cinque signal storici non versionati sono visibili al replay esplicito,
  ma voce e Silent Chat non possono reclamarli.

## 5. Criterio di uscita

GO soltanto se C1-C13 sono verdi e il replay in sola lettura dei cinque signal
organici `proposed` produce domande auditabili senza alcuna mutazione. Il live
successivo deve usare un signal nuovo; non si consumano automaticamente i cinque
casi storici durante lo sviluppo.

Rollback: disabilitazione del nuovo offer/review; i signal restano documenti di
audit e nessun link già committato viene cancellato.

## 6. Deviazione dichiarata dopo il primo replay di sviluppo

Il primo replay read-only sui cinque casi congelati ha falsificato la forma più
larga di H2: rilanciare oggi il KNN sull'intero archivio ha associato UBQ alla
memoria su Federico Cella e la fabbricazione Bini al confronto reale fra Yizumi
e Chen Hsong. Entrambi i nodi erano fuori dalla provenienza registrata nel
signal. Il corpus è development evidence, quindi il trattamento viene corretto
prima del GO e il replay va ripetuto integralmente.

Vincolo aggiunto: un target trovato oggi è proponibile soltanto se il suo ID era
già nel `rag_ctx`, nei candidati di risoluzione 2g o nella quarantena del signal.
Fuori da questo insieme il resolver si astiene. Inoltre `SEPARATE` non salva il
testo conversazionale della vecchia correzione: chiede all'owner una seconda
frase esatta e salva soltanto quella. Nessuna LLM distilla silenziosamente il
nuovo fatto.

## 7. Esito offline del 2 settembre 2026

**Decisione:** GO offline; live ancora aperto e in coda dietro `RETR-03`.

- C1-C13 verdi nelle regressioni dedicate. Il test del 2g verifica che una nuova
  proposta riceva `owner_review_contract_version=1`; un test separato verifica
  che un signal legacy sia visibile con `include_legacy=True` soltanto al replay,
  mentre la claim runtime si astiene senza creare lease.
- Il replay reale finale sui cinque signal storici si è astenuto 5/5: ogni KNN
  risolto era fuori dalla provenienza congelata oppure non esisteva un target
  sostenuto. Snapshot dei documenti e namespace lease identici prima e dopo
  (`READ_ONLY_OK`).
- La Lua reale ha collegato vecchia memoria, nuova versione e signal in una sola
  esecuzione su Redis Stack; la sonda usava tre chiavi effimere con TTL e le ha
  eliminate nel `finally`.
- Compilazione dei moduli modificati e `git diff --check` verdi. Manifest
  unitario completo sul commit candidato: **92/92 in 78,7 secondi**.

Il GO non afferma ancora che una nuova correzione organica venga chiusa bene
end-to-end. Afferma che il circuito è fail-closed offline, non consuma il corpus
storico e può essere sottoposto a quel live senza concedere autorità al giudice
notturno.
