"""Entry point: load config, build the panel, run the HTTP server."""

from __future__ import annotations

import json
import logging
import sys

import uvicorn
from pydantic import ValidationError

from . import __version__
from .app import create_app
from .config import LogConfig, PanelConfig, Settings, load_settings
from .driver.base import Panel
from .driver.fake import FakePanel
from .driver.it8951 import IT8951Panel
from .runtime import Runtime

log = logging.getLogger("ha_trmnl_ws_display")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def setup_logging(cfg: LogConfig) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if cfg.format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.basicConfig(level=cfg.level, handlers=[handler], force=True)


def build_panel(cfg: PanelConfig) -> Panel:
    if cfg.driver == "fake":
        return FakePanel(cfg.expected_width or 1200, cfg.expected_height or 825)
    from .driver.transport import SpiGpioTransport

    transport = SpiGpioTransport(
        cfg.spi_device,
        cfg.spi_speed_hz,
        cfg.gpio_chip,
        pin_rst=cfg.pin_rst,
        pin_hrdy=cfg.pin_hrdy,
        pin_cs=cfg.pin_cs,
        ready_timeout_s=cfg.ready_timeout_s,
    )
    return IT8951Panel(
        transport,
        cfg.vcom,
        enhance_driving=cfg.enhance_driving,
        ready_timeout_s=cfg.ready_timeout_s,
        expected_size=(cfg.expected_width, cfg.expected_height),
    )


def main() -> int:
    try:
        settings: Settings = load_settings()
    except ValidationError as e:
        print(f"invalid configuration:\n{e}", file=sys.stderr)
        return 2
    setup_logging(settings.log)
    log.info("ha-trmnl-ws-display %s starting", __version__)
    log.info("configuration: %s", json.dumps(settings.redacted()))
    try:
        panel = build_panel(settings.panel)
    except Exception as e:  # noqa: BLE001 - any hardware setup failure ends startup
        log.error("cannot open the panel hardware: %s: %s", type(e).__name__, e)
        return 1
    app = create_app(Runtime(settings, panel))
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_config=None,
        access_log=settings.log.level == "DEBUG",
        timeout_graceful_shutdown=5,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
