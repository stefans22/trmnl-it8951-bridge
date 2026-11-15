"""Interfaces shared by the real and the fake panel."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt

Pixels = npt.NDArray[np.uint8]
"""2-D array (rows, columns) of 4-bit gray values 0 (black) .. 15 (white)."""

WHITE = 15
BLACK = 0

LowPower = Literal["sleep", "standby", "none"]


class Waveform(IntEnum):
    """Display modes of the IT8951 (values for LUT M841 / 9.7")."""

    INIT = 0
    GC16 = 2


class PanelError(RuntimeError):
    """The controller did not respond or reported something unexpected."""


@dataclass(frozen=True)
class PanelInfo:
    width: int
    height: int
    fw_version: str
    lut_version: str
    image_buffer: int


class Panel(Protocol):
    @property
    def info(self) -> PanelInfo | None: ...

    def init(self) -> PanelInfo:
        """Reset the controller, read device info and apply VCOM. Idempotent."""
        ...

    def draw(self, region: Pixels, x: int, y: int, rotation: int, mode: Waveform) -> None:
        """Upload ``region`` to frame coordinates (x, y) and refresh exactly that area.

        Frame coordinates are logical (image) coordinates for the given clockwise
        ``rotation``; the panel maps them to physical coordinates itself.
        Returns once the refresh has been started, not when it has finished.
        """
        ...

    def wait_idle(self) -> None:
        """Block until the display engine has finished all refreshes."""
        ...

    def low_power(self, kind: LowPower) -> None: ...

    def wake(self) -> None: ...

    def close(self) -> None: ...
