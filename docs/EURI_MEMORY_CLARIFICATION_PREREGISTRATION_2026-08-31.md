# Chiarimento mnemonico conversazionale — preregistrazione 2026-08-31

Stato: **protocollo congelato prima dell'implementazione**

## Problema osservato

Nel test live «La pompa e' prima o dopo il filtro?» il frame Gemma ha indicato
fra i `missing_facts` la configurazione specifica del sistema, ma ha lasciato
`requires_clarification=false`. Il RAG ha quindi recuperato memorie ICMA2
pertinenti ma non grounded dal turno corrente e il Brain ha scelto quel soggetto
senza chiedere conferma.

Il difetto non e' l'assenza delle memorie e non e' una contraddizione da
risolvere nel database: e' un riferimento conversazionale insufficiente prima
della risposta.

## Ipotesi

**H1 — comportamento da collega.** Quando una domanda non identifica il
referente e interpretazioni incompatibili cambiano materialmente la risposta,
Euri formula una sola domanda breve del tipo «intendi A o B?» invece di scegliere
silenziosamente il candidato RAG piu' disponibile.

**H2 — non esitazione.** Un soggetto nominato nel turno, risolto dalla
conversazione recente o accompagnato da uno stato esplicito (`attuale`,
`proposto`, ecc.) continua a ricevere una risposta diretta.

**H3 — integrita' mnemonica.** Lo scambio che apre il chiarimento non produce
memorie passive e non autorizza save, supersessione, consolidamento o Dream. Il
verbatim resta nell'archivio conversazionale ordinario.

## Trattamento congelato

1. Il frame semantico usa `requires_clarification=true` soltanto quando il
   referente non e' risolvibile dal turno e dal dialogo recente e scegliere una
   delle alternative cambierebbe la risposta.
2. Il RAG resta read-only e puo' recuperare candidati utili a formulare le
   alternative. Il loro recupero non rende grounded il soggetto.
3. Un gate post-RAG aggiunge al prompt un contratto obbligatorio: non rispondere
   alla domanda sottostante, chiedere un solo chiarimento naturale e presentare
   al massimo due alternative sostenute dal contesto.
4. Il gate non genera la frase con regole di dominio e non chiama un secondo
   modello. Gemma formula la domanda usando il pacchetto gia' disponibile.
5. Il frame passato al Brain viene copiato e annotato
   `memory_clarification_required=true`; raw, frame originario, memorie e RAG non
   vengono mutati.
6. `memory_clarification_required` blocca il learner passivo per la domanda e la
   relativa risposta. Non crea una chiave pending cognitiva: la domanda esplicita
   nel normale storico conversazionale e' il contesto del follow-up.
7. Il trattamento e' disattivabile con
   `EURI_MEMORY_CLARIFICATION_ENABLED=0`; nessuna migrazione Redis e' richiesta.

### Precisazione pre-collaudo sulla confidenza

La sonda immediatamente successiva alla modifica del prompt, ancora prima del
collaudo del gate, ha prodotto un frame coerente ma con confidenza globale zero:
`requires_clarification=true`, `memory_retrieval.needed=true` senza focus
inventato ed `evidence_request.dependency=required` con un fatto discriminante
mancante. Gemma stava usando la confidenza globale per rappresentare
l'impossibilita' di scegliere la configurazione, non l'incertezza sulla necessita'
di chiedere.

Il trattamento resta fail-closed sulle mutazioni e accetta una confidenza globale
bassa soltanto se questi tre segnali strutturali concordano nello stesso frame.
Una bassa confidenza priva della concordanza resta percorso legacy. La diagnostica
distingue `frame_confidence` da `structural_convergence`; questa precisazione e'
congelata prima di qualunque risposta runtime valutata col gate attivo.

## Casi preregistrati

| ID | Caso | Esito atteso |
|---|---|---|
| C1 | «La pompa e' prima o dopo il filtro?» senza storia | una sola domanda di chiarimento; nessuna topologia asserita |
| C2 | «Nella configurazione attuale di ICMA2, la pompa e' prima o dopo il RAS500?» | risposta diretta |
| C3 | «Con la FPP20 proposta, la pompa e' prima o dopo il RAS500?» | risposta diretta |
| C4 | domanda anaforica dopo un turno owner con focus nominale ICMA2 | risposta diretta usando il resolver sistemico |
| C5 | due alternative incompatibili presenti nel RAG | domanda con massimo due alternative realmente sostenute |
| C6 | frame fallback, bassa confidenza senza concordanza strutturale o turno operativo | percorso precedente invariato |
| C7 | scambio di chiarimento | domanda e risposta di Euri escluse dal learner passivo |
| C8 | flag disattivato | prompt e frame byte-equivalenti al percorso precedente |

Orione/BX17 non e' un test di ragionamento logico di questo trattamento. Puo'
restare soltanto come controllo diagnostico di contaminazione temporale e non
concorre al criterio primario.

## Criteri e stop

- C1, C2, C3, C6, C7 e C8 devono essere verdi nelle regressioni pure.
- Il replay live appaiato deve mantenere corretta la topologia ICMA2 nei casi
  grounded e trasformare il solo caso senza focus in chiarimento.
- Zero nuove chiamate LLM nel gate e zero scritture Redis durante il replay.
- Se un caso grounded viene bloccato, se il gate dipende da nomi/regex di dominio
  o se il learner salva lo scambio ambiguo, il trattamento e' NO-GO.

## Limite dichiarato

Il primo trattamento risolve il turno che deve chiedere. La qualita' con cui un
follow-up molto ellittico risolve l'alternativa attraverso lo storico resta una
misura separata: non verra' dichiarata chiusa senza una prova reale dedicata.
