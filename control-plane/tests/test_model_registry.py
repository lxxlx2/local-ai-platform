import json
from pathlib import Path

import pytest

from local_ai_control.services.models import ModelRegistry, ModelRole, ModelRouter

SOURCE = Path("/Users/jerson/AI/config/model-registry-v0.1.json")


def registry_with(tmp_path, **changes):
    payload=json.loads(SOURCE.read_text())
    for role, value in changes.items(): payload["production_aliases"][role]=value
    path=tmp_path/"registry.json"; path.write_text(json.dumps(payload))
    return ModelRegistry(config_path=path)


def test_unqualified_qwen38_is_not_runtime_eligible(tmp_path):
    registry=registry_with(tmp_path,MAIN={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"},VISION={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"})
    required={ModelRole.MAIN,ModelRole.FAST,ModelRole.FALLBACK,ModelRole.RAW,ModelRole.VISION,
              ModelRole.VIDEO_UNDERSTANDING,ModelRole.STT_MAIN,ModelRole.TTS_MAIN,ModelRole.TTS_DESIGN,
              ModelRole.IMAGE_MAIN,ModelRole.VIDEO_MAIN,ModelRole.VIDEO_HIGH,ModelRole.EMBED,ModelRole.RERANK}
    assert required <= {role for model in registry.models.values() for role in model.roles}
    assert registry.eligible(ModelRole.MAIN)==[] and registry.eligible(ModelRole.VISION)==[]
    assert registry.eligible(ModelRole.FAST)[0].profile_id=="local-qwen36"


def test_qualified_main_becomes_eligible_and_drives_normal_chat(tmp_path):
    registry=registry_with(tmp_path,MAIN={"profile":"local-qwen38","status":"QUALIFIED","max_context_tokens":16384})
    assert registry.eligible(ModelRole.MAIN)[0].profile_id=="local-qwen38"
    assert ModelRouter(registry).route("CHAT").profile_id=="local-qwen38"
    assert ModelRouter(registry).route("FAST").profile_id=="local-qwen36"


@pytest.mark.parametrize("mutation",[
    lambda p:p["production_aliases"]["MAIN"].update(status="READY"),
    lambda p:p["production_aliases"]["MAIN"].update(profile="attacker/repo"),
    lambda p:p["production_aliases"]["MAIN"].update(extra="unsafe"),
    lambda p:p["production_aliases"].pop("FAST"),
    lambda p:p.update(extra="unsafe"),
])
def test_malformed_registry_fails_closed(tmp_path,mutation):
    payload=json.loads(SOURCE.read_text()); mutation(payload)
    path=tmp_path/"bad.json"; path.write_text(json.dumps(payload))
    with pytest.raises(ValueError): ModelRegistry(config_path=path)


def test_config_cannot_weaken_immutable_safety_metadata(tmp_path):
    payload=json.loads(SOURCE.read_text()); payload["policy"]["raw_owner_only"]=False
    path=tmp_path/"bad.json"; path.write_text(json.dumps(payload))
    with pytest.raises(ValueError): ModelRegistry(config_path=path)
    registry=ModelRegistry()
    with pytest.raises(PermissionError): registry.require("owner-qwen38-raw",owner=False)
    raw=registry.require("owner-qwen38-raw",owner=True)
    assert raw.owner_only and raw.model_id=="orcarouter/Qwen3.8-27B-Uncensored-MLX#8-bit"
    with pytest.raises(LookupError): registry.require("attacker/repo",owner=True)


def test_chat_falls_back_when_main_missing_unhealthy_or_resource_denied(tmp_path):
    unqualified=registry_with(tmp_path,MAIN={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"},VISION={"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"})
    assert ModelRouter(unqualified).route("CHAT").profile_id=="local-qwen36"
    qualified=registry_with(tmp_path,MAIN={"profile":"local-qwen38","status":"QUALIFIED","max_context_tokens":16384})
    assert ModelRouter(qualified,health_check=lambda p:p.profile_id!="local-qwen38").route("CHAT").profile_id=="local-qwen36"
    assert ModelRouter(qualified,resource_check=lambda p:p.profile_id!="local-qwen38").route("CHAT").profile_id=="local-qwen36"
