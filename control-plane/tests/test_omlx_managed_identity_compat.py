from pathlib import Path
import os
import plistlib
from types import SimpleNamespace

import pytest

from local_ai_control.services.heavy_process_identity import (
    ProcessIdentity,
    identity_status,
    read_identity,
    write_identity,
)
from local_ai_control.services.models import QWEN36, QWEN38
from local_ai_control.services.runtime_providers import (
    HeavyModelConflict,
    LaunchdHeavyRuntimeLifecycle,
)


class LaunchdRunner:
    def __init__(self, state):
        self.state = state
        self.outputs = {}
        self.events = []
        self.on_bootout = None
        self.on_print = None
        self.print_counts = {}

    def __call__(self, argv, **_kwargs):
        self.events.append(tuple(argv))
        action = argv[1]
        label = argv[-1].rsplit("/", 1)[-1]
        if action == "print":
            output = self.outputs.get(label)
            if isinstance(output, list):
                output = output.pop(0) if len(output) > 1 else output[0]
            self.print_counts[label] = self.print_counts.get(label, 0) + 1
            if self.on_print:
                self.on_print(label, self.print_counts[label])
            return SimpleNamespace(returncode=0 if output is not None else 1, stdout=output or "")
        if action == "bootout":
            if self.on_bootout:
                self.on_bootout(label)
            self.outputs.pop(label, None)
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="")


def lifecycle_fixture(tmp_path):
    state = {"ports": {8000: (), 8001: ()}, "snapshots": {}}
    runner = LaunchdRunner(state)
    lifecycle = LaunchdHeavyRuntimeLifecycle(
        tmp_path,
        sleep=lambda _delay: None,
        runner=runner,
        snapshot=lambda pid: state["snapshots"].get(pid),
        listeners=lambda port: tuple(state["ports"].get(port, ())),
    )
    return lifecycle, runner, state


def posttitle(pid=47736, start="Mon Aug 24 14:40:37 2026"):
    return ProcessIdentity(pid, "/Users/jerson/AI/omlx-server", ("omlx-server",), start)


def exact_identity(lifecycle, profile_id, pid=41, start="START"):
    executable, argv = lifecycle._process_signature(profile_id)
    return ProcessIdentity(pid, executable, argv, start)


def job_output(lifecycle, profile_id, pid, *, pids=None, header=None, path=None, program=None):
    label = lifecycle.labels[profile_id]
    domain = f"gui/{lifecycle.uid}/{label}"
    pid_lines = [pid] if pids is None else pids
    return "\n".join(
        [
            f"{header or domain} = {{",
            f"\tpath = {path or lifecycle.runtime_root / f'{label}.plist'}",
            "\tstate = running",
            f"\tprogram = {program or lifecycle._launch_args(profile_id)[0]}",
            *(f"\tpid = {item}" for item in pid_lines),
            "}",
        ]
    )


def prepare_posttitle(tmp_path, *, pid=47736):
    lifecycle, runner, state = lifecycle_fixture(tmp_path)
    lifecycle._plist(QWEN36.profile_id)
    observed = posttitle(pid)
    state["ports"][8000] = (pid,)
    state["snapshots"][pid] = observed
    runner.outputs[lifecycle.labels[QWEN36.profile_id]] = job_output(
        lifecycle, QWEN36.profile_id, pid
    )
    return lifecycle, runner, state, observed


def assert_capture_rejected(lifecycle):
    identity_path = lifecycle._identity_path(QWEN36.profile_id)
    with pytest.raises(HeavyModelConflict):
        lifecycle.capture_started(QWEN36.profile_id)
    assert not identity_path.exists()


def rewrite_plist(path, transform):
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    transform(payload)
    with path.open("wb") as handle:
        plistlib.dump(payload, handle)


def test_qwen38_capture_behavior_remains_exact(tmp_path):
    lifecycle, _runner, state = lifecycle_fixture(tmp_path)
    exact = exact_identity(lifecycle, QWEN38.profile_id, pid=81)
    state["ports"][8001] = (81,)
    state["snapshots"][81] = exact
    assert lifecycle.capture_started(QWEN38.profile_id) == exact
    assert read_identity(lifecycle._identity_path(QWEN38.profile_id)) == exact

    lifecycle._identity_path(QWEN38.profile_id).unlink()
    state["snapshots"][81] = posttitle(81)
    with pytest.raises(HeavyModelConflict):
        lifecycle.capture_started(QWEN38.profile_id)
    assert not lifecycle._identity_path(QWEN38.profile_id).exists()


