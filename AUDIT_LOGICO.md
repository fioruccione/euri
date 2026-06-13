# Audit Logico Euri

| id | area | severita |
|---|---|---|
| F-01 | lifecycle | alta |
| F-02 | lifecycle | alta |
| F-03 | lifecycle | media |
| F-04 | redis | media |
| F-05 | routing | media |
| F-06 | redis | media |
| F-07 | loop | bassa |
| F-08 | error-handling | bassa |

### F-01 — Loop 2d estende `expires_at` ma non il TTL Redis
Area: lifecycle
Severita: alta
File: core/dream_engine.py:1032
Cosa: il death-row gate aggiorna il campo JSON `expires_at` quando conserva una memoria, ma non aggiorna la scadenza reale della chiave Redis. L'invariante rispettata dal path di recall e da `save_memory` e invece doppia: campo JSON + TTL Redis.
Evidenza: `core/dream_engine.py:1032` e `core/dream_engine.py:1040` fanno solo `json().set(... "$.expires_at" ...)`; il path coerente in `core/memory_manager.py:373` aggiorna `$.expires_at` e subito dopo `expireat` in `core/memory_manager.py:374`.
Impatto: una memoria che Loop 2d considera da conservare puo continuare ad avere la vecchia scadenza Redis e sparire comunque. Il JSON dira "salvata piu avanti", ma Redis puo eliminarla alla data precedente.
Confidenza: alta

### F-02 — Cleanup stantie ignora il nuovo `expires_at` deciso dal death-row gate
Area: lifecycle
Severita: alta
File: core/dream_engine.py:970
Cosa: `_cleanup_stale_memories` elimina memorie `passive/reflection/conversation` mai richiamate guardando `created_at`, non `expires_at`. Quindi una memoria con `recalled_count == 0` che Loop 2d ha giudicato `KEEP` resta comunque cancellabile come "stantia" per eta originaria.
Evidenza: `core/dream_engine.py:970` salta solo se `recalled_count > 0`, poi `core/dream_engine.py:972` confronta `created_at` col cutoff e `core/dream_engine.py:974` elimina. Il salvataggio LLM in `core/dream_engine.py:1038` aggiorna `expires_at` ma non cambia `created_at` ne `recalled_count`.
Impatto: il giudizio LLM del Loop 2d per memorie mai richiamate puo diventare inefficace nel ciclo successivo: una memoria "KEEP" puo essere cancellata per eta storica invece che per scadenza aggiornata.
Confidenza: alta

### F-03 — STATUS conta le memorie ma rinforza il lifecycle
Area: lifecycle
Severita: media
File: voice_daemon.py:652
Cosa: `_handle_status` usa `get_recent_memories(limit=999)` solo per contare le memorie, ma il default di `get_recent_memories` e `touch=True`. Questo incrementa `recalled_count` e rinnova TTL su molte memorie durante un comando diagnostico.
Evidenza: `voice_daemon.py:652` chiama `self.memory.get_recent_memories(limit=999)`; `core/memory_manager.py:402` ha `touch=True`; `_touch_memories` incrementa `recalled_count` in `core/memory_manager.py:367` e rinnova `expires_at`/TTL in `core/memory_manager.py:373`.
Impatto: chiedere lo stato del sistema puo alterare il lifecycle memoria, promuovendo artificialmente memorie che non sono state realmente usate come contesto cognitivo.
Confidenza: alta

### F-04 — `last_rag_ctx` e globale e aggiornato con delete/rpush/expire separati
Area: redis
Severita: media
File: core/memory_manager.py:973
Cosa: il contesto RAG usato dal Loop 2g e salvato in una singola chiave globale (`euri:last_rag_ctx`) con tre operazioni separate: delete, rpush, expire. Voce e Silent Chat condividono la stessa chiave.
Evidenza: `core/memory_manager.py:973` usa una chiave fissa; `core/memory_manager.py:974` cancella, `core/memory_manager.py:976` riscrive, `core/memory_manager.py:977` applica TTL. La voce aggiorna il contesto in `voice_daemon.py:918`; la chat testuale lo aggiorna in `ui/app.py:551`.
Impatto: se due canali sono attivi o se una correzione arriva mentre un altro turno ha appena aggiornato la chiave, il correction signal puo agganciarsi al contesto sbagliato o a una finestra vuota tra delete e rpush.
Confidenza: alta

