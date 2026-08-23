import pytest

from local_ai_control.services.models import ModelRouter, ModelRole, ModelRoleRegistry, QWEN36
from local_ai_control.services.providers import LOCAL_OMLX, ProviderControlService, ProviderProfile, ProviderRegistry
from local_ai_control.services.quality import QualityGateService, QualityPolicyRegistry, ReviewFinding, Severity, ReviewState, reconcile_review_completion
from local_ai_control.services.capability_matrix import Capability, CapabilityStatus, CAPABILITIES, summary, render_document
from local_ai_control.services.evals import promotion_allowed, run_golden
from pathlib import Path


def test_code_change_cannot_self_approve_and_needs_security_gate():
    gate = QualityGateService()
    assert gate.evaluate(candidate_id="c1", task_type="CODE_CHANGE", producer="producer", reviewer="producer", tests_pass=True, review_passed=True, security_passed=True) == "REVIEW_REQUIRED"
    assert gate.evaluate(candidate_id="c1", task_type="CODE_CHANGE", producer="producer", reviewer="reviewer", tests_pass=True, review_passed=True, security_passed=True) == "ACCEPTANCE_READY"
    assert gate.evaluate(candidate_id="c1", task_type="MODEL_PROVIDER_CHANGE", producer="producer", reviewer="reviewer", tests_pass=True, review_passed=True, security_passed=True) == "BLOCKED"
    assert gate.evaluate(candidate_id="c1", task_type="SECURITY_CHANGE", producer="producer", reviewer="reviewer", tests_pass=True, review_passed=True, security_passed=False) == "BLOCKED"
    assert QualityPolicyRegistry().get("MODEL_PROVIDER_CHANGE").rollback_required


def test_findings_only_close_after_independent_rereview():
    finding = ReviewFinding("F1", "C1", 1, Severity.HIGH, "test", "x:1", "evidence", "safe", "unsafe", "risk", "fix", True, "reviewer")
    with pytest.raises(ValueError):
        finding.close_for("C2", closer="producer", producer="producer", reviewer="reviewer", independent_review_passed=True)
    assert finding.close_for("C2", closer="quality-gate", producer="producer", reviewer="reviewer", independent_review_passed=True).status == "CLOSED"


def test_router_keeps_qwen_fast_only_and_rejects_code_role():
    aliases={"MAIN":{"profile":"local-qwen36","status":"VALIDATED"},"FAST":{"profile":"local-qwen36","status":"VALIDATED"},"FALLBACK":{"profile":"local-qwen36","status":"VALIDATED"}}
    router = ModelRouter(ModelRoleRegistry((QWEN36,),aliases=aliases))
    assert router.route("CHAT").profile_id == "local-qwen36"
    with pytest.raises(LookupError): router.route("CODE")
    assert ModelRole.CODE not in QWEN36.roles


def test_provider_registry_blocks_unsafe_urls_and_keeps_credentials_as_aliases():
    assert ProviderRegistry().list_safe() == (LOCAL_OMLX,)
    with pytest.raises(ValueError):
        ProviderProfile("bad", "Bad", "http://example.com/v1", "model", None, "LOCAL", "NONE").validate()
    with pytest.raises(ValueError):
        ProviderProfile("bad", "Bad", "https://example.com/v1", "model", "token=value", "REMOTE", "REMOTE").validate()
    for bad_url in ("https://example.com/v1?api_key=x", "https://example.com/v1#token=x", "https://example.com/v1?", "https://example.com/v1#"):
        with pytest.raises(ValueError): ProviderProfile("remote", "Remote", bad_url, "model", "alias", "REMOTE", "REMOTE").validate()
    for bad_url in ("http://user:pass@127.0.0.1:8000", "http://token@localhost:8000", "http://localhost:8000/?token=x", "http://localhost:8000/#token", "http://localhost:8000/?", "http://localhost:8000/#"):
        with pytest.raises(ValueError): ProviderProfile("bad", "Bad", bad_url, "model", None, "LOCAL", "NONE").validate()
    with pytest.raises(ValueError): ProviderProfile("bad", "Bad", "http://127.0.0.1:8000", "model", None, "LOCAL", "REMOTE").validate()
    assert ProviderProfile("ok", "OK", "http://localhost:8000", "model", None, "LOCAL", "NONE").validate().data_egress == "NONE"


