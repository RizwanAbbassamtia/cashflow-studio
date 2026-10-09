"""Caption language codes for the channel languages in ``models/channel.py``."""

from __future__ import annotations

LANGUAGE_CODES: dict[str, str] = {
    "English": "en",
    "Spanish": "es",
    "Hindi": "hi",
    "Arabic": "ar",
    "Portuguese": "pt",
    "Indonesian": "id",
    "Japanese": "ja",
    "German": "de",
    "French": "fr",
    "Russian": "ru",
    "Vietnamese": "vi",
    "Turkish": "tr",
    "Korean": "ko",
    "Urdu": "ur",
    "Italian": "it",
    "Other": "en",
}


def language_code(language: str | None) -> str:
    """``"Spanish" -> "es"``; unknown names fall back to English."""
    return LANGUAGE_CODES.get(language or "", "en")


def caption_track_candidates(code: str) -> list[str]:
    """Track names yt-dlp may list for one language, most exact first.

    YouTube names the original-language auto track ``xx-orig`` and sometimes uses a regional
    code (``en-US``, ``pt-BR``); the caller also accepts any key starting with ``xx-``.
    """
    base = code.split("-")[0].lower()
    names = [code, f"{base}-orig", base]
    return list(dict.fromkeys(names))
