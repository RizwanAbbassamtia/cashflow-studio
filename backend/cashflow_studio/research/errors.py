"""Errors raised by the research engine. Every message is plain English for the UI."""

from __future__ import annotations


class ResearchError(Exception):
    """Something went wrong while researching; the message is safe to show to the user."""


class ResearchBlocked(ResearchError):
    """YouTube asked this computer to slow down or sign in; research must pause for a while."""


class VideoNotFound(ResearchError):
    """The video id is unknown, private or removed."""


class TranscriptUnavailable(ResearchError):
    """No captions in any usable language."""
