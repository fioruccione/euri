import sys
import time
from pathlib import Path
from loguru import logger

sys.path.append(str(Path(__file__).parent))

# Imposta loguru per mostrare anche i DEBUG nel terminale
logger.remove()
logger.add(sys.stderr, level="DEBUG")

import config
# Forziamo la promozione immediata per il test
config.DREAM_INSIGHT_MIN_CONVERGENCES = 1  

import redis
from core.embedder import Embedder
from core.dream_engine import DreamEngine

print("Caricamento modelli in corso...")
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
emb = Embedder()
emb.load()

print("\nAvvio Dream Engine (Forzato)...")
engine = DreamEngine(r, emb)

# Facciamo un loop finché non trova un insight (max 10 tentativi)
trovato = False
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
        print("\n🎉 BINGO! Ha trovato un isomorfismo e creato l'Insight in Obsidian!")
        trovato = True
        break
    else:
        print("Nessun collegamento brillante in questo sogno. Riproviamo...")
        time.sleep(1)

if not trovato:
    print("\nNessun Insight trovato dopo 10 tentativi. Euri ha bisogno di più memorie diverse per fare collegamenti creativi!")
