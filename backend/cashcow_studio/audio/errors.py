"""Errors of the audio helpers; every message is plain English for the reviewer."""

from __future__ import annotations


class AudioError(Exception):
    """A file could not be read, converted or stitched."""


class AudioToolMissing(AudioError):
    """FFmpeg or ffprobe was not found on this PC."""
