"""
diag_provenance.py — audit READ-ONLY della propagazione di provenienza (invariante A).
Le memorie derivate (loop2e) hanno gia' `consolidated_from` (edge ai genitori) e un
`consolidation_risk` FOTOGRAFATO alla nascita. Questo script ricalcola il rischio DAL VIVO
(stato attuale dei genitori) e misura quanti nodi ATTIVI poggiano su genitori ormai
superseded/spariti/da-verificare — la classe-Leonardo. Nessuna scrittura.
"""
from utils.redis_client import get_client

r = get_client()

def live_risk(parents):
    sup=[]; miss=[]; rv=[]; af=[]
    for cid in parents:
        raw = r.json().get(f"euri:memory:{cid}", "$")
        doc = raw[0] if raw else None
        if not doc: miss.append(cid); continue
        if int(doc.get("audit_flag") or 0) > 0: af.append(cid)
        if doc.get("superseded_by"): sup.append(cid)
        if doc.get("requires_verification"): rv.append(cid)
    level = "high" if (miss or sup or rv) else ("watch" if af else "ok")
    return level, sup, miss, rv, af

total=0; active=0; rotted=[]; drift=[]
for key in r.scan_iter("euri:memory:*"):
    try: d = r.json().get(key, "$")[0]
    except Exception: continue
    parents = d.get("consolidated_from")
    if not parents: continue
    total += 1
    is_active = not d.get("superseded_by")
    level, sup, miss, rv, af = live_risk(parents)
    if is_active: active += 1
    stored = (d.get("consolidation_risk") or {}).get("level", "n/a")
    if is_active and level == "high":
        rotted.append((d, level, sup, miss, rv, stored))
    # drift: il rischio LIVE e' peggiorato rispetto allo snapshot alla nascita
    order={"ok":0,"watch":1,"high":2,"n/a":-1}
    if is_active and order.get(level,-1) > order.get(stored,-1):
        drift.append(d)

print(f"== nodi derivati (consolidated_from): {total} | attivi: {active} ==")
print(f"ATTIVI con rischio-LIVE high (poggiano su genitori morti/contraddetti): {len(rotted)}")
print(f"  di cui rischio PEGGIORATO dopo la nascita (correzione silenziosamente disfatta): {len(drift)}")
print()
print("-- peggiori per recalled_count (i piu' dannosi, surfano di piu') --")
for d,level,sup,miss,rv,stored in sorted(rotted, key=lambda x:x[0].get('recalled_count',0), reverse=True)[:12]:
    print(f"  recalled={d.get('recalled_count',0):>3} | snapshot={stored:<5} live=high | sup={len(sup)} miss={len(miss)} rv={len(rv)} | dom={d.get('domain')}")
    print(f"      {(d.get('content') or '')[:120].replace(chr(10),' ')}")
