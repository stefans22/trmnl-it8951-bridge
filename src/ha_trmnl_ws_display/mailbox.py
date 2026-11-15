"""Single-slot frame mailbox plus a command queue (ADR 0006)."""

from __future__ import annotations

import threading
from collections import deque
from enum import Enum

from .pipeline import Frame


class Command(Enum):
    REFRESH_FULL = "refresh_full"
    CLEAR = "clear"
    SHUTDOWN = "shutdown"


class Mailbox:
    """Producers: HTTP push, pull scheduler, API commands. Consumer: the display worker.

    A new frame replaces a pending one (newest received wins). Commands are kept
    in order and are handled before a pending frame; SHUTDOWN jumps the queue.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: Frame | None = None
        self._commands: deque[Command] = deque()
        self.coalesced = 0

    def put_frame(self, frame: Frame) -> None:
        with self._cond:
            if self._frame is not None:
                self.coalesced += 1
            self._frame = frame
            self._cond.notify()

    def put_command(self, cmd: Command) -> None:
        with self._cond:
            if cmd is Command.SHUTDOWN:
                self._commands.appendleft(cmd)
            else:
                self._commands.append(cmd)
            self._cond.notify()

    def get(self, timeout: float | None = None) -> Frame | Command | None:
        """Next item, or ``None`` on timeout."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._commands or self._frame is not None, timeout):
                return None
            if self._commands:
                return self._commands.popleft()
            frame, self._frame = self._frame, None
            return frame

    @property
    def pending_frame(self) -> Frame | None:
        with self._cond:
            return self._frame
