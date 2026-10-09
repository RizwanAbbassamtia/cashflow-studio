"""Perceptual hash maths with synthetic pictures: identical and near-identical pictures land
within a few bits, unrelated ones far apart; Hamming distance and hex round-trips."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter

from cashcow_studio.images.phash import (
    HASH_BITS,
    dct_lowfreq,
    hamming,
    nearest,
    phash,
    phash_hex,
    similar,
    to_hex,
    to_int,
)


def gradient(width: int = 320, height: int = 180, seed: int = 1) -> Image.Image:
    """A smooth gradient with a few big shapes: enough structure for a stable hash."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (int(255 * x / width), int(255 * y / height), 120)
    draw = ImageDraw.Draw(image)
    rng = random.Random(seed)
    for _ in range(4):
        x0, y0 = rng.randint(0, width - 60), rng.randint(0, height - 60)
        draw.ellipse([x0, y0, x0 + rng.randint(40, 140), y0 + rng.randint(40, 100)],
                     fill=(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    return image


def checkerboard(width: int = 320, height: int = 180, cell: int = 20) -> Image.Image:
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if (x // cell + y // cell) % 2 == 0:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=255)
    return image


def jpeg_round_trip(image: Image.Image, quality: int = 60) -> Image.Image:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def test_hash_is_64_bits_stable_and_hex_round_trips(tmp_path) -> None:
    picture = gradient()
    value = phash(picture)
    assert 0 <= value < 2**HASH_BITS
    assert phash(picture) == value  # deterministic
    text = phash_hex(picture)
    assert len(text) == 16 and text == to_hex(value)
    assert to_int(text) == value and to_int(text.upper()) == value
    path = tmp_path / "p.png"
    picture.save(path)
    assert phash(path) == value and phash(str(path)) == value  # paths work too
    with pytest.raises(ValueError):
        to_int("   ")


def test_near_identical_pictures_are_close_and_different_ones_far() -> None:
    original = gradient(seed=1)
    resized = original.resize((160, 90), Image.Resampling.LANCZOS)
    compressed = jpeg_round_trip(original, quality=40)
    blurred = original.filter(ImageFilter.GaussianBlur(1.5))
    assert hamming(phash(original), phash(original)) == 0
    assert hamming(phash(original), phash(resized)) <= 6
    assert hamming(phash(original), phash(compressed)) <= 6
    assert hamming(phash(original), phash(blurred)) <= 6
    assert similar(phash_hex(original), phash_hex(resized))

    other = gradient(seed=7)
    board = checkerboard()
    assert hamming(phash(original), phash(board)) > 12
    assert hamming(phash(original), phash(other)) > 6
    assert not similar(phash(original), phash(board))
    inverted = Image.eval(original.convert("L"), lambda v: 255 - v)
    assert hamming(phash(original), phash(inverted)) > 40  # the opposite picture


def test_hamming_and_nearest_maths() -> None:
    assert hamming(0, 0) == 0
    assert hamming(0b1011, 0b0001) == 2
    assert hamming("ffffffffffffffff", "0000000000000000") == 64
    assert hamming("00000000000000ff", 0) == 8
    assert nearest(0b1111, []) is None
    assert nearest(0b1111, [0b0000, 0b1110, 0b1111]) == (0, 2)
    assert nearest("f0", ["0f", "f1"]) == (1, 1)


def test_dct_lowfreq_matches_a_direct_computation() -> None:
    import math

    n, keep = 8, 3
    rng = random.Random(3)
    pixels = [[rng.uniform(0, 255) for _ in range(n)] for _ in range(n)]
    block = dct_lowfreq(pixels, keep)
    for u in range(keep):
        for v in range(keep):
            expected = sum(
                pixels[y][x]
                * math.cos(math.pi / n * (x + 0.5) * v)
                * math.cos(math.pi / n * (y + 0.5) * u)
                for y in range(n)
                for x in range(n)
            )
            assert block[u][v] == pytest.approx(expected, rel=1e-9, abs=1e-6)
