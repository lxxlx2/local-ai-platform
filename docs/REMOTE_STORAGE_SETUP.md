# Remote storage setup

Do not create a provider from this document. When authorized, supply separate private/public PostgreSQL connection strings and an S3-compatible endpoint through an ignored local secret source. Enable pgvector in the chosen PostgreSQL environment, apply reviewed migrations, verify separate credentials, and test deletion/retention before real users.

Use [config/local-ai-platform.env.example](../config/local-ai-platform.env.example) only as a placeholder reference. Never commit actual URLs containing credentials, access keys, tokens, or object data.
