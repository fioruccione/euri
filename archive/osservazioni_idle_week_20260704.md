# Osservazioni settimana idle — 04/07/2026

Companion di `baseline_idle_week_20260703.json`. Due osservazioni emerse verificando lo
stato di Euri durante la prima finestra idle (Stefano assente, substrato senza osservatore).
Read-only sul sistema: nessuna memoria di Euri né codice modificati.

---

## 1. Memoria datata riusata come presente (staleness temporale nel sogno)

Il sogno dell'01:09 (`euri:insight:5c9d7ea3`, domini `tempo libero` × `logistica`,
promosso conv=4) apre con *"Stefano è in vacanza per il ponte festivo di giugno fino a
mercoledì"* trattandola come attuale (*"solo dopo il rientro di mercoledì"*).

Catena di provenienza verificata: `source_memory_ids[0]` =
`euri:memory:0c5b8fee` (source `passive`, creata **30/05/2026 11:28** — 35 giorni prima).
Nessun input esterno: le fonti sono tutte memorie interne di Euri (confermato su richiesta —
Claude non ha alcun canale di scrittura sul runtime di Euri).

**Nessuna memoria della settimana idle corrente.** Ricerca doppia e indipendente sulle 1401
memorie:
- per stringa (vacanza/ferie/via/stacco/…): unico match esplicito la memoria del 30/05;
- semantica (coseno, `multilingual-e5-large`, query-mode): unico vero match `0c5b8fee`
  a sim=0.8745, Δ=+0.0835 sul mean 0.7911 (e5 anisotropo → conta il delta). Dal 2° in giù
  si crolla nella baseline su fatti generici ("Stefano lavora presso…"), nessuno sull'assenza.

Il "Stefano è in vacanza" nei sogni è quindi **grounding corretto ma stale**: una memoria con
data assoluta interna ("mercoledì") riusata mesi dopo senza scarto d'età. Tocca il richiamo
temporale — il Dream Engine non pesa l'età delle date scritte nel testo quando le riusa.
Da rivedere se il pattern "data vecchia presa per presente" ricorre nei sogni della settimana.
Non è un bug da fixare ora: osservazione appesa.

---

## 2. Nodo contaminato Leonardo — stato e verifica del retrieval

Nodo `euri:memory:01d1b73d-fdc4-4d8a-a1b0-ed7ae261fb81` (l'idra identità Stefano↔Leonardo,
esperimento self-heal). Lasciato **in vita per scelta**: sonda per vedere se l'errore
riemerge o se la correzione a voce ha tenuto.

**Stato attuale (baseline da diffare al rientro):**
- Testo ANCORA sporco verbatim: *"Il soggetto è Stefano, il cui nome abituale è Leonardo."*
  (`category=consolidato`, `requires_verification=false`) — NON bonificato.
- Marcato `superseded_by → 119da2ae` = l'ancora-di-realtà (02/06, source `user`, tags
  `identità`/`ancora-di-realtà`: *"Stefano e Leonardo sono due persone diverse — NON Leonardo"*).
- Nodo sporco: `recalled_count=8`, `last_recalled_at=2026-05-12 08:54` — cioè MAI richiamato
  da quando esiste la correzione (02/06) → dormiente. Ancora corretta `119da2ae`:
  `recalled_count=3`, `last_recalled=2026-06-19` → viva.

**Verifica a codice: il retrieval ONORA `superseded_by` ovunque (difesa in profondità).**
Il nodo superato è escluso dal contesto di richiamo per costruzione, non per fortuna:
- `core/memory_manager.py:458` — `_hydrate` scarta i superseded (copre keyword/L1 e il recall
  temporale `search_memories_by_timerange` a `:563`);
- `core/memory_manager.py:268` — filtro esplicito sul livello semantico L2;
- `core/domain_gater.py:178` — esclusione dentro `domain_aware_search`;
- `core/memory_manager.py:394` — `_search_semantic` (usato dal fill hybrid L3);
- `core/dream_engine.py:366/839/1088/1469/1501` — il campionamento onirico esclude i superseded.

→ almeno 4 checkpoint ridondanti su tutti i path di context-injection.

**Conseguenza sul criterio di re-emergenza:** NON aspettarsi che `01d1b73d` risalga via
retrieval (non può, finché il flag resta). La re-emergenza da osservare è che il **sogno
ri-derivi da zero** la confusione cognome → un NUOVO nodo contaminato promosso (dinamica idra),
non la resurrezione del vecchio. Criterio di lettura al rientro:
- **correzione tenuta** = nodo sporco fermo a `recalled_count=8`, ancora corretta che sale,
  nessun insight nuovo che ripesca la confusione;
- **errore riemerso** = nodo sporco con `recalled_count>8` o `last_recalled` più recente del
  02/06, oppure un insight promosso nuovo che ri-associa Stefano↔Leonardo.

Distinto dal finding confab (aperto), che riguarda un percorso diverso: lo scan che ordina per
`source_priority`/`recalled_count`/substring e ignora `requires_verification` e le *correzioni*
— meccanismo diverso da `superseded_by`, non toccato da questa verifica.
