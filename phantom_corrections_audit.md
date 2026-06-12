# N1 — Correzioni-fantasma: baseline pre-fix (12/06/2026)

Misurato da `diag_phantom_corrections.py` (read-only, giudice LLM Gemma). Branch
`n1-correzioni-fantasma`.

## Numero

**≥ 53% fantasmi: 25/47 correction signal NON sono correzioni di Euri** (domande,
elaborazioni, accordi, pensieri ad alta voce catturati da `detect_correction`).
È un **pavimento**: il giudice ha falsi negativi (es. `bf1e535b`, `cab645f1`
chiamati CORREZIONE pur essendo fantasma) → il tasso reale è più alto.

## Il finding che decide la direzione (obiezione #3)

Il verdict notturno di Loop 2g NON separa i fantasmi — li sparge su tutti i verdict:

| verdict notturno | CORREZIONE | ALTRO (fantasma) |
|---|---|---|
| ambiguous | 0 | 6 |
| bad_memory | 10 | 4 |
| bad_reasoning | 12 | **15** |

Solo 6/25 fantasmi finiscono in `ambiguous`; gli altri **19** prendono
`bad_memory`/`bad_reasoning` e **generano lesson spurie**. Filtrare sul verdict
esistente catturerebbe il 24% dei fantasmi. Causa: il classify presuppone "è una
correzione" e chiede solo *che tipo*; non chiede mai **se** lo è.

## Implicazione per il fix

- Posto giusto: **fase ACT notturna** (option c della mia obiezione #3), NON la
  cattura (hot-path latenza) né la regex (overfit, vietato).
- Serve una **domanda-gate nuova** ("è davvero una correzione di un errore di
  Euri?") prima di generare la lesson. Lo stesso LLM notturno la risponde → costo
  quasi nullo. Il prompt del giudice di questo script è il prototipo, MA va
  raffinato (ha falsi negativi: ha mancato domande di richiamo tipo "ti ricordi
  che parlavamo di X?").
- Successo del fix = ri-misura: il tasso di lesson generate da fantasmi crolla,
  senza perdere le correzioni vere (contro-caso: i ~22 CORREZIONE veri devono
  restare).
- **Bonifica (Passo 3):** i 19 fantasmi finiti in bad_memory/bad_reasoning hanno
  prodotto lesson/audit_flag spuri → identificarli e **soft-deletarli** (mai
  cancellare).

## Contro-caso obbligatorio
Le correzioni VERE (almeno 22/47, es. "non è tricarbonato è bicarbonato",
"Davide è responsabile produzione non lavora con...", "lo 03 PPR610 è rigenerato")
NON devono essere filtrate. Il gate deve essere conservativo sul lato "scarto".

## DOPO il fix (classify a 4 vie, gate `not_a_correction` — `diag_n1_validate.py`, Qwen reale)

- **Contro-caso: 0/11 correzioni vere perse.** Tutte restano `bad_memory`/`bad_reasoning`. Il bias conservativo ("nel dubbio, correzione") tiene.
- **Recall: 10/11 fantasmi chiari beccati** (domande, elaborazioni, accordi → `not_a_correction`). Unico miss: `bf1e535b` ("ti ricordi che parlavamo di Eurostampi?") → `bad_memory`: domanda-richiamo borderline che implica "dovresti ricordarlo" → lato sicuro/conservativo dell'errore.

Implementazione: `core/dream_engine._llm_classify_correction` ora a 4 vie (gate prima del tipo); `_audit_corrections_pass` su `not_a_correction` → niente lesson, status `dismissed` (soft-delete, audit preservato, TTL 30gg). La cattura `detect_correction` resta invariata (un punto solo). Il fix previene la pollution FUTURA; i fantasmi GIÀ diventati lesson (19, `status=analyzed`) restano da bonificare nel **Passo 3**.
