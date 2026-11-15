from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from ha_trmnl_ws_display.config import Settings
from ha_trmnl_ws_display.driver.base import Waveform
from ha_trmnl_ws_display.driver.fake import FakePanel
from ha_trmnl_ws_display.mailbox import Command, Mailbox
from ha_trmnl_ws_display.pipeline import to_png
from ha_trmnl_ws_display.state import Persisted, StateStore, Status
from ha_trmnl_ws_display.worker import DisplayWorker

from .conftest import SettingsFactory, png, wait_for

W, H = 240, 160


class Harness:
    def __init__(self, settings: Settings, panel: FakePanel | None = None) -> None:
        self.settings = settings
        self.panel = panel or FakePanel(W, H)
        self.mailbox = Mailbox()
        self.status = Status()
        self.store = StateStore(settings.storage.data_dir, settings.storage.persist)
        self.worker = DisplayWorker(settings, self.panel, self.mailbox, self.store, self.status)

    def start(self) -> None:
        self.worker.start()
        wait_for(lambda: self.status.state in ("idle", "error"))

    def show(self, img: np.ndarray) -> None:
        assert self.worker.pipeline is not None
        frame = self.worker.pipeline.process(png(img), "test")
        self.mailbox.put_frame(frame)
        wait_for(lambda: self.worker.current is not None and self.worker.current.hash == frame.hash)

    def counters(self) -> dict[str, int]:
        return dict(vars(self.status.persisted.counters))


@pytest.fixture
def harness(make_settings: SettingsFactory) -> Iterator[Harness]:
    h = Harness(make_settings())
    yield h
    h.worker.stop()


def white() -> np.ndarray:
    return np.full((H, W), 255, np.uint8)


def test_startup_clears_and_goes_idle(harness: Harness) -> None:
    harness.start()
    assert harness.status.state == "idle"
    (draw,) = harness.panel.draws()
    assert draw.mode is Waveform.INIT
    assert harness.status.panel is not None
    assert harness.status.panel["width"] == W
    assert harness.panel.asleep  # low_power after the operation


def test_full_then_partial_then_duplicate(harness: Harness) -> None:
    harness.start()
    harness.panel.ops.clear()
    img = white()
    img[10:20, 10:20] = 0
    harness.show(img)
    img2 = img.copy()
    img2[100:110, 200:210] = 0
    harness.show(img2)
    draws = harness.panel.draws()
    assert [d.rect for d in draws] == [(0, 0, W, H), (192, 96, 32, 32)]
    assert all(d.mode is Waveform.GC16 for d in draws)
    assert np.array_equal(harness.panel.screen, harness.worker.current.pixels)  # type: ignore[union-attr]
    # same image again: no panel activity
    harness.mailbox.put_frame(harness.worker.pipeline.process(png(img2), "test"))  # type: ignore[union-attr]
    wait_for(lambda: harness.counters()["duplicates"] == 1)
    assert len(harness.panel.draws()) == 2
    assert harness.counters()["full"] == 1
    assert harness.counters()["partial"] == 1
    assert harness.status.persisted.since_full == 1
    assert harness.panel.ops[0].name == "wake"  # woke up from sleep before drawing


def test_cadence_full_refresh(make_settings: SettingsFactory) -> None:
    h = Harness(make_settings(display={"full_refresh_every": 2}))
    h.start()
    try:
        img = white()
        h.show(img)
        kinds = []
        for i in range(4):
            img = img.copy()
            img[0, i * 40] = 0
            h.show(img)
            kinds.append(h.status.last_update.kind)  # type: ignore[union-attr]
        assert kinds == ["partial", "partial", "full", "partial"]
    finally:
        h.worker.stop()


def test_init_then_gc16_waveform(make_settings: SettingsFactory) -> None:
    h = Harness(make_settings(display={"full_refresh_waveform": "init_then_gc16", "clear_on_startup": False}))
    h.start()
    try:
        h.show(white())
        assert [d.mode for d in h.panel.draws()] == [Waveform.INIT, Waveform.GC16]
    finally:
        h.worker.stop()


