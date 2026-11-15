from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ha_trmnl_ws_display.driver.base import Waveform
from ha_trmnl_ws_display.driver.fake import FakePanel
from ha_trmnl_ws_display.driver.packing import pack_4bpp, unpack_4bpp
from ha_trmnl_ws_display.geometry import Rect, controller_code, logical_size, to_physical

PW, PH = 48, 32  # small panel for exhaustive geometry checks


def test_controller_code_counter_clockwise() -> None:
    # Smoke test 2026-11-15: controller codes rotate counter-clockwise.
    assert [controller_code(r) for r in (0, 90, 180, 270)] == [0, 3, 2, 1]
    with pytest.raises(ValueError, match="rotation"):
        controller_code(45)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@given(data=st.data())
def test_to_physical_matches_rotating_the_image(rotation: int, data: st.DataObject) -> None:
    lw, lh = logical_size(rotation, PW, PH)
    x = data.draw(st.integers(0, lw - 1))
    y = data.draw(st.integers(0, lh - 1))
    w = data.draw(st.integers(1, lw - x))
    h = data.draw(st.integers(1, lh - y))
    logical = np.zeros((lh, lw), dtype=np.uint8)
    logical[y : y + h, x : x + w] = 1
    physical = np.rot90(logical, k=-(rotation // 90))  # clockwise
    p = to_physical(Rect(x, y, w, h), rotation, PW, PH)
    assert physical.shape == (PH, PW)
    assert physical[p.y : p.y + p.h, p.x : p.x + p.w].all()
    assert physical.sum() == p.area


def test_to_physical_rejects_bad_rotation() -> None:
    with pytest.raises(ValueError, match="rotation"):
        to_physical(Rect(0, 0, 1, 1), 45, PW, PH)


def test_rect_union() -> None:
    assert Rect(0, 0, 4, 4).union(Rect(8, 2, 4, 4)) == Rect(0, 0, 12, 6)


def test_pack_golden_waveshare_order() -> None:
    # p0..p3 = 1, 2, 3, 4 -> wire bytes [p3<<4|p2, p1<<4|p0] (verified on hardware)
    assert pack_4bpp(np.array([[1, 2, 3, 4]], dtype=np.uint8)) == bytes([0x43, 0x21])


def test_pack_pads_rows_with_white() -> None:
    packed = pack_4bpp(np.array([[0, 0, 0, 0, 0]], dtype=np.uint8))
    assert packed == bytes([0x00, 0x00, 0xFF, 0xF0])


def test_pack_rejects_1d() -> None:
    with pytest.raises(ValueError, match="2-D"):
        pack_4bpp(np.zeros(4, dtype=np.uint8))


@given(
    st.integers(1, 3).flatmap(
        lambda h: st.integers(1, 13).flatmap(
            lambda w: st.lists(st.integers(0, 15), min_size=w * h, max_size=w * h).map(
                lambda v: np.array(v, dtype=np.uint8).reshape(h, w)
            )
        )
    )
)
def test_pack_roundtrip(region: np.ndarray) -> None:
    h, w = region.shape
    assert np.array_equal(unpack_4bpp(pack_4bpp(region), w, h), region)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_fake_panel_shows_logical_image_upright(rotation: int) -> None:
    panel = FakePanel(PW, PH)
    panel.init()
    lw, lh = logical_size(rotation, PW, PH)
    rng = np.random.default_rng(rotation)
    image = rng.integers(0, 16, size=(lh, lw), dtype=np.uint8)
    panel.draw(image, 0, 0, rotation, Waveform.GC16)
    assert np.array_equal(panel.logical_screen(rotation), image)
    # A partial update in logical coordinates lands in the right place.
    image[4:12, 8:20] = 0
    panel.draw(image[4:12, 8:20], 8, 4, rotation, Waveform.GC16)
    assert np.array_equal(panel.logical_screen(rotation), image)
