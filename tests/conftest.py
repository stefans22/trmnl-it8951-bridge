from __future__ import annotations

import io
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from trmnl_it8951_bridge.config import Settings, load_settings
from trmnl_it8951_bridge.driver.base import Pixels


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No EPD_* variables from the developer's shell; no config file unless a test writes one."""
    for key in list(os.environ):
        if key.upper().startswith("EPD_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("EPD_CONFIG_FILE", str(tmp_path / "missing.yaml"))


SettingsFactory = Callable[..., Settings]


@pytest.fixture
def make_settings(tmp_path: Path) -> SettingsFactory:
    def factory(**sections: dict[str, Any]) -> Settings:
        panel = {"vcom": -1.5, "driver": "fake", **sections.pop("panel", {})}
        storage = {"data_dir": str(tmp_path / "data"), **sections.pop("storage", {})}
        return load_settings(panel=panel, storage=storage, **sections)

    return factory


def png(pixels: Pixels | np.ndarray, mode: str = "L") -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.asarray(pixels, dtype=np.uint8), mode=mode).save(buf, format="PNG")
    return buf.getvalue()


def gray_image(width: int = 1200, height: int = 825, value: int = 255) -> np.ndarray:
    return np.full((height, width), value, dtype=np.uint8)


def wait_for(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.005)
