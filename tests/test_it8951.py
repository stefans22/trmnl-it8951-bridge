"""Protocol tests with a recording transport (no hardware)."""

from __future__ import annotations

import struct
from collections import deque

import numpy as np
import pytest

from trmnl_it8951_bridge.driver.base import PanelError, Waveform
from trmnl_it8951_bridge.driver.it8951 import (
    CMD_DPY_AREA,
    CMD_GET_DEV_INFO,
    CMD_LD_IMG_AREA,
    CMD_LD_IMG_END,
    CMD_REG_RD,
    CMD_REG_WR,
    CMD_SLEEP,
    CMD_STANDBY,
    CMD_SYS_RUN,
    CMD_VCOM,
    REG_LISAR,
    REG_LUTAFSR,
    IT8951Panel,
)
from trmnl_it8951_bridge.driver.packing import pack_4bpp


def dev_info_words(w: int = 1200, h: int = 825, addr: int = 0x0011A1E0) -> list[int]:
    def text(s: str) -> list[int]:
        raw = s.encode().ljust(16, b"\0")
        return list(struct.unpack("<8H", raw))

    return [w, h, addr & 0xFFFF, addr >> 16, *text("SWv_0.2.1T"), *text("M841")]


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.reads: deque[list[int]] = deque()
        self.closed = False

    def command(self, cmd: int) -> None:
        self.calls.append(("cmd", cmd))

    def data(self, *words: int) -> None:
        self.calls.append(("data", words))

    def data_bulk(self, payload: bytes) -> None:
        self.calls.append(("bulk", payload))

    def read(self, n_words: int) -> list[int]:
        self.calls.append(("read", n_words))
        words = self.reads.popleft() if self.reads else [0] * n_words
        assert len(words) == n_words
        return words

    def reset(self) -> None:
        self.calls.append(("reset", None))

    def close(self) -> None:
        self.closed = True


def ready_panel(**kwargs: object) -> tuple[IT8951Panel, RecordingTransport]:
    t = RecordingTransport()
    t.reads.extend([dev_info_words(), [1870]])
    panel = IT8951Panel(t, -1.87, **kwargs)  # type: ignore[arg-type]
    panel.init()
    t.calls.clear()
    return panel, t


def test_init_sequence_sets_vcom() -> None:
    t = RecordingTransport()
    t.reads.extend([dev_info_words(), [2600]])
    info = IT8951Panel(t, -1.87).init()
    assert (info.width, info.height, info.lut_version, info.fw_version) == (1200, 825, "M841", "SWv_0.2.1T")
    assert info.image_buffer == 0x0011A1E0
    assert t.calls == [
        ("reset", None),
        ("cmd", CMD_SYS_RUN),
        ("cmd", CMD_GET_DEV_INFO),
        ("read", 20),
        ("cmd", CMD_REG_WR),
        ("data", (0x0004, 0x0001)),  # packed write
        ("cmd", CMD_VCOM),
        ("data", (0x0000,)),
        ("read", 1),
        ("cmd", CMD_VCOM),
        ("data", (0x0001, 1870)),
    ]


def test_init_skips_vcom_write_when_equal_and_enhances() -> None:
    t = RecordingTransport()
    t.reads.extend([dev_info_words(), [1870]])
    IT8951Panel(t, -1.87, enhance_driving=True).init()
    assert ("data", (0x0001, 1870)) not in t.calls
    assert t.calls[-1] == ("data", (0x0038, 0x0602))


def test_init_rejects_garbage_and_unexpected_size() -> None:
    t = RecordingTransport()
    t.reads.append([0xFFFF] * 20)
    with pytest.raises(PanelError, match="implausible"):
        IT8951Panel(t, -1.5).init()
    t.reads.extend([dev_info_words(), [1500]])
    with pytest.raises(PanelError, match="expected 1872x1404"):
        IT8951Panel(t, -1.5, expected_size=(1872, 1404)).init()


def test_draw_requires_init() -> None:
    with pytest.raises(PanelError, match="initialised"):
        IT8951Panel(RecordingTransport(), -1.5).draw(np.zeros((4, 4), np.uint8), 0, 0, 0, Waveform.GC16)


def test_draw_rotation_90_uses_code_3_and_physical_rect() -> None:
    panel, t = ready_panel()
    region = np.zeros((120, 160), np.uint8)  # h=120, w=160 in logical (portrait) coordinates
    panel.draw(region, 100, 600, 90, Waveform.GC16)
    assert t.calls == [
        ("cmd", CMD_REG_RD),
        ("data", (REG_LUTAFSR,)),
        ("read", 1),
        ("cmd", CMD_REG_WR),
        ("data", (REG_LISAR + 2, 0x0011)),
        ("cmd", CMD_REG_WR),
        ("data", (REG_LISAR, 0xA1E0)),
        ("cmd", CMD_LD_IMG_AREA),
        ("data", ((0 << 8) | (2 << 4) | 3, 100, 600, 160, 120)),
        ("bulk", pack_4bpp(region)),
        ("cmd", CMD_LD_IMG_END),
        ("cmd", CMD_DPY_AREA),
        # clockwise 90: (x, y, w, h) -> (W - y - h, x, h, w)
        ("data", (1200 - 600 - 120, 100, 120, 160, int(Waveform.GC16))),
    ]


def test_wait_idle_polls_and_times_out() -> None:
    panel, t = ready_panel(ready_timeout_s=0.05, poll_interval_s=0.001)
    t.reads.extend([[1], [1], [0]])
    panel.wait_idle()
    assert t.calls.count(("read", 1)) == 3
    t.reads.extend([[1]] * 1000)
    with pytest.raises(PanelError, match="idle"):
        panel.wait_idle()


def test_low_power_wake_close() -> None:
    panel, t = ready_panel()
    panel.low_power("sleep")
    panel.low_power("standby")
    panel.low_power("none")
    panel.wake()
    panel.close()
    assert t.calls == [("cmd", CMD_SLEEP), ("cmd", CMD_STANDBY), ("cmd", CMD_SYS_RUN)]
    assert t.closed
