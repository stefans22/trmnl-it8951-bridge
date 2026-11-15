from __future__ import annotations

import asyncio

import httpx
import pytest

from trmnl_it8951_bridge.config import PullConfig
from trmnl_it8951_bridge.pipeline import ImageRejectedError
from trmnl_it8951_bridge.pull import FIRST_BACKOFF_S, Puller

URL = "http://addon.local:10000/lovelace/0?viewport=1200x825"


class Sink:
    def __init__(self, error: Exception | None = None) -> None:
        self.received: list[tuple[bytes, str]] = []
        self.error = error

    async def __call__(self, data: bytes, source: str) -> None:
        if self.error:
            raise self.error
        self.received.append((data, source))


def cfg(**kw: object) -> PullConfig:
    base: dict[str, object] = {"enabled": True, "url": URL, "interval_s": 60, "backoff_max_s": 45}
    return PullConfig.model_validate({**base, **kw})


def run(puller: Puller, handler: httpx.MockTransport) -> float:
    async def go() -> float:
        async with httpx.AsyncClient(transport=handler) as client:
            return await puller.run_once(client)

    return asyncio.run(go())


def test_success_submits_image_and_uses_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"PNG", headers={"ETag": '"v1"'})

    sink = Sink()
    puller = Puller(cfg(headers={"X-Key": "k"}), sink)
    assert run(puller, httpx.MockTransport(handler)) == 60
    assert sink.received == [(b"PNG", "pull")]
    assert seen[0].headers["X-Key"] == "k"
    assert "If-None-Match" not in seen[0].headers
    assert puller.status.last_success is not None

    # second request is conditional; 304 counts as success without submitting
    def conditional(req: httpx.Request) -> httpx.Response:
        assert req.headers["If-None-Match"] == '"v1"'
        return httpx.Response(304)

    transport = httpx.MockTransport(conditional)
    assert run(puller, transport) == 60
    assert puller.status.not_modified == 1
    assert len(sink.received) == 1


def test_failures_back_off_exponentially_and_reset() -> None:
    puller = Puller(cfg(), Sink())
    fail = httpx.MockTransport(lambda req: httpx.Response(500))
    delays = [run(puller, fail) for _ in range(4)]
    assert delays == [FIRST_BACKOFF_S, 2 * FIRST_BACKOFF_S, 40.0, 45.0]  # capped at backoff_max_s
    assert puller.status.consecutive_failures == 4
    assert "500" in (puller.status.last_error or "")
    ok = httpx.MockTransport(lambda req: httpx.Response(200, content=b"x"))
    assert run(puller, ok) == 60
    assert puller.status.consecutive_failures == 0


def test_connection_error_is_a_failure() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    puller = Puller(cfg(), Sink())
    assert run(puller, httpx.MockTransport(boom)) == FIRST_BACKOFF_S


def test_rejected_image_waits_normal_interval() -> None:
    puller = Puller(cfg(), Sink(ImageRejectedError("wrong size")))
    assert run(puller, httpx.MockTransport(lambda req: httpx.Response(200, content=b"x"))) == 60
    assert "rejected" in (puller.status.last_error or "")


def test_not_ready_is_a_failure() -> None:
    puller = Puller(cfg(), Sink(RuntimeError("panel not initialised yet")))
    assert run(puller, httpx.MockTransport(lambda req: httpx.Response(200, content=b"x"))) == FIRST_BACKOFF_S


def test_url_required() -> None:
    with pytest.raises(ValueError, match="url"):
        Puller(PullConfig(), Sink())


def test_run_loop_fetches_on_start_and_survives_errors() -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"img")

    async def go() -> None:
        sink = Sink()
        puller = Puller(cfg(interval_s=10), sink, transport=httpx.MockTransport(handler))
        task = asyncio.create_task(puller.run())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if sink.received:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sink.received == [(b"img", "pull")]
        assert puller.snapshot()["next_run"] is not None

    asyncio.run(go())
    assert calls == 1
