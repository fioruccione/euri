import sys
import time
from pathlib import Path
from loguru import logger

sys.path.append(str(Path(__file__).parent))

# Imposta loguru per mostrare anche i DEBUG nel terminale
logger.remove()
logger.add(sys.stderr, level="DEBUG")

import config
# MIN_CONVERGENCES dal config — stesso comportamento del Dream Engine in idle

import redis
from core.embedder import Embedder
from core.dream_engine import DreamEngine

print("Caricamento modelli in corso...")
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
emb = Embedder()
emb.load()

print("\nAvvio Dream Engine (Forzato)...")
engine = DreamEngine(r, emb)

# Facciamo un loop finché non genera un candidate operativo (max 10 tentativi).
# Candidate, promozione e scrittura Obsidian sono tre stati distinti.
candidate_creato = False
for i in range(1, 11):
    print(f"\n--- TENTATIVO {i}/10 ---")
    domains = engine._get_unique_domains()
    if len(domains) < 2:
        print("ERRORE: Non hai ancora abbastanza memorie in categorie diverse per fare un sogno!")
        break
        
    dream = engine._generate_dream(domains)
    if dream and dream.get("status") == "candidate":
        engine._evaluate_insights()
        engine._cleanup_expired_insights()
        print(
            "\nCandidate Dream creato e valutato. "
            "Questo non implica promozione né scrittura in Obsidian; "
            "l'esito effettivo è nel log e nella convergence trace."
        )
        candidate_creato = True
        break
    else:
        print("Nessun collegamento brillante in questo sogno. Riproviamo...")
        time.sleep(1)

if not candidate_creato:
    print("\nNessun candidate operativo generato dopo 10 tentativi.")
