"""Frame pipeline: decode -> size policy -> quantize -> mirror/rotate -> hash (design §3.3)."""

from __future__ import annotations

import hashlib
import io
import itertools
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import DisplayConfig
from .driver.base import Pixels
from .geometry import logical_size

ALLOWED_FORMATS = ("PNG", "JPEG", "BMP", "GIF", "WEBP")
_ids = itertools.count(1)


class ImageRejectedError(ValueError):
    """The input can't be shown. ``status_code`` is the HTTP code for push."""

    status_code = 400


class SizeMismatchError(ImageRejectedError):
    status_code = 422


@dataclass(frozen=True, eq=False)
class Frame:
    id: int
    source: str
    received_at: datetime
    pixels: Pixels
    """Quantized 0..15 in frame coordinates (what is sent to the panel)."""
    preview: Pixels
    """Quantized 0..15 in logical orientation (what the viewer sees)."""
    rotation: int
    """Clockwise rotation the driver applies when uploading (0 in the software fallback)."""
    hash: str
    force_full: bool = False

    def forced(self) -> Frame:
        return replace(self, force_full=True)


class Pipeline:
    def __init__(self, cfg: DisplayConfig, panel_width: int, panel_height: int) -> None:
        self.cfg = cfg
        self.panel_size = (panel_width, panel_height)
        self.logical_size = logical_size(cfg.rotation, panel_width, panel_height)
        self._max_pixels = 4 * panel_width * panel_height
        self._palette = _gray_palette()

    def process(self, data: bytes, source: str, *, force_full: bool = False) -> Frame:
        received = datetime.now(UTC)
        img = self._decode(data)
        img = self._fit(img)
        preview = self._quantize(img)
        pixels = self._orient(preview)
        h = hashlib.blake2b(digest_size=16)
        h.update(f"{pixels.shape}:{self._driver_rotation}".encode())
        h.update(pixels.tobytes())
        return Frame(
            id=next(_ids),
            source=source,
            received_at=received,
            pixels=pixels,
            preview=preview,
            rotation=self._driver_rotation,
            hash=h.hexdigest(),
            force_full=force_full,
        )

    @property
    def _driver_rotation(self) -> int:
        return self.cfg.rotation if self.cfg.rotation_via_controller else 0

    # -- steps ------------------------------------------------------------- #

    def _decode(self, data: bytes) -> Image.Image:
        if not data:
            raise ImageRejectedError("empty body")
        try:
            img: Image.Image = Image.open(io.BytesIO(data), formats=ALLOWED_FORMATS)
            if img.width * img.height > self._max_pixels:
                raise ImageRejectedError(f"image too large: {img.width}x{img.height}")
            img.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError) as e:
            raise ImageRejectedError(f"cannot decode image: {e}") from e
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, rgba)
        return img.convert("L")

    def _fit(self, img: Image.Image) -> Image.Image:
        size = self.logical_size
        if img.size == size:
            return img
        policy = self.cfg.size_mismatch
        if policy == "reject":
            raise SizeMismatchError(
                f"image is {img.width}x{img.height}, expected {size[0]}x{size[1]} "
                f"(rotation {self.cfg.rotation}); set the add-on viewport accordingly"
            )
        if policy == "fit":
            return ImageOps.pad(img, size, method=Image.Resampling.LANCZOS, color=255)
        return img.resize(size, Image.Resampling.LANCZOS)

    def _quantize(self, img: Image.Image) -> Pixels:
        if self.cfg.dither:
            # Pillow only maps RGB input onto a custom palette; the indices are the gray levels.
            q = img.convert("RGB").quantize(palette=self._palette, dither=Image.Dither.FLOYDSTEINBERG)
            return np.asarray(q, dtype=np.uint8).copy()
        a = np.asarray(img, dtype=np.uint16)
        return ((a * 15 + 127) // 255).astype(np.uint8)

    def _orient(self, preview: Pixels) -> Pixels:
        rot = self.cfg.rotation
        mx, my = self.cfg.mirror_x, self.cfg.mirror_y  # physical axes (after rotation)
        out = preview
        if self.cfg.rotation_via_controller:
            # Mirror in logical space; a physical x-flip is a logical y-flip at 90/270.
            if rot in (90, 270):
                mx, my = my, mx
        else:
            out = np.rot90(out, k=-(rot // 90))
        if mx:
            out = out[:, ::-1]
        if my:
            out = out[::-1, :]
        return np.ascontiguousarray(out)


def _gray_palette() -> Image.Image:
    pal = Image.new("P", (1, 1))
    levels = [v for i in range(16) for v in (i * 17,) * 3]
    pal.putpalette(levels + [0] * (768 - len(levels)))
    return pal


def to_png(pixels: Pixels) -> bytes:
    """Encode 0..15 gray values as an 8-bit grayscale PNG."""
    buf = io.BytesIO()
    Image.fromarray((pixels * 17).astype(np.uint8), mode="L").save(buf, format="PNG", optimize=False)
    return buf.getvalue()
