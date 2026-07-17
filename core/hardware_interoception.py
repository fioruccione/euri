"""Interocezione hardware locale: sensazione, stato e transizioni.

Il modulo non interpreta semanticamente i dati e non chiama modelli. I sensori
producono letture; una macchina a stati applica persistenza, isteresi e cooldown;
Redis conserva uno snapshot effimero e uno stream delle sole transizioni.

ROADMAP COGNITIVA (non rimuovere senza sostituirla con una specifica):

Fase 0, attiva oggi: OSSERVAZIONE. Raccogliere una baseline reale, verificare le
soglie e distinguere carico normale, pressione persistente, guasto del sensore e
pericolo. Nessun consumer deve ancora fermare processi o coinvolgere un LLM.

Fase 1: RIFLESSO PROTETTIVO DETERMINISTICO. Solo dopo la baseline, un consumer
separato potra' reagire a CRITICAL con azioni reversibili e idempotenti (prima
rinviare Dream/maintenance, poi eventualmente impedire nuovi model load). Deve
sempre rivalidare lo snapshot fresco; mai uccidere processi dalla sola telemetria.

Fase 2: PERCEZIONE COGNITIVA. Il modello potra' ricevere soltanto eventi gia'
stabilizzati e il loro esito, non il flusso grezzo. Potra' spiegare o ricordare
episodi eccezionali, ma non decidere il riflesso urgente.

Estensione prevista: riusare lo stesso contratto sensazione -> stato -> transizione
per disco, UPS, Redis, Ollama, microfono e webcam. Non creare code parallele per
ogni organo: gli eventi confluiscono nel Pulse, mentre ogni senso mantiene il suo
snapshot locale e la propria policy.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from loguru import logger

from core.pulse import pulse_emit


LATEST_KEY = "euri:hardware:latest"
STATE_KEY = "euri:hardware:state"
EVENT_STREAM = "euri:hardware:events"
EVENT_STREAM_MAXLEN = 10000
BASELINE_STREAM = "euri:hardware:baseline"
BASELINE_STREAM_MAXLEN = 20160  # circa 14 giorni a un campione/minuto

NORMAL = "NORMAL"
WARNING = "WARNING"
CRITICAL = "CRITICAL"
_RANK = {NORMAL: 0, WARNING: 1, CRITICAL: 2}


@dataclass(frozen=True)
class SensorSpec:
    sensor: str
    unit: str
    warning: float | None = None
    critical: float | None = None
    warning_samples: int = 3
    critical_samples: int = 1
    recovery_samples: int = 3
    hysteresis: float = 2.0


@dataclass(frozen=True)
class SensorReading:
    spec: SensorSpec
    value: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sensor": self.spec.sensor,
            "value": round(float(self.value), 3),
            "unit": self.spec.unit,
            "warning": self.spec.warning,
            "critical": self.spec.critical,
            "metadata": self.metadata,
        }


@dataclass
class HardwareSample:
    readings: list[SensorReading]
    metrics: dict[str, Any]
    faults: list[dict[str, str]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class SensorRuntime:
    level: str = NORMAL
    pending_level: str | None = None
    pending_count: int = 0
    last_event_mono: float | None = None


@dataclass(frozen=True)
class InteroceptiveEvent:
    kind: str
    level: str
    previous_level: str
    sensor: str
    value: float
    unit: str
    timestamp: float
    threshold: float | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def valid_temperature(value: Any) -> bool:
    """Scarta sentinel e letture corrotte (alcuni driver espongono 65261 C)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and -20.0 <= number <= 150.0


