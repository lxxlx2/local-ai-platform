# ADR-0001: Local-first provider and authority policy

Status: ACCEPTED

## Decision

Routine production uses qualified local providers by default. OpenAI Codex model quota is reserved for high-value planning, acceptance and difficult escalation. Gemini is a cloud reviewer/second opinion when privacy permits. Host/Supervisor owns machine permissions.

## Required boundaries

- LOCAL_QWEN is the default routine worker where qualified.
- Codex model quota is not consumed for ordinary repetitive production when local capability is sufficient.
- Gemini has no ambient shell, Git, deployment, service-control or credential authority.
- PUBLIC may use allowed cloud review; RESTRICTED requires minimization/egress controls; PRIVATE may deny cloud review.
- Models cannot expand their own host authority.
- Long-term target is a host-owned LocalToolExecutor so routine local work does not depend on cloud-model availability.

## Scope

Applies to coding, research synthesis, X/content work, novel production, media orchestration, retrieval, recurring workflows and future specialist models.

## Tracking

- Infrastructure roadmap: Issue #14.
- Product contract: Issue #15.
- Interactive exception/priority: ADR-0002 and Issue #18.

## Production state

This ADR defines policy. Individual provider paths still require their own implementation, qualification, review, merge and activation gates.
