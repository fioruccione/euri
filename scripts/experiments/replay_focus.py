"""Replay harness per l'Active Focus (SPEC_ACTIVE_FOCUS.md) — validazione OFFLINE.

Riproduce la storia reale (memorie con embedding/dominio/ts, 23/04→oggi) dentro il
motore focus a regole e genera:
  1. statistiche di dinamica (nascite, churn, vita media, attivi simultanei);
  2. AUDIT_FOCUS_REPLAY_<data>.md — per k giorni campionati, i top-3 focus di quel
     giorno: Stefano segna se riconosce il proprio lavoro reale (GO: ≥80% dei giorni).

Read-only su Redis, nessun daemon, nessuna scrittura. Regole (dalla spec):
  - NASCITA solo da eventi nominabili (source user/teach), mai da passive o sintesi reaction;
  - RINFORZO: stesso dominio + overlap di identificatori o ≥2 keyword (identifier-first,
    MAI cosine-only: anisotropia);
  - DECADIMENTO esponenziale per regola (τ 3 giorni), stati active/cooling/archiviato;
  - CAP 7 attivi (un focus che non compete non è un focus); dedup per rinforzo.
"""
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, date

import numpy as np

sys.path.insert(0, "/home/fio/Euri")

import config
import redis

from core.memory_manager import MemoryManager

# ── parametri (tutti qui, tarabili) ─────────────────────────────────────────
TAU_S = 3 * 86400.0          # decadimento tema_lavoro
A_ON, A_OFF = 0.35, 0.10     # soglie active / archivio
A_BIRTH = 0.45               # attivazione alla nascita
CAP_ACTIVE = 7
BIRTH_SOURCES = {"user", "teach"}
REINFORCE_W = {"user": 0.40, "teach": 0.40, "reaction_raw": 0.35, "conversation": 0.20,
               "episode": 0.20, "passive": 0.15, "obsidian_vault": 0.25}
W_DEFAULT = 0.10             # reflection/loop2e/web/…: interni, rinforzo debole
AUDIT_DAYS = 8
AUDIT_WINDOW_FROM = "2026-06-22"   # finestra recente: giorni che Stefano ricorda
RNG = np.random.default_rng(20260713)

_ID_RE = MemoryManager._COMPOSITE_ID_RE
_kw = MemoryManager._safe_keywords


def sig_of(content: str) -> tuple[set, set]:
    ids = {m.strip().lower() for m in _ID_RE.findall(content or "")
           if any(c.isdigit() for c in m)}
    kws = set(_kw(content or "")[:10])
    return ids, kws


def normalize_focus_event(doc: dict) -> tuple[str, str]:
    """Use external feedback, never Euri's synthesized reaction lesson.

    `source=reaction` stores an LLM lesson plus `reaction_raw`. The former may
    elaborate beyond Stefano's words and therefore cannot seed or reinforce the
    work plan. The raw feedback can reinforce an existing focus but cannot birth
    one, because feedback to an insight is not automatically ongoing work.
    """
    source = str(doc.get("source") or "?")
    if source == "reaction":
        return (str(doc.get("reaction_raw") or "").strip(), "reaction_raw")
    return (str(doc.get("content") or "").strip(), source)


@dataclass
class Focus:
    fid: int
    label: str
    domain: str
    activation: float
    born_ts: float
    last_ts: float
    ids: set
    kws: set
    cause: str
    reinforcements: int = 0
    refs: list = field(default_factory=list)

    def decayed(self, now_ts: float) -> float:
        return self.activation * np.exp(-(now_ts - self.last_ts) / TAU_S)


class FocusEngine:
    def __init__(self):
        self.focuses: list[Focus] = []
        self.archived: list[Focus] = []
        self.births = 0
        self._next = 0

    def _match(self, domain, ids, kws):
        best, best_score = None, 0
        for f in self.focuses:
            if f.domain != domain:
                continue
            score = 3 * len(f.ids & ids) + len(f.kws & kws)
            if (f.ids & ids or len(f.kws & kws) >= 2) and score > best_score:
                best, best_score = f, score
        return best

    def step(self, ts, domain, content, source, ref):
        # decadimento lazy + archivio
        alive = []
        for f in self.focuses:
            if f.decayed(ts) >= A_OFF:
                alive.append(f)
            else:
                self.archived.append(f)
        self.focuses = alive

        ids, kws = sig_of(content)
        target = self._match(domain, ids, kws)
        if target is not None:
            w = REINFORCE_W.get(source, W_DEFAULT)
            target.activation = min(1.0, target.decayed(ts) + w)
            target.last_ts = ts
            target.reinforcements += 1
            # la firma (ids/kws) resta quella del SEME: accrescerla a ogni rinforzo
            # trasforma il focus in un acchiappa-tutto del dominio (visto al 1° replay:
            # "Regrado PP" 324 rinforzi, attivazione ~1.0 per due mesi e mezzo)
            target.refs.append(ref)
            return
        if source in BIRTH_SOURCES and len((content or "").strip()) >= 40 and (ids or len(kws) >= 3):
            self._next += 1
            self.focuses.append(Focus(
                fid=self._next, label=(content or "").strip()[:90],
                domain=domain, activation=A_BIRTH, born_ts=ts, last_ts=ts,
                ids=ids, kws=kws, refs=[ref],
                cause=f"{source} {datetime.fromtimestamp(ts):%d/%m %H:%M}"))
            self.births += 1
            # competizione: cap sugli attivi
            act = sorted((f for f in self.focuses if f.decayed(ts) >= A_ON),
                         key=lambda f: -f.decayed(ts))
            for f in act[CAP_ACTIVE:]:
                f.activation = min(f.activation, A_ON - 0.01)  # retrocesso a cooling

    def top_active(self, ts, k=3):
        act = [(f.decayed(ts), f) for f in self.focuses]
        act = [(a, f) for a, f in act if a >= A_ON]
        return sorted(act, key=lambda x: -x[0])[:k]