class InteroceptiveStateMachine:
    """Converte letture rumorose in cambi di stato significativi."""

    def __init__(self, cooldown_s: float = 300.0, monotonic: Callable[[], float] = time.monotonic):
        self.cooldown_s = max(0.0, float(cooldown_s))
        self._monotonic = monotonic
        self._runtime: dict[str, SensorRuntime] = {}

    def levels(self) -> dict[str, str]:
        return {sensor: runtime.level for sensor, runtime in self._runtime.items()}

    def observe(self, reading: SensorReading, timestamp: float | None = None) -> list[InteroceptiveEvent]:
        spec = reading.spec
        if spec.warning is None and spec.critical is None:
            return []

        runtime = self._runtime.setdefault(spec.sensor, SensorRuntime())
        desired = self._desired_level(reading.value, spec, runtime.level)
        now_mono = self._monotonic()
        wall_ts = float(timestamp if timestamp is not None else time.time())

        if desired == runtime.level:
            runtime.pending_level = None
            runtime.pending_count = 0
            if runtime.level != NORMAL and self._reminder_due(runtime, now_mono):
                runtime.last_event_mono = now_mono
                return [self._event("reminder", runtime.level, runtime.level, reading, wall_ts)]
            return []

        if runtime.pending_level != desired:
            runtime.pending_level = desired
            runtime.pending_count = 1
        else:
            runtime.pending_count += 1

        required = self._required_samples(runtime.level, desired, spec)
        if runtime.pending_count < required:
            return []

        previous = runtime.level
        runtime.level = desired
        runtime.pending_level = None
        runtime.pending_count = 0
        runtime.last_event_mono = now_mono
        if desired == NORMAL:
            kind = "recovered"
        elif _RANK[desired] > _RANK[previous]:
            kind = "alert"
        else:
            kind = "deescalated"
        return [self._event(kind, desired, previous, reading, wall_ts)]

    def _desired_level(self, value: float, spec: SensorSpec, current: str) -> str:
        warning = spec.warning
        critical = spec.critical
        hysteresis = max(0.0, spec.hysteresis)

        if current == CRITICAL and critical is not None and value >= critical - hysteresis:
            return CRITICAL
        if critical is not None and value >= critical:
            return CRITICAL
        if current in {WARNING, CRITICAL} and warning is not None and value >= warning - hysteresis:
            return WARNING
        if warning is not None and value >= warning:
            return WARNING
        return NORMAL

    @staticmethod
    def _required_samples(current: str, desired: str, spec: SensorSpec) -> int:
        if _RANK[desired] < _RANK[current]:
            return max(1, spec.recovery_samples)
        if desired == CRITICAL:
            return max(1, spec.critical_samples)
        return max(1, spec.warning_samples)

    def _reminder_due(self, runtime: SensorRuntime, now_mono: float) -> bool:
        return (
            self.cooldown_s > 0
            and runtime.last_event_mono is not None
            and now_mono - runtime.last_event_mono >= self.cooldown_s
        )

    @staticmethod
    def _event(
        kind: str,
        level: str,
        previous: str,
        reading: SensorReading,
        timestamp: float,
    ) -> InteroceptiveEvent:
        threshold = reading.spec.critical if level == CRITICAL else reading.spec.warning
        return InteroceptiveEvent(
            kind=kind,
            level=level,
            previous_level=previous,
            sensor=reading.spec.sensor,
            value=round(float(reading.value), 3),
            unit=reading.spec.unit,
            timestamp=timestamp,
            threshold=threshold,
            metadata=reading.metadata,
        )


