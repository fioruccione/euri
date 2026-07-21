"""Regressione: Ctrl+C arresta Euri/UI ma non il monitor hardware indipendente."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


_FAKE_PROCESS = """#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

if Path(sys.argv[0]).name == "streamlit":
    role = "streamlit"
else:
    role = Path(sys.argv[1]).stem

Path(f"{role}.pid").write_text(str(os.getpid()))

def stop(_signum, _frame):
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(0.1)
"""


def _wait_pid(path: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"PID non pubblicato: {path.name}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return state != "Z"
    except ProcessLookupError:
        return False
    except FileNotFoundError:
        return False


def test_monitor_survives_launcher_interrupt():
    if shutil.which("setsid") is None:
        raise AssertionError("setsid richiesto da start_euri.sh")

    monitor_pid = None
    launcher = None
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        shutil.copy2(ROOT / "start_euri.sh", project / "start_euri.sh")
        (project / "venv" / "bin").mkdir(parents=True)
        for executable in ("python", "streamlit"):
            path = project / "venv" / "bin" / executable
            path.write_text(_FAKE_PROCESS)
            path.chmod(0o755)

        launcher = subprocess.Popen(
            ["bash", "start_euri.sh"],
            cwd=project,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            monitor_pid = _wait_pid(project / "hardware_monitor.pid")
            streamlit_pid = _wait_pid(project / "streamlit.pid")
            _wait_pid(project / "voice_daemon.pid")

            assert os.getsid(monitor_pid) == monitor_pid
            assert os.getpgid(monitor_pid) == monitor_pid
            assert os.getsid(monitor_pid) != os.getsid(launcher.pid)

            os.killpg(launcher.pid, signal.SIGINT)
            launcher.communicate(timeout=5)

            assert not _alive(streamlit_pid), "Streamlit e' rimasto attivo"
            assert _alive(monitor_pid), "Il monitor ha ricevuto il Ctrl+C di Euri"
        finally:
            if launcher.poll() is None:
                os.killpg(launcher.pid, signal.SIGKILL)
                launcher.wait(timeout=5)
            if monitor_pid is not None and _alive(monitor_pid):
                os.kill(monitor_pid, signal.SIGTERM)


if __name__ == "__main__":
    test_monitor_survives_launcher_interrupt()
    print("test_start_euri_lifecycle: OK")
