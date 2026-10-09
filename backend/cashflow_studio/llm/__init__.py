"""Claude layer: one client, Markdown prompts, structured outputs, cost logging and a mock.

* ``client.py``: :class:`LLMClient` (``complete(task, system, user, schema) -> (parsed, usage)``),
  the real :class:`AnthropicLLMClient` and :func:`build_llm_client`.
* ``mock.py``: :class:`MockLLMClient`, selected by ``CFS_LLM_PROVIDER=mock``; deterministic,
  schema-valid answers derived from the inputs, no network.
* ``config.py``: ``config/llm.yaml`` (models, effort, prices) and the other YAML files.
* ``prompts.py``: the ``prompts/*.md`` templates with ``{{placeholders}}``.
* ``frameworks.py``: the channel's framework files (PDF, TXT, MD) as text.
* ``log.py``: the ``llm_calls`` SQLite table.
"""

from .client import (
    AnthropicLLMClient,
    BaseLLMClient,
    LLMClient,
    LLMError,
    LLMRefused,
    LLMUsage,
    build_llm_client,
)
from .mock import MockLLMClient

__all__ = [
    "AnthropicLLMClient",
    "BaseLLMClient",
    "LLMClient",
    "LLMError",
    "LLMRefused",
    "LLMUsage",
    "MockLLMClient",
    "build_llm_client",
]
