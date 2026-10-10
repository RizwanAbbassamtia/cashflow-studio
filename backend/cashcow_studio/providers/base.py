"""Shared pieces of the provider layer: errors, cost estimates and health reports.

Providers are the pluggable tools behind the voice and images stages (``voice/`` and
``image/``). Every provider:

* declares what it can do in a capabilities object the Settings page can show;
* reads its API key from the process environment by *name* (``key_env``); the value never
  appears in logs, error messages, results or the API;
* reports ``health()`` from local facts only (key present, software installed), so the
  Doctor page never waits on the network;
* is synchronous. A stage runs a provider call with ``asyncio.to_thread`` so the event loop
  that serves the API stays responsive.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

ProviderKind = Literal["voice", "image"]
AdapterState = Literal["ready", "stub", "planned"]
"""``ready`` works; ``stub`` exists but waits for API details; ``planned`` arrives later."""
HealthStatus = Literal["ok", "warn", "fail", "not_configured", "planned"]
BillingUnit = Literal["character", "second", "image", "request", "credit", "free"]


class ProviderError(Exception):
    """A provider failed. The message is plain English and safe to show to the user."""


class ProviderNotConfigured(ProviderError):
    """The provider cannot run yet: no key, no adapter or missing software."""


class CostEstimate(BaseModel):
    provider: str
    unit: BillingUnit
    units: float = Field(default=0.0, ge=0, description="characters, seconds or images")
    cost_usd: float = Field(default=0.0, ge=0)
    note: str = ""


class ProviderHealth(BaseModel):
    """What the Doctor and Settings pages show for one provider. Never carries a key value."""

    provider: str
    kind: ProviderKind
    status: HealthStatus
    detail: str
    key_env: str = ""
    key_set: bool = False
    model: str = ""


def key_is_set(env_name: str) -> bool:
    """True when the named environment variable holds a non-blank value."""
    return bool(env_name) and bool(os.environ.get(env_name, "").strip())


def scrub_secret(text: str, secret: str | None) -> str:
    """Replace every copy of ``secret`` in ``text`` so a message can never carry a key."""
    if not secret or len(secret) < 8:
        return text
    return text.replace(secret, "***")
