#!/usr/bin/env python3
"""Hardware smoke test for the Waveshare IT8951 e-paper HAT on a Raspberry Pi.

Standalone on purpose: it needs only the Python standard library plus `spidev`
and `gpiod` (libgpiod v1 or v2 bindings), so it can run directly on the Pi host
before any container exists.

It verifies the open hardware assumptions of the design:

* ADR 0004 - SPI via spidev with chip select driven manually on GPIO 8
  (`dtoverlay=spi0-0cs`).
* 4bpp pixel packing (nibble/byte order) - gray bars and fine diagonals.
* ADR 0005 - partial (area) refresh with GC16.
* ADR 0008 - rotation done by the controller (LD_IMG_AREA rotate flag) and the
  logical -> physical rectangle mapping used for DPY_AREA.
* Throughput (NFR-1).

Usage (on the Pi):

    sudo apt install python3-spidev python3-libgpiod
    python3 tools/hw_smoketest.py --vcom -1.50

Without hardware (renders the simulated panel to PGM files):

    python3 tools/hw_smoketest.py --vcom -1.50 --dry-run out/

See docs/hardware-smoke-test.md for details.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import signal
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# --------------------------------------------------------------------------- #
# IT8951 protocol constants (from the IT8951 datasheet / Waveshare reference)
# --------------------------------------------------------------------------- #

PRE_CMD = 0x6000
PRE_WRITE = 0x0000
PRE_READ = 0x1000

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

REG_I80CPCR = 0x0004  # packed write enable
REG_LISAR = 0x0208  # load image start address (low word; +2 = high word)
REG_LUTAFSR = 0x1224  # LUT engine status, 0 = idle
REG_DRIVE = 0x0038  # "enhance driving capability"

MODE_INIT = 0
MODE_GC16 = 2

BPP_4 = 2
ENDIAN_LITTLE = 0

BLACK = 0
WHITE = 15

ROTATIONS = (0, 90, 180, 270)


# --------------------------------------------------------------------------- #
# Low-level access: GPIO (libgpiod v1 + v2) and SPI
# --------------------------------------------------------------------------- #


class Gpio:
    """Minimal libgpiod wrapper supporting both the v1 and v2 Python bindings."""

    def __init__(self, chip: str, outputs: list[int], inputs: list[int]) -> None:
        import gpiod  # type: ignore[import-not-found]

        self._v2 = hasattr(gpiod, "request_lines")
        consumer = "hw_smoketest"
        if self._v2:
            from gpiod.line import Direction, Value  # type: ignore[import-not-found]

            self._val = Value
            config: dict[Any, Any] = {
                tuple(outputs): gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.ACTIVE
                ),
                tuple(inputs): gpiod.LineSettings(direction=Direction.INPUT),
            }
            self._req = gpiod.request_lines(chip, consumer=consumer, config=config)
        else:
            self._chip = gpiod.Chip(chip)
            self._lines = {}
            for pin in outputs:
                line = self._chip.get_line(pin)
                line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_OUT, default_val=1)
                self._lines[pin] = line
            for pin in inputs:
                line = self._chip.get_line(pin)
                line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_IN)
                self._lines[pin] = line

    def set(self, pin: int, high: bool) -> None:
        if self._v2:
            self._req.set_value(pin, self._val.ACTIVE if high else self._val.INACTIVE)
        else:
            self._lines[pin].set_value(1 if high else 0)

    def get(self, pin: int) -> bool:
        if self._v2:
            return bool(self._req.get_value(pin) == self._val.ACTIVE)
        return bool(self._lines[pin].get_value())

    def close(self) -> None:
        if self._v2:
            self._req.release()
        else:
            for line in self._lines.values():
                line.release()
            self._chip.close()


class Spi:
    def __init__(self, device: str, speed_hz: int) -> None:
        import spidev  # type: ignore[import-not-found]

        bus, dev = (int(p) for p in Path(device).name.removeprefix("spidev").split("."))
        self._spi = spidev.SpiDev()
        self._spi.open(bus, dev)
        self._spi.mode = 0
        self._spi.max_speed_hz = speed_hz
        # CS is driven manually via GPIO (ADR 0004); not every driver allows this flag.
        with contextlib.suppress(OSError, AttributeError):
            self._spi.no_cs = True

    def write(self, data: bytes) -> None:
        # writebytes2 splits into chunks of the kernel's spidev.bufsiz itself.
        self._spi.writebytes2(data)

    def read(self, n: int) -> bytes:
        return bytes(self._spi.readbytes(n))

    def close(self) -> None:
        self._spi.close()


class Transport:
    """IT8951 host interface over SPI: preamble + HRDY handshake + manual CS."""

    def __init__(
        self, spi: Spi, gpio: Gpio, rst: int, hrdy: int, cs: int, timeout_s: float
    ) -> None:
        self.spi, self.gpio = spi, gpio
        self.rst, self.hrdy, self.cs = rst, hrdy, cs
        self.timeout_s = timeout_s

    def wait_ready(self) -> None:
        if self.gpio.get(self.hrdy):
            return
        deadline = time.monotonic() + self.timeout_s
        while not self.gpio.get(self.hrdy):
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"HRDY (GPIO {self.hrdy}) stayed low for {self.timeout_s}s - "
                    "check wiring, the HAT's SPI/I80 switch and power"
                )
            time.sleep(0.0002)

    def _transaction(self, preamble: int, payload: bytes) -> None:
        self.wait_ready()
        self.gpio.set(self.cs, False)
        try:
            self.spi.write(struct.pack(">H", preamble))
            self.wait_ready()
            self.spi.write(payload)
        finally:
            self.gpio.set(self.cs, True)

    def command(self, cmd: int) -> None:
        self._transaction(PRE_CMD, struct.pack(">H", cmd))

    def data(self, *words: int) -> None:
        # The reference sends every argument word as its own transaction.
        for w in words:
            self._transaction(PRE_WRITE, struct.pack(">H", w))

    def data_bulk(self, payload: bytes) -> None:
        self._transaction(PRE_WRITE, payload)

    def read(self, n_words: int) -> list[int]:
        self.wait_ready()
        self.gpio.set(self.cs, False)
        try:
            self.spi.write(struct.pack(">H", PRE_READ))
            self.wait_ready()
            self.spi.read(2)  # dummy word
            self.wait_ready()
            raw = self.spi.read(2 * n_words)
        finally:
            self.gpio.set(self.cs, True)
        return list(struct.unpack(f">{n_words}H", raw))

    def reset(self) -> None:
        self.gpio.set(self.rst, True)
        time.sleep(0.2)
        self.gpio.set(self.rst, False)
        time.sleep(0.01)
        self.gpio.set(self.rst, True)
        time.sleep(0.2)


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #


@dataclass
class DevInfo:
    width: int
    height: int
    img_addr: int
    fw: str
    lut: str


def _words_to_str(words: list[int]) -> str:
    # The C reference stores the read words in a little-endian uint16 array and
    # reads them as a char string: low byte first.
    raw = b"".join(struct.pack("<H", w) for w in words)
    return raw.split(b"\0", 1)[0].decode("ascii", "replace")


class Controller(Protocol):
    def init(self, vcom_mv: int, enhance: bool) -> tuple[DevInfo, int, int]: ...
    def load_area(self, rotate: int, x: int, y: int, w: int, h: int, packed: bytes) -> None: ...
    def display_area(self, x: int, y: int, w: int, h: int, mode: int) -> None: ...
    def wait_display(self) -> None: ...
    def sleep(self) -> None: ...
    def close(self) -> None: ...


class IT8951:
    def __init__(self, t: Transport, display_timeout_s: float) -> None:
        self.t = t
        self.display_timeout_s = display_timeout_s
        self.info: DevInfo | None = None

    def reg_read(self, reg: int) -> int:
        self.t.command(CMD_REG_RD)
        self.t.data(reg)
        return self.t.read(1)[0]

    def reg_write(self, reg: int, value: int) -> None:
        self.t.command(CMD_REG_WR)
        self.t.data(reg, value)

    def init(self, vcom_mv: int, enhance: bool) -> tuple[DevInfo, int, int]:
        """Reset, read device info, set VCOM. Returns (info, vcom_before, vcom_after)."""
        self.t.reset()
        self.t.command(CMD_SYS_RUN)
        self.t.command(CMD_GET_DEV_INFO)
        w = self.t.read(20)
        self.info = DevInfo(
            width=w[0],
            height=w[1],
            img_addr=w[2] | (w[3] << 16),
            fw=_words_to_str(w[4:12]),
            lut=_words_to_str(w[12:20]),
        )
        self.reg_write(REG_I80CPCR, 0x0001)
        self.t.command(CMD_VCOM)
        self.t.data(0x0000)
        before = self.t.read(1)[0]
        if before != vcom_mv:
            self.t.command(CMD_VCOM)
            self.t.data(0x0001, vcom_mv)
        self.t.command(CMD_VCOM)
        self.t.data(0x0000)
        after = self.t.read(1)[0]
        if enhance:
            self.reg_write(REG_DRIVE, 0x0602)
        return self.info, before, after

    def wait_display(self) -> None:
        deadline = time.monotonic() + self.display_timeout_s
        while self.reg_read(REG_LUTAFSR) != 0:
            if time.monotonic() > deadline:
                raise TimeoutError("display engine did not become idle (LUTAFSR != 0)")
            time.sleep(0.005)

    def load_area(self, rotate: int, x: int, y: int, w: int, h: int, packed: bytes) -> None:
        assert self.info is not None
        self.wait_display()
        self.reg_write(REG_LISAR + 2, (self.info.img_addr >> 16) & 0xFFFF)
        self.reg_write(REG_LISAR, self.info.img_addr & 0xFFFF)
        self.t.command(CMD_LD_IMG_AREA)
        self.t.data((ENDIAN_LITTLE << 8) | (BPP_4 << 4) | rotate, x, y, w, h)
        self.t.data_bulk(packed)
        self.t.command(CMD_LD_IMG_END)

    def display_area(self, x: int, y: int, w: int, h: int, mode: int) -> None:
        self.t.command(CMD_DPY_AREA)
        self.t.data(x, y, w, h, mode)

    def sleep(self) -> None:
        self.t.command(CMD_SLEEP)

    def close(self) -> None:
        self.t.spi.close()
        self.t.gpio.close()


# --------------------------------------------------------------------------- #
# Images: a tiny 4-bit canvas, packing and the rotation mapping under test
# --------------------------------------------------------------------------- #


class Canvas:
    """Row-major image with one byte (0..15) per pixel."""

    def __init__(self, w: int, h: int, fill: int = WHITE) -> None:
        self.w, self.h = w, h
        self.buf = bytearray([fill]) * (w * h)

    def rect(self, x: int, y: int, w: int, h: int, v: int) -> None:
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.w, x + w), min(self.h, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes([v]) * (x1 - x0)
        for yy in range(y0, y1):
            off = yy * self.w
            self.buf[off + x0 : off + x1] = row

    def frame(self, v: int, t: int = 1) -> None:
        self.rect(0, 0, self.w, t, v)
        self.rect(0, self.h - t, self.w, t, v)
        self.rect(0, 0, t, self.h, v)
        self.rect(self.w - t, 0, t, self.h, v)

    def diagonal(self, x: int, y: int, length: int, v: int, thickness: int = 1, step: int = 1) -> None:
        """45 degree line going right and down (step=1) or right and up (step=-1)."""
        for i in range(length):
            self.rect(x + i, y + i * step, thickness, 1, v)

    def crop(self, x: int, y: int, w: int, h: int) -> Canvas:
        out = Canvas(w, h)
        for yy in range(h):
            src = (y + yy) * self.w + x
            out.buf[yy * w : (yy + 1) * w] = self.buf[src : src + w]
        return out

    def row(self, y: int) -> bytes:
        return bytes(self.buf[y * self.w : (y + 1) * self.w])


_SHL4 = bytes((i << 4) & 0xFF for i in range(256))


def pack_row(row: bytes) -> bytes:
    """Pack 4 pixels (p0..p3, values 0..15) into one 16-bit word, sent MSB first.

    Reproduces the Waveshare reference: GUI_Paint puts even pixels in the low
    nibble of a byte, the byte buffer is reinterpreted as little-endian uint16
    and sent high byte first. Wire bytes per 4 pixels: [p3<<4|p2, p1<<4|p0].
    """
    assert len(row) % 4 == 0
    n = len(row) // 4
    hi = int.from_bytes(row[3::4].translate(_SHL4), "big") | int.from_bytes(row[2::4], "big")
    lo = int.from_bytes(row[1::4].translate(_SHL4), "big") | int.from_bytes(row[0::4], "big")
    out = bytearray(2 * n)
    out[0::2] = hi.to_bytes(n, "big")
    out[1::2] = lo.to_bytes(n, "big")
    return bytes(out)


def unpack_row(data: bytes, w: int) -> bytes:
    out = bytearray(len(data) * 2)
    out[3::4] = bytes(b >> 4 for b in data[0::2])
    out[2::4] = bytes(b & 0xF for b in data[0::2])
    out[1::4] = bytes(b >> 4 for b in data[1::2])
    out[0::4] = bytes(b & 0xF for b in data[1::2])
    return bytes(out[:w])


def pack(c: Canvas, odd_width: str) -> tuple[int, bytes]:
    """Pack a canvas. Returns (declared width, payload).

    odd_width='pad'  : declare the real width, pad each row with white to whole words.
    odd_width='trunc': declare and send only the width rounded down to 4 px.
    """
    if odd_width == "trunc":
        declared = c.w - c.w % 4
        pad = 0
    else:
        declared = c.w
        pad = (-c.w) % 4
    cache: dict[bytes, bytes] = {}
    parts = []
    for y in range(c.h):
        row = c.row(y)[:declared] + bytes([WHITE]) * pad
        packed = cache.get(row)
        if packed is None:
            packed = cache[row] = pack_row(row)
        parts.append(packed)
    return declared, b"".join(parts)


def logical_size(rotation: int, pw: int, ph: int) -> tuple[int, int]:
    return (ph, pw) if rotation in (90, 270) else (pw, ph)


def controller_code(rotation: int) -> int:
    """IT8951 LD_IMG rotate code for a clockwise rotation.

    Smoke test 2026-11-15 (M841, FW SWv_0.2.1T): code 1 turns the image 90 degrees
    COUNTER-clockwise (bar ended up on the left edge), so clockwise 90 is code 3.
    """
    return ((360 - rotation) % 360) // 90


def to_physical(rect: tuple[int, int, int, int], rotation: int, pw: int, ph: int) -> tuple[int, int, int, int]:
    """Map a rect in logical (image) coordinates to physical panel coordinates.

    `rotation` is the clockwise rotation of the image onto the panel (config
    semantics). The controller code to send for it is `controller_code()`.
    """
    x, y, w, h = rect
    if rotation == 0:
        return x, y, w, h
    if rotation == 90:
        return pw - y - h, x, h, w
    if rotation == 180:
        return pw - x - w, ph - y - h, w, h
    if rotation == 270:
        return y, ph - x - w, h, w
    raise ValueError(rotation)


# --------------------------------------------------------------------------- #
# Dry-run controller: simulates panel memory and writes the screen to PGM files
# --------------------------------------------------------------------------- #


class FakeIT8951:
    def __init__(self, out_dir: Path, width: int = 1200, height: int = 825) -> None:
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.info = DevInfo(width, height, 0x0011_9F00, "dry-run", "M841")
        self.mem = Canvas(width, height)
        self.screen = Canvas(width, height)
        self.n = 0
        self.label = "start"

    def init(self, vcom_mv: int, enhance: bool) -> tuple[DevInfo, int, int]:
        return self.info, 2000, vcom_mv

    def load_area(self, rotate: int, x: int, y: int, w: int, h: int, packed: bytes) -> None:
        pw, ph = self.info.width, self.info.height
        stride = 2 * ((w + 3) // 4)
        assert len(packed) == stride * h, (len(packed), stride, h)
        rot_cw = (360 - rotate * 90) % 360
        lw, lh = logical_size(rot_cw, pw, ph)
        for yy in range(h):
            row = unpack_row(packed[yy * stride : (yy + 1) * stride], w)
            if rotate == 0:  # fast path
                x1 = min(pw, x + w)
                if 0 <= y + yy < ph and x < x1:
                    off = (y + yy) * pw
                    self.mem.buf[off + x : off + x1] = row[: x1 - x]
                continue
            for xx, v in enumerate(row):
                lx, ly = x + xx, y + yy
                if not (0 <= lx < lw and 0 <= ly < lh):
                    continue
                px, py, _, _ = to_physical((lx, ly, 1, 1), rot_cw, pw, ph)
                self.mem.buf[py * pw + px] = v

    def display_area(self, x: int, y: int, w: int, h: int, mode: int) -> None:
        part = self.mem.crop(x, y, w, h)
        for yy in range(h):
            off = (y + yy) * self.screen.w + x
            self.screen.buf[off : off + w] = part.row(yy)
        self.n += 1
        name = self.out / f"{self.n:02d}-{self.label}-mode{mode}.pgm"
        header = f"P5 {self.screen.w} {self.screen.h} 255\n".encode()
        name.write_bytes(header + self.screen.buf.translate(bytes(min(255, i * 17) for i in range(256))))

    def wait_display(self) -> None:
        pass

    def sleep(self) -> None:
        pass

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# Test steps
# --------------------------------------------------------------------------- #


@dataclass
class Result:
    step: str
    check: str
    outcome: str  # pass | fail | skip | info | unverified
    note: str = ""


@dataclass
class Ctx:
    args: argparse.Namespace
    ctl: Controller | None = None
    info: DevInfo | None = None
    results: list[Result] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    def add(self, step: str, check: str, outcome: str, note: str = "") -> None:
        self.results.append(Result(step, check, outcome, note))
        mark = {"pass": "OK  ", "fail": "FAIL", "skip": "SKIP", "info": "INFO", "unverified": "??  "}[outcome]
        print(f"  [{mark}] {check}" + (f" - {note}" if note else ""))

    def ask(self, step: str, check: str, choices: tuple[str, ...] = ("y", "n")) -> str:
        """Ask the operator to judge the panel. Returns the chosen answer."""
        if self.args.yes or self.args.dry_run:
            self.add(step, check, "unverified", "not judged (--yes/--dry-run)")
            return "?"
        prompt = f"  ? {check} [{'/'.join(choices)}/s=skip] "
        while True:
            ans = input(prompt).strip().lower()
            if ans in choices or ans == "s":
                break
        if ans == "s":
            self.add(step, check, "skip")
        elif choices == ("y", "n"):
            self.add(step, check, "pass" if ans == "y" else "fail")
        else:
            self.add(step, check, "info", f"answer: {ans}")
        if ans == "n":
            note = input("    what did you see? ").strip()
            if note:
                self.results[-1].note = note
        return ans

    def label(self, text: str) -> None:
        if isinstance(self.ctl, FakeIT8951):
            self.ctl.label = text


def timed(ctx: Ctx, key: str) -> contextlib.AbstractContextManager[None]:
    @contextlib.contextmanager
    def _cm() -> Any:
        t0 = time.perf_counter()
        yield
        ctx.timings[key] = time.perf_counter() - t0

    return _cm()


def show(ctx: Ctx, c: Canvas, mode: int, label: str, rotate: int = 0, x: int = 0, y: int = 0) -> None:
    """Upload a canvas at logical (x, y) with the given rotation and display it."""
    assert ctx.ctl and ctx.info
    ctx.label(label)
    declared, payload = pack(c, ctx.args.odd_width)
    ctx.ctl.load_area(controller_code(rotate), x, y, declared, c.h, payload)
    px, py, pw, ph = to_physical((x, y, declared, c.h), rotate, ctx.info.width, ctx.info.height)
    ctx.ctl.display_area(px, py, pw, ph, mode)
    ctx.ctl.wait_display()


def full_clear(ctx: Ctx, mode: int = MODE_INIT, label: str = "clear") -> None:
    assert ctx.info
    show(ctx, Canvas(ctx.info.width, ctx.info.height), mode, label)


def step_env(ctx: Ctx) -> bool:
    a = ctx.args
    print("\n== env: host prerequisites")
    if a.dry_run:
        ctx.add("env", "host checks", "skip", "dry run")
        return True
    model = Path("/proc/device-tree/model")
    ctx.add("env", "board", "info", model.read_text().strip("\0\n") if model.exists() else "unknown")
    ctx.add("env", "kernel / arch", "info", f"{platform.release()} / {platform.machine()}")
    for cfg in (Path("/boot/firmware/config.txt"), Path("/boot/config.txt")):
        if cfg.exists():
            lines = [ln.strip() for ln in cfg.read_text().splitlines() if not ln.strip().startswith("#")]
            has_0cs = any(ln.startswith("dtoverlay=spi0-0cs") for ln in lines)
            spi_on = any(ln.startswith("dtparam=spi=on") for ln in lines)
            ctx.add("env", f"{cfg}: dtoverlay=spi0-0cs active", "pass" if has_0cs else "fail",
                    "" if has_0cs else "add it, comment out dtparam=spi=on, reboot (see README)")
            if spi_on:
                ctx.add("env", "dtparam=spi=on is active", "fail" if not has_0cs else "info",
                        "claims GPIO 8 as hardware CS, so the script cannot drive it" if not has_0cs
                        else "Waveshare recommends commenting it out")
            config_ok = has_0cs
            break
    else:
        config_ok = True  # unknown distro layout: let the device checks decide
    ok = config_ok
    for dev in (a.spi, a.gpiochip):
        exists = Path(dev).exists()
        rw = exists and os.access(dev, os.R_OK | os.W_OK)
        ctx.add("env", f"{dev} exists and is read/writable", "pass" if rw else "fail",
                "" if rw else ("missing" if not exists else "no permission (group spi/gpio?)"))
        ok &= rw
    for mod in ("spidev", "gpiod"):
        try:
            m = __import__(mod)
            ctx.add("env", f"python module {mod}", "pass", getattr(m, "__version__", ""))
        except ImportError as e:
            ctx.add("env", f"python module {mod}", "fail", str(e))
            ok = False
    return ok


def step_info(ctx: Ctx) -> bool:
    a = ctx.args
    print("\n== info: reset, device info, VCOM (proves SPI + manual CS + HRDY, ADR 0004)")
    assert ctx.ctl
    vcom_mv = round(abs(a.vcom) * 1000)
    try:
        with timed(ctx, "init_s"):
            info, before, after = ctx.ctl.init(vcom_mv, a.enhance)
    except TimeoutError as e:
        ctx.add("info", "controller responds", "fail", str(e))
        return False
    ctx.info = info
    plausible = 0 < info.width <= 4096 and 0 < info.height <= 4096 and info.lut.isprintable() and info.lut
    ctx.add("info", "device info plausible", "pass" if plausible else "fail",
            f"{info.width}x{info.height}, image buffer 0x{info.img_addr:08X}, FW '{info.fw}', LUT '{info.lut}'")
    if not plausible:
        print("    Garbage device info usually means SPI data is corrupted: try --speed 2000000,")
        print("    check that the HAT is set to SPI and that CS really is GPIO 8.")
        return False
    if info.lut != "M841" or (info.width, info.height) != (1200, 825):
        ctx.add("info", "is the 9.7\" reference panel (M841, 1200x825)", "info", "different panel - results may differ")
    ctx.add("info", "VCOM set", "pass" if after == vcom_mv else "fail",
            f"before -{before / 1000:.2f} V, wanted -{vcom_mv / 1000:.2f} V, now -{after / 1000:.2f} V")
    ctx.add("info", "init duration", "info", f"{ctx.timings['init_s']:.2f} s")
    return after == vcom_mv


def step_clear(ctx: Ctx) -> None:
    print("\n== clear: full white with INIT waveform")
    with timed(ctx, "clear_init_s"):
        full_clear(ctx)
    ctx.add("clear", "INIT clear duration", "info", f"{ctx.timings['clear_init_s']:.2f} s")
    ctx.ask("clear", "Panel flashed and is now uniformly white")


def gray_bars(w: int, h: int) -> Canvas:
    c = Canvas(w, h)
    for i in range(16):
        x0, x1 = i * w // 16, (i + 1) * w // 16
        c.rect(x0, 0, x1 - x0, h, i)
    return c


def step_gray(ctx: Ctx) -> None:
    assert ctx.info
    print("\n== gray: 16 gray levels (packing, GC16)")
    w, h = ctx.info.width, ctx.info.height
    c = gray_bars(w, h)
    with timed(ctx, "full_gc16_s"):
        show(ctx, c, MODE_GC16, "gray-bars")
    ctx.add("gray", "full GC16 update duration", "info", f"{ctx.timings['full_gc16_s']:.2f} s")
    ctx.ask("gray", "16 vertical bars, black on the LEFT to white on the RIGHT, clean edges, no noise")


def detail_pattern(w: int, h: int) -> Canvas:
    c = Canvas(w, h)
    c.frame(BLACK, 1)
    # 1 px diagonals: any pixel-order error inside a 4 px word turns them into zig-zags.
    for i in range(6):
        c.diagonal(40 + i * 60, 40, min(h - 80, 400), BLACK, thickness=1 + i % 2)
        c.diagonal(40 + i * 60, h - 40, min(h - 80, 400), BLACK, thickness=1 + i % 2, step=-1)
    # Single pixels at x = 0..15 relative to a word boundary, every 4th row.
    bx, by = w - 200, 40
    for i in range(16):
        c.rect(bx + i * 5, by + i * 4, 1, 1, BLACK)
    # Groups of 4 adjacent 1 px lines (one 16-bit word) with rising gray levels:
    # each group must look like a small black->light gradient stripe.
    for i in range(4):
        for k in range(8):
            c.rect(bx + i + k * 20, by + 100, 1, 60, i * 4)
    return c


def step_pattern(ctx: Ctx) -> None:
    assert ctx.info
    print("\n== pattern: fine 1-px details (pixel order inside a 4-px word)")
    show(ctx, detail_pattern(ctx.info.width, ctx.info.height), MODE_GC16, "pattern")
    ctx.ask("pattern", "1-px frame visible on all four edges")
    ctx.ask("pattern", "Diagonal lines are smooth and continuous (no zig-zag, gaps or shifted segments)")
    ctx.ask("pattern", "Top right, 8 thin stripes: each fades from BLACK on its left edge to light gray on its "
                       "right edge (use a magnifier or phone camera zoom)")


def step_partial(ctx: Ctx) -> None:
    assert ctx.info
    print("\n== partial: area updates with GC16 (ADR 0005)")
    w, h = ctx.info.width, ctx.info.height
    base = detail_pattern(w, h)
    show(ctx, base, MODE_GC16, "partial-base")
    rects = [(w // 2 - 96, 64, 192, 128), (w // 2 - 200, h // 2 - 32, 400, 64), (w - 296, h - 196, 256, 128)]
    durations = []
    for n, (x, y, rw, rh) in enumerate(rects, 1):
        tile = Canvas(rw, rh, BLACK) if n != 2 else gray_bars(rw, rh)
        t0 = time.perf_counter()
        show(ctx, tile, MODE_GC16, f"partial-{n}", x=x, y=y)
        durations.append(time.perf_counter() - t0)
    ctx.timings["partial_s"] = max(durations)
    ctx.add("partial", "partial update durations", "info", ", ".join(f"{d:.2f} s" for d in durations))
    ctx.ask("partial", "Three areas changed one after another (black box top, gray strip middle, "
                       "black box bottom right) and the REST of the screen did not flash")
    # And back to white in one area: checks that partial GC16 cleans up.
    x, y, rw, rh = rects[0]
    show(ctx, base.crop(x, y, rw, rh), MODE_GC16, "partial-undo", x=x, y=y)
    ctx.ask("partial", "The top black box disappeared again without visible ghosting")


def marker(lw: int, lh: int, rotation: int) -> Canvas:
    """Asymmetric picture: thick bar at the top, an 'F' at top-left, N squares next to it."""
    c = Canvas(lw, lh)
    c.frame(BLACK, 2)
    c.rect(0, 0, lw, 24, BLACK)  # "this side up"
    s = max(8, min(lw, lh) // 40)  # stroke
    fx, fy, fh = 3 * s, 24 + 3 * s, 12 * s
    c.rect(fx, fy, s, fh, BLACK)  # F stem
    c.rect(fx, fy, 6 * s, s, BLACK)  # F top arm
    c.rect(fx, fy + 5 * s, 4 * s, s, BLACK)  # F middle arm
    for i in range(rotation // 90 + 1):  # 1..4 squares = rotation code
        c.rect(fx + 9 * s + i * 3 * s, fy, 2 * s, 2 * s, BLACK)
    # Arrow pointing right along the bottom: shows the logical x direction.
    cy, head = lh - 6 * s + s // 2, 4 * s
    c.rect(4 * s, lh - 6 * s, lw - 8 * s - head, s, BLACK)
    for i in range(head):  # triangle, base 4s high, narrowing to a 1 px point
        half = (head - 1 - i) * 2 * s // head
        c.rect(lw - 4 * s - head + i, cy - half, 1, 2 * half + 1, BLACK)
    return c


def step_rotate(ctx: Ctx) -> None:
    assert ctx.ctl and ctx.info
    print("\n== rotate: rotation by the controller (ADR 0008)")
    pw, ph = ctx.info.width, ctx.info.height
    print("   Edges below refer to the panel held in its NORMAL LANDSCAPE position "
          "(as in the 'gray' step, black bar on the left).")
    for rot in ctx.args.rotations:
        lw, lh = logical_size(rot, pw, ph)
        print(f"\n -- rotation {rot} (logical image {lw}x{lh}, rotate code {controller_code(rot)})")
        full_clear(ctx, MODE_INIT, f"rot{rot}-clear")
        show(ctx, marker(lw, lh, rot), MODE_GC16, f"rot{rot}-full", rotate=rot)
        step = f"rotate {rot}"
        if lw % 4:
            ctx.add(step, f"logical width {lw} is not a multiple of 4", "info",
                    f"odd-width mode '{ctx.args.odd_width}'")
        ctx.ask(step, "Image is complete and undistorted (not sheared, no stripes)")
        ctx.ask(step, "Which physical edge has the thick black bar", ("top", "right", "bottom", "left"))
        ctx.ask(step, f"Viewed with the bar on top: 'F' reads correctly (not mirrored) at the top-left, "
                      f"with {rot // 90 + 1} square(s) to its right")
        # Partial update in logical coordinates; DPY_AREA uses our clockwise mapping.
        bx, by, bw, bh = lw // 8 - (lw // 8) % 4, lh // 2, 160, 120
        box = Canvas(bw, bh, BLACK)
        box.rect(0, 0, 40, 40, WHITE)  # notch at the box's logical top-left
        show(ctx, box, MODE_GC16, f"rot{rot}-partial", rotate=rot, x=bx, y=by)
        ans = ctx.ask(step, "Viewed upright: a black box appeared fully drawn, left of center, below the 'F', "
                            "with a white notch in its top-left corner")
        if ans == "n":
            print("    Refreshing the whole panel to see where the box was loaded ...")
            ctx.label(f"rot{rot}-reveal")
            ctx.ctl.display_area(0, 0, pw, ph, MODE_GC16)
            ctx.ctl.wait_display()
            ctx.ask(step, "After the full refresh: where is the box (describe in the note)", ("seen", "none"))


def step_timing(ctx: Ctx) -> None:
    assert ctx.ctl and ctx.info
    print("\n== timing: upload vs. waveform")
    pw, ph = ctx.info.width, ctx.info.height
    c = gray_bars(pw, ph)
    t0 = time.perf_counter()
    declared, payload = pack(detail_pattern(pw, ph), "pad")
    t_pack = time.perf_counter() - t0
    declared, payload = pack(c, "pad")
    t0 = time.perf_counter()
    ctx.ctl.load_area(0, 0, 0, declared, ph, payload)
    t_up = time.perf_counter() - t0
    t0 = time.perf_counter()
    ctx.label("timing")
    ctx.ctl.display_area(0, 0, pw, ph, MODE_GC16)
    ctx.ctl.wait_display()
    t_disp = time.perf_counter() - t0
    mbit = len(payload) * 8 / t_up / 1e6 if t_up else 0
    ctx.add("timing", "pack full frame (pure Python, detail pattern)", "info", f"{t_pack:.2f} s")
    ctx.add("timing", "upload full frame", "info",
            f"{len(payload)} B in {t_up:.2f} s = {mbit:.1f} Mbit/s at --speed {ctx.args.speed}")
    ctx.add("timing", "GC16 full waveform", "info", f"{t_disp:.2f} s")


STEPS = {
    "env": step_env,
    "info": step_info,
    "clear": step_clear,
    "gray": step_gray,
    "pattern": step_pattern,
    "partial": step_partial,
    "rotate": step_rotate,
    "timing": step_timing,
}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def write_report(ctx: Ctx, path: Path) -> None:
    a = ctx.args
    lines = [
        "# IT8951 hardware smoke test report",
        "",
        f"- Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Host: {platform.node()} ({platform.release()}, {platform.machine()})",
        f"- Args: vcom={a.vcom} spi={a.spi} speed={a.speed} gpiochip={a.gpiochip} "
        f"rst={a.rst} hrdy={a.hrdy} cs={a.cs} odd_width={a.odd_width} enhance={a.enhance} dry_run={bool(a.dry_run)}",
    ]
    if ctx.info:
        i = ctx.info
        lines.append(f"- Panel: {i.width}x{i.height}, FW '{i.fw}', LUT '{i.lut}', buffer 0x{i.img_addr:08X}")
    lines += ["", "| Step | Check | Result | Note |", "|------|-------|--------|------|"]
    for r in ctx.results:
        note = r.note.replace("|", "\\|")
        lines.append(f"| {r.step} | {r.check} | {r.outcome} | {note} |")
    path.write_text("\n".join(lines) + "\n")
    print(f"\nReport written to {path}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--vcom", type=float, required=True, help="VCOM from the FPC cable, e.g. -1.50")
    p.add_argument("--steps", default=",".join(STEPS), help=f"comma-separated subset of: {','.join(STEPS)}")
    p.add_argument("--rotations", default="0,90,180,270", help="rotations for the 'rotate' step")
    p.add_argument("--odd-width", choices=("pad", "trunc"), default="pad",
                   help="rows whose width is not a multiple of 4: pad to whole words or truncate")
    p.add_argument("--spi", default="/dev/spidev0.0")
    p.add_argument("--speed", type=int, default=12_000_000, help="SPI clock in Hz")
    p.add_argument("--gpiochip", default="/dev/gpiochip0")
    p.add_argument("--rst", type=int, default=17)
    p.add_argument("--hrdy", type=int, default=24)
    p.add_argument("--cs", type=int, default=8)
    p.add_argument("--timeout", type=float, default=10.0, help="HRDY / display-idle timeout in seconds")
    p.add_argument("--enhance", action="store_true", help="enable 'enhance driving capability'")
    p.add_argument("--keep-image", action="store_true", help="do not clear the panel at the end")
    p.add_argument("--yes", action="store_true", help="do not ask; visual checks are reported as unverified")
    p.add_argument("--report", type=Path, default=Path("smoketest-report.md"))
    p.add_argument("--dry-run", type=Path, metavar="DIR", help="no hardware: write simulated screens to DIR")
    a = p.parse_args(argv)
    if a.vcom >= 0 or a.vcom < -5:
        p.error("--vcom must be negative volts as printed on the cable, e.g. -1.50")
    a.steps = [s.strip() for s in a.steps.split(",") if s.strip()]
    if unknown := set(a.steps) - set(STEPS):
        p.error(f"unknown steps: {', '.join(sorted(unknown))}")
    a.rotations = [int(r) for r in a.rotations.split(",")]
    if bad := set(a.rotations) - set(ROTATIONS):
        p.error(f"invalid rotations: {bad}")
    return a


def main(argv: list[str] | None = None) -> int:
    a = parse_args(argv)
    ctx = Ctx(a)
    def _on_sigterm(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_sigterm)

    steps = list(a.steps)
    if "env" in steps:
        steps.remove("env")
        if not step_env(ctx):
            write_report(ctx, a.report)
            return 1

    if a.dry_run:
        ctx.ctl = FakeIT8951(a.dry_run)
    else:
        try:
            gpio = Gpio(a.gpiochip, outputs=[a.rst, a.cs], inputs=[a.hrdy])
            spi = Spi(a.spi, a.speed)
        except Exception as e:  # noqa: BLE001 - report any setup failure
            hint = ""
            if isinstance(e, OSError) and e.errno == 16:
                hint = (f" - a GPIO line ({a.rst}/{a.hrdy}/{a.cs}) is in use; usually GPIO {a.cs} claimed as SPI CS0 "
                        "because dtoverlay=spi0-0cs is missing (check: gpioinfo gpiochip0)")
            ctx.add("setup", "open SPI and GPIO", "fail", f"{type(e).__name__}: {e}{hint}")
            write_report(ctx, a.report)
            return 1
        ctx.ctl = IT8951(Transport(spi, gpio, a.rst, a.hrdy, a.cs, a.timeout), a.timeout)

    rc = 0
    try:
        if not step_info(ctx):
            rc = 1
        else:
            for name in steps:
                if name != "info":
                    STEPS[name](ctx)
    except KeyboardInterrupt:
        print("\ninterrupted")
        ctx.add("run", "completed", "skip", "interrupted by user")
        rc = 130
    except Exception as e:  # noqa: BLE001 - keep the report on any failure
        ctx.add("run", "completed", "fail", f"{type(e).__name__}: {e}")
        rc = 1
    finally:
        if ctx.info:
            with contextlib.suppress(Exception):
                if not a.keep_image:
                    print("\nClearing the panel (INIT) ...")
                    full_clear(ctx, MODE_INIT, "final-clear")
                ctx.ctl.sleep()
        ctx.ctl.close()
        write_report(ctx, a.report)
    return rc


if __name__ == "__main__":
    sys.exit(main())
