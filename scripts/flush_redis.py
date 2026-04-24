"""
Pulizia completa di tutti i dati Euri in Redis.
Ricrea gli indici con lo schema aggiornato (include campo 'source').

Uso:
    cd ~/euri && ./venv/bin/python scripts/flush_redis.py
"""
import sys
sys.path.insert(0, "/Users/stefanofiorucci/euri")

from utils.redis_client import get_client, flush_and_reinit

r = get_client()

confirm = input("Cancello tutti i dati euri:* da Redis e ricreo gli indici? [s/N] ").strip().lower()
if confirm != "s":
    print("Annullato.")
    sys.exit(0)

flush_and_reinit(r)
print("Fatto. Redis pulito, indici ricreati con schema aggiornato.")
