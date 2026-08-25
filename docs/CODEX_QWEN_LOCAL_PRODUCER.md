# Codex + Qwen3.8 Local Producer V1

Status: FEATURE BRANCH / QUALIFICATION PENDING

Branch: `feat/codex-qwen-local-producer-v01`

Target Codex CLI: `0.146.0`, the version used by the successful real-Mac proof of concept.

## Goal

Keep Codex CLI as the host-owned coding and tool-execution shell while local Qwen3.8 performs the coding reasoning. This allows a local fallback coding agent to inspect a repository, run tests, edit implementation files through Codex tools, consume tool results, and continue until it can report a final result.

V1 is deliberately isolated from the production Supervisor. Automatic task routing, merge, deployment, and unattended production activation remain disabled until a separate review.

## Architecture

```text
User / later Supervisor
        |
        v
Codex CLI 0.146.0
        |
        | POST /v1/responses, stream=true
        v
Codex-Qwen bridge :8010
        |
        | compact local coding prompt
        v
Qwen3.8 MAIN :8001
        |
        | <EXEC>command</EXEC> or <FINAL>text</FINAL>
        v
Bridge serializes Responses SSE / function_call
        |
        v
Codex executes exec_command inside workspace-write sandbox
        |
        | function_call_output
        +-------------------------> Qwen3.8 next turn
```

Qwen chooses the next coding action. The bridge does not execute shell commands. Codex owns tool execution and filesystem mutations.

## Request compaction

Codex requests contain a large bootstrap instruction block. The bridge intentionally does not forward that block verbatim to Qwen3.8.

The compact prompt retains:

- the current user objective;
- recent relevant messages;
- recent `function_call` arguments;
- recent `function_call_output` results;
- a bounded local-agent contract.

The compact prompt is capped at 48 KiB before it is sent to the Qwen3.8 sidecar. Qwen3.8 remains limited to the production-qualified 16,384-token context window.

## Model action protocol

Qwen3.8 must emit exactly one action.

Command request:

```text
<EXEC>pytest -q</EXEC>
```

Completion:

```text
<FINAL>Tests pass after fixing the parser.</FINAL>
```

The bridge rejects surrounding prose, Markdown fences, empty actions, nested actions, mixed EXEC/FINAL output, and other malformed envelopes. It never repairs malformed model text into a command.

The model never serializes a Codex function-call object. The bridge constructs the Responses/SSE JSON and assigns the `call_id`.

## Workspace boundary

The local Codex profile is generated into an isolated `CODEX_HOME` under:

```text
/Users/jerson/AI/runtime/codex-qwen/<workspace-hash>/
```

The global Codex configuration is not modified.

A workspace is accepted only when:

- it exists and is a directory;
- it is the exact Git worktree root supplied to the launcher;
- the supplied path is not a symlink;
- the branch is attached and is not `main` or `master`;
- the branch name passes the bounded safe-name check.

The generated Codex profile explicitly sets:

```toml
model = "mlx-community/Qwen3.8-27B-8bit"
model_provider = "qwen_local_bridge"
approval_policy = "never"
sandbox_mode = "workspace-write"

[model_providers.qwen_local_bridge]
base_url = "http://127.0.0.1:8010/v1"
wire_api = "responses"
requires_openai_auth = false

[sandbox_workspace_write]
network_access = false
```

The explicit `model` setting is required for the qualified custom-provider path because recent Codex versions can omit the shell tool when a custom provider has no explicit model.

## Authority boundary

V1 grants Qwen no direct shell, filesystem, Git, service, credential, network, merge, or deployment authority.

The local planner contract also forbids requests for:

- commit, push, or merge;
- deployment;
- `sudo`;
- `launchctl` or production service control;
- process termination;
- credential access;
- network access.

The primary enforcement boundary for filesystem and network access is the Codex workspace sandbox and the explicitly approved worktree. The planner text is an additional policy layer.

## Running the feature branch bridge

The bridge runs in the foreground and does not install a daemon or launchd service.

From the feature worktree:

```bash
zsh control-plane/scripts/run-codex-qwen-bridge.sh
```

Expected health endpoint:

```bash
curl -s http://127.0.0.1:8010/health
```

A V1 bridge reports `status=healthy`, backend `mlx-community/Qwen3.8-27B-8bit`, and tool `exec_command`.

If port 8010 is already owned by the old temporary proof-of-concept bridge, verify the listener and its exact command before stopping that temporary process. Do not use broad process matching.

## Running Codex with local Qwen

Interactive Codex against an approved feature worktree:

```bash
zsh control-plane/scripts/run-codex-qwen-local.sh /absolute/path/to/feature-worktree
```

Non-interactive example:

```bash
zsh control-plane/scripts/run-codex-qwen-local.sh /absolute/path/to/feature-worktree \
  exec --json --ephemeral "Inspect the failing tests, fix the implementation, rerun tests, and report the result."
```

The launcher fails closed if the Codex version differs from the currently qualified `0.146.0`, the V1 bridge is unhealthy, or the workspace policy rejects the supplied root.

## Real-Mac qualification

The qualification script performs:

1. V1 bridge and Qwen3.8 health checks;
2. focused Local Producer tests;
3. full control-plane pytest suite;
4. `git diff --check`;
5. a seeded scratch-repository test that must fail before the agent run;
6. a real Codex -> bridge -> Qwen3.8 -> `exec_command` loop that fixes only the implementation;
7. post-fix tests;
8. protected test-file hash verification;
9. proof that no Git commit was created;
10. Qwen3.8 and Telegram Bot PID invariants;
11. an obvious credential-literal scan over the Local Producer implementation.

Run from the feature worktree while the V1 bridge is already listening on 8010:

```bash
zsh control-plane/scripts/qualify-codex-qwen-local.sh
```

Qualification artifacts are retained under:

```text
/Users/jerson/AI/runtime/codex-qwen/qualification-*/
```

A successful run ends with `QUALIFICATION_PASS` and prints the artifact directory for independent review.

## Cloud Codex and local Qwen roles

Cloud Codex remains the higher-capability coding route when its quota is available and authorized. Local Qwen3.8 provides a private local reasoning path through the same Codex CLI tool shell.

V1 does not implement automatic cloud/local failover. Selection is explicit through the isolated local launcher. A later reviewed router can choose between Cloud Codex and the local Qwen producer without changing repository authority boundaries.

## Current limitations

- Codex CLI `0.146.0` is the only qualified version for this V1 branch.
- Only `exec_command` is supported by the bridge action protocol.
- Interactive shell continuation via `write_stdin` is not implemented.
- Qwen3.8 production context is capped at 16K.
- The bridge is foreground-only and is not deployed as a persistent service.
- Supervisor automatic routing is disabled.
- Real Codex Supervisor activation remains separately gated.
- No automatic commit, push, merge, deploy, or production service control exists.

## Next stage after V1 qualification

After focused tests, full-suite tests, real-Mac qualification, and independent review pass, the next project stage is to add the qualified Local Producer as a Supervisor task-runner option using the existing durable SQLite job/work-unit state.

That stage should let Qwen drive Codex from queued work while the human role moves primarily to reviewing the resulting diff, tests, findings, and final approval. Merge and deployment must remain explicit approval gates.
