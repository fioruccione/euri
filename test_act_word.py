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
from core.act_word_check import (
    emit_unbacked_action_commitment,
    needs_honest_correction,
    scrub_unbacked_action_claim,
    honest_correction,
    honest_commitment_correction,
)

# (descrizione, reply, turn_actions, atteso_flag)
CASES = [
    # --- DEVE scattare: claim d'azione + NESSUNA azione nel turno ---
    ("17:18 confabulazione reale", "Ho aggiornato la nota: ho aggiunto questo tuo commento sulla radice umana.", set(), True),
    ("claim save senza azione", "Certo Stefano, ho salvato il promemoria.", set(), True),
    ("claim memorizza senza azione", "Fatto, ho memorizzato tutto quello che mi hai detto.", set(), True),
    ("claim elimina senza azione", "L'ho eliminato come mi avevi chiesto.", set(), True),
    ("claim a prefisso", "Salvato: la lezione sul settore plastiche.", set(), True),
    ("claim creato senza azione", "Ho creato il documento e te l'ho messo nella cartella.", set(), True),
    ("claim generato reale 05/08", "Ho generato il documento Word con l'intestazione aziendale.", set(), True),
    ("claim prodotto senza azione", "Ho prodotto ed esportato il nuovo PDF.", set(), True),
    ("promessa preparando reale 05/08", "Sto preparando il documento nella cartella scambio dati.", set(), True),
    ("promessa live studio codice", "Vado a dare un'occhiata al codice, specialmente alla gestione dei cicli.", set(), True),
    ("promessa immediata controllo", "Ora controllo il codice e ti dico.", set(), True),
    ("claim agenda presente live", "Ricevuto. Lo tolgo dai sospesi.", set(), True),
    ("claim sospensione presente live", "Lascio il test in sospeso.", set(), True),

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
    ("offerta condizionata", "Se vuoi, provo a controllare il codice.", set(), False),

    # --- NON deve scattare: claim VERO su azione PASSATA-distante (turno vuoto) ---
    ("azione passata: ieri", "Sì, l'ho salvata ieri quella nota.", set(), False),
    ("azione passata: settimana scorsa", "Te l'ho memorizzato la settimana scorsa.", set(), False),
    ("azione passata: già/prima", "Sì, l'ho già salvata prima.", set(), False),
    ("azione passata: stamattina", "Quella nota l'ho creata stamattina.", set(), False),
    # --- MA un claim su azione di QUESTO turno deve ancora scattare ("appena") ---
    ("claim appena, nessuna azione", "Ho appena salvato la nota.", set(), True),
    # --- e una negazione non scatta ---
    ("negazione", "Ok, non ho salvato niente.", set(), False),
]


_TAIL = honest_correction()
_COMMITMENT_TAIL = honest_commitment_correction()

# scrub_unbacked_action_claim: (descrizione, reply, turn_actions, predicato sull'output)
SCRUB_CASES = [
    # claim falso isolato → resta solo la coda onesta
    ("claim solo → coda onesta",
     "Ho aggiornato la nota.", set(),
     lambda out: out == _TAIL),
    # frase vera + claim falso → tiene la vera, droppa il claim, aggiunge coda
    ("tiene la frase vera, droppa il claim",
     "Capisco il punto sulla reologia. Ho salvato tutto.", set(),
     lambda out: "reologia" in out and "salvato" not in out.lower() and out.endswith(_TAIL)),
    # azione reale nel turno → invariato (il claim è coperto)
    ("turno con azione → invariato",
     "Ho salvato il promemoria.", {"save_todo"},
     lambda out: out == "Ho salvato il promemoria."),
    # nessun claim → invariato
    ("nessun claim → invariato",
     "Posso salvarlo quando vuoi.", set(),
     lambda out: out == "Posso salvarlo quando vuoi."),
    # passato-distante → racconto vero, invariato
    ("passato-distante → invariato",
     "Sì, l'ho salvata ieri quella nota.", set(),
     lambda out: out == "Sì, l'ho salvata ieri quella nota."),
    ("promessa background → coda onesta",
     "Capisco il punto. Vado a dare un'occhiata al codice.", set(),
     lambda out: "Capisco il punto" in out and "dare un'occhiata" not in out
     and out.endswith(_COMMITMENT_TAIL)),
    ("claim agenda presente → coda onesta",
     "Ricevuto. Lo tolgo dai sospesi.", set(),
     lambda out: "tolgo" not in out.lower() and out.endswith(_COMMITMENT_TAIL)),
    ("claim generato → coda onesta",
     "Ho generato il documento Word. Lo trovi in scambio_dati.", set(),
     lambda out: "ho generato" not in out.lower() and "scambio_dati" not in out
     and out == _TAIL),
    ("sto preparando → niente background finto",
     "Sto preparando il documento.", set(),
     lambda out: out == _COMMITMENT_TAIL),
]


class _FakeRedis:
    def __init__(self):
        self.calls = []

    def xadd(self, *args, **kwargs):
        self.calls.append((args, kwargs))


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

    print("\n  --- scrub_unbacked_action_claim ---")
    for desc, reply, actions, pred in SCRUB_CASES:
        out = scrub_unbacked_action_claim(reply, actions)
        passed = pred(out)
        ok += passed
        mark = "✓" if passed else "✗ FAIL"
        if not passed:
            fails.append(desc)
        print(f"  {mark}  {desc}  →  {out!r}")

    print("\n  --- emit_unbacked_action_commitment ---")
    fake = _FakeRedis()
    emitted = emit_unbacked_action_commitment(
        fake, "Ho salvato il promemoria.", set(), channel="test"
    )
    passed = emitted and len(fake.calls) == 1
    ok += passed
    if not passed:
        fails.append("emit commitment")
    print(f"  {'✓' if passed else '✗ FAIL'}  claim non coperto → Pulse")

    fake2 = _FakeRedis()
    emitted2 = emit_unbacked_action_commitment(
        fake2, "Ho salvato il promemoria.", {"save_todo"}, channel="test"
    )
    passed = not emitted2 and len(fake2.calls) == 0
    ok += passed
    if not passed:
        fails.append("no emit su azione coperta")
    print(f"  {'✓' if passed else '✗ FAIL'}  claim coperto da azione reale → niente Pulse")

    total = len(CASES) + len(SCRUB_CASES) + 2
    print("\n" + "=" * 60)
    print(f"PASS: {ok}/{total}")
    if fails:
        print("FALLITI:", ", ".join(fails))
    else:
        print("Tutti i casi passano — claim veri beccati, offerte/descrizioni no.")
    print("=" * 60)
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
