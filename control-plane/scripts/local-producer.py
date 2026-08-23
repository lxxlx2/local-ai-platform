#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path("/Users/jerson/AI")
sys.path.insert(0, str(ROOT / "control-plane/src"))

from local_ai_control.services.local_producer import (  # noqa: E402
    LocalPatchProducer, LocalProducerError, MAX_TASK_BYTES, discover_context_paths, require_safe_worktree,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3.8 Local Producer V0.1")
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument("--read", action="append", default=[], help="repo-relative safe context file; repeatable")
    parser.add_argument("--attempts", type=int, default=2, choices=(1, 2, 3))
    parser.add_argument("--apply", action="store_true", help="apply validated patch to current feature branch")
    args = parser.parse_args()

    try:
        branch = require_safe_worktree(ROOT)
        task_data = args.task_file.read_bytes()
        if len(task_data) > MAX_TASK_BYTES:
            raise LocalProducerError(f"task file exceeds {MAX_TASK_BYTES} bytes")
        task = task_data.decode("utf-8")
        paths = tuple(args.read) if args.read else discover_context_paths(task, ROOT)
        producer = LocalPatchProducer(repo_root=ROOT)
        proposal = producer.propose(task, paths, attempts=args.attempts)
        payload = {
            "status": "PROPOSED",
            "branch": branch,
            "patch_sha256": proposal.patch_sha256,
            "paths": list(proposal.paths),
            "summary": proposal.summary,
            "applied": False,
        }
        if args.apply:
            producer.apply(proposal)
            payload["status"] = "APPLIED_UNCOMMITTED"
            payload["applied"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        if not args.apply:
            print("\n--- VALIDATED PATCH (NOT APPLIED) ---\n")
            print(proposal.patch)
        return 0
    except (OSError, UnicodeDecodeError, LocalProducerError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": type(exc).__name__, "detail": str(exc)[:3000]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