class HardwareCollector:
    """Raccoglie CPU/RAM con psutil e GPU tramite NVML o nvidia-smi."""

    def __init__(self):
        import config

        self.config = config
        self._nvml: Any | None = None
        self._nvml_ready = False
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml = pynvml
            self._nvml_ready = True
        except Exception:
            self._nvml = None

    def close(self) -> None:
        if self._nvml_ready and self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                pass
        self._nvml_ready = False

    def collect(self) -> HardwareSample:
        readings: list[SensorReading] = []
        metrics: dict[str, Any] = {}
        faults: list[dict[str, str]] = []

        try:
            self._collect_host(readings, metrics)
        except Exception as exc:
            faults.append({"sensor": "host", "error": f"{type(exc).__name__}: {exc}"})

        try:
            gpu_metrics = self._collect_gpus()
            metrics["gpus"] = gpu_metrics
            readings.extend(self._gpu_readings(gpu_metrics))
        except Exception as exc:
            faults.append({"sensor": "gpu_provider", "error": f"{type(exc).__name__}: {exc}"})

        return HardwareSample(readings=readings, metrics=metrics, faults=faults)

    def _collect_host(self, readings: list[SensorReading], metrics: dict[str, Any]) -> None:
        import psutil

        vm = psutil.virtual_memory()
        metrics["cpu_percent"] = float(psutil.cpu_percent(interval=None))
        metrics["ram"] = {
            "percent": float(vm.percent),
            "available_bytes": int(vm.available),
            "total_bytes": int(vm.total),
        }
        readings.append(SensorReading(
            SensorSpec(
                sensor="ram_percent",
                unit="percent",
                warning=float(self.config.HARDWARE_RAM_WARNING_PCT),
                critical=float(self.config.HARDWARE_RAM_CRITICAL_PCT),
                warning_samples=int(self.config.HARDWARE_INTEROCEPTION_WARNING_SAMPLES),
                recovery_samples=int(self.config.HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES),
                hysteresis=3.0,
            ),
            float(vm.percent),
            {"available_bytes": int(vm.available), "total_bytes": int(vm.total)},
        ))

        package_temps: list[tuple[str, float, float | None, float | None]] = []
        for family, entries in (psutil.sensors_temperatures() or {}).items():
            if family.lower() != "coretemp":
                continue
            for entry in entries:
                if entry.label.lower().startswith("package") and valid_temperature(entry.current):
                    package_temps.append((entry.label, float(entry.current), entry.high, entry.critical))
        if package_temps:
            hottest = max(package_temps, key=lambda item: item[1])
            metrics["cpu_packages"] = [
                {"label": label, "temp_c": value, "high_c": high, "critical_c": critical}
                for label, value, high, critical in package_temps
            ]
            readings.append(SensorReading(
                SensorSpec(
                    sensor="cpu_package_max_temp",
                    unit="celsius",
                    warning=float(self.config.HARDWARE_CPU_TEMP_WARNING_C),
                    critical=float(self.config.HARDWARE_CPU_TEMP_CRITICAL_C),
                    warning_samples=int(self.config.HARDWARE_INTEROCEPTION_WARNING_SAMPLES),
                    recovery_samples=int(self.config.HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES),
                    hysteresis=3.0,
                ),
                hottest[1],
                {"package": hottest[0], "reported_high": hottest[2], "reported_critical": hottest[3]},
            ))

    def _collect_gpus(self) -> list[dict[str, Any]]:
        if self._nvml_ready and self._nvml is not None:
            try:
                return self._collect_gpus_nvml()
            except Exception as exc:
                logger.warning(f"Hardware interoception: NVML non disponibile, fallback nvidia-smi ({exc})")
                self._nvml_ready = False
        return self._collect_gpus_smi()

    def _collect_gpus_nvml(self) -> list[dict[str, Any]]:
        nvml = self._nvml
        result = []
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            memory = nvml.nvmlDeviceGetMemoryInfo(handle)
            name = nvml.nvmlDeviceGetName(handle)
            uuid = nvml.nvmlDeviceGetUUID(handle)
            if isinstance(name, bytes):
                name = name.decode(errors="replace")
            if isinstance(uuid, bytes):
                uuid = uuid.decode(errors="replace")
            result.append({
                "index": index,
                "uuid": str(uuid),
                "name": str(name),
                "temp_c": float(nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)),
                "util_percent": float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu),
                "memory_used_bytes": int(memory.used),
                "memory_total_bytes": int(memory.total),
                "provider": "pynvml",
            })
        return result

    @staticmethod
    def _collect_gpus_smi() -> list[dict[str, Any]]:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        return HardwareCollector._parse_nvidia_smi(output)

    @staticmethod
    def _parse_nvidia_smi(output: str) -> list[dict[str, Any]]:
        result = []
        for line in output.splitlines():
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 7:
                continue
            index, uuid, name, temp, util, used_mib, total_mib = fields
            result.append({
                "index": int(index),
                "uuid": uuid,
                "name": name,
                "temp_c": float(temp),
                "util_percent": float(util),
                "memory_used_bytes": int(float(used_mib) * 1024**2),
                "memory_total_bytes": int(float(total_mib) * 1024**2),
                "provider": "nvidia-smi",
            })
        if not result:
            raise RuntimeError("nessuna GPU leggibile")
        return result

    def _gpu_readings(self, gpus: Iterable[dict[str, Any]]) -> list[SensorReading]:
        readings = []
        for gpu in gpus:
            index = int(gpu["index"])
            metadata = {"gpu_index": index, "uuid": gpu["uuid"], "name": gpu["name"], "provider": gpu["provider"]}
            if valid_temperature(gpu.get("temp_c")):
                readings.append(SensorReading(
                    SensorSpec(
                        sensor=f"gpu_{index}_temp",
                        unit="celsius",
                        warning=float(self.config.HARDWARE_GPU_TEMP_WARNING_C),
                        critical=float(self.config.HARDWARE_GPU_TEMP_CRITICAL_C),
                        warning_samples=int(self.config.HARDWARE_INTEROCEPTION_WARNING_SAMPLES),
                        recovery_samples=int(self.config.HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES),
                        hysteresis=3.0,
                    ),
                    float(gpu["temp_c"]),
                    metadata,
                ))
            total = int(gpu.get("memory_total_bytes") or 0)
            if total > 0:
                used = int(gpu.get("memory_used_bytes") or 0)
                percent = 100.0 * used / total
                readings.append(SensorReading(
                    SensorSpec(
                        sensor=f"gpu_{index}_vram",
                        unit="percent",
                        warning=float(self.config.HARDWARE_VRAM_WARNING_PCT),
                        critical=None,
                        warning_samples=max(5, int(self.config.HARDWARE_INTEROCEPTION_WARNING_SAMPLES)),
                        recovery_samples=int(self.config.HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES),
                        hysteresis=5.0,
                    ),
                    percent,
                    {**metadata, "used_bytes": used, "total_bytes": total},
                ))
        return readings


