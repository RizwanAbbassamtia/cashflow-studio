"""A small perceptual hash (pHash) in pure Pillow and Python, for the thumbnail gate.

The images stage owns the main copy (``images/phash.py``); the export stage uses that one
when it is there and falls back to this file so the export gate never depends on another
module's timing. Same recipe as the common ``imagehash`` implementation: grey, 32x32,
2-D DCT-II, keep the 8x8 low-frequency block, one bit per value above the block's median.
Two pictures with the same layout land within a Hamming distance of about 10; unrelated
pictures sit around 32.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from statistics import median

from PIL import Image

HASH_SIZE = 8
IMAGE_SIZE = 32
HASH_BITS = HASH_SIZE * HASH_SIZE
FLAT_THRESHOLD = 2.0
"""Standard deviation (0-255 scale) under which a picture counts as flat (no detail)."""


@lru_cache(maxsize=1)
def _cosines() -> list[list[float]]:
    """``c[u][x] = cos(pi * u * (2x + 1) / (2N))`` for the first ``HASH_SIZE`` frequencies."""
    return [
        [math.cos(math.pi * u * (2 * x + 1) / (2 * IMAGE_SIZE)) for x in range(IMAGE_SIZE)]
        for u in range(HASH_SIZE)
    ]


def _grey_matrix(image: Image.Image) -> list[list[float]]:
    small = image.convert("L").resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    data = list(small.get_flattened_data()) if hasattr(small, "get_flattened_data") else list(
        small.getdata()  # Pillow < 12
    )
    return [
        [float(v) for v in data[row * IMAGE_SIZE : (row + 1) * IMAGE_SIZE]]
        for row in range(IMAGE_SIZE)
    ]


def low_frequency_dct(matrix: list[list[float]]) -> list[float]:
    """The top-left ``HASH_SIZE`` x ``HASH_SIZE`` block of the 2-D DCT-II, row-major."""
    cos = _cosines()
    # Rows first: partial[u][y] = sum over x of f[y][x] * cos(u, x).
    partial = [
        [sum(matrix[y][x] * cos[u][x] for x in range(IMAGE_SIZE)) for y in range(IMAGE_SIZE)]
        for u in range(HASH_SIZE)
    ]
    block: list[float] = []
    for v in range(HASH_SIZE):
        for u in range(HASH_SIZE):
            block.append(sum(partial[u][y] * cos[v][y] for y in range(IMAGE_SIZE)))
    return block


def _open(image: Image.Image | Path | str) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    with Image.open(image) as opened:
        return opened.convert("RGB")


def phash(image: Image.Image | Path | str) -> int:
    """64-bit perceptual hash of a picture (a path or a Pillow image)."""
    block = low_frequency_dct(_grey_matrix(_open(image)))
    threshold = median(block)
    value = 0
    for coefficient in block:
        value = (value << 1) | (1 if coefficient > threshold else 0)
    return value


def hamming(a: int, b: int) -> int:
    """How many of the 64 bits differ."""
    return bin((a ^ b) & ((1 << HASH_BITS) - 1)).count("1")


def is_flat(image: Image.Image | Path | str, threshold: float = FLAT_THRESHOLD) -> bool:
    """True for a picture with no detail (one colour, a broken download): nothing to compare."""
    matrix = _grey_matrix(_open(image))
    values = [v for row in matrix for v in row]
    mean = sum(values) / len(values)
    deviation = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return deviation < threshold


def distance(a: Image.Image | Path | str, b: Image.Image | Path | str) -> int:
    return hamming(phash(a), phash(b))
