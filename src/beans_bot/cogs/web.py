"""
The admin website, served from inside the bot so it can use the live Discord client and the loaded
classifier. See src/beans_bot/web/.

- Off unless `web.enabled` is set. It has no commands.
- Hypercorn runs on the bot's own event loop as a task, and stops when the cog unloads.
- If the port can't be bound, the error is logged and the bot carries on without a website.
"""

from __future__ import annotations

import asyncio
from typing import cast

from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig
from hypercorn.typing import ASGIFramework

from beans_bot.cogs.base import BeansCog
from beans_bot.web.app import create_app
from beans_bot.web.context import WebContext


class WebCog(BeansCog):
    async def cog_load(self):
        self.shutdown = asyncio.Event()
        self.server: asyncio.Task[None] | None = None
        web = self.config.web
        if not web.enabled:
            return
        app = create_app(WebContext(config=self.config, services=self.services, bot=self.bot))
        config = HypercornConfig()
        config.bind = [f"{web.host}:{web.port}"]
        config.accesslog = None
        config.errorlog = self.logger
        # Keep a reference, or the task can be garbage collected while it serves.
        # Starlette's ASGI types are looser than Hypercorn's, but the same protocol.
        self.server = asyncio.create_task(self._serve(cast(ASGIFramework, app), config))

    async def _serve(self, app: ASGIFramework, config: HypercornConfig) -> None:
        # Try the port first. Hypercorn starts the app's lifespan before it binds, and a failed bind
        # leaves that lifespan hanging.
        try:
            sockets = config.create_sockets()
        except OSError as e:
            self.logger.error(f"The website couldn't start on {config.bind[0]}: {e}. The bot carries on without it.")
            return
        for sock in (*sockets.secure_sockets, *sockets.insecure_sockets, *sockets.quic_sockets):
            sock.close()
        try:
            await serve(app, config, shutdown_trigger=self.shutdown.wait)
        except OSError as e:
            self.logger.error(f"The website couldn't start on {config.bind[0]}: {e}. The bot carries on without it.")
        except Exception:
            self.logger.exception("The website stopped with an error. The bot carries on without it.")

    async def cog_unload(self):
        self.shutdown.set()
        if self.server is not None:
            await self.server
            self.server = None
