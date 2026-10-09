"""``/api/channels``: the channel files behind the Channel Setup form."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import Field

from ..llm.frameworks import check_framework_path
from ..models.channel import Channel, ChannelSummary, Framework, FrameworkType
from ..storage.channel_store import (
    SLUG_PATTERN,
    ChannelExists,
    ChannelNotFound,
    ChannelStoreError,
    InvalidChannelName,
    InvalidSlug,
    UrlCheck,
    make_slug,
    validate_url,
)
from .deps import ChannelStoreDep

router = APIRouter(prefix="/channels", tags=["channels"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class ChannelCreate(Channel):
    """A channel as posted by the form: the slug may be left out and is then derived."""

    slug: str | None = Field(default=None, pattern=SLUG_PATTERN)


def _not_found(exc: ChannelNotFound) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _check_frameworks(channel: Channel) -> None:
    """Framework files must sit inside the shared folder (relative path) or be a web link.

    The stages read these files and send their text to the writing model, so a full path such
    as ``C:/Users/.../.env`` must never get as far as the channel file.
    """
    for index, framework in enumerate(channel.frameworks):
        problem = check_framework_path(framework.path)
        if problem is not None:
            raise HTTPException(
                status_code=422,
                detail=f"Framework {index + 1} ('{framework.name}'): {problem}",
            )


def _not_saved(exc: OSError) -> HTTPException:
    """channel.json could not be written: a Drive sync, antivirus or Explorer preview holding
    the file, a read-only attribute, or a full or disconnected drive."""
    return HTTPException(
        status_code=500,
        detail=f"The channel file could not be saved: {exc}. If the shared folder is "
        "syncing, wait a moment and try again.",
    )


@router.get("", response_model=list[ChannelSummary])
def list_channels(store: ChannelStoreDep) -> list[ChannelSummary]:
    return store.list()


# Declared before ``/{slug}`` so the literal path wins.
@router.get("/validate-url", response_model=UrlCheck)
def validate_channel_url(
    url: Annotated[str, Query(description="A YouTube channel or video link")],
) -> UrlCheck:
    return validate_url(url)


@router.post("", response_model=Channel, status_code=201)
def create_channel(body: ChannelCreate, store: ChannelStoreDep) -> Channel:
    try:
        slug = body.slug or make_slug(body.channel.name)
    except InvalidChannelName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    channel = Channel.model_validate(body.model_dump(mode="json") | {"slug": slug})
    _check_frameworks(channel)
    try:
        return store.create(channel)
    except ChannelExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidSlug as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise _not_saved(exc) from exc


@router.get("/{slug}", response_model=Channel)
def get_channel(slug: str, store: ChannelStoreDep) -> Channel:
    try:
        return store.get(slug)
    except (ChannelNotFound, InvalidSlug) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The channel file could not be read: {exc}"
        ) from exc


@router.put("/{slug}", response_model=Channel)
def update_channel(slug: str, body: Channel, store: ChannelStoreDep) -> Channel:
    if body.slug != slug:
        raise HTTPException(
            status_code=422,
            detail=f"The channel id in the data ('{body.slug}') does not match the one in "
            f"the address ('{slug}').",
        )
    _check_frameworks(body)
    try:
        return store.update(slug, body)
    except (ChannelNotFound, InvalidSlug) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChannelStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except OSError as exc:
        raise _not_saved(exc) from exc


@router.delete("/{slug}", status_code=204, response_class=Response)
def archive_channel(slug: str, store: ChannelStoreDep) -> Response:
    try:
        store.archive(slug)
    except (ChannelNotFound, InvalidSlug) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The channel folder could not be moved: {exc}"
        ) from exc
    return Response(status_code=204)


@router.post("/{slug}/frameworks/upload", response_model=Framework)
async def upload_framework(
    slug: str,
    file: Annotated[UploadFile, File()],
    framework_type: Annotated[FrameworkType, Form(alias="type")],
    store: ChannelStoreDep,
) -> Framework:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    try:
        return store.save_framework(slug, file.filename, data, framework_type)
    except ChannelNotFound as exc:
        raise _not_found(exc) from exc
    except InvalidSlug as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"The file could not be saved: {exc}"
        ) from exc
