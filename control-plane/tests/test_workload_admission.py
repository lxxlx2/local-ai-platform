from local_ai_control.services.models import (
    MemoryPreflightResult,
    MemorySnapshot,
    QWEN36,
    QWEN38,
)
from local_ai_control.services.workload_admission import (
    WorkloadAdmissionPolicy,
    WorkloadClass,
    WorkloadManifestProbe,
)


def _snapshot(*, reclaimable=32.0, pressure="NORMAL", swap=2.0):
    return MemorySnapshot(
        total_gib=48.0,
        available_gib=reclaimable,
        swap_used_gib=swap,
        pressure=pressure,
        reclaimable_gib=reclaimable,
    )


class FakePreflight:
    def __init__(self, *, allowed, reason="OK", snapshot=None):
        self.allowed = allowed
        self.reason = reason
        self.snapshot = snapshot or _snapshot()
        self.required = []

    def check(self, required_gib):
        self.required.append(required_gib)
        return MemoryPreflightResult(
            allowed=self.allowed,
            required_gib=required_gib,
            available_gib=self.snapshot.available_gib,
            reason=self.reason,
            snapshot=self.snapshot,
        )


def test_manifest_captures_material_apps_and_fixed_ports_read_only():
    calls = []

    def process_reader():
        calls.append("process-read")
        return "\n".join([
            "101 1048576 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "102 524288 /Applications/Unity/Hub/Editor/6000/Unity.app/Contents/MacOS/Unity",
            "103 262144 /Applications/Visual Studio Code.app/Contents/MacOS/Electron",
            "104 131072 /Applications/ChatGPT.app/Contents/MacOS/ChatGPT",
        ])

    def listeners(port):
        calls.append(("listeners", port))
        return (9000 + port,) if port == 8001 else ()

    probe = WorkloadManifestProbe(
        process_reader=process_reader,
        memory_probe=lambda: _snapshot(reclaimable=31.5),
        listeners=listeners,
        ports=(8000, 8001, 8011),
    )

    manifest = probe.capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)

    assert manifest.workload_class is WorkloadClass.REPRESENTATIVE_WORKLOAD
    assert manifest.memory.reclaimable_gib == 31.5
    assert manifest.fixed_port_listeners == (
        (8000, ()),
        (8001, (17001,)),
        (8011, ()),
    )
    apps = {item.category: item for item in manifest.material_applications}
    assert set(apps) == {"BROWSER", "UNITY", "IDE", "CHATGPT"}
    assert apps["BROWSER"].process_count == 1
    assert apps["BROWSER"].rss_mib == 1024.0
    assert calls == [
        "process-read",
        ("listeners", 8000),
        ("listeners", 8001),
        ("listeners", 8011),
    ]


def test_deliberate_reduction_cannot_be_labeled_representative():
    probe = WorkloadManifestProbe(
        process_reader=lambda: "",
        memory_probe=_snapshot,
        listeners=lambda _port: (),
    )

    try:
        probe.capture(
            WorkloadClass.REPRESENTATIVE_WORKLOAD,
            deliberate_reductions=("closed browser",),
        )
    except ValueError as error:
        assert "classified as LAB" in str(error)
    else:
        raise AssertionError("reduced workload was mislabeled representative")


def test_lab_manifest_is_never_promotion_eligible_even_when_resources_pass():
    probe = WorkloadManifestProbe(
        process_reader=lambda: "",
        memory_probe=_snapshot,
        listeners=lambda _port: (),
    )
    manifest = probe.capture(
        WorkloadClass.LAB,
        deliberate_reductions=("closed browser",),
    )
    preflight = FakePreflight(allowed=True)

    result = WorkloadAdmissionPolicy(preflight).admit(QWEN38, manifest)

    assert result.allowed is True
    assert result.promotion_eligible is False
    assert result.reason == "LAB_ONLY"
    assert preflight.required == [34]


def test_representative_resource_failure_routes_as_admission_block_not_runtime_error():
    probe = WorkloadManifestProbe(
        process_reader=lambda: "101 1024 /Applications/Google Chrome.app/x",
        memory_probe=_snapshot,
        listeners=lambda _port: (),
    )
    manifest = probe.capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)
    preflight = FakePreflight(
        allowed=False,
        reason="INSUFFICIENT_RECLAIMABLE_MEMORY",
        snapshot=_snapshot(reclaimable=20.0),
    )

    result = WorkloadAdmissionPolicy(preflight).admit(QWEN38, manifest)

    assert result.allowed is False
    assert result.promotion_eligible is False
    assert result.reason == "RESOURCE_PREFLIGHT_DENIED"
    assert result.preflight_reason == "INSUFFICIENT_RECLAIMABLE_MEMORY"


def test_representative_qwen36_resource_pass_can_be_qualification_evidence():
    probe = WorkloadManifestProbe(
        process_reader=lambda: "101 1048576 /Applications/Google Chrome.app/x",
        memory_probe=_snapshot,
        listeners=lambda _port: (),
    )
    manifest = probe.capture(WorkloadClass.REPRESENTATIVE_WORKLOAD)
    preflight = FakePreflight(allowed=True)

    result = WorkloadAdmissionPolicy(preflight).admit(QWEN36, manifest)

    assert result.allowed is True
    assert result.promotion_eligible is True
    assert result.reason == "RESOURCE_ADMISSION_PASS"
    assert preflight.required == [28]


def test_manifest_serialization_keeps_workload_class_explicit():
    probe = WorkloadManifestProbe(
        process_reader=lambda: "",
        memory_probe=_snapshot,
        listeners=lambda _port: (),
    )

    payload = probe.capture(WorkloadClass.STRESS_COEXISTENCE).to_dict()

    assert payload["workload_class"] == "STRESS_COEXISTENCE"
    assert payload["memory"]["pressure"] == "NORMAL"
