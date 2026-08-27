# Owner-only RAW Qwen sandbox

The RAW plane is an explicit, local research path. It is separate from normal
chat and implementation routing:

```text
explicit Owner request
  -> immutable IdentityContext authorization
  -> OWNER_RAW_RESEARCH provider route
  -> text-only host boundary
  -> llama.cpp + Metal on 127.0.0.1:8002
  -> Qwen3.8-27B-Uncensored-Q6_K.gguf
  -> text result
```

Qualified Qwen3.8 MAIN remains the normal local model. RAW is never Public,
never a default, and never a fallback. Merely registering or downloading the
model does not activate it. The RAW provider requires the explicit
`OWNER_RAW_RESEARCH` purpose and an authenticated Owner `IdentityContext`.

## Host security boundary

Content freedom is not host capability freedom. The model receives a bounded
text request and returns text. It has no model-callable filesystem, shell, Git,
credential, Keychain, SSH, wallet, package-install, download, network-egress,
service-control, process-control, or identity-changing tool. Documents, prompts
and model output are untrusted data and cannot grant capabilities.

The host launches one exact absolute `llama-server` executable with `shell=false`,
a scrubbed five-variable environment, private HOME/TMP directories, an exact
pinned model path, one request slot, an 8K context cap, and a fixed loopback
bind. It never binds `0.0.0.0`. Start and stop use a saved exact process identity
(PID, executable, full ordered argv, and start identity); unknown listeners and
PID reuse fail closed. No automatic download or package installation exists.
Start also refuses to proceed while either managed MAIN/FAST listener is
resident and applies a 30 GiB memory preflight; it never stops those services
on the RAW model's behalf.

The existing macOS `sandbox-exec` network-denial profile is retained for
Generic Project test execution. It is not wrapped around the Metal inference
server because the server requires a loopback socket and macOS/Metal runtime
file access; applying the permissive Generic profile would add no protection,
while an unreviewed restrictive Seatbelt profile could break Metal loading.
RAW host authority is instead removed at the service/provider interface and
the child receives no secrets or tool channel.

## Artifact and runtime states

- `NOT_DOWNLOADED`: model directory/artifact is absent.
- `INCOMPLETE`: partial cache exists, the exact artifact is unsafe, or SHA256
  does not match.
- `READY`: exact regular non-symlink artifact exists and its pinned SHA256 is
  verified.
- `RUNNING`: READY plus exact saved process/listener identity and loopback
  health.
- `UNHEALTHY`: runtime evidence exists but cannot be proven healthy/owned.

Pinned artifact:

- repository: `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`
- revision: `dee0a3164d9e11bbbebf5b63f52ba99443d14fc3`
- file: `Qwen3.8-27B-Uncensored-Q6_K.gguf`
- SHA256: `a50aa1478295b58ee3d93eabe02c17f6d5fcf6cb787fd8a0ab07ac629a46cae6`

## Owner CLI

The local CLI binds authority to the effective UID that owns `/Users/jerson/AI`:

```sh
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh status
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh health
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh start
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh smoke
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh stop
```

One later real qualification (start, health, bounded local generation, stop and
reap) is:

```sh
/Users/jerson/AI/control-plane/scripts/owner-raw-qwen.sh qualify
```

Qualification remains blocked until both the verified Q6_K artifact and the
pinned `/opt/homebrew/bin/llama-server` executable are available. The controller
does not install either one.
