# RAG context — baseline pre-fix (11/06/2026)

Misurato da `diag_rag_context.py` (read-only, `touch=False`). Caso scatenante:
conversazione tecnica Eurostampi (estrusione/perossido) col contesto RAG dominato
dai nodi freschi del ciclo Dream di mezzogiorno.

## Numero da muovere

**Query tecniche (3 turni Eurostampi): ON=5 / OFF=13** su 18 slot (~72% off-topic).

Struttura ricorrente per ogni turno tecnico (6 slot, `mem_cap=6`):
- **5 slot da recency** (`get_recent_memories(limit=5)`, riga 853) = output ciclo
  Dream 12:05–12:27: `automazione industriale`, `informatica`, `intelligenza
  artificiale`, `logistica materiale` (OFF) + `chimica polimeri` reflection (ON).
- **1 slot semantico** (`search_memories(limit=3)`, cap a 6 → ne entra ~1), a volte
  pure OFF (`gestione dati`).

Persistenza: i nodi delle 12:05 dominavano ancora alle 12:53 → un singolo ciclo
onirico avvelena la recency per ore, finché non si accumulano memorie più recenti.

## Invariante da non rompere (contro-caso #2)

- `di cosa parlavamo ieri?` → `temporale=True`, percorso `prioritize_window`. OK.
- `di cosa parlavamo prima?` → **`temporale=False`** (BUG PREESISTENTE: "prima" non
  è nel vocabolario di `extract_temporal_range` ed è stop-word). Prende il contesto
  inquinato come le query normali. Il ribilanciamento la aiuta comunque; il fix del
  riconoscimento di "prima" è separato e fuori scope.

## Obiettivo del fix

- Query tecniche: **ON↑ / OFF↓** (la recency non deve annegare la rilevanza).
- Query temporali: composizione/ordine **invariati** rispetto a oggi.

## Risultato del ribilanciamento (recency 5→2, semantic 3→5; config.py)

Confronto **back-to-back sullo stesso stato Redis** (cambia solo il config — annulla
il drift di stato tra misure separate):

| config | ON | OFF |
|---|---|---|
| PRIMA (recency=5 / sem=3) | 4 | 14 |
| DOPO  (recency=2 / sem=5) | **8** | **6** |

ON raddoppia, OFF −57%. Invariante temporale verificata su "di cosa abbiamo parlato
oggi?" (finestra popolata): **9 slot di testa su 9 identici, stesso ordine**; la query
temporale guadagna solo +1 nodo semantico in coda (slot 10, prima vuoto). Nessuna
regressione: il diario di `prioritize_window` è a monte e non viene toccato.

## Punto #3 (deep, decisione esplicita pendente)

`recalled_count` viene incrementato anche sui nodi iniettati per recency (non solo
sui richiami meritati dal match semantico) → droga le statistiche che nutrono Loop
2e e il lifecycle insight. Fix superficiale (ribilancio slot) ≠ fix profondo
(distinguere richiamo meritato da iniettato). Scope da decidere con Stefano.
Vedi [[project_euri_touch_lifecycle_validation]].
