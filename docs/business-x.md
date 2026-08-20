# BUSINESS_A — X automation architecture

`Data Sources → Crawler/API/Search → Dedup → Trend Scoring → Qwen analysis → Candidate Posts → Quality Review → Human Approval → Publish → Analytics → Strategy Update`.

Use deterministic code for ingestion, deduplication, and simple classification. Use the local model for summaries and candidates; reserve cloud review for high-value content. V1 does not create accounts, publish, or automate posting.
