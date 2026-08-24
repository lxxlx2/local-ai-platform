from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_ai_control.services.models import (
    MemoryPreflight,
    MemorySnapshot,
    ModelRegistry,
    QWEN36,
    QWEN38,
)
from local_ai_control.services.qwen38_runtime import RuntimeUnavailable
from local_ai_control.services.runtime_providers import (
    HeavyModelConflict,
    ResourcePreflightDenied,
    RuntimeProviderFactory,
)


def snapshot(swap, *, pressure="NORMAL", reclaimable=40):
    return MemorySnapshot(48, reclaimable, swap, pressure, reclaimable_gib=reclaimable)


class Provider:
    def __init__(self, name, healthy=False):
        self.name = name
        self.healthy = healthy

    def health(self):
        if not self.healthy:
            raise OSError("down")
        return {"status": "healthy"}

    def generate(self, *_args, **_kwargs):
        return f"{self.name}-ok"


class SequencedPreflight:
    def __init__(self, events, snapshots):
        self.events = events
        self.snapshots = deque(snapshots)
        self.guard = MemoryPreflight(self._probe, max_swap_used_gib=6)

    def _probe(self):
        if not self.snapshots:
            raise AssertionError("unexpected resource probe")
        return self.snapshots.popleft()

    def admit_owned_transition(self, required_gib):
        result = self.guard.admit_owned_transition(required_gib)
        self.events.append(("admission", result.snapshot.swap_used_gib, result.reason))
        return result

    def check(self, required_gib, *, owned_reclaimable_gib=0):
        assert owned_reclaimable_gib == 0
        result = self.guard.check(required_gib)
        self.events.append(("full_preflight", result.snapshot.swap_used_gib, result.reason))
        return result


class Lifecycle:
    def __init__(self, main, fast, events, *, owned=None, partial_start=None,
                 cleanup_refusal=None, ambiguous=False):
        self.main = main
        self.fast = fast
        self.events = events
        self.owned = set(owned or ())
        self.partial_start = partial_start
        self.cleanup_refusal = cleanup_refusal
        self.ambiguous = ambiguous
        self.maximum_resident = int(main.healthy) + int(fast.healthy)

    def provider(self, profile_id):
        return self.main if profile_id == QWEN38.profile_id else self.fast

    def _record_residency(self):
        resident = int(self.main.healthy) + int(self.fast.healthy)
        self.maximum_resident = max(self.maximum_resident, resident)
        assert resident <= 1

    def admit_owned_transition(self, current_profile_id, _probes):
        self.events.append(("ownership_admission", current_profile_id))
        if self.ambiguous or current_profile_id not in self.owned:
            raise HeavyModelConflict("current heavy runtime is not exactly owned")
        other = QWEN36.profile_id if current_profile_id == QWEN38.profile_id else QWEN38.profile_id
        if other in self.owned:
            raise HeavyModelConflict("second heavy runtime is live")

    def transition_source_state(self, current_profile_id, _probes):
        self.events.append(("source_state", current_profile_id))
        if self.ambiguous:
            raise HeavyModelConflict("ambiguous source")
        return "OWNED" if current_profile_id in self.owned else "ABSENT"

    def safe_stop(self, profile_id, _endpoint):
        self.events.append(("safe_stop", profile_id))
        if profile_id == self.cleanup_refusal:
            raise HeavyModelConflict("cleanup refused")
        self.provider(profile_id).healthy = False
        self.owned.discard(profile_id)
        self._record_residency()

    def wait_stopped(self, profile_id, endpoint, attempts=20):
        self.events.append(("absence_proven", profile_id))
        assert not endpoint()
        assert profile_id not in self.owned

    def prove_all_absent(self, _probes):
        self.events.append(("all_absent_proven",))
        assert not self.main.healthy and not self.fast.healthy
        assert not self.owned

    def reconcile_before_start(self, target_profile_id, _probes):
        self.events.append(("reconcile", target_profile_id))
        assert not self.main.healthy and not self.fast.healthy
        assert not self.owned

    def start(self, profile_id):
        self.events.append(("start", profile_id))
        assert not self.main.healthy and not self.fast.healthy
        target = self.provider(profile_id)
        target.healthy = True
        self.owned.add(profile_id)
        self._record_residency()
        if profile_id == self.partial_start:
            raise RuntimeUnavailable("partial start")

    def capture_started(self, profile_id):
        self.events.append(("capture", profile_id))
        assert profile_id in self.owned
        return SimpleNamespace(pid=1)


