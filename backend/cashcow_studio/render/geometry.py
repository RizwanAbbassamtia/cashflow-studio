"""Rectangle maths shared by the timeline builder and the renderer.

A storyboard camera move is a pair of fraction rectangles ``[x, y, w, h]`` in 0-1. The
timeline stores them in pixels of the source image, always with the frame's aspect ratio
and always inside the part of the image that covers the frame (the "cover region"): the
renderer scales that region to its working canvas and zooms between the two rectangles.
"""

from __future__ import annotations

from collections.abc import Sequence

Rect = tuple[float, float, float, float]


def cover_region(image_w: float, image_h: float, frame_w: float, frame_h: float) -> Rect:
    """The centred part of the image with the frame's aspect ratio (what ``scale`` +
    ``crop`` keep when the image covers the frame): ``(x, y, w, h)`` in image pixels."""
    if image_w <= 0 or image_h <= 0 or frame_w <= 0 or frame_h <= 0:
        return (0.0, 0.0, max(image_w, 1.0), max(image_h, 1.0))
    target = frame_w / frame_h
    if image_w / image_h > target:
        height = float(image_h)
        width = height * target
    else:
        width = float(image_w)
        height = width / target
    return ((image_w - width) / 2.0, (image_h - height) / 2.0, width, height)


def rect_in_pixels(
    frac_rect: Sequence[float],
    image_w: float,
    image_h: float,
    frame_w: float,
    frame_h: float,
) -> list[float]:
    """A fraction rectangle of the frame -> pixels of the source image, fitted to the
    frame's aspect ratio around the same centre and kept inside the cover region."""
    rx, ry, rw, rh = cover_region(image_w, image_h, frame_w, frame_h)
    fx, fy, fw, fh = (float(v) for v in frac_rect)
    fw = min(max(fw, 0.01), 1.0)
    fh = min(max(fh, 0.01), 1.0)
    fx = min(max(fx, 0.0), 1.0 - fw)
    fy = min(max(fy, 0.0), 1.0 - fh)
    target = frame_w / frame_h if frame_h else 1.0
    width, height = fw * rw, fh * rh
    centre_x, centre_y = rx + (fx + fw / 2.0) * rw, ry + (fy + fh / 2.0) * rh
    if width / height > target:
        width = height * target
    else:
        height = width / target
    if width > rw:
        width, height = rw, rw / target
    if height > rh:
        height, width = rh, rh * target
    x = min(max(centre_x - width / 2.0, rx), rx + rw - width)
    y = min(max(centre_y - height / 2.0, ry), ry + rh - height)
    return [round(x, 2), round(y, 2), round(width, 2), round(height, 2)]


def rect_to_canvas(
    rect_px: Sequence[float], region: Rect, canvas_w: float, canvas_h: float
) -> Rect:
    """Source-image pixels -> canvas pixels (the cover region scaled to the canvas), clamped
    so the renderer never asks for pixels outside the canvas."""
    rx, ry, rw, rh = region
    factor = canvas_w / rw if rw else 1.0
    x, y, w, h = (float(v) for v in rect_px)
    w = min(max(w * factor, 1.0), canvas_w)
    h = min(max(h * factor, 1.0), canvas_h)
    x = min(max((x - rx) * factor, 0.0), canvas_w - w)
    y = min(max((y - ry) * factor, 0.0), canvas_h - h)
    return (x, y, w, h)
