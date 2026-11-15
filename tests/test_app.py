"""HTTP API tests via httpx's ASGI transport (no extra test client dependency)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import numpy as np
import pytest

from ha_trmnl_ws_display.app import create_app
from ha_trmnl_ws_display.config import Settings
from ha_trmnl_ws_display.driver.fake import FakePanel
from ha_trmnl_ws_display.runtime import Runtime

from .conftest import SettingsFactory, png

pytestmark = pytest.mark.anyio

W, H = 240, 160
AUTH = {"Authorization": "Bearer s3cret"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def image(value: int = 255) -> bytes:
    return png(np.full((H, W), value, np.uint8))


async def until(cond: Callable[[], bool], limit_s: float = 5.0) -> None:
    deadline = time.monotonic() + limit_s
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


@asynccontextmanager
async def serve(
    settings: Settings, panel: FakePanel | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Runtime]]:
    runtime = Runtime(settings, panel or FakePanel(W, H))
    app = create_app(runtime)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, runtime


@asynccontextmanager
async def ready(settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, Runtime]]:
    async with serve(settings) as (client, runtime):
        await until(lambda: runtime.status.state == "idle")
        yield client, runtime


@pytest.fixture
def secured(make_settings: SettingsFactory) -> Settings:
    return make_settings(push={"token": "s3cret"})


async def test_health_index_openapi(secured: Settings) -> None:
    async with ready(secured) as (client, _):
        assert (await client.get("/health")).json() == {"status": "ok"}
        page = await client.get("/")
        assert page.status_code == 200
        assert "e-paper display" in page.text
        assert (await client.get("/openapi.json")).status_code == 200


async def test_push_flow(secured: Settings) -> None:
    async with ready(secured) as (client, runtime):
        assert (await client.get("/api/image/current.png")).status_code == 404
        r = await client.post("/api/image", content=image(0), headers={**AUTH, "Content-Type": "image/png"})
        assert r.status_code == 202
        body = r.json()
        assert body["duplicate"] is False
        await until(lambda: runtime.worker.current is not None)
        again = await client.post("/api/image", content=image(0), headers=AUTH)
        assert again.json()["duplicate"] is True

        cur = await client.get("/api/image/current.png")
        assert cur.status_code == 200
        assert cur.headers["content-type"] == "image/png"
        etag = cur.headers["etag"]
        assert etag == f'"{body["hash"]}"'
        cached = await client.get("/api/image/current.png", headers={"If-None-Match": etag})
        assert cached.status_code == 304

        status = (await client.get("/api/status")).json()
        assert status["current"]["source"] == "push"
        assert status["push"] == {"enabled": True, "auth": True}
        assert status["config"]["push"]["token"] == "**********"
        assert status["panel"]["logical_width"] == W
        assert status["pull"] is None


async def test_push_forced_full(secured: Settings) -> None:
    async with ready(secured) as (client, runtime):
        await client.post("/api/image", content=image(0), headers=AUTH)
        await until(lambda: runtime.status.persisted.counters.full == 1)
        r = await client.post("/api/image?refresh=full", content=image(0), headers=AUTH)
        assert r.json()["duplicate"] is False
        await until(lambda: runtime.status.persisted.counters.full == 2)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({}, 401), ({"Authorization": "Bearer wrong"}, 401), ({"Authorization": "s3cret"}, 401), (AUTH, 202)],
)
async def test_push_auth(secured: Settings, headers: dict[str, str], expected: int) -> None:
    async with ready(secured) as (client, _):
        r = await client.post("/api/image", content=image(), headers=headers)
        assert r.status_code == expected


async def test_commands_need_auth(secured: Settings) -> None:
    async with ready(secured) as (client, runtime):
        assert (await client.post("/api/refresh")).status_code == 401
        assert (await client.post("/api/clear")).status_code == 401
        assert (await client.post("/api/refresh", headers=AUTH)).status_code == 202
        assert (await client.post("/api/clear", headers=AUTH)).status_code == 202
        await until(lambda: len(runtime.worker.panel.draws()) >= 3)  # type: ignore[attr-defined]


async def test_push_rejections(secured: Settings) -> None:
    async with ready(secured) as (client, runtime):
        assert (await client.post("/api/image", content=b"garbage", headers=AUTH)).status_code == 400
        wrong = await client.post("/api/image", content=png(np.zeros((10, 10), np.uint8)), headers=AUTH)
        assert wrong.status_code == 422
        assert "expected 240x160" in wrong.json()["detail"]
        assert runtime.status.persisted.counters.rejected == 2


async def test_body_limit(make_settings: SettingsFactory) -> None:
    async with ready(make_settings(push={"max_body_bytes": 100})) as (client, _):
        assert (await client.post("/api/image", content=b"x" * 101)).status_code == 413

        async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: streamed limit applies
            yield b"x" * 60
            yield b"x" * 60

        assert (await client.post("/api/image", content=chunks())).status_code == 413


async def test_push_disabled(make_settings: SettingsFactory) -> None:
    async with ready(make_settings(push={"enabled": False})) as (client, _):
        assert (await client.post("/api/image", content=image())).status_code == 404


async def test_panel_failure_reports_unhealthy_and_503(make_settings: SettingsFactory) -> None:
    panel = FakePanel(W, H)
    panel.fail_next = 100
    async with serve(make_settings(), panel) as (client, runtime):
        await until(lambda: runtime.status.state == "error")
        health = await client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "error"
        assert "injected" in health.json()["reason"]
        assert (await client.post("/api/image", content=image())).status_code == 503


async def test_health_starting_and_stopped(make_settings: SettingsFactory) -> None:
    runtime = Runtime(make_settings(), FakePanel(W, H))
    assert runtime.healthy == (False, "display worker not running")
    async with ready(make_settings()) as (client, runtime):
        runtime.status.set_state("starting")
        health = await client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "starting"
        runtime.status.set_state("idle")


async def test_pull_status_present(make_settings: SettingsFactory) -> None:
    settings = make_settings(pull={"enabled": True, "url": "http://127.0.0.1:9/never", "on_start": False})
    async with ready(settings) as (client, runtime):
        assert runtime.puller is not None
        puller = runtime.puller
        await until(lambda: puller.status.next_run is not None)
        pull = (await client.get("/api/status")).json()["pull"]
        assert pull["url"] == "http://127.0.0.1:9/never"
        assert pull["next_run"] is not None


async def test_both_sources_disabled_warns(
    make_settings: SettingsFactory, caplog: pytest.LogCaptureFixture
) -> None:
    async with ready(make_settings(push={"enabled": False})):
        pass
    assert "both disabled" in caplog.text
