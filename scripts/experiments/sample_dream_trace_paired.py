"""Esporta l'audit cieco del test appaiato Dream Trace Paired V2.

Fonte unica: euri:dream_trace:paired:cycles, scritto al momento della generazione
con output e due memorie sorgente complete per OGNI lato di OGNI coppia. Redis e'
letto soltanto. L'export fallisce se integrita' o numerosita' non sono verificabili.

Una coppia (pair_id) e' ammissibile solo con ENTRAMBI i lati presenti UNA VOLTA
SOLA, validi (hash coerenti su memorie e output, per QUALUNQUE stato — non solo
candidate) e non in stato "error". Un lato duplicato (stesso pair_id+arm visto due
volte) invalida l'intera coppia: fail-closed, non si tiene silenziosamente la prima
occorrenza. Uno scarto (`status == "discarded"`) resta ammissibile: conta come
non-passa per quel lato per costruzione (vedi ESPERIMENTO_DREAM_TRACE_V2.md,
"Scarto come esito, non come esclusione") — solo i lati "candidate" producono
testo per il file cieco, ma la coppia entra comunque nel conteggio di numerosita'
e nella chiave.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, "/home/fio/Euri")

import redis

import config
from core.dream_engine import DREAM_TRACE_PAIRED_STREAM


N_PAIRS_DEFAULT = 50
RANDOM_SEED = 20260722
OUTPUT_DIR = Path("audit_output")
# Il batch corretto usa experiment_version=dream_trace_paired_v2 e chiavi di stato
# versionate. La v1 resta nello stesso stream come prova diagnostica ma viene esclusa
# per versione: non dipendiamo da un timestamp inserito manualmente dopo il riavvio.


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_side(fields: dict) -> bool:
    """Integrita' del lato: hash/lunghezza di memorie e output devono coincidere
    per QUALUNQUE stato (candidate o discarded), non solo per candidate — altrimenti
    uno scarto con output corrotto/troncato passerebbe inosservato."""
    if fields.get("status") == "error":
        return False
    if fields.get("status") not in {"candidate", "discarded"}:
        return False
    if fields.get("record_complete") != "1":
        return False
    if fields.get("trace_available") != "1":
        return False
    residue = fields.get("trace_residue")
    if residue is None or fields.get("trace_residue_sha256") != _sha(residue):
        return False
    source_a = fields.get("memory_a_content") or ""
    source_b = fields.get("memory_b_content") or ""
    if not source_a or not source_b:
        return False
    if fields.get("memory_a_sha256") != _sha(source_a):
        return False
    if fields.get("memory_b_sha256") != _sha(source_b):
        return False
    output = fields.get("model_output") or ""
    if fields.get("status") == "candidate" and not output:
        return False
    try:
        expected_chars = int(fields.get("model_output_chars") or "-1")
    except ValueError:
        return False
    if expected_chars != len(output):
        return False
    if fields.get("model_output_sha256") != _sha(output):
        return False
    try:
        if float(fields.get("duration_s") or "-1") < 0:
            return False
    except ValueError:
        return False
    return True


def _error_imbalance(error_counts: dict[str, int]) -> tuple[bool, str]:
    """Valuta anche i casi 0:N, che la vecchia guardia `if b > 0 and t > 0`
    nascondeva proprio quando lo squilibrio era massimo."""
    baseline = int(error_counts.get("baseline") or 0)
    treatment = int(error_counts.get("trattamento") or 0)
    if baseline == treatment == 0:
        return False, "nessun errore"
    if baseline == 0 or treatment == 0:
        return True, f"baseline={baseline}, trattamento={treatment} (rapporto infinito)"
    ratio = treatment / baseline
    return (
        ratio >= 2 or ratio <= 0.5,
        f"baseline={baseline}, trattamento={treatment}, rapporto={ratio:.2f}",
    )


def _collect_pairs(entries, expected_version: str, *, valid_since_ts: float | None = None):
    """Raggruppa le entry dello stream per pair_id, applicando integrita' e
    fail-closed sui duplicati. Estratta da main() per essere testabile senza Redis
    reale: entries e' l'iterabile (entry_id, fields) di r.xrange().

    valid_since_ts (opzionale): ulteriore cutoff diagnostico. Il batch corrente non
    ne dipende: v1/v2 sono separati da experiment_version. None = nessun cutoff."""
    sides_by_pair: dict[str, dict[str, dict]] = defaultdict(dict)
    duplicate_pairs: set[str] = set()
    invalid_sides = 0
    excluded_pre_fix = 0
    error_counts = {"baseline": 0, "trattamento": 0}

    for entry_id, fields in entries:
        if fields.get("experiment_version") != expected_version:
            continue
        if valid_since_ts is not None:
            try:
                entry_ts = float(fields.get("ts") or "0")
            except ValueError:
                entry_ts = 0.0
            if entry_ts < valid_since_ts:
                excluded_pre_fix += 1
                continue
        pair_id = fields.get("pair_id") or ""
        arm = fields.get("arm") or ""
        if not pair_id or arm not in ("baseline", "trattamento"):
            invalid_sides += 1
            continue
        if fields.get("status") == "error":
            error_counts[arm] += 1
        if not _valid_side(fields):
            invalid_sides += 1
            continue
        if arm in sides_by_pair[pair_id]:
            # Stesso pair_id+arm visto due volte: la coppia intera non e' piu'
            # affidabile (non sappiamo quale delle due occorrenze sia quella
            # "vera"). Fail-closed: si esclude, non si sceglie in silenzio.
            duplicate_pairs.add(pair_id)
            continue
        sides_by_pair[pair_id][arm] = {"entry_id": entry_id, **fields}

    complete_pairs = {
        pid: sides for pid, sides in sides_by_pair.items()
        if pid not in duplicate_pairs and "baseline" in sides and "trattamento" in sides
    }
    incomplete = len(sides_by_pair) - len(complete_pairs) - len(duplicate_pairs)
    return complete_pairs, duplicate_pairs, invalid_sides, error_counts, incomplete, excluded_pre_fix


def main() -> None:
    n_pairs = int(sys.argv[1]) if len(sys.argv) > 1 else N_PAIRS_DEFAULT
    r = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
    )
    expected_version = getattr(
        config, "DREAM_TRACE_PAIRED_VERSION", "dream_trace_paired_v2"
    )

    complete_pairs, duplicate_pairs, invalid_sides, error_counts, incomplete, excluded_pre_fix = (
        _collect_pairs(
            r.xrange(DREAM_TRACE_PAIRED_STREAM, "-", "+"), expected_version,
        )
    )

    print(
        f"coppie complete valide: {len(complete_pairs)}; "
        f"coppie incomplete: {incomplete}; "
        f"coppie escluse per duplicato: {len(duplicate_pairs)}; "
        f"lati esclusi da eventuale cutoff opzionale: {excluded_pre_fix}; "
        f"lati non validi: {invalid_sides}; "
        f"errori di generazione per braccio: baseline={error_counts['baseline']}, "
        f"trattamento={error_counts['trattamento']}"
    )
    imbalanced, imbalance_detail = _error_imbalance(error_counts)
    if imbalanced:
        print(
            "ATTENZIONE: tasso di errore molto diverso tra i due bracci "
            f"({imbalance_detail}) — puo' essere un effetto reale del residuo "
            "(es. prompt che manda in timeout il modello), non solo rumore. "
            "Da indagare prima di continuare."
        )

    if len(complete_pairs) < n_pairs:
        raise RuntimeError(
            f"Audit paired non pronto: {len(complete_pairs)}/{n_pairs} coppie complete."
        )

    rng = random.Random(RANDOM_SEED)
    chosen_pair_ids = rng.sample(sorted(complete_pairs.keys()), n_pairs)

    # Ogni lato "candidate" diventa un item cieco indipendente, mescolato con tutti
    # gli altri (nessuna informazione su braccio o pairing nel file cieco). I lati
    # "discarded" non producono testo da giudicare: contano come non-passa per quel
    # lato, registrato solo nella chiave.
    blind_items = []
    key_pairs = {}
    for pair_id in chosen_pair_ids:
        sides = complete_pairs[pair_id]
        key_pairs[pair_id] = {}
        for arm in ("baseline", "trattamento"):
            side = sides[arm]
            key_pairs[pair_id][arm] = {
                "entry_id": side["entry_id"],
                "status": side["status"],
                "model_output_sha256": side.get("model_output_sha256", ""),
                "duration_s": side.get("duration_s", ""),
            }
            if side["status"] == "candidate":
                blind_items.append({
                    "pair_id": pair_id,
                    "arm": arm,
                    "content": side["model_output"],
                    "domain_a": side.get("domain_a") or "",
                    "domain_b": side.get("domain_b") or "",
                    "memory_a_content": side["memory_a_content"],
                    "memory_b_content": side["memory_b_content"],
                })

    rng.shuffle(blind_items)

    tag = date.today().strftime("%Y%m%d")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_PAIRED_items_{tag}.md"
    key_path = OUTPUT_DIR / f"AUDIT_DREAM_TRACE_PAIRED_key_{tag}.json"

    with open(items_path, "x", encoding="utf-8") as fh:
        fh.write("# Audit cieco Dream Trace Paired V2\n\n")
        fh.write(
            "Due valutatori indipendenti compilano questa fase senza aprire la "
            "chiave e senza sapere che gli item sono in coppia.\n\n"
        )
        fh.write("- `G2` = fondato; `G1` = ipotesi incompleta; `G0` = inventato/contraddetto.\n")
        fh.write("- `V2` = non ovvio; `V1` = incrementale; `V0` = ovvio/analogia decorativa.\n")
        fh.write("- `C` = chiaro; `A` = ambiguo/non giudicabile.\n")
        fh.write(
            "- Pass: G2+V2+C. Un disaccordo tra i due valutatori su una qualunque "
            "dimensione rende quel lato AMBIGUO (non passa) — nessuna adjudicazione.\n"
        )
        fh.write(
            "- I lati scartati (non in questo file) contano come non-passa per "
            "costruzione.\n\n"
        )
        for index, item in enumerate(blind_items, 1):
            fh.write(
                f"---\n\n**#{index}**  "
                "Grounding [ ]G2 [ ]G1 [ ]G0  "
                "Novità [ ]V2 [ ]V1 [ ]V0  Chiarezza [ ]C [ ]A\n\n"
                f"**Memoria A — {item['domain_a']}**\n\n{item['memory_a_content']}\n\n"
                f"**Memoria B — {item['domain_b']}**\n\n{item['memory_b_content']}\n\n"
                f"**Candidate**\n\n{item['content']}\n\n"
            )

    with open(key_path, "x", encoding="utf-8") as fh:
        json.dump({
            "diagnostics": {
                "coppie_complete": len(complete_pairs),
                "coppie_incomplete": incomplete,
                "coppie_escluse_duplicato": len(duplicate_pairs),
                "lati_esclusi_cutoff_opzionale": excluded_pre_fix,
                "lati_non_validi": invalid_sides,
                "errori_per_braccio": error_counts,
            },
            "pairs": key_pairs,
            "blind_item_order": [
                {"index": i, "pair_id": item["pair_id"], "arm": item["arm"]}
                for i, item in enumerate(blind_items, 1)
            ],
        }, fh, indent=2, ensure_ascii=False)

    print(
        f"scritti: {items_path} ({len(blind_items)} lati candidate su "
        f"{n_pairs} coppie) + {key_path} (chiave)"
    )


if __name__ == "__main__":
    main()
