import runpy
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "qualify-qwen36-stress-coexistence.py"
)
NS = runpy.run_path(str(SCRIPT))


class FakeProcess:
    pid = 5151

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeMonitor:
    def __init__(self, violation=None):
        self.violation = violation
        self.required = False
        self.phase = None

    def set_require_target(self, required):
        self.required = bool(required)

    def set_phase(self, phase):
        self.phase = phase


def test_health_gate_returns_runtime_exit_reason_instead_of_raising():
    reason = NS["wait_health_with_monitor"](
        FakeProcess(returncode=7),
        FakeMonitor(),
        8013,
        1.0,
    )

    assert reason == "RUNTIME_EXITED_BEFORE_HEALTH:7"


def test_health_gate_preserves_resource_violation_over_runtime_exit():
    reason = NS["wait_health_with_monitor"](
        FakeProcess(returncode=-15),
        FakeMonitor("RELATIVE_SWAP_GROWTH_LIMIT"),
        8013,
        1.0,
    )

    assert reason == "RELATIVE_SWAP_GROWTH_LIMIT"


def test_target_wait_preserves_monitor_violation():
    reason = NS["wait_for_target_entry"](
        FakeProcess(),
        FakeMonitor("RESOURCE_OBSERVATION_FAILED"),
        "IDE",
        1.0,
    )

    assert reason == "RESOURCE_OBSERVATION_FAILED"


def test_target_wait_reports_runtime_exit_explicitly():
    reason = NS["wait_for_target_entry"](
        FakeProcess(returncode=9),
        FakeMonitor(),
        "IDE",
        1.0,
    )

    assert reason == "RUNTIME_EXITED_WAITING_FOR_TARGET:9"


def test_target_wait_timeout_is_only_no_stress_evidence_case():
    reason = NS["wait_for_target_entry"](
        FakeProcess(),
        FakeMonitor(),
        "IDE",
        0.0,
    )

    assert reason == "TARGET_WORKLOAD_NOT_OBSERVED"


def test_source_refreshes_load_preflight_immediately_before_spawn_and_uses_it_as_baseline():
    source = SCRIPT.read_text(encoding="utf-8")

    refresh = source.index(
        "load_preflight_result = MemoryPreflight().check"
    )
    spawn = source.index("process = subprocess.Popen", refresh)
    monitor = source.index("monitor = StressResourceMonitor(", spawn)
    baseline = source.index("load_preflight_result.snapshot", monitor)
    start = source.index("monitor.start()", baseline)
    health = source.index("load_failure = wait_health_with_monitor", start)

    assert refresh < spawn < monitor < baseline < start < health
    assert "preflight_result.snapshot" not in source[monitor:start]


def test_source_revalidates_preloaded_target_before_model_load():
    source = SCRIPT.read_text(encoding="utf-8")

    fresh = source.index("fresh_raw = process_text()")
    preloaded = source.index(
        "scenario is Scenario.PRELOADED_COEXISTENCE",
        fresh,
    )
    invalid = source.index("TARGET_PRESENT_BEFORE_MODEL_LOAD", preloaded)
    refresh = source.index("load_preflight_result = MemoryPreflight().check", invalid)

    assert fresh < preloaded < invalid < refresh


def test_source_maps_only_true_target_timeout_to_no_stress_evidence():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'return monitor.violation or "TARGET_WORKLOAD_NOT_OBSERVED"' in source
    assert 'return monitor.violation or f"RUNTIME_EXITED_WAITING_FOR_TARGET:{returncode}"' in source
    assert 'return monitor.violation or f"RUNTIME_EXITED_BEFORE_HEALTH:{returncode}"' in source
    assert '"TARGET_WORKLOAD_NOT_OBSERVED",' in source
    assert 'verdict = "NO_STRESS_EVIDENCE"' in source
