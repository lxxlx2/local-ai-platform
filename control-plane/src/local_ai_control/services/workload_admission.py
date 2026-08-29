"""Read-only workload observation and local-model admission primitives.

The workstation exists to support user work.  This module therefore observes
normal desktop workload without exposing any API that closes, suspends, kills,
or otherwise controls user applications.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
import subprocess
from typing import Callable, Iterable

from local_ai_control.services.heavy_process_identity import listener_pids
from local_ai_control.services.models import MemoryPreflight, MemorySnapshot, ModelProfile


class WorkloadClass(StrEnum):
    LAB = "LAB"
    REPRESENTATIVE_WORKLOAD = "REPRESENTATIVE_WORKLOAD"
    STRESS_COEXISTENCE = "STRESS_COEXISTENCE"


@dataclass(frozen=True)
class ProcessSample:
    pid: int
    rss_mib: float
    # Historical field name retained for compatibility. The production reader
    # now supplies executable identity from ps(1) `comm`, never the full argv.
    command: str


@dataclass(frozen=True)
class MaterialApplication:
    category: str
    process_count: int
    rss_mib: float


@dataclass(frozen=True)
class WorkloadManifest:
    workload_class: WorkloadClass
    timestamp: str
    memory: MemorySnapshot
    top_processes: tuple[ProcessSample, ...]
    material_applications: tuple[MaterialApplication, ...]
    fixed_port_listeners: tuple[tuple[int, tuple[int, ...]], ...]
    deliberate_reductions: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["workload_class"] = self.workload_class.value
        return payload


@dataclass(frozen=True)
class WorkloadAdmissionResult:
    profile_id: str
    workload_class: WorkloadClass
    allowed: bool
    qualification_evidence_eligible: bool
    reason: str
    preflight_reason: str
    snapshot: MemorySnapshot

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["workload_class"] = self.workload_class.value
        return payload


_MATERIAL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BROWSER", (
        "/Google Chrome.app/", "/Safari.app/", "/Firefox.app/",
        "/Arc.app/", "/Microsoft Edge.app/",
    )),
    # UNITY means the actual Editor workload. Unity Hub alone is a launcher and
    # must not trigger the stress-coexistence category used by downstream policy.
    ("UNITY", ("/Unity.app/",)),
    ("IDE", (
        "/Visual Studio Code.app/", "/Cursor.app/", "/Xcode.app/",
        "/IntelliJ IDEA.app/", "/PyCharm.app/",
    )),
    ("CHATGPT", ("/ChatGPT.app/",)),
    ("TERMINAL", ("/Terminal.app/", "/iTerm.app/", "/Warp.app/")),
)


def _default_process_reader() -> str:
    # `command` includes argv and can be spoofed by an unrelated process whose
    # arguments merely mention an application path. `comm` reports executable
    # identity and matches the hardened Phase C workload-observation contract.
    return subprocess.check_output(
        ["ps", "ax", "-o", "pid=,rss=,comm="],
        text=True,
    )


def _parse_processes(raw: str) -> tuple[ProcessSample, ...]:
    samples: list[ProcessSample] = []
    for line in raw.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            rss_kib = int(parts[1])
        except ValueError:
            continue
        if pid <= 0 or rss_kib < 0:
            continue
        samples.append(ProcessSample(pid, rss_kib / 1024.0, parts[2]))
    samples.sort(key=lambda item: item.rss_mib, reverse=True)
    return tuple(samples)


def _material_applications(processes: Iterable[ProcessSample]) -> tuple[MaterialApplication, ...]:
    grouped: dict[str, list[ProcessSample]] = {name: [] for name, _ in _MATERIAL_PATTERNS}
    for process in processes:
        for category, patterns in _MATERIAL_PATTERNS:
            if any(pattern in process.command for pattern in patterns):
                grouped[category].append(process)
                break
    result = [
        MaterialApplication(
            category=category,
            process_count=len(items),
            rss_mib=round(sum(item.rss_mib for item in items), 3),
        )
        for category, items in grouped.items()
        if items
    ]
    return tuple(result)


class WorkloadManifestProbe:
    """Capture host evidence using read-only process and resource probes only."""

    def __init__(
        self,
        *,
        process_reader: Callable[[], str] | None = None,
        memory_probe: Callable[[], MemorySnapshot] | None = None,
        listeners: Callable[[int], tuple[int, ...]] = listener_pids,
        ports: tuple[int, ...] = (8000, 8001, 8011),
        top_n: int = 30,
    ):
        self.process_reader = process_reader or _default_process_reader
        self.memory_probe = memory_probe or MemoryPreflight().probe
        self.listeners = listeners
        self.ports = tuple(int(port) for port in ports)
        self.top_n = max(1, int(top_n))

    def capture(
        self,
        workload_class: WorkloadClass | str,
        *,
        deliberate_reductions: Iterable[str] = (),
    ) -> WorkloadManifest:
        declared = WorkloadClass(workload_class)
        reductions = tuple(str(item).strip() for item in deliberate_reductions if str(item).strip())
        if reductions and declared is not WorkloadClass.LAB:
            raise ValueError("deliberately reduced workload must be classified as LAB")

        all_processes = _parse_processes(self.process_reader())
        memory = self.memory_probe()
        fixed_ports = tuple((port, tuple(self.listeners(port))) for port in self.ports)
        return WorkloadManifest(
            workload_class=declared,
            timestamp=datetime.now(UTC).isoformat(),
            memory=memory,
            top_processes=all_processes[: self.top_n],
            material_applications=_material_applications(all_processes),
            fixed_port_listeners=fixed_ports,
            deliberate_reductions=reductions,
        )


class WorkloadAdmissionPolicy:
    """Evaluate one model against the current host without controlling user apps."""

    def __init__(self, preflight: MemoryPreflight | None = None):
        self.preflight = preflight or MemoryPreflight()

    def admit(self, profile: ModelProfile, manifest: WorkloadManifest) -> WorkloadAdmissionResult:
        check = self.preflight.check(profile.expected_memory_gib or 0)
        if not check.allowed:
            return WorkloadAdmissionResult(
                profile_id=profile.profile_id,
                workload_class=manifest.workload_class,
                allowed=False,
                qualification_evidence_eligible=False,
                reason="RESOURCE_PREFLIGHT_DENIED",
                preflight_reason=check.reason,
                snapshot=check.snapshot,
            )

        lab_only = manifest.workload_class is WorkloadClass.LAB
        return WorkloadAdmissionResult(
            profile_id=profile.profile_id,
            workload_class=manifest.workload_class,
            allowed=True,
            qualification_evidence_eligible=not lab_only,
            reason="LAB_ONLY" if lab_only else "RESOURCE_ADMISSION_PASS",
            preflight_reason=check.reason,
            snapshot=check.snapshot,
        )
