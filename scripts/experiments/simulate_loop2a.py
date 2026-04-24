#!/usr/bin/env python3
"""
simulate_loop2a.py — Dry-run offline di Loop 2a (background reflection).

Legge memorie da Redis DB 0, raggruppa in sessioni simulate, genera
riflessioni tramite Ollama e produce un report markdown.
NON scrive nulla su Redis.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import redis

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import config as euri_config

# Sources escluse dalla selezione temporale delle sessioni
SESSION_EXCLUDE_SOURCES = {"campus", "web"}
# Sources escluse anche dalle memorie correlate
RELATED_EXCLUDE_SOURCES = {"web"}

SYSTEM_PROMPT = """\
Sei un sistema di consolidamento memoria. Il tuo compito è leggere alcune
memorie recenti di Stefano insieme a memorie correlate dal suo archivio, e
produrre una sintesi breve che identifichi:

1. Il tema dominante di questa sessione
2. Eventuali connessioni con attività passate di Stefano
3. Un possibile "interesse a breve termine" che potrebbe emergere

Regole:
- Massimo 3 frasi totali
- Tono funzionale, non emotivo
- Prima persona esclusa: scrivi in terza persona su Stefano
- Nessun preambolo, nessuna introduzione ("Ecco la sintesi:", ecc.)
- Se le memorie sono troppo scollegate per una sintesi utile, rispondi
  esattamente: "NO_COHERENT_PATTERN"\
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simulate Loop 2a — offline reflection dry-run")
    p.add_argument("--session-window-minutes", type=int, default=10,
                   help="Finestra temporale per raggruppare memorie in sessioni (default: 10)")
    p.add_argument("--max-sessions", type=int, default=10,
                   help="Numero massimo di sessioni da processare (default: 10)")
    p.add_argument("--min-memories-per-session", type=int, default=4,
                   help="Memorie minime per considerare una sessione (default: 4)")
    p.add_argument("--output-dir", type=str, default="./experiments_output",
                   help="Directory di output per il report (default: ./experiments_output)")
    return p.parse_args()


def load_all_memories(r: redis.Redis) -> list[dict]:
    """SCAN-based load — non blocca Redis su corpus grandi."""
    memories = []
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor, match="euri:memory:*", count=100)
        for key in keys:
            try:
                val = r.json().get(key)
                if val:
                    memories.append(val)
            except Exception as e:
                print(f"  [WARN] Skip {key}: {e}", file=sys.stderr)
        if cursor == 0:
            break
    return memories


def ts_to_dt(ts) -> datetime:
    if ts is None:
        return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(float(ts))


def group_into_sessions(memories: list[dict], window_minutes: int) -> list[list[dict]]:
    """
    Raggruppa per finestre temporali contigue.
    Esclude SESSION_EXCLUDE_SOURCES dalla selezione.
    """
    eligible = [m for m in memories if m.get("source") not in SESSION_EXCLUDE_SOURCES]
    eligible.sort(key=lambda m: float(m.get("created_at") or 0))

    if not eligible:
        return []

    window_secs = window_minutes * 60
    sessions: list[list[dict]] = [[eligible[0]]]

    for mem in eligible[1:]:
        last_ts = float(sessions[-1][-1].get("created_at") or 0)
        curr_ts = float(mem.get("created_at") or 0)
        if curr_ts - last_ts <= window_secs:
            sessions[-1].append(mem)
        else:
            sessions.append([mem])

    return sessions


def get_stored_embedding(mem: dict) -> np.ndarray | None:
    emb = mem.get("embedding")
    if isinstance(emb, list) and len(emb) == 384:
        return np.array(emb, dtype=np.float32)
    return None


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def find_related(
    session_memories: list[dict],
    all_memories: list[dict],
    n: int = 5,
) -> list[dict]:
    """
    Trova le N memorie più simili via cosine similarity sugli embedding già
    salvati in Redis. Campus è ammesso qui; web è escluso.
    """
    session_ids = {m.get("id") for m in session_memories}

    vecs = [get_stored_embedding(m) for m in session_memories]
    vecs = [v for v in vecs if v is not None]
    if not vecs:
        return []
    mean_vec = np.mean(vecs, axis=0).astype(np.float32)

    scored = []
    for mem in all_memories:
        if mem.get("id") in session_ids:
            continue
        if mem.get("source") in RELATED_EXCLUDE_SOURCES:
            continue
        vec = get_stored_embedding(mem)
        if vec is None:
            continue
        scored.append((cosine_sim(mean_vec, vec), mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:n]]


def fmt_memories(mems: list[dict], truncate: int = 120) -> str:
    lines = []
    for m in mems:
        src = m.get("source", "?")
        content = (m.get("content") or "")[:truncate]
        lines.append(f"[{src}] {content}")
    return "\n".join(lines)


def generate_reflection(
    session_memories: list[dict],
    related_memories: list[dict],
    model: str,
) -> tuple[str, float]:
    import ollama

    user_msg = (
        f"Memorie recenti della sessione:\n{fmt_memories(session_memories)}\n\n"
        f"Memorie correlate dall'archivio:\n{fmt_memories(related_memories)}"
    )

    t0 = time.monotonic()
    try:
        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            options={"temperature": 0.4, "num_predict": 200},
            think=False,
        )
        elapsed = time.monotonic() - t0
        msg = response["message"]
        content = msg.content if hasattr(msg, "content") else msg["content"]
        return (content or "").strip(), elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        return f"ERROR: {e}", elapsed


