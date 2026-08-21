# Business-outcome learning

Business outcomes can help prioritize reviewed examples but cannot override quality and safety.

V0.1 supports namespace-specific metric allowlists for X content, stickers, livestream content, novel editing, and personal work. Values must be finite, non-negative, bounded numbers; currency uses a three-letter code; external content is referenced by SHA-256; and source type is limited to manual or fixture evidence. Unknown metrics are rejected.

An outcome contributes only when both `verified` and `quality_pass` are true. High impressions, downloads, revenue, or engagement with a failed quality gate produces zero learning score. Owner corrections, explicit approvals, business relevance, and verified outcomes increase review priority; unapproved synthetic-only data is penalized.

These scores prioritize review and dataset curation. They never directly promote an adapter or convert public/model-generated content into trusted truth.