def runtime(*, main=True, fast=False, snapshots=(), attempts=3, **lifecycle_options):
    events = []
    main_provider = Provider("main", main)
    fast_provider = Provider("fast", fast)
    owned = lifecycle_options.pop(
        "owned",
        {QWEN38.profile_id} if main else ({QWEN36.profile_id} if fast else set()),
    )
    lifecycle = Lifecycle(
        main_provider, fast_provider, events, owned=owned, **lifecycle_options,
    )
    preflight = SequencedPreflight(events, snapshots)
    factory = RuntimeProviderFactory(
        ModelRegistry(),
        main=main_provider,
        fast=fast_provider,
        preflight=preflight,
        lifecycle=lifecycle,
        sleep=lambda _seconds: events.append(("settle_wait",)),
        post_stop_preflight_attempts=attempts,
        post_stop_preflight_interval=1,
    )
    return factory, main_provider, fast_provider, lifecycle, events


def event_index(events, name, profile_id=None):
    for index, event in enumerate(events):
        if event[0] == name and (profile_id is None or event[1] == profile_id):
            return index
    raise AssertionError(f"event missing: {name} {profile_id}")


def test_memory_admission_does_not_waive_final_six_gib_swap_gate():
    samples = iter((snapshot(9.4), snapshot(9.4)))
    guard = MemoryPreflight(lambda: next(samples), max_swap_used_gib=6)
    assert guard.max_swap_used_gib == 6
    assert guard.admit_owned_transition(24).allowed
    denied = guard.check(24)
    assert not denied.allowed and denied.reason == "SWAP_ABSOLUTE_LIMIT"


def test_owned_main_high_swap_stops_before_fresh_gate_and_starts_fast_after_reclaim():
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(snapshot(9.4), snapshot(4.8)),
    )
    factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert fast.healthy and not main.healthy
    assert event_index(events, "ownership_admission") < event_index(events, "safe_stop")
    assert event_index(events, "absence_proven") < event_index(events, "all_absent_proven")
    assert event_index(events, "all_absent_proven") < event_index(events, "full_preflight")
    assert event_index(events, "full_preflight") < event_index(events, "start", QWEN36.profile_id)
    assert lifecycle.maximum_resident == 1


def test_critical_transition_admission_never_stops_owned_current():
    factory, main, fast, _, events = runtime(
        snapshots=(snapshot(9.4, pressure="CRITICAL"),),
    )
    with pytest.raises(ResourcePreflightDenied) as error:
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert error.value.category == "TRANSITION_ADMISSION_DENIED"
    assert error.value.reason == "MEMORY_PRESSURE_CRITICAL"
    assert not any(event[0] in {"safe_stop", "full_preflight", "start"} for event in events)
    assert main.healthy and not fast.healthy


def test_bounded_post_stop_polling_waits_until_swap_is_at_or_below_ceiling():
    factory, main, fast, _, events = runtime(
        snapshots=(snapshot(9.414), snapshot(9.1), snapshot(7.2), snapshot(5.5)),
        attempts=3,
    )
    factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    probes = [event[1] for event in events if event[0] == "full_preflight"]
    assert probes == [9.1, 7.2, 5.5]
    assert len([event for event in events if event[0] == "settle_wait"]) == 2
    assert event_index(events, "start", QWEN36.profile_id) > max(
        index for index, event in enumerate(events) if event[0] == "full_preflight"
    )


def test_post_stop_poll_exhaustion_never_starts_target():
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(
            snapshot(9.4),
            snapshot(9.4), snapshot(9.4), snapshot(9.4),
            snapshot(4.8),
        ),
        attempts=3,
    )
    with pytest.raises(ResourcePreflightDenied) as error:
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert error.value.category == "POST_STOP_RESOURCE_PREFLIGHT_DENIED"
    assert error.value.reason == "SWAP_ABSOLUTE_LIMIT"
    assert ("start", QWEN36.profile_id) not in events
    assert main.healthy and not fast.healthy
    assert lifecycle.maximum_resident == 1


@pytest.mark.parametrize(
    ("denied_snapshot", "reason"),
    [
        (snapshot(9.4), "SWAP_ABSOLUTE_LIMIT"),
        (snapshot(1, pressure="CRITICAL"), "MEMORY_PRESSURE_CRITICAL"),
        (snapshot(1, reclaimable=4), "INSUFFICIENT_RECLAIMABLE_MEMORY"),
    ],
)
def test_post_stop_resource_denial_never_starts_target_and_restores_only_after_own_gate(
    denied_snapshot, reason,
):
    attempts = 1
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(snapshot(9.4), denied_snapshot, snapshot(1.1)),
        attempts=attempts,
    )
    with pytest.raises(ResourcePreflightDenied) as denied:
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert denied.value.category == "POST_STOP_RESOURCE_PREFLIGHT_DENIED"
    assert denied.value.reason == reason
    assert ("start", QWEN36.profile_id) not in events
    assert ("start", QWEN38.profile_id) in events
    restore_gate = [
        index for index, event in enumerate(events)
        if event[0] == "full_preflight" and event[1] == 1.1
    ][0]
    assert restore_gate < event_index(events, "start", QWEN38.profile_id)
    assert main.healthy and not fast.healthy and lifecycle.maximum_resident == 1