def build_report(
    sessions_data: list[dict],
    total_memories: int,
    generated_at: datetime,
) -> str:
    coherent = [
        s for s in sessions_data
        if s["reflection"] not in ("NO_COHERENT_PATTERN",)
        and not s["reflection"].startswith("ERROR")
    ]
    no_pattern = [s for s in sessions_data if s["reflection"] == "NO_COHERENT_PATTERN"]
    errors = [s for s in sessions_data if s["reflection"].startswith("ERROR")]

    times = [s["elapsed"] for s in sessions_data if not s["reflection"].startswith("ERROR")]
    avg_time = sum(times) / len(times) if times else 0.0

    lengths = [len(s["reflection"]) for s in coherent]
    avg_len = int(sum(lengths) / len(lengths)) if lengths else 0
    median_len = sorted(lengths)[len(lengths) // 2] if lengths else 0
    pct_coh = 100 * len(coherent) / len(sessions_data) if sessions_data else 0

    lines = [
        "# Simulate Loop 2a — Report",
        "",
        f"**Generated:** {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Total memories scanned:** {total_memories}",
        f"**Sessions identified:** {len(sessions_data)}",
        (
            f"**Total reflections generated:** {len(sessions_data)} "
            f"(of which {len(no_pattern)} returned NO_COHERENT_PATTERN, "
            f"{len(errors)} errors)"
        ),
        "",
        "---",
        "",
        "## Statistics",
        "",
        f"- Average reflection generation time: {avg_time:.1f}s",
        f"- Average reflection length: {avg_len} chars",
        f"- Median reflection length: {median_len} chars",
        f"- Coherent reflections: {len(coherent)} ({pct_coh:.0f}%)",
        (
            f"- Non-coherent (NO_COHERENT_PATTERN): {len(no_pattern)} "
            f"({100*len(no_pattern)/len(sessions_data) if sessions_data else 0:.0f}%)"
        ),
        f"- Errors: {len(errors)}",
        "",
        "---",
        "",
        "## Sessions",
    ]

    for i, s in enumerate(sessions_data, 1):
        dt_str = s["session_start"].strftime("%Y-%m-%d %H:%M")
        n = len(s["session_memories"])
        lines += [
            "",
            f"### Session {i} — {dt_str} ({n} memories)",
            "",
            "**Input memories:**",
        ]
        for m in s["session_memories"]:
            src = m.get("source", "?")
            content = (m.get("content") or "")[:80]
            lines.append(f"- [{src}] \"{content}\"")

        lines += ["", "**Related memories from archive:**"]
        for m in s["related_memories"]:
            src = m.get("source", "?")
            content = (m.get("content") or "")[:80]
            lines.append(f"- [{src}] \"{content}\"")

        lines += [
            "",
            "**Generated reflection:**",
            "",
            f"> {s['reflection']}",
            "",
            f"**Generation time:** {s['elapsed']:.1f}s",
            "",
            "---",
        ]

    return "\n".join(lines)


def main() -> None:
    args = parse_args()

    # --- Redis ---
    print("Connecting to Redis DB 0...")
    r = redis.Redis(
        host=euri_config.REDIS_HOST,
        port=euri_config.REDIS_PORT,
        db=0,
        decode_responses=False,
    )
    try:
        r.ping()
    except Exception as e:
        print(f"ABORT: Redis unreachable — {e}", file=sys.stderr)
        sys.exit(1)

    print("Loading memories...")
    all_memories = load_all_memories(r)
    print(f"  {len(all_memories)} memories loaded")
    if not all_memories:
        print("ABORT: no memories found.", file=sys.stderr)
        sys.exit(1)

    # --- Embedder sanity check ---
    print("Loading embedder (sanity check)...")
    from core.embedder import Embedder
    emb = Embedder()
    emb.load()
    if not emb.available:
        print("ABORT: embedder failed to load.", file=sys.stderr)
        sys.exit(1)
    print("  Embedder ready.")

    # --- Session grouping ---
    sessions = group_into_sessions(all_memories, args.session_window_minutes)
    sessions = [s for s in sessions if len(s) >= args.min_memories_per_session]
    print(
        f"  Sessions with >= {args.min_memories_per_session} memories: "
        f"{len(sessions)}"
    )
    if not sessions:
        print("ABORT: no sessions meet the minimum threshold.", file=sys.stderr)
        sys.exit(1)

    sessions = sessions[: args.max_sessions]
    model = euri_config.OLLAMA_MODEL
    print(f"  Processing {len(sessions)} sessions — model: {model}")

    # --- Main loop ---
    sessions_data = []
    for i, session in enumerate(sessions, 1):
        session_start = ts_to_dt(session[0].get("created_at"))
        print(
            f"\n[{i}/{len(sessions)}] {session_start.strftime('%Y-%m-%d %H:%M')} "
            f"— {len(session)} memories"
        )

        related = find_related(session, all_memories, n=5)
        print(f"  Related: {len(related)}")

        print("  Generating reflection...", end=" ", flush=True)
        reflection, elapsed = generate_reflection(session, related, model)
        print(f"{elapsed:.1f}s")
        preview = reflection[:80].replace("\n", " ")
        print(f"  → {preview}...")

        sessions_data.append(
            {
                "session_start": session_start,
                "session_memories": session,
                "related_memories": related,
                "reflection": reflection,
                "elapsed": elapsed,
            }
        )

        time.sleep(0.5)

    # --- Report ---
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"simulate_loop2a_report_{ts}.md"

    report = build_report(sessions_data, len(all_memories), datetime.now())
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
