"""Regressioni del confinamento CodeRunner: bwrap, env e cleanup processi."""
import os
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, '/home/fio/Euri')
from agent.code_runner import CodeRunner
import config

cr = CodeRunner()
run = lambda code: cr._execute_code(code, threading.Event(), timeout=30)

# 1. L'argv confinato usa mount attesi e ripulisce l'ambiente.
argv = cr._bwrap_base_cmd("/usr/bin/bwrap") + [sys.executable, "-u", "x.py"]
assert "--unshare-all" in argv and "--ro-bind" in argv
assert "--clearenv" in argv
# input read-only, output read-write
i = argv.index(str(cr._input_dir)); assert argv[i-1] == "--ro-bind-try", "input non è read-only!"
print("1) argv bwrap: unshare-all, clearenv, input RO, output RW OK")

# 2. Se il preflight reale passa, prova la barriera kernel. Su host che vietano
# user namespace il CodeRunner deve degradare senza rendere inutilizzabile ogni job.
if cr._usable_bwrap():
    r = run("p = '/' + 'etc' + '/shadow'\nprint(open(p).read())")
    assert not r.success, "shadow LETTO: confinamento fallito"
    assert "No such file" in (r.error or "") or "FileNotFound" in (r.output + (r.error or ""))
    r = run("import os\nfd = os.open('/home/fio/.ssh/id_rsa', os.O_RDONLY)\nprint('LETTO')")
    assert not r.success and "LETTO" not in r.output, "ssh letto via os.open"
    print("2) barriera filesystem bwrap verificata a runtime OK")
else:
    assert cr._wrap_cmd(cr._sandbox_dir / "x.py")[0] == sys.executable
    print("2) bwrap non utilizzabile: fallback diretto verificato OK")

# 3. Binario presente ma preflight negativo: fallback, non errore per ogni script.
cr_unusable = CodeRunner()
with patch("agent.code_runner.shutil.which", return_value="/usr/bin/bwrap"):
    cr_unusable._probe_bwrap = lambda _path: (False, "Operation not permitted")
    assert cr_unusable._wrap_cmd(cr_unusable._sandbox_dir / "x.py")[0] == sys.executable
print("3) bwrap installato ma non usabile: fallback verificato OK")

# 4. L'ambiente del daemon non passa al codice generato.
os.environ["EURI_TEST_SECRET"] = "non_deve_passare"
r = run("import os\nprint(os.getenv('EURI_TEST_SECRET'))")
os.environ.pop("EURI_TEST_SECRET", None)
assert r.success and r.output.strip() == "None", f"env ereditato: {r.output}"
print("4) allowlist ambiente: variabile applicativa non ereditata OK")

# 5. Codice legittimo e data stack continuano a funzionare.
r = run("print(6 * 7)")
assert r.success and "42" in r.output, f"codice legittimo fallito: {r.output} {r.error}"
r = run("import pandas as pd\nprint(int(pd.Series([10,20,30]).sum()))")
assert r.success and "60" in r.output, f"pandas fallito: {r.output} {r.error}"
print("5) codice legittimo e pandas OK")

# 6. kill-switch off → lancio diretto (nessun bwrap)
config.CODE_RUNNER_BWRAP_ENABLED = False
argv2 = cr._wrap_cmd(cr._sandbox_dir / "x.py")
assert argv2[0] == sys.executable, "kill-switch non degrada al lancio diretto"
config.CODE_RUNNER_BWRAP_ENABLED = True
print("6) kill-switch off: lancio diretto OK")

# 7. Timeout e interrupt raccolgono sempre il processo.
r = cr._execute_code("import time\nwhile True: time.sleep(1)", threading.Event(), timeout=1)
assert not r.success and r.error == "timeout"
stop = threading.Event(); stop.set()
r = cr._execute_code("import time\nwhile True: time.sleep(1)", stop, timeout=30)
assert not r.success and r.interrupted
print("7) timeout e interrupt raccolgono il subprocess OK")

# 8. Un processo resistente a SIGTERM riceve il fallback SIGKILL.
stubborn = subprocess.Popen(
    [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
    preexec_fn=os.setsid,
)
time.sleep(0.2)
cr._terminate_process_group(stubborn, grace=0.2)
assert stubborn.poll() is not None
print("8) fallback SIGKILL verificato OK")
print("PASS")
