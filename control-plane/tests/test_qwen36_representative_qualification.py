import runpy
from pathlib import Path
from unittest.mock import patch

from local_ai_control.services.models import MemorySnapshot
from local_ai_control.services.workload_admission import WorkloadClass, WorkloadManifestProbe


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "qualify-qwen36-representative.py"
)
NS = runpy.run_path(str(SCRIPT))


def _snapshot(*, reclaimable=34.0, swap=1.5, pressure="NORMAL"):
    return MemorySnapshot(
        total_gib=48.0,
        available_gib=reclaimable,
        swap_used_gib=swap,
        pressure=pressure,
        reclaimable_gib=reclaimable,
    )


def _representative_manifest(*, browser=True, occupied_port=False):
    rows = ["101 1048576 /Applications/Terminal.app/Contents/MacOS/Terminal"]
    if browser:
        rows.append(
            "102 2097152 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    return WorkloadManifestProbe(
        process_reader=lambda: "\n".join(rows),
        memory_probe=lambda: _snapshot(),
        listeners=(lambda port: (777,) if occupied_port and port == 8012 else ()),
        ports=(8000, 8001, 8011, 8012),
    ).capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)


def test_runtime_command_is_qualification_only_and_reuses_registered_qwen36_runtime():
    command = NS["runtime_command"](8012)

    assert command[0] == "/Users/jerson/AI/runtime/omlx-venv/bin/omlx"
    assert command[1] == "serve"
    assert command[command.index("--port") + 1] == "8012"
    assert command[command.index("--memory-guard-gb") + 1] == "28"
    assert "8000" not in command
    assert "8001" not in command


def test_browser_detection_is_observation_only():
    browser_present = NS["browser_present"]

    assert browser_present(
        "1 10 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    assert not browser_present("1 10 /Applications/Terminal.app/Contents/MacOS/Terminal")


def test_representative_manifest_requires_browser_and_clear_fixed_ports():
    validate = NS["validate_representative_manifest"]

    validate(_representative_manifest())

    try:
        validate(_representative_manifest(browser=False))
    except RuntimeError as error:
        assert "lost the browser" in str(error)
    else:
        raise AssertionError("browser-free workload was accepted as representative")

    try:
        validate(_representative_manifest(occupied_port=True))
    except RuntimeError as error:
        assert "fixed port already occupied" in str(error)
    else:
        raise AssertionError("occupied qualification port was accepted")


def test_resource_monitor_aborts_owned_runtime_on_relative_swap_growth_only():
    ResourceMonitor = NS["ResourceMonitor"]

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    terminations = []
    monitor = ResourceMonitor(
        FakeProcess(),
        _snapshot(swap=1.0),
        probe=lambda: _snapshot(swap=3.1),
        browser_check=lambda: True,
        terminate_owned=lambda process, graceful_timeout: terminations.append(
            (process.pid, graceful_timeout)
        ),
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation == "RELATIVE_SWAP_GROWTH_LIMIT"
    assert terminations == [(4242, 5.0)]
    assert monitor.samples[0].swap_delta_gib == 2.1


def test_resource_monitor_records_warning_without_killing_user_workload():
    ResourceMonitor = NS["ResourceMonitor"]

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    terminations = []
    monitor = ResourceMonitor(
        FakeProcess(),
        _snapshot(swap=1.0),
        probe=lambda: _snapshot(swap=1.1, pressure="WARNING"),
        browser_check=lambda: True,
        terminate_owned=lambda process, graceful_timeout: terminations.append(process.pid),
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation is None
    assert monitor.warning_observed is True
    assert terminations == []


def test_resource_monitor_fails_if_representative_browser_disappears():
    ResourceMonitor = NS["ResourceMonitor"]

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

    terminations = []
    monitor = ResourceMonitor(
        FakeProcess(),
        _snapshot(),
        probe=lambda: _snapshot(),
        browser_check=lambda: False,
        terminate_owned=lambda process, graceful_timeout: terminations.append(process.pid),
    )
    monitor._started = 1.0

    with patch.object(NS["time"], "monotonic", return_value=2.0):
        monitor._record()

    assert monitor.violation == "REPRESENTATIVE_BROWSER_LOST"
    assert terminations == [4242]


def test_cleanup_signals_only_the_owned_process_group():
    terminate = NS["terminate_process_group"]

    class FakeProcess:
        pid = 5151

        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def wait(self, timeout):
            self.running = False
            return 0

    process = FakeProcess()
    signals = []

    with patch.object(NS["os"], "getpgid", return_value=5151):
        assert terminate(
            process,
            graceful_timeout=1.0,
            killpg=lambda pgid, sig: signals.append((pgid, sig)),
        )

    assert signals == [(5151, NS["signal"].SIGTERM)]


def test_cleanup_rejects_non_owned_process_group():
    terminate = NS["terminate_process_group"]

    class FakeProcess:
        pid = 5151

        def poll(self):
            return None

    with patch.object(NS["os"], "getpgid", return_value=9999):
        try:
            terminate(FakeProcess(), killpg=lambda *_: None)
        except RuntimeError as error:
            assert "not leader of its owned process group" in str(error)
        else:
            raise AssertionError("non-owned process group was signalled")


def test_harness_contains_no_user_application_control_commands():
    source = SCRIPT.read_text(encoding="utf-8").lower()

    assert "osascript" not in source
    assert "killall" not in source
    assert "pkill" not in source
    assert "application \"google chrome\" quit" not in source
    assert "application \"unity\" quit" not in source
    assert "start_new_session=true" in source
    assert "os.killpg" in source
