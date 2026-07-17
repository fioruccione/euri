# Interocezione hardware di Euri

## Intenzione

Questo sottosistema non e' un semplice monitor di sistema. E' il primo recettore
interocettivo: traduce condizioni fisiche locali in uno stato interno stabile,
senza chiedere a un modello di decidere se l'hardware e' in pericolo.

La sequenza architetturale e':

`sensazione -> stato -> transizione -> riflesso -> interpretazione -> memoria`

La versione corrente implementa soltanto i primi tre passaggi. Anticipare gli altri
renderebbe Euri piu' teatrale, non piu' cognitiva.

## Fase 0 attiva

- Campionamento ogni 3 secondi, senza LLM.
- CPU e RAM tramite `psutil`.
- GPU multiple tramite `pynvml`, se installato, altrimenti `nvidia-smi`.
- Stati `NORMAL`, `WARNING`, `CRITICAL` con persistenza e isteresi.
- `CRITICAL` puo' scavalcare il cooldown; gli alert ripetuti no.
- Recovery e ripristino del provider sono eventi espliciti.
- Temperature non finite o fuori da -20..150 C vengono scartate.
- Utilizzo CPU/GPU e' telemetria, non dolore.
- VRAM alta e' pressione persistente ma non e' critica da sola, perche' i modelli
  residenti occupano memoria anche quando il sistema e' sano.

Nessun evento della Fase 0 ferma Dream, Ollama, Whisper o il daemon vocale. Initiative
non considera `sense=hardware` eleggibile, quindi non produce interruzioni vocali.

## Persistenza

- `euri:hardware:latest`: snapshot completo, TTL 30 secondi.
- `euri:hardware:state`: livelli correnti e fault, TTL 30 secondi.
- `euri:hardware:baseline`: campione compatto al minuto, circa 14 giorni bounded.
- `euri:hardware:events`: transizioni, reminder, fault e recovery.
- `euri:pulse`: copia delle sole transizioni significative.

Lo snapshot serve al presente; la baseline serve a calibrare; gli eventi servono ai
futuri consumer. Non usare il Pulse come archivio della telemetria grezza.

## Criteri per la Fase 1

Non attivare un riflesso protettivo finche' non sono soddisfatti tutti questi punti:

1. La baseline comprende almeno chat, STT, TTS, Dream, maintenance e caricamento modelli.
2. Le soglie non producono falsi critical durante carichi normali osservati.
3. Ogni azione e' reversibile, idempotente e testata senza hardware reale.
4. Il consumer rivalida che `latest` sia fresco e che lo stato critico persista.
5. Un fault del sensore non viene interpretato come surriscaldamento.
6. Esiste un recovery verificabile e registrato per ogni azione.

Il primo checkpoint e' fissato a 72 ore. L'audit e' deterministico e read-only:

`./venv/bin/python scripts/audit_hardware_baseline.py`

Calcola durata, copertura, min/p50/p95/max per sensore, fault, transizioni e presenza
di almeno un carico GPU rappresentativo. Non autorizza da solo la Fase 1: produce il
report che Codex o Claude devono leggere insieme a Stefano.

Ordine prudente delle azioni future:

1. Rinviare Dream e manutenzione differibile.
2. Impedire nuovi caricamenti di modelli pesanti.
3. Ridurre concorrenza o rilasciare cache solo tramite API supportate.
4. Notificare Stefano se la condizione persiste.

Non terminare processi automaticamente sulla base di una singola lettura. Un arresto
di emergenza richiede un contratto distinto, piu' sensori o conferma del driver, e una
ragione operativa piu' forte della sola metafora del dolore.

## Estensioni

Lo stesso contratto puo' modellare disco, UPS, Redis, Ollama, rete, microfono e webcam.
Ogni senso mantiene raccolta e policy locali, ma pubblica transizioni nello stesso Pulse.
Il modello vede soltanto stati stabilizzati e conseguenze utili, non migliaia di campioni.
