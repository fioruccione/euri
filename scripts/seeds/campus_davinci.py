# seed_campus_davinci.py
# Carica in Redis capsule di conoscenza sul Campus Da Vinci per la demo di Roberta.
# Usa lo stesso schema euri:memory:* — il context injection le trova automaticamente.

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.redis_client import get_client as get_redis
from core.memory_manager import MemoryManager

CAPSULES = [
    {
        "content": (
            "Il percorso 'IA e scienza dei dati' del Campus Da Vinci nasce come "
            "curvatura del Liceo Scientifico opzione Scienze applicate, con più ore "
            "di Informatica e approccio interdisciplinare."
        ),
        "category": "campus",
        "tags": ["campus", "davinci", "umbertide", "liceo", "scienze applicate", "informatica"],
        "source": "campus",
    },
    {
        "content": (
            "Il percorso sviluppa pensiero logico, analisi, astrazione, gestione dei dati, "
            "machine learning e comprensione critica delle implicazioni etiche, sociali e giuridiche dell'IA."
        ),
        "category": "campus",
        "tags": ["machine learning", "etica", "dati", "ia", "campus"],
        "source": "campus",
    },
    {
        "content": (
            "Il Campus Leonardo Da Vinci è entrato nella Rete Nazionale dei Licei "
            "'Scienza dei Dati e Intelligenza Artificiale' ed è l'unica scuola "
            "in Umbria presente in questa rete."
        ),
        "category": "campus",
        "tags": ["rete", "sdia", "umbria", "nazionale", "scienza dei dati", "campus"],
        "source": "campus",
    },
    {
        "content": (
            "Il laboratorio del Campus Da Vinci include Dobot Magician, robot Makeblock mBot2, "
            "droni DJI Tello, robot umanoide NAO e visori AR. Strumenti usati per programmazione "
            "Python, visione artificiale, rilevamento oggetti e progetti collaborativi."
        ),
        "category": "campus",
        "tags": ["laboratorio", "dobot", "nao", "droni", "python", "visione artificiale", "robotica", "campus"],
        "source": "campus",
    },
    {
        "content": (
            "Il titolo finale del Campus Da Vinci è il Diploma di Liceo Scientifico opzione Scienze applicate. "
            "Il percorso orienta verso Data Science, Big Data, Machine Learning, AI, IoT, Informatica e STEM."
        ),
        "category": "campus",
        "tags": ["diploma", "data science", "big data", "ml", "ai", "iot", "stem", "campus"],
        "source": "campus",
    },
    {
        "content": (
            "Gli studenti del Campus Da Vinci imparano a usare strumenti generativi in modo responsabile, "
            "tenendo conto di privacy, sicurezza dei dati e impatto sociale."
        ),
        "category": "campus",
        "tags": ["chat generative", "privacy", "sicurezza", "responsabilità", "campus"],
        "source": "campus",
    },
    {
        "content": (
            "Roberta è una studentessa del percorso IA e scienza dei dati del Campus Leonardo Da Vinci "
            "di Umbertide. È la figlia di Stefano."
        ),
        "category": "persona",
        "tags": ["roberta", "campus", "studentessa", "umbertide"],
        "source": "campus",
    },
    {
        "content": (
            "Il professor Crispoldoni Michele insegna nel percorso IA e scienza dei dati "
            "del Campus Leonardo Da Vinci di Umbertide."
        ),
        "category": "persona",
        "tags": ["crispoldoni", "michele", "professore", "campus", "umbertide"],
        "source": "campus",
    },
]


def flush_campus(r):
    """Rimuove solo le memorie source=campus già caricate."""
    removed = 0
    for key in r.scan_iter("euri:memory:*"):
        data = r.json().get(key, "$.source")
        if data and data[0] == "campus":
            r.delete(key)
            removed += 1
    print(f"Rimossi {removed} record campus precedenti.")


def load(flush_all: bool = False):
    r = get_redis()
    mm = MemoryManager(r)

    if flush_all:
        # Flush completo di tutta la memoria (memorie, todo, note)
        from utils.redis_client import flush_and_reinit
        flush_and_reinit(r)
        print("Redis svuotato completamente.")
    else:
        flush_campus(r)

    for c in CAPSULES:
        mm.save_memory(
            content=c["content"],
            category=c["category"],
            tags=c["tags"],
            source=c["source"],
        )

    print(f"Caricate {len(CAPSULES)} capsule Campus Da Vinci.")


if __name__ == "__main__":
    # Passa --flush per svuotare tutta la Redis prima del seed
    flush_all = "--flush" in sys.argv
    load(flush_all=flush_all)
