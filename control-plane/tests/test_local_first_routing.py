from pathlib import Path

import pytest

from local_ai_control.services.execution_backend import ExecutionPolicy, ToolIntent
from local_ai_control.services.provider_router import (
    Capability,
    HostPermissionProfile,
    InvocationPurpose,
    PrivacyMode,
    ProviderRequest,
    QuotaClass,
    default_provider_router,
)
from local_ai_control.services.provider_usage import ProviderUsageEvent, ProviderUsageLedger


def test_routine_code_is_local_and_never_codex_quota():
    selection = default_provider_router().route(
        ProviderRequest(
            capability=Capability.CODE,
            privacy=PrivacyMode.PRIVATE,
            purpose=InvocationPurpose.ROUTINE,
        )
    )
    assert selection.provider.provider_id == "local-qwen"
    assert selection.quota_class is QuotaClass.NONE
    assert selection.consumes_codex_quota is False


def test_codex_model_is_denied_for_routine_even_when_explicit():
    with pytest.raises(PermissionError):
        default_provider_router().route(
            ProviderRequest(
                capability=Capability.REASONING,
                privacy=PrivacyMode.PUBLIC,
                purpose=InvocationPurpose.ROUTINE,
                explicit_provider="openai-codex",
                premium_codex_allowed=True,
            )
        )


def test_codex_model_requires_explicit_premium_budget_gate():
    router = default_provider_router()
    local = router.route(
        ProviderRequest(
            capability=Capability.PLANNING,
            privacy=PrivacyMode.PUBLIC,
            purpose=InvocationPurpose.PLANNING,
        )
    )
    assert local.provider.provider_id == "local-qwen"
    assert local.consumes_codex_quota is False

    premium = router.route(
        ProviderRequest(
            capability=Capability.PLANNING,
            privacy=PrivacyMode.PUBLIC,
            purpose=InvocationPurpose.PLANNING,
            premium_codex_allowed=True,
        )
    )
    assert premium.provider.provider_id == "openai-codex"
    assert premium.consumes_codex_quota is True


def test_gemini_is_preferred_for_public_independent_review():
    selection = default_provider_router().route(
        ProviderRequest(
            capability=Capability.REVIEW,
            privacy=PrivacyMode.PUBLIC,
            purpose=InvocationPurpose.REVIEW,
        )
    )
    assert selection.provider.provider_id == "gemini"
    assert selection.quota_class is QuotaClass.GEMINI
    assert selection.host_permission_profile is HostPermissionProfile.CLOUD_REVIEW_ONLY


def test_restricted_review_uses_local_until_egress_gate_passes():
    router = default_provider_router()
    local = router.route(
        ProviderRequest(
            capability=Capability.REVIEW,
            privacy=PrivacyMode.RESTRICTED,
            purpose=InvocationPurpose.REVIEW,
            sanitized_for_egress=False,
        )
    )
    assert local.provider.provider_id == "local-qwen"

    gemini = router.route(
        ProviderRequest(
            capability=Capability.REVIEW,
            privacy=PrivacyMode.RESTRICTED,
            purpose=InvocationPurpose.REVIEW,
            sanitized_for_egress=True,
        )
    )
    assert gemini.provider.provider_id == "gemini"


def test_private_review_never_egresses():
    selection = default_provider_router().route(
        ProviderRequest(
            capability=Capability.REVIEW,
            privacy=PrivacyMode.PRIVATE,
            purpose=InvocationPurpose.REVIEW,
            sanitized_for_egress=True,
            premium_codex_allowed=True,
        )
    )
    assert selection.provider.provider_id == "local-qwen"
    assert selection.quota_class is QuotaClass.NONE


def test_owner_raw_requires_explicit_owner_authorization():
    router = default_provider_router()
    with pytest.raises(LookupError):
        router.route(
            ProviderRequest(
                capability=Capability.RESEARCH,
                privacy=PrivacyMode.PRIVATE,
                purpose=InvocationPurpose.OWNER_RAW_RESEARCH,
                owner_authorized=False,
            )
        )

    selection = router.route(
        ProviderRequest(
            capability=Capability.RESEARCH,
            privacy=PrivacyMode.PRIVATE,
            purpose=InvocationPurpose.OWNER_RAW_RESEARCH,
            owner_authorized=True,
        )
    )
    assert selection.provider.provider_id == "local-qwen-owner-raw"
    assert selection.quota_class is QuotaClass.NONE
    assert selection.consumes_codex_quota is False
    assert selection.host_permission_profile is HostPermissionProfile.OWNER_RAW_RESEARCH


def test_owner_raw_cannot_be_selected_for_routine_work():
    with pytest.raises(PermissionError):
        default_provider_router().route(
            ProviderRequest(
                capability=Capability.REASONING,
                privacy=PrivacyMode.PRIVATE,
                purpose=InvocationPurpose.ROUTINE,
                explicit_provider="local-qwen-owner-raw",
                owner_authorized=True,
            )
        )


def test_usage_ledger_counts_only_codex_quota_events(tmp_path):
    router = default_provider_router()
    local_selection = router.route(
        ProviderRequest(Capability.CODE, PrivacyMode.PRIVATE)
    )
    codex_selection = router.route(
        ProviderRequest(
            capability=Capability.PLANNING,
            privacy=PrivacyMode.PUBLIC,
            purpose=InvocationPurpose.PLANNING,
            premium_codex_allowed=True,
        )
    )
    ledger = ProviderUsageLedger(tmp_path / "provider-usage.jsonl")
    ledger.append(
        ProviderUsageEvent.from_selection(
            task_id="local-1",
            selection=local_selection,
            purpose=InvocationPurpose.ROUTINE,
            privacy=PrivacyMode.PRIVATE,
            status="PASS",
        )
    )
    ledger.append(
        ProviderUsageEvent.from_selection(
            task_id="plan-1",
            selection=codex_selection,
            purpose=InvocationPurpose.PLANNING,
            privacy=PrivacyMode.PUBLIC,
            status="PASS",
        )
    )
    assert ledger.codex_quota_event_count() == 1


def test_execution_policy_rejects_network_and_workspace_escape(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    policy = ExecutionPolicy(
        workspace_root=workspace,
        allowed_tools=frozenset({"python"}),
        allowed_write_roots=(workspace,),
        network_allowed=False,
    )
    policy.validate(ToolIntent("python", ("-m", "pytest"), workspace, 60))

    with pytest.raises(PermissionError):
        policy.validate(ToolIntent("python", ("script.py",), workspace, 60, allow_network=True))

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PermissionError):
        policy.validate(ToolIntent("python", ("script.py",), outside, 60))
