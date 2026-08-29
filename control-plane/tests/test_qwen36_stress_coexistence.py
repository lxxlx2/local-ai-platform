import runpy
from pathlib import Path
from unittest.mock import patch

from local_ai_control.services.models import MemorySnapshot
from local_ai_control.services.workload_admission import WorkloadClass, WorkloadManifestProbe


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "qualify-qwen36-stress-coexistence.py"
)
NS = runpy.run_path(str(SCRIPT))


def _snapshot(*, reclaimable=30.0, swap=1.5, pressure="NORMAL"):
    return MemorySnapshot(
        total_gib=48.0,
        available_gib=reclaimable,
        swap_used_gib=swap,
        pressure=pressure,
        reclaimable_gib=reclaimable,
    )


def _raw(*, browser=True, unity=False, ide=False):
    rows = ["1 102400 /Applications/Terminal.app/Contents/MacOS/Terminal"]
    if browser:
        rows.append(
            "2 1048576 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    if unity:
        rows.append(
            "3 3145728 /Applications/Unity/Hub/Editor/6000.3.23f1/Unity.app/Contents/MacOS/Unity"
        )
    if ide:
        rows.append(
            "4 1048576 /Applications/Visual Studio Code.app/Contents/MacOS/Electron"
        )
    return "\n".join(rows)


def _manifest(raw, *, occupied=False):
    return WorkloadManifestProbe(
        process_reader=lambda: raw,
        memory_probe=lambda: _snapshot(),
        listeners=lambda port: (9001,) if occupied and port == 8013 else (),
        ports=(8000, 8001, 8011, 8012, 8013),
    ).capture(WorkloadClass.STRESS_COEXISTENCE)


def test_runtime_command_uses_isolated_stress_port_and_registered_qwen36_runtime():
    command = NS["runtime_command"](8013)

    assert command[0] == "/Users/jerson/AI/runtime/omlx-venv/bin/omlx"
    assert command[1] == "serve"
    assert command[command.index("--port") + 1] == "8013"
    assert command[command.index("--memory-guard-gb") + 1] == "28"
    assert "8000" not in command
    assert "8001" not in command
    assert "8012" not in command


def test_unity_editor_detection_does_not_accept_unity_hub_only():
    target_present = NS["target_present"]

    hub = (
        "10 1000 /Applications/Unity Hub.app/Contents/MacOS/Unity Hub"
    )
    editor = (
        "11 1000 /Applications/Unity/Hub/Editor/6000.3.23f1/"
        "Unity.app/Contents/MacOS/Unity"
    )

    assert not target_present("UNITY_EDITOR", hub)
    assert target_present("UNITY_EDITOR", editor)


def test_ide_detection_accepts_vscode():
    assert NS["target_present"]("IDE", _raw(ide=True))
    assert not NS["target_present"]("IDE", _raw(ide=False))


def test_cold_start_requires_browser_and_target_already_present():
    validate = NS["validate_initial_workload"]
    Scenario = NS["Scenario"]

    good = _raw(browser=True, unity=True)
    validate(
        Scenario.STRESS_COLD_START,
        "UNITY_EDITOR",
        _manifest(good),
        good,
    )

    missing_target = _raw(browser=True, unity=False)
    try:
        validate(
            Scenario.STRESS_COLD_START,
            "UNITY_EDITOR",
            _manifest(missing_target),
            missing_target,
        )
    except RuntimeError as error:
        assert "must already be running" in str(error)
    else:
        raise AssertionError("cold start accepted without stress target")

    missing_browser = _raw(browser=False, unity=True)
    try:
        validate(
            Scenario.STRESS_COLD_START,
            "UNITY_EDITOR",
            _manifest(missing_browser),
            missing_browser,
        )
    except RuntimeError as error:
        assert "browser missing" in str(error)
    else:
        raise AssertionError("stress test accepted without browser")


def test_preloaded_scenario_requires_target_to_enter_after_model_load():
    validate = NS["validate_initial_workload"]
    Scenario = NS["Scenario"]

    clean = _raw(browser=True, unity=False)
    validate(
        Scenario.PRELOADED_COEXISTENCE,
        "UNITY_EDITOR",
        _manifest(clean),
        clean,
    )

    already_open = _raw(browser=True, unity=True)
    try:
        validate(
            Scenario.PRELOADED_COEXISTENCE,
            "UNITY_EDITOR",
            _manifest(already_open),
            already_open,
        )
    except RuntimeError as error:
        assert "already running" in str(error)
    else:
        raise AssertionError("preloaded entry test accepted already-running target")


def test_stress_manifest_rejects_occupied_fixed_port():
    validate = NS["validate_initial_workload"]
    Scenario = NS["Scenario"]
    raw = _raw(browser=True, ide=True)

    try:
        validate(
            Scenario.STRESS_COLD_START,
            "IDE",
            _manifest(raw, occupied=True),
            raw,
        )
    except RuntimeError as error:
        assert "fixed port already occupied" in str(error)
    else:
        raise AssertionError("occupied stress port was accepted")


class FakeProcess:
    pid = 4242

    def __init__(self):
        self.running = True
        self.returncode = None

    def poll(self):
        return None if self.running else self.returncode

    def wait(self, timeout):
        self.running = False
        self.returncode = 0
        return 0


def test_monitor_aborts_owned_runtime_on_relative_swap_growth():
    Monitor = NS["StressResourceMonitor"]
    process = FakeProcess()
    terminations = []

    monitor = Monitor(
        process,
        _snapshot(swap=1.0),
        "IDE",
        require_target=True,
        probe=lambda: _snapshot(swap=3.1),
        process_reader=lambda: _raw(browser=True, ide=True),
        terminate_owned=lambda p, graceful_timeout: terminations.append(
            (p.pid, graceful_timeout)
        ),
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation == "RELATIVE_SWAP_GROWTH_LIMIT"
    assert terminations == [(4242, 5.0)]
    assert monitor.samples[0].swap_delta_gib == 2.1


def test_monitor_marks_warning_without_stopping_user_or_model():
    Monitor = NS["StressResourceMonitor"]
    process = FakeProcess()
    terminations = []

    monitor = Monitor(
        process,
        _snapshot(swap=1.0),
        "IDE",
        require_target=True,
        probe=lambda: _snapshot(swap=1.1, pressure="WARNING"),
        process_reader=lambda: _raw(browser=True, ide=True),
        terminate_owned=lambda p, graceful_timeout: terminations.append(p.pid),
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation is None
    assert monitor.warning_observed is True
    assert terminations == []


def test_monitor_blocks_when_browser_or_required_target_disappears():
    Monitor = NS["StressResourceMonitor"]

    browser_lost = Monitor(
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=True,
        probe=lambda: _snapshot(),
        process_reader=lambda: _raw(browser=False, ide=True),
        terminate_owned=lambda *args, **kwargs: None,
    )
    browser_lost._started = 1.0
    with patch.object(NS["time"], "monotonic", return_value=2.0):
        browser_lost._record()
    assert browser_lost.violation == "BROWSER_WORKLOAD_LOST"

    target_lost = Monitor(
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=True,
        probe=lambda: _snapshot(),
        process_reader=lambda: _raw(browser=True, ide=False),
        terminate_owned=lambda *args, **kwargs: None,
    )
    target_lost._started = 1.0
    with patch.object(NS["time"], "monotonic", return_value=2.0):
        target_lost._record()
    assert target_lost.violation == "STRESS_TARGET_LOST"


def test_preloaded_monitor_can_wait_without_target_then_require_it():
    Monitor = NS["StressResourceMonitor"]
    state = {"target": False}

    monitor = Monitor(
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=False,
        probe=lambda: _snapshot(),
        process_reader=lambda: _raw(browser=True, ide=state["target"]),
        terminate_owned=lambda *args, **kwargs: None,
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()
    assert monitor.violation is None
    assert monitor.samples[-1].target_present is False

    state["target"] = True
    monitor.set_require_target(True)
    with patch.object(NS["time"], "monotonic", return_value=3.0):
        monitor._record()
    assert monitor.violation is None
    assert monitor.samples[-1].target_present is True
    assert monitor.target_entry_elapsed_seconds == 2.0


def test_monitored_health_gate_returns_resource_violation_instead_of_runtime_crash():
    wait_health = NS["wait_health_with_monitor"]
    process = FakeProcess()

    class Monitor:
        violation = "RELATIVE_SWAP_GROWTH_LIMIT"

    assert wait_health(process, Monitor(), 8013, 1.0) == "RELATIVE_SWAP_GROWTH_LIMIT"


def test_cleanup_signals_only_exact_owned_process_group():
    terminate = NS["terminate_process_group"]
    process = FakeProcess()
    signals = []

    with patch.object(NS["os"], "getpgid", return_value=4242):
        assert terminate(
            process,
            graceful_timeout=1.0,
            killpg=lambda pgid, sig: signals.append((pgid, sig)),
        )

    assert signals == [(4242, NS["signal"].SIGTERM)]


def test_cleanup_refuses_non_owned_process_group():
    terminate = NS["terminate_process_group"]

    with patch.object(NS["os"], "getpgid", return_value=9999):
        try:
            terminate(FakeProcess(), killpg=lambda *_: None)
        except RuntimeError as error:
            assert "not leader of its owned process group" in str(error)
        else:
            raise AssertionError("non-owned process group was signalled")


def test_source_starts_monitor_before_health_and_contains_no_user_app_control():
    source = SCRIPT.read_text(encoding="utf-8")
    lower = source.lower()

    create = source.index("monitor = StressResourceMonitor(")
    start = source.index("monitor.start()", create)
    health = source.index("load_violation = wait_health_with_monitor", start)
    first_task = source.index('monitor.set_phase("FIRST_FUNCTIONAL_TASK")', health)

    assert create < start < health < first_task
    assert "preflight_result.snapshot" in source[create:health]

    assert "osascript" not in lower
    assert "killall" not in lower
    assert "pkill" not in lower
    assert "open -a" not in lower
    assert "application \"google chrome\"" not in lower
    assert "application \"unity\"" not in lower
    assert "start_new_session=true" in lower
    assert "os.killpg" in lower
