import json
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.heavy_process_identity import normalize_executable
from local_ai_control.supervisor import process_identity


PID = 43123
START = "Mon Aug 24 20:00:00 2026"


def framework_root() -> str:
    root = normalize_executable(str(process_identity.CONTROL_PLANE_PYTHON))
    assert "/Python.framework/Versions/" in root
    return root


def framework_app() -> str:
    return framework_root() + "/Resources/Python.app/Contents/MacOS/Python"


def framework_bin() -> str:
    version = framework_root().rsplit("/", 1)[-1]
    return framework_root() + f"/bin/python{version}"


def observed(executable=None, argv0=None, start=START, tail=None):
    executable = executable or framework_app()
    argv0 = argv0 or executable
    tail = tuple(tail or ("-m", "local_ai_control.supervisor.app", "daemon"))
    return process_identity.ProcessIdentity(PID, executable, (argv0, *tail), start)


@pytest.mark.parametrize(
    ("executable", "argv0"),
    (
        (str(process_identity.CONTROL_PLANE_PYTHON), str(process_identity.CONTROL_PLANE_PYTHON)),
        (framework_app(), framework_app()),
        (framework_app(), framework_bin()),
    ),
)
def test_exact_spawn_accepts_literal_and_same_version_framework_forms(executable, argv0):
    assert process_identity._matches_expected_spawn(observed(executable, argv0))


@pytest.mark.parametrize(
    "identity",
    (
        observed(
            "/opt/test/Python.framework/Versions/999.0/Resources/Python.app/Contents/MacOS/Python",
            "/opt/test/Python.framework/Versions/999.0/Resources/Python.app/Contents/MacOS/Python",
        ),
        observed("/usr/bin/python3", "/usr/bin/python3"),
        observed("python", "python"),
        observed(tail=("-m", "unrelated.supervisor.app", "daemon")),
        observed(tail=("-m", "local_ai_control.supervisor.app", "status")),
        observed(tail=("-m", "local_ai_control.supervisor.app", "daemon", "extra")),
        observed(tail=("-m", "local_ai_control.supervisor.app")),
        observed(tail=("local_ai_control.supervisor.app", "-m", "daemon")),
    ),
)
def test_exact_spawn_rejects_wrong_version_interpreter_module_and_argv(identity):
    assert not process_identity._matches_expected_spawn(identity)


def fake_run_factory(*, proc_path=None, command=None, comm=None, start=START, calls=None):
    def run(argv, **_kwargs):
        if calls is not None:
            calls.append(tuple(argv))
        if argv[0] == "/usr/bin/proc_pidpath":
            if proc_path is None:
                raise FileNotFoundError("proc_pidpath unavailable")
            return SimpleNamespace(returncode=0, stdout=proc_path + "\n")
        if argv[-1] == "command=":
            return SimpleNamespace(returncode=0, stdout=(command or "") + "\n")
        if argv[-1] == "comm=":
            return SimpleNamespace(returncode=0, stdout=(comm or "") + "\n")
        if argv[-1] == "lstart=":
            return SimpleNamespace(returncode=0, stdout=start + "\n")
        raise AssertionError(argv)

    return run


def test_process_snapshot_uses_full_argv_and_independent_executable(monkeypatch):
    long_arg = "x" * 4_096
    command = f"{framework_app()} -m local_ai_control.supervisor.app daemon {long_arg}"
    calls = []
    monkeypatch.setattr(
        process_identity.subprocess,
        "run",
        fake_run_factory(proc_path=framework_app(), command=command, calls=calls),
    )
    snapshot = process_identity.process_snapshot(PID)
    assert snapshot.executable == framework_app()
    assert snapshot.argv == (
        framework_app(), "-m", "local_ai_control.supervisor.app", "daemon", long_arg,
    )
    assert snapshot.start_identity == START
    command_call = next(call for call in calls if call[-1] == "command=")
    assert command_call[:2] == ("/bin/ps", "-ww")
    assert not process_identity._matches_expected_spawn(snapshot)


def test_process_snapshot_falls_back_to_exact_comm_when_proc_pidpath_is_unavailable(monkeypatch):
    command = f"{framework_app()} -m local_ai_control.supervisor.app daemon"
    monkeypatch.setattr(
        process_identity.subprocess,
        "run",
        fake_run_factory(command=command, comm=framework_app()),
    )
    assert process_identity.process_snapshot(PID) == observed()


def test_process_snapshot_rejects_nonpositive_and_boolean_pid():
    assert process_identity.process_snapshot(0) is None
    assert process_identity.process_snapshot(-1) is None
    assert process_identity.process_snapshot(True) is None


def test_production_like_framework_capture_and_pid_reuse(monkeypatch, tmp_path):
    current = observed()
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: current)
    assert process_identity.classify_started_process(PID) == ("EXPECTED", START)
    assert process_identity.start_identity(PID) == START
    target = tmp_path / "supervisor.identity.json"
    captured = process_identity.capture(PID, target)
    assert captured == current
    assert process_identity.read_identity(target) == current
    assert process_identity.identity_status(target) == ("MATCH", PID)
    reused = observed(start="Mon Aug 24 21:00:00 2026")
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: reused)
    assert process_identity.identity_status(target) == ("MISMATCH", PID)


