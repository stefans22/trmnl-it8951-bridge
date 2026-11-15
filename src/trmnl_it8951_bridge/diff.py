"""Changed-area detection: tile diff -> connected components -> merged rects (ADR 0005)."""

from __future__ import annotations

from collections import deque
from itertools import combinations

import numpy as np

from .driver.base import Pixels
from .geometry import Rect

MERGE_SLACK = 1.3
"""Merge two rects if their union is at most this factor larger than both together."""


def dirty_rects(prev: Pixels, new: Pixels, tile: int, max_rects: int) -> list[Rect]:
    """Rectangles (frame coordinates) covering every pixel that differs.

    ``x`` is always a multiple of ``tile`` (and so of 4); widths are multiples of
    ``tile`` except at the right edge of the frame.
    """
    if prev.shape != new.shape:
        raise ValueError(f"shape mismatch {prev.shape} != {new.shape}")
    if tile <= 0 or max_rects <= 0:
        raise ValueError("tile and max_rects must be positive")
    mask = prev != new
    if not mask.any():
        return []
    h, w = mask.shape
    th, tw = -(-h // tile), -(-w // tile)
    padded = np.zeros((th * tile, tw * tile), dtype=bool)
    padded[:h, :w] = mask
    tiles = padded.reshape(th, tile, tw, tile).any(axis=(1, 3))

    rects = [_to_pixels(box, tile, w, h) for box in _components(tiles)]
    rects = _merge(rects, max_rects)
    return sorted(rects, key=lambda r: (r.y, r.x))


def _components(tiles: np.ndarray) -> list[Rect]:
    """Bounding boxes (in tile units) of 8-connected groups of dirty tiles."""
    th, tw = tiles.shape
    seen = np.zeros_like(tiles)
    boxes: list[Rect] = []
    for r0, c0 in zip(*np.nonzero(tiles), strict=True):
        if seen[r0, c0]:
            continue
        seen[r0, c0] = True
        queue = deque([(int(r0), int(c0))])
        rmin = rmax = int(r0)
        cmin = cmax = int(c0)
        while queue:
            r, c = queue.popleft()
            rmin, rmax, cmin, cmax = min(rmin, r), max(rmax, r), min(cmin, c), max(cmax, c)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < th and 0 <= cc < tw and tiles[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        queue.append((rr, cc))
        boxes.append(Rect(cmin, rmin, cmax - cmin + 1, rmax - rmin + 1))
    return boxes


def _to_pixels(box: Rect, tile: int, w: int, h: int) -> Rect:
    x, y = box.x * tile, box.y * tile
    return Rect(x, y, min(box.w * tile, w - x), min(box.h * tile, h - y))


def _merge(rects: list[Rect], max_rects: int) -> list[Rect]:
    rects = list(rects)
    merged = True
    while merged and len(rects) > 1:
        merged = False
        for a, b in combinations(rects, 2):
            u = a.union(b)
            if u.area <= (a.area + b.area) * MERGE_SLACK:
                rects.remove(a)
                rects.remove(b)
                rects.append(u)
                merged = True
                break
    while len(rects) > max_rects:
        a, b = min(combinations(rects, 2), key=lambda p: p[0].union(p[1]).area - p[0].area - p[1].area)
        rects.remove(a)
        rects.remove(b)
        rects.append(a.union(b))
    return rects
