#!/usr/bin/env python3
"""Regressioni pure per stati, isteresi e anti-spam hardware."""

from types import SimpleNamespace

from core.hardware_interoception import (
    CRITICAL,
    NORMAL,
    WARNING,
    HardwareCollector,
    HardwareInteroceptor,
    HardwareSample,
    InteroceptiveStateMachine,
    SensorReading,
    SensorSpec,
    valid_temperature,
)
from core.hardware_baseline import summarize_hardware_baseline


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def reading(value, *, sensor="gpu_0_temp", warning=82.0, critical=90.0):
    return SensorReading(
        SensorSpec(
            sensor=sensor,
            unit="celsius",
            warning=warning,
            critical=critical,
            warning_samples=3,
            critical_samples=1,
            recovery_samples=3,
            hysteresis=3.0,
        ),
        value,
    )


def test_warning_requires_persistence_but_critical_is_immediate():
    clock = FakeClock()
    machine = InteroceptiveStateMachine(cooldown_s=300, monotonic=clock)

    assert machine.observe(reading(83)) == []
    assert machine.observe(reading(84)) == []
    warning_event = machine.observe(reading(85))
    assert len(warning_event) == 1
    assert warning_event[0].level == WARNING
    assert warning_event[0].previous_level == NORMAL

    critical_event = machine.observe(reading(92))
    assert len(critical_event) == 1
    assert critical_event[0].level == CRITICAL
    assert critical_event[0].previous_level == WARNING


def test_hysteresis_and_recovery_avoid_threshold_flapping():
    machine = InteroceptiveStateMachine(cooldown_s=300, monotonic=FakeClock())
    for value in (83, 84, 85):
        events = machine.observe(reading(value))
    assert events[0].level == WARNING

    # 80 C e' sotto la soglia di ingresso, ma dentro l'isteresi (82 - 3).
    assert machine.observe(reading(80)) == []
    assert machine.levels()["gpu_0_temp"] == WARNING

    assert machine.observe(reading(78)) == []
    assert machine.observe(reading(78)) == []
    recovered = machine.observe(reading(78))
    assert len(recovered) == 1
    assert recovered[0].kind == "recovered"
    assert recovered[0].level == NORMAL


def test_cooldown_suppresses_repeats_without_blocking_escalation():
    clock = FakeClock()
    machine = InteroceptiveStateMachine(cooldown_s=300, monotonic=clock)
    for _ in range(3):
        events = machine.observe(reading(85))
    assert events[0].level == WARNING

    clock.value = 299
    assert machine.observe(reading(85)) == []
    # L'escalation non aspetta il cooldown del warning.
    critical = machine.observe(reading(91))
    assert critical[0].level == CRITICAL

    clock.value = 598
    assert machine.observe(reading(91)) == []
    clock.value = 599
    reminder = machine.observe(reading(91))
    assert reminder[0].kind == "reminder"
    assert reminder[0].level == CRITICAL


def test_vram_pressure_has_no_critical_state_by_default():
    collector = object.__new__(HardwareCollector)
    collector.config = SimpleNamespace(
        HARDWARE_VRAM_WARNING_PCT=92.0,
        HARDWARE_INTEROCEPTION_WARNING_SAMPLES=3,
        HARDWARE_INTEROCEPTION_RECOVERY_SAMPLES=3,
        HARDWARE_GPU_TEMP_WARNING_C=82.0,
        HARDWARE_GPU_TEMP_CRITICAL_C=90.0,
    )
    readings = collector._gpu_readings([{
        "index": 0,
        "uuid": "GPU-test",
        "name": "test",
        "temp_c": 45.0,
        "provider": "fake",
        "memory_used_bytes": 99,
        "memory_total_bytes": 100,
    }])
    vram = next(item for item in readings if item.spec.sensor == "gpu_0_vram")
    assert vram.spec.warning == 92.0
    assert vram.spec.critical is None

    machine = InteroceptiveStateMachine(monotonic=FakeClock())
    for _ in range(5):
        events = machine.observe(vram)
    assert events[0].level == WARNING
    assert machine.levels()["gpu_0_vram"] != CRITICAL


def test_bogus_temperatures_are_rejected():
    assert valid_temperature(59.0)
    assert not valid_temperature(65261.85)
    assert not valid_temperature(float("nan"))
    assert not valid_temperature("not-a-number")


