from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from ha_trmnl_ws_display.mailbox import Command, Mailbox
from ha_trmnl_ws_display.pipeline import Frame
from ha_trmnl_ws_display.state import Counters, CurrentFrame, Persisted, StateStore, Status


def test_store_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "data", True)
    state = Persisted(3, Counters(full=1, partial=2), CurrentFrame(1, "push", "abc", "t1", "t2"))
    store.save(state, b"png")
    loaded, image = store.load()
    assert loaded == state
    assert image == b"png"
    store.save(Persisted(), None)
    assert store.load()[1] is None
    assert [p.name for p in (tmp_path / "data").iterdir()] == ["state.json"]  # no temp files left


def test_store_disabled(tmp_path: Path) -> None:
    store = StateStore(tmp_path, False)
    store.save(Persisted(5), b"png")
    assert list(tmp_path.iterdir()) == []
    assert store.load() == (Persisted(), None)


def test_store_missing_and_corrupt(tmp_path: Path) -> None:
    store = StateStore(tmp_path, True)
    assert store.load() == (Persisted(), None)
    (tmp_path / "state.json").write_text("{not json")
    assert store.load() == (Persisted(), None)
    (tmp_path / "state.json").write_text('{"since_full": 2}')
    assert store.load() == (Persisted(2), None)


def test_store_write_error_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    StateStore(blocker / "sub", True).save(Persisted(), None)
    assert "cannot persist" in caplog.text


def test_status_snapshot() -> None:
    s = Status()
    s.error("boom")
    snap = s.snapshot()
    assert snap["state"] == "starting"
    assert snap["last_error"]["message"] == "boom"
    assert snap["current"] is None


def make_frame(n: int) -> Frame:
    px = np.zeros((1, 1), np.uint8)
    return Frame(n, "t", datetime.now(UTC), px, px, 0, str(n))


def test_mailbox_newest_frame_wins() -> None:
    mb = Mailbox()
    mb.put_frame(make_frame(1))
    mb.put_frame(make_frame(2))
    assert mb.coalesced == 1
    assert mb.pending_frame is not None
    item = mb.get(0)
    assert isinstance(item, Frame)
    assert item.id == 2
    assert mb.get(0) is None


def test_mailbox_commands_first_and_shutdown_jumps_queue() -> None:
    mb = Mailbox()
    mb.put_frame(make_frame(1))
    mb.put_command(Command.CLEAR)
    mb.put_command(Command.REFRESH_FULL)
    mb.put_command(Command.SHUTDOWN)
    assert [mb.get(0) for _ in range(3)] == [Command.SHUTDOWN, Command.CLEAR, Command.REFRESH_FULL]
    assert isinstance(mb.get(0), Frame)


def test_mailbox_get_blocks_until_put() -> None:
    mb = Mailbox()
    threading.Timer(0.05, mb.put_command, args=(Command.CLEAR,)).start()
    assert mb.get(2) is Command.CLEAR