def test_commands_refresh_and_clear(harness: Harness) -> None:
    harness.start()
    harness.mailbox.put_command(Command.REFRESH_FULL)  # nothing shown -> clear
    wait_for(lambda: len(harness.panel.draws()) == 2)
    img = white()
    img[0:5, 0:5] = 0
    harness.show(img)
    harness.mailbox.put_command(Command.REFRESH_FULL)
    wait_for(lambda: harness.counters()["full"] == 2)
    assert harness.status.last_update.reason == "forced"  # type: ignore[union-attr]
    harness.mailbox.put_command(Command.CLEAR)
    wait_for(lambda: harness.worker.current is None)
    assert (harness.panel.screen == 15).all()
    assert not (harness.store.dir / StateStore.IMAGE).exists()


def test_recovers_after_one_panel_error(harness: Harness) -> None:
    harness.start()
    harness.panel.fail_next = 1
    harness.show(white())
    assert harness.counters()["panel_errors"] == 1
    assert harness.status.state == "idle"
    assert harness.status.last_error is not None


def test_error_state_then_recovery_on_next_frame(harness: Harness) -> None:
    harness.start()
    assert harness.worker.pipeline is not None
    harness.panel.fail_next = 2  # draw fails, re-init fails
    harness.mailbox.put_frame(harness.worker.pipeline.process(png(white()), "test"))
    wait_for(lambda: harness.status.state == "error")
    assert harness.worker.current is None
    harness.show(white())  # next frame: re-init succeeds
    assert harness.status.state == "idle"


def test_startup_failure_then_retry(make_settings: SettingsFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ha_trmnl_ws_display.worker.RETRY_INTERVAL_S", 0.01)
    panel = FakePanel(W, H)
    panel.fail_next = 2
    h = Harness(make_settings(), panel)
    h.worker.start()
    try:
        wait_for(lambda: h.status.state == "error")
        wait_for(lambda: h.worker.pipeline is not None)  # periodic retry re-initialised the panel
        wait_for(lambda: h.status.state == "idle")
        h.show(white())
        assert h.status.state == "idle"
    finally:
        h.worker.stop()


def test_shutdown_clears_sleeps_and_closes(harness: Harness) -> None:
    harness.start()
    img = white()
    img[:, :50] = 0
    harness.show(img)
    harness.worker.stop()
    assert not harness.worker.alive
    assert (harness.panel.screen == 15).all()
    assert harness.panel.closed
    assert harness.status.state == "stopped"
    # the image is still persisted for the next start
    assert (harness.store.dir / StateStore.IMAGE).exists()


def test_shutdown_without_clear(make_settings: SettingsFactory) -> None:
    h = Harness(make_settings(display={"clear_on_shutdown": False}))
    h.start()
    img = white()
    img[:, :50] = 0
    h.show(img)
    h.worker.stop()
    assert (h.panel.screen[:, :50] == 0).all()


def test_restores_last_frame_and_counter(make_settings: SettingsFactory, tmp_path: Path) -> None:
    settings = make_settings()
    img = white()
    img[50:60, 50:60] = 0
    preview = (img // 17).astype(np.uint8)
    store = StateStore(settings.storage.data_dir, True)
    store.save(Persisted(since_full=7), to_png(preview))
    h = Harness(settings)
    h.start()
    try:
        assert h.worker.current is not None
        assert h.worker.current.source == "restore"
        assert (h.panel.screen[50:60, 50:60] == 0).all()
        assert h.status.persisted.since_full == 0  # restore is a full refresh
        assert h.status.last_update.reason == "forced"  # type: ignore[union-attr]
    finally:
        h.worker.stop()


def test_restore_ignores_bad_image(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    StateStore(settings.storage.data_dir, True).save(Persisted(), b"not a png")
    h = Harness(settings)
    h.start()
    try:
        assert h.worker.current is None
        assert h.status.state == "idle"
    finally:
        h.worker.stop()


def test_portrait_end_to_end(make_settings: SettingsFactory) -> None:
    h = Harness(make_settings(display={"rotation": 270}))
    h.start()
    try:
        img = np.full((W, H), 255, np.uint8)  # logical portrait 160x240
        img[0:20, 0:40] = 0
        h.show(img)
        img2 = img.copy()
        img2[200:230, 100:150] = 0
        h.show(img2)
        assert h.status.last_update.kind == "partial"  # type: ignore[union-attr]
        assert np.array_equal(h.panel.logical_screen(270), (img2 // 17).astype(np.uint8))
    finally:
        h.worker.stop()


def test_low_power_none(make_settings: SettingsFactory) -> None:
    h = Harness(make_settings(panel={"low_power": "none"}))
    h.start()
    try:
        assert not h.panel.asleep
        assert not any(op.name == "low_power" for op in h.panel.ops)
    finally:
        h.worker.stop()
