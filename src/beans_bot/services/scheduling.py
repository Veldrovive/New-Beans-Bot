"""Daily jobs that run at a fixed local time, and once on startup if that time has already passed today."""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable

from discord.ext import commands, tasks

logger = logging.getLogger(__name__)


class DailyJob:
    def __init__(
        self,
        bot: commands.Bot,
        name: str,
        at: datetime.time,
        run: Callable[[], Awaitable[None]],
        on_startup: Callable[[], Awaitable[None]] | None = None,
    ):
        """
        `at` must be timezone-aware. `run` is called every day at that time. On startup,
        `on_startup` (e.g. a self-test) runs first, then `run` if `at` has already passed.
        """
        assert at.tzinfo is not None, "DailyJob times must be timezone-aware"
        self.bot = bot
        self.name = name
        self.at = at
        self._run = run
        self._on_startup = on_startup
        self._loop = tasks.loop(time=at)(self._safe_run)
        self._loop.before_loop(self.bot.wait_until_ready)
        self._startup_task: asyncio.Task | None = None

    async def _safe_run(self) -> None:
        try:
            await self._run()
        except Exception:
            logger.exception(f"Daily job {self.name} failed")

    async def _startup(self) -> None:
        await self.bot.wait_until_ready()
        if self._on_startup is not None:
            try:
                await self._on_startup()
            except Exception as e:
                logger.warning(f"Startup check for {self.name} failed: {e}")
        now = datetime.datetime.now(self.at.tzinfo)
        if now.time() >= self.at.replace(tzinfo=None):
            await self._safe_run()

    def start(self) -> None:
        self._loop.start()
        self._startup_task = asyncio.create_task(self._startup(), name=f"{self.name}-startup")

    def stop(self) -> None:
        self._loop.cancel()
        if self._startup_task is not None:
            self._startup_task.cancel()
