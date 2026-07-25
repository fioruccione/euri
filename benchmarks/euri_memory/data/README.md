# Dataset locali

Questa directory contiene copie locali non versionate dei dataset ufficiali.

Per LoCoMo:

```bash
./venv/bin/python -m benchmarks.euri_memory.fetch_locomo
```

Lo script acquisisce `locomo10.json` e `LICENSE.txt` dal repository ufficiale
`snap-research/locomo` e registra URL, data e SHA-256 in `source_manifest.json`.
