# ADR-0005: Workload-aware local-model qualification and admission

Status: ACCEPTED

Owner approval: 2026-08-29

## Context

The local AI platform runs on the same Mac that the Owner uses for ordinary work. The platform therefore competes for unified memory, swap, CPU/GPU resources, and responsiveness with the browser, Unity, IDEs, ChatGPT/Codex, terminals, and other desktop applications.

A qualification process that closes those applications can produce a technically cleaner benchmark while failing the product goal: the model must support work rather than require work to stop.

During Qwen3.8 Context V2 Phase 2, this distinction became material. Under a normal browser-running workload, the corrected worker passed preflight but model loading increased relative swap by roughly 3.47 GiB and was correctly aborted by the 2 GiB safety gate. Under an intentionally reduced workload after the browser was closed, the same runtime completed 4K/12K/13.25K recall stages, with 12K and 13.25K in WARNING memory pressure.

The reduced-load run is useful diagnostic evidence but cannot supersede the representative-workload failure.

## Decision

Local-model qualification and runtime admission are workload-aware.

1. Production qualification must use a representative desktop workload for the intended use case.
2. Qualification automation must never close, suspend, or kill user applications to create benchmark headroom.
3. The harness may manage only exact-owned model/qualification processes.
4. Reduced-load runs are explicitly `LAB` evidence and cannot by themselves justify a production-default promotion.
5. A model/context tier that fails representative cold-load or coexistence admission is not made production-capable by weakening resource gates or asking the user to close normal work applications.
6. Representative resource failures become routing/admission constraints: unload or downgrade the AI runtime's own heavy model, route to a smaller qualified local model, queue until resources recover, use an approved provider fallback when policy allows, or decline the heavy route with an explicit reason.
7. Plausible heavier use such as Unity coexistence is tested separately as `STRESS_COEXISTENCE`; failure defines a concurrency boundary rather than automatically invalidating lighter qualified workloads.
8. Qualification evidence must preserve workload class and workload manifest so that LAB, representative, and stress results cannot be confused.
9. Resource contention is resolved in favor of the Owner's desktop workload. The AI service yields first.
10. `ON_DEMAND_COLD_START` and `PRELOADED_DAEMON` are separate qualification modes. A preloaded-daemon pass does not retroactively convert an on-demand cold-start failure into a pass.

Detailed methodology and evidence language live in `docs/qualification/WORKLOAD_QUALIFICATION_POLICY.md`.

## Consequences

### Positive

- Production claims correspond to how the workstation is actually used.
- Benchmarks cannot improve by silently disrupting the Owner's work.
- Large-model resource failures become actionable router/admission requirements.
- A smaller local model can be qualified as the everyday coexistence fallback while a larger model remains available when resources permit.
- Unity and other heavy applications can be modeled as explicit concurrency states rather than accidental test noise.
- A 7x24 preloaded service can be evaluated honestly as its own deployment mode, including whether it yields safely when desktop load grows.

### Cost

- Some models that pass on an idle Mac will remain blocked from normal-production default use.
- Qualification requires capturing a workload manifest and may require multiple workload classes and deployment modes.
- Runtime routing must eventually incorporate host resource/workload admission instead of assuming one local model is always startable.
- A heavy model may need to self-unload or downgrade when the Owner starts Unity or other memory-intensive work.

## Current application to Qwen3.8

For the current 48 GiB target Mac:

- the browser-running cold-load result is authoritative representative-workload evidence and is resource-blocked at model load;
- the browser-closed 4K/12K/13.25K run is LAB evidence only;
- 12K and ~13.25K were functionally successful in LAB but entered WARNING memory pressure;
- the existing 16,384 TOTAL production context envelope is not changed by this ADR;
- 24,576 TOTAL default remains NOT QUALIFIED;
- 32K remains NOT TESTED / NOT JUSTIFIED;
- future platform work should qualify a smaller local fallback for normal multitasking and make heavy-model admission workload-aware;
- the current evidence does not yet qualify a `PRELOADED_DAEMON` coexistence mode. If 7x24 preloading remains an intended deployment mode, it needs a separate representative test where browser/Unity workload is allowed to arrive while the service is running, and the heavy model must yield safely if limits are crossed.

## Non-decisions

This ADR does not select the smaller fallback model, modify current production model-registry values, restart production services, change the 2 GiB swap-growth safety gate, authorize deployment, or authorize any automation to close user applications.

Those actions require their own implementation, qualification, review, and Owner gates.
