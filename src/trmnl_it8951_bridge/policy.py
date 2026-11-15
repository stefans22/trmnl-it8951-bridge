"""Refresh policy: skip, partial or full (design §3.5, ADR 0005)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .config import DisplayConfig
from .diff import dirty_rects
from .geometry import Rect
from .pipeline import Frame


@dataclass(frozen=True)
class Plan:
    kind: Literal["skip", "partial", "full"]
    reason: str
    rects: list[Rect] = field(default_factory=list)


def plan_update(current: Frame | None, new: Frame, since_full: int, cfg: DisplayConfig) -> Plan:
    if new.force_full:
        return Plan("full", "forced")
    if current is None:
        return Plan("full", "no previous frame")
    if current.hash == new.hash:
        return Plan("skip", "duplicate")
    if current.pixels.shape != new.pixels.shape or current.rotation != new.rotation:
        return Plan("full", "geometry changed")
    if cfg.full_refresh_every and since_full >= cfg.full_refresh_every:
        return Plan("full", f"cadence ({since_full} partial updates)")
    rects = dirty_rects(current.pixels, new.pixels, cfg.tile_size, cfg.max_rects)
    if not rects:  # same pixels, different hash input: nothing to do
        return Plan("skip", "no pixel changes")
    total = new.pixels.shape[0] * new.pixels.shape[1]
    ratio = sum(r.area for r in rects) / total
    if ratio > cfg.full_refresh_area_ratio:
        return Plan("full", f"changed area {ratio:.0%}")
    return Plan("partial", f"changed area {ratio:.0%}", rects)