def test_nvidia_smi_parser_handles_multiple_gpus():
    output = (
        "0, GPU-one, NVIDIA GeForce RTX 4060 Ti, 48, 11, 1198, 16380\n"
        "1, GPU-two, NVIDIA GeForce RTX 4060 Ti, 44, 5, 126, 16380\n"
    )
    parsed = HardwareCollector._parse_nvidia_smi(output)
    assert [gpu["index"] for gpu in parsed] == [0, 1]
    assert parsed[0]["provider"] == "nvidia-smi"
    assert parsed[0]["memory_used_bytes"] == 1198 * 1024**2


class FakeCollector:
    def __init__(self, values):
        self.values = iter(values)
        self.closed = False

    def collect(self):
        value = next(self.values)
        return HardwareSample([reading(value)], {"value": value}, timestamp=1000 + value)

    def close(self):
        self.closed = True


class FakePublisher:
    def __init__(self):
        self.snapshots = []
        self.events = []
        self.faults = []
        self.baselines = []

    def publish_snapshot(self, sample, levels):
        self.snapshots.append((sample, dict(levels)))

    def publish_event(self, event):
        self.events.append(event)

    def publish_baseline(self, sample, levels):
        self.baselines.append((sample, dict(levels)))

    def publish_fault(self, fault, timestamp, kind="sensor_fault"):
        self.faults.append((kind, fault, timestamp))


def test_every_sample_updates_snapshot_but_only_transitions_emit_events():
    publisher = FakePublisher()
    interoceptor = HardwareInteroceptor(
        FakeCollector([40, 83, 84, 85, 85]),
        InteroceptiveStateMachine(cooldown_s=300, monotonic=FakeClock()),
        publisher,
        interval_s=3,
    )
    for _ in range(5):
        interoceptor.sample_once()

    assert len(publisher.snapshots) == 5
    assert len(publisher.baselines) == 1
    assert len(publisher.events) == 1
    assert publisher.events[0].level == WARNING


def test_sensor_fault_emits_once_then_recovery():
    clock = FakeClock()
    publisher = FakePublisher()

    class FaultCollector:
        def __init__(self):
            self.samples = iter([
                HardwareSample([], {}, [{"sensor": "gpu_provider", "error": "offline"}], 1),
                HardwareSample([], {}, [{"sensor": "gpu_provider", "error": "offline"}], 2),
                HardwareSample([], {}, [], 3),
            ])

        def collect(self):
            return next(self.samples)

        def close(self):
            pass

    interoceptor = HardwareInteroceptor(
        FaultCollector(),
        InteroceptiveStateMachine(cooldown_s=300, monotonic=clock),
        publisher,
        monotonic=clock,
    )
    interoceptor.sample_once()
    clock.value = 1
    interoceptor.sample_once()
    clock.value = 2
    interoceptor.sample_once()

    assert [entry[0] for entry in publisher.faults] == ["sensor_fault", "sensor_recovered"]


def test_baseline_audit_requires_time_and_coverage():
    samples = []
    for minute in range(0, 72 * 60 + 1):
        samples.append({
            "timestamp": minute * 60,
            "levels": {"gpu_0_temp": "NORMAL"},
            "readings": {"gpu_0_temp": 45 + (minute % 10), "gpu_0_vram": 85},
            "metrics": {"gpus": [{"util_percent": 60 if minute == 100 else 5}]},
            "faults": [],
        })
    report = summarize_hardware_baseline(samples, expected_interval_s=60)
    assert report["ready_for_review"] is True
    assert report["coverage"] == 1.0
    assert report["representative_load"] is True
    assert report["sensors"]["gpu_0_temp"]["max"] == 54

    early = summarize_hardware_baseline(samples[:61], expected_interval_s=60)
    assert early["status"] == "collecting"
    assert early["ready_for_review"] is False


def run():
    tests = [
        test_warning_requires_persistence_but_critical_is_immediate,
        test_hysteresis_and_recovery_avoid_threshold_flapping,
        test_cooldown_suppresses_repeats_without_blocking_escalation,
        test_vram_pressure_has_no_critical_state_by_default,
        test_bogus_temperatures_are_rejected,
        test_nvidia_smi_parser_handles_multiple_gpus,
        test_every_sample_updates_snapshot_but_only_transitions_emit_events,
        test_sensor_fault_emits_once_then_recovery,
        test_baseline_audit_requires_time_and_coverage,
    ]
    for test in tests:
        test()
    print(f"test_hardware_interoception: {len(tests)}/{len(tests)} OK")


if __name__ == "__main__":
    run()
