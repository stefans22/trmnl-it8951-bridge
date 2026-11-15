"""Wires panel, worker, mailbox, pull scheduler and status together."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import anyio

from . import __version__
from .config import Settings
from .driver.base import Panel
from .mailbox import Command, Mailbox
from .pipeline import Frame, ImageRejectedError
from .pull import Puller
from .state import StateStore, Status
from .worker import DisplayWorker

log = logging.getLogger(__name__)


class NotReadyError(RuntimeError):
    """The panel has not been initialised yet, so images can't be processed."""


@dataclass(frozen=True)
class Accepted:
    frame: Frame
    duplicate: bool


class Runtime:
    def __init__(self, settings: Settings, panel: Panel) -> None:
        self.settings = settings
        self.status = Status()
        self.mailbox = Mailbox()
        self.store = StateStore(settings.storage.data_dir, settings.storage.persist)
        self.worker = DisplayWorker(settings, panel, self.mailbox, self.store, self.status)
        self.puller = Puller(settings.pull, self.submit) if settings.pull.enabled else None
        self._pull_task: asyncio.Task[None] | None = None

    # -- lifecycle --------------------------------------------------------- #

    async def start(self) -> None:
        self.worker.start()
        if self.puller is not None:
            self._pull_task = asyncio.create_task(self.puller.run(), name="pull")
        if not self.settings.push.enabled and self.puller is None:
            log.warning("push and pull are both disabled - the panel will only change via the API")

    async def stop(self) -> None:
        if self._pull_task is not None:
            self._pull_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pull_task
        await anyio.to_thread.run_sync(self.worker.stop)

    # -- inputs ------------------------------------------------------------ #

    async def submit(self, data: bytes, source: str, force_full: bool = False) -> Accepted:
        """Decode and queue an image. Raises ImageRejectedError or NotReadyError."""
        pipeline = self.worker.pipeline
        if pipeline is None:
            raise NotReadyError("panel not initialised yet")
        try:
            frame = await anyio.to_thread.run_sync(
                lambda: pipeline.process(data, source, force_full=force_full)
            )
        except ImageRejectedError:
            with self.status.lock:
                self.status.persisted.counters.rejected += 1
            raise
        current = self.worker.current
        pending = self.mailbox.pending_frame
        duplicate = not force_full and any(f is not None and f.hash == frame.hash for f in (current, pending))
        self.mailbox.put_frame(frame)
        return Accepted(frame, duplicate)

    def command(self, cmd: Command) -> None:
        self.mailbox.put_command(cmd)

    # -- observability ----------------------------------------------------- #

    @property
    def healthy(self) -> tuple[bool, str]:
        if not self.worker.alive:
            return False, "display worker not running"
        state = self.status.state
        if state == "starting":
            return False, "starting"
        if state == "error":
            err = self.status.last_error
            return False, err["message"] if err else "panel error"
        return True, "ok"

    def status_json(self) -> dict[str, Any]:
        snap = self.status.snapshot()
        cfg = self.settings.display
        pending = self.mailbox.pending_frame
        return {
            "version": __version__,
            **snap,
            "pending_frame": pending.id if pending else None,
            "display": {
                "rotation": cfg.rotation,
                "rotation_via_controller": cfg.rotation_via_controller,
                "mirror_x": cfg.mirror_x,
                "mirror_y": cfg.mirror_y,
                "full_refresh_every": cfg.full_refresh_every,
            },
            "push": {"enabled": self.settings.push.enabled, "auth": self.settings.push.token is not None},
            "pull": self.puller.snapshot() if self.puller else None,
            "config": self.settings.redacted(),
        }
