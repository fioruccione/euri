#!/usr/bin/env python3
"""
Diagnostico READ-ONLY del plausibility gate — misura il rischio FALSI POSITIVI.

Sceglie 30-40 memorie tra le più INUSUALI-MA-VERE del DB (gemme di dominio: Realube/Reagens/
VistaMax, MFI/IZOD/grado anomali, schede impianti, chimica di materiali sporchi/contaminati)
— NON gli 8 che il ciclo prenderebbe per recall — e gira _llm_plausibility_check su ognuna.
Mostra la distribuzione di verdetti/confidenze ed evidenzia quante toccano o superano la
soglia di flag (suspicious≥0.70 / impossible≥0.82). NON scrive nulla in Redis.

Uso: venv/bin/python diag_plausibility.py [N]   (N candidati, default 36)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import redis as redis_lib
import config
from core.dream_engine import DreamEngine

N = int(sys.argv[1]) if len(sys.argv) > 1 else 36
SKIP_SOURCES = {"web", "reflection", "conversation"}

# Lessico delle gemme di dominio inusuali-ma-vere (selezione, non giudizio).
KW = [
    "realube", "reagens", "vistamax", "mfi", "izod", "perossido", "dcp", "talco",
    "carbonat", "neutro", "densificat", "macinat", "contaminat", "sporco", "umido",
    "grumi", "reolog", "carbon black", "master", "italrek", "icma", "costarelli",
    "densificatore", "mdpe", "hdpe", "grado", "fluidit", "additiv", "blend",
    "modulo", "trazione", "filler", "carica", "tenacit", "estrusion", "iniezione",
]

r = redis_lib.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
r.ping()
engine = DreamEngine.__new__(DreamEngine)   # niente init: serve solo _llm_plausibility_check
print("✓ Redis connesso, engine pronto (read-only)\n")

# 1. Seleziona le gemme: source non escluse, non superseded, contengono lessico di dominio.
cands = []
for key in r.scan_iter("euri:memory:*"):
    try:
        d = r.json().get(key, "$")
    except Exception:
        continue
    if not d:
        continue
    doc = d[0]
    content = (doc.get("content") or "").strip()
    if not content or doc.get("superseded_by") or doc.get("source") in SKIP_SOURCES:
        continue
    low = content.lower()
    hits = sum(1 for k in KW if k in low)
    if hits == 0:
        continue
    # priorità: più denso di lessico tecnico + già flaggato requires_verification + richiamato
    score = (hits, 1 if doc.get("requires_verification") else 0, int(doc.get("recalled_count") or 0))
    cands.append((score, doc))

cands.sort(key=lambda x: x[0], reverse=True)
cands = cands[:N]
print(f"Gemme di dominio selezionate: {len(cands)} (su soglia gate: impossible≥0.82, suspicious≥0.70)\n")

# 2. Giudizio di plausibilità su ognuna (read-only).
rows = []
for _score, doc in cands:
    res = engine._llm_plausibility_check(doc.get("content", ""), doc.get("domain", "generale"))
    v = (res.get("verdict") or "?").strip().lower()
    try:
        c = float(res.get("confidence", 0.0))
    except (TypeError, ValueError):
        c = 0.0
    would_flag = DreamEngine._plausibility_should_flag(v, c)
    rows.append((doc, v, c, res.get("reason", ""), would_flag, bool(doc.get("requires_verification"))))

# 3. Distribuzione verdetti.
from collections import Counter
vc = Counter(v for _, v, _, _, _, _ in rows)
print("── Distribuzione VERDETTI ──────────────────")
for v, n in vc.most_common():
    print(f"  {v:<12} {n}")

# 4. Distribuzione confidenze per fascia.
def bucket(c):
    if c < 0.5: return "<0.50"
    if c < 0.70: return "0.50–0.69"
    if c < 0.82: return "0.70–0.81"
    return "≥0.82"
bc = Counter(bucket(c) for _, _, c, _, _, _ in rows)
print("\n── Distribuzione CONFIDENZE ────────────────")
for b in ["<0.50", "0.50–0.69", "0.70–0.81", "≥0.82"]:
    print(f"  {b:<12} {bc.get(b,0)}")

# 5. Quante toccano/superano la soglia suspicious≥0.70 (il numero che interessa).
susp_70 = [x for x in rows if x[1] == "suspicious" and x[2] >= 0.70]
imp_82 = [x for x in rows if x[1] == "impossible" and x[2] >= 0.82]
flagged = [x for x in rows if x[4]]
susp_near = [x for x in rows if x[1] == "suspicious" and 0.65 <= x[2] < 0.70]  # quasi-soglia

print("\n── SOGLIA ──────────────────────────────────")
print(f"  suspicious ≥ 0.70 : {len(susp_70)}")
print(f"  impossible ≥ 0.82 : {len(imp_82)}")
print(f"  TOTALE che il gate FLAGGEREBBE: {len(flagged)}")
print(f"  (quasi-soglia suspicious 0.65–0.69: {len(susp_near)})")

# 6. Dettaglio di TUTTE quelle che il gate flaggerebbe — per giudicare i falsi positivi.
print("\n── DETTAGLIO: cosa verrebbe FLAGGATO (giudica se è una gemma vera) ──")
if not flagged:
    print("  (nessuna)")
for doc, v, c, reason, _, rv in sorted(flagged, key=lambda x: -x[2]):
    cid = (doc.get("id") or "")[:8]
    rvtag = "req_verif" if rv else "NO req_verif (il gate NON la prenderebbe comunque)"
    print(f"\n  🚩 [{cid}] {v} {c:.2f}  src={doc.get('source')} dom={doc.get('domain')} [{rvtag}]")
    print(f"     {(doc.get('content') or '').strip()[:180]}")
    print(f"     reason: {reason[:200]}")

print("\n══════════════════════════════════════════")
print(f"  VERDETTO SOGLIA: {len(flagged)} flaggate su {len(rows)} gemme.")
print("  > Se più di 1-2 sono gemme VERE → 0.70 troppo bassa, da rivedere prima di attivare.")
print("══════════════════════════════════════════")
