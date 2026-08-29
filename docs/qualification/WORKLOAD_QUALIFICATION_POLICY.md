# Local Model Workload Qualification Policy

Status: OWNER-APPROVED

This policy defines how local-model qualification must represent the machine as it is actually used for work. The platform exists to support normal desktop work; qualification must not manufacture an empty-machine benchmark that requires the user to stop doing normal work.

## 1. Core principle

A production qualification result is valid only when the tested host workload is representative of the intended production workload.

The qualification harness MUST NOT improve a result by closing, pausing, suspending, or killing user applications such as browsers, Unity, IDEs, ChatGPT/Codex, terminals, communication tools, media apps, or other normal desktop processes.

The harness may terminate only processes that it spawned and whose exact identity/ownership has been verified according to the repository heavy-process identity policy.

If a model can run only after normal user applications are closed, that result is diagnostic evidence and MUST NOT be used to promote the model/context tier as the normal production default.

## 2. Workload classes

Every live local-model qualification must declare one workload class.

### LAB

Purpose: isolate model/runtime behavior and establish an optimistic upper bound.

Examples:
- reduced desktop workload;
- intentionally closed normal applications;
- dedicated benchmark conditions.

LAB evidence may prove functional correctness, tokenizer/context behavior, throughput, or an upper resource bound. It cannot by itself justify production promotion.

### REPRESENTATIVE_WORKLOAD

Purpose: qualify the workload the Owner reasonably expects during normal use.

The environment should preserve the applications that are ordinarily open at the time of use. For the current target Mac this commonly includes a browser with real tabs/session state, Terminal, and whichever of ChatGPT/Codex/IDE are actually in use. The exact application set is observational, not a fixed synthetic list.

REPRESENTATIVE_WORKLOAD is the mandatory promotion gate for a normal production default.

### STRESS_COEXISTENCE

Purpose: test plausible heavier concurrent work without constructing an unrealistic worst case.

Examples include REPRESENTATIVE_WORKLOAD plus Unity Editor with a real project loaded, an IDE, or another application the Owner commonly uses concurrently.

A failure here does not automatically invalidate all use of the model. It defines a concurrency/routing boundary that the runtime must respect.

## 3. Workload manifest

Before every live qualification the evidence must capture the host workload rather than silently changing it.

At minimum record:
- workload class;
- timestamp and host identity;
- top RSS processes or equivalent process snapshot;
- memory pressure and reclaimable memory;
- absolute swap and qualification-relative swap baseline;
- relevant fixed ports and existing heavy-model processes;
- whether browser, Unity, IDE and other material user applications were running;
- any deliberate workload change made for the test.

If a material application is intentionally closed, the result must be labeled LAB unless the intended production deployment is explicitly a dedicated/isolated host.

## 4. Qualification dimensions

A local model/context tier is evaluated across separate dimensions:

1. Functional correctness: prompt/token accounting, recall/coding/vision behavior as applicable, completion and output reservation.
2. Cold-load admission: can the model start safely while the declared workload is already present?
3. Sustained coexistence: after loading, can it remain healthy while the declared workload continues?
4. Host safety: no CRITICAL memory-pressure event, uncontrolled swap growth, unexpected process/port mutation, or user-application termination.
5. Cleanup/recovery: exact owned model processes terminate cleanly and host resources recover.
6. User utility: qualification must not depend on the user abandoning the normal applications the platform is meant to coexist with.

Functional success and resource qualification must be reported separately.

## 5. Promotion rules

A production default may be promoted only when:
- required static tests pass;
- required independent review passes;
- REPRESENTATIVE_WORKLOAD cold-load admission passes;
- required representative functional stages pass within resource gates;
- cleanup/recovery passes;
- production registry/runtime changes, if any, pass their own Owner gate.

LAB PASS + REPRESENTATIVE_WORKLOAD FAIL is classified as `LAB_ONLY`, not production-qualified.

A REPRESENTATIVE_WORKLOAD resource failure is valid architecture evidence. Do not repeatedly empty the machine or weaken safety gates to turn that failure into a PASS.

## 6. Runtime consequence of coexistence failures

The user should not be asked to close normal work applications so that the AI runtime can start.

If a large local model fails representative cold-load or coexistence admission, the runtime architecture must handle that condition by policy, for example:
- keep the request queued until resources naturally recover;
- route to a smaller qualified local model;
- use an already-approved cloud/provider fallback when privacy/routing policy allows;
- decline the heavy local route with a clear resource-state reason.

Automatic termination or suspension of user applications is forbidden.

This means local-model selection is workload-aware. A model may be qualified for one workload class and blocked for another.

## 7. Current Qwen3.8 Phase 2 interpretation

The 2026-08-29 Qwen3.8 27B 8-bit evidence must be interpreted in two distinct environments.

Representative normal-workload evidence with the browser running:
- production memory preflight passed;
- the corrected Qwen virtualenv worker began model loading;
- load-time relative swap grew from about 4518 MiB to about 7985 MiB, approximately +3466 MiB;
- the 2048 MiB safety gate correctly aborted before any context stage;
- cleanup succeeded and no fixed-port/heavy-process residue remained.

This is a representative cold-load resource failure and must not be erased by a later LAB run.

LAB evidence after the browser was intentionally closed:
- reclaimable memory increased to about 41.7 GiB and swap dropped to about 1.84 GiB before load;
- 4095-token recall PASS, min free 25%;
- 12000-token recall PASS_WITH_WARNING, min free 18%, about 20 seconds below NORMAL;
- 13248-token recall PASS_WITH_WARNING, min free 17%, about 23 seconds below NORMAL;
- no stage-relative swap growth was observed;
- cleanup succeeded.

This proves the context/runtime path can function in a reduced-load environment. It does not qualify the 24K tier, and it does not prove the large model can cold-start safely during normal desktop work.

The existing 16,384 TOTAL production context envelope remains unchanged unless a separate representative-workload promotion process says otherwise. 24,576 TOTAL default remains NOT QUALIFIED on the current target. 32K remains NOT TESTED / NOT JUSTIFIED.

## 8. Test-sequence guidance

For future local-model qualification:

1. Capture the current workload manifest without changing it.
2. Run static and independent-review gates.
3. Run REPRESENTATIVE_WORKLOAD cold-load admission first.
4. If representative cold-load fails a resource gate, stop escalation and record the boundary.
5. LAB testing may follow only when useful for diagnosis; label it LAB explicitly.
6. Run STRESS_COEXISTENCE only for plausible workloads that matter to actual use, such as Unity coexistence.
7. Never close normal applications as an automated prerequisite to qualification.
8. Convert representative failures into routing/admission requirements rather than benchmark manipulation.

## 9. Evidence language

Qualification reports should use these terms consistently:
- `FUNCTIONAL_PASS`: behavior completed correctly.
- `RESOURCE_PASS`: resource gates passed for the declared workload class.
- `PASS_WITH_WARNING`: functional/resource hard gates passed but warning pressure was observed.
- `LAB_ONLY`: result obtained under intentionally reduced workload and not valid for normal-production promotion.
- `REPRESENTATIVE_BLOCKED`: intended normal workload could not safely admit or sustain the model.
- `STRESS_COEXISTENCE_BLOCKED`: plausible heavier coexistence workload exceeded the qualified boundary.

A report must never collapse these categories into a single generic PASS.
