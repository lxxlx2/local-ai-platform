# Quality Gates

`QualityPolicyRegistry` covers CHAT_RESPONSE, CODE_CHANGE, CONFIG_CHANGE, DATABASE_MIGRATION, SECURITY_CHANGE, MODEL_PROVIDER_CHANGE, FILE_PROCESSING, PUBLIC_FEATURE, PRIVATE_CONTROL, CONTENT_GENERATION, PUBLISHING, and DELETE_OPERATION.

`QualityGateService` returns `BLOCKED`, `REVIEW_REQUIRED`, or `ACCEPTANCE_READY`. Tests alone never produce acceptance: code needs an independent reviewer distinct from its producer, and high-risk policies need security approval.
