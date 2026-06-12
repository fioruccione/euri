#!/usr/bin/env python3
"""
Baseline/test N2 — accuratezza del check atto-parola (funzione pura, deterministica).
Casi organici (timestamp reali 11/06) + sintetici, inclusa la zona-trappola delle
offerte/condizionali/descrizioni che NON devono scattare.
Uso: venv/bin/python test_act_word.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from core.act_word_check import needs_honest_correction

# (descrizione, reply, turn_actions, atteso_flag)
CASES = [
    # --- DEVE scattare: claim d'azione + NESSUNA azione nel turno ---
    ("17:18 confabulazione reale", "Ho aggiornato la nota: ho aggiunto questo tuo commento sulla radice umana.", set(), True),
    ("claim save senza azione", "Certo Stefano, ho salvato il promemoria.", set(), True),
    ("claim memorizza senza azione", "Fatto, ho memorizzato tutto quello che mi hai detto.", set(), True),
    ("claim elimina senza azione", "L'ho eliminato come mi avevi chiesto.", set(), True),
    ("claim a prefisso", "Salvato: la lezione sul settore plastiche.", set(), True),
    ("claim creato senza azione", "Ho creato il documento e te l'ho messo nella cartella.", set(), True),

    # --- NON deve scattare: claim + azione realmente eseguita (binario lascia passare) ---
    ("17:43 reale: ho aggiornato ma ha creato (azione c'è)", "Ho aggiornato la memoria: Lezione sul settore plastiche.", {"save"}, False),
    ("claim save con save", "Ho salvato il fatto in memoria.", {"save"}, False),
    ("claim todo con todo", "Segnato: 'chiamare Mario'. Vuoi una scadenza?", {"save_todo"}, False),

    # --- NON deve scattare: nessun claim d'azione ---
    ("saluto", "Buongiorno Stefano, di cosa parliamo adesso?", set(), False),
    ("risposta tecnica", "Il PP a grado 1,2 è troppo viscoso per un pezzo da 35 kg.", set(), False),

    # --- NON deve scattare: offerte / condizionali / descrizioni (zona-trappola) ---
    ("offerta", "Vuoi che lo salvi in memoria?", set(), False),
    ("condizionale", "Se vuoi lo memorizzo, dimmi pure.", set(), False),
    ("descrizione abitudine", "Di solito salvo le cose importanti che mi dici.", set(), False),
    ("possibilità", "Posso salvarlo per te quando vuoi.", set(), False),
    ("istruzione all'utente", "Dimmi 'memorizza questo' e lo salvo subito.", set(), False),
    ("futuro/intenzione", "Lo memorizzerò appena confermi.", set(), False),
]


def main():
    ok = 0
    fails = []
    for desc, reply, actions, expected in CASES:
        got = needs_honest_correction(reply, actions)
        passed = got == expected
        ok += passed
        mark = "✓" if passed else "✗ FAIL"
        if not passed:
            fails.append(desc)
        print(f"  {mark}  atteso={str(expected):5} ottenuto={str(got):5} | {desc}")
    print("\n" + "=" * 60)
    print(f"PASS: {ok}/{len(CASES)}")
    if fails:
        print("FALLITI:", ", ".join(fails))
    else:
        print("Tutti i casi passano — claim veri beccati, offerte/descrizioni no.")
    print("=" * 60)
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
