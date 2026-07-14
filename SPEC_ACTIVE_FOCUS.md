# Artefatto 2 — Specifica "Active Focus": continuità di lavoro esplicita e ispezionabile

**Progetto, 13/07/2026. Solo design — nessuna implementazione. Integrazione runtime
comunque NON prima della fine della raccolta dream_trace (~18/07).**

## Cosa è (e cosa non è)

Un **working set piccolo, simbolico e groundato**: le 3–7 cose su cui Euri e Stefano
stanno lavorando ADESSO, ciascuna con provenienza nominabile, stato e decadimento per
regola. Non è un vettore, non è un umore, non è memoria a lungo termine: è il piano di
lavoro attivo. Ogni affermazione che ne deriva risale a fonti nominabili (Regola d'Oro,
P-GT applicato alla continuità).

## Schema dati

Chiave: `euri:focus:{id}` (JSON), niente indice RediSearch in v1 (≤7 chiavi: scan basta).

```json
{
  "id": "uuid",
  "label": "prova blend Poseidon a mescola fredda",
  "type": "impegno | tema_lavoro | esperimento | questione_aperta",
  "domain": "chimica polimeri",
  "activation": 0.85,
  "priority": 0.7,
  "confidence": 0.9,
  "created_at": 1783950000.0,
  "last_reinforced_at": 1783953600.0,
  "decay_tau_s": 259200,
  "activation_cause": "memoria f621c34c salvata a voce il 21/06 (evento nominabile)",
  "source_refs": ["euri:memory:f621c34c", "euri:pulse:1783953600-0"],
  "open_loops": [
    {"text": "pinze robot in arrivo, prova slittata", "ref": "euri:memory:<id>"}
  ],
  "status": "active | cooling | resolved | contradicted",
  "conflicts": [],
  "requires_verification": false,
  "schema_version": 1
}
```

Regola anti-silo (lezione todo, 13/07): il focus **referenzia** nodi esistenti, non copia
contenuto. Un impegno pending È già un quasi-focus: il focus type=impegno è una *vista*
sulla memoria-impegno (stessa id in source_refs), mai un duplicato.

## Dinamica (tutta per regola, zero LLM in v1)

- **Rinforzo:** un evento appartiene a un focus se condivide dominio **e** almeno un
  identificatore (riuso di `_COMPOSITE_ID_RE` / safe_keywords — identifier-first, NON
  cosine-only: anisotropia). Allora `activation ← min(1, activation + w·salience)`,
  `last_reinforced_at ← now`. w di default 0.3, configurabile.
- **Decadimento (lazy, nessun loop nuovo):** calcolato in lettura:
  `a(t) = activation · exp(−(now − last_reinforced_at)/τ)`. τ per tipo: tema_lavoro ~3g,
  esperimento ~7g, impegno = niente decay (governa due_at), questione_aperta ~14g.
- **Stati:** `active` se a ≥ 0.35; `cooling` se 0.10 ≤ a < 0.35; sotto 0.10 il focus si
  archivia (delete con log). `resolved` solo per evento esplicito (impegno done,
  esperimento chiuso). `contradicted` se una correzione tocca i suoi source_refs —
  in append, mai edit silenzioso (stile del progetto).
- **Competizione:** cap 7 attivi. Se un ottavo si accende, il meno attivo passa a
  cooling. Il cap è il punto: un focus che non compete non è un focus.
- **Dedup:** prima di creare, cercare focus attivo con stesso dominio + overlap di
  identificatori → rinforza quello. Mai due focus per la stessa cosa.
- **Nascita:** SOLO da eventi esterni nominabili (`user`/`teach`, impegno creato,
  esperimento avviato). MAI da passive-ambient da solo (spugna, registro incerto —
  lezione Simone-scherzo), da insight non validati o da una lezione `reaction`
  sintetizzata da Euri. Il campo `reaction_raw` puo' rinforzare un focus esistente,
  ma una risposta a un insight non diventa automaticamente lavoro in corso.

## Riuso e sovrapposizioni (richieste esplicitamente)

