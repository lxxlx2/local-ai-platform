import runpy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

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
            "3 3145728 /Applications/Unity/Hub/Editor/6000.3.23f1/"
            "Unity.app/Contents/MacOS/Unity"
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


class FakeProcess:
    pid = 4242

    def __init__(self, *, running=True, returncode=None):
        self.running = running
        self.returncode = returncode

    def poll(self):
        return None if self.running else self.returncode

    def wait(self, timeout):
        self.running = False
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


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
    hub = "10 1000 /Applications/Unity Hub.app/Contents/MacOS/Unity Hub"
    editor = (
        "11 1000 /Applications/Unity/Hub/Editor/6000.3.23f1/"
        "Unity.app/Contents/MacOS/Unity"
    )

    assert not target_present("UNITY_EDITOR", hub)
    assert target_present("UNITY_EDITOR", editor)


def test_ide_detection_accepts_vscode():
    assert NS["target_present"]("IDE", _raw(ide=True))
    assert not NS["target_present"]("IDE", _raw(ide=False))


def test_process_probe_uses_executable_column_not_full_argv():
    with patch.object(NS["subprocess"], "check_output", return_value="") as check:
        NS["process_text"]()

    assert check.call_args.args[0] == ["ps", "ax", "-o", "pid=,rss=,comm="]


def test_workload_detection_rejects_app_path_mentioned_only_in_argv():
    unrelated = (
        "9 1000 /bin/zsh -c echo "
        "/Applications/Unity/Hub/Editor/6000.3.23f1/Unity.app/Contents/MacOS/Unity"
    )

    # Detection receives executable-only process snapshots in production.  An
    # argv-shaped fixture documents the adversarial false-positive that the
    # process probe must exclude before this helper is called.
    assert NS["TARGET_PATTERNS"]["UNITY_EDITOR"][0] in unrelated
    with patch.object(
        NS["subprocess"],
        "check_output",
        return_value="9 1000 /bin/zsh\n",
    ):
        assert not NS["target_present"]("UNITY_EDITOR")


def test_cold_start_requires_browser_and_target_already_present():
    validate = NS["validate_initial_workload"]
    Scenario = NS["Scenario"]
    good = _raw(browser=True, unity=True)
    validate(Scenario.STRESS_COLD_START, "UNITY_EDITOR", _manifest(good), good)

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
    validate(Scenario.PRELOADED_COEXISTENCE, "UNITY_EDITOR", _manifest(clean), clean)

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
        validate(Scenario.STRESS_COLD_START, "IDE", _manifest(raw, occupied=True), raw)
    except RuntimeError as error:
        assert "fixed port already occupied" in str(error)
    else:
        raise AssertionError("occupied stress port was accepted")


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
    terminations = []
    monitor = Monitor(
        FakeProcess(),
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


def test_monitor_stop_fails_closed_if_sampler_thread_does_not_exit():
    class StuckThread:
        def join(self, timeout):
            self.timeout = timeout

        def is_alive(self):
            return True

    terminations = []
    monitor = NS["StressResourceMonitor"](
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=True,
        terminate_owned=lambda process, graceful_timeout: terminations.append(
            (process.pid, graceful_timeout)
        ),
    )
    monitor._thread = StuckThread()

    assert monitor.stop() is False
    assert monitor.violation == "RESOURCE_OBSERVATION_FAILED"
    assert terminations == [(4242, 5.0)]


def test_monitor_stop_preserves_existing_hard_violation():
    class StuckThread:
        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    terminations = []
    monitor = NS["StressResourceMonitor"](
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=True,
        terminate_owned=lambda *args, **kwargs: terminations.append(True),
    )
    monitor.violation = "RELATIVE_SWAP_GROWTH_LIMIT"
    monitor._thread = StuckThread()

    assert monitor.stop() is False
    assert monitor.violation == "RELATIVE_SWAP_GROWTH_LIMIT"
    assert terminations == []


def test_monitor_run_preserves_resource_reason_if_owned_cleanup_raises():
    monitor = NS["StressResourceMonitor"](
        FakeProcess(),
        _snapshot(swap=1.0),
        "IDE",
        require_target=True,
        probe=lambda: _snapshot(swap=3.1),
        process_reader=lambda: _raw(browser=True, ide=True),
        terminate_owned=lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic cleanup failure")
        ),
    )
    monitor._started = 1.0

    monitor._run()

    assert monitor.violation == "RELATIVE_SWAP_GROWTH_LIMIT"


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


