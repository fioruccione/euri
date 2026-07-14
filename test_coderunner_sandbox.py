"""Punto 2 hardening: confinamento OS del CodeRunner via bwrap. Prova RUNTIME
(non AST): un path da variabile verso /etc/shadow o ~/.ssh deve fallire dentro
il namespace; il codice legittimo (data stack) deve funzionare."""
import sys, threading
sys.path.insert(0, '/home/fio/Euri')
from agent.code_runner import CodeRunner

cr = CodeRunner()
run = lambda code: cr._execute_code(code, threading.Event(), timeout=30)

# 1. bwrap è nell'argv (confinamento attivo)
argv = cr._wrap_cmd(cr._sandbox_dir / "x.py")
assert argv[0].endswith("bwrap"), f"bwrap non nell'argv: {argv[0]}"
assert "--unshare-all" in argv and "--ro-bind" in argv
# input read-only, output read-write
i = argv.index(str(cr._input_dir)); assert argv[i-1] == "--ro-bind-try", "input non è read-only!"
print("1) argv bwrap: unshare-all, input RO, output RW ✓")

# 2. path da VARIABILE verso /etc/shadow (il buco che lo scanner AST non vede) → bloccato a runtime
r = run("p = '/' + 'etc' + '/shadow'\nprint(open(p).read())")
assert not r.success, "shadow LETTO — confinamento fallito!"
assert "No such file" in (r.error or "") or "FileNotFound" in (r.output + (r.error or "")), f"errore inatteso: {r.error}"
print(f"2) open(variabile→/etc/shadow) → bloccato ({r.error[:40] if r.error else r.output[:40]}) ✓")

# 3. os.open diretto verso ~/.ssh → bloccato
r = run("import os\nfd = os.open('/home/fio/.ssh/id_rsa', os.O_RDONLY)\nprint('LETTO')")
assert not r.success and "LETTO" not in r.output, "ssh letto via os.open!"
print("3) os.open(~/.ssh/id_rsa) → bloccato ✓")

# 4. codice legittimo: aritmetica
r = run("print(6 * 7)")
assert r.success and "42" in r.output, f"codice legittimo fallito: {r.output} {r.error}"
print(f"4) codice legittimo (6*7) → {r.output.strip()} ✓")

# 5. data stack: pandas dentro la sandbox
r = run("import pandas as pd\nprint(int(pd.Series([10,20,30]).sum()))")
assert r.success and "60" in r.output, f"pandas fallito: {r.output} {r.error}"
print("5) pandas nella sandbox → 60 ✓")

# 6. kill-switch off → lancio diretto (nessun bwrap)
import config
config.CODE_RUNNER_BWRAP_ENABLED = False
argv2 = cr._wrap_cmd(cr._sandbox_dir / "x.py")
assert argv2[0] == sys.executable, "kill-switch non degrada al lancio diretto"
config.CODE_RUNNER_BWRAP_ENABLED = True
print("6) kill-switch off → lancio diretto ✓")
print("PASS")
