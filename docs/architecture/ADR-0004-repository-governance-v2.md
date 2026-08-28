# ADR-0004: Repository Governance V2

Status: ACCEPTED / IMPLEMENTED / CONTINUOUSLY MAINTAINED

## Context

Project decisions, qualification evidence and implementation state had accumulated across many feature/docs branches and Issue bodies. Several historical records were accurate for their original moment but stale as a description of the current platform. Creating one docs branch per architecture change would increase branch sprawl and make source-of-truth recovery harder.

## Decision

Adopt a small canonical navigation layer:

- `README.md` is the repository entrypoint.
- `docs/CURRENT_STATUS.md` is the cross-branch current-state snapshot.
- `docs/architecture/INDEX.md` is the architecture decision index.
- `docs/architecture/ADR-*.md` stores approved architecture decisions.
- Issues track implementation/checklists/blockers.
- feature branches carry code implementation.
- qualification documents carry evidence.
- a single long-lived `docs/architecture-ledger` branch is used only when a relevant implementation branch is frozen and architecture/status still must be recorded durably.

Do not create a new docs branch for every decision.

## State precedence

1. live runtime evidence for process/resource facts;
2. merged main for stable code;
3. `CURRENT_STATUS.md` for cross-branch project state;
4. accepted ADRs;
5. active Issue + exact branch/commit;
6. qualification evidence;
7. historical docs/Issue bodies;
8. chat memory.

## Branch lifecycle

Use `ACTIVE`, `FROZEN_REVIEW`, `REFERENCE`, `MERGED`, `RETIRED` in the current-status branch table. Old branches are deleted only after unique decisions/evidence are preserved elsewhere.

## Download-state rule

A model is complete only when the completion marker and snapshot validation pass. Partial-cache bytes are resumable work and must be reported separately from completed payload; duplicate `.incomplete` fragments must not inflate a canonical percent-complete number.

## Implementation evidence

Repository Governance V2 was independently reviewed by Gemini on 2026-08-29 with verdict PASS and zero findings, then merged to `main` through PR #20 at merge commit `fcbb71d6f10f850ccfbb8dbb28015eae4ceed105`.

Post-merge cleanup then:

- closed superseded/completed Issues #1-#6;
- reduced active Issues to #13-#19 as applicable;
- retired ten obsolete remote branches after containment review;
- preserved three unique historical branches as verified annotated archive tags before deleting their remote branches;
- migrated unique long-term architecture/qualification evidence from the legacy `docs/context-architecture-v2` aggregate branch into the single `docs/architecture-ledger`.

Governance remains a continuously maintained process rather than a one-time migration.

## Consequences

- Future agents should recover project state by reading a few canonical files before historical branches/issues.
- Architecture decisions remain durable without branch proliferation.
- `main` is the stable baseline, while merge, deployment and production activation remain separate gates.
- On the current private-repository plan, GitHub reports `main` as unprotected and ruleset/branch-protection APIs are not currently available through the connected account/integration; procedural review/merge gates remain mandatory until technical protection can be enabled and verified.