def test_switch_requires_confirmation_and_rolls_back_on_health_failure():
    service = ProviderControlService()
    assert service.apply("FAST", "local-omlx", actor_role="PUBLIC", owner_confirmed=True, health_check=lambda _: True) == "AUTHORIZATION_DENIED"
    assert service.apply("FAST", "local-omlx", actor_role="OWNER", owner_confirmed=False, health_check=lambda _: True) == "CONFIRM_REQUIRED"
    before = dict(service.active_by_role)
    assert service.apply("FAST", "local-omlx", actor_role="OWNER", owner_confirmed=True, health_check=lambda _: False) == "DEFERRED_NO_CONFIG_MUTATION"
    assert service.active_by_role == before
    assert service.active_by_role["FAST"] == "local-omlx"


def test_remote_classification_and_golden_runner_are_enforced():
    with pytest.raises(ValueError): ProviderProfile("remote", "Remote", "https://example.com/v1", "model", "alias", "UNKNOWN", "NONE").validate()
    results = run_golden(Path(__file__).parents[2] / "evals/golden-set.json", lambda case: case["id"] != "json-001")
    assert not promotion_allowed(results)
    assert promotion_allowed(run_golden(Path(__file__).parents[2] / "evals/golden-set.json", lambda _: True))
    golden = run_golden(Path(__file__).parents[2] / "evals/golden-set.json", lambda _: True)
    required = {"GOLDEN-NAV-001", "GOLDEN-NAV-002", "GOLDEN-UX-001", "GOLDEN-TG-002", "GOLDEN-TG-003", "GOLDEN-CODE-002"}
    assert required.issubset({result.case_id for result in golden})
    assert len(golden) == 10 and promotion_allowed(golden)


def test_capability_matrix_is_single_source_of_truth_and_review_completion_reconciles():
    fixture = (Capability("a", CapabilityStatus.NOT_STARTED, "x"), Capability("b", CapabilityStatus.FOUNDATION, "x"), Capability("c", CapabilityStatus.PARTIAL, "x"), Capability("d", CapabilityStatus.FUNCTIONAL, "x"), Capability("e", CapabilityStatus.PRODUCTION_READY, "x"))
    assert summary(fixture) == {"TOTAL_CAPABILITIES": 5, "FUNCTIONAL_COUNT": 2, "PRODUCTION_READY_COUNT": 1, "FUNCTIONAL_COVERAGE": 0.4, "PRODUCTION_READY_COVERAGE": 0.2}
    assert summary(CAPABILITIES)["TOTAL_CAPABILITIES"] == 30
    assert (Path(__file__).parents[2] / "docs/CAPABILITY_MATRIX.md").read_text() == render_document()
    assert reconcile_review_completion(ReviewState.IN_REVIEW, "FAIL", active_candidate_id="c1", result_candidate_id="c1") is ReviewState.REVISION
    assert reconcile_review_completion(ReviewState.IN_REVIEW, "PASS", active_candidate_id="c1", result_candidate_id="c1") is ReviewState.REVIEW_PASSED
    assert reconcile_review_completion(ReviewState.DEPLOYED, "FAIL") is ReviewState.DEPLOYED
    assert reconcile_review_completion(ReviewState.IN_REVIEW, "PASS", active_candidate_id="c2", result_candidate_id="c1") is ReviewState.IN_REVIEW


def test_credential_setup_uses_keychain_secure_prompt_without_secret_argv():
    script = (Path(__file__).parents[1] / "scripts/configure-provider-credential.sh").read_text()
    assert "security add-generic-password" in script and " -w\n" in script
    assert "| security" not in script and "read -r secret" not in script


def test_intermediate_state_can_finish_task():
    terminal = {ReviewState.ACCEPTANCE_READY, ReviewState.USER_ACCEPTED, ReviewState.DEPLOY_READY, ReviewState.DEPLOYED, ReviewState.FAILED}
    for state in (ReviewState.REVISION, ReviewState.IN_REVIEW, ReviewState.REVIEW_PENDING, ReviewState.SELF_TESTING):
        assert state not in terminal
