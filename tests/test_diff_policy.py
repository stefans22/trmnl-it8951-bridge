from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trmnl_it8951_bridge.config import DisplayConfig
from trmnl_it8951_bridge.diff import dirty_rects
from trmnl_it8951_bridge.geometry import Rect
from trmnl_it8951_bridge.pipeline import Frame
from trmnl_it8951_bridge.policy import plan_update

W, H = 1200, 825


def blank() -> np.ndarray:
    return np.full((H, W), 15, np.uint8)


def test_no_change() -> None:
    assert dirty_rects(blank(), blank(), 32, 8) == []


def test_single_pixel_is_one_tile() -> None:
    new = blank()
    new[100, 100] = 0
    assert dirty_rects(blank(), new, 32, 8) == [Rect(96, 96, 32, 32)]


def test_right_and_bottom_edge_are_clamped() -> None:
    new = blank()
    new[H - 1, W - 1] = 0
    (r,) = dirty_rects(blank(), new, 32, 8)
    assert r.x + r.w == W
    assert r.y + r.h == H
    assert r.x % 4 == 0


def test_two_far_apart_changes_stay_separate() -> None:
    new = blank()
    new[10, 10] = 0
    new[800, 1190] = 0
    rects = dirty_rects(blank(), new, 32, 8)
    assert len(rects) == 2
    assert sum(r.area for r in rects) < 0.01 * W * H


def test_adjacent_changes_merge() -> None:
    new = blank()
    new[100:110, 100:300] = 0  # spans several tiles in a row
    new[140, 100] = 0  # one tile row below, touching diagonally
    assert len(dirty_rects(blank(), new, 32, 8)) == 1


def test_nearby_rects_merge_when_cheap() -> None:
    new = blank()
    new[0:10, 0:128] = 0  # tiles 0..3
    new[0:10, 160:288] = 0  # tiles 5..8, one-tile gap: union costs only one extra tile
    assert dirty_rects(blank(), new, 32, 8) == [Rect(0, 0, 288, 32)]


def test_max_rects_is_respected() -> None:
    new = blank()
    for i in range(10):
        new[i * 80, (i * 113) % W] = 0
    assert len(dirty_rects(blank(), new, 32, 3)) <= 3


def test_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="shape"):
        dirty_rects(blank(), np.zeros((10, 10), np.uint8), 32, 8)


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="positive"):
        dirty_rects(blank(), blank(), 0, 8)


@settings(max_examples=60, deadline=None)
@given(
    points=st.lists(st.tuples(st.integers(0, 199), st.integers(0, 132)), min_size=1, max_size=25),
    tile=st.sampled_from([4, 8, 16, 32]),
    max_rects=st.integers(1, 8),
)
def test_rects_cover_every_change(points: list[tuple[int, int]], tile: int, max_rects: int) -> None:
    w, h = 201, 133  # deliberately not tile-aligned
    prev = np.full((h, w), 15, np.uint8)
    new = prev.copy()
    for x, y in points:
        new[y, x] = 0
    rects = dirty_rects(prev, new, tile, max_rects)
    assert 1 <= len(rects) <= max_rects
    covered = np.zeros((h, w), bool)
    for r in rects:
        assert r.x % 4 == 0
        assert r.x >= 0
        assert r.y >= 0
        assert r.x + r.w <= w
        assert r.y + r.h <= h
        covered[r.y : r.y + r.h, r.x : r.x + r.w] = True
    assert covered[new != prev].all()


# --------------------------------------------------------------------------- #


def frame(pixels: np.ndarray, *, force: bool = False, rotation: int = 0) -> Frame:
    return Frame(
        id=0,
        source="test",
        received_at=datetime.now(UTC),
        pixels=pixels,
        preview=pixels,
        rotation=rotation,
        hash=str(hash(pixels.tobytes())) + str(rotation),
        force_full=force,
    )


CFG = DisplayConfig()


def test_policy_first_frame_is_full() -> None:
    assert plan_update(None, frame(blank()), 0, CFG).kind == "full"


def test_policy_duplicate_is_skipped() -> None:
    assert plan_update(frame(blank()), frame(blank()), 0, CFG).kind == "skip"


def test_policy_forced_beats_duplicate() -> None:
    assert plan_update(frame(blank()), frame(blank(), force=True), 0, CFG).kind == "full"


def test_policy_small_change_is_partial() -> None:
    new = blank()
    new[0:10, 0:10] = 0
    plan = plan_update(frame(blank()), frame(new), 0, CFG)
    assert plan.kind == "partial"
    assert plan.rects == [Rect(0, 0, 32, 32)]


def test_policy_cadence_forces_full() -> None:
    new = blank()
    new[0, 0] = 0
    assert plan_update(frame(blank()), frame(new), 9, CFG).kind == "partial"
    assert plan_update(frame(blank()), frame(new), 10, CFG).kind == "full"
    never = DisplayConfig(full_refresh_every=0)
    assert plan_update(frame(blank()), frame(new), 1000, never).kind == "partial"


def test_policy_large_change_is_full() -> None:
    new = blank()
    new[:, : W * 2 // 3] = 0
    plan = plan_update(frame(blank()), frame(new), 0, CFG)
    assert plan.kind == "full"
    assert "area" in plan.reason


def test_policy_geometry_change_is_full() -> None:
    assert plan_update(frame(blank()), frame(blank(), rotation=180), 0, CFG).kind == "full"
