#!/usr/bin/env python3
"""CLI entrypoint for the manual parallel model download manager.

The controller code/config is loaded from the checkout that owns this script,
while model payloads and runtime state remain under /Users/jerson/AI. This keeps
feature-branch queue changes from silently falling back to stale production
configuration.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = SOURCE_ROOT / "config/model-download-queue-v0.1.json"
DEFAULT_RUNTIME = Path("/Users/jerson/AI/runtime/model-downloads")
sys.path.insert(0, str(SOURCE_ROOT / "control-plane/src"))

from local_ai_control.services.model_downloads import (  # noqa: E402
    ModelDownloadQueue,
    bounded_status,
    load_queue_config,
    stop_manager,
)

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--run", action="store_true")
mode.add_argument("--status", action="store_true")
mode.add_argument("--stop", action="store_true")
parser.add_argument("--parallel", type=int, default=None)
parser.add_argument("--config", default=str(DEFAULT_CONFIG))
parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
args = parser.parse_args()

config_path = Path(args.config).expanduser().resolve(strict=True)
runtime_path = Path(args.runtime).expanduser().resolve()
config = load_queue_config(config_path)

if args.run:
    result = ModelDownloadQueue(
        config,
        runtime_dir=runtime_path,
        parallel_limit=args.parallel,
    ).run()
    raise SystemExit(
        0
        if result
        in {
            "COMPLETED",
            "COMPLETED_WITH_FAILURES",
            "PAUSED",
            "ALREADY_RUNNING",
        }
        else 1
    )
if args.status:
    print(bounded_status(runtime_path, config=config), end="")
if args.stop:
    result = stop_manager(runtime_path)
    print(result)
    raise SystemExit(0 if result in {"STOPPED", "ALREADY_STOPPED"} else 1)
