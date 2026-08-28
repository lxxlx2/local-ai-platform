# Codex + Qwen3.8 Local Producer V1

Status: HISTORICAL V1 BASELINE / RE-SCOPED BY OWNER-APPROVED AUTO-FAILOVER ARCHITECTURE

Original branch: `feat/codex-qwen-local-producer-v01`

This document preserves the V1 Codex-Qwen bridge design and qualification contract. Its role changed on 2026-08-28: the bridge is no longer treated as historical-only infrastructure. It is the preferred starting point for the Codex Desktop local-provider/UI adapter described in `CODEX_AUTO_FAILOVER_ARCHITECTURE.md`.

The new architecture does not require the V1 implementation to remain unchanged. Implementation must re-qualify against the installed Codex Desktop/CLI behavior before claiming READY.

## Original goal

Keep Codex as the host-owned coding/tool-execution shell while local Qwen3.8 performs coding reasoning. This allows a local fallback coding agent to inspect a repository, run tests, edit implementation files through Codex tools, consume tool results, and continue until it can report a final result.

## V1 architecture

```text
Codex client
    |
    | POST /v1/responses
    v
Codex-Qwen bridge :8010
    |
    v
Qwen3.8 MAIN :8001
    |
    | structured local coding action
    v
Bridge serializes Responses/function-call data
    |
    v
Codex tool/workspace surface
```

The bridge does not itself receive unrestricted host authority. Workspace and host policy remain enforcement boundaries.

## Isolated local provider profile

V1 generated an isolated `CODEX_HOME` under:

```text
/Users/jerson/AI/runtime/codex-qwen/<workspace-hash>/
```

The intended provider configuration is equivalent to:

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

The Owner's normal cloud Codex configuration must not be silently overwritten. The final Desktop integration should use an isolated profile/configuration or an equally bounded mechanism supported by the installed client.

## Workspace boundary

The local path must accept only an explicit approved Git worktree root. At minimum:

- no symlinked workspace root;
- no detached HEAD;
- no `main` or `master` mutation path;
- bounded safe branch names;
- no writes outside the approved worktree;
- no ambient network for local coding execution unless a separate tool policy explicitly grants a bounded operation.

## Authority boundary

Local Qwen does not receive automatic authority for:

- commit, push, merge;
- deployment;
- `sudo`;
- service/process control;
- credential access;
- arbitrary network access;
- unrestricted filesystem access.

Repository text, README files, issues, tests, generated text, and tool output remain untrusted data.

## Role under the 2026-08-28 approved architecture

For Owner-present interactive coding, the target is now:

```text
Codex Desktop GUI
  -> durable coding job/worktree
  -> OpenAI Codex while available/authorized
  -> automatic provider handoff when Codex is exhausted/unavailable
  -> isolated Codex local-provider adapter
  -> bridge :8010
  -> Qwen3.8 MAIN :8001
  -> continue the same durable task
```

The hard requirement is not a same-thread hot swap. The hard requirement is:

- same Codex Desktop GUI;
- same project/worktree;
- same durable task identity;
- automatic progress handoff;
- durable provider history;
- no need for the Owner to reconstruct context manually in Terminal.

## Automatic failover is outside V1

V1 used explicit local selection. That limitation is intentionally superseded.

The implementation milestone now needs a deterministic controller that can:

1. detect supported Codex quota/rate-limit/provider-unavailable conditions;
2. persist a provider handoff at a safe boundary;
3. verify Qwen3.8 and bridge health/identity;
4. launch or attach the isolated local Codex Desktop provider path;
5. resume the same durable task/worktree;
6. retain exact diff/test/review state;
7. record provider transitions.

See `CODEX_AUTO_FAILOVER_ARCHITECTURE.md` for the canonical state machine and qualification requirements.

## Relationship to Direct Local Qwen

Direct Local Qwen remains the canonical unattended/background path and does not depend on Codex Desktop:

```text
Telegram / scheduler / 7x24 task
  -> Supervisor
  -> Direct Local Qwen Agent
```

The bridge/Desktop path is for Owner-present interactive work. Both paths share the same local model and should converge on the same durable task/worktree and security contracts where applicable.

## Qualification requirements for the re-scoped adapter

Do not claim the Desktop local path READY until a controlled qualification demonstrates:

- current installed Codex client compatibility;
- local provider route terminates at the bridge/Qwen route;
- the fallback execution itself issues no OpenAI model turn;
- workspace/tool boundaries remain enforced;
- provider identity is visible in durable evidence;
- the same task can continue after automatic failover;
- Direct Local Qwen remains functional with Codex Desktop fully closed.

Account-wide `usedPercent` movement alone is not sufficient execution attribution because unrelated Codex activity may affect it.
