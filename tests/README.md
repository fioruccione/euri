# Livelli di test

I test di Euri sono script autonomi raccolti in questa directory. I manifest
impediscono che una CI o un comando generico esegua per errore test che usano
memoria reale, Ollama o hardware.

- `unit.txt`: fake/temp/subprocess locali; e' il solo livello eseguito in CI.
- `integration.txt`: richiede servizi o modelli, ma deve usare chiavi isolate o
  restare read-only.
- `live.txt`: puo' leggere o mutare lo stato reale di Euri, usare audio/camera o
  avviare cicli cognitivi. Non entra mai in CI.

Esecuzione:

```bash
./venv/bin/python scripts/run_test_manifest.py tests/manifests/unit.txt
./venv/bin/python scripts/run_test_manifest.py tests/manifests/integration.txt
./venv/bin/python scripts/run_test_manifest.py tests/manifests/live.txt
```

Ogni nuovo `tests/test_*.py` deve essere inserito in un solo manifest usando il
percorso completo relativo alla root. Il workflow CI verifica anche che la
classificazione sia completa e senza duplicati. Il runner aggiunge la root del
repository a `PYTHONPATH`, così i test continuano a importare `core`, `voice`,
`agent` e gli altri package applicativi senza modificare il codice di runtime.
