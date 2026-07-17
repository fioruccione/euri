#!/usr/bin/env python3
"""
thought_map_pulse.py — ponte Riorganizzatore → Initiative Engine (branch feat/thought-map-initiative).

Legge la MAPPA_DEL_PENSIERO (da thought_maps/, FUORI dal Vault sorvegliato), estrae le singole
tensioni dalle sezioni '### Tensioni', e per ognuna emette UN evento su euri:pulse:
    sense="thought_map", kind="tension", payload={subject, description, ids, domain, has_stefano_claim}

Il resto lo fa l'Initiative controller (build_candidate → generate_question → domanda a Stefano).

INVARIANTI (load-bearing):
- READ-ONLY sulla memoria; scrive SOLO sul bus euri:pulse + un set di dedup. Nessuna mutazione,
  nessuna scrittura nel Vault (observer-effect, incidente 01/07).
- La mappa/Euri NON corregge: emette un MOTIVO per CHIEDERE. Consolida la parola di Stefano.
- Dedup: una tensione già emessa (fingerprint = dominio+ids) non ri-spamma ai giri successivi.
- Anti-nag naturale a valle: build_candidate scarta la tensione se <2 nodi in conflitto sono
  ancora vivi (se la risposta di Stefano ne ha superseduto uno, sparisce).
"""
import argparse, hashlib, re, sys
from pathlib import Path

import redis
import config
from core.pulse import pulse_emit
from build_thought_map import parse_sections, MAPS_DIR

R = redis.Redis(host=config.REDIS_HOST, port=getattr(config, "REDIS_PORT", 6379), decode_responses=True)
EMITTED_SET = "euri:thought_map:emitted"
PRIMARY = {"user", "teach", "obsidian_vault"}
ID_RE = re.compile(r"\[([0-9a-fA-F]{8})\]")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def extract_tensions(md_text):
    """[(domain, subject, description, [ids])] dalle sezioni ### Tensioni."""
    out = []
    for dom, body in parse_sections(md_text):
        # isola il blocco ### Tensioni fino al prossimo header ###
        m = re.search(r"###\s*Tensioni\s*(.+?)(?=\n###|\Z)", body, re.S | re.I)
        if not m:
            continue
        block = m.group(1)
        # bullet = riga che inizia con '*' o '-' (con eventuali continuazioni indentate)
        bullets, cur = [], None
        for line in block.splitlines():
            s = line.strip()
            if s.startswith(("* ", "*\t", "- ", "*   ")) or re.match(r"^[*\-]\s", s):
                if cur:
                    bullets.append(cur)
                cur = s.lstrip("*- \t")
            elif cur is not None and s:
                cur += " " + s
        if cur:
            bullets.append(cur)
        for b in bullets:
            ids = [i.lower() for i in ID_RE.findall(b)]
            if len(ids) < 2:
                continue  # una tensione ha per definizione ≥2 note in conflitto
            subj = BOLD_RE.search(b)
            subject = (subj.group(1) if subj else dom).strip(" :*")
            desc = BOLD_RE.sub(lambda x: x.group(1), b)  # togli i ** per leggibilità
            desc = ID_RE.sub("", desc).replace("()", "").strip(" .,()")
            out.append((dom, subject, desc[:400], sorted(set(ids))))
    return out


def build_prefix_map():
    """La mappa cita id a 8 cifre; le chiavi Redis sono UUID interi. Indice 8hex→id-intero."""
    pm = {}
    for k in R.scan_iter("euri:memory:*", count=1000):
        full = k.split("euri:memory:", 1)[-1]
        pm[full[:8].lower()] = full
    return pm


def node_source(full_id):
    try:
        d = R.json().get(f"euri:memory:{full_id}")
    except Exception:
        return None
    if isinstance(d, list):
        d = d[0] if d else None
    return d.get("source") if isinstance(d, dict) else None


def fingerprint(domain, ids):
    return hashlib.sha1(f"{domain}|{'|'.join(sorted(ids))}".encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=str(MAPS_DIR / "MAPPA_DEL_PENSIERO.md"))
    ap.add_argument("--dry-run", action="store_true", help="mostra cosa emetterebbe, senza emettere")
    ap.add_argument("--reset-dedup", action="store_true", help="svuota il set dedup (ri-emette tutto)")
    ap.add_argument("--include-derived", action="store_true",
                    help="emetti anche le tensioni derivato-vs-derivato (default: solo vs una parola di Stefano)")
    args = ap.parse_args()

    if args.reset_dedup:
        R.delete(EMITTED_SET)
        print("dedup azzerato", file=sys.stderr)

    p = Path(args.map)
    if not p.exists():
        print(f"mappa non trovata: {p} (genera prima con build_thought_map.py)", file=sys.stderr)
        return
    tensions = extract_tensions(p.read_text(encoding="utf-8"))
    pmap = build_prefix_map()
    print(f"tensioni trovate: {len(tensions)} | nodi indicizzati: {len(pmap)}", file=sys.stderr)

    emitted = skipped_dup = skipped_lowsig = skipped_gone = 0
    for dom, subject, desc, ids in tensions:
        full = [pmap[i] for i in ids if i in pmap]     # 8hex → UUID intero
        if len(full) < 2:
            skipped_gone += 1
            continue
        primary = any(node_source(f) in PRIMARY for f in full)
        if not primary and not args.include_derived:
            skipped_lowsig += 1                          # v1: solo tensioni vs parola di Stefano
            continue
        fp = fingerprint(dom, full)
        if not args.reset_dedup and R.sismember(EMITTED_SET, fp):
            skipped_dup += 1
            continue
        payload = {"subject": subject, "description": desc, "ids": full,
                   "domain": dom, "has_stefano_claim": primary}
        sal = 0.7 if primary else 0.5
        if args.dry_run:
            print(f"  [{'S' if primary else ' '}] «{subject}» ({len(full)} nodi) :: {desc[:80]}")
        else:
            pulse_emit(R, sense="thought_map", source="reorganizer", kind="tension",
                       payload=payload, salience=sal)
            R.sadd(EMITTED_SET, fp)
        emitted += 1
    print(f"{'(dry-run) ' if args.dry_run else ''}emesse {emitted} | saltate: dup={skipped_dup} "
          f"basso-segnale={skipped_lowsig} risolte/assenti={skipped_gone}", file=sys.stderr)


if __name__ == "__main__":
    main()
