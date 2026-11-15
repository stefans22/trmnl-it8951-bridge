"""IT8951 host protocol (ported from the Waveshare reference, see docs/design.md §3.7)."""

from __future__ import annotations

import logging
import struct
import time
from typing import Protocol

from ..geometry import Rect, controller_code, to_physical
from .base import LowPower, PanelError, PanelInfo, Pixels, Waveform
from .packing import pack_4bpp

log = logging.getLogger(__name__)

CMD_SYS_RUN = 0x0001
CMD_STANDBY = 0x0002
CMD_SLEEP = 0x0003
CMD_REG_RD = 0x0010
CMD_REG_WR = 0x0011
CMD_LD_IMG_AREA = 0x0021
CMD_LD_IMG_END = 0x0022
CMD_DPY_AREA = 0x0034
CMD_GET_DEV_INFO = 0x0302
CMD_VCOM = 0x0039

REG_I80CPCR = 0x0004
REG_LISAR = 0x0208
REG_LUTAFSR = 0x1224
REG_DRIVE = 0x0038

BPP_4 = 2
ENDIAN_LITTLE = 0


class Transport(Protocol):
    def command(self, cmd: int) -> None: ...
    def data(self, *words: int) -> None: ...
    def data_bulk(self, payload: bytes) -> None: ...
    def read(self, n_words: int) -> list[int]: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...


def _words_to_str(words: list[int]) -> str:
    raw = b"".join(struct.pack("<H", w) for w in words)
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


class IT8951Panel:
    def __init__(
        self,
        transport: Transport,
        vcom: float,
        *,
        enhance_driving: bool = False,
        ready_timeout_s: float = 10.0,
        expected_size: tuple[int | None, int | None] = (None, None),
        poll_interval_s: float = 0.005,
    ) -> None:
        self._t = transport
        self._vcom_mv = round(abs(vcom) * 1000)
        self._enhance = enhance_driving
        self._timeout = ready_timeout_s
        self._expected = expected_size
        self._poll = poll_interval_s
        self._info: PanelInfo | None = None

    @property
    def info(self) -> PanelInfo | None:
        return self._info

    # -- registers ---------------------------------------------------------- #

    def _reg_read(self, reg: int) -> int:
        self._t.command(CMD_REG_RD)
        self._t.data(reg)
        return self._t.read(1)[0]

    def _reg_write(self, reg: int, value: int) -> None:
        self._t.command(CMD_REG_WR)
        self._t.data(reg, value)

    # -- Panel protocol ----------------------------------------------------- #

    def init(self) -> PanelInfo:
        self._t.reset()
        self._t.command(CMD_SYS_RUN)
        self._t.command(CMD_GET_DEV_INFO)
        w = self._t.read(20)
        info = PanelInfo(
            width=w[0],
            height=w[1],
            image_buffer=w[2] | (w[3] << 16),
            fw_version=_words_to_str(w[4:12]),
            lut_version=_words_to_str(w[12:20]),
        )
        if not (0 < info.width <= 4096 and 0 < info.height <= 4096):
            raise PanelError(f"implausible device info {info} - SPI data corrupted?")
        ew, eh = self._expected
        if (ew is not None and ew != info.width) or (eh is not None and eh != info.height):
            raise PanelError(f"panel reports {info.width}x{info.height}, expected {ew}x{eh}")
        self._reg_write(REG_I80CPCR, 0x0001)  # packed write
        self._t.command(CMD_VCOM)
        self._t.data(0x0000)
        if self._t.read(1)[0] != self._vcom_mv:
            self._t.command(CMD_VCOM)
            self._t.data(0x0001, self._vcom_mv)
        if self._enhance:
            self._reg_write(REG_DRIVE, 0x0602)
        self._info = info
        log.info(
            "IT8951 ready: %dx%d, FW %s, LUT %s, VCOM -%.2f V",
            info.width,
            info.height,
            info.fw_version,
            info.lut_version,
            self._vcom_mv / 1000,
        )
        return info

    def wait_idle(self) -> None:
        deadline = time.monotonic() + self._timeout
        while self._reg_read(REG_LUTAFSR) != 0:
            if time.monotonic() > deadline:
                raise PanelError("display engine did not become idle")
            time.sleep(self._poll)

    def draw(self, region: Pixels, x: int, y: int, rotation: int, mode: Waveform) -> None:
        info = self._require_info()
        h, w = region.shape
        self.wait_idle()
        self._reg_write(REG_LISAR + 2, (info.image_buffer >> 16) & 0xFFFF)
        self._reg_write(REG_LISAR, info.image_buffer & 0xFFFF)
        self._t.command(CMD_LD_IMG_AREA)
        self._t.data((ENDIAN_LITTLE << 8) | (BPP_4 << 4) | controller_code(rotation), x, y, w, h)
        self._t.data_bulk(pack_4bpp(region))
        self._t.command(CMD_LD_IMG_END)
        phys = to_physical(Rect(x, y, w, h), rotation, info.width, info.height)
        self._t.command(CMD_DPY_AREA)
        self._t.data(phys.x, phys.y, phys.w, phys.h, int(mode))

    def low_power(self, kind: LowPower) -> None:
        if kind == "sleep":
            self._t.command(CMD_SLEEP)
        elif kind == "standby":
            self._t.command(CMD_STANDBY)

    def wake(self) -> None:
        self._t.command(CMD_SYS_RUN)

    def close(self) -> None:
        self._t.close()

    def _require_info(self) -> PanelInfo:
        if self._info is None:
            raise PanelError("panel not initialised")
        return self._info
