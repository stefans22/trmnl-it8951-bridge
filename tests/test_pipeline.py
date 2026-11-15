from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from ha_trmnl_ws_display.config import DisplayConfig
from ha_trmnl_ws_display.pipeline import (
    ImageRejectedError,
    Pipeline,
    SizeMismatchError,
    to_png,
)

from .conftest import png


def pipe(**cfg: object) -> Pipeline:
    return Pipeline(DisplayConfig.model_validate(cfg), 120, 80)


def test_quantizes_to_16_levels() -> None:
    img = np.tile(np.arange(0, 256, 17, dtype=np.uint8).repeat(8), (80, 1))[:, :120]
    frame = pipe().process(png(img), "push")
    assert frame.pixels.dtype == np.uint8
    assert frame.pixels.shape == (80, 120)
    assert list(frame.pixels[0, ::8]) == list(range(15))
    assert frame.source == "push"
    assert not frame.force_full


def test_preview_roundtrips_through_png() -> None:
    p = pipe()
    frame = p.process(png(np.full((80, 120), 136, np.uint8)), "push")
    again = p.process(to_png(frame.preview), "restore")
    assert again.hash == frame.hash


def test_hash_identifies_content() -> None:
    p = pipe()
    a = p.process(png(np.full((80, 120), 255, np.uint8)), "push")
    b = p.process(png(np.full((80, 120), 255, np.uint8)), "pull")
    c = p.process(png(np.full((80, 120), 0, np.uint8)), "push")
    assert a.hash == b.hash != c.hash
    assert a.id != b.id


def test_alpha_is_flattened_on_white() -> None:
    rgba = np.zeros((80, 120, 4), np.uint8)  # fully transparent black
    frame = pipe().process(png(rgba, mode="RGBA"), "push")
    assert (frame.pixels == 15).all()


def test_jpeg_and_rgb_are_accepted() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (0, 0, 0)).save(buf, format="JPEG")
    assert (pipe().process(buf.getvalue(), "push").pixels == 0).all()


@pytest.mark.parametrize("data", [b"", b"not an image", b"\x89PNG\r\n\x1a\n broken"])
def test_garbage_is_rejected(data: bytes) -> None:
    with pytest.raises(ImageRejectedError) as e:
        pipe().process(data, "push")
    assert e.value.status_code == 400


def test_huge_image_is_rejected_before_decoding() -> None:
    with pytest.raises(ImageRejectedError, match="too large"):
        pipe().process(png(np.zeros((400, 400), np.uint8)), "push")


def test_size_mismatch_rejected_by_default() -> None:
    with pytest.raises(SizeMismatchError) as e:
        pipe().process(png(np.zeros((40, 60), np.uint8)), "push")
    assert e.value.status_code == 422
    assert "expected 120x80" in str(e.value)


def test_size_fit_letterboxes_white() -> None:
    frame = pipe(size_mismatch="fit").process(png(np.zeros((80, 80), np.uint8)), "push")
    assert frame.pixels.shape == (80, 120)
    assert (frame.pixels[:, 0] == 15).all()  # white bar on the left
    assert (frame.pixels[:, 60] == 0).all()


def test_size_stretch() -> None:
    frame = pipe(size_mismatch="stretch").process(png(np.zeros((10, 10), np.uint8)), "push")
    assert frame.pixels.shape == (80, 120)
    assert (frame.pixels == 0).all()


def test_portrait_expects_rotated_size() -> None:
    p = pipe(rotation=90)
    assert p.logical_size == (80, 120)
    frame = p.process(png(np.zeros((120, 80), np.uint8)), "push")
    # Controller rotates: frame stays logical, driver gets rotation 90.
    assert frame.pixels.shape == (120, 80)
    assert frame.rotation == 90


def marked_image() -> np.ndarray:
    img = np.full((120, 80), 255, np.uint8)  # portrait logical 80x120
    img[0:10, 0:20] = 0  # top-left marker
    return img


def test_software_rotation_produces_physical_frame() -> None:
    p = pipe(rotation=90, rotation_via_controller=False)
    frame = p.process(png(marked_image()), "push")
    assert frame.pixels.shape == (80, 120)
    assert frame.rotation == 0
    # Clockwise: logical top-left ends up physical top-right.
    assert (frame.pixels[0:20, 110:120] == 0).all()
    assert frame.preview.shape == (120, 80)


@pytest.mark.parametrize("via_controller", [True, False])
def test_mirror_is_physical_in_both_modes(via_controller: bool) -> None:
    """mirror_x flips the physical x axis, whoever does the rotation."""
    from ha_trmnl_ws_display.driver.base import Waveform
    from ha_trmnl_ws_display.driver.fake import FakePanel

    p = pipe(rotation=90, rotation_via_controller=via_controller, mirror_x=True)
    frame = p.process(png(marked_image()), "push")
    panel = FakePanel(120, 80)
    panel.init()
    panel.draw(frame.pixels, 0, 0, frame.rotation, Waveform.GC16)
    # Without mirroring the marker is physical top-right; mirrored on x it is top-left.
    assert (panel.screen[0:20, 0:10] == 0).all()
    assert (panel.screen[0:20, 110:120] == 15).all()


def test_dither_uses_only_palette_levels() -> None:
    gradient = np.tile(np.linspace(0, 255, 120, dtype=np.uint8), (80, 1))
    frame = pipe(dither=True).process(png(gradient), "push")
    assert frame.pixels.max() <= 15
    assert len(np.unique(frame.pixels)) > 8


def test_forced_copy() -> None:
    frame = pipe().process(png(np.zeros((80, 120), np.uint8)), "push")
    forced = frame.forced()
    assert forced.force_full
    assert forced.hash == frame.hash
