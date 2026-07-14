# Livelli di test

I test di Euri sono script autonomi. I manifest impediscono che una CI o un
comando generico esegua per errore test che usano memoria reale, Ollama o hardware.

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

Ogni nuovo `test_*.py` deve essere inserito in un solo manifest. Il workflow CI
verifica anche che la classificazione sia completa e senza duplicati.
