import json
from pathlib import Path

import pytest

from local_ai_control.services.qualification_evidence_store import (
    DEFAULT_HOST_SCOPE,
    DeploymentMode,
    QualificationEvidenceStore,
)
from local_ai_control.services.workload_admission import WorkloadClass
from local_ai_control.services.workload_router import EvidenceStatus


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPO_ROOT / "config/qualification-evidence-v0.1.json"


def load_payload():
    return json.loads(REAL_CONFIG.read_text(encoding="utf-8"))


def write_payload(tmp_path, payload):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_checked_in_ledger_has_current_representative_evidence():
    store = QualificationEvidenceStore(config_path=REAL_CONFIG)

    assert store.host_scope.scope_id == DEFAULT_HOST_SCOPE

    qwen36 = store.record_for(
        "local-qwen36",
        deployment_mode=DeploymentMode.ON_DEMAND_COLD_START,
        workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD,
    )
    assert qwen36 is not None
    assert qwen36.status is EvidenceStatus.PASS
    assert qwen36.candidate_ref == "ad444f54621a9dd45f6409306ada20f630a3d32a"
    assert qwen36.source_ref == "github:issue/24#issuecomment-5461045048"

    qwen38 = store.record_for(
        "local-qwen38",
        deployment_mode=DeploymentMode.ON_DEMAND_COLD_START,
        workload_class=WorkloadClass.REPRESENTATIVE_WORKLOAD,
    )
    assert qwen38 is not None
    assert qwen38.status is EvidenceStatus.BLOCKED
    assert qwen38.reason == "RELATIVE_SWAP_GROWTH_LIMIT"
    assert qwen38.source_ref == "github:issue/19#issuecomment-5460597710"


def test_router_adapter_preserves_unknown_stress_and_mode_separation():
    store = QualificationEvidenceStore(config_path=REAL_CONFIG)

    on_demand = store.routing_evidence(
        deployment_mode=DeploymentMode.ON_DEMAND_COLD_START,
    )
    assert on_demand["local-qwen36"].representative is EvidenceStatus.PASS
    assert on_demand["local-qwen36"].stress_status("IDE") is EvidenceStatus.UNKNOWN
    assert on_demand["local-qwen36"].stress_status("UNITY") is EvidenceStatus.UNKNOWN
    assert on_demand["local-qwen38"].representative is EvidenceStatus.BLOCKED

    preloaded = store.routing_evidence(
        deployment_mode=DeploymentMode.PRELOADED_DAEMON,
    )
    assert "local-qwen36" not in preloaded
    assert "local-qwen38" not in preloaded


def test_host_scope_mismatch_fails_closed(tmp_path):
    path = write_payload(tmp_path, load_payload())
    with pytest.raises(ValueError, match="host scope mismatch"):
        QualificationEvidenceStore(
            config_path=path,
            expected_host_scope="another-host-scope",
        )


def test_wrong_model_binding_fails_closed(tmp_path):
    payload = load_payload()
    payload["records"][0]["model_id"] = "wrong/model"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="model binding mismatch"):
        QualificationEvidenceStore(config_path=path)


def test_unknown_profile_fails_closed(tmp_path):
    payload = load_payload()
    payload["records"][0]["profile_id"] = "unknown-profile"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="unknown profile"):
        QualificationEvidenceStore(config_path=path)


def test_lab_evidence_is_rejected(tmp_path):
    payload = load_payload()
    payload["records"][0]["workload_class"] = "LAB"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="LAB evidence"):
        QualificationEvidenceStore(config_path=path)


def test_deliberate_reduction_is_rejected(tmp_path):
    payload = load_payload()
    payload["records"][0]["deliberate_reductions"] = ["closed browser"]
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="deliberately reduced workload"):
        QualificationEvidenceStore(config_path=path)


def test_unknown_status_must_be_absent_not_persisted(tmp_path):
    payload = load_payload()
    payload["records"][0]["status"] = "UNKNOWN"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="cannot be UNKNOWN"):
        QualificationEvidenceStore(config_path=path)


def test_stress_record_requires_supported_category(tmp_path):
    payload = load_payload()
    record = dict(payload["records"][0])
    record["workload_class"] = "STRESS_COEXISTENCE"
    record["stress_category"] = "GPU_BENCHMARK"
    payload["records"] = [record]
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="supported stress category"):
        QualificationEvidenceStore(config_path=path)


def test_representative_record_cannot_claim_stress_category(tmp_path):
    payload = load_payload()
    payload["records"][0]["stress_category"] = "IDE"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="cannot declare stress category"):
        QualificationEvidenceStore(config_path=path)


def test_duplicate_active_evidence_key_fails_closed(tmp_path):
    payload = load_payload()
    payload["records"].append(dict(payload["records"][0]))
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="duplicate qualification evidence key"):
        QualificationEvidenceStore(config_path=path)


def test_candidate_reference_type_is_strict(tmp_path):
    payload = load_payload()
    payload["records"][0]["candidate_ref"] = "deadbeef"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="40-hex"):
        QualificationEvidenceStore(config_path=path)

    payload = load_payload()
    payload["records"][1]["candidate_ref"] = "a" * 40
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="64-hex"):
        QualificationEvidenceStore(config_path=path)


def test_source_ref_must_be_durable_github_issue_reference(tmp_path):
    payload = load_payload()
    payload["records"][0]["source_ref"] = "/tmp/local-only-result"
    path = write_payload(tmp_path, payload)

    with pytest.raises(ValueError, match="durable GitHub issue source"):
        QualificationEvidenceStore(config_path=path)


def test_stress_pass_is_compiled_only_for_matching_deployment_mode(tmp_path):
    payload = load_payload()
    stress = dict(payload["records"][0])
    stress["deployment_mode"] = "PRELOADED_DAEMON"
    stress["workload_class"] = "STRESS_COEXISTENCE"
    stress["stress_category"] = "IDE"
    stress["candidate_ref_type"] = "GIT_COMMIT"
    stress["candidate_ref"] = "1" * 40
    stress["source_ref"] = "github:issue/24#issuecomment-9999999999"
    payload["records"].append(stress)
    path = write_payload(tmp_path, payload)

    store = QualificationEvidenceStore(config_path=path)

    on_demand = store.routing_evidence(
        deployment_mode=DeploymentMode.ON_DEMAND_COLD_START,
    )
    assert on_demand["local-qwen36"].stress_status("IDE") is EvidenceStatus.UNKNOWN

    preloaded = store.routing_evidence(
        deployment_mode=DeploymentMode.PRELOADED_DAEMON,
    )
    assert preloaded["local-qwen36"].representative is EvidenceStatus.UNKNOWN
    assert preloaded["local-qwen36"].stress_status("IDE") is EvidenceStatus.PASS
