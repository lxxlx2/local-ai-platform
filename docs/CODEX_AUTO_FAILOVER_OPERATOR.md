# Codex Automatic Failover Operator Gate

Status: FEATURE-BRANCH QUALIFIED / NOT PRODUCTION-ACTIVATED

## Scope

This gate covers one existing durable Supervisor coding job. Provider changes must retain
the same job, approved feature worktree, branch, objective binding, candidate identity,
validation evidence and review state.

It does not authorize merge, deploy, service control, package installation, credential
access, arbitrary network access, or an automatic approval. Local Qwen is a producer only;
review, security, Git and deployment gates remain host-controlled.

## Eligible cloud failure evidence

- explicit exhaustion from supported quota telemetry;
- a recognized quota/rate-limit error from the active cloud request;
- provider unavailability from an active request or bounded provider probe.

`UNKNOWN`, generic exceptions, ambiguous text and account-wide usage deltas do not trigger
failover. A rate-limit classification without active-request attribution is denied.

## Required local preflight

Before changing the provider to Local Qwen, all of the following must match:

- Qwen3.8 health on the qualified localhost runtime;
- bridge health, Qwen3.8 backend identity and `exec_command` route;
- the exact durable job worktree and feature branch;
- the immutable job/objective binding;
- the bounded local producer security profile.

Failure leaves the same job and candidate durably blocked for reconciliation.

## Durable audit

Supervisor SQLite stores the current provider state and append-only transition history.
History contains only structural references and hashes; it does not store credentials or
raw prompts. Each signal has an idempotency key so replay cannot duplicate a handoff.

The local adapter writes private runtime-only status beneath:

`/Users/jerson/AI/runtime/codex-failover/`

It creates an isolated `CODEX_HOME`; it never edits the Owner's normal `~/.codex` config.

## Recovery

Cloud availability never interrupts a mutating Local-Qwen step. At a confirmed safe boundary,
the same durable job can transition back to OpenAI Codex for review/planning. Completed local
changes and candidate evidence remain in place.

## Codex Desktop limitation

Codex CLI 0.148.0 exposes isolated configuration, local custom providers, workspace selection,
resume and `codex app <workspace>`. Current evidence does not establish a safe API for changing
the provider inside the exact already-running Desktop chat thread. The adapter therefore records
`same_thread_hot_swap_supported=false` and prepares a best-effort new local Desktop session plus
the qualified CLI path. Operators must not describe this as exact same-thread hot swap.

## Activation gate

Before production activation:

1. obtain independent review of the feature branch;
2. verify the reviewed commit and clean fast-forward eligibility;
3. merge only with explicit Owner authorization;
4. perform a separate deployment precheck;
5. activate only with explicit authorization and preserve current runtime processes;
6. run post-activation same-job failover observation without using real quota exhaustion as a destructive test.

Until those gates pass, production behavior is unchanged.