def test_ambiguous_current_ownership_blocks_before_stop_or_target_probe():
    factory, main, fast, _, events = runtime(
        snapshots=(), ambiguous=True,
    )
    with pytest.raises(HeavyModelConflict):
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert not any(event[0] in {"safe_stop", "full_preflight", "start"} for event in events)
    assert main.healthy and not fast.healthy


def test_cold_start_remains_strict_for_high_swap_and_valid_resources():
    denied, _, fast, _, denied_events = runtime(
        main=False, snapshots=(snapshot(9.4),),
    )
    with pytest.raises(ResourcePreflightDenied) as error:
        denied._switch(QWEN36, fast)
    assert error.value.category == "COLD_START_RESOURCE_PREFLIGHT_DENIED"
    assert not any(event[0] == "start" for event in denied_events)

    allowed, _, fast, _, allowed_events = runtime(
        main=False, snapshots=(snapshot(4.8),),
    )
    allowed._switch(QWEN36, fast)
    assert ("start", QWEN36.profile_id) in allowed_events and fast.healthy


def test_partial_target_cleanup_precedes_resource_gated_previous_restore():
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(snapshot(4.8), snapshot(4.7), snapshot(4.6)),
        partial_start=QWEN36.profile_id,
    )
    with pytest.raises(RuntimeUnavailable, match="partial start"):
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    target_cleanup = event_index(events, "absence_proven", QWEN36.profile_id)
    restore_preflight = [
        index for index, event in enumerate(events)
        if event[0] == "full_preflight" and event[1] == 4.6
    ][0]
    restore_start = event_index(events, "start", QWEN38.profile_id)
    assert target_cleanup < restore_preflight < restore_start
    assert main.healthy and not fast.healthy and lifecycle.maximum_resident == 1


def test_restore_preflight_failure_leaves_zero_heavy_and_fails_closed():
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(snapshot(4.8), snapshot(4.7), snapshot(9.4)),
        attempts=1,
        partial_start=QWEN36.profile_id,
    )
    with pytest.raises(HeavyModelConflict, match="PREVIOUS_RUNTIME_RESTORE_PREFLIGHT_DENIED"):
        factory._switch(QWEN36, fast, current=QWEN38, current_provider=main)
    assert not main.healthy and not fast.healthy
    assert ("start", QWEN38.profile_id) not in events
    assert lifecycle.maximum_resident == 1


def test_failover_uses_owned_transition_sequence_even_when_main_endpoint_is_down():
    factory, main, fast, lifecycle, events = runtime(
        main=False,
        owned={QWEN38.profile_id},
        snapshots=(snapshot(9.4), snapshot(4.8)),
    )
    with factory.failover_session() as provider:
        assert provider is fast
    assert event_index(events, "ownership_admission") < event_index(events, "safe_stop")
    assert event_index(events, "absence_proven") < event_index(events, "full_preflight")
    assert lifecycle.maximum_resident == 1


def test_failover_from_proven_dead_main_uses_strict_cold_start_gate():
    denied, _, fast, _, events = runtime(
        main=False, owned=set(), snapshots=(snapshot(9.4),),
    )
    with pytest.raises(ResourcePreflightDenied) as error:
        with denied.failover_session():
            pass
    assert error.value.category == "COLD_START_RESOURCE_PREFLIGHT_DENIED"
    assert not any(event[0] == "start" for event in events)

    allowed, _, fast, lifecycle, events = runtime(
        main=False, owned=set(), snapshots=(snapshot(4.8),),
    )
    with allowed.failover_session() as provider:
        assert provider is fast
    assert fast.healthy and lifecycle.maximum_resident == 1
    assert events[0][0] == "source_state"


def test_real_machine_sequence_main_fast_main_has_fresh_gate_before_every_start():
    factory, main, fast, lifecycle, events = runtime(
        snapshots=(
            snapshot(9.414),
            snapshot(9.1), snapshot(7.0), snapshot(4.79),
            snapshot(4.8), snapshot(4.7),
        ),
        attempts=3,
    )
    with factory.session("FAST") as provider:
        assert provider.generate("ping") == "fast-ok"
        assert fast.healthy and not main.healthy
    assert main.healthy and not fast.healthy
    starts = [index for index, event in enumerate(events) if event[0] == "start"]
    full_gates = [index for index, event in enumerate(events) if event[0] == "full_preflight"]
    assert len(starts) == 2
    assert all(any(gate < start for gate in full_gates) for start in starts)
    for start in starts:
        previous_start = max((item for item in starts if item < start), default=-1)
        assert any(previous_start < gate < start for gate in full_gates)
    assert lifecycle.maximum_resident == 1


def test_no_direct_pid_signal_or_threshold_weakening_was_introduced():
    source = Path(
        "/Users/jerson/AI/control-plane/src/local_ai_control/services/runtime_providers.py"
    ).read_text()
    assert "os.kill" not in source and "pkill" not in source and "killall" not in source
    assert MemoryPreflight().max_swap_used_gib == 6
