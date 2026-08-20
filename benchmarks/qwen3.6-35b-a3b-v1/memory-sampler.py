#!/usr/bin/env python3
"""Lightweight macOS memory sampler for the local Qwen/oMLX benchmarks.

Writes JSON Lines to stdout.  It never changes system or model state.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import time


def run(command, timeout=4):
    try:
        return subprocess.run(command, text=True, capture_output=True,
                              timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def mib(value):
    return round(value / (1024 * 1024), 2)


def page_values():
    output = run(["vm_stat"])
    values = {}
    for line in output.splitlines():
        match = re.match(r'^Pages (.+?):\s+(\d+)\.?$', line.strip())
        if match:
            values[match.group(1)] = int(match.group(2))
    return values


def process_values(pid):
    output = run(["ps", "-p", str(pid), "-o", "rss=,pcpu=,state="])
    fields = output.split()
    if len(fields) >= 3:
        return {"rss_mib": round(int(fields[0]) / 1024, 2),
                "cpu_percent": float(fields[1]), "state": fields[2]}
    return {"rss_mib": None, "cpu_percent": None, "state": "NOT_FOUND"}


def sample(pid, total_bytes):
    pages = page_values()
    page_size = 16384
    get = lambda key: pages.get(key, 0) * page_size
    free = get("free")
    active = get("active")
    inactive = get("inactive")
    speculative = get("speculative")
    wired = get("wired down")
    compressed = get("occupied by compressor")
    physical_used = max(0, total_bytes - free - inactive - speculative)
    pressure_text = run(["memory_pressure", "-Q"])
    pressure_match = re.search(r'free percentage:\s*(\d+)%', pressure_text)
    pressure_free = int(pressure_match.group(1)) if pressure_match else None
    swap_text = run(["sysctl", "-n", "vm.swapusage"])
    swap_match = re.search(r'used =\s*([0-9.]+)([MG])', swap_text)
    if swap_match:
        factor = 1024 if swap_match.group(2) == "G" else 1
        swap_mib = round(float(swap_match.group(1)) * factor, 2)
    else:
        swap_mib = None
    thermal_text = run(["pmset", "-g", "therm"], timeout=2).lower()
    if not thermal_text.strip():
        thermal = "UNAVAILABLE"
    elif "no thermal warning level" in thermal_text and "no performance warning level" in thermal_text:
        thermal = "NORMAL"
    elif any(word in thermal_text for word in ("critical", "throttle", "thermal warning level: ")):
        thermal = "WARNING"
    else:
        thermal = "NORMAL"
    proc = process_values(pid)
    return {
        "timestamp": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "metric_definition": "system_memory_used = total_physical - free - inactive - speculative; process_rss is oMLX server RSS, not unified-memory allocation",
        "physical_memory_used_mib": mib(physical_used),
        "wired_memory_mib": mib(wired),
        "active_memory_mib": mib(active),
        "inactive_memory_mib": mib(inactive),
        "compressed_memory_mib": mib(compressed),
        "swap_used_mib": swap_mib,
        "memory_pressure_free_percent": pressure_free,
        "model_process_rss_mib": proc["rss_mib"],
        "model_process_cpu_percent": proc["cpu_percent"],
        "model_process_state": proc["state"],
        "thermal_state": thermal,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", help="JSONL output path; defaults to stdout")
    args = parser.parse_args()
    total_bytes = int(run(["sysctl", "-n", "hw.memsize"]).strip())
    end = time.monotonic() + args.seconds
    handle = open(args.output, "w", encoding="utf-8") if args.output else None
    try:
        while True:
            line = json.dumps(sample(args.pid, total_bytes), ensure_ascii=False)
            print(line, flush=True)
            if handle:
                print(line, file=handle, flush=True)
            if time.monotonic() >= end:
                break
            time.sleep(args.interval)
    finally:
        if handle:
            handle.close()


if __name__ == "__main__":
    main()