def test_preloaded_monitor_records_target_seen_before_workload_window():
    Monitor = NS["StressResourceMonitor"]
    monitor = Monitor(
        FakeProcess(),
        _snapshot(),
        "IDE",
        require_target=False,
        probe=lambda: _snapshot(),
        process_reader=lambda: _raw(browser=True, ide=True),
        terminate_owned=lambda *args, **kwargs: None,
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation is None
    assert monitor.target_seen_before_workload_window is True


def test_health_gate_preserves_resource_violation():
    wait_health = NS["wait_health_with_monitor"]

    class Monitor:
        violation = "RELATIVE_SWAP_GROWTH_LIMIT"

    assert (
        wait_health(FakeProcess(), Monitor(), 8013, 1.0)
        == "RELATIVE_SWAP_GROWTH_LIMIT"
    )


def test_health_gate_returns_runtime_exit_reason_instead_of_raising():
    wait_health = NS["wait_health_with_monitor"]

    class Monitor:
        violation = None

    reason = wait_health(
        FakeProcess(running=False, returncode=7),
        Monitor(),
        8013,
        1.0,
    )
    assert reason == "RUNTIME_EXITED_BEFORE_HEALTH:7"


def test_target_wait_preserves_monitor_violation_and_runtime_exit():
    wait_target = NS["wait_for_target_entry"]

    class Monitor:
        def __init__(self, violation=None):
            self.violation = violation

        def set_require_target(self, required):
            pass

        def set_phase(self, phase):
            pass

    assert (
        wait_target(
            FakeProcess(),
            Monitor("RESOURCE_OBSERVATION_FAILED"),
            "IDE",
            1.0,
        )
        == "RESOURCE_OBSERVATION_FAILED"
    )
    assert (
        wait_target(
            FakeProcess(running=False, returncode=9),
            Monitor(),
            "IDE",
            1.0,
        )
        == "RUNTIME_EXITED_WAITING_FOR_TARGET:9"
    )
    assert wait_target(FakeProcess(), Monitor(), "IDE", 0.0) == "TARGET_WORKLOAD_NOT_OBSERVED"


def test_target_wait_rejects_target_already_present_at_window_boundary():
    wait_target = NS["wait_for_target_entry"]

    class Monitor:
        violation = None

    with patch.object(NS["subprocess"], "check_output", return_value=_raw(ide=True)):
        assert (
            wait_target(FakeProcess(), Monitor(), "IDE", 1.0)
            == "TARGET_PRESENT_BEFORE_WORKLOAD_WINDOW"
        )


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


def test_process_group_proof_classifies_child_exit_before_health():
    establish = NS["establish_owned_process_group"]
    process = FakeProcess(running=False, returncode=23)

    with patch.object(NS["os"], "getpgid", side_effect=ProcessLookupError):
        try:
            establish(process)
        except NS["RuntimeExitedBeforeHealth"] as error:
            assert str(error) == "RUNTIME_EXITED_BEFORE_HEALTH:23"
        else:
            raise AssertionError("early child exit was not classified explicitly")


def test_main_rejects_weakened_port_or_sustain_duration():
    main = NS["main"]

    with pytest.raises(SystemExit, match="isolated port 8013"):
        main(["--scenario", "STRESS_COLD_START", "--target", "IDE", "--qualification-port", "9000"])
    with pytest.raises(SystemExit, match="at least 60 sustain seconds"):
        main(["--scenario", "STRESS_COLD_START", "--target", "IDE", "--sustain-seconds", "0"])


def test_initial_workload_rejection_writes_parseable_result(tmp_path):
    manifest = _manifest(_raw(browser=False, ide=True))

    class FakeProbe:
        def __init__(self, **kwargs):
            pass

        def capture(self, workload_class):
            return manifest

    class ForbiddenPreflight:
        def check(self, *_):
            raise AssertionError("preflight must not run after initial rejection")

    fake_omlx = tmp_path / "omlx"
    fake_omlx.touch()
    output = tmp_path / "result-dir"
    with patch.dict(
        NS["main"].__globals__,
        {
            "OMLX_BIN": fake_omlx,
            "process_text": lambda: _raw(browser=False, ide=True),
            "WorkloadManifestProbe": FakeProbe,
            "MemoryPreflight": ForbiddenPreflight,
            "ports_clear": lambda ports: True,
        },
    ):
        returncode = NS["main"](
            [
                "--scenario",
                "STRESS_COLD_START",
                "--target",
                "IDE",
                "--output-dir",
                str(output),
                "--sustain-seconds",
                "60",
            ]
        )

    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert returncode == 1
    assert result["verdict"] == "NO_STRESS_EVIDENCE"
    assert result["reason"] == "BROWSER_MISSING_AT_INITIAL_VALIDATION"
    assert result["cleanup_ok"] is True


def test_source_uses_fresh_load_baseline_before_health_and_no_user_app_control():
    source = SCRIPT.read_text(encoding="utf-8")
    lower = source.lower()

    refresh = source.index("load_preflight_result = MemoryPreflight().check")
    spawn = source.index("process = subprocess.Popen", refresh)
    create = source.index("monitor = StressResourceMonitor(", spawn)
    baseline = source.index("load_preflight_result.snapshot", create)
    start = source.index("monitor.start()", baseline)
    health = source.index("load_failure = wait_health_with_monitor", start)
    first_task = source.index('monitor.set_phase("FIRST_FUNCTIONAL_TASK")', health)

    assert refresh < spawn < create < baseline < start < health < first_task
    assert "\n                            preflight_result.snapshot," not in source[create:start]
    assert "fresh_raw = process_text()" in source[:refresh]
    assert "TARGET_PRESENT_BEFORE_MODEL_LOAD" in source[:refresh]

    assert "osascript" not in lower
    assert "killall" not in lower
    assert "pkill" not in lower
    assert "open -a" not in lower
    assert "application \"google chrome\"" not in lower
    assert "application \"unity\"" not in lower
    assert "start_new_session=true" in lower
    assert "os.killpg" in lower