def main():
    r = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT,
                    db=config.REDIS_DB, decode_responses=True)
    events, seen = [], set()
    for k in r.scan_iter(match="euri:memory:*"):
        d = r.json().get(k, "$")
        if not d:
            continue
        d = d[0]
        content, source = normalize_focus_event(d)
        if not content or not d.get("created_at") or not d.get("domain"):
            continue
        key_ = content.lower()
        if key_ in seen:
            continue
        seen.add(key_)
        events.append((float(d["created_at"]), d["domain"], content,
                       source, d.get("id", "")[:8]))
    events.sort(key=lambda e: e[0])
    print(f"eventi: {len(events)} ({datetime.fromtimestamp(events[0][0]):%d/%m/%y} → "
          f"{datetime.fromtimestamp(events[-1][0]):%d/%m/%y})")

    eng = FocusEngine()
    day_snapshots = {}   # date -> top3 (a fine giornata)
    max_active, active_series = 0, []
    cur_day = None
    for ts, dom, content, src, ref in events:
        d = date.fromtimestamp(ts)
        if cur_day is not None and d != cur_day:
            day_snapshots[cur_day] = [(a, f.label, f.domain, f.reinforcements, f.cause)
                                      for a, f in eng.top_active(prev_ts)]
            active_series.append(len([1 for f in eng.focuses if f.decayed(prev_ts) >= A_ON]))
        eng.step(ts, dom, content, src, ref)
        cur_day, prev_ts = d, ts
        max_active = max(max_active, len([1 for f in eng.focuses if f.decayed(ts) >= A_ON]))
    day_snapshots[cur_day] = [(a, f.label, f.domain, f.reinforcements, f.cause)
                              for a, f in eng.top_active(prev_ts)]

    all_f = eng.archived + eng.focuses
    lifetimes = [(f.last_ts - f.born_ts) / 86400 for f in all_f]
    print(f"\n=== dinamica ===")
    print(f"focus nati: {eng.births}  |  vivi a fine replay: {len(eng.focuses)}")
    print(f"vita media (nascita→ultimo rinforzo): {np.mean(lifetimes):.1f}g  "
          f"mediana {np.median(lifetimes):.1f}g  |  mai rinforzati: "
          f"{sum(1 for f in all_f if f.reinforcements == 0)}/{len(all_f)}")
    print(f"attivi simultanei: max {max_active}, medio {np.mean(active_series):.1f}")
    print(f"nascite/giorno: {eng.births / max(1, (events[-1][0]-events[0][0])/86400):.1f}")

    # ── audit: campiona giorni recenti con ≥3 eventi ────────────────────────
    from_d = date.fromisoformat(AUDIT_WINDOW_FROM)
    ev_per_day = Counter(date.fromtimestamp(ts) for ts, *_ in events)
    candidates = sorted(d for d in day_snapshots
                        if d >= from_d and ev_per_day.get(d, 0) >= 3 and day_snapshots[d])
    step = max(1, len(candidates) // AUDIT_DAYS)
    sampled = candidates[::step][:AUDIT_DAYS]

    out = f"AUDIT_FOCUS_REPLAY_{date.today():%Y%m%d}.md"
    with open(out, "w") as fh:
        fh.write("# Audit replay Active Focus — riconosci il tuo lavoro?\n\n")
        fh.write("Per ogni giorno: i top-3 focus che il motore aveva ATTIVI a fine giornata.\n")
        fh.write("Segna: `[S]` = sì, era il mio lavoro · `[~]` = in parte · `[N]` = no/fantasma.\n"
                 "GO pre-registrato: ≥80% dei giorni con S o ~ senza fantasmi in cima.\n\n")
        for d in sampled:
            fh.write(f"---\n\n## {d:%A %d/%m}   [ ]S  [ ]~  [ ]N\n\n")
            for a, label, dom, reinf, cause in day_snapshots[d]:
                fh.write(f"- **{a:.2f}** [{dom}] {label}\n"
                         f"  _(nato da {cause}, rinforzato {reinf}×)_\n")
            fh.write("\n")
    print(f"\naudit scritto: {out} ({len(sampled)} giorni campionati)")


if __name__ == "__main__":
    main()
