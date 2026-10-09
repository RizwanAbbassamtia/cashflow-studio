"""Research engine: competitor scanning with yt-dlp, outlier scoring, the AI pick.

Modules:

* ``ytdlp_client``  - yt-dlp Python API: tab listings, video details, transcripts, thumbnails
* ``mock``          - offline provider from recorded fixtures (``CFS_RESEARCH_PROVIDER=mock``)
* ``provider``      - the ``ResearchProvider`` protocol and ``build_provider``
* ``cache``         - SQLite tables ``research_videos``, ``research_scans``, ``research_picks``
* ``outliers``      - pure maths: baselines, scores, labels, exclusions, cross-competitor rank
* ``picker``        - ``pick_one`` (strategy ``top_outlier_fresh``, optional LLM rerank hook)
* ``scanner``       - scan all competitors (cache first) and build candidates
* ``jobs``          - the ``research.scan`` background job
* ``research_stage`` - the pipeline stage writing ``01_research/``
"""

from .errors import ResearchBlocked, ResearchError, TranscriptUnavailable, VideoNotFound
from .provider import ResearchProvider, build_provider, provider_name
from .research_stage import ResearchStage

__all__ = [
    "ResearchBlocked",
    "ResearchError",
    "ResearchProvider",
    "ResearchStage",
    "TranscriptUnavailable",
    "VideoNotFound",
    "build_provider",
    "provider_name",
]
