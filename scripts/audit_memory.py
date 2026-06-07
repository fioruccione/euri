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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Aggiungi root al path
sys.path.insert(0, str(Path(__file__).parent.parent))

import redis
import ollama
import config


def get_client() -> redis.Redis:
    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )


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
    Chiede al LLM se la memoria merita di essere conservata nel contesto
    operativo di Stefano (lavoro + vita), non solo se parla di lui come soggetto.
    Ritorna (verdetto, motivazione): verdetto = "UTILE" o "RUMORE"
    """
    prompt = (
        f"Sei un quality control per la memoria di Euri, assistente personale "
        f"di Stefano — ingegnere in azienda chimica polimeri, sviluppatore software, "
        f"appassionato di motorsport.\n\n"
        f"Valuta se questa memoria merita di essere conservata.\n\n"
        f"UTILE: qualunque fatto verificabile o operativo nel contesto del lavoro o "
        f"della vita di Stefano. Include:\n"
        f"- Conoscenza tecnica oggettiva (additivi, materiali, formulazioni, parametri, MFI, dosaggi)\n"
        f"- Persone, clienti, fornitori, progetti, codici lotto\n"
        f"- Strumenti, sistemi, configurazioni hardware/software\n"
        f"- Decisioni, preferenze, esperienze, competenze\n"
        f"- Riferimenti diretti a Stefano come soggetto\n\n"
        f"RUMORE: solo questi casi:\n"
        f"- Frase troncata, incompleta, priva di senso\n"
        f"- Saluto/riempitivo senza contenuto informativo\n"
        f"- Duplicato palese di un concetto banale\n"
        f"- Errore evidente o contraddizione interna\n\n"
        f"Regola: in dubbio → UTILE. Un fatto tecnico oggettivo è SEMPRE utile, "
        f"anche se non parla di Stefano direttamente — è la sua knowledge base.\n\n"
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


def scan_outliers(r: redis.Redis, fix: bool = False, k: int = 10):
    """
    R1 — rileva memorie il cui dominio assegnato è incoerente col vicinato semantico
    (il dominio NON compare tra i k vicini più prossimi, self escluso). Riusa gli
    embedding già in Redis: nessun re-encoding, nessun dominio cablato nel codice.

    La correzione proposta passa per lo STESSO tagger context-aware di P1
    (assign_domain con i vicini come suggerimento): l'arbitro è il modello di Euri,
    non una soglia statistica. È pensato come REPORT da rivedere: con fix=True applica,
    ma di default mostra soltanto. NON va agganciato ai loop notturni autonomi —
    un re-tag di massa eroderebbe la granularità dei domini imparati.
    """
    import numpy as np
    from core.domain_gater import _knn_domains, assign_domain

    docs = scan_memories(r, "all")
    docs = [d for d in docs if d.get("embedding")]
    print_header(f"SCAN OUTLIER DI DOMINIO (R1) — {len(docs)} memorie con embedding")

    outliers = []
    for i, doc in enumerate(docs, 1):
        print(f"[{i}/{len(docs)}] analisi vicinato...      ", end="\r", flush=True)
        vec_bytes = np.asarray(doc["embedding"], dtype="float32").tobytes()
        mid = doc["_key"].replace("euri:memory:", "")
        assigned = doc.get("domain", "generale")
        neighbors = _knn_domains(vec_bytes, r, k=k, exclude_id=mid)
        if not neighbors or assigned in neighbors:
            continue  # coerente col vicinato → non è un outlier
        proposed = assign_domain(doc.get("content", ""), hint_domains=neighbors)
        outliers.append((doc, assigned, proposed, neighbors))

    print(" " * 50, end="\r")
    if not outliers:
        print("\nNessun outlier rilevato: ogni dominio è coerente col proprio vicinato.")
        return

    print(f"\n{len(outliers)} outlier rilevati:\n")
    for doc, assigned, proposed, neighbors in outliers:
        nb = ", ".join(dict.fromkeys(neighbors))
        mark = "→" if proposed != assigned else "(invariato)"
        print(f"  [{assigned}]  {mark}  [{proposed}]")
        print(f"     vicini: {nb}")
        print(f"     content: {doc.get('content', '')[:60]}")
        print()

    if fix:
        applied = 0
        for doc, assigned, proposed, _ in outliers:
            if proposed != assigned:
                r.json().set(doc["_key"], "$.domain", proposed)
                applied += 1
        print(f"→ {applied} domini ri-etichettati (tagger context-aware).")
    else:
        print("→ Solo report. Rivedi le proposte; usa --fix-outliers per applicarle.")


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


def _scan_json_docs(r: redis.Redis, pattern: str) -> list[dict]:
    """Legge documenti RedisJSON senza usare API di retrieval o mutare contatori/TTL."""
    docs = []
    for key in r.scan_iter(pattern):
        try:
            data = r.json().get(key, "$")
            if not data:
                continue
            doc = data[0]
            doc["_key"] = key
            docs.append(doc)
        except Exception:
            continue
    return docs


def _ts_to_dt(ts) -> datetime | None:
    if ts in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except Exception:
        return None


def _fmt_age(ts, now_dt: datetime) -> str:
    dt = _ts_to_dt(ts)
    if not dt:
        return "n/d"
    delta = now_dt - dt
    days = delta.days
    if days <= 0:
        hours = max(0, int(delta.total_seconds() // 3600))
        return f"{hours}h fa"
    if days < 30:
        return f"{days}g fa"
    return f"{days // 30} mesi fa"


def _print_counter(title: str, counter: Counter, limit: int = 15):
    print(f"\n{title}")
    if not counter:
        print("  n/d")
        return
    for key, count in counter.most_common(limit):
        label = str(key or "unknown")
        print(f"  {label:<28} {count:>5}")
    hidden = len(counter) - limit
    if hidden > 0:
        print(f"  ... altri {hidden}")


def _short(text: str, width: int = 92) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _consolidation_risk(r: redis.Redis, doc: dict) -> dict:
    """Calcola read-only la fragilità delle fonti di un nodo consolidato."""
    source_ids = doc.get("consolidated_from") or []
    risk = {
        "level": "ok",
        "total_sources": len(source_ids),
        "audit_flagged": [],
        "superseded": [],
        "missing": [],
        "requires_verification": [],
    }
    for cid in source_ids:
        try:
            raw = r.json().get(f"euri:memory:{cid}", "$")
        except Exception:
            raw = None
        if not raw:
            risk["missing"].append(cid)
            continue
        src = raw[0]
        if int(src.get("audit_flag") or 0) > 0:
            risk["audit_flagged"].append(cid)
        if src.get("superseded_by"):
            risk["superseded"].append(cid)
        if src.get("requires_verification"):
            risk["requires_verification"].append(cid)

    if risk["missing"] or risk["superseded"] or risk["requires_verification"]:
        risk["level"] = "high"
    elif risk["audit_flagged"]:
        risk["level"] = "watch"
    return risk


def backfill_consolidation_risk(r: redis.Redis, apply: bool = False):
    """Calcola e opzionalmente salva consolidation_risk sui loop2e esistenti."""
    docs = [
        d for d in _scan_json_docs(r, "euri:memory:*")
        if d.get("source") == "loop2e" and d.get("consolidated_from")
    ]
    if not docs:
        print("\nNessun nodo loop2e con consolidated_from.")
        return

    counts = Counter()
    changed = 0
    for doc in docs:
        risk = _consolidation_risk(r, doc)
        counts[risk["level"]] += 1
        old = doc.get("consolidation_risk")
        if old != risk:
            changed += 1
            if apply:
                key = doc["_key"]
                r.json().set(key, "$.consolidation_risk", risk)
                if risk["level"] != "ok":
                    r.json().set(key, "$.source_audit_flags", risk["audit_flagged"])

    print_header("BACKFILL CONSOLIDATION RISK")
    print(f"  loop2e analizzati     {len(docs):>5}")
    print(f"  da aggiornare         {changed:>5}")
    print(f"  ok                    {counts.get('ok', 0):>5}")
    print(f"  watch                 {counts.get('watch', 0):>5}")
    print(f"  high                  {counts.get('high', 0):>5}")
    if apply:
        print("  → campi Redis aggiornati.")
    else:
        print("  → dry-run. Usa --apply per salvare i campi Redis.")


def backfill_ttl_from_expires_at(r: redis.Redis, apply: bool = False):
    """
    Riallinea il TTL Redis al campo JSON expires_at (F-01/F-02).

    Modello: TTL Redis = fonte di verità operativa, expires_at = mirror di audit.
    Per le memorie temporanee (hanno expires_at) ma SENZA TTL Redis:
      - expires_at nel FUTURO → si imposta expireat (con --apply). Operazione sicura.
      - expires_at GIÀ SCADUTO → NON si tocca: impostare expireat su un timestamp
        passato cancellerebbe la chiave all'istante. Si segnala soltanto, perché la
        cancellazione richiede conferma esplicita di Stefano.
    Non cancella mai nulla.
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    docs = _scan_json_docs(r, "euri:memory:*")

    aligned = 0          # ha già TTL Redis coerente
    permanent = 0        # nessuna expires_at (user/teach/obsidian/loop2e) — corretto così
    to_set_future = []   # senza TTL, expires_at futura → target del backfill
    flagged_past = []    # senza TTL, expires_at scaduta → richiede conferma

    for doc in docs:
        exp = doc.get("expires_at")
        if not exp:
            permanent += 1
            continue
        ttl = r.ttl(doc["_key"])
        if ttl and ttl > 0:
            aligned += 1
            continue
        # ttl == -1 (chiave senza scadenza) → disallineata rispetto a expires_at
        if float(exp) > now_ts:
            to_set_future.append(doc)
        else:
            flagged_past.append(doc)

    applied = 0
    if apply:
        for doc in to_set_future:
            try:
                r.expireat(doc["_key"], int(float(doc["expires_at"])))
                applied += 1
            except Exception as e:
                print(f"  ! errore expireat su {doc['_key']}: {e}")

    print_header("BACKFILL TTL ⟵ expires_at")
    print(f"  memorie totali                       {len(docs):>5}")
    print(f"  permanenti (nessuna expires_at)      {permanent:>5}")
    print(f"  TTL già allineato                    {aligned:>5}")
    print(f"  senza TTL, expires_at FUTURA         {len(to_set_future):>5}  ← backfill sicuro")
    print(f"  senza TTL, expires_at SCADUTA        {len(flagged_past):>5}  ← richiede conferma, NON toccate")

    if flagged_past:
        print("\nScadute senza TTL (decisione di Stefano — questo script non cancella):")
        by_src = Counter(d.get("source") for d in flagged_past)
        for src, n in by_src.most_common():
            print(f"  {str(src or 'unknown'):<16} {n:>5}")
        for doc in sorted(flagged_past, key=lambda d: float(d.get("expires_at") or 0))[:10]:
            print(
                f"  {_fmt_age(doc.get('expires_at'), datetime.now(timezone.utc)):>9} scaduta | "
                f"{doc.get('source', '?'):<12} | {_short(doc.get('content', ''))}"
            )

    if apply:
        print(f"\n  → expireat impostato su {applied} chiavi (solo expires_at futura).")
    else:
        print("\n  → dry-run. Usa --apply per impostare il TTL sulle chiavi con expires_at futura.")


