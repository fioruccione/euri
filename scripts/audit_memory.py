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


def backfill_domains(r: redis.Redis, only_generale: bool = True):
    """
    Ricalcola il domain label per le memorie esistenti usando assign_domain().
    Per default aggiorna solo quelle con domain='generale' (le mal classificate).
    """
    from core.domain_gater import assign_domain

    docs = scan_memories(r, "all")
    targets = [d for d in docs if (d.get("domain") == "generale") or not only_generale]

    if not targets:
        print("\nNessuna memoria da aggiornare.")
        return

    print_header(f"BACKFILL DOMINI — {len(targets)} memorie da rietichettare")
    updated = 0
    unchanged = 0

    for i, doc in enumerate(targets, 1):
        content = doc.get("content", "")
        old_domain = doc.get("domain", "generale")
        print(f"[{i}/{len(targets)}] elaborazione...         ", end="\r", flush=True)

        new_domain = assign_domain(content)

        if new_domain != old_domain:
            r.json().set(doc["_key"], "$.domain", new_domain)
            print(f"[{i}/{len(targets)}] {old_domain:15} → {new_domain:20} | {content[:55]}")
            updated += 1
        else:
            unchanged += 1

    print(f"\n→ {updated} aggiornate, {unchanged} invariate (già corrette o ancora 'generale').")


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
    parser.add_argument("--backfill-domains", action="store_true",
                        help="Ricalcola il domain label per le memorie con domain='generale'")
    parser.add_argument("--backfill-all", action="store_true",
                        help="Ricalcola il domain label per TUTTE le memorie")
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
    elif args.backfill_all:
        backfill_domains(r, only_generale=False)
    elif args.backfill_domains:
        backfill_domains(r, only_generale=True)
    else:
        audit(r, args.source)
