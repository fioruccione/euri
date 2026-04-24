"""
audit_memory.py — Audit + pulizia selettiva delle memorie Redis di Euri.

Flusso:
  1. Legge tutte le memorie per source (default: passive)
  2. Per ognuna chiede al LLM: UTILE o RUMORE?
  3. Stampa report
  4. Chiede conferma prima di cancellare le memorie segnate come RUMORE
  5. Opzione reset totale per source

Uso:
  python scripts/audit_memory.py                  # audita source=passive
  python scripts/audit_memory.py --source user     # audita source=user
  python scripts/audit_memory.py --source all      # audita tutte
  python scripts/audit_memory.py --delete passive  # cancella TUTTO source=passive senza audit
"""
import sys
import json
import argparse
from pathlib import Path

# Aggiungi root al path
sys.path.insert(0, str(Path(__file__).parent.parent))

import redis
import ollama
import config


def get_client() -> redis.Redis:
    return redis.Redis(host="localhost", port=6379, decode_responses=True)


def scan_memories(r: redis.Redis, source_filter: str = None) -> list[dict]:
    docs = []
    for key in r.scan_iter("euri:memory:*"):
        try:
            # Mac usa Redis Stack con RedisJSON
            data = r.json().get(key, "$")
            if not data:
                continue
            doc = data[0]
            doc["_key"] = key
            if source_filter and source_filter != "all":
                if doc.get("source") != source_filter:
                    continue
            docs.append(doc)
        except Exception:
            pass
    return sorted(docs, key=lambda d: d.get("created_at", 0))


def llm_judge(content: str) -> tuple[str, str]:
    """
    Chiede al LLM se il contenuto è un fatto utile su Stefano o rumore.
    Ritorna (verdetto, motivazione): verdetto = "UTILE" o "RUMORE"
    """
    prompt = (
        f"Sei un sistema di controllo qualità per una memoria personale.\n"
        f"Valuta se questo testo è un FATTO UTILE da ricordare su Stefano (l'utente) "
        f"oppure è RUMORE (conversazione ambient, frase troncata, dato generico non personale, "
        f"informazione su terzi irrilevanti, frase senza contesto).\n\n"
        f"Memoria: \"{content}\"\n\n"
        f"Rispondi ESATTAMENTE in questo formato:\n"
        f"VERDETTO: UTILE oppure RUMORE\n"
        f"MOTIVO: una riga breve"
    )
    try:
        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 60},
            think=False,
        )
        text = (response.message.content or "").strip()
        verdetto = "RUMORE"
        motivo = ""
        for line in text.splitlines():
            if line.startswith("VERDETTO:"):
                v = line.split(":", 1)[1].strip().upper()
                if "UTILE" in v:
                    verdetto = "UTILE"
            elif line.startswith("MOTIVO:"):
                motivo = line.split(":", 1)[1].strip()
        return verdetto, motivo
    except Exception as e:
        return "ERRORE", str(e)


def print_header(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")


def audit(r: redis.Redis, source: str):
    docs = scan_memories(r, source)
    if not docs:
        print(f"\nNessuna memoria trovata per source='{source}'.")
        return

    print_header(f"AUDIT MEMORIE — source={source} ({len(docs)} trovate)")

    utili = []
    rumore = []

    for i, doc in enumerate(docs, 1):
        content = doc.get("content", "")
        cat = doc.get("category", "?")
        src = doc.get("source", "?")
        print(f"\n[{i}/{len(docs)}] [{src}] [{cat}]")
        print(f"  {content[:120]}")
        print(f"  → valutazione LLM...", end="", flush=True)

        verdetto, motivo = llm_judge(content)
        marker = "✓" if verdetto == "UTILE" else "✗"
        print(f"\r  {marker} {verdetto:<6} — {motivo[:80]}")

        if verdetto == "UTILE":
            utili.append(doc)
        else:
            rumore.append(doc)

    # Report finale
    print_header(f"REPORT — {len(utili)} UTILI / {len(rumore)} RUMORE")

    if rumore:
        print(f"\nMemorie segnate come RUMORE ({len(rumore)}):")
        for doc in rumore:
            print(f"  ✗ [{doc.get('source')}] {doc.get('content', '')[:100]}")

        print(f"\nVuoi cancellare le {len(rumore)} memorie RUMORE? [s/N] ", end="")
        risposta = input().strip().lower()
        if risposta == "s":
            for doc in rumore:
                r.delete(doc["_key"])
            print(f"  → {len(rumore)} memorie cancellate.")
        else:
            print("  → Nessuna cancellazione.")
    else:
        print("\nNessun rumore trovato — memorie pulite.")

    if utili:
        print(f"\nMemorie UTILI conservate ({len(utili)}):")
        for doc in utili:
            print(f"  ✓ [{doc.get('source')}] {doc.get('content', '')[:100]}")


def delete_by_source(r: redis.Redis, source: str):
    """Cancella TUTTE le memorie di una source senza audit."""
    docs = scan_memories(r, source)
    if not docs:
        print(f"\nNessuna memoria trovata per source='{source}'.")
        return

    print(f"\nStai per cancellare {len(docs)} memorie con source='{source}'.")
    print("Esempi:")
    for doc in docs[:5]:
        print(f"  - {doc.get('content', '')[:80]}")
    if len(docs) > 5:
        print(f"  ... e altre {len(docs) - 5}")

    print(f"\nConfermi la cancellazione? [s/N] ", end="")
    risposta = input().strip().lower()
    if risposta == "s":
        for doc in docs:
            r.delete(doc["_key"])
        print(f"  → {len(docs)} memorie cancellate.")
    else:
        print("  → Annullato.")


def stats(r: redis.Redis):
    """Stampa statistiche rapide per source."""
    docs = scan_memories(r, "all")
    by_source: dict[str, int] = {}
    for doc in docs:
        src = doc.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    print_header(f"STATISTICHE REDIS — {len(docs)} memorie totali")
    for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {src:<15} {count:>4} memorie")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit e pulizia memorie Redis di Euri")
    parser.add_argument("--source", default="passive",
                        help="Source da auditare: passive, user, web, teach, conversation, all (default: passive)")
    parser.add_argument("--delete", metavar="SOURCE",
                        help="Cancella TUTTE le memorie di una source senza audit")
    parser.add_argument("--stats", action="store_true",
                        help="Mostra solo statistiche per source")
    args = parser.parse_args()

    r = get_client()

    try:
        r.ping()
    except Exception:
        print("Errore: Redis non raggiungibile su localhost:6379")
        sys.exit(1)

    if args.stats:
        stats(r)
    elif args.delete:
        delete_by_source(r, args.delete)
    else:
        audit(r, args.source)
