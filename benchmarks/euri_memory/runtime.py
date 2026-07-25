"""Runtime Redis/Vault effimero con guard fail-closed per i benchmark."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Mapping

import redis


class IsolationError(RuntimeError):
    pass


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class IsolatedRuntime:
    """Avvia una nuova istanza Redis in una directory temporanea.

    Il benchmark non seleziona un DB diverso sul Redis personale: crea proprio
    un processo Redis distinto, su una porta casuale non predefinita. Ogni reset
    richiede inoltre un marker segreto scritto da questo stesso runtime.
    """

    PERSONAL_REDIS_PORTS = frozenset({6379})
    PERSONAL_VAULT_PATHS = frozenset({Path("/home/fio/EuriVault")})
    REQUIRED_MODULE_NAMES = frozenset({"ReJSON", "search"})
    DEFAULT_MODULE_PATHS = (
        Path("/usr/lib/redis/modules/rejson.so"),
        Path("/usr/lib/redis/modules/redisearch.so"),
    )

    def __init__(
        self,
        *,
        base_dir: Path | None = None,
        redis_server: str = "redis-server",
        module_paths: tuple[Path, ...] | None = None,
        startup_timeout: float = 8.0,
    ):
        self.base_dir = Path(base_dir).resolve() if base_dir else None
        self.redis_server = redis_server
        self.module_paths = module_paths
        self.startup_timeout = startup_timeout
        self.root: Path | None = None
        self.redis_dir: Path | None = None
        self.vault_dir: Path | None = None
        self.report_dir: Path | None = None
        self.port: int | None = None
        self.runtime_id = str(uuid.uuid4())
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._client: redis.Redis | None = None

    def __enter__(self) -> "IsolatedRuntime":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.stop()

    @property
    def client(self) -> redis.Redis:
        if self._client is None:
            raise IsolationError("runtime non avviato")
        self.assert_isolated()
        return self._client

    def start(self) -> None:
        if self._process is not None:
            raise IsolationError("runtime già avviato")
        executable = shutil.which(self.redis_server)
        if not executable:
            raise IsolationError(f"{self.redis_server!r} non trovato")
        if self.base_dir:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="euri-memory-benchmark-",
            dir=str(self.base_dir) if self.base_dir else None,
        )
        self.root = Path(self._temporary.name).resolve()
        self.redis_dir = self.root / "redis"
        self.vault_dir = self.root / "vault"
        self.report_dir = self.root / "reports"
        for directory in (self.redis_dir, self.vault_dir, self.report_dir):
            directory.mkdir(parents=True, exist_ok=False)

        try:
            modules = self._resolve_modules()
        except Exception:
            self.stop()
            raise
        last_error: Exception | None = None
        for _attempt in range(8):
            self.port = self._unused_loopback_port()
            try:
                self.validate_settings(
                    root=self.root,
                    redis_port=self.port,
                    redis_dir=self.redis_dir,
                    vault_dir=self.vault_dir,
                    report_dir=self.report_dir,
                )
                self._start_process(executable, modules)
                self._wait_until_ready()
                self._write_runtime_marker()
                self.assert_isolated()
                return
            except Exception as exc:
                last_error = exc
                self._stop_process()
        self.stop()
        raise IsolationError(f"impossibile avviare Redis isolato: {last_error}")

    def stop(self) -> None:
        self._client = None
        self._stop_process()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self.root = None
        self.redis_dir = None
        self.vault_dir = None
        self.report_dir = None
        self.port = None

    def environment(self) -> Mapping[str, str]:
        self.assert_isolated()
        assert self.port is not None
        assert self.vault_dir is not None
        return {
            "EURI_BENCHMARK_MODE": "1",
            "EURI_BENCHMARK_RUNTIME_ID": self.runtime_id,
            "EURI_DISABLE_EXTERNAL_ACTIONS": "1",
            "EURI_REDIS_HOST": "127.0.0.1",
            "EURI_REDIS_PORT": str(self.port),
            "EURI_REDIS_DB": "0",
            "EURI_OBSIDIAN_VAULT_PATH": str(self.vault_dir),
            "EURI_OBSIDIAN_SYNC_ENABLED": "0",
            "EURI_OWNER_ACTOR_ID": "benchmark-owner",
            "EURI_OWNER_DISPLAY_NAME": "Benchmark User",
        }

    def reset(self) -> None:
        client = self.client
        client.flushdb()
        client.set(self._marker_key, self.runtime_id)
        self.assert_isolated()

    def assert_isolated(self) -> None:
        if (
            self._client is None
            or self._process is None
            or self._process.poll() is not None
            or self.port is None
            or self.root is None
            or self.redis_dir is None
            or self.vault_dir is None
            or self.report_dir is None
        ):
            raise IsolationError("runtime isolato inattivo")
        self.validate_settings(
            root=self.root,
            redis_port=self.port,
            redis_dir=self.redis_dir,
            vault_dir=self.vault_dir,
            report_dir=self.report_dir,
        )
        try:
            marker = self._client.get(self._marker_key)
        except redis.RedisError as exc:
            raise IsolationError(f"Redis isolato non raggiungibile: {exc}") from exc
        if marker != self.runtime_id:
            raise IsolationError("marker Redis assente: reset/scrittura rifiutati")

    @classmethod
    def validate_settings(
        cls,
        *,
        root: Path,
        redis_port: int,
        redis_dir: Path,
        vault_dir: Path,
        report_dir: Path,
    ) -> None:
        resolved_root = root.resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        if resolved_root in {Path("/"), temp_root, Path.home().resolve()}:
            raise IsolationError(f"radice benchmark troppo ampia: {resolved_root}")
        if not _is_within(resolved_root, temp_root):
            raise IsolationError(f"radice benchmark fuori dalla directory temporanea: {resolved_root}")
        if redis_port in cls.PERSONAL_REDIS_PORTS:
            raise IsolationError(f"porta Redis personale vietata: {redis_port}")
        for label, path in (
            ("redis", redis_dir),
            ("vault", vault_dir),
            ("report", report_dir),
        ):
            resolved = path.resolve()
            if not _is_within(resolved, resolved_root):
                raise IsolationError(f"directory {label} fuori dal runtime: {resolved}")
        if vault_dir.resolve() in {path.resolve() for path in cls.PERSONAL_VAULT_PATHS}:
            raise IsolationError(f"Vault personale vietato: {vault_dir}")

    @property
    def _marker_key(self) -> str:
        return "euri:benchmark:runtime_id"

    def _resolve_modules(self) -> tuple[Path, ...]:
        modules = self.module_paths or self.DEFAULT_MODULE_PATHS
        missing = [path for path in modules if not path.is_file()]
        if missing:
            joined = ", ".join(str(path) for path in missing)
            raise IsolationError(f"moduli Redis richiesti non trovati: {joined}")
        return tuple(path.resolve() for path in modules)

    @staticmethod
    def _unused_loopback_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _start_process(self, executable: str, modules: tuple[Path, ...]) -> None:
        assert self.port is not None
        assert self.redis_dir is not None
        assert self.root is not None
        command = [
            executable,
            "--bind",
            "127.0.0.1",
            "--protected-mode",
            "yes",
            "--port",
            str(self.port),
            "--dir",
            str(self.redis_dir),
            "--dbfilename",
            "benchmark.rdb",
            "--appendonly",
            "no",
            "--save",
            "",
            "--loglevel",
            "warning",
            "--logfile",
            str(self.root / "redis.log"),
        ]
        for module in modules:
            command.extend(("--loadmodule", str(module)))
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._client = redis.Redis(
            host="127.0.0.1",
            port=self.port,
            db=0,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.5,
        )

    def _wait_until_ready(self) -> None:
        assert self._process is not None
        assert self._client is not None
        deadline = time.monotonic() + self.startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                log = self._read_redis_log()
                raise IsolationError(
                    f"redis-server terminato con exit {self._process.returncode}: {log}"
                )
            try:
                if self._client.ping():
                    server_pid = int(self._client.info("server")["process_id"])
                    if server_pid != self._process.pid:
                        raise IsolationError(
                            "la porta appartiene a un altro processo Redis"
                        )
                    loaded = {
                        str(item.get("name", ""))
                        for item in self._client.module_list()
                    }
                    missing = self.REQUIRED_MODULE_NAMES - loaded
                    if missing:
                        raise IsolationError(
                            f"moduli Redis non caricati: {', '.join(sorted(missing))}"
                        )
                    return
            except (redis.RedisError, IsolationError) as exc:
                last_error = exc
            time.sleep(0.05)
        raise IsolationError(f"timeout avvio Redis isolato: {last_error}")

    def _write_runtime_marker(self) -> None:
        assert self._client is not None
        assert self.root is not None
        assert self.port is not None
        self._client.set(self._marker_key, self.runtime_id)
        manifest = {
            "schema_version": 1,
            "runtime_id": self.runtime_id,
            "redis_host": "127.0.0.1",
            "redis_port": self.port,
            "redis_pid": self._process.pid if self._process else None,
            "root": str(self.root),
        }
        (self.root / "runtime.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read_redis_log(self) -> str:
        if self.root is None:
            return ""
        try:
            return (self.root / "redis.log").read_text(
                encoding="utf-8",
                errors="replace",
            )[-2000:]
        except OSError:
            return ""

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
