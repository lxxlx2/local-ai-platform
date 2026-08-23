from local_ai_control.services.models import MemoryPreflight, MemorySnapshot

def test_preflight_reserves_system_memory():
    guard=MemoryPreflight(lambda: MemorySnapshot(48,40,0,"NORMAL"),reserve_gib=6)
    assert guard.check(34).allowed
    assert guard.check(35).allowed
    assert not guard.check(43).allowed

def test_preflight_rejects_critical_pressure_even_if_available_probe_is_high():
    result=MemoryPreflight(lambda: MemorySnapshot(48,40,2,"CRITICAL")).check(1)
    assert not result.allowed and result.reason=="MEMORY_PRESSURE_CRITICAL"

def test_preflight_uses_reclaimable_owned_model_but_rejects_runaway_swap():
    normal=MemoryPreflight(lambda: MemorySnapshot(48,8,1,"NORMAL",reclaimable_gib=10))
    assert normal.check(34,owned_reclaimable_gib=28).allowed
    runaway=MemoryPreflight(lambda: MemorySnapshot(48,40,1,"NORMAL",reclaimable_gib=40,swap_delta_gib=2.1))
    result=runaway.check(34)
    assert not result.allowed and result.reason=="SWAP_RUNAWAY"

def test_preflight_detects_swap_growth_between_live_samples():
    samples=iter((MemorySnapshot(48,40,1,"NORMAL",reclaimable_gib=40),MemorySnapshot(48,40,3.2,"NORMAL",reclaimable_gib=40)))
    guard=MemoryPreflight(lambda: next(samples),max_swap_delta_gib=2)
    assert guard.check(34).allowed
    assert guard.check(34).reason=="SWAP_RUNAWAY"

def test_preflight_rejects_high_absolute_swap_on_first_sample():
    guard=MemoryPreflight(lambda: MemorySnapshot(48,34,6.1,"NORMAL",reclaimable_gib=34))
    result=guard.check(34)
    assert not result.allowed and result.reason=="SWAP_ABSOLUTE_LIMIT"

def test_preflight_accepts_qualification_level_swap_when_other_signals_are_safe():
    guard=MemoryPreflight(lambda: MemorySnapshot(48,34,3.999,"NORMAL",reclaimable_gib=34))
    assert guard.check(34).allowed
