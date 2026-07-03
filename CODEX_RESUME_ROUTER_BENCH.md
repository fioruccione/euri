# Ripresa benchmark modello router

Data: 2026-06-19

## Stato

Obiettivo: scegliere un modello piccolo locale Ollama da affiancare a Gemma4-26B per compiti meccanici:
- intent classification
- JUNK vs ok
- dedup semantico
- same-subject gate

Conversazione principale resta su Gemma4-26B. Il modello piccolo deve fare output corto/strutturato.

## File creato da Claude

`probe_router_bench.py`

Compila correttamente con:

```bash
python3 -m py_compile probe_router_bench.py
```

Va eseguito col virtualenv del progetto, non con Python di sistema:

```bash
./venv/bin/python probe_router_bench.py --model qwen2.5:3b
```

Python di sistema fallisce per mancanza modulo `ollama`.

## Modelli presenti prima dello stop

Output di `ollama list`:

```text
qwen2.5:3b     1.9 GB
gemma4:31b     19 GB
qwen3.6:35b    23 GB
gemma4:26b     17 GB
```

## Risultato qwen2.5:3b con prompt originale

Comando:

```bash
./venv/bin/python probe_router_bench.py --model qwen2.5:3b
```

Risultati:

```text
Intent: 10/17 = 59%
Dedup:   3/5 = 60%
```

Latenza:
- primo giro con cold start: una chiamata da circa 10.96s
- a caldo intent: circa 0.57s media
- dedup: circa 0.56s media

Problema principale: col prompt originale `qwen2.5:3b` sovra-classifica `SEARCH`.
Esempi sbagliati:

```text
"Euri buon pomeriggio, come stai?" -> SEARCH invece di CHAT
"Le solite cose da fare." -> SEARCH invece di CHAT
"Euri aiutami a fare una proporzione..." -> SEARCH invece di CHAT
```

## Test prompt stretto

Solo per diagnosi, senza modificare il file:
- prompt con regola "se sei indeciso tra CHAT e SEARCH, scegli CHAT"
- risultato intent: 14/17
- latenza media: circa 0.57s

Decisione utente: per ora NON cambiare prompt, ragionare su altri modelli.

## Valutazione provvisoria

`qwen2.5:3b`:
- velocità: buona
- intent: recuperabile con prompt migliore, ma mediocre con prompt originale
- dedup: non ancora affidabile
- same-subject/JUNK: ancora da testare

## Prossimi modelli da provare

Ordine consigliato:

```bash
ollama pull llama3.2:3b
ollama pull granite3.3:2b
ollama pull qwen3.5:0.8b
ollama pull qwen2.5:1.5b
```

Poi lanciare, senza cambiare prompt:

```bash
./venv/bin/python probe_router_bench.py --model llama3.2:3b
./venv/bin/python probe_router_bench.py --model granite3.3:2b
./venv/bin/python probe_router_bench.py --model qwen3.5:0.8b
./venv/bin/python probe_router_bench.py --model qwen2.5:1.5b
```

Confrontare:
- accuratezza intent
- accuratezza dedup
- latenza media a caldo
- errori sistematici, soprattutto CHAT vs SEARCH

## DA FARE quando chiudiamo il confronto modelli — same-subject gate

Il same-subject gate del Loop 2e (`core/dream_engine.py:_same_subject_gate`) è uno dei
compiti meccanici da scaricare sul micro-router: ~150 chiamate di classificazione binaria
a notte sullo stesso pool stabile (497 candidati recalled≥3). Oggi gira sul dream model.
Va aggiunto al bench come terzo task accanto a intent/dedup.

**Caso d'oro da inserire (discriminante duro, dominio elettronica).** Trovato con
`diag_gate.py` il 19/06. Il gate ha ESCLUSO correttamente, e il micro-router NON deve
fonderle:

- SEED: «Competenze in elettronica (riparazione TV Mivar, radio FM) e calcolo della formula di Friis.»
- CANDIDATO: «In passato applicava la formula del link budget di Friis per stime di guadagno
  d'antenna, ma non ricorda più i dettagli dei calcoli.»
- VERITÀ: **NO (soggetto diverso)** — nonostante Friis compaia in entrambe.

Perché è il caso duro: il topic è lo STESSO (Friis), ma il registro epistemico è opposto
("ho competenza" vs "lo sapevo e ho dimenticato"). Fonderle appiattisce il grounding. Un
micro-router che guarda solo la sovrapposizione lessicale risponde SI (sbaglia); Qwen-thinking
oggi risponde NO. È il test che separa "match di topic" da "match di registro/grounding".

Quando si rilancia il bench: aggiungere questa coppia ai DEDUP_CASES (o a una nuova sezione
SAME_SUBJECT) e guardare se i modelli 2-3B la sbagliano — se la sbagliano tutti, il same-subject
gate NON è offloadabile su un micro-router con prompt naive, serve istruzione esplicita sul registro.

## Nota operativa

Se il sandbox blocca Ollama con:

```text
socket: operation not permitted
```

serve eseguire il comando con permesso escalated perché Ollama è su `127.0.0.1:11434`.