| Esistente | Rapporto col focus |
|---|---|
| **Impegni** (13/07, memorie con due_at+status) | già focus-con-scadenza → il focus li referenzia; zero duplicazione |
| **Reflection Loop 2a** ("Sintesi recenti" nel RAG) | copre "temi recenti" ma senza stato/priorità/decay; può SEMINARE candidati focus; il focus non la sostituisce |
| **Recency slots RAG** | continuità implicita e indifferenziata; il focus è la versione curata (pochi, con stato e causa) |
| **Pulse (afferente, Fase 0/1)** | fonte naturale del rinforzo — il focus engine sarebbe il **primo consumatore del bus** (simbolico, non-efferente: coerente col disaccoppiamento cattura/consumo) |
| **Filtro del Risveglio** (`_active_domains_cache`) | focus-per-dominio grezzo a 30g; il focus lo raffina (per-tema, non per-dominio) |
| **Thought map (strada A)** | cartografia offline della memoria; il focus è il working set vivo — complementari |

Il delta architetturale vero è piccolo: la struttura + le regole. I sensi, gli eventi,
la provenienza e il punto di iniezione nel prompt (pattern del blocco impegni) esistono già.

## Output per il prompt (futuro, NON in v1)

3–5 righe, cap token configurabile, OGNI riga con la causa:

```
Lavoro in corso (piano attivo, con fonte):
- prova blend Poseidon — impegno, lunedì 20/07 9:00 [da memoria f621c34c]
- aperto: pinze robot in arrivo, prova slittata [detto a voce 13/07]
- esperimento dream_trace in raccolta fino ~18/07 [ESPERIMENTO_DREAM_TRACE.md]
```

Mai "livello di incertezza/umore": solo fatti con riferimento. Integra il retrieval,
non lo sostituisce (stesso principio del blocco impegni).

## Validazione PRIMA dell'integrazione (misura-prima, offline)

**Replay harness:** si riproduce la storia reale (memorie+pulse, es. 25/06→13/07) dentro
il motore focus offline e si genera la timeline dei focus. Poi audit umano piccolo:
per k giorni campionati (es. 8), Stefano guarda i top-3 focus di quel giorno e dice se
riconosce il proprio lavoro reale.

- **GO:** ≥80% dei giorni con top-3 "giusti o quasi" (nessun focus fantasma in cima).
- **NO-GO:** focus fantasma ricorrenti (nati da passive rumoroso) o churn (focus che
  nascono/muoiono a ogni burst) non curabili coi parametri → il modello a regole non
  regge, si riprogetta prima di qualunque runtime.

## Rischi

- **Matching a regole fragile** → mitigato: identifier-first + dominio, MAI regex nuove
  di settore (no-overfit-parsing); i casi grigi semplicemente non rinforzano (fail-quiet).
- **Focus stantio che orienta il prompt** → decay + cap + formulazione fattuale con data.
- **Nuovo silo** → è la negazione del design: referenzia, non copia; vive o muore col
  replay audit.
- **Scope creep verso l'efferente** → v1 SOLO afferente+iniezione contesto; nessuna
  iniziativa, nessun messaggio spontaneo.

## Superficie di modifica futura (stima, post-raccolta)

- `core/focus.py` nuovo (~250–300 righe: dataclass, store, regole).
- 2–3 punti di rinforzo: consumo pulse nel ciclo leggero esistente (nessun thread nuovo)
  o hook in save_memory/reaction.
- 1 sezione in `build_rag_context` (pattern blocco impegni, ~20 righe).
- Flag `FOCUS_ENABLED` default False. Test come per dream_trace (mock, scenari on/off).
- Replay harness: `scripts/experiments/replay_focus.py` (~200 righe, read-only).

## Raccomandazione

Costruire PRIMA il replay harness (offline, zero rischio, validabile da Stefano in
mezz'ora di audit) — solo dopo il GO si tocca il runtime, e comunque non prima della
fine della raccolta dream_trace.

## Replay epistemico 14/07/2026

Il replay e' stato ripetuto sostituendo ogni lezione `source=reaction` col solo
`reaction_raw` e vietando alle reaction di creare un focus. Risultato: 72 focus nati,
5 giorni recenti campionabili e nessun focus vivo a fine replay. Le sintesi generate
da Euri sono correttamente sparite dai top, ma e' emersa una lacuna di copertura:
dopo il 26/06 il dataset di memorie non contiene abbastanza eventi diretti
`user`/`teach` per rappresentare il lavoro recente.

Questo risultato annulla il vecchio GO come autorizzazione al runtime: quell'audit
aveva validato che i testi provenissero dalle conversazioni, non che il ranking
ricostruisse davvero la salienza quotidiana. Stato attuale: **NO-GO per copertura**.
Non si corregge allargando di nuovo alle sintesi interne; serve una sorgente diretta
dei turni accettati o un evento Pulse groundato, da progettare dopo `dream_trace`.