def test_identity_status_requires_exact_saved_observed_identity(monkeypatch, tmp_path):
    saved = observed()
    path = tmp_path / "identity.json"
    process_identity.write_identity(path, saved)
    changed_argv = observed(tail=("-m", "local_ai_control.supervisor.app", "status"))
    changed_executable = process_identity.ProcessIdentity(
        PID, framework_bin(), saved.argv, START,
    )
    for current in (
        observed(start="Mon Aug 24 21:00:00 2026"),
        changed_argv,
        changed_executable,
    ):
        monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid, value=current: value)
        assert process_identity.identity_status(path) == ("MISMATCH", PID)
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: None)
    assert process_identity.identity_status(path) == ("DEAD", PID)


def test_read_identity_accepts_only_exact_normalized_supervisor_signature(tmp_path):
    valid = observed()
    valid_path = tmp_path / "valid.json"
    process_identity.write_identity(valid_path, valid)
    assert process_identity.read_identity(valid_path) == valid

    for index, invalid in enumerate((
        observed(executable="/usr/bin/python3", argv0="/usr/bin/python3"),
        observed(tail=("-m", "local_ai_control.supervisor.app", "status")),
    )):
        path = tmp_path / f"invalid-{index}.json"
        payload = {
            "pid": invalid.pid,
            "executable": invalid.executable,
            "argv": list(invalid.argv),
            "start_identity": invalid.start_identity,
        }
        path.write_text(json.dumps(payload))
        path.chmod(0o600)
        with pytest.raises(ValueError, match="does not match supervisor signature"):
            process_identity.read_identity(path)
        assert process_identity.identity_status(path) == ("INVALID", None)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.update(pid=True),
        lambda payload: payload.update(executable=7),
        lambda payload: payload.update(argv="not-a-list"),
        lambda payload: payload.update(argv=[*payload["argv"], 7]),
        lambda payload: payload.update(start_identity=7),
        lambda payload: payload.update(extra="field"),
    ),
)
def test_read_identity_rejects_schema_type_coercion(tmp_path, mutation):
    current = observed()
    payload = {
        "pid": current.pid,
        "executable": current.executable,
        "argv": list(current.argv),
        "start_identity": current.start_identity,
    }
    mutation(payload)
    path = tmp_path / "invalid-schema.json"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    with pytest.raises(ValueError, match="invalid process identity schema"):
        process_identity.read_identity(path)


def test_capture_atomic_private_file_persists_actual_observation(monkeypatch, tmp_path):
    current = observed()
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: current)
    target = tmp_path / "private" / "identity.json"
    assert process_identity.capture(PID, target) == current
    assert process_identity.read_identity(target) == current
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not list(target.parent.glob("*.tmp"))


def test_cleanup_signals_only_exact_attempt_owned_child(monkeypatch, tmp_path):
    current = observed()
    path = tmp_path / "identity.json"
    process_identity.write_identity(path, current)
    snapshots = iter((current, None))
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: next(snapshots, None))
    signals = []
    monkeypatch.setattr(process_identity.os, "kill", lambda *args: signals.append(args))
    assert process_identity.cleanup_started_process(PID, START, path, 0.2) == "TERMINATED"
    assert signals == [(PID, signal.SIGTERM)]


@pytest.mark.parametrize(
    ("current", "attempt_start"),
    (
        (observed(), "WRONG-START"),
        (observed(tail=("-m", "local_ai_control.supervisor.app", "status")), START),
        (observed(executable="/usr/bin/python3", argv0="/usr/bin/python3"), START),
    ),
)
def test_cleanup_refuses_wrong_start_argv_or_executable(monkeypatch, tmp_path, current, attempt_start):
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: current)
    signals = []
    monkeypatch.setattr(process_identity.os, "kill", lambda *args: signals.append(args))
    assert process_identity.cleanup_started_process(
        PID, attempt_start, tmp_path / "missing.json", 0.01,
    ) == "ORPHAN_RECONCILIATION_REQUIRED"
    assert signals == []


def test_stale_dead_and_invalid_identity_remain_launcher_compatible(monkeypatch, tmp_path):
    valid = tmp_path / "valid.json"
    process_identity.write_identity(valid, observed())
    monkeypatch.setattr(process_identity, "process_snapshot", lambda _pid: None)
    assert process_identity.identity_status(valid) == ("DEAD", PID)
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}")
    invalid.chmod(0o600)
    assert process_identity.identity_status(invalid) == ("INVALID", None)


def test_no_broad_or_weak_process_matching_was_introduced():
    source = Path(process_identity.__file__).read_text()
    scripts = "\n".join(
        (Path("/Users/jerson/AI/control-plane/scripts") / name).read_text()
        for name in ("start-supervisor.sh", "status-supervisor.sh", "stop-supervisor.sh")
    )
    combined = source + scripts
    assert "pgrep" not in combined
    assert "pkill" not in combined
    assert "killall" not in combined
    assert "substring" not in source.lower()
    assert "basename" not in source.lower()
