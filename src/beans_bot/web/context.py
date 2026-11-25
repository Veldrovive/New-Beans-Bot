"""What every request handler can reach: the config, the services and, when it's connected, the bot."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from beans_bot.config.schema import BotConfig
from beans_bot.db.types import utcnow
from beans_bot.services import Services

if TYPE_CHECKING:
    from beans_bot.bot import BeansBot

logger = logging.getLogger(__name__)


@dataclass
class WebContext:
    config: BotConfig
    services: Services
    bot: BeansBot | None = None  # None in tests and scripts/run_web.py
    started_at: datetime.datetime = field(default_factory=utcnow)

    @property
    def connected(self) -> bool:
        return self.bot is not None and self.bot.is_ready() and not self.bot.is_closed()

    async def post(self, channel_id: int, text: str) -> bool:
        """
        Post in a channel or thread as the bot, for announcements that follow an action. False when the
        bot isn't connected or Discord refuses; the action itself already happened, so that's only logged.
        """
        if self.bot is None or not self.connected:
            return False
        channel = self.bot.get_channel(channel_id)
        try:
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable):
                return False
            await channel.send(text, allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True))
        except discord.HTTPException as e:
            logger.warning(f"Could not post in channel {channel_id}: {e}")
            return False
        return True
