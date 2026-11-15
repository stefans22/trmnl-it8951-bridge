"""4 bpp pixel packing for the IT8951 (little-endian load, words sent MSB first).

Reproduces the Waveshare reference: even pixels go into the low nibble of a
byte, the byte buffer is read as little-endian 16-bit words and each word is
sent high byte first. Wire bytes per 4 pixels p0..p3: ``[p3<<4 | p2, p1<<4 | p0]``.
Verified on hardware by the smoke test (gray bars, 1 px diagonals, gradients).
"""

from __future__ import annotations

import numpy as np

from .base import WHITE, Pixels


def pack_4bpp(region: Pixels) -> bytes:
    """Pack a (h, w) array of 0..15 values; rows are padded with white to whole words."""
    if region.ndim != 2:
        raise ValueError("region must be 2-D")
    h, w = region.shape
    pad = (-w) % 4
    if pad:
        region = np.pad(region, ((0, 0), (0, pad)), constant_values=WHITE)
    p = region.reshape(h, -1, 4).astype(np.uint8)
    out = np.empty((h, p.shape[1], 2), dtype=np.uint8)
    out[:, :, 0] = (p[:, :, 3] << 4) | p[:, :, 2]
    out[:, :, 1] = (p[:, :, 1] << 4) | p[:, :, 0]
    return out.tobytes()


def unpack_4bpp(data: bytes, width: int, height: int) -> Pixels:
    """Inverse of :func:`pack_4bpp` (used by tests)."""
    words = (width + 3) // 4
    b = np.frombuffer(data, dtype=np.uint8).reshape(height, words, 2)
    p = np.empty((height, words, 4), dtype=np.uint8)
    p[:, :, 3] = b[:, :, 0] >> 4
    p[:, :, 2] = b[:, :, 0] & 0xF
    p[:, :, 1] = b[:, :, 1] >> 4
    p[:, :, 0] = b[:, :, 1] & 0xF
    return p.reshape(height, words * 4)[:, :width].copy()