def test_qwen36_original_exact_spawn_identity_is_still_accepted(tmp_path):
    lifecycle, _runner, state = lifecycle_fixture(tmp_path)
    exact = exact_identity(lifecycle, QWEN36.profile_id, pid=82)
    state["ports"][8000] = (82,)
    state["snapshots"][82] = exact
    assert lifecycle.capture_started(QWEN36.profile_id) == exact
    assert read_identity(lifecycle._identity_path(QWEN36.profile_id)) == exact


def test_real_mac_posttitle_capture_binds_launchd_pid_and_saves_observed_identity(tmp_path):
    lifecycle, _runner, _state, observed = prepare_posttitle(tmp_path)
    captured = lifecycle.capture_started(QWEN36.profile_id)
    assert captured == observed
    assert read_identity(lifecycle._identity_path(QWEN36.profile_id)) == observed
    assert identity_status(
        lifecycle._identity_path(QWEN36.profile_id), snapshot=lifecycle.snapshot
    ) == ("MATCH", observed.pid)


def test_saved_posttitle_identity_remains_exact_and_safe_stop_uses_label(tmp_path):
    lifecycle, runner, state, observed = prepare_posttitle(tmp_path)
    lifecycle.capture_started(QWEN36.profile_id)

    def stopped(label):
        assert label == lifecycle.labels[QWEN36.profile_id]
        state["ports"][8000] = ()
        state["snapshots"].pop(observed.pid, None)

    runner.on_bootout = stopped
    assert lifecycle.safe_stop(QWEN36.profile_id, lambda: False) == "STOP_REQUESTED"
    lifecycle.wait_stopped(QWEN36.profile_id, lambda: False)
    assert identity_status(
        lifecycle._identity_path(QWEN36.profile_id), snapshot=lifecycle.snapshot
    )[0] == "DEAD"
    assert any(event[1] == "bootout" for event in runner.events)


def test_arbitrary_omlx_title_without_launchd_label_is_rejected(tmp_path):
    lifecycle, runner, _state, _observed = prepare_posttitle(tmp_path)
    runner.outputs.clear()
    assert_capture_rejected(lifecycle)


def test_launchd_job_pid_must_equal_listener_pid(tmp_path):
    lifecycle, runner, _state, observed = prepare_posttitle(tmp_path)
    runner.outputs[lifecycle.labels[QWEN36.profile_id]] = job_output(
        lifecycle, QWEN36.profile_id, observed.pid + 1
    )
    assert_capture_rejected(lifecycle)


@pytest.mark.parametrize("pids", [[], [47736, 47737]])
def test_launchd_pid_parser_rejects_missing_or_ambiguous_pid(tmp_path, pids):
    lifecycle, runner, _state, observed = prepare_posttitle(tmp_path)
    runner.outputs[lifecycle.labels[QWEN36.profile_id]] = job_output(
        lifecycle, QWEN36.profile_id, observed.pid, pids=pids
    )
    assert_capture_rejected(lifecycle)


def test_launchd_details_require_exact_label_header_path_and_program(tmp_path):
    for field in ("header", "path", "program"):
        case = tmp_path / field
        case.mkdir()
        lifecycle, runner, _state, observed = prepare_posttitle(case)
        changes = {
            "header": {"header": f"gui/{lifecycle.uid}/foreign.label"},
            "path": {"path": case / "foreign.plist"},
            "program": {"program": "/foreign/program"},
        }[field]
        runner.outputs[lifecycle.labels[QWEN36.profile_id]] = job_output(
            lifecycle, QWEN36.profile_id, observed.pid, **changes
        )
        assert_capture_rejected(lifecycle)


def test_multiple_8000_listeners_are_rejected_before_identity_write(tmp_path):
    lifecycle, _runner, state, _observed = prepare_posttitle(tmp_path)
    state["ports"][8000] = (47736, 47737)
    assert_capture_rejected(lifecycle)


