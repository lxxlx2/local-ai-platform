#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE_ROOT / "control-plane/src"))

from local_ai_control.services.generic_project_adapter import GenericProjectRegistry  # noqa: E402
from local_ai_control.services.generic_project_policy import TestProfile  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generic local project adapter")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register")
    register.add_argument("--repo", required=True)
    register.add_argument("--project-id")

    listing = sub.add_parser("list")

    worktree = sub.add_parser("worktree")
    worktree.add_argument("--project", required=True)
    worktree.add_argument("--task", required=True)
    worktree.add_argument("--base", default="HEAD")
    worktree.add_argument("--test-profile", choices=tuple(item.value for item in TestProfile))

    args = parser.parse_args(argv)
    registry = GenericProjectRegistry()
    try:
        if args.command == "register":
            result = asdict(registry.register(args.repo, project_id=args.project_id))
        elif args.command == "list":
            result = {"projects": [asdict(item) for item in registry.list_projects()]}
        else:
            result = asdict(
                registry.create_task_worktree(
                    args.project,
                    args.task,
                    base_ref=args.base,
                    test_profile=args.test_profile,
                )
            )
    except Exception as error:
        print(
            json.dumps(
                {"status": "ERROR", "error": type(error).__name__, "message": str(error)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps({"status": "OK", **result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
