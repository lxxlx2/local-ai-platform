# oMLX configuration

V1 policy: one large LLM loaded at a time; conservative concurrency; initial context 32K; no default 128K or 262K context. API starts on localhost only. Loaded-idle, 8K, 32K, and conditional 64K tests decide safe limits. Automatic unload/LRU/KV cache settings will only be recorded after confirming the installed oMLX version exposes them.
