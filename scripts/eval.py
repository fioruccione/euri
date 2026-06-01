#!/usr/bin/env python3
"""
eval.py — lancia TUTTO il set di benchmark di Euri in un colpo:
  - eval_euri.py   → calibrazione (è onesto, dato un contesto?)
  - eval_recall.py → recupero reale dal RAG (ricorda il fatto giusto?)

Uso da shell:
    PYTHONPATH=. ./venv/bin/python scripts/eval.py

Oppure dal prompt di Euri (Silent Chat): scrivi semplicemente  eval
(il tool run_eval lancia questo script in un subprocess isolato).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # root (core, config…)
sys.path.insert(0, str(Path(__file__).parent))          # scripts/ (eval_euri, eval_recall)

import eval_euri    # noqa: E402
import eval_recall  # noqa: E402


def main():
    print("\n" + "#" * 66)
    print("#  EVAL SUITE EURI — calibrazione + recall")
    print("#" * 66)

    p1, t1 = eval_euri.main()
    print()
    p2, t2 = eval_recall.main()

    tot, den = p1 + p2, t1 + t2
    print("\n" + "#" * 66)
    print(f"#  TOTALE COMPLESSIVO: {tot}/{den}   "
          f"(calibrazione {p1}/{t1} · recall {p2}/{t2})")
    print("#" * 66)
    return tot, den


if __name__ == "__main__":
    main()
