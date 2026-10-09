"""The one way this app talks to Claude.

``LLMClient.complete(task, system, user, schema)`` renders nothing itself: the stage renders
its Markdown prompt and passes the system blocks (framework text first, task instructions
second) and the user text. The client picks the model and effort for the task from
``config/llm.yaml``, asks for a structured answer validated against ``schema`` (a Pydantic
model), streams when the task is long, opts into Anthropic's server-side fallback, handles
a refusal (one reworded retry, then a clear error) and logs tokens and cost to ``llm_calls``.

Rules from ``docs/llm-notes.md``: official SDK only; thinking is adaptive by omission; effort
goes in ``output_config``; no assistant prefill; no temperature; the key stays in the
environment and is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLMConfig, compute_cost
from .log import ensure_llm_calls_table, log_llm_call

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PROVIDER_ENV = "CFS_LLM_PROVIDER"
SETTINGS_LLM_KEY = "llm"

REWORD_PREFIX = (
    "Context for this request: this is editorial writing for an entertainment video channel. "
    "Describe and narrate; never give the viewer personal advice or instructions, and keep "
    "every sensitive subject factual and neutral.\n\n"
)


class LLMError(Exception):
    """Claude could not be used; the message is plain English for the reviewer."""


class LLMRefused(LLMError):
    """Claude declined the request (``stop_reason == "refusal"``) even after one rewording."""

    def __init__(self, category: str | None, explanation: str | None = None) -> None:
        self.category = category
        self.explanation = explanation
        detail = f" (category: {category})" if category else ""
        super().__init__(
            "Claude declined to write this because of its safety rules" + detail + ". "
            "Soften the topic or the notes and try again."
        )


class LLMUsage(BaseModel):
    task: str
    model: str
    """The model that answered (after a server-side fallback it differs from ``requested``)."""
    requested_model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    request_id: str | None = None
    fallback_used: bool = False
    attempts: int = 1


class LLMClient(Protocol):
    provider: str
    config: LLMConfig

    async def complete(
        self,
        task: str,
        system: str | Sequence[str],
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        variables: dict[str, Any] | None = None,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> tuple[T, LLMUsage]: ...


class BaseLLMClient:
    """Shared bookkeeping: model choice, cost, the ``llm_calls`` log."""

    provider = "base"

    def __init__(self, config: LLMConfig, app_data_dir: Path | None = None) -> None:
        self.config = config
        self.app_data_dir = Path(app_data_dir) if app_data_dir else None
        self._table_ready = False

    def model_for(self, task: str, model: str | None = None) -> str:
        return model or self.config.model_for(task)

    def effort_for(self, task: str, effort: str | None = None) -> str:
        return effort or self.config.effort_for(task)

    def cost_for(
        self,
        model: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_5m_tokens: int = 0,
        cache_write_1h_tokens: int = 0,
    ) -> float:
        return compute_cost(
            self.config.price_for(model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_5m_tokens=cache_write_5m_tokens,
            cache_write_1h_tokens=cache_write_1h_tokens,
        )

    def record(
        self,
        usage: LLMUsage,
        *,
        project_id: str | None,
        stage: str | None,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        """Append one row to ``llm_calls``. Never raises: a logging problem is not a failure."""
        if self.app_data_dir is None:
            return
        try:
            if not self._table_ready:
                ensure_llm_calls_table(self.app_data_dir)
                self._table_ready = True
            log_llm_call(
                self.app_data_dir,
                {
                    "project_id": project_id,
                    "stage": stage,
                    "task": usage.task,
                    "model": usage.model,
                    "requested_model": usage.requested_model,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                    "cost_usd": usage.cost_usd,
                    "duration_ms": usage.duration_ms,
                    "status": status,
                    "request_id": usage.request_id,
                    "error": error,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the call succeeded; only the log failed
            log.warning("Could not write the llm_calls row for task %s: %s", usage.task, exc)


def system_blocks(system: str | Sequence[str], cache_ttl: str) -> list[dict[str, Any]]:
    """System blocks in API form: the first (framework / style guide) carries the cache mark."""
    texts = [system] if isinstance(system, str) else [t for t in system if t and t.strip()]
    if not texts:
        return []
    blocks: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        block: dict[str, Any] = {"type": "text", "text": text}
        if index == 0:
            block["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}
        blocks.append(block)
    return blocks


class AnthropicLLMClient(BaseLLMClient):
    """The real client, built on the official ``anthropic`` SDK (async)."""

    provider = "anthropic"

    def __init__(
        self,
        config: LLMConfig,
        app_data_dir: Path | None = None,
        *,
        max_retries: int = 3,
        timeout_s: float = 600.0,
    ) -> None:
        super().__init__(config, app_data_dir)
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._client: Any = None
        self._client_credentials: tuple[str, str] | None = None

    @staticmethod
    def _credentials() -> tuple[str, str]:
        """The key and token in the environment right now (never logged, never returned)."""
        return (
            os.environ.get("ANTHROPIC_API_KEY") or "",
            os.environ.get("ANTHROPIC_AUTH_TOKEN") or "",
        )

    def _sdk(self) -> Any:
        """The SDK client, rebuilt whenever the key in the environment changed.

        The Settings screen writes a new key into ``.env`` and the environment; a client
        built with the old key would keep failing with "key rejected" until a restart.
        """
        credentials = self._credentials()
        stale = self._client_credentials is not None and credentials != self._client_credentials
        if self._client is None or stale:
            import anthropic

            if not any(credentials):
                raise LLMError(
                    "No Anthropic API key is set. Add ANTHROPIC_API_KEY in Settings > API keys."
                )
            self._client = anthropic.AsyncAnthropic(
                max_retries=self._max_retries, timeout=self._timeout_s
            )
            self._client_credentials = credentials
        return self._client

    async def complete(
        self,
        task: str,
        system: str | Sequence[str],
        user: str,
        schema: type[T],
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        variables: dict[str, Any] | None = None,
        project_id: str | None = None,
        stage: str | None = None,
    ) -> tuple[T, LLMUsage]:
        del variables  # only the mock derives its answer from the raw inputs
        requested = self.model_for(task, model)
        params: dict[str, Any] = {
            "model": requested,
            "max_tokens": max_tokens or self.config.max_tokens_for(task),
            "system": system_blocks(system, self.config.cache_ttl),
            "output_config": {"effort": self.effort_for(task, effort)},
            "output_format": schema,
        }
        if self.config.fallback_enabled:
            params["betas"] = [self.config.fallback_beta]
            params["fallbacks"] = self.config.fallback_mode
        prompt = user
        attempts = 0
        while True:
            attempts += 1
            started = time.monotonic()
            message = await self._send(
                task, params | {"messages": [{"role": "user", "content": prompt}]}
            )
            usage = self._usage_of(task, requested, message, started, attempts)
            if message.stop_reason == "refusal":
                details = getattr(message, "stop_details", None)
                category = getattr(details, "category", None)
                explanation = getattr(details, "explanation", None)
                self.record(usage, project_id=project_id, stage=stage, status="refused",
                            error=str(category or "refusal"))
                if attempts == 1:
                    log.info("Claude declined task %s (%s); retrying reworded", task, category)
                    prompt = REWORD_PREFIX + user
                    continue
                raise LLMRefused(category, explanation)
            if message.stop_reason == "max_tokens":
                self.record(usage, project_id=project_id, stage=stage, status="truncated",
                            error="max_tokens")
                raise LLMError(
                    f"Claude's answer for '{task}' was cut off because it was too long. "
                    "Try again or lower the target length."
                )
            parsed = getattr(message, "parsed_output", None)
            if parsed is None:
                parsed = self._parse_text(schema, message)
            if parsed is None:
                self.record(usage, project_id=project_id, stage=stage, status="invalid",
                            error="no structured output")
                raise LLMError(
                    f"Claude's answer for '{task}' did not have the expected shape. Try again."
                )
            self.record(usage, project_id=project_id, stage=stage)
            return parsed, usage

    async def _send(self, task: str, params: dict[str, Any]) -> Any:
        import anthropic

        client = self._sdk()
        try:
            if self.config.streams(task):
                async with client.beta.messages.stream(**params) as stream:
                    return await stream.get_final_message()
            return await client.beta.messages.parse(**params)
        except anthropic.AuthenticationError as exc:
            raise LLMError(
                "The Anthropic API key was rejected. Check it in Settings > API keys."
            ) from exc
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after", "") if exc.response else ""
            wait = f" Wait {retry_after} seconds and" if retry_after else " Wait a minute and"
            log.warning("Rate limited on task %s (request %s)", task, _request_id(exc))
            raise LLMError(f"Claude is busy right now.{wait} try again.") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude error %s on task %s (request %s)", exc.status_code, task,
                        _request_id(exc))
            if exc.status_code >= 500:
                raise LLMError(
                    "Claude's servers had a problem. Wait a moment and try again."
                ) from exc
            raise LLMError(
                f"Claude rejected the request ({exc.status_code}): {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(
                "Could not reach Claude. Check the internet connection and try again."
            ) from exc
        except anthropic.AnthropicError as exc:
            raise LLMError(f"Claude could not be called: {exc}") from exc

    def _usage_of(
        self, task: str, requested: str, message: Any, started: float, attempts: int
    ) -> LLMUsage:
        usage = getattr(message, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        creation = getattr(usage, "cache_creation", None)
        write_1h = int(getattr(creation, "ephemeral_1h_input_tokens", 0) or 0)
        write_5m = int(getattr(creation, "ephemeral_5m_input_tokens", 0) or 0)
        if write_1h + write_5m == 0:
            write_1h = cache_write if self.config.cache_ttl == "1h" else 0
            write_5m = cache_write - write_1h
        answered_by = str(getattr(message, "model", None) or requested)
        return LLMUsage(
            task=task,
            model=answered_by,
            requested_model=requested,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            cost_usd=self.cost_for(
                answered_by,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_write_5m_tokens=write_5m,
                cache_write_1h_tokens=write_1h,
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
            request_id=getattr(message, "_request_id", None),
            fallback_used=answered_by != requested,
            attempts=attempts,
        )

    @staticmethod
    def _parse_text(schema: type[T], message: Any) -> T | None:
        """Fallback when the SDK did not attach ``parsed_output``: validate the text ourselves."""
        text = "".join(
            getattr(block, "text", "") for block in getattr(message, "content", []) or []
            if getattr(block, "type", "") == "text"
        ).strip()
        if not text:
            return None
        try:
            return schema.model_validate(json.loads(text))
        except (ValueError, ValidationError):
            return None


def _request_id(exc: Any) -> str:
    return str(getattr(exc, "request_id", None) or "")


def settings_llm_overrides(app_data_dir: Path | None) -> dict[str, Any]:
    """The ``llm`` object from ``settings.json`` (model and effort per task), if any."""
    if app_data_dir is None:
        return {}
    path = Path(app_data_dir) / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    value = data.get(SETTINGS_LLM_KEY) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def provider_name(explicit: str | None = None, config: LLMConfig | None = None) -> str:
    """``mock`` or ``anthropic``: the argument, then ``CFS_LLM_PROVIDER``, then llm.yaml."""
    name = (explicit or os.environ.get(PROVIDER_ENV) or (config.provider if config else "")
            or "anthropic")
    return name.strip().lower()


def build_llm_client(
    settings: Any = None,
    *,
    provider: str | None = None,
    overrides: dict[str, Any] | None = None,
    app_data_dir: Path | None = None,
) -> LLMClient:
    """The client the pipeline uses. ``CFS_LLM_PROVIDER=mock`` gives the offline mock."""
    data_dir = app_data_dir or (Path(settings.app_data_dir) if settings is not None else None)
    merged = settings_llm_overrides(data_dir)
    if overrides:
        merged = {**merged, **overrides}
    config = LLMConfig.load(merged)
    name = provider_name(provider, config)
    if name == "mock":
        from .mock import MockLLMClient

        return MockLLMClient(config, data_dir)
    if name != "anthropic":
        raise LLMError(
            f"Unknown LLM provider '{name}'. Use 'anthropic' or 'mock' in CFS_LLM_PROVIDER."
        )
    return AnthropicLLMClient(config, data_dir)