class RedisInteroceptionPublisher:
    """Persistenza fail-open: Redis non deve mai fermare il campionamento."""

    def __init__(self, redis_client: Any, latest_ttl_s: int = 30):
        self.redis = redis_client
        self.latest_ttl_s = max(5, int(latest_ttl_s))

    def publish_snapshot(self, sample: HardwareSample, levels: dict[str, str]) -> None:
        overall = max(levels.values(), key=lambda level: _RANK.get(level, 0), default=NORMAL)
        if sample.faults and overall == NORMAL:
            overall = WARNING
        payload = {
            "timestamp": sample.timestamp,
            "overall_level": overall,
            "levels": levels,
            "readings": [reading.snapshot() for reading in sample.readings],
            "metrics": sample.metrics,
            "faults": sample.faults,
        }
        try:
            encoded = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
            pipe = self.redis.pipeline(transaction=False)
            pipe.set(LATEST_KEY, encoded, ex=self.latest_ttl_s)
            pipe.set(
                STATE_KEY,
                json.dumps(
                    {"overall_level": overall, "levels": levels, "faults": sample.faults},
                    separators=(",", ":"),
                ),
                ex=self.latest_ttl_s,
            )
            pipe.execute()
        except Exception as exc:
            logger.warning(f"Hardware interoception: snapshot Redis fallito ({exc})")

    def publish_event(self, event: InteroceptiveEvent) -> None:
        payload = event.payload()
        try:
            self.redis.xadd(
                EVENT_STREAM,
                {"payload": json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))},
                maxlen=EVENT_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:
            logger.warning(f"Hardware interoception: evento Redis fallito ({exc})")
        salience = {CRITICAL: 0.95, WARNING: 0.60, NORMAL: 0.35}.get(event.level, 0.5)
        pulse_emit(self.redis, "hardware", "intero", event.kind, payload, salience)

    def publish_baseline(self, sample: HardwareSample, levels: dict[str, str]) -> None:
        """Serie temporale bounded per calibrare soglie; non entra nel Pulse."""
        payload = {
            "timestamp": sample.timestamp,
            "levels": levels,
            "metrics": sample.metrics,
            "readings": {
                reading.spec.sensor: round(float(reading.value), 3)
                for reading in sample.readings
            },
            "faults": sample.faults,
        }
        try:
            self.redis.xadd(
                BASELINE_STREAM,
                {"payload": json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))},
                maxlen=BASELINE_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:
            logger.warning(f"Hardware interoception: baseline Redis fallita ({exc})")

    def publish_fault(self, fault: dict[str, str], timestamp: float, kind: str = "sensor_fault") -> None:
        level = NORMAL if kind == "sensor_recovered" else WARNING
        payload = {"kind": kind, "level": level, "timestamp": timestamp, **fault}
        try:
            self.redis.xadd(
                EVENT_STREAM,
                {"payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
                maxlen=EVENT_STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:
            logger.warning(f"Hardware interoception: fault Redis fallito ({exc})")
        pulse_emit(
            self.redis,
            "hardware",
            "intero",
            kind,
            payload,
            0.35 if kind == "sensor_recovered" else 0.65,
        )


class HardwareInteroceptor:
    def __init__(
        self,
        collector: HardwareCollector,
        state_machine: InteroceptiveStateMachine,
        publisher: RedisInteroceptionPublisher,
        interval_s: float = 3.0,
        baseline_interval_s: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.collector = collector
        self.state_machine = state_machine
        self.publisher = publisher
        self.interval_s = max(0.5, float(interval_s))
        self.baseline_interval_s = max(self.interval_s, float(baseline_interval_s))
        self._monotonic = monotonic
        self._last_baseline_mono: float | None = None
        self._fault_last_sent: dict[str, float] = {}
        self._active_faults: dict[str, dict[str, str]] = {}

    def sample_once(self) -> HardwareSample:
        sample = self.collector.collect()
        events: list[InteroceptiveEvent] = []
        for reading in sample.readings:
            events.extend(self.state_machine.observe(reading, sample.timestamp))
        levels = self.state_machine.levels()
        self.publisher.publish_snapshot(sample, levels)
        now = self._monotonic()
        if self._last_baseline_mono is None or now - self._last_baseline_mono >= self.baseline_interval_s:
            self.publisher.publish_baseline(sample, levels)
            self._last_baseline_mono = now
        for event in events:
            self.publisher.publish_event(event)
            logger.warning(
                f"Hardware interoception: {event.sensor} {event.previous_level}->{event.level} "
                f"({event.value:g} {event.unit})"
            )
        self._publish_faults(sample)
        return sample

    def _publish_faults(self, sample: HardwareSample) -> None:
        now = self._monotonic()
        current = {fault.get("sensor", "unknown"): fault for fault in sample.faults}
        for fault in sample.faults:
            sensor = fault.get("sensor", "unknown")
            last = self._fault_last_sent.get(sensor)
            if sensor in self._active_faults and last is not None and now - last < self.state_machine.cooldown_s:
                continue
            self._fault_last_sent[sensor] = now
            self._active_faults[sensor] = fault
            self.publisher.publish_fault(fault, sample.timestamp)

        for sensor in set(self._active_faults) - set(current):
            previous = self._active_faults.pop(sensor)
            self._fault_last_sent.pop(sensor, None)
            self.publisher.publish_fault(
                {"sensor": sensor, "previous_error": previous.get("error", "")},
                sample.timestamp,
                kind="sensor_recovered",
            )

    def run(self, stop_event: Any) -> None:
        try:
            while not stop_event.is_set():
                started = self._monotonic()
                try:
                    self.sample_once()
                except Exception:
                    logger.exception("Hardware interoception: campionamento fallito")
                elapsed = self._monotonic() - started
                stop_event.wait(max(0.1, self.interval_s - elapsed))
        finally:
            self.collector.close()
