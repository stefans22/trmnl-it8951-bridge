"""Rectangles and the logical <-> physical mapping for panel rotation (ADR 0008)."""

from __future__ import annotations

from typing import NamedTuple


class Rect(NamedTuple):
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self) -> int:
        return self.w * self.h

    def union(self, other: Rect) -> Rect:
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1 = max(self.x + self.w, other.x + other.w)
        y1 = max(self.y + self.h, other.y + other.h)
        return Rect(x0, y0, x1 - x0, y1 - y0)


def logical_size(rotation: int, width: int, height: int) -> tuple[int, int]:
    """Size of the image that fills a ``width`` x ``height`` panel at ``rotation``."""
    return (height, width) if rotation in (90, 270) else (width, height)


def to_physical(rect: Rect, rotation: int, width: int, height: int) -> Rect:
    """Map a rect in logical (image) coordinates to physical panel coordinates.

    ``rotation`` is the clockwise rotation of the image onto the panel;
    ``width``/``height`` are the physical panel dimensions.
    """
    x, y, w, h = rect
    if rotation == 0:
        return rect
    if rotation == 90:
        return Rect(width - y - h, x, h, w)
    if rotation == 180:
        return Rect(width - x - w, height - y - h, w, h)
    if rotation == 270:
        return Rect(y, height - x - w, h, w)
    raise ValueError(f"invalid rotation {rotation}")


def controller_code(rotation: int) -> int:
    """IT8951 LD_IMG rotate code for a clockwise rotation.

    The controller's codes turn the image counter-clockwise (smoke test
    2026-11-15, LUT M841), so clockwise 90 is code 3.
    """
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"invalid rotation {rotation}")
    return ((360 - rotation) % 360) // 90
