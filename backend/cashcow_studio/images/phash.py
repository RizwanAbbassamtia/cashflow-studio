"""64-bit perceptual hash (pHash) with pure Pillow and plain Python; no numpy, no scipy.

The method is the classic one (also used by the ``imagehash`` package): grey-scale, shrink to
32 x 32, take the 2-D DCT-II, keep the 8 x 8 low-frequency block, and set a bit for every
coefficient above the block's median. Two pictures that look alike (same picture resized,
re-encoded, slightly colour-shifted) land within a few bits of each other; unrelated pictures
are about 32 bits apart. The images stage treats a Hamming distance of 6 or less as "the same
picture" (``policy/rules.yaml``, ``images.variety``).
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image

HASH_SIZE = 8
HIGHFREQ_FACTOR = 4
HASH_BITS = HASH_SIZE * HASH_SIZE


@lru_cache(maxsize=8)
def _cosines(n: int, k: int) -> tuple[tuple[float, ...], ...]:
    """``c[u][x] = cos(pi / n * (x + 0.5) * u)`` for the first ``k`` frequencies."""
    return tuple(
        tuple(math.cos(math.pi / n * (x + 0.5) * u) for x in range(n)) for u in range(k)
    )


def dct_lowfreq(pixels: list[list[float]], keep: int) -> list[list[float]]:
    """The top-left ``keep x keep`` block of the 2-D DCT-II of a square ``pixels`` matrix.

    Separable: rows first (only the first ``keep`` frequencies are needed), then columns.
    """
    n = len(pixels)
    cos = _cosines(n, keep)
    rows = [[sum(row[x] * cos[v][x] for x in range(n)) for v in range(keep)] for row in pixels]
    return [
        [sum(cos[u][y] * rows[y][v] for y in range(n)) for v in range(keep)] for u in range(keep)
    ]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _grey_matrix(image: Image.Image, side: int) -> list[list[float]]:
    small = image.convert("L").resize((side, side), Image.Resampling.LANCZOS)
    data = small.tobytes()  # one byte per pixel in mode "L", row by row
    return [[float(v) for v in data[y * side : (y + 1) * side]] for y in range(side)]


def _open(image: Image.Image | Path | str) -> tuple[Image.Image, bool]:
    if isinstance(image, Image.Image):
        return image, False
    return Image.open(image), True


def phash(
    image: Image.Image | Path | str,
    hash_size: int = HASH_SIZE,
    highfreq_factor: int = HIGHFREQ_FACTOR,
) -> int:
    """The perceptual hash of a picture as an integer of ``hash_size ** 2`` bits (64 by default)."""
    side = hash_size * highfreq_factor
    opened, close = _open(image)
    try:
        pixels = _grey_matrix(opened, side)
    finally:
        if close:
            opened.close()
    block = dct_lowfreq(pixels, hash_size)
    flat = [value for row in block for value in row]
    median = _median(flat)
    result = 0
    for value in flat:
        result = (result << 1) | (1 if value > median else 0)
    return result


def to_hex(value: int, bits: int = HASH_BITS) -> str:
    return f"{value:0{bits // 4}x}"


def phash_hex(image: Image.Image | Path | str) -> str:
    """The 64-bit hash as 16 lower-case hex characters (what ``images.json`` stores)."""
    return to_hex(phash(image))


def to_int(value: int | str) -> int:
    """Accept an int or a hex string (what the SQLite table stores)."""
    if isinstance(value, int):
        return value
    text = value.strip().lower()
    if not text:
        raise ValueError("an empty string is not an image hash")
    return int(text, 16)


def hamming(a: int | str, b: int | str) -> int:
    """How many of the 64 bits differ; 0 means identical hashes."""
    return (to_int(a) ^ to_int(b)).bit_count()


def nearest(target: int | str, candidates: list[int | str]) -> tuple[int, int] | None:
    """``(distance, index)`` of the closest candidate, or ``None`` when there are none."""
    best: tuple[int, int] | None = None
    for index, candidate in enumerate(candidates):
        distance = hamming(target, candidate)
        if best is None or distance < best[0]:
            best = (distance, index)
            if distance == 0:
                break
    return best


def similar(a: int | str, b: int | str, max_distance: int = 6) -> bool:
    return hamming(a, b) <= max_distance
