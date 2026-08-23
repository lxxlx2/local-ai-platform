# Model Download Queue V0.1

The queue downloads one model at a time into `/Users/jerson/AI/models`. It is
resumable through Hugging Face `--local-dir`, uses pinned revisions, defaults to
the non-Xet HTTP path, retries each item at most three times, records a failed
item, and continues to the next item. Runtime state, individual download logs,
completion markers, locks, and PID files remain under ignored runtime/model
directories and never enter Git.

The committed queue metadata was verified against Hugging Face on 2026-08-23.
Repository IDs, exact commit revisions, advertised byte sizes, and licenses are
stored in `config/model-download-queue-v0.1.json`. An available snapshot is not
treated as production-qualified: every model still requires a separate Apple
Silicon hardware and runtime qualification after download. In particular, the
Embedding and Reranker entries are upstream Transformers snapshots, FLUX needs
its MLX diffusion adapter, and LongCat must pass a 48 GB memory test.

Start or resume the one-shot launchd job:

```sh
/Users/jerson/AI/control-plane/scripts/start-model-downloads.sh
```

Read a bounded live status (including completed payload bytes, separate partial
cache bytes, and shard count when an index is available):

```sh
/Users/jerson/AI/control-plane/scripts/status-model-downloads.sh
```

The runtime plist has `KeepAlive=false`, so a completed queue does not respawn.
It is registered only in the current GUI launchd session, not installed into
`~/Library/LaunchAgents`; automatic recovery after logout or reboot is therefore
not provided in V0.1. Running the start command safely resumes partial files.
