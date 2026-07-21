"""Campionatore per l'audit CIECO dell'esperimento dream_trace (continuità 2b).

Pesca i candidate dalla euri:convergence:trace (che logga seed_content per OGNI esito,
promossi e scartati → niente selection bias del gate), li divide nei due bracci:

  baseline    = trace_injected == ""   (candidate pre-esperimento, flag spento)
  trattamento = trace_injected == "1"  (candidate nati con residuo iniettato)
  esclusi     = trace_injected == "0"  (flag acceso ma residuo assente: primo ciclo
                o residuo scaduto — braccio ambiguo, fuori da entrambi)

e scrive DUE file:
  AUDIT_DREAM_TRACE_items_<data>.md — lista MESCOLATA e SENZA etichetta di braccio,
      con la rubrica di maggio (non-ovvio / ovvio / incerto) da compilare a mano.
      Stefano NON deve sapere da che braccio viene un item mentre giudica.
  AUDIT_DREAM_TRACE_key_<data>.json — la chiave item→braccio, da aprire SOLO a
      etichette finite (unblinding).

Read-only su Redis. Uso:  python scripts/experiments/sample_dream_audit.py [n_per_braccio]
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/fio/Euri")

import redis

import config

N_DEFAULT = 60
# Riavvio che ha caricato il fix anti-eco del commit 2aa540c. I due candidate
# trattamento precedenti hanno ricevuto il residuo degenere e sono esclusi dalla
# misura per la regola pre-registrata in ESPERIMENTO_DREAM_TRACE.md.
ANTI_ECHO_RESTART_TS = 1783956958.0  # 13/07/2026 17:35:58 Europe/Rome
OUTPUT_DIR = Path("audit_output")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_DEFAULT
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)

    seen = set()
    arms = {"baseline": [], "trattamento": []}
    excluded_anti_echo = 0
    for _eid, f in r.xrange("euri:convergence:trace", "-", "+"):
        sid = f.get("seed_id", "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        content = (f.get("seed_content") or "").strip()
        if not content:
            continue
        ti = f.get("trace_injected", "")
        if ti == "1":
            created_at = float(f.get("created_at") or 0)
            if created_at < ANTI_ECHO_RESTART_TS:
                excluded_anti_echo += 1
                continue
            arms["trattamento"].append({
                "seed_id": sid,
                "content": content,
                "created_at": created_at,
                "promotion_policy": f.get("promotion_policy") or "legacy",
            })
        elif ti == "":
            arms["baseline"].append({
                "seed_id": sid,
                "content": content,
                "created_at": float(f.get("created_at") or 0),
                "promotion_policy": f.get("promotion_policy") or "legacy",
            })
        # ti == "0": braccio ambiguo, escluso

    print(f"candidate distinti: baseline={len(arms['baseline'])} "
          f"trattamento={len(arms['trattamento'])} "
          f"esclusi_anti_echo={excluded_anti_echo}")

    rng = random.Random(20260713)  # riproducibile
    sample = []
    for arm, pool in arms.items():
        take = rng.sample(pool, min(n, len(pool)))
        sample.extend((arm, item) for item in take)
        if len(pool) < n:
            print(f"  ATTENZIONE: {arm} ha solo {len(pool)} candidate (< {n}) — "
                  f"raccogliere ancora prima dell'audit?")
    rng.shuffle(sample)

    tag = date.today().strftime("%Y%m%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_items_{tag}.md"
    key_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_key_{tag}.json"

    with open(items_path, "w") as fh:
        fh.write("# Audit cieco dream_trace — rubrica di maggio\n\n")
        fh.write("Per ogni item UNA sola X: `[N]`=non-ovvio  `[O]`=ovvio  `[?]`=incerto.\n")
        fh.write("Giudica il contenuto, non la forma. NON aprire il file key finché non hai finito.\n\n")
        for i, (_arm, item) in enumerate(sample, 1):
            fh.write(
                f"---\n\n**#{i}**  [ ]N  [ ]O  [ ]?\n\n{item['content']}\n\n"
            )

    with open(key_path, "w") as fh:
        json.dump({
            str(i): {
                "arm": arm,
                "seed_id": item["seed_id"],
                "created_at": item["created_at"],
                "promotion_policy": item["promotion_policy"],
            }
            for i, (arm, item) in enumerate(sample, 1)
        }, fh, indent=2)

    print(f"scritti: {items_path} ({len(sample)} item mescolati) + {key_path} (chiave)")


if __name__ == "__main__":
    main()
