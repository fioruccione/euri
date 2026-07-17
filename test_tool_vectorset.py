"""
Test isolato del Tool VectorSet (V2.18) — NON tocca chiavi di produzione.

Sandbox:
    test:tools:vset       (VectorSet di test)
    test:tool:{slug}      (JSON metadata di test)

Verifica:
    1. Bootstrap: 6 tool registrati con embedding sulla descrizione+esempi
    2. Match positivo: query semanticamente vicina ai tool → match con score≥0.85
    3. Match negativo: query generica/conversazionale → None (fallback LLM)
    4. Latenza: VSIM KNN puro <10ms (escluso embedding query)
    5. Cleanup: tutte le chiavi test:* rimosse al termine, indipendentemente
       dal risultato dei test (try/finally).

Uso:
    /home/fio/Euri/venv/bin/python test_tool_vectorset.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import redis
from core.embedder import Embedder
from core.tool_registry import ToolRegistry, DEFAULT_TOOL_DEFINITIONS


# Casi di test: (query, expected_fast_path oppure None per fallback LLM).
# Questo test esercita ToolRegistry.match_tool, non il classificatore completo:
# una query con top-1/top-2 troppo vicini deve tornare None anche quando l'intento
# finale atteso dal successivo slow path e' noto.
TEST_CASES = [
    # === MATCH ATTESI (semanticamente vicini alle descrizioni) ===
    ("cerca su google il fatturato di Primpex spa",       "WEB_SEARCH"),
    ("trovami online il prezzo del polipropilene",        "WEB_SEARCH"),
    ("ricordi quando abbiamo parlato di Realube?",        "SEARCH"),
    # Ambigua SEARCH/SAVE_MEMORY nello spazio e5: astensione intenzionale.
    ("hai in memoria informazioni su Fanti Plast?",       None),
    ("ricordami di chiamare Mario domani mattina",        "SAVE_TODO"),
    # "segna" senza scadenza e' ambiguo TODO/MEMORY: decide il slow path.
    ("segna che devo verificare il lotto 03 PPR 043",     None),
    ("ricordati che il grado 50 è migliore del 25",       "SAVE_MEMORY"),
    # Gap sotto 0.005: fail-quiet, non forcing del top-1.
    ("annota che il fornitore consegna il martedì",       None),
    ("controlla quanta ram sto usando ora",               "EXECUTE"),
    ("leggi il log degli errori di sistema",              "EXECUTE"),
    ("l'ho fatto, ho chiamato il cliente",                "COMPLETE"),
    ("ho inviato la mail",                                "COMPLETE"),

    # === NO MATCH (conversazione generica → fallback LLM) ===
    ("come stai oggi Euri?",                              None),
    ("che ne pensi del lavoro che stiamo facendo?",       None),
    ("interessante quello che dici sulla fisica",         None),
    ("buongiorno",                                        None),
]


def main():
    r = redis.Redis(decode_responses=True)
    print(f"Redis ping: {r.ping()}, version {r.info('server')['redis_version']}")
    print(f"VectorSet modulo: {[m for m in r.execute_command('MODULE', 'LIST') if b'vectorset' in (m if isinstance(m, bytes) else str(m).encode()).lower()] or 'verificare'}")
    print()

    # Connessione raw per VSIM (bytes — il vector deve essere binary)
    raw_r = redis.Redis(decode_responses=False)

    print("Caricamento embedder...")
    t0 = time.time()
    emb = Embedder()
    emb.load()
    print(f"  ✓ Embedder pronto in {time.time()-t0:.1f}s\n")

    # Sandbox isolato
    SANDBOX_VSET = "test:tools:vset"
    SANDBOX_PREFIX = "test:tool:"

    # Pulizia preventiva (potrebbe esserci residuo da run fallito)
    r.delete(SANDBOX_VSET)
    for key in r.scan_iter(f"{SANDBOX_PREFIX}*"):
        r.delete(key)

    registry = ToolRegistry(r, emb, vset_key=SANDBOX_VSET, json_prefix=SANDBOX_PREFIX)
    # Registry usa raw_r per VSIM via execute_command? No — ToolRegistry usa self._r.
    # redis-py decode_responses=True dovrebbe gestire i bytes ricevuti.

    try:
        # === BOOTSTRAP ===
        print(f"=== Bootstrap: registro {len(DEFAULT_TOOL_DEFINITIONS)} tool ===")
        t0 = time.time()
        n = registry.bootstrap_from_definitions(DEFAULT_TOOL_DEFINITIONS)
        elapsed = time.time() - t0
        print(f"  ✓ {n}/{len(DEFAULT_TOOL_DEFINITIONS)} registrati in {elapsed:.2f}s")
        if n != len(DEFAULT_TOOL_DEFINITIONS):
            print(f"  ✗ FAIL: bootstrap incompleto")
            return False

        # VCARD per verifica
        try:
            card = r.execute_command("VCARD", SANDBOX_VSET)
            print(f"  ✓ VCARD: {card} elementi nel VectorSet")
        except Exception as e:
            print(f"  ✗ VCARD fallito: {e}")
            return False

        # Verifica VINFO per dimensione vettoriale
        try:
            info = r.execute_command("VINFO", SANDBOX_VSET)
            # Parse alternato: [key1, val1, key2, val2, ...]
            info_dict = {}
            for i in range(0, len(info), 2):
                k = info[i].decode() if isinstance(info[i], bytes) else info[i]
                v = info[i+1]
                if isinstance(v, bytes):
                    v = v.decode()
                info_dict[k] = v
            print(f"  ✓ VINFO: dim={info_dict.get('vector-dim','?')} quant={info_dict.get('quant-type','?')}")
        except Exception as e:
            print(f"  ! VINFO non parsabile: {e}")

        print()

        # === ROUTING TESTS ===
        print(f"=== Routing: {len(TEST_CASES)} query test ===")
        latencies = []
        passed = 0
        failed = []
        for query, expected in TEST_CASES:
            result = registry.match_tool(query)
            if result:
                latencies.append(result["elapsed_ms"])
                got = result["intent"]
                score = result["score"]
            else:
                got = None
                score = None

            ok = (got == expected)
            if ok:
                passed += 1
                if got:
                    print(f"  ✓ [{got:11s}] score={score:.3f}  ⏱{result['elapsed_ms']:.1f}ms  '{query[:55]}'")
                else:
                    print(f"  ✓ [None       ]  (sotto soglia, fallback)         '{query[:55]}'")
            else:
                failed.append((query, expected, got, score))
                got_str = f"[{got:11s}] score={score:.3f}" if got else "[None       ]"
                exp_str = f"[{expected:11s}]" if expected else "[None       ]"
                print(f"  ✗ {got_str}  atteso {exp_str}  '{query[:55]}'")

        print()
        print(f"=== Risultati ===")
        print(f"  Passati:  {passed}/{len(TEST_CASES)}")
        print(f"  Falliti:  {len(failed)}")
        if latencies:
            print(f"  Latenza KNN puro (escluso embedding):")
            print(f"     min={min(latencies):.2f}ms  avg={sum(latencies)/len(latencies):.2f}ms  max={max(latencies):.2f}ms")
            if max(latencies) > 10:
                print(f"     ⚠ Latenza max sopra soglia attesa (10ms)")
            else:
                print(f"     ✓ Latenza max sotto soglia attesa (10ms)")

        if failed:
            print()
            print(f"=== Dettaglio FAILED (utili per calibrare la soglia) ===")
            for q, exp, got, sc in failed:
                # Ri-run con threshold basso per vedere score reale
                low = registry.match_tool(q, threshold=0.0, top_k=3)
                if low:
                    top = low["top_k"][:3]
                    top_str = ", ".join(f"{s}={sc_:.3f}" for s, sc_ in top)
                    print(f"  '{q[:60]}'")
                    print(f"    atteso={exp} got={got}  top3=[{top_str}]")

        return passed == len(TEST_CASES)

    finally:
        # === CLEANUP GARANTITO ===
        print()
        print("=== Cleanup sandbox ===")
        n_json = 0
        for key in r.scan_iter(f"{SANDBOX_PREFIX}*"):
            r.delete(key)
            n_json += 1
        deleted_vset = r.delete(SANDBOX_VSET)
        print(f"  ✓ Eliminati {n_json} JSON test:tool:* + VectorSet test:tools:vset ({deleted_vset})")
        # Verifica residui zero
        residui_json = sum(1 for _ in r.scan_iter(f"{SANDBOX_PREFIX}*"))
        residui_vset = r.exists(SANDBOX_VSET)
        if residui_json or residui_vset:
            print(f"  ✗ Residui rilevati: json={residui_json}, vset={residui_vset}")
        else:
            print(f"  ✓ Sandbox completamente pulita (zero residui)")


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
