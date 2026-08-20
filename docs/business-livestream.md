# BUSINESS_C — Livestream intelligence architecture

`Recording → Audio extraction → ASR → Timestamp subtitles → signal extraction → candidate detection → LLM semantic analysis → vision verification → clip score → FFmpeg/VideoToolbox → subtitles/title/cover → Human Approval → Publish`.

Do not send hours of frames directly to a 27B/35B LLM. Use timestamps, ASR, volume, scene changes, interaction and trend signals to narrow candidate clips. V1 installs none of the ASR, vision, video, or publishing components.
