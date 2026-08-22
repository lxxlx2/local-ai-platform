import pytest
from local_ai_control.services.models import ModelRegistry, ModelRole

def test_registry_has_all_production_roles_and_never_qualifies_registered_candidate():
    registry=ModelRegistry()
    required={ModelRole.MAIN,ModelRole.FAST,ModelRole.FALLBACK,ModelRole.RAW,ModelRole.VISION,
              ModelRole.VIDEO_UNDERSTANDING,ModelRole.STT_MAIN,ModelRole.TTS_MAIN,ModelRole.TTS_DESIGN,
              ModelRole.IMAGE_MAIN,ModelRole.VIDEO_MAIN,ModelRole.VIDEO_HIGH,ModelRole.EMBED,ModelRole.RERANK}
    assert required <= {role for model in registry.models.values() for role in model.roles}
    assert registry.models["local-qwen38"].model_id=="mlx-community/Qwen3.8-27B-8bit"
    assert registry.eligible(ModelRole.VISION)==[]

def test_raw_is_owner_only_and_unknown_repo_cannot_bypass_registry():
    registry=ModelRegistry()
    with pytest.raises(PermissionError): registry.require("owner-qwen38-raw",owner=False)
    assert registry.require("owner-qwen38-raw",owner=True).owner_only
    with pytest.raises(LookupError): registry.require("attacker/repo",owner=True)