### F-05 — `read_url/save_url` non sono raggiungibili in modo coerente dal canale voce
Area: routing
Severita: media
File: agent/executor.py:755
Cosa: l'Executor ha tool e regex per leggere un URL esplicito, ma il canale voce passa prima da `intent_router`/`llm_classifier`. Il fallback semantico conosce solo intent generici e definisce `EXECUTE` come hardware/sistema; non esiste un intent dedicato a `read_url`.
Evidenza: `agent/executor.py:755` definisce il pattern `read_url`; `_handle_execute` lo potrebbe eseguire in `voice_daemon.py:656`. Pero il router voce manda a `_handle_execute` solo se l'intent e `EXECUTE` (`voice_daemon.py:1381`), mentre il prompt fallback descrive `EXECUTE` come dati hardware in `core/llm_classifier.py:73` e il ToolRegistry fa lo stesso in `core/tool_registry.py:356`.
Impatto: in Silent Chat `dispatch_text` puo eseguire `read_url`, ma in voce una frase tipo "leggi questa pagina https://..." puo finire in CHAT o WEB_SEARCH invece che nella lettura diretta della pagina. Tool presente, path voce non garantito.
Confidenza: alta

### F-06 — Incremento `audit_flag` read-modify-write non atomico
Area: redis
Severita: media
File: core/dream_engine.py:865
Cosa: Loop 2g incrementa `audit_flag` leggendo il valore e poi riscrivendo `cur_val + 1`, invece di usare un incremento atomico RedisJSON. In condizioni normali il Dream Engine e singolo, ma `force_full_cycle.py`, UI o piu istanze possono sovrapporsi.
Evidenza: `core/dream_engine.py:865` legge `$.audit_flag`; `core/dream_engine.py:867` scrive il valore incrementato. Non e lo stesso pattern atomico usato per `recalled_count` in `core/memory_manager.py:367`.
Impatto: due correzioni concorrenti sulla stessa memoria possono perdere un incremento. Il segnale resta soft e non distruttivo, ma il peso storico dell'errore puo essere sottostimato.
Confidenza: da-confermare

### F-07 — Eredita `requires_verification` in Loop 2e ma i candidati sono gia esclusi
Area: loop
Severita: bassa
File: core/dream_engine.py:1090
Cosa: Loop 2e filtra fuori ogni memoria sorgente con `requires_verification=True`, ma piu avanti prova comunque a ereditare `requires_verification` dalle stesse sorgenti consolidate.
Evidenza: `core/dream_engine.py:1090` esclude i candidati con `requires_verification`; `core/dream_engine.py:1225` calcola `sources_rv`; `core/dream_engine.py:1231` imposta `$.requires_verification` sul consolidato se una sorgente lo aveva.
Impatto: il ramo di ereditarieta sembra irraggiungibile nel flusso normale. Se l'intento e "nessun dato numerico incerto entra in 2e", va bene; se invece il consolidato dovrebbe poter ereditare fragilita fattuale, oggi quella via non produce effetto.
Confidenza: alta

### F-08 — Eccezioni RedisJSON nei report possono sottostimare dati mancanti
Area: error-handling
Severita: bassa
File: scripts/audit_memory.py:303
Cosa: i path di audit leggono documenti con SCAN + JSON.GET, ma gli errori per singola chiave vengono assorbiti e il documento sparisce dal conteggio. Per un report diagnostico e corretto non mutare stato, ma un errore di lettura puo sembrare "assenza di problema".
Evidenza: `scripts/audit_memory.py:303` legge JSON, `scripts/audit_memory.py:310` fa `except Exception: continue`; il path storico `scan_memories` fa lo stesso con `pass` in `scripts/audit_memory.py:55`.
Impatto: se una chiave ha JSON corrotto, tipo inatteso o errore temporaneo Redis, il report read-only puo sottostimare memorie, consolidati fragili o campi mancanti invece di evidenziare una sezione "chiavi non leggibili".
Confidenza: alta
