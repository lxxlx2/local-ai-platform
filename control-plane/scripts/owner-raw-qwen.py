#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / "src"))

from local_ai_control.services.owner_raw import (  # noqa: E402
    OwnerRawService,
    RawRuntimeState,
    local_owner_identity,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-only RAW Qwen sandbox controller")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("health")
    commands.add_parser("start")
    commands.add_parser("stop")
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--prompt", default="请只回复：RAW_LOCAL_SMOKE_OK")
    smoke.add_argument("--max-output-tokens", type=int, default=64)
    commands.add_parser("qualify")
    args = parser.parse_args(argv)

    service = OwnerRawService()
    try:
        identity = local_owner_identity()
        if args.command == "status":
            result = service.status(identity).to_dict()
        elif args.command == "health":
            result = {"status": "OK", "health": service.health(identity)}
        elif args.command == "start":
            result = service.start(identity).to_dict()
        elif args.command == "stop":
            result = {"status": service.stop(identity)}
        elif args.command == "smoke":
            text = service.generate(identity, args.prompt, max_output_tokens=args.max_output_tokens)
            result = {"status": "PASS", "local_text_generated": bool(text.strip()), "char_count": len(text)}
        else:
            before = service.status(identity)
            if before.state not in {RawRuntimeState.READY, RawRuntimeState.RUNNING}:
                raise RuntimeError(f"qualification blocked: model state is {before.state.value}")
            started_here = before.state is not RawRuntimeState.RUNNING
            if started_here:
                service.start(identity)
            try:
                health = service.health(identity)
                text = service.generate(identity, "请只回复：RAW_LOCAL_SMOKE_OK", max_output_tokens=64)
                result = {
                    "status": "PASS",
                    "health": bool(health),
                    "local_text_generated": bool(text.strip()),
                    "char_count": len(text),
                }
            finally:
                if started_here:
                    service.stop(identity)
    except Exception as error:
        print(json.dumps({"status": "ERROR", "error": type(error).__name__, "message": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
