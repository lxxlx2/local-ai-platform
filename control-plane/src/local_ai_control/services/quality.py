"""Deterministic quality governance; no candidate may self-approve."""
from dataclasses import dataclass
from enum import StrEnum


class ReviewState(StrEnum):
    DRAFT = "DRAFT"; IMPLEMENTING = "IMPLEMENTING"; SELF_TESTING = "SELF_TESTING"
    SELF_TEST_FAILED = "SELF_TEST_FAILED"; SELF_TESTED = "SELF_TESTED"; REVIEW_PENDING = "REVIEW_PENDING"
    IN_REVIEW = "IN_REVIEW"; REVIEW_FAILED = "REVIEW_FAILED"; REVISION = "REVISION"
    REVIEW_PASSED = "REVIEW_PASSED"; SECURITY_GATE = "SECURITY_GATE"; ACCEPTANCE_READY = "ACCEPTANCE_READY"
    USER_ACCEPTED = "USER_ACCEPTED"; DEPLOY_READY = "DEPLOY_READY"; DEPLOYED = "DEPLOYED"; FAILED = "FAILED"


class Severity(StrEnum):
    BLOCKING = "BLOCKING"; HIGH = "HIGH"; MEDIUM = "MEDIUM"; LOW = "LOW"; IMPROVEMENT = "IMPROVEMENT"


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str; candidate_id: str; review_round: int; severity: Severity; category: str
    location: str; evidence: str; expected_behavior: str; actual_behavior: str; risk: str
    required_fix: str; regression_required: bool; reviewer: str; status: str = "OPEN"
    created_at: str = ""

    def close_for(self, new_candidate_id: str, *, closer: str, producer: str, reviewer: str, independent_review_passed: bool) -> "ReviewFinding":
        if not independent_review_passed or closer in {producer, reviewer} or reviewer == producer:
            raise ValueError("finding can only close after independent re-review")
        return ReviewFinding(**{**self.__dict__, "status": "CLOSED", "candidate_id": new_candidate_id})


@dataclass(frozen=True)
class QualityPolicy:
    task_type: str; deterministic_checks: tuple[str, ...]; independent_review: bool
    security_gate: bool; owner_approval: bool; rollback_required: bool


class QualityPolicyRegistry:
    _HIGH_RISK = {"CONFIG_CHANGE", "DATABASE_MIGRATION", "SECURITY_CHANGE", "MODEL_PROVIDER_CHANGE", "FILE_PROCESSING", "PUBLIC_FEATURE", "PRIVATE_CONTROL", "PUBLISHING", "DELETE_OPERATION"}
    _NAMES = ("CHAT_RESPONSE", "CODE_CHANGE", "CONFIG_CHANGE", "DATABASE_MIGRATION", "SECURITY_CHANGE", "MODEL_PROVIDER_CHANGE", "FILE_PROCESSING", "PUBLIC_FEATURE", "PRIVATE_CONTROL", "CONTENT_GENERATION", "PUBLISHING", "DELETE_OPERATION")

    def __init__(self):
        self._policies = {name: QualityPolicy(name, ("unit", "integration", "regression"), name not in {"CHAT_RESPONSE", "CONTENT_GENERATION"}, name in self._HIGH_RISK, name in {"PUBLISHING", "DELETE_OPERATION", "MODEL_PROVIDER_CHANGE"}, name in self._HIGH_RISK) for name in self._NAMES}

    def get(self, task_type: str) -> QualityPolicy:
        return self._policies[task_type]


class QualityGateService:
    def evaluate(self, *, candidate_id: str, task_type: str, producer: str, reviewer: str | None, tests_pass: bool, review_passed: bool, security_passed: bool, owner_approved: bool = False) -> str:
        policy = QualityPolicyRegistry().get(task_type)
        if not tests_pass or (policy.security_gate and not security_passed):
            return "BLOCKED"
        if not review_passed or reviewer is None or reviewer == producer:
            return "REVIEW_REQUIRED"
        if policy.owner_approval and not owner_approved:
            return "BLOCKED"
        return "ACCEPTANCE_READY"


def reconcile_review_completion(current: ReviewState, reviewer_status: str, *, active_candidate_id: str | None = None, result_candidate_id: str | None = None) -> ReviewState:
    """Recovery-safe polling reconciliation when a detached-review callback is unavailable."""
    if current not in {ReviewState.REVIEW_PENDING, ReviewState.IN_REVIEW} or not active_candidate_id or result_candidate_id != active_candidate_id:
        return current
    if reviewer_status == "PASS":
        return ReviewState.REVIEW_PASSED
    if reviewer_status == "FAIL":
        return ReviewState.REVISION
    if reviewer_status == "COMPLETED_UNKNOWN":
        return ReviewState.REVIEW_FAILED
    return current
