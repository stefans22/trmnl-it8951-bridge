"""SPI + GPIO transport for the IT8951 (spidev + libgpiod, manual CS; ADR 0003/0004).

Hardware-only; verified by tools/hw_smoketest.py, excluded from unit-test coverage.
"""

from __future__ import annotations

import contextlib
import struct
import time
from pathlib import Path
from typing import Any

from .base import PanelError

PRE_CMD = 0x6000
PRE_WRITE = 0x0000
PRE_READ = 0x1000


class _Gpio:
    """Minimal libgpiod wrapper for the v2 and (host-installed) v1 Python bindings."""

    def __init__(self, chip: Path, outputs: list[int], inputs: list[int]) -> None:
        import gpiod

        consumer = "trmnl-it8951-bridge"
        self._v2 = hasattr(gpiod, "request_lines")
        if self._v2:
            from gpiod.line import Direction, Value

            self._val = Value
            config: dict[Any, Any] = {
                tuple(outputs): gpiod.LineSettings(direction=Direction.OUTPUT, output_value=Value.ACTIVE),
                tuple(inputs): gpiod.LineSettings(direction=Direction.INPUT),
            }
            self._req = gpiod.request_lines(str(chip), consumer=consumer, config=config)
        else:
            self._chip = gpiod.Chip(str(chip))
            self._lines: dict[int, Any] = {}
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


class SpiGpioTransport:
    def __init__(
        self,
        spi_device: Path,
        speed_hz: int,
        gpio_chip: Path,
        *,
        pin_rst: int,
        pin_hrdy: int,
        pin_cs: int,
        ready_timeout_s: float,
    ) -> None:
        import spidev

        self._rst, self._hrdy, self._cs = pin_rst, pin_hrdy, pin_cs
        self._timeout = ready_timeout_s
        self._gpio = _Gpio(gpio_chip, outputs=[pin_rst, pin_cs], inputs=[pin_hrdy])
        try:
            bus, dev = (int(p) for p in spi_device.name.removeprefix("spidev").split("."))
            self._spi = spidev.SpiDev()
            self._spi.open(bus, dev)
            self._spi.mode = 0
            self._spi.max_speed_hz = speed_hz
            with contextlib.suppress(OSError, AttributeError):
                self._spi.no_cs = True
        except Exception:
            self._gpio.close()
            raise

    def _wait_ready(self) -> None:
        if self._gpio.get(self._hrdy):
            return
        deadline = time.monotonic() + self._timeout
        while not self._gpio.get(self._hrdy):
            if time.monotonic() > deadline:
                raise PanelError(f"HRDY (GPIO {self._hrdy}) stayed low for {self._timeout} s")
            time.sleep(0.0002)

    def _transaction(self, preamble: int, payload: bytes) -> None:
        self._wait_ready()
        self._gpio.set(self._cs, False)
        try:
            self._spi.writebytes2(struct.pack(">H", preamble))
            self._wait_ready()
            self._spi.writebytes2(payload)
        finally:
            self._gpio.set(self._cs, True)

    def command(self, cmd: int) -> None:
        self._transaction(PRE_CMD, struct.pack(">H", cmd))

    def data(self, *words: int) -> None:
        for w in words:
            self._transaction(PRE_WRITE, struct.pack(">H", w))

    def data_bulk(self, payload: bytes) -> None:
        self._transaction(PRE_WRITE, payload)

    def read(self, n_words: int) -> list[int]:
        self._wait_ready()
        self._gpio.set(self._cs, False)
        try:
            self._spi.writebytes2(struct.pack(">H", PRE_READ))
            self._wait_ready()
            self._spi.readbytes(2)  # dummy word
            self._wait_ready()
            raw = bytes(self._spi.readbytes(2 * n_words))
        finally:
            self._gpio.set(self._cs, True)
        return list(struct.unpack(f">{n_words}H", raw))

    def reset(self) -> None:
        self._gpio.set(self._rst, True)
        time.sleep(0.2)
        self._gpio.set(self._rst, False)
        time.sleep(0.01)
        self._gpio.set(self._rst, True)
        time.sleep(0.2)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._spi.close()
        self._gpio.close()
