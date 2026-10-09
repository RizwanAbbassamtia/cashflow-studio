"""Timing estimates used when nobody measured the words: spread sentences over a recording
by their share of the text, and spread the words of a sentence inside its span by their
character length. Pure functions; the voice stage marks their output ``estimated``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ..models.timing import TimedWord

_WORD_CHARS = re.compile(r"[\w']", re.UNICODE)
MIN_WORD_WEIGHT = 1.0
PUNCTUATION_PAUSE = 0.6
"""Extra weight a word carries when it ends with . , ; : ! ? (the speaker pauses)."""


def word_tokens(text: str) -> list[str]:
    """The spoken words of ``text`` in order (whitespace split, punctuation kept attached)."""
    return [token for token in (text or "").split() if _WORD_CHARS.search(token)]


def word_weight(token: str) -> float:
    """How long a word takes relative to others: its letters, plus a pause after punctuation."""
    letters = len(_WORD_CHARS.findall(token))
    weight = max(float(letters), MIN_WORD_WEIGHT)
    if token[-1:] in ".,;:!?":
        weight += PUNCTUATION_PAUSE
    return weight


def sentence_weight(text: str) -> float:
    tokens = word_tokens(text)
    if not tokens:
        return MIN_WORD_WEIGHT
    return sum(word_weight(t) for t in tokens)


def estimate_words(
    text: str, start_s: float, end_s: float, *, confidence: float = 0.3
) -> list[TimedWord]:
    """Words of ``text`` laid out between ``start_s`` and ``end_s`` by character length.

    The first word starts exactly at ``start_s`` and the last ends exactly at ``end_s``.
    """
    tokens = word_tokens(text)
    if not tokens:
        return []
    span = max(0.0, float(end_s) - float(start_s))
    weights = [word_weight(t) for t in tokens]
    total = sum(weights) or 1.0
    words: list[TimedWord] = []
    clock = float(start_s)
    running = 0.0
    for index, (token, weight) in enumerate(zip(tokens, weights, strict=True)):
        running += weight
        end = float(end_s) if index == len(tokens) - 1 else float(start_s) + span * running / total
        end = max(end, clock)
        words.append(
            TimedWord(text=token, start_s=round(clock, 6), end_s=round(end, 6),
                      confidence=confidence)
        )
        clock = end
    return words


def distribute_sentences(
    texts: Sequence[str], total_s: float, *, lead_s: float = 0.0, tail_s: float = 0.0
) -> list[tuple[float, float]]:
    """``(start_s, end_s)`` for every text, sharing ``total_s`` by text length.

    ``lead_s`` and ``tail_s`` are silence kept before the first and after the last sentence
    (an own recording usually has a little of both); they are clipped so the sentences
    keep at least half of the recording.
    """
    count = len(texts)
    if count == 0:
        return []
    total = max(0.0, float(total_s))
    margin = max(0.0, float(lead_s)) + max(0.0, float(tail_s))
    if margin > total / 2:
        scale = (total / 2) / margin if margin else 0.0
        lead_s, tail_s = lead_s * scale, tail_s * scale
    usable = max(0.0, total - lead_s - tail_s)
    weights = [sentence_weight(t) for t in texts]
    weight_total = sum(weights) or 1.0
    spans: list[tuple[float, float]] = []
    clock = float(lead_s)
    running = 0.0
    for index, weight in enumerate(weights):
        running += weight
        end = lead_s + usable if index == count - 1 else lead_s + usable * running / weight_total
        end = max(end, clock)
        spans.append((round(clock, 6), round(end, 6)))
        clock = end
    return spans
