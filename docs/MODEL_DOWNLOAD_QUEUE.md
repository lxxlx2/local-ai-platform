# Model Download Queue V0.1

The queue downloads up to three models in parallel into
`/Users/jerson/AI/models`. It is resumable through Hugging Face `--local-dir`,
uses pinned revisions, defaults to the non-Xet HTTP path, retries each item at
most three times, records a failed item, and continues to the next item. Runtime
state, individual download logs, completion markers, locks, and PID files remain
under ignored runtime/model directories and never enter Git.

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

## Latest Operational Snapshot

Verified: `2026-08-24T19:57:00+07:00` (`Asia/Bangkok`)

- Manager state: **PAUSED**, intentionally during production validation.
- Stored manager PID: `46236`, identity `DEAD` / not verified. The manager was
  not restarted.
- Parallel limit: 3; active count: 0; quarantine count: 0.
- Completed: Whisper Large V3 MLX, Qwen3-TTS Base bf16, Qwen3-TTS
  VoiceDesign bf16.
- Partial: FLUX 60.09%, Embedding 40.66%, Reranker 55.65%.
- Pending: LongCat Video q8 0%, RAW owner-only Qwen3.8 8-bit 0%.

| ID | State | Payload bytes | Partial cache bytes | Expected bytes | Progress |
|---|---:|---:|---:|---:|---:|
| `stt-whisper-large-v3` | COMPLETE | 3,083,522,487 | 0 | 3,083,522,487 | 100.00% |
| `tts-qwen3-base-bf16` | COMPLETE | 4,544,212,739 | 73,400,320 | 4,544,212,739 | 100.00% |
| `tts-qwen3-voice-design-bf16` | COMPLETE | 4,520,194,992 | 0 | 4,520,194,992 | 100.00% |
| `image-flux2-klein-4b-bf16` | PARTIAL / PAUSED | 192,792,189 | 14,071,889,920 | 23,739,989,637 | 60.09% |
| `embed-qwen3-8b` | PARTIAL / PAUSED | 351,511,586 | 5,809,111,040 | 15,150,575,778 | 40.66% |
| `rerank-qwen3-8b` | PARTIAL / PAUSED | 1,258,402,033 | 7,864,320,000 | 16,393,071,729 | 55.65% |
| `video-longcat-q8` | PENDING / PAUSED | 0 | 0 | 33,624,123,487 | 0.00% |
| `raw-qwen38-27b-8bit` | PENDING / PAUSED | 0 | 0 | 29,528,165,982 | 0.00% |

Aggregate calculation from exact live status bytes:

- Total expected: 130,583,856,831 bytes (121.616 GiB).
- Verified completed payload credited: 12,147,930,218 bytes (11.314 GiB).
- Non-complete payload present: 1,802,705,808 bytes (1.679 GiB).
- Non-complete resumable `.incomplete` cache: 27,745,320,960 bytes
  (25.840 GiB).
- Credited present: 41,695,956,986 bytes (38.832 GiB).
- Remaining: 88,887,899,845 bytes (82.783 GiB).
- Aggregate progress: 31.93%.

Completed items are capped at their configured expected bytes. The completed
TTS Base residual cache is reported in its row but is not counted as extra
aggregate progress. Partial/cache bytes remain resumable data, not completed
payload. Download completion is still not production qualification.

Resume only after separate resource/scheduling approval:

```sh
/Users/jerson/AI/control-plane/scripts/start-model-downloads.sh
```

Resumed downloads must continue to respect the one-heavy-runtime invariant,
resource scheduling, exact worker identity, and quarantine gates.
