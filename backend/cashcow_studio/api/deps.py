"""Request-scoped helpers shared by the routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..config import Settings
from ..storage.channel_store import ChannelStore
from ..storage.settings_store import SettingsStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_settings_store(request: Request) -> SettingsStore:
    return SettingsStore(get_settings(request).app_data_dir)


def get_channel_store(request: Request) -> ChannelStore:
    # Built per request so a changed shared folder takes effect immediately.
    return ChannelStore(get_settings(request).resolved_shared_dir)


SettingsDep = Annotated[Settings, Depends(get_settings)]
SettingsStoreDep = Annotated[SettingsStore, Depends(get_settings_store)]
ChannelStoreDep = Annotated[ChannelStore, Depends(get_channel_store)]
