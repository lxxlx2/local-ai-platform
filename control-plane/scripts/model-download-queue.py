#!/usr/bin/env python3
"""CLI entrypoint for the one-shot serial model download queue."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path("/Users/jerson/AI")
sys.path.insert(0, str(ROOT / "control-plane/src"))

from local_ai_control.services.model_downloads import ModelDownloadQueue, bounded_status, load_queue_config, write_launch_plist

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--run", action="store_true")
mode.add_argument("--status", action="store_true")
mode.add_argument("--write-launch-plist", action="store_true")
args = parser.parse_args()
if args.run:
    raise SystemExit(0 if ModelDownloadQueue(load_queue_config()).run() in {"COMPLETED", "COMPLETED_WITH_FAILURES", "ALREADY_RUNNING"} else 1)
if args.status:
    print(bounded_status(), end="")
if args.write_launch_plist:
    print(write_launch_plist())
