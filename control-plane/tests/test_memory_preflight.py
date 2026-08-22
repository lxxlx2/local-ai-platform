from local_ai_control.services.models import MemoryPreflight, MemorySnapshot

def test_preflight_reserves_system_memory():
    guard=MemoryPreflight(lambda: MemorySnapshot(48,40,0,"NORMAL"),reserve_gib=6)
    assert guard.check(34).allowed
    assert not guard.check(35).allowed

def test_preflight_rejects_critical_pressure_even_if_available_probe_is_high():
    result=MemoryPreflight(lambda: MemorySnapshot(48,40,2,"CRITICAL")).check(1)
    assert not result.allowed and result.reason=="MEMORY_PRESSURE_CRITICAL"
