"""Campione CIECO esplorativo dei candidate Dream recuperabili integralmente.

Questo script NON ricostruisce l'esperimento pre-registrato: seleziona a posteriori
solo candidate il cui testo completo sopravvive nel RedisJSON dell'insight oppure
nel dream grezzo (TTL 7 giorni). La disponibilita' e' molto diversa tra i bracci,
quindi il risultato puo' descrivere la qualita' dei sopravvissuti ma non stimare
causalmente l'effetto di dream_trace.

Scrive una lista cieca e una chiave separata. Non modifica Redis.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/fio/Euri")

import redis

import config


N_DEFAULT = 60
ANTI_ECHO_RESTART_TS = 1783956958.0
RANDOM_SEED = 20260721
OUTPUT_DIR = Path("audit_output")


def _json_scalar(r, key: str, path: str) -> str:
    value = r.json().get(key, path)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def _raw_dream_contents(r) -> list[str]:
    contents = []
    for key in r.scan_iter(match="euri:dream:*"):
        if r.type(key) != "ReJSON-RL":
            continue
        content = _json_scalar(r, key, "$.content").strip()
        if content and content != "Nessuna analogia trovata":
            contents.append(content)
    return contents


def _recover_full_content(r, seed_id: str, trace_prefix: str,
                          raw_contents: list[str]):
    """Recupera solo una copia univoca che conserva esattamente la trace come prefisso."""
    live = _json_scalar(r, seed_id, "$.content").strip()
    if live and live.startswith(trace_prefix):
        return live, "live_insight"

    matches = [content for content in raw_contents if content.startswith(trace_prefix)]
    if len(matches) == 1:
        return matches[0], "raw_dream"
    return None, None


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_DEFAULT
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )
    raw_contents = _raw_dream_contents(r)

    seen = set()
    arms = {"baseline": [], "trattamento": []}
    missing = {"baseline": 0, "trattamento": 0}
    excluded_anti_echo = 0

    for _entry_id, fields in r.xrange("euri:convergence:trace", "-", "+"):
        seed_id = fields.get("seed_id", "")
        if not seed_id or seed_id in seen:
            continue
        seen.add(seed_id)
        trace_prefix = (fields.get("seed_content") or "").strip()
        if not trace_prefix:
            continue

        trace_injected = fields.get("trace_injected", "")
        created_at = float(fields.get("created_at") or 0)
        if trace_injected == "1":
            if created_at < ANTI_ECHO_RESTART_TS:
                excluded_anti_echo += 1
                continue
            arm = "trattamento"
        elif trace_injected == "":
            arm = "baseline"
        else:
            continue

        content, recovery_source = _recover_full_content(
            r, seed_id, trace_prefix, raw_contents
        )
        if content is None:
            missing[arm] += 1
            continue
        arms[arm].append({
            "seed_id": seed_id,
            "content": content,
            "content_chars": len(content),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "created_at": created_at,
            "promotion_policy": fields.get("promotion_policy") or "legacy",
            "recovery_source": recovery_source,
        })

    print(
        "pool recuperabile: "
        f"baseline={len(arms['baseline'])} (mancanti={missing['baseline']}), "
        f"trattamento={len(arms['trattamento'])} (mancanti={missing['trattamento']}), "
        f"esclusi_anti_echo={excluded_anti_echo}"
    )
    undersized = {arm: len(pool) for arm, pool in arms.items() if len(pool) < n}
    if undersized:
        details = ", ".join(f"{arm}={count}/{n}" for arm, count in undersized.items())
        raise RuntimeError(f"Campione recuperato insufficiente: {details}")

    rng = random.Random(RANDOM_SEED)
    sample = []
    for arm, pool in arms.items():
        sample.extend((arm, item) for item in rng.sample(pool, n))
    rng.shuffle(sample)

    tag = date.today().strftime("%Y%m%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_RECOVERED_items_{tag}.md"
    key_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_RECOVERED_key_{tag}.json"

    with open(items_path, "x", encoding="utf-8") as fh:
        fh.write("# Audit cieco ESPLORATIVO Dream Trace — testi recuperati\n\n")
        fh.write(
            "Campione post-hoc 60+60 di soli testi integralmente recuperabili. "
            "Descrive i sopravvissuti; non sostituisce il test pre-registrato.\n\n"
        )
        fh.write("Per ogni item UNA sola X: `[N]`=non-ovvio  `[O]`=ovvio  `[?]`=incerto.\n")
        fh.write("Giudica il contenuto, non la forma. NON aprire il file key.\n\n")
        for index, (_arm, item) in enumerate(sample, 1):
            fh.write(
                f"---\n\n**#{index}**  [ ]N  [ ]O  [ ]?\n\n"
                f"{item['content']}\n\n"
            )

    with open(key_path, "x", encoding="utf-8") as fh:
        json.dump({
            str(index): {
                "arm": arm,
                "seed_id": item["seed_id"],
                "created_at": item["created_at"],
                "promotion_policy": item["promotion_policy"],
                "recovery_source": item["recovery_source"],
                "content_chars": item["content_chars"],
                "content_sha256": item["content_sha256"],
            }
            for index, (arm, item) in enumerate(sample, 1)
        }, fh, indent=2, ensure_ascii=False)

    print(f"scritti: {items_path} ({len(sample)} item) + {key_path} (chiave sigillata)")


if __name__ == "__main__":
    main()
