"""In-memory panel for tests and local development (``panel.driver: fake``)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from ..geometry import Rect, logical_size, to_physical
from .base import WHITE, LowPower, PanelError, PanelInfo, Pixels, Waveform


@dataclass
class Op:
    name: str
    rect: Rect | None = None
    mode: Waveform | None = None
    rotation: int = 0
    extra: dict[str, object] = field(default_factory=dict)


class FakePanel:
    """Simulates controller memory and the visible screen in physical coordinates.

    ``fail_next`` makes the next N calls of ``init``/``draw`` raise ``PanelError``
    to exercise error recovery.
    """

    def __init__(self, width: int = 1200, height: int = 825) -> None:
        self._size = (width, height)
        self._info: PanelInfo | None = None
        self.memory: Pixels = np.full((height, width), WHITE, dtype=np.uint8)
        self.screen: Pixels = np.full((height, width), WHITE, dtype=np.uint8)
        self.ops: list[Op] = []
        self.fail_next = 0
        self.asleep = False
        self.closed = False
        self._lock = threading.Lock()

    @property
    def info(self) -> PanelInfo | None:
        return self._info

    def _maybe_fail(self, what: str) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise PanelError(f"injected failure in {what}")

    def init(self) -> PanelInfo:
        with self._lock:
            self._maybe_fail("init")
            w, h = self._size
            self._info = PanelInfo(w, h, "fake", "M841", 0x0011_A1E0)
            self.asleep = False
            self.ops.append(Op("init"))
            return self._info

    def draw(self, region: Pixels, x: int, y: int, rotation: int, mode: Waveform) -> None:
        with self._lock:
            self._maybe_fail("draw")
            if self._info is None:
                raise PanelError("panel not initialised")
            pw, ph = self._size
            h, w = region.shape
            lw, lh = logical_size(rotation, pw, ph)
            if x < 0 or y < 0 or x + w > lw or y + h > lh:
                raise PanelError(f"area {Rect(x, y, w, h)} outside the {lw}x{lh} frame")
            phys = to_physical(Rect(x, y, w, h), rotation, pw, ph)
            # np.rot90 with k=-1 rotates clockwise.
            rotated = np.rot90(region, k=-(rotation // 90))
            self.memory[phys.y : phys.y + phys.h, phys.x : phys.x + phys.w] = rotated
            self.screen[phys.y : phys.y + phys.h, phys.x : phys.x + phys.w] = rotated
            self.ops.append(Op("draw", Rect(x, y, w, h), mode, rotation, {"physical": phys}))

    def wait_idle(self) -> None:
        self.ops.append(Op("wait_idle"))

    def low_power(self, kind: LowPower) -> None:
        if kind != "none":
            self.asleep = True
            self.ops.append(Op("low_power", extra={"kind": kind}))

    def wake(self) -> None:
        self.asleep = False
        self.ops.append(Op("wake"))

    def close(self) -> None:
        self.closed = True
        self.ops.append(Op("close"))

    # -- test helpers ------------------------------------------------------ #

    def draws(self) -> list[Op]:
        return [op for op in self.ops if op.name == "draw"]

    def logical_screen(self, rotation: int) -> Pixels:
        """The screen as seen by a viewer of the rotated image."""
        return np.rot90(self.screen, k=rotation // 90).copy()
