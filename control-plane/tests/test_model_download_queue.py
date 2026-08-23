import fcntl
import json
from pathlib import Path
import plistlib

from local_ai_control.services.model_downloads import (
    LABEL,
    ModelDownloadQueue,
    bounded_status,
    load_queue_config,
    storage_bytes,
    write_launch_plist,
)


def make_config(tmp_path: Path, *, count=2, attempts=3):
    models_root = tmp_path / "models"
    models_root.mkdir()
    rows = []
    for index in range(count):
        rows.append({
            "id": f"model-{index}", "role": "TEST", "repo": f"org/model-{index}",
            "revision": f"{index + 1:040x}", "local_dir": str(models_root / f"model-{index}"),
            "expected_bytes": 10, "license": "test", "runtime": "test",
        })
    path = tmp_path / "queue.json"
    path.write_text(json.dumps({"serial_only": True, "max_attempts": attempts, "reserve_bytes": 0, "models": rows}))
    return load_queue_config(path, models_root=models_root), models_root


def complete_file(spec, value=b"0123456789"):
    spec.local_dir.mkdir(parents=True, exist_ok=True)
    (spec.local_dir / "weights.bin").write_bytes(value)


def test_production_config_is_pinned_serial_and_has_eight_models():
    config = load_queue_config()
    assert len(config.models) == 8
    assert len({item.id for item in config.models}) == 8
    assert all(len(item.revision) == 40 for item in config.models)
    assert config.models[-1].include == ("8-bit/*",)


def test_serial_order_partial_resume_and_completed_skip(tmp_path):
    config, _ = make_config(tmp_path)
    events = []
    first = config.models[0]
    first.local_dir.mkdir()
    (first.local_dir / "partial.bin").write_bytes(b"12345")

    def download(spec, _log):
        events.append((spec.id, sum(path.stat().st_size for path in spec.local_dir.glob("*"))))
        complete_file(spec)
        return 0

    runner = ModelDownloadQueue(config, tmp_path / "runtime", downloader=download, sleeper=lambda _: None)
    assert runner.run() == "COMPLETED"
    assert events == [("model-0", 5), ("model-1", 0)]
    events.clear()
    assert runner.run() == "COMPLETED"
    assert events == []


def test_retry_is_bounded_and_failure_continues_serially(tmp_path):
    config, _ = make_config(tmp_path, attempts=2)
    calls = []

    def download(spec, _log):
        calls.append(spec.id)
        if spec.id == "model-0":
            return 7
        complete_file(spec)
        return 0

    runner = ModelDownloadQueue(config, tmp_path / "runtime", downloader=download, sleeper=lambda _: None)
    assert runner.run() == "COMPLETED_WITH_FAILURES"
    state = json.loads(runner.state_path.read_text())
    assert calls == ["model-0", "model-0", "model-1"]
    assert state["failed"] == ["model-0"]
    assert state["completed"] == ["model-1"]
    assert state["models"]["model-0"]["retry_count"] == 1


def test_singleton_lock_refuses_second_runner(tmp_path):
    config, _ = make_config(tmp_path, count=1)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    lock = (runtime / "queue.lock").open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        runner = ModelDownloadQueue(config, runtime, downloader=lambda *_: 0, sleeper=lambda _: None)
        assert runner.run() == "ALREADY_RUNNING"
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def test_status_is_bounded_and_secret_free(tmp_path):
    config, _ = make_config(tmp_path, count=1)
    runtime = tmp_path / "runtime"

    def download(spec, log):
        complete_file(spec)
        log.write_text("safe downloader event\n")
        return 0

    runner = ModelDownloadQueue(config, runtime, downloader=download, sleeper=lambda _: None)
    runner.run()
    for number in range(9):
        runner._log(f"EVENT_{number}")
    output = bounded_status(runtime)
    assert len(output.splitlines()) < 35
    assert "EVENT_3" not in output and "EVENT_8" in output
    assert not any(word in (runtime / "state.json").read_text().lower() for word in ("authorization", "cookie", "credential"))


def test_partial_cache_is_separate_and_cannot_complete_snapshot(tmp_path):
    config,_=make_config(tmp_path,count=1); spec=config.models[0]
    cache=spec.local_dir/".cache/huggingface/download"; cache.mkdir(parents=True)
    (cache/"weights.incomplete").write_bytes(b"x"*100)
    sizes=storage_bytes(spec.local_dir)
    assert sizes.payload_bytes==0 and sizes.partial_cache_bytes==100
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=lambda *_:0,sleeper=lambda _:None)
    runner._write_marker(spec)
    assert not runner._is_complete(spec)


def test_indexed_snapshot_requires_every_referenced_shard(tmp_path):
    config,_=make_config(tmp_path,count=1); spec=config.models[0]
    spec.local_dir.mkdir(); (spec.local_dir/"model.safetensors.index.json").write_text(json.dumps({"weight_map":{"a":"a.safetensors","b":"b.safetensors"}}))
    (spec.local_dir/"a.safetensors").write_bytes(b"x"*10)
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=lambda *_:0,sleeper=lambda _:None)
    assert not runner._snapshot_valid(spec)


def test_completion_marker_binds_exact_payload_manifest(tmp_path):
    config,_=make_config(tmp_path,count=1); spec=config.models[0]; complete_file(spec)
    runner=ModelDownloadQueue(config,tmp_path/"runtime",downloader=lambda *_:0,sleeper=lambda _:None)
    runner._write_marker(spec); assert runner._is_complete(spec)
    (spec.local_dir/"weights.bin").write_bytes(b"tampered")
    assert not runner._is_complete(spec)


def test_launch_plist_is_one_shot_and_old_job_cannot_respawn(tmp_path):
    path = write_launch_plist(tmp_path / "queue.plist")
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["Label"] == LABEL
    assert payload["KeepAlive"] is False
    assert payload["RunAtLoad"] is True
    assert "local-ai.qwen38-download" not in path.read_text()
    assert payload["EnvironmentVariables"]["HF_HUB_DISABLE_XET"] == "1"


def test_runtime_download_state_is_git_ignored():
    ignore = Path("/Users/jerson/AI/.gitignore").read_text().splitlines()
    assert "runtime/" in ignore
