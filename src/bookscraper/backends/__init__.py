from __future__ import annotations

from .dedup import CompositeDedupSource, DedupSource, MongoDedupSource, StagingDedupSource, resolve_dedup_source

__all__ = ["CompositeDedupSource", "DedupSource", "MongoDedupSource", "StagingDedupSource", "resolve_dedup_source"]