@pytest.mark.parametrize("field", ["Label", "ProgramArguments", "WorkingDirectory"])
def test_managed_plist_fields_must_match_exactly(tmp_path, field):
    lifecycle, _runner, _state, _observed = prepare_posttitle(tmp_path)
    path = lifecycle.runtime_root / f"{lifecycle.labels[QWEN36.profile_id]}.plist"

    def alter(payload):
        if field == "ProgramArguments":
            payload[field] = [*payload[field], "--unexpected"]
        else:
            payload[field] = "unexpected"

    rewrite_plist(path, alter)
    assert_capture_rejected(lifecycle)


def test_symlink_and_malformed_plist_are_rejected(tmp_path):
    symlink_case = tmp_path / "symlink"
    symlink_case.mkdir()
    lifecycle, _runner, _state, _observed = prepare_posttitle(symlink_case)
    path = lifecycle.runtime_root / f"{lifecycle.labels[QWEN36.profile_id]}.plist"
    target = symlink_case / "outside.plist"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    assert_capture_rejected(lifecycle)

    malformed_case = tmp_path / "malformed"
    malformed_case.mkdir()
    lifecycle, _runner, _state, _observed = prepare_posttitle(malformed_case)
    path = lifecycle.runtime_root / f"{lifecycle.labels[QWEN36.profile_id]}.plist"
    path.write_bytes(b"not a plist")
    assert_capture_rejected(lifecycle)


def test_qwen38_listener_or_managed_service_conflict_is_rejected(tmp_path):
    listener_case = tmp_path / "listener"
    listener_case.mkdir()
    lifecycle, _runner, state, _observed = prepare_posttitle(listener_case)
    qwen38 = exact_identity(lifecycle, QWEN38.profile_id, pid=90)
    state["snapshots"][90] = qwen38
    state["ports"][8001] = (90,)
    write_identity(lifecycle._identity_path(QWEN38.profile_id), qwen38)
    assert_capture_rejected(lifecycle)

    service_case = tmp_path / "service"
    service_case.mkdir()
    lifecycle, runner, _state, _observed = prepare_posttitle(service_case)
    runner.outputs[lifecycle.labels[QWEN38.profile_id]] = "present"
    assert_capture_rejected(lifecycle)


def test_posttitle_pid_reuse_changes_exact_saved_identity_to_mismatch(tmp_path):
    lifecycle, _runner, state, observed = prepare_posttitle(tmp_path)
    lifecycle.capture_started(QWEN36.profile_id)
    state["snapshots"][observed.pid] = ProcessIdentity(
        observed.pid, observed.executable, observed.argv, "REUSED"
    )
    assert identity_status(
        lifecycle._identity_path(QWEN36.profile_id), snapshot=lifecycle.snapshot
    ) == ("MISMATCH", observed.pid)


def test_launchd_pid_change_during_capture_is_rejected_without_identity_write(tmp_path):
    lifecycle, runner, _state, observed = prepare_posttitle(tmp_path)
    label = lifecycle.labels[QWEN36.profile_id]
    runner.outputs[label] = [
        job_output(lifecycle, QWEN36.profile_id, observed.pid),
        job_output(lifecycle, QWEN36.profile_id, observed.pid + 1),
    ]
    assert_capture_rejected(lifecycle)


def test_qwen38_appearance_at_capture_commit_boundary_is_rejected(tmp_path):
    lifecycle, runner, state, _observed = prepare_posttitle(tmp_path)
    qwen36_label = lifecycle.labels[QWEN36.profile_id]
    qwen38 = exact_identity(lifecycle, QWEN38.profile_id, pid=99)

    def appear(label, count):
        if label == qwen36_label and count == 2:
            state["ports"][8001] = (qwen38.pid,)
            state["snapshots"][qwen38.pid] = qwen38

    runner.on_print = appear
    assert_capture_rejected(lifecycle)


def test_safe_stop_still_refuses_unknown_listener_and_source_has_no_pid_signal(tmp_path):
    lifecycle, runner, state = lifecycle_fixture(tmp_path)
    state["ports"][8000] = (91,)
    state["snapshots"][91] = posttitle(91)
    with pytest.raises(HeavyModelConflict):
        lifecycle.safe_stop(QWEN36.profile_id, lambda: True)
    assert not any(event[1] == "bootout" for event in runner.events)
    source = Path(__import__("local_ai_control.services.runtime_providers", fromlist=["x"]).__file__).read_text()
    assert "os.kill" not in source and "pkill" not in source and "killall" not in source
