"""
diag_convergence_claim.py — BANCO READ-ONLY per "convergenza a livello di claim".
Non tocca il Dream Engine. Nessuna scrittura.

Premessa da verificare PRIMA di progettare la macchina:
  Q1. l'embedding del SOLO claim (la connessione operativa, spogliata del template
      "Nel dominio X succede...") discrimina dove il testo intero falliva?
      → misura la geometria della nuvola claim-only (μ, σ, dove cade la soglia).
  Q2. il GIUDICE LLM (Qwen, già esistente per la zona grigia) boccia le coppie-brodo
      che il coseno assoluto contava come convergenza?
      → spot-check su coppie soup (cos>0.85, domini diversi) vs genuine (cos alto).

Esito → decide il meccanismo: claim-embedding+soglia relativa (se Q1 sì) oppure
        giudice-LLM come discriminatore primario (se Q1 no ma Q2 sì).
"""
import json, re
import numpy as np
import redis
import config
from core.embedder import Embedder
from core.ollama_client import dream_client

CONV_COS = 0.85
REL_DELTA = 0.07
CLAIM_MARKERS = [
    "la connessione operativa non ovvia è:",
    "la connessione operativa non ovvia e:",
    "la connessione operativa è:",
]


def extract_claim(content: str) -> str:
    low = content.lower()
    for m in CLAIM_MARKERS:
        idx = low.find(m)
        if idx >= 0:
            return content[idx + len(m):].strip()
    # fallback: ultima riga non vuota
    lines = [l for l in content.splitlines() if l.strip()]
    return lines[-1].strip() if lines else content.strip()


def judge_same(a: str, b: str):
    """Replica FEDELE di _llm_judge_same_insight (Qwen, think=True) — parsing incluso
    (<channel|> split + strip <think>), come dream_engine.py:633-638.
    Ritorna (verdetto_bool, raw_snippet) per diagnosi."""
    prompt = (f'Analizza questi due insight generati da processi di ragionamento indipendenti.\n\n'
              f'Insight A: "{a}"\n\nInsight B: "{b}"\n\n'
              f'Esprimono lo stesso principio strutturale o la stessa analogia profonda, '
              f'anche se formulati con parole diverse?\n\nRispondi SOLO con SÌ o NO.')
    resp = dream_client.chat(model=config.DREAM_OLLAMA_MODEL,
                             messages=[{"role": "user", "content": prompt}],
                             options={"temperature": 0, "num_predict": 1500}, think=True)
    raw = resp.message.content or ""
    text = raw
    if "<channel|>" in text:
        text = text.split("<channel|>", 1)[-1]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    verdict = text.strip().upper().startswith(("SÌ", "SI", "YES"))
    snippet = re.sub(r"\s+", " ", text)[:80] or re.sub(r"\s+", " ", raw)[:80]
    return verdict, snippet


def main():
    emb = Embedder(); emb.load()
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    ids, doms, claims, full, EF = [], [], [], [], []
    for k in r.scan_iter(match="euri:insight:*"):
        e = r.execute_command("JSON.GET", k, "$.embedding")
        st = json.loads(r.execute_command("JSON.GET", k, "$.status") or "[null]")[0]
        c = json.loads(r.execute_command("JSON.GET", k, "$.content") or "[null]")[0]
        if not e or st != "promoted" or not c:
            continue
        da = json.loads(r.execute_command("JSON.GET", k, "$.domain_a") or "[null]")[0]
        db = json.loads(r.execute_command("JSON.GET", k, "$.domain_b") or "[null]")[0]
        ids.append(k.split(":")[-1][:8]); doms.append((da, db))
        full.append(c); claims.append(extract_claim(c))
        EF.append(np.asarray(json.loads(e)[0], dtype=np.float32))
    n = len(ids)
    EF = np.vstack(EF); EF /= np.linalg.norm(EF, axis=1, keepdims=True)
    # embedding del solo claim
    EC = np.vstack([emb.encode(cl, mode="passage") for cl in claims])
    EC /= np.linalg.norm(EC, axis=1, keepdims=True)

    def geom(M, label):
        S = M @ M.T; off = S[~np.eye(n, dtype=bool)]
        mu, sd = float(off.mean()), float(off.std())
        pct = float((off < CONV_COS).mean() * 100)
        print(f"  [{label}] μ={mu:.4f} σ={sd:.4f} | soglia {CONV_COS} al percentile {pct:.1f} "
              f"| μ+{REL_DELTA}={mu+REL_DELTA:.4f}")
        return S, mu

    print(f"Pool: {n} insight promossi\n")
    print("=== Q1 — geometria: testo intero vs solo-claim ===")
    SF, muF = geom(EF, "testo intero")
    SC, muC = geom(EC, "solo claim ")
    print("  → se μ(claim) è nettamente più basso e la soglia sale sopra il ~75° pct, "
          "il claim-embedding discrimina.")

    # scegli coppie soup (cos-full alto, domini diversi) e genuine (cos-full altissimo)
    pairs_soup, pairs_gen = [], []
    for i in range(n):
        for j in range(i + 1, n):
            cf = float(SF[i, j])
            diff_dom = doms[i][0] != doms[j][0] and doms[i][1] != doms[j][1] \
                       and doms[i][0] != doms[j][1] and doms[i][1] != doms[j][0]
            if 0.85 <= cf <= 0.875 and diff_dom:
                pairs_soup.append((i, j, cf))
            if cf >= 0.91:
                pairs_gen.append((i, j, cf))
    pairs_soup.sort(key=lambda p: p[2]); pairs_gen.sort(key=lambda p: -p[2])
    pairs_soup = pairs_soup[:6]; pairs_gen = pairs_gen[:4]

    print("\n=== Q2 — giudice LLM (Qwen) su coppie che il coseno-testo chiamava convergenti ===")
    print("SOUP (cos>0.85, domini diversi → il giudice DOVREBBE dire NO):")
    for i, j, cf in pairs_soup:
        v, snip = judge_same(full[i], full[j])
        cc = float(EC[i] @ EC[j])
        print(f"  {ids[i]}[{doms[i][0][:14]}] ~ {ids[j]}[{doms[j][0][:14]}] "
              f"cos_full={cf:.3f} cos_claim={cc:.3f} → {'SÌ' if v else 'NO'}  raw='{snip}'")
    print("GENUINE (cos≥0.91 → il giudice DOVREBBE dire SÌ):")
    for i, j, cf in pairs_gen:
        v, snip = judge_same(full[i], full[j])
        cc = float(EC[i] @ EC[j])
        print(f"  {ids[i]}[{doms[i][0][:14]}] ~ {ids[j]}[{doms[j][0][:14]}] "
              f"cos_full={cf:.3f} cos_claim={cc:.3f} → {'SÌ' if v else 'NO'}  raw='{snip}'")


if __name__ == "__main__":
    main()
