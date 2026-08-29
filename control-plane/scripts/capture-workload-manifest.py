#!/usr/bin/env python3
"""Capture a read-only local workload manifest for qualification evidence."""
from __future__ import annotations

import argparse
import json

from local_ai_control.services.workload_admission import (
    WorkloadClass,
    WorkloadManifestProbe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workload-class",
        choices=[item.value for item in WorkloadClass],
        required=True,
    )
    parser.add_argument(
        "--deliberate-reduction",
        action="append",
        default=[],
        help="Describe an intentionally removed normal workload component. Any such run must be LAB.",
    )
    parser.add_argument("--top-n", type=int, default=30)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = WorkloadManifestProbe(top_n=args.top_n).capture(
        WorkloadClass(args.workload_class),
        deliberate_reductions=args.deliberate_reduction,
    )
    print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
