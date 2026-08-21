from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from local_ai_control.services.learning import (
    LEARNING_RUNTIME,
    AdapterRegistry,
    BoundedLocalContentStore,
    DatasetBuilder,
    GoldenEvalHarness,
    LearningImportExport,
    LearningRepository,
    LearningService,
    MLXLoRATrainingProvider,
    TrainingPriorityService,
    retention_dry_run,
)


def open_engine():
    repository = LearningRepository(); repository.migrate()
    store = BoundedLocalContentStore()
    service = LearningService(repository, store)
    return repository, store, service


def cli() -> int:
    parser = argparse.ArgumentParser(description="Private Learning Engine V0.1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    candidates = sub.add_parser("candidates"); candidates.add_argument("--namespace")
    sub.add_parser("datasets")
    build = sub.add_parser("build-dataset"); build.add_argument("namespace")
    evaluate = sub.add_parser("eval")
    evaluate.add_argument("namespace"); evaluate.add_argument("--base-scores", required=True)
    evaluate.add_argument("--adapter-scores", required=True); evaluate.add_argument("--adapter-id")
    adapters = sub.add_parser("adapters"); adapters.add_argument("--namespace")
    sub.add_parser("priorities")
    probe = sub.add_parser("probe")
    config = sub.add_parser("build-config"); config.add_argument("--dataset", required=True); config.add_argument("--adapter", required=True)
    importer = sub.add_parser("import-jsonl"); importer.add_argument("namespace"); importer.add_argument("path")
    export_manifest = sub.add_parser("export-manifest"); export_manifest.add_argument("dataset_id"); export_manifest.add_argument("path")
    export_jsonl = sub.add_parser("export-redacted-jsonl"); export_jsonl.add_argument("dataset_id"); export_jsonl.add_argument("path")
    sub.add_parser("cleanup-dry-run")
    args = parser.parse_args()

    if args.command == "probe":
        print(json.dumps(asdict(MLXLoRATrainingProvider().probe()), sort_keys=True))
        return 0
    if args.command == "build-config":
        config_value = MLXLoRATrainingProvider().build_config(
            dataset_path=Path(args.dataset), adapter_path=Path(args.adapter),
        )
        print(json.dumps(asdict(config_value) | {"config_hash": config_value.config_hash}, sort_keys=True))
        return 0

    repository, store, service = open_engine()
    try:
        if args.command == "status":
            output = repository.metrics()
        elif args.command == "candidates":
            output = [asdict(item) | {"prompt": None, "response": None}
                      for item in service.list_candidates(args.namespace)]
        elif args.command == "datasets":
            output = [dict(row) for row in repository.db.execute(
                "SELECT dataset_id,version,namespace,created_at,schema_version,manifest_hash FROM datasets ORDER BY created_at DESC"
            ).fetchall()]
        elif args.command == "build-dataset":
            output = asdict(DatasetBuilder(repository, store).build(args.namespace))
        elif args.command == "eval":
            output = asdict(GoldenEvalHarness(repository).compare(
                args.namespace, json.loads(args.base_scores), json.loads(args.adapter_scores), args.adapter_id,
            ))
        elif args.command == "adapters":
            output = AdapterRegistry(repository).list(args.namespace)
        elif args.command == "priorities":
            output = TrainingPriorityService(repository).top()
        elif args.command == "cleanup-dry-run":
            output = retention_dry_run(repository) | {"content_references": store.cleanup(dry_run=True)}
        else:
            transfer = LearningImportExport(service)
            if args.command == "import-jsonl":
                output = transfer.import_jsonl(Path(args.path), args.namespace)
            elif args.command == "export-manifest":
                output = {"path": str(transfer.export_manifest(args.dataset_id, Path(args.path)))}
            else:
                output = {"path": str(transfer.export_redacted_jsonl(args.dataset_id, Path(args.path)))}
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, default=str))
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
