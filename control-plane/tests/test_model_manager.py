from local_ai_control.services.models import ModelHealth, ModelManager, ModelRegistry, MemoryPreflight, MemorySnapshot

class Adapter:
    def __init__(self, healthy=True): self.events=[]; self.healthy=healthy
    def load(self,p): self.events.append(("load",p.profile_id))
    def unload(self,p): self.events.append(("unload",p.profile_id))
    def health(self,p): self.events.append(("health",p.profile_id)); return ModelHealth(self.healthy)

def manager(adapters,available=48):
    aliases={
        "MAIN":{"profile":"local-qwen38","status":"QUALIFIED","max_context_tokens":16384},
        "FAST":{"profile":"local-qwen36","status":"VALIDATED"},
        "FALLBACK":{"profile":"local-qwen36","status":"VALIDATED"},
        "VISION":{"profile":"local-qwen38","status":"QUALIFIED"},
        "VIDEO_UNDERSTANDING":{"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"},
    }
    return ModelManager(ModelRegistry(aliases=aliases),adapters,MemoryPreflight(lambda:MemorySnapshot(48,available,0,"NORMAL"),reserve_gib=1))

def test_heavy_switch_unloads_before_loading_and_is_mutually_exclusive():
    omlx=Adapter(); vlm=Adapter(); m=manager({"local-omlx":omlx,"local-mlx-vlm":vlm})
    assert m.request("local-qwen36").status=="READY"
    assert m.request("local-qwen38").status=="READY"
    assert omlx.events.index(("unload","local-qwen36"))>=0
    assert vlm.events.index(("load","local-qwen38"))>=0
    assert m.active_profile_id=="local-qwen38"

def test_failed_target_rolls_back_to_previous_healthy_model():
    omlx=Adapter(); vlm=Adapter(); m=manager({"local-omlx":omlx,"local-mlx-vlm":vlm})
    m.request("local-qwen36"); vlm.healthy=False
    result=m.request("local-qwen38")
    assert result.status=="FAILED" and result.rolled_back_to=="local-qwen36"
    assert m.active_profile_id=="local-qwen36"

def test_preflight_failure_never_claims_target_ready():
    omlx=Adapter(); vlm=Adapter(); m=manager({"local-omlx":omlx,"local-mlx-vlm":vlm},available=20)
    result=m.request("local-qwen38")
    assert result.status=="FAILED" and result.active_profile_id is None
    assert not vlm.events

def test_unqualified_profile_is_never_loaded():
    aliases={"MAIN":{"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"},"FAST":{"profile":"local-qwen36","status":"VALIDATED"},"FALLBACK":{"profile":"local-qwen36","status":"VALIDATED"},"VISION":{"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"},"VIDEO_UNDERSTANDING":{"profile":"local-qwen38","status":"REGISTERED_NOT_QUALIFIED"}}
    vlm=Adapter(); m=ModelManager(ModelRegistry(aliases=aliases),{"local-mlx-vlm":vlm},MemoryPreflight(lambda:MemorySnapshot(48,48,0,"NORMAL")))
    result=m.request("local-qwen38")
    assert result.status=="NOT_ELIGIBLE" and result.failure_category=="NOT_QUALIFIED" and not vlm.events
