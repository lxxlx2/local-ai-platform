#!/usr/bin/env python3
"""CLI entrypoint for the manual parallel model download manager."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys

ROOT = Path("/Users/jerson/AI")
sys.path.insert(0, str(ROOT / "control-plane/src"))

from local_ai_control.services.model_downloads import ModelDownloadQueue,bounded_status,load_queue_config,stop_manager,write_launch_plist

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--run", action="store_true")
mode.add_argument("--status", action="store_true")
mode.add_argument("--stop", action="store_true")
mode.add_argument("--write-launch-plist", action="store_true")
parser.add_argument("--parallel",type=int,default=None)
args = parser.parse_args()
if args.run:
    raise SystemExit(0 if ModelDownloadQueue(load_queue_config(),parallel_limit=args.parallel).run() in {"COMPLETED","COMPLETED_WITH_FAILURES","PAUSED","ALREADY_RUNNING"} else 1)
if args.status:
    print(bounded_status(), end="")
if args.stop:
    result=stop_manager(); print(result); raise SystemExit(0 if result in {"STOPPED","ALREADY_STOPPED"} else 1)
if args.write_launch_plist:
    print(write_launch_plist())
