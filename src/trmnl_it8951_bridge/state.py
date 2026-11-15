"""Runtime status (shared with the API) and its persistence in the data directory."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

WorkerState = Literal["starting", "idle", "updating", "recovering", "error", "stopped"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Counters:
    full: int = 0
    partial: int = 0
    duplicates: int = 0
    rejected: int = 0
    coalesced: int = 0
    panel_errors: int = 0


@dataclass
class CurrentFrame:
    frame_id: int
    source: str
    hash: str
    received_at: str
    displayed_at: str


@dataclass
class LastUpdate:
    kind: str
    reason: str
    rects: int
    duration_s: float
    at: str


@dataclass
class Persisted:
    """What survives a restart (``state.json``)."""

    since_full: int = 0
    counters: Counters = field(default_factory=Counters)
    current: CurrentFrame | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Persisted:
        cur = data.get("current")
        return cls(
            since_full=int(data.get("since_full", 0)),
            counters=Counters(**data.get("counters", {})),
            current=CurrentFrame(**cur) if cur else None,
        )


class Status:
    """Thread-safe runtime status. Written by the worker, read by the API."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = datetime.now(UTC)
        self.state: WorkerState = "starting"
        self.persisted = Persisted()
        self.panel: dict[str, Any] | None = None
        self.last_update: LastUpdate | None = None
        self.last_error: dict[str, str] | None = None

    def set_state(self, state: WorkerState) -> None:
        with self.lock:
            self.state = state

    def error(self, message: str) -> None:
        with self.lock:
            self.last_error = {"at": now_iso(), "message": message}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state,
                "uptime_s": round((datetime.now(UTC) - self.started_at).total_seconds()),
                "panel": self.panel,
                "current": asdict(self.persisted.current) if self.persisted.current else None,
                "since_full": self.persisted.since_full,
                "counters": asdict(self.persisted.counters),
                "last_update": asdict(self.last_update) if self.last_update else None,
                "last_error": self.last_error,
            }


class StateStore:
    """``state.json`` + ``current.png`` in the data dir, written atomically (design §3.8)."""

    STATE = "state.json"
    IMAGE = "current.png"

    def __init__(self, data_dir: Path, persist: bool) -> None:
        self.dir = data_dir
        self.persist = persist

    def load(self) -> tuple[Persisted, bytes | None]:
        if not self.persist:
            return Persisted(), None
        try:
            data = json.loads((self.dir / self.STATE).read_text())
            state = Persisted.from_json(data)
        except FileNotFoundError:
            return Persisted(), None
        except (ValueError, TypeError, KeyError) as e:
            log.warning("ignoring unreadable %s: %s", self.dir / self.STATE, e)
            return Persisted(), None
        try:
            image = (self.dir / self.IMAGE).read_bytes()
        except FileNotFoundError:
            image = None
        return state, image

    def save(self, state: Persisted, image_png: bytes | None) -> None:
        if not self.persist:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            if image_png is None:
                with contextlib.suppress(FileNotFoundError):
                    (self.dir / self.IMAGE).unlink()
            else:
                self._atomic_write(self.IMAGE, image_png)
            self._atomic_write(self.STATE, json.dumps(asdict(state), indent=2).encode())
        except OSError as e:
            log.error("cannot persist state to %s: %s", self.dir, e)

    def _atomic_write(self, name: str, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=f".{name}.")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, self.dir / name)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
