"""Audio helpers for the voice stage: FFmpeg/ffprobe wrappers, sample-accurate WAV
concatenation, timing estimates and the optional forced aligner.

Everything here is CPU only. Only ``aligner.py`` can import a heavy package (WhisperX), and it
does so lazily inside ``align()``; importing this package costs nothing.
"""

from .errors import AudioError, AudioToolMissing

__all__ = ["AudioError", "AudioToolMissing"]
