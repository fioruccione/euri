# Per Codex — confabulazione in una consolidazione Loop 2e (caso Giada)

Data: 2026-06-23. Scritto da Claude (capofila) per un parere INDIPENDENTE.
Ti do l'evidenza e la domanda; la mia ipotesi è in fondo, marcata come mia: **sentiti
libero di smontarla**. Vogliamo il tuo punto di vista, non una conferma.

## Cosa è successo

Notte 22→23/06, prima notte sui commit `85c0976`+`d9f0d65` (RAG source-aware +
provenienza insight). Tutto pulito, 5 insight promossi decenti. MA il Loop 2e ha
prodotto una consolidazione confabulata su **Giada** — una collega NUOVA, junior,
introdotta a voce QUEL giorno (Stefano: "è arrivata da poco, sta imparando, ha solo
la teoria della chimica", fisicamente in laboratorio).

Nodo `euri:memory:78bc17b6-31fa-456f-9d2b-4ef4b01a7785`, dominio "lavoro":

> "Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica in
> fase di apprendimento delle procedure operative, che lavora presso la Lucy Plast spa
> di Umbertide (PG) **gestendo un team di esperti e collaborando con il collega
> Leonardo; opera in modalità remota da casa**."

Le parti in grassetto sono FALSE: Giada non gestisce un team (è junior), non lavora con
Leonardo (è un collega di Stefano), non è in remoto (è in laboratorio).

## L'evidenza decisiva (la provenienza di oggi l'ha resa leggibile)

`consolidated_from` del nodo = 5 frammenti, tutti `[passive|lavoro]`:

1. "Ha un collega di nome Leonardo." → soggetto implicito = **Stefano**
2. "Lavora da casa in modalità remota." (30/04) → soggetto implicito = **Stefano**
3. "Giada è una nuova collaboratrice di laboratorio con basi teoriche di chimica..." → **Giada** ✅
4. "Lavora presso la Lucy Plast spa e gestisce un team di collaboratori esperti." (24/04) → soggetto implicito = **Stefano**
5. "Lavora presso la Lucy Plast spa di Umbertide (PG)." → generico

**4 frammenti su 5 NON sono di Giada. Il gate `_same_subject_gate` li ha tenuti tutti.**

Osservazione chiave: i frammenti 1, 2, 4 sono **acefali** — nessun soggetto esplicito
nel testo ("Lavora da casa", "gestisce un team", "Ha un collega Leonardo"). Il passive
learner li ha salvati senza il soggetto (Stefano).

## Codice rilevante (per verifica indipendente)

- `core/dream_engine.py::_same_subject_gate` (~1467): chiede al modello notturno
  "quali frammenti parlano dello STESSO soggetto del frammento 1?", `think=False`,
  `num_predict=20`, esempio nel prompt = "prodotto vs impianto" (cose, non persone).
  Cap `items = ordered[:5]`. Fail-open: risposta vuota/ambigua → ritorna cluster INTERO.
- `_consolidation_pass` (~1517): cluster per dominio, recalled>=3, requires_verification=False.
- passive learner (ingestione che produce i frammenti acefali).
- step di SINTESI del 2e (fonde i frammenti tenuti in un nodo unico).

Verifica tu stesso: `r.json().get(<nodo>)` → `consolidated_from` → contenuto di ciascuna fonte.

## La domanda per te

Dove metteresti il fix, e perché? Il punteggio tension (read-side, a valle) NON lo
sistema: la memoria falsa è già scritta. Ma la radice qual è secondo te —
- il **gate** (deve escludere, non tenere, frammenti senza soggetto risolvibile)?
- l'**ingestione** (il passive learner non dovrebbe salvare frasi acefale)?
- la **sintesi** (non attribuire al seed attributi di frammenti acefali)?
- o un asse che non stiamo vedendo?

Vincoli da non violare:
- la memoria di Euri deve restare fallibile/umana — NON curare verso la sterilità;
  ma "acefalo" è perdita d'informazione, non fallibilità umana.
- l'onere sta su Euri, non su Stefano: niente soluzioni del tipo "Stefano dica sempre
  il soggetto".
- niente conoscenza di dominio hardcoded (Lucy Plast) nel codice.

## La mia ipotesi (MIA — contestala)

È un problema a DUE stadi con radice all'ingestione:
1. Radice: i frammenti passive sono **acefali** → il gate non può risolvere un'identità
   che nel testo non c'è → li assorbe nel seed.
2. Il gate amplifica (person-blind: prompt su prodotti, think=False, fail-open che tiene
   tutto sull'ambiguo).
3. La sintesi aggiunge fioritura ("gestendo un team") senza vincolo di fedeltà.

Quindi io NON partirei dal solo gate: o si preserva il soggetto all'ingestione, o il
gate tratta "soggetto non risolvibile" come ESCLUDI (oggi fa l'opposto col fail-open).
Ma è la mia lettura — dicci la tua.
