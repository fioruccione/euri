# Risultati prestazionali X99 -> ThinkStation P620

**Data:** 24 agosto 2026
**Stato:** prima misura post-migrazione; decode replicato, pipeline vocale
preliminare

## Configurazione osservata

- Lenovo ThinkStation P620, AMD Ryzen Threadripper PRO 3975WX, singolo nodo NUMA;
- 128 GiB di RAM installati sugli otto canali, frequenza riferita 2666 MHz;
- 2x NVIDIA GeForce RTX 4060 Ti 16 GB;
- entrambe le GPU osservate a PCIe 4.0 x8;
- Ollama 0.30.7, contesto 32.768, modelli interamente residenti sulle GPU.

## Decode a modello caldo

Protocollo: warm-up da 64 token, tre repliche da 512 token, thinking disattivato,
temperatura 0, seed 42. La velocita' e' calcolata dalle metriche native Ollama
`eval_count / eval_duration`, non dal solo tempo wall-clock.

| Modello | X99 conservata | P620, repliche | Mediana P620 | Delta |
|---|---:|---:|---:|---:|
| Qwen3.8 27B Q4_K_M | 14,108 t/s | 14,314 / 14,318 / 14,323 | 14,318 t/s | +1,49% |
| Gemma4 26B A4B Q4_K_M | 58,25 t/s | 67,082 / 67,022 / 67,254 | 67,082 t/s | +15,16% |

La baseline Gemma di 51,4 t/s riportata nella preregistrazione produce un
apparente +30%. Esiste pero' una misura X99 successiva e piu' direttamente
confrontabile di 58,25 t/s in `NOTE_HARDWARE_20260802.md`; il risultato
prudente da usare e' quindi +15,16%.

Il prompt X99 originale non e' stato conservato insieme alla misura. Le opzioni,
il modello e il budget sono allineati, ma il confronto non e' byte-identico e
non chiude formalmente la preregistrazione.

## Pipeline vocale reale

Lo strumento ricostruisce il tempo fino alla prima voce come:

`STT + frame semantico + RAG + brain.respond + TTS first-ready`

Sono inclusi soltanto turni completi `CHAT` e `SEARCH`; la riproduzione audio e
i percorsi operativi vengono esclusi.

| Finestra P620 | RAM | n | Min | Mediana | p95 | Max |
|---|---:|---:|---:|---:|---:|---:|
| 17:14-17:32 | 32 GiB | 6 | 13,422 s | 15,398 s | 17,140 s | 17,491 s |
| 17:32-18:00 | 128 GiB | 2 | 13,856 s | 14,131 s | 14,379 s | 14,406 s |

La seconda finestra centra la previsione preregistrata di 13-14,5 secondi e
mostra un miglioramento descrittivo dell'8,23% sulla mediana rispetto alla
prima finestra P620. Non e' ancora una stima causale dell'effetto RAM:

- ci sono soltanto due turni dopo il montaggio;
- i prompt e le lunghezze delle risposte non sono appaiati;
- un turno `SEARCH` ha avuto RAG da 6 ms, molto piu' rapido del percorso comune.

Servono almeno 10-20 turni organici post-installazione, oppure un replay a
prompt fissi, prima di attribuire una percentuale alla RAM completa. Il dato
attuale autorizza soltanto la conclusione che l'obiettivo percettivo e' stato
raggiunto nei primi due campioni.

## Strumento

Analisi read-only dei turni vocali:

```bash
./venv/bin/python scripts/measure_euri_performance.py voice \
  --since 2026-08-24T17:32:01
```

Benchmark decode; viene rifiutato automaticamente se `voice_daemon.py` e'
attivo:

```bash
./venv/bin/python scripts/measure_euri_performance.py decode \
  --model gemma4:26b --baseline-tps 58.25
```

Il comando supporta `--json-out` per conservare un risultato esplicito. Non
scrive memorie, non tocca Redis e non modifica la configurazione di Euri.
