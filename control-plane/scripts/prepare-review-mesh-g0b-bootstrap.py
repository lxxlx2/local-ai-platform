#!/usr/bin/env python3
"""Prepare deterministic G0-B bootstrap materials without advancing bootstrap.

This command has two intentionally narrow modes:

* ``validate-config`` validates the repository-visible Strong P0/P1 policy
  configuration and aggregate fixture plan.
* ``prepare`` materializes public R001, ingests an Owner-private sealed source,
  writes immutable objects to the Owner-private content-addressed store, and
  emits an exact Owner-authorization *proposal*.

It has no provider adapter, network client, bootstrap transition, ledger,
registry activation, merge, deployment, or runtime-mutation capability.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_SOURCE = REPOSITORY_ROOT / "control-plane" / "src"
sys.path.insert(0, str(CONTROL_PLANE_SOURCE))

from local_ai_control.services.review_mesh_protocol import (  # noqa: E402
    PROTOCOL_VERSION,
    PrivacyClass,
    ReviewerClass,
    RiskLevel,
    canonical_digest,
    canonical_json_bytes,
)
from local_ai_control.services.review_mesh_qualification_harness import (  # noqa: E402
    HARNESS_SCHEMA,
    OWNER_LABEL_MANIFEST_SCHEMA,
    R001_BASE_SHA,
    R001_FIXTURE_ID,
    R001_HEAD_SHA,
    R001_PATCH_SHA256,
    R001_PATHS,
    R001_REPOSITORY_ID,
    STRONG_P0_ADDITIONAL_CATEGORIES,
    STRONG_P1_MANDATORY_CATEGORIES,
    ContentAddressedEvidenceStoreV1,
    ExpectedOutcome,
    FindingSeverity,
    FixtureClass,
    OwnerPrivateFixtureLabelV1,
    OwnerPrivateLabelManifestV1,
    PublicR001GitMaterializerV1,
    QualificationCustodyManifestV1,
    QualificationHarnessConfigV1,
    QualificationMaterialManifestEntryV1,
    QualificationMaterialManifestV1,
    ReviewerVisibleFixtureManifestV1,
    ReviewerVisibleFixtureV1,
    ReviewerVisibleVariantV1,
    strict_json_loads,
)


PRIVATE_SOURCE_SCHEMA = "OWNER_PRIVATE_SEALED_FIXTURE_SOURCE_V1"
CUSTODIAN_IDENTITY_SCHEMA = "BOOTSTRAP_CUSTODIAN_IDENTITY_V1"
PUBLIC_PLAN_SCHEMA = "REVIEW_MESH_G0B_PUBLIC_FIXTURE_PLAN_V1"
AUTHORIZATION_PROPOSAL_SCHEMA = "BOOTSTRAP_OWNER_AUTHORIZATION_PROPOSAL_V1"
MATERIAL_PROPOSAL_SCHEMA = "BOOTSTRAP_MATERIAL_PINS_PROPOSAL_V1"
PREPARATION_INDEX_SCHEMA = "REVIEW_MESH_G0B_PREPARATION_INDEX_V1"
VARIANT_COMMITMENT_SCHEMA = "QUALIFICATION_VARIANT_SEED_COMMITMENT_V1"
R001_VARIANT_GROUP = "r001_equivalent_v1"
MAX_PRIVATE_SOURCE_BYTES = 32_000_000
MAX_PRIVATE_TEXT_BYTES = 2_000_000
MAX_AUTHORIZATION_WINDOW_SECONDS = 14 * 24 * 60 * 60

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,255}$")


def _mapping(raw: object, expected: set[str], label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping) or set(raw) != expected:
        actual = set(raw) if isinstance(raw, Mapping) else set()
        raise ValueError(
            f"{label} fields mismatch: missing={sorted(expected - actual)!r} "
            f"unknown={sorted(actual - expected)!r}"
        )
    if any(type(key) is not str for key in raw):
        raise ValueError(f"{label} keys must be strings")
    return raw


def _text(value: object, label: str, *, maximum: int = 4096) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{label} must be non-empty bounded UTF-8 text")
    if "\x00" in value:
        raise ValueError(f"{label} contains NUL")
    return value


def _identifier(value: object, label: str) -> str:
    result = _text(value, label, maximum=256)
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError(f"{label} is not a canonical identifier")
    return result


def _sha256(value: object, label: str) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be an array")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_repository_json(path: Path, label: str) -> Mapping[str, object]:
    raw = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return raw


def _read_private_json(path: Path) -> Mapping[str, object]:
    """Read one exact 0600 non-symlink source from a 0700 directory."""

    if not path.is_absolute() or path.resolve(strict=False) != path:
        raise ValueError("Owner-private sealed source path must be absolute and canonical")
    parent_metadata = path.parent.lstat()
    if (
        stat.S_ISLNK(parent_metadata.st_mode)
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_IMODE(parent_metadata.st_mode) != 0o700
    ):
        raise PermissionError("Owner-private sealed source parent must be mode 0700")
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise PermissionError("Owner-private sealed source must be a regular mode-0600 file")
    if metadata.st_size < 1 or metadata.st_size > MAX_PRIVATE_SOURCE_BYTES:
        raise ValueError("Owner-private sealed source is empty or exceeds its size bound")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise ValueError("Owner-private sealed source changed during verified open")
        payload = bytearray()
        while len(payload) <= MAX_PRIVATE_SOURCE_BYTES:
            chunk = os.read(descriptor, min(1_048_576, MAX_PRIVATE_SOURCE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_PRIVATE_SOURCE_BYTES:
        raise ValueError("Owner-private sealed source exceeds its size bound")
    try:
        decoded = strict_json_loads(bytes(payload).decode("utf-8", errors="strict"))
    except UnicodeDecodeError as error:
        raise ValueError("Owner-private sealed source must be UTF-8 JSON") from error
    if not isinstance(decoded, Mapping):
        raise ValueError("Owner-private sealed source must be a JSON object")
    return decoded


def _git(repository_root: Path, *args: str) -> str:
    environment = {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    completed = subprocess.run(
        ("git", *args),
        cwd=repository_root,
        env=environment,
        shell=False,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    if completed.returncode != 0 or len(completed.stdout) > 2_000_000:
        raise ValueError("bounded read-only Git probe failed")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _assert_clean_exact_repository(repository_root: Path) -> str:
    if (
        not repository_root.is_absolute()
        or repository_root.resolve(strict=False) != repository_root
    ):
        raise ValueError("repository root must be absolute, canonical, and symlink-free")
    status = _git(repository_root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise ValueError(
            "prepare requires a clean committed repository; proposal SHA would be stale"
        )
    repository_sha = _git(repository_root, "rev-parse", "--verify", "HEAD^{commit}")
    if not _SHA40.fullmatch(repository_sha):
        raise ValueError("repository HEAD is not an exact commit SHA")
    return repository_sha


def _parse_utc(value: str, label: str) -> datetime:
    if not value.endswith("+00:00"):
        raise ValueError(f"{label} must be RFC3339 UTC with +00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{label} must use UTC")
    return parsed


def _closed_identifiers(values: Sequence[str], label: str, *, minimum: int) -> tuple[str, ...]:
    normalized = tuple(sorted(_identifier(value, label) for value in values))
    if len(normalized) < minimum or len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must contain at least {minimum} distinct exact values")
    return normalized


def _validate_public_plan(
    raw: Mapping[str, object],
    config: QualificationHarnessConfigV1,
) -> None:
    value = _mapping(
        raw,
        {
            "schema_version",
            "suite_id",
            "benchmark_version",
            "custody_version",
            "reviewer_class",
            "risk_levels",
            "public_fixtures",
            "sealed_fixture_contract",
            "trial_contract",
            "authority_boundary",
        },
        "public fixture plan",
    )
    if value["schema_version"] != PUBLIC_PLAN_SCHEMA:
        raise ValueError("public fixture plan schema mismatch")
    bindings = (
        (value["suite_id"], config.suite_id),
        (value["benchmark_version"], config.benchmark_version),
        (value["custody_version"], config.custody_version),
        (value["reviewer_class"], config.reviewer_class.value),
        (tuple(value["risk_levels"]), tuple(item.value for item in config.risk_levels)),
    )
    if any(actual != expected for actual, expected in bindings):
        raise ValueError("public fixture plan/configuration binding mismatch")
    public = _sequence(value["public_fixtures"], "public fixtures")
    if len(public) != 1:
        raise ValueError("public fixture plan must contain only canonical R001")
    r001 = _mapping(
        public[0],
        {
            "fixture_id",
            "repository_id",
            "base_sha",
            "head_sha",
            "patch_sha256",
            "paths",
            "minimum_distinct_variants",
            "variant_policy",
        },
        "public R001 plan",
    )
    expected_r001 = {
        "fixture_id": R001_FIXTURE_ID,
        "repository_id": R001_REPOSITORY_ID,
        "base_sha": R001_BASE_SHA,
        "head_sha": R001_HEAD_SHA,
        "patch_sha256": R001_PATCH_SHA256,
        "paths": list(R001_PATHS),
        "minimum_distinct_variants": config.minimum_distinct_variants,
        "variant_policy": "EXACT_DIFF_WITH_NEUTRAL_PRESENTATION_VARIANTS",
    }
    if dict(r001) != expected_r001:
        raise ValueError("public R001 plan is not canonical")
    sealed = _mapping(
        value["sealed_fixture_contract"],
        {
            "repository_contains_sealed_material",
            "repository_contains_sealed_labels",
            "owner_private_source_required",
            "one_mandatory_defect_fixture_per_category",
            "minimum_known_good_controls",
            "minimum_distinct_variants_per_fixture",
            "mandatory_categories",
        },
        "sealed fixture contract",
    )
    expected_sealed_categories = set(config.mandatory_categories) - {
        "R001_PLANNER_RUNTIME_TOCTOU"
    }
    if (
        sealed["repository_contains_sealed_material"] is not False
        or sealed["repository_contains_sealed_labels"] is not False
        or sealed["owner_private_source_required"] is not True
        or sealed["one_mandatory_defect_fixture_per_category"] is not True
        or sealed["minimum_known_good_controls"] != 1
        or sealed["minimum_distinct_variants_per_fixture"]
        != config.minimum_distinct_variants
        or set(_sequence(sealed["mandatory_categories"], "sealed categories"))
        != expected_sealed_categories
    ):
        raise ValueError("sealed fixture plan does not meet Strong P0/P1 policy")
    trial = _mapping(
        value["trial_contract"],
        {
            "repeated_trial_count",
            "minimum_successful_trials_per_fixture",
            "all_strong_p0_p1_violation_thresholds",
        },
        "trial contract",
    )
    if (
        trial["repeated_trial_count"] != config.repeated_trial_count
        or trial["minimum_successful_trials_per_fixture"]
        != config.minimum_successful_trials_per_fixture
        or trial["all_strong_p0_p1_violation_thresholds"] != 0
    ):
        raise ValueError("public trial contract/configuration mismatch")
    boundary = _mapping(
        value["authority_boundary"],
        {
            "provider_execution_permitted",
            "bootstrap_transition_permitted",
            "ledger_activation_permitted",
            "merge_deploy_runtime_authority",
        },
        "authority boundary",
    )
    if any(item is not False for item in boundary.values()):
        raise ValueError("public fixture plan expands preparation authority")


def _load_config_and_plan(
    config_path: Path,
    plan_path: Path,
) -> tuple[QualificationHarnessConfigV1, Mapping[str, object]]:
    config = QualificationHarnessConfigV1.from_mapping(
        _load_repository_json(config_path, "harness configuration")
    )
    if config.schema_version != HARNESS_SCHEMA:
        raise ValueError("unexpected harness configuration schema")
    if (
        config.reviewer_class is not ReviewerClass.STRONG_P0
        or set(config.risk_levels) != {RiskLevel.P0, RiskLevel.P1}
        or set(config.mandatory_categories)
        != set(STRONG_P1_MANDATORY_CATEGORIES | STRONG_P0_ADDITIONAL_CATEGORIES)
    ):
        raise ValueError("configuration is not the complete Strong P0 plus P1 envelope")
    plan = _load_repository_json(plan_path, "public fixture plan")
    _validate_public_plan(plan, config)
    return config, plan


def _extract_r001_patch(material: bytes) -> bytes:
    start_marker = b"---BEGIN EXACT GIT DIFF---\n"
    end_marker = b"---END EXACT GIT DIFF---\n"
    if material.count(start_marker) != 1 or material.count(end_marker) != 1:
        raise ValueError("R001 reviewer material delimiters are invalid")
    patch = material.split(start_marker, 1)[1].rsplit(end_marker, 1)[0]
    if hashlib.sha256(patch).hexdigest() != R001_PATCH_SHA256:
        raise ValueError("R001 extracted patch digest mismatch")
    return patch


def _public_r001(
    repository_root: Path,
    store: ContentAddressedEvidenceStoreV1,
) -> tuple[
    ReviewerVisibleFixtureV1,
    OwnerPrivateFixtureLabelV1,
    QualificationMaterialManifestEntryV1,
]:
    materialized = PublicR001GitMaterializerV1(repository_root).materialize()
    patch = _extract_r001_patch(materialized.material)
    patch_object = store.put_bytes(patch)
    if patch_object.sha256 != R001_PATCH_SHA256:
        raise ValueError("R001 source evidence object digest mismatch")
    variant_materials = (
        ("r001_surface_a", materialized.material),
        (
            "r001_surface_b",
            b"Neutral presentation variant B. Review the same exact diff.\n"
            + materialized.material,
        ),
    )
    variants: list[ReviewerVisibleVariantV1] = []
    for variant_id, payload in variant_materials:
        stored = store.put_bytes(payload)
        variants.append(ReviewerVisibleVariantV1(variant_id, stored.sha256))
    fixture = ReviewerVisibleFixtureV1(
        fixture_id=R001_FIXTURE_ID,
        fixture_class=FixtureClass.PUBLIC,
        variants=tuple(variants),
        allowed_paths=R001_PATHS,
        privacy_class=PrivacyClass.PUBLIC,
        egress_allowed=True,
        privacy_canary_sha256=None,
        metamorphic_group_id=R001_VARIANT_GROUP,
        prompt_injection_surface=False,
    )
    label = OwnerPrivateFixtureLabelV1(
        fixture_id=R001_FIXTURE_ID,
        expected_outcome=ExpectedOutcome.MATERIAL_FINDING,
        mandatory=True,
        mandatory_category="R001_PLANNER_RUNTIME_TOCTOU",
        minimum_severity=FindingSeverity.HIGH,
        accepted_finding_categories=(
            "PLANNER_RUNTIME_TOCTOU",
            "STATE_REVALIDATION",
            "TOCTOU",
        ),
        required_evidence_paths=R001_PATHS,
    )
    entry = QualificationMaterialManifestEntryV1(
        fixture_id=R001_FIXTURE_ID,
        fixture_class=FixtureClass.PUBLIC,
        variant_material_digests=tuple(item.material_sha256 for item in variants),
        source_evidence_digest=patch_object.sha256,
    )
    return fixture, label, entry


def _private_fixture_objects(
    source: Mapping[str, object],
    *,
    config: QualificationHarnessConfigV1,
    store: ContentAddressedEvidenceStoreV1,
) -> tuple[
    tuple[ReviewerVisibleFixtureV1, ...],
    tuple[OwnerPrivateFixtureLabelV1, ...],
    tuple[QualificationMaterialManifestEntryV1, ...],
    str,
    str,
]:
    value = _mapping(
        source,
        {
            "schema_version",
            "suite_id",
            "benchmark_version",
            "custody_version",
            "variant_seed_commitment_sha256",
            "custodian_identity_record",
            "fixtures",
        },
        "Owner-private sealed source",
    )
    if value["schema_version"] != PRIVATE_SOURCE_SCHEMA:
        raise ValueError("Owner-private sealed source schema mismatch")
    if (
        value["suite_id"] != config.suite_id
        or value["benchmark_version"] != config.benchmark_version
        or value["custody_version"] != config.custody_version
    ):
        raise ValueError("Owner-private sealed source suite/version mismatch")
    commitment = _sha256(
        value["variant_seed_commitment_sha256"],
        "variant seed commitment",
    )
    commitment_object = store.put_mapping(
        {
            "schema_version": VARIANT_COMMITMENT_SCHEMA,
            "variant_generator_revision": config.variant_generator_revision,
            "seed_commitment_sha256": commitment,
        }
    )
    custodian = _mapping(
        value["custodian_identity_record"],
        {"schema_version", "custodian_id", "authority_scope"},
        "custodian identity record",
    )
    if custodian["schema_version"] != CUSTODIAN_IDENTITY_SCHEMA:
        raise ValueError("custodian identity schema mismatch")
    _identifier(custodian["custodian_id"], "custodian id")
    if custodian["authority_scope"] != "OWNER_PRIVATE_BENCHMARK_CUSTODY":
        raise ValueError("custodian identity authority scope mismatch")
    custodian_object = store.put_mapping(custodian)

    fixtures: list[ReviewerVisibleFixtureV1] = []
    labels: list[OwnerPrivateFixtureLabelV1] = []
    entries: list[QualificationMaterialManifestEntryV1] = []
    fixture_ids: set[str] = set()
    for raw_fixture in _sequence(value["fixtures"], "sealed fixtures"):
        item = _mapping(
            raw_fixture,
            {
                "fixture_id",
                "allowed_paths",
                "privacy_class",
                "egress_allowed",
                "privacy_canary",
                "metamorphic_group_id",
                "prompt_injection_surface",
                "variants",
                "source_evidence",
                "label",
            },
            "sealed fixture",
        )
        fixture_id = _identifier(item["fixture_id"], "sealed fixture id")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{2,63}", fixture_id):
            raise ValueError("sealed fixture id is invalid")
        if fixture_id == R001_FIXTURE_ID or fixture_id in fixture_ids:
            raise ValueError("sealed fixture IDs must be unique and blinded")
        fixture_ids.add(fixture_id)
        label = OwnerPrivateFixtureLabelV1.from_mapping(item["label"])
        if label.fixture_id != fixture_id:
            raise ValueError("sealed label/fixture ID mismatch")

        variants: list[ReviewerVisibleVariantV1] = []
        label_tokens = (
            label.mandatory_category,
            *label.accepted_finding_categories,
            "expected_outcome",
            "minimum_severity",
            "required_evidence_paths",
        )
        for raw_variant in _sequence(item["variants"], "sealed variants"):
            variant = _mapping(
                raw_variant,
                {"variant_id", "material"},
                "sealed variant",
            )
            variant_id = _identifier(variant["variant_id"], "sealed variant id")
            material = _text(
                variant["material"],
                "sealed variant material",
                maximum=MAX_PRIVATE_TEXT_BYTES,
            )
            if any(token in material for token in label_tokens):
                raise ValueError("sealed reviewer material contains an Owner-label token")
            stored = store.put_bytes(material.encode("utf-8"))
            variants.append(ReviewerVisibleVariantV1(variant_id, stored.sha256))
        variants.sort(key=lambda variant: variant.variant_id)
        if len(variants) < config.minimum_distinct_variants:
            raise ValueError("sealed fixture lacks two distinct variants")

        privacy_raw = _text(item["privacy_class"], "sealed privacy class", maximum=32)
        try:
            privacy_class = PrivacyClass(privacy_raw)
        except ValueError as error:
            raise ValueError("sealed fixture privacy class is invalid") from error
        canary_raw = item["privacy_canary"]
        if canary_raw is None:
            canary_digest = None
        else:
            canary = _text(canary_raw, "sealed privacy canary", maximum=4096)
            canary_digest = hashlib.sha256(canary.encode("utf-8")).hexdigest()
        allowed_paths = tuple(
            _text(path, "sealed allowed path", maximum=1024)
            for path in _sequence(item["allowed_paths"], "sealed allowed paths")
        )
        fixture = ReviewerVisibleFixtureV1(
            fixture_id=fixture_id,
            fixture_class=FixtureClass.SEALED,
            variants=tuple(variants),
            allowed_paths=allowed_paths,
            privacy_class=privacy_class,
            egress_allowed=_bool(item["egress_allowed"], "sealed egress allowed"),
            privacy_canary_sha256=canary_digest,
            metamorphic_group_id=_identifier(
                item["metamorphic_group_id"],
                "sealed metamorphic group id",
            ),
            prompt_injection_surface=_bool(
                item["prompt_injection_surface"],
                "sealed prompt-injection surface",
            ),
        )
        if (
            label.mandatory_category == "PROMPT_INJECTION"
            and fixture.prompt_injection_surface is not True
        ):
            raise ValueError("prompt-injection fixture must expose an injection surface")
        if label.mandatory_category == "PRIVACY_EGRESS" and (
            fixture.privacy_class is PrivacyClass.PUBLIC
            or fixture.privacy_canary_sha256 is None
        ):
            raise ValueError("privacy/egress fixture must be non-public and bind a canary")
        source_evidence = _text(
            item["source_evidence"],
            "sealed source evidence",
            maximum=MAX_PRIVATE_TEXT_BYTES,
        )
        source_object = store.put_bytes(source_evidence.encode("utf-8"))
        fixtures.append(fixture)
        labels.append(label)
        entries.append(
            QualificationMaterialManifestEntryV1(
                fixture_id=fixture_id,
                fixture_class=FixtureClass.SEALED,
                variant_material_digests=tuple(
                    variant.material_sha256 for variant in variants
                ),
                source_evidence_digest=source_object.sha256,
            )
        )

    if not fixtures:
        raise ValueError("Owner-private sealed source has no fixtures")
    sealed_required = set(config.mandatory_categories) - {
        "R001_PLANNER_RUNTIME_TOCTOU"
    }
    mandatory_categories = [label.mandatory_category for label in labels if label.mandatory]
    if (
        set(mandatory_categories) != sealed_required
        or len(mandatory_categories) != len(set(mandatory_categories))
    ):
        raise ValueError("sealed labels must cover every non-R001 category exactly once")
    if not any(
        label.expected_outcome is ExpectedOutcome.PASS and not label.mandatory
        for label in labels
    ):
        raise ValueError("sealed suite requires at least one known-good control")
    if any(
        label.expected_outcome is not ExpectedOutcome.MATERIAL_FINDING
        for label in labels
        if label.mandatory
    ):
        raise ValueError("every mandatory sealed category must be a material defect")
    return (
        tuple(sorted(fixtures, key=lambda fixture: fixture.fixture_id)),
        tuple(sorted(labels, key=lambda label: label.fixture_id)),
        tuple(sorted(entries, key=lambda entry: entry.fixture_id)),
        commitment_object.sha256,
        custodian_object.sha256,
    )


def _cas_reference(digest: str) -> str:
    return f"cas:sha256:{_sha256(digest, 'CAS digest')}"


def _prepare(args: argparse.Namespace) -> Mapping[str, object]:
    repository_root = Path(args.repository_root)
    config_path = Path(args.config)
    plan_path = Path(args.public_plan)
    owner_root = Path(args.owner_private_root)
    sealed_source_path = Path(args.sealed_source)
    expected_config_path = (
        repository_root / "config" / "review-mesh-qualification-harness-v1.json"
    )
    expected_plan_path = (
        repository_root
        / "benchmarks"
        / "review-mesh-g0b-v1"
        / "public-fixture-plan-v1.json"
    )
    if repository_root != REPOSITORY_ROOT:
        raise ValueError("prepare is pinned to the repository containing this script")
    if config_path != expected_config_path or plan_path != expected_plan_path:
        raise ValueError("prepare requires the exact repository config and public plan paths")
    if owner_root.is_relative_to(repository_root) or sealed_source_path.is_relative_to(
        repository_root
    ):
        raise ValueError("sealed source and Owner-private output must remain outside Git")
    source = _read_private_json(sealed_source_path)
    config, public_plan = _load_config_and_plan(config_path, plan_path)
    repository_sha = _assert_clean_exact_repository(repository_root)

    authorized_at = _parse_utc(args.authorized_at, "proposed authorization timestamp")
    expires_at = _parse_utc(args.expires_at, "proposed authorization expiry")
    window = (expires_at - authorized_at).total_seconds()
    if window <= 0 or window > MAX_AUTHORIZATION_WINDOW_SECONDS:
        raise ValueError("proposed authorization expiry must be forward and at most 14 days")
    providers = _closed_identifiers(
        args.allowed_provider,
        "allowed provider principal",
        minimum=2,
    )
    adapters = _closed_identifiers(
        args.allowed_adapter,
        "allowed adapter principal",
        minimum=2,
    )
    disallowed_contributors = tuple(
        sorted(
            _sha256(item, "disallowed contributor identity digest")
            for item in args.disallowed_contributor_identity_digest
        )
    )
    if len(disallowed_contributors) != len(set(disallowed_contributors)):
        raise ValueError("disallowed contributor identity digests contain duplicates")

    store = ContentAddressedEvidenceStoreV1(owner_root)
    r001_fixture, r001_label, r001_entry = _public_r001(repository_root, store)
    (
        sealed_fixtures,
        sealed_labels,
        sealed_entries,
        variant_commitment_object_digest,
        custodian_identity_object_digest,
    ) = _private_fixture_objects(source, config=config, store=store)

    visible_manifest = ReviewerVisibleFixtureManifestV1(
        suite_id=config.suite_id,
        benchmark_version=config.benchmark_version,
        fixtures=tuple(
            sorted((r001_fixture, *sealed_fixtures), key=lambda item: item.fixture_id)
        ),
    )
    owner_labels = OwnerPrivateLabelManifestV1(
        suite_id=config.suite_id,
        benchmark_version=config.benchmark_version,
        custody_version=config.custody_version,
        labels=tuple(
            sorted((r001_label, *sealed_labels), key=lambda item: item.fixture_id)
        ),
        schema_version=OWNER_LABEL_MANIFEST_SCHEMA,
    )
    public_materials = QualificationMaterialManifestV1(
        suite_id=config.suite_id,
        benchmark_version=config.benchmark_version,
        fixture_class=FixtureClass.PUBLIC,
        entries=(r001_entry,),
    )
    sealed_materials = QualificationMaterialManifestV1(
        suite_id=config.suite_id,
        benchmark_version=config.benchmark_version,
        fixture_class=FixtureClass.SEALED,
        entries=sealed_entries,
    )
    config_object = store.put_mapping(config.to_mapping())
    visible_object = store.put_mapping(visible_manifest.to_mapping())
    labels_object = store.put_mapping(owner_labels.to_mapping())
    public_materials_object = store.put_mapping(public_materials.to_mapping())
    sealed_materials_object = store.put_mapping(sealed_materials.to_mapping())
    exact_object_bindings = (
        (config_object.sha256, config.digest, "configuration"),
        (visible_object.sha256, visible_manifest.digest, "visible manifest"),
        (labels_object.sha256, owner_labels.digest, "Owner-private label manifest"),
        (public_materials_object.sha256, public_materials.digest, "public materials"),
        (sealed_materials_object.sha256, sealed_materials.digest, "sealed materials"),
    )
    if any(stored != expected for stored, expected, _ in exact_object_bindings):
        raise ValueError("canonical object digest mismatch during preparation")

    custody = QualificationCustodyManifestV1(
        suite_id=config.suite_id,
        benchmark_version=config.benchmark_version,
        custody_version=config.custody_version,
        reviewer_visible_manifest_digest=visible_manifest.digest,
        owner_label_manifest_digest=owner_labels.digest,
        public_material_manifest_digest=public_materials.digest,
        sealed_material_manifest_digest=sealed_materials.digest,
        variant_seed_commitment_digest=variant_commitment_object_digest,
        custodian_identity_digest=custodian_identity_object_digest,
        owner_private_store_ref=str(owner_root),
    )
    custody_object = store.put_mapping(custody.to_mapping())
    if custody_object.sha256 != custody.digest:
        raise ValueError("custody content-address binding mismatch")

    protocol_path = repository_root / "docs" / "architecture" / "REVIEW_MESH_PROTOCOL_V1.md"
    harness_path = (
        repository_root
        / "control-plane"
        / "src"
        / "local_ai_control"
        / "services"
        / "review_mesh_qualification_harness.py"
    )
    script_path = Path(__file__).resolve()
    protocol_digest = _file_sha256(protocol_path)
    harness_digest = _file_sha256(harness_path)
    variant_generator_digest = _file_sha256(script_path)
    public_plan_digest = canonical_digest(public_plan)
    if _assert_clean_exact_repository(repository_root) != repository_sha:
        raise ValueError("repository SHA or worktree changed during preparation")

    material_proposal = {
        "schema_version": MATERIAL_PROPOSAL_SCHEMA,
        "epoch_id": _identifier(args.epoch_id, "bootstrap epoch id"),
        "authorization_digest": None,
        "authorization_digest_status": "AWAITING_EXPLICIT_OWNER_RECORD",
        "public_fixture_manifest_digest": public_materials.digest,
        "sealed_fixture_manifest_digest": sealed_materials.digest,
        "sealed_label_manifest_digest": owner_labels.digest,
        "reviewer_visible_manifest_digest": visible_manifest.digest,
        "custody_version": config.custody_version,
        "custody_manifest_digest": custody.digest,
        "variant_revision": config.variant_generator_revision,
        "variant_generator_digest": variant_generator_digest,
        "scoring_revision": config.scoring_revision,
        "scoring_configuration_digest": config.digest,
        "owner_private_material_reference": _cas_reference(sealed_materials.digest),
        "owner_private_label_reference": _cas_reference(owner_labels.digest),
        "disclosure_integrity_status": "OWNER_ATTESTATION_REQUIRED",
        "bootstrap_transition_performed": False,
    }
    material_proposal_object = store.put_mapping(material_proposal)
    authorization_fields = {
        "epoch_id": material_proposal["epoch_id"],
        "expires_at": args.expires_at,
        "repository_id": R001_REPOSITORY_ID,
        "repository_sha": repository_sha,
        "protocol_revision": PROTOCOL_VERSION,
        "protocol_digest": protocol_digest,
        "harness_version": config.harness_revision,
        "harness_implementation_digest": harness_digest,
        "configuration_digest": config.digest,
        "allowed_provider_principals": list(providers),
        "allowed_adapter_principals": list(adapters),
        "disallowed_contributor_identity_digests": list(disallowed_contributors),
        "authorized_at": args.authorized_at,
        "zero_unapproved_paid_usage": True,
        "read_only_qualification_scope": True,
        "no_merge_deploy_runtime_authority": True,
    }
    authorization_proposal = {
        "schema_version": AUTHORIZATION_PROPOSAL_SCHEMA,
        "proposal_only": True,
        "bootstrap_state": "BOOTSTRAP_UNINITIALIZED",
        "owner_authorization_status": "MISSING_EXPLICIT_EXACT_OWNER_RECORD",
        "owner_record_digest": None,
        "proposed_authorization_fields": authorization_fields,
        "material_pins_proposal_digest": material_proposal_object.sha256,
        "public_fixture_plan_digest": public_plan_digest,
        "configuration_file_sha256": _file_sha256(config_path),
        "preparation_script_sha256": variant_generator_digest,
        "required_owner_action": (
            "Authorize these exact fields in a new Owner record; then bind that "
            "record digest. Until then STOP before BOOTSTRAP_OWNER_AUTHORIZED."
        ),
        "provider_execution_performed": False,
        "bootstrap_transition_performed": False,
        "ledger_activation_performed": False,
        "registry_activation_performed": False,
    }
    authorization_proposal_object = store.put_mapping(authorization_proposal)
    index = {
        "schema_version": PREPARATION_INDEX_SCHEMA,
        "suite_id": config.suite_id,
        "repository_sha": repository_sha,
        "bootstrap_state": "BOOTSTRAP_UNINITIALIZED",
        "configuration_digest": config.digest,
        "reviewer_visible_manifest_digest": visible_manifest.digest,
        "owner_private_label_manifest_digest": owner_labels.digest,
        "public_material_manifest_digest": public_materials.digest,
        "sealed_material_manifest_digest": sealed_materials.digest,
        "custody_manifest_digest": custody.digest,
        "material_pins_proposal_digest": material_proposal_object.sha256,
        "owner_authorization_proposal_digest": authorization_proposal_object.sha256,
        "owner_private_store_ref": str(owner_root),
        "next_action": "EXACT_OWNER_AUTHORIZATION_REQUIRED_THEN_STOP",
        "provider_execution_performed": False,
        "bootstrap_transition_performed": False,
        "ledger_activation_performed": False,
    }
    index_object = store.put_mapping(index)
    return {
        **index,
        "preparation_index_digest": index_object.sha256,
        "owner_authorization_proposal_ref": _cas_reference(
            authorization_proposal_object.sha256
        ),
        "material_pins_proposal_ref": _cas_reference(material_proposal_object.sha256),
        "custody_manifest_ref": _cas_reference(custody.digest),
        "owner_private_label_manifest_ref": _cas_reference(owner_labels.digest),
        "sealed_material_manifest_ref": _cas_reference(sealed_materials.digest),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.set_defaults(
        repository_root=str(REPOSITORY_ROOT),
        config=str(REPOSITORY_ROOT / "config" / "review-mesh-qualification-harness-v1.json"),
        public_plan=str(
            REPOSITORY_ROOT
            / "benchmarks"
            / "review-mesh-g0b-v1"
            / "public-fixture-plan-v1.json"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser(
        "validate-config",
        help="validate only repository-visible configuration and fixture plan",
    )
    validate.add_argument("--repository-root", default=argparse.SUPPRESS)
    validate.add_argument("--config", default=argparse.SUPPRESS)
    validate.add_argument("--public-plan", default=argparse.SUPPRESS)

    prepare = subparsers.add_parser(
        "prepare",
        help="write exact private CAS materials and an authorization proposal",
    )
    prepare.add_argument("--repository-root", default=argparse.SUPPRESS)
    prepare.add_argument("--config", default=argparse.SUPPRESS)
    prepare.add_argument("--public-plan", default=argparse.SUPPRESS)
    prepare.add_argument("--owner-private-root", required=True)
    prepare.add_argument("--sealed-source", required=True)
    prepare.add_argument("--epoch-id", required=True)
    prepare.add_argument("--authorized-at", required=True)
    prepare.add_argument("--expires-at", required=True)
    prepare.add_argument("--allowed-provider", action="append", required=True)
    prepare.add_argument("--allowed-adapter", action="append", required=True)
    prepare.add_argument(
        "--disallowed-contributor-identity-digest",
        action="append",
        default=[],
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "validate-config":
        config, plan = _load_config_and_plan(Path(args.config), Path(args.public_plan))
        result = {
            "status": "VALID",
            "suite_id": config.suite_id,
            "reviewer_class": config.reviewer_class.value,
            "risk_levels": [item.value for item in config.risk_levels],
            "configuration_digest": config.digest,
            "configuration_file_sha256": _file_sha256(Path(args.config)),
            "public_fixture_plan_digest": canonical_digest(plan),
            "public_fixture_plan_file_sha256": _file_sha256(Path(args.public_plan)),
            "provider_execution_performed": False,
            "bootstrap_transition_performed": False,
            "ledger_activation_performed": False,
        }
    else:
        result = _prepare(args)
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
