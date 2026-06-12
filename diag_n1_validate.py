#!/usr/bin/env python3
"""
Contro-caso N1 — valida il classify a 4 vie (not_a_correction) di Loop 2g.
Chiama il metodo REALE DreamEngine._llm_classify_correction (Qwen) sui correction
signal esistenti, su due set hand-picked dalla baseline:
  - REAL: correzioni vere → NON devono diventare not_a_correction (contro-caso sacro).
  - PHANTOM: fantasmi chiari → DEVONO diventare not_a_correction (recall).
Read-only. Risultato 12/06: correzioni vere perse 0/11, fantasmi beccati 10/11
(l'unico miss è bf1e535b, domanda-richiamo borderline → lato sicuro/conservativo).
Uso: venv/bin/python diag_n1_validate.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('/home/fio/Euri')))
import config
from core.embedder import Embedder
from core.dream_engine import DreamEngine
from utils.redis_client import get_client

r = get_client(); e = Embedder(); e.load()
engine = DreamEngine(r, e)

REAL = {'a46059a7','9a0c8cf7','5dd41531','cd136321','7c730057','a0126483','8f76dc5b','d8293509','07b3b781','accc7af8','97885dc5'}
PHANTOM = {'2147f387','08387c07','78e5bf9f','9bb6f4d5','bf1e535b','9beb750e','9e33a85e','30192d55','65a8941e','65cac167','b9df4fdb'}

sigs={}
for k in r.scan_iter('euri:correction:*'):
    d=r.json().get(k,'$')
    if d: sigs[d[0]['id'][:8]]=d[0]

def classify(doc):
    ctx=[]
    for mid in doc.get('rag_ctx_ids',[]):
        if not mid: continue
        mk=mid if mid.startswith('euri:memory:') else 'euri:memory:'+mid
        try:
            m=r.json().get(mk,'$')
            if m and m[0].get('content'): ctx.append(m[0]['content'][:200])
        except Exception: pass
    return engine._llm_classify_correction(doc.get('prompt_original',''),doc.get('risposta_euri',''),doc.get('correzione_user',''),ctx)

print('=== CONTRO-CASO: correzioni VERE (NON devono cadere) ===')
real_dropped=0
for sid in sorted(REAL):
    if sid in sigs:
        v=classify(sigs[sid]); bad=(v=='not_a_correction'); real_dropped+=bad
        print('  '+sid+' -> '+v+('  ✗ CADUTA!' if bad else '  ✓ tenuta'))
print('  >>> correzioni vere perse: '+str(real_dropped)+'/'+str(len(REAL))+' (deve essere ~0)')

print()
print('=== RECALL: fantasmi chiari (DEVONO essere scartati) ===')
ph=0
for sid in sorted(PHANTOM):
    if sid in sigs:
        v=classify(sigs[sid]); ok=(v=='not_a_correction'); ph+=ok
        print('  '+sid+' -> '+v+('  ✓ beccato' if ok else '  ✗ mancato'))
print('  >>> fantasmi beccati: '+str(ph)+'/'+str(len(PHANTOM)))
