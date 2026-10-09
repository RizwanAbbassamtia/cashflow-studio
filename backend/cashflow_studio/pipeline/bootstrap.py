"""Builds the one :class:`PipelineEngine` the app keeps on ``app.state.engine``.

Stages and providers are imported lazily and individually: a stage module that is missing
(or broken) only means that stage is marked ``awaiting_manual`` when a project reaches it,
never that the app fails to start. The research, title, script and storyboard stages are
built by other parts of the code base and picked up here by import path.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from typing import Any

from ..config import Settings
from .engine import DEFAULT_MAX_PARALLEL, PipelineEngine
from .events import EventBus
from .jobs import registry as job_registry

log = logging.getLogger(__name__)

STAGE_CLASSES: tuple[tuple[str, str], ...] = (
    ("cashflow_studio.research.research_stage", "ResearchStage"),
    ("cashflow_studio.pipeline.stages.title", "TitleStage"),
    ("cashflow_studio.pipeline.stages.script", "ScriptStage"),
    ("cashflow_studio.pipeline.stages.storyboard", "StoryboardStage"),
    ("cashflow_studio.pipeline.stages.voice", "VoiceStage"),
    ("cashflow_studio.pipeline.stages.images", "ImagesStage"),
    ("cashflow_studio.pipeline.stages.edit", "EditStage"),
    ("cashflow_studio.pipeline.stages.export", "ExportStage"),
)
PROVIDERS_MODULE = "cashflow_studio.providers.registry"
LLM_MODULE = "cashflow_studio.llm"
RESEARCH_PROVIDER_MODULE = "cashflow_studio.research.provider"
MAX_PARALLEL_ENV = "CFS_MAX_PARALLEL_PROJECTS"


def max_parallel_projects(settings: Settings) -> int:
    """``CFS_MAX_PARALLEL_PROJECTS`` (environment) or Settings > pipeline; default 2."""
    for candidate in (
        os.environ.get(MAX_PARALLEL_ENV),
        getattr(getattr(settings, "pipeline", None), "max_parallel_projects", None),
        getattr(settings, "max_parallel_projects", None),
    ):
        try:
            value = int(candidate) if candidate not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if value is not None and value >= 1:
            return value
    return DEFAULT_MAX_PARALLEL


def _build_from(module_name: str, function_name: str, settings: Settings, what: str) -> Any:
    """Call ``module.function(settings)``; ``None`` (and a log line) when it cannot be built."""
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        log.info("No %s yet; stages run without it.", what)
        return None
    build = getattr(module, function_name, None)
    if build is None:
        return None
    try:
        return build(settings)
    except Exception:  # noqa: BLE001 - a misconfigured provider must not stop the app
        log.exception("The %s could not be built; stages run without it.", what)
        return None


def load_providers(settings: Settings) -> dict[str, Any]:
    """Everything ``StageContext.providers`` carries.

    * ``image`` and ``voice`` from ``providers.registry.build_providers`` (M3 adapters, mocks);
    * ``llm`` from ``cashflow_studio.llm.build_llm_client`` (Claude or the mock), with the
      per-task model and effort overrides from Settings > Models and providers;
    * ``research`` from ``cashflow_studio.research.provider.build_provider`` (yt-dlp or mock).

    A part that cannot be built is left out (the stage then fails with a plain message)
    rather than stopping the app.
    """
    providers: dict[str, Any] = {}
    registry_providers = _build_from(
        PROVIDERS_MODULE, "build_providers", settings, "provider registry"
    )
    if isinstance(registry_providers, dict):
        providers.update(registry_providers)
    llm = _build_from(LLM_MODULE, "build_llm_client", settings, "writing model client")
    if llm is not None:
        providers["llm"] = llm
    research = _build_from(
        RESEARCH_PROVIDER_MODULE, "build_provider", settings, "research provider"
    )
    if research is not None:
        providers["research"] = research
    return providers


def refresh_providers(engine: PipelineEngine, settings: Settings) -> None:
    """Rebuild the providers after a settings change, in place.

    Stage contexts share the engine's provider dict, so the next stage that runs sees the
    new model choices, research source and parallel limit without a restart.
    """
    fresh = load_providers(settings)
    engine.providers.clear()
    engine.providers.update(fresh)
    engine.set_max_parallel(max_parallel_projects(settings))


def load_stages() -> list[Any]:
    """Instantiate every stage class that can be imported; skip (and log) the others."""
    stages: list[Any] = []
    for module_name, class_name in STAGE_CLASSES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            log.debug("Stage module %s is not available", module_name)
            continue
        except Exception:  # noqa: BLE001 - a broken stage module must not stop the app
            log.exception("Stage module %s could not be loaded", module_name)
            continue
        cls = getattr(module, class_name, None)
        if cls is None:
            log.warning("Stage module %s has no class %s", module_name, class_name)
            continue
        try:
            stages.append(cls())
        except Exception:  # noqa: BLE001
            log.exception("Stage %s could not be created", class_name)
    return stages


_unsubscribe_jobs: Callable[[], None] | None = None


def build_engine(settings: Settings) -> PipelineEngine:
    global _unsubscribe_jobs
    events = EventBus()
    # One app at a time listens to the process-wide job registry (tests build many apps).
    if _unsubscribe_jobs is not None:
        _unsubscribe_jobs()
    _unsubscribe_jobs = job_registry.subscribe(events.job_log)
    engine = PipelineEngine(
        settings,
        events=events,
        jobs=job_registry,
        providers=load_providers(settings),
        max_parallel=max_parallel_projects(settings),
    )
    for stage in load_stages():
        try:
            engine.register(stage)
        except (AttributeError, ValueError):
            log.exception("Stage %r has no valid name and was not registered", stage)
    return engine
