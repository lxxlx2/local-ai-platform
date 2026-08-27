# Model Registry Authority

`config/model-registry-v0.1.json` is the sole runtime authority for production
role eligibility. Only `QUALIFIED` and `VALIDATED` aliases may be routed or
loaded. `REGISTERED_NOT_QUALIFIED` and `REGISTERED_NOT_DOWNLOADED` remain visible
inventory and are never execution permission.

The Python profile catalog contains immutable identity and safety metadata only:
known model/provider IDs, supported roles, local runtime and model paths, data
egress, memory expectations, and owner-only restrictions. The JSON schema cannot
add profiles or override those fields. Startup fails closed for an unknown
profile/status/role, missing alias, extra schema field, unsafe context value, or
a change to the pinned isolation and safety policy.

The RAW profile additionally pins its repository, revision, exact GGUF filename,
SHA256, llama.cpp runtime class, Owner-only flag and denied host capabilities.
Configuration cannot weaken those fields or inject another RAW repository.

Normal chat selects qualified MAIN. An explicit fast request selects FAST. If
MAIN is absent, unhealthy, or denied by the resource check, routing falls back
to FALLBACK and then FAST. Runtime health and resource checks remain mandatory
even after versioned qualification.
