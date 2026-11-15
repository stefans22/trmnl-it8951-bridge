"""Display worker: the only code that touches the panel (design §3.5, ADR 0006)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import numpy as np

from .config import Settings
from .driver.base import WHITE, Panel, PanelError, Waveform
from .mailbox import Command, Mailbox
from .pipeline import Frame, ImageRejectedError, Pipeline, to_png
from .policy import Plan, plan_update
from .state import CurrentFrame, LastUpdate, StateStore, Status, WorkerState, now_iso

log = logging.getLogger(__name__)

RETRY_INTERVAL_S = 30.0
"""How often to retry initialising the panel while in the error state."""

PANEL_ERRORS = (PanelError, OSError, TimeoutError)


class DisplayWorker:
    def __init__(
        self,
        settings: Settings,
        panel: Panel,
        mailbox: Mailbox,
        store: StateStore,
        status: Status,
    ) -> None:
        self.settings = settings
        self.cfg = settings.display
        self.panel = panel
        self.mailbox = mailbox
        self.store = store
        self.status = status
        self.pipeline: Pipeline | None = None
        self.current: Frame | None = None
        self._ready = False
        self._asleep = False
        self._starting = True
        self._thread: threading.Thread | None = None

    # -- lifecycle --------------------------------------------------------- #

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="display-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 25.0) -> None:
        self.mailbox.put_command(Command.SHUTDOWN)
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.error("display worker did not stop within %.0f s", timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(self) -> None:
        try:
            self._startup()
            while True:
                item = self.mailbox.get(timeout=None if self._ready else RETRY_INTERVAL_S)
                if item is Command.SHUTDOWN:
                    break
                if item is None:  # timeout while in error state
                    self._guarded("re-initialise", lambda: None)
                    continue
                self._handle(item)
        except Exception:  # pragma: no cover - last resort, keeps the reason visible
            log.exception("display worker crashed")
            self.status.error("display worker crashed, see log")
            self.status.set_state("error")
            raise
        finally:
            self._shutdown()

    # -- startup / shutdown ------------------------------------------------ #

    def _startup(self) -> None:
        persisted, image = self.store.load()
        with self.status.lock:
            self.status.persisted = persisted
            self.status.persisted.current = None  # the panel content is unknown until we draw
        try:
            if not self._guarded("initialise", lambda: None):
                return  # state is "error"; the main loop retries periodically
            if self.cfg.clear_on_startup:
                self._guarded("clear", self._clear)
            if self.cfg.restore_last_frame and image is not None and self.pipeline is not None:
                try:
                    frame = self.pipeline.process(image, "restore", force_full=True)
                except ImageRejectedError as e:
                    log.warning("cannot restore last frame: %s", e)
                else:
                    self._handle(frame)
        finally:
            self._starting = False
        if self._ready:
            self.status.set_state("idle")

    def _shutdown(self) -> None:
        try:
            if self._ready:
                if self.cfg.clear_on_shutdown:
                    log.info("clearing panel before shutdown")
                    self._wake()
                    self._clear_panel()
                    self.panel.wait_idle()
                self.panel.low_power("sleep")
        except PANEL_ERRORS as e:
            log.error("panel error during shutdown: %s", e)
        finally:
            self.panel.close()
            self.status.set_state("stopped")

    # -- operations -------------------------------------------------------- #

    def _handle(self, item: Frame | Command) -> None:
        if item is Command.CLEAR:
            self._guarded("clear", self._clear)
        elif item is Command.REFRESH_FULL:
            if self.current is not None:
                current = self.current
                self._guarded("refresh", lambda: self._show(current.forced()))
            else:
                self._guarded("clear", self._clear)
        elif isinstance(item, Frame):
            self._guarded("update", lambda: self._show(item))

    def _guarded(self, what: str, op: Callable[[], None]) -> bool:
        """Run ``op`` with (re-)initialisation and one retry (design §3.5 state machine)."""
        for attempt in (1, 2):
            try:
                if not self._ready:
                    self._init_panel()
                op()
                return True
            except PANEL_ERRORS as e:
                self._ready = False
                with self.status.lock:
                    self.status.persisted.counters.panel_errors += 1
                self.status.error(f"{what}: {e}")
                log.warning("panel error during %s (attempt %d): %s", what, attempt, e)
                self.status.set_state("recovering" if attempt == 1 else "error")
        return False

    def _init_panel(self) -> None:
        info = self.panel.init()
        self._ready = True
        self._asleep = False
        if self.pipeline is None or self.pipeline.panel_size != (info.width, info.height):
            self.pipeline = Pipeline(self.cfg, info.width, info.height)
        lw, lh = self.pipeline.logical_size
        with self.status.lock:
            self.status.panel = {
                "width": info.width,
                "height": info.height,
                "logical_width": lw,
                "logical_height": lh,
                "fw_version": info.fw_version,
                "lut_version": info.lut_version,
                "vcom": self.settings.panel.vcom,
            }
            if self.status.state in ("error", "recovering"):
                self.status.state = self._idle_state

    @property
    def _idle_state(self) -> WorkerState:
        # During startup (clear + restore) health keeps reporting "starting".
        return "starting" if self._starting else "idle"

    @property
    def _busy_state(self) -> WorkerState:
        return "starting" if self._starting else "updating"

    def _wake(self) -> None:
        if self._asleep:
            self.panel.wake()
            self._asleep = False

    def _rest(self) -> None:
        self.panel.wait_idle()
        kind = self.settings.panel.low_power
        if kind != "none":
            self.panel.low_power(kind)
            self._asleep = True

    def _show(self, frame: Frame) -> None:
        plan = plan_update(self.current, frame, self.status.persisted.since_full, self.cfg)
        if plan.kind == "skip":
            log.debug("frame %d (%s) skipped: %s", frame.id, frame.source, plan.reason)
            with self.status.lock:
                self.status.persisted.counters.duplicates += 1
            return
        self.status.set_state(self._busy_state)
        t0 = time.monotonic()
        self._wake()
        self._execute(frame, plan)
        self._rest()
        duration = time.monotonic() - t0
        self.current = replace(frame, force_full=False)
        log.info(
            "frame %d from %s: %s refresh (%s, %d rects) in %.2f s",
            frame.id,
            frame.source,
            plan.kind,
            plan.reason,
            len(plan.rects),
            duration,
        )
        with self.status.lock:
            p = self.status.persisted
            if plan.kind == "full":
                p.counters.full += 1
                p.since_full = 0
            else:
                p.counters.partial += 1
                p.since_full += 1
            p.counters.coalesced = self.mailbox.coalesced
            p.current = CurrentFrame(
                frame_id=frame.id,
                source=frame.source,
                hash=frame.hash,
                received_at=frame.received_at.isoformat(timespec="seconds"),
                displayed_at=now_iso(),
            )
            self.status.last_update = LastUpdate(
                plan.kind, plan.reason, len(plan.rects), round(duration, 3), now_iso()
            )
            self.status.state = self._idle_state
        self.store.save(self.status.persisted, to_png(frame.preview))

    def _execute(self, frame: Frame, plan: Plan) -> None:
        if plan.kind == "full":
            if self.cfg.full_refresh_waveform == "init_then_gc16":
                white = np.full_like(frame.pixels, WHITE)
                self.panel.draw(white, 0, 0, frame.rotation, Waveform.INIT)
            self.panel.draw(frame.pixels, 0, 0, frame.rotation, Waveform.GC16)
            return
        for r in plan.rects:
            region = frame.pixels[r.y : r.y + r.h, r.x : r.x + r.w]
            self.panel.draw(region, r.x, r.y, frame.rotation, Waveform.GC16)

    def _clear_panel(self) -> None:
        info = self.panel.info
        if info is None:
            raise PanelError("panel not initialised")
        white = np.full((info.height, info.width), WHITE, dtype=np.uint8)
        self.panel.draw(white, 0, 0, 0, Waveform.INIT)

    def _clear(self) -> None:
        self.status.set_state(self._busy_state)
        t0 = time.monotonic()
        self._wake()
        self._clear_panel()
        self._rest()
        self.current = None
        with self.status.lock:
            self.status.persisted.since_full = 0
            self.status.persisted.current = None
            self.status.last_update = LastUpdate(
                "clear", "clear", 0, round(time.monotonic() - t0, 3), now_iso()
            )
            self.status.state = self._idle_state
        self.store.save(self.status.persisted, None)
        log.info("panel cleared")
