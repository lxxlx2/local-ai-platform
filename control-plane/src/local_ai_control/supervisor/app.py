from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from local_ai_control.services.supervisor import (
    SUPERVISOR_DB, SUPERVISOR_RUNTIME, JobStatus, LeaseLostError,
    SupervisorRepository, WorkflowSupervisor, default_demo_runners,
    ensure_private_directory, ensure_private_file,
)

LOG_FILE = SUPERVISOR_RUNTIME / "supervisor.log"


def database_path() -> Path:
    override = os.environ.get("LOCAL_AI_SUPERVISOR_DB")
    return Path(override).resolve() if override else SUPERVISOR_DB


def configure_logging() -> None:
    ensure_private_directory(SUPERVISOR_RUNTIME)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    ensure_private_file(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().handlers[:] = [handler]
    logging.getLogger().setLevel(logging.INFO)


def open_repository() -> SupervisorRepository:
    repository = SupervisorRepository(database_path())
    repository.migrate()
    return repository


def daemon() -> int:
    configure_logging()
    repository = open_repository()
    supervisor = WorkflowSupervisor(repository, default_demo_runners(real_validation=True), retry_backoff_seconds=2)
    stopping = False
    lease_lost = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not supervisor.acquire_singleton():
        logging.error("supervisor single-instance lock unavailable")
        repository.close()
        return 2
    recovered = supervisor.recover_interrupted()
    pruned = repository.prune_terminal_jobs()
    logging.info("supervisor started pid=%s recovered=%s pruned=%s", os.getpid(), recovered, pruned)
    try:
        while not stopping:
            try:
                job = supervisor.run_once()
            except LeaseLostError:
                lease_lost = True
                logging.error("supervisor lease lost; exiting without reacquire")
                break
            if job is None:
                time.sleep(1)
            else:
                logging.info("job=%s status=%s stage=%s", job.job_id, job.status.value, job.current_stage.value)
    finally:
        supervisor.release_singleton()
        repository.close()
        logging.info("supervisor stopped")
    return 3 if lease_lost else 0


def status_payload(repository: SupervisorRepository) -> dict:
    health = repository.health_snapshot()
    jobs = repository.list_jobs(limit=20)
    current = next(
        (job for job in jobs if job.status in {JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.WAITING}
         or job.resume_state == "BLOCKED_REQUIRES_RECONCILIATION"), None,
    )
    return health | {
        "current_job_id": current.job_id if current else None,
        "current_stage": current.current_stage.value if current else None,
    }


def cli() -> int:
    parser = argparse.ArgumentParser(description="Local Workflow Supervisor V0.1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon")
    sub.add_parser("status")
    sub.add_parser("health")
    demo_parser = sub.add_parser("demo")
    demo_parser.add_argument("--owner-id", required=True)
    action_parser = sub.add_parser("control")
    action_parser.add_argument("action", choices=("pause", "resume", "cancel", "retry"))
    action_parser.add_argument("job_id")
    action_parser.add_argument("--owner-id", required=True)
    reconcile_parser = sub.add_parser("reconcile-fence")
    reconcile_parser.add_argument("job_id")
    reconcile_parser.add_argument("fence_name")
    reconcile_parser.add_argument("--owner-id", required=True)
    reconcile_parser.add_argument("--note", required=True)
    args = parser.parse_args()
    if args.command == "daemon":
        return daemon()
    repository = open_repository()
    supervisor = WorkflowSupervisor(repository, default_demo_runners(real_validation=True))
    try:
        if args.command in {"status", "health"}:
            print(json.dumps(status_payload(repository), ensure_ascii=False, sort_keys=True))
        elif args.command == "demo":
            job = supervisor.create_demo(args.owner_id)
            print(json.dumps({"job_id": job.job_id, "status": job.status.value}, sort_keys=True))
        elif args.command == "reconcile-fence":
            job = supervisor.reconcile_fence(
                args.job_id, args.owner_id, args.fence_name, args.note,
            )
            print(json.dumps({"job_id": job.job_id, "status": job.status.value,
                              "stage": job.current_stage.value}, sort_keys=True))
        else:
            job = getattr(supervisor, args.action)(args.job_id, args.owner_id)
            print(json.dumps({"job_id": job.job_id, "status": job.status.value,
                              "stage": job.current_stage.value}, sort_keys=True))
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
