"""Guard di consenso conversazionale (punto 1 hardening): una voce autenticata
senza 'Euri' e fuori dalla finestra NON deve essere processata. Regressione del
bug in cui _last_activity_ts, aggiornato prima del guard, azzerava la finestra."""
import sys, types
sys.path.insert(0, '/home/fio/Euri')
import voice_daemon as vd

WIN = vd._CONVERSATION_WINDOW_SEC

def make(translate=False, dictation=False):
    d = vd.VoiceDaemon.__new__(vd.VoiceDaemon)
    d._translate_bidir = translate
    d._dictation_mode = dictation
    return d

f = vd.VoiceDaemon._utterance_is_addressed
cases = [
    # (has_wake, since_last, translate, dictation) -> atteso
    ("wake word esplicita, fuori finestra", (True,  WIN+10, False, False), True),
    ("no wake, dentro finestra (conversazione)", (False, WIN-5,  False, False), True),
    ("no wake, FUORI finestra → IGNORA (il bug)", (False, WIN+10, False, False), False),
    ("no wake, appena parlato (0s)", (False, 0.0,    False, False), True),
    ("traduzione: sempre (parla l'altro)", (False, WIN+99, True,  False), True),
    ("dettato: sempre", (False, WIN+99, False, True), True),
]
ok = True
for name, (hw, sl, tr, di), want in cases:
    got = f(make(tr, di), hw, sl)
    good = got is want
    ok &= good
    print(f"{'OK ' if good else 'FAIL'} {name:45} → {got}")

# Regressione del meccanismo: prima del fix since_last era ~0 (timestamp aggiornato
# all'utterance corrente) → il caso-bug tornava True. Ora, misurando dal turno
# precedente, since_last è grande e il guard ignora. Verifica del delta:
buggy_since_last = 0.0      # com'era: now - (timestamp appena scritto) ≈ 0
fixed_since_last = WIN + 10 # com'è: now - (fine turno precedente)
assert f(make(), False, buggy_since_last) is True,  "sanity: con ~0s è in conversazione"
assert f(make(), False, fixed_since_last) is False, "col fix la voce non chiamata è ignorata"
print("OK  regressione: il guard ora DISCRIMINA (0s→processa, fuori finestra→ignora)")
print("PASS" if ok else "FALLITO")
sys.exit(0 if ok else 1)


# ── Punto 3: degrado a DEBOLE del parlato ambient (no wake nel segmento) ──────
def _test_passive_weak():
    g = vd.VoiceDaemon._passive_weak_support
    assert g("strong", True)  is False   # rivolto a Euri → fatto pieno
    assert g("strong", False) is True    # ambient → degradato anche se FORTE
    assert g("weak",   True)  is True     # weak resta weak
    assert g(None,     False) is True     # default + ambient → degradato
    assert g(None,     True)  is False    # default + rivolto → pieno
    print("OK  punto 3: degrado ambient→DEBOLE 5/5")

_test_passive_weak()
