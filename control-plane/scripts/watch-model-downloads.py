#!/usr/bin/env python3
"""Interactive read-only progress view; Ctrl+C never signals the manager."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time

ROOT=Path("/Users/jerson/AI")
sys.path.insert(0,str(ROOT/"control-plane/src"))
from local_ai_control.services.model_downloads import status_snapshot


def render(snapshot,previous,elapsed):
    lines=[f"MODEL DOWNLOADS | {snapshot['manager_state']} | active {snapshot['active_count']} / {snapshot['parallel_limit']}"]
    current={row['id']:row['downloaded_bytes'] for row in snapshot['models']}
    for row in snapshot["models"]:
        delta=max(row["downloaded_bytes"]-previous.get(row["id"],row["downloaded_bytes"]),0); speed=delta/max(elapsed,0.001)/1024**2
        width=24; filled=min(int(row["progress_pct"]/100*width),width); bar="#"*filled+"-"*(width-filled)
        lines.append(f"{row['id']:<30} [{bar}] {row['progress_pct']:6.2f}%  {row['downloaded_bytes']/1024**3:7.3f}/{row['expected_bytes']/1024**3:7.3f} GiB  payload {row['payload_bytes']/1024**3:.3f}  partial {row['partial_cache_bytes']/1024**3:.3f}  +{delta/1024**2:.1f} MiB  {speed:.2f} MiB/s  pid {row['worker_pid'] or '-'}  {row['state']}")
    return "\n".join(lines),current


def watch(interval,*,provider=status_snapshot,printer=print,sleeper=time.sleep):
    previous={}; last=time.monotonic()
    try:
        while True:
            now=time.monotonic(); output,previous=render(provider(),previous,now-last); last=now
            printer("\033[2J\033[H"+output,flush=True); sleeper(interval)
    except KeyboardInterrupt:
        return "WATCH_STOPPED"


if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("interval",nargs="?",type=int,default=5); args=parser.parse_args()
    if args.interval not in {5,10}: raise SystemExit("interval must be 5 or 10 seconds")
    raise SystemExit(0 if watch(args.interval)=="WATCH_STOPPED" else 1)