def read_only_report(r: redis.Redis, expiring_days: int = 14, report_source: str = "all"):
    """
    Report diagnostico non invasivo.

    Non chiama search_memories(), _hydrate(), _search_hybrid() o FT.SEARCH:
    legge solo RedisJSON via SCAN + JSON.GET, quindi non incrementa recalled_count
    e non estende TTL.
    """
    memories = _scan_json_docs(r, "euri:memory:*")
    if report_source != "all":
        memories = [d for d in memories if d.get("source") == report_source]
    insights = _scan_json_docs(r, "euri:insight:*")
    dreams = _scan_json_docs(r, "euri:dream:*")
    corrections = _scan_json_docs(r, "euri:correction:*")

    now_dt = datetime.now(timezone.utc)
    now_ts = now_dt.timestamp()
    exp_cutoff = now_ts + expiring_days * 86400

    by_source = Counter(d.get("source") for d in memories)
    by_domain = Counter(d.get("domain") for d in memories)
    by_category = Counter(d.get("category") for d in memories)
    by_safety = Counter(flag for d in memories for flag in (d.get("safety_flag") or []))

    permanent_sources = {"user", "teach", "obsidian_vault", "campus"}
    permanent = [d for d in memories if d.get("source") in permanent_sources and not d.get("expires_at")]
    no_expiry_non_explicit = [
        d for d in memories
        if not d.get("expires_at") and d.get("source") not in permanent_sources
    ]
    temporary = [d for d in memories if d.get("expires_at")]
    expired_field = [d for d in temporary if float(d.get("expires_at") or 0) <= now_ts]
    expiring = [d for d in temporary if now_ts < float(d.get("expires_at") or 0) <= exp_cutoff]
    requires_verification = [d for d in memories if d.get("requires_verification")]
    superseded = [d for d in memories if d.get("superseded_by")]
    audit_flagged = [d for d in memories if int(d.get("audit_flag") or 0) > 0]
    missing_embedding = [d for d in memories if not d.get("embedding")]
    never_recalled = [d for d in memories if int(d.get("recalled_count") or 0) == 0]
    with_consolidated_from = [d for d in memories if d.get("consolidated_from")]
    consolidation_risks = [
        (d, _consolidation_risk(r, d))
        for d in with_consolidated_from
    ]
    by_consolidation_risk = Counter(risk["level"] for _, risk in consolidation_risks)
    high_recall = sorted(memories, key=lambda d: int(d.get("recalled_count") or 0), reverse=True)
    recent = sorted(memories, key=lambda d: float(d.get("created_at") or 0), reverse=True)

    include_global_sections = report_source == "all"
    insight_by_status = Counter(d.get("status") for d in insights)
    insight_never_recalled = [d for d in insights if int(d.get("recalled_count") or 0) == 0]
    insight_promoted_cold = [
        d for d in insights
        if d.get("status") == "promoted" and int(d.get("recalled_count") or 0) == 0
    ]
    dream_by_status = Counter(d.get("status") for d in dreams)

    title = "REPORT READ-ONLY MEMORIA"
    if report_source != "all":
        title += f" — source={report_source}"
    print_header(title)
    print("Modalità: solo SCAN + JSON.GET. Nessun recalled_count o TTL modificato.")
    print(f"Timestamp: {now_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")

    print("\nSintesi")
    print(f"  Memorie totali                 {len(memories):>5}")
    print(f"  Permanenti esplicite           {len(permanent):>5}")
    print(f"  Senza expires_at non esplicite {len(no_expiry_non_explicit):>5}")
    print(f"  Temporanee con expires_at      {len(temporary):>5}")
    print(f"  Campo expires_at già scaduto   {len(expired_field):>5}")
    print(f"  In scadenza entro {expiring_days:>2}g        {len(expiring):>5}")
    print(f"  Mai richiamate                 {len(never_recalled):>5}")
    print(f"  Con consolidated_from          {len(with_consolidated_from):>5}")
    if with_consolidated_from:
        print(f"  Consolidati rischio high       {by_consolidation_risk.get('high', 0):>5}")
        print(f"  Consolidati rischio watch      {by_consolidation_risk.get('watch', 0):>5}")
    print(f"  Requires verification          {len(requires_verification):>5}")
    print(f"  Soft-deleted/superseded        {len(superseded):>5}")
    print(f"  Audit flag > 0                 {len(audit_flagged):>5}")
    print(f"  Senza embedding                {len(missing_embedding):>5}")
    if include_global_sections:
        print(f"  Insight totali                 {len(insights):>5}")
        print(f"  Insight promoted freddi        {len(insight_promoted_cold):>5}")
        print(f"  Dreams totali                  {len(dreams):>5}")
        print(f"  Correction signal aperti       {len(corrections):>5}")

    _print_counter("Distribuzione per source", by_source)
    _print_counter("Distribuzione per dominio", by_domain)
    _print_counter("Distribuzione per categoria", by_category)
    _print_counter("Safety flag", by_safety)
    if with_consolidated_from:
        _print_counter("Rischio consolidati", by_consolidation_risk)
    if include_global_sections:
        _print_counter("Insight per status", insight_by_status)
        _print_counter("Dream per status", dream_by_status)

    print("\nTop memorie per recalled_count")
    for doc in high_recall[:10]:
        print(
            f"  {int(doc.get('recalled_count') or 0):>4} | "
            f"{doc.get('source', '?'):<12} | {doc.get('domain', '?'):<24} | "
            f"{_short(doc.get('content', ''))}"
        )

    print("\nMemorie recenti")
    for doc in recent[:10]:
        print(
            f"  {_fmt_age(doc.get('created_at'), now_dt):>9} | "
            f"{doc.get('source', '?'):<12} | {doc.get('domain', '?'):<24} | "
            f"{_short(doc.get('content', ''))}"
        )

    if expiring:
        print(f"\nMemorie in scadenza entro {expiring_days} giorni")
        for doc in sorted(expiring, key=lambda d: float(d.get("expires_at") or 0))[:15]:
            print(
                f"  {_fmt_age(doc.get('created_at'), now_dt):>9} | "
                f"{doc.get('source', '?'):<12} | rc={int(doc.get('recalled_count') or 0):<3} | "
                f"{_short(doc.get('content', ''))}"
            )

    if no_expiry_non_explicit:
        print("\nSenza expires_at non esplicite")
        by_no_expiry_source = Counter(d.get("source") for d in no_expiry_non_explicit)
        for source, count in by_no_expiry_source.most_common():
            print(f"  {str(source or 'unknown'):<16} {count:>5}")

    if requires_verification:
        print("\nRequires verification")
        for doc in sorted(requires_verification, key=lambda d: float(d.get("created_at") or 0), reverse=True)[:15]:
            print(
                f"  {doc.get('source', '?'):<12} | {doc.get('domain', '?'):<24} | "
                f"{_short(doc.get('content', ''))}"
            )

    if superseded:
        print("\nSoft-deleted / superseded")
        for doc in sorted(superseded, key=lambda d: float(d.get("created_at") or 0), reverse=True)[:10]:
            print(
                f"  {doc.get('id', '?')[:8]} -> {str(doc.get('superseded_by'))[:8]} | "
                f"{doc.get('source', '?'):<12} | {_short(doc.get('content', ''))}"
            )

    if audit_flagged:
        print("\nAudit flag")
        for doc in sorted(audit_flagged, key=lambda d: int(d.get("audit_flag") or 0), reverse=True)[:10]:
            print(
                f"  flag={int(doc.get('audit_flag') or 0):>2} | "
                f"{doc.get('source', '?'):<12} | {doc.get('domain', '?'):<24} | "
                f"{_short(doc.get('content', ''))}"
            )

    risky_consolidations = [
        (doc, risk) for doc, risk in consolidation_risks
        if risk["level"] != "ok"
    ]
    if risky_consolidations:
        print("\nConsolidati con fonti fragili")
        risky_consolidations.sort(
            key=lambda x: (
                0 if x[1]["level"] == "high" else 1,
                -len(x[1]["audit_flagged"]),
                int(x[0].get("recalled_count") or 0),
            )
        )
        for doc, risk in risky_consolidations[:15]:
            details = []
            if risk["missing"]:
                details.append(f"missing={len(risk['missing'])}")
            if risk["superseded"]:
                details.append(f"superseded={len(risk['superseded'])}")
            if risk["requires_verification"]:
                details.append(f"rv={len(risk['requires_verification'])}")
            if risk["audit_flagged"]:
                details.append(f"audit_src={len(risk['audit_flagged'])}")
            print(
                f"  {risk['level']:<5} | rc={int(doc.get('recalled_count') or 0):<3} | "
                f"{doc.get('id', '?')[:8]} | {', '.join(details):<28} | "
                f"{_short(doc.get('content', ''))}"
            )

    if include_global_sections and insight_never_recalled:
        print("\nInsight mai richiamati")
        for doc in sorted(insight_never_recalled, key=lambda d: float(d.get("created_at") or 0), reverse=True)[:10]:
            domains = f"{doc.get('domain_a', '?')} ↔ {doc.get('domain_b', '?')}"
            print(f"  {doc.get('status', '?'):<10} | {domains:<38} | {_short(doc.get('content', ''))}")

    print("\nNota")
    print("  Questo report misura lo stato della memoria; non decide cancellazioni e non chiama LLM.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit e pulizia memorie Redis di Euri")
    parser.add_argument("--source", default="passive",
                        help="Source da auditare: passive, user, web, teach, conversation, all (default: passive)")
    parser.add_argument("--delete", metavar="SOURCE",
                        help="Cancella TUTTE le memorie di una source senza audit")
    parser.add_argument("--stats", action="store_true",
                        help="Mostra solo statistiche per source")
    parser.add_argument("--report", action="store_true",
                        help="Report read-only completo: memorie, domini, TTL, insight, audit flag")
    parser.add_argument("--report-source", default="all",
                        help="Filtra il report read-only per source (default: all)")
    parser.add_argument("--expiring-days", type=int, default=14,
                        help="Finestra per memorie in scadenza nel report read-only (default: 14)")
    parser.add_argument("--backfill-domains", action="store_true",
                        help="Ricalcola il domain label per le memorie con domain='generale'")
    parser.add_argument("--backfill-all", action="store_true",
                        help="Ricalcola il domain label per TUTTE le memorie")
    parser.add_argument("--scan-outliers", action="store_true",
                        help="R1: rileva memorie con dominio incoerente col vicinato (solo report)")
    parser.add_argument("--fix-outliers", action="store_true",
                        help="R1: rileva E ri-etichetta gli outlier al dominio modale dei vicini")
    parser.add_argument("--backfill-consolidation-risk", action="store_true",
                        help="Calcola/salva consolidation_risk sui nodi loop2e esistenti")
    parser.add_argument("--backfill-ttl", action="store_true",
                        help="Riallinea il TTL Redis al campo expires_at (solo scadenze future; le scadute non vengono toccate)")
    parser.add_argument("--apply", action="store_true",
                        help="Applica i backfill che supportano dry-run")
    args = parser.parse_args()

    r = get_client()

    try:
        r.ping()
    except Exception:
        print("Errore: Redis non raggiungibile su localhost:6379")
        sys.exit(1)

    if args.report:
        read_only_report(r, expiring_days=args.expiring_days, report_source=args.report_source)
    elif args.backfill_consolidation_risk:
        backfill_consolidation_risk(r, apply=args.apply)
    elif args.backfill_ttl:
        backfill_ttl_from_expires_at(r, apply=args.apply)
    elif args.stats:
        stats(r)
    elif args.fix_outliers:
        scan_outliers(r, fix=True)
    elif args.scan_outliers:
        scan_outliers(r, fix=False)
    elif args.delete:
        delete_by_source(r, args.delete)
    elif args.backfill_all:
        backfill_domains(r, only_generale=False)
    elif args.backfill_domains:
        backfill_domains(r, only_generale=True)
    else:
        audit(r, args.source)
