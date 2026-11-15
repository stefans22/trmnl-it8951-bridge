"""Pull scheduler: periodically fetch the add-on's image URL (design §3.2)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import PullConfig
from .pipeline import ImageRejectedError
from .state import now_iso

log = logging.getLogger(__name__)

FIRST_BACKOFF_S = 10.0

Submit = Callable[[bytes, str], Awaitable[object]]


@dataclass
class PullStatus:
    last_attempt: str | None = None
    last_success: str | None = None
    last_error: str | None = None
    next_run: str | None = None
    consecutive_failures: int = 0
    not_modified: int = 0


class Puller:
    def __init__(
        self,
        cfg: PullConfig,
        submit: Submit,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "ha-trmnl-ws-display",
    ) -> None:
        if cfg.url is None:
            raise ValueError("pull.url is not set")
        self.cfg = cfg
        self.url = str(cfg.url)
        self._submit = submit
        self._transport = transport
        self._etag: str | None = None
        self._user_agent = user_agent
        self.status = PullStatus()

    def snapshot(self) -> dict[str, Any]:
        return {"url": self.url, "interval_s": self.cfg.interval_s, **asdict(self.status)}

    async def run(self) -> None:
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self.cfg.timeout_s, follow_redirects=True
        ) as client:
            delay = 0.0 if self.cfg.on_start else float(self.cfg.interval_s)
            while True:
                self._schedule(delay)
                await asyncio.sleep(delay)
                try:
                    delay = await self.run_once(client)
                except Exception as e:  # never let the scheduler die
                    log.exception("unexpected error in pull")
                    delay = self._failure(f"{type(e).__name__}: {e}")

    def _schedule(self, delay: float) -> None:
        self.status.next_run = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(timespec="seconds")

    async def run_once(self, client: httpx.AsyncClient) -> float:
        """Fetch once; returns the delay until the next attempt."""
        self.status.last_attempt = now_iso()
        headers = {k: v.get_secret_value() for k, v in self.cfg.headers.items()}
        headers.setdefault("User-Agent", self._user_agent)
        if self._etag:
            headers["If-None-Match"] = self._etag
        try:
            resp = await client.get(self.url, headers=headers)
            if resp.status_code == httpx.codes.NOT_MODIFIED:
                self.status.not_modified += 1
                return self._success()
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return self._failure(f"{type(e).__name__}: {e}")
        try:
            await self._submit(resp.content, "pull")
        except ImageRejectedError as e:
            # The add-on answered, but with an image we can't use: no point retrying fast.
            self.status.last_error = f"{now_iso()} image rejected: {e}"
            log.warning("pulled image rejected: %s", e)
            return float(self.cfg.interval_s)
        except RuntimeError as e:  # e.g. panel not initialised yet
            return self._failure(str(e))
        self._etag = resp.headers.get("ETag")
        return self._success()

    def _success(self) -> float:
        self.status.last_success = now_iso()
        self.status.consecutive_failures = 0
        return float(self.cfg.interval_s)

    def _failure(self, message: str) -> float:
        self.status.consecutive_failures += 1
        self.status.last_error = f"{now_iso()} {message}"
        exponent = min(self.status.consecutive_failures - 1, 20)
        delay = min(float(self.cfg.backoff_max_s), FIRST_BACKOFF_S * float(2**exponent))
        log.warning("pull from %s failed (%s), retrying in %.0f s", self.url, message, delay)
        return delay
