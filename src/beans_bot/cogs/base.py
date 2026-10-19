"""Things every cog shares: access to config and services, and app command checks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from beans_bot.config.schema import GuildConfig

if TYPE_CHECKING:
    from beans_bot.bot import BeansBot


class BeansCog(commands.Cog):
    def __init__(self, bot: BeansBot):
        self.bot = bot
        self.config = bot.config
        self.services = bot.services
        self.logger = logging.getLogger(f"beans_bot.cogs.{type(self).__name__}")

    def guild_config(self, guild_id: int | None) -> GuildConfig | None:
        return self.config.guild(guild_id)

    def is_own_user(self, user_id: int) -> bool:
        return self.bot.user is not None and user_id == self.bot.user.id

    async def fetch_message(self, channel_id: int, message_id: int) -> discord.Message | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return None


class CheckMessage(app_commands.CheckFailure):
    """A failed check whose message is shown to the member."""


# Lookup commands take `quiet: bool = False` and reply ephemerally when it's set.
QUIET_HELP = "Only you see the reply"


def _interaction_guild_config(interaction: discord.Interaction) -> GuildConfig | None:
    return interaction.client.config.guild(interaction.guild_id)  # type: ignore[attr-defined]


def requires_feature(feature: str):
    """Only allow the command in guilds that have the given config section."""

    async def predicate(interaction: discord.Interaction) -> bool:
        config = _interaction_guild_config(interaction)
        if config is None or getattr(config, feature) is None:
            raise CheckMessage("This command is not set up for this server.")
        return True

    return app_commands.check(predicate)


def in_bot_channel():
    """Only allow the command in the guild's bot channel, if it has one."""

    async def predicate(interaction: discord.Interaction) -> bool:
        config = _interaction_guild_config(interaction)
        if config is not None and config.bot_channel_id and interaction.channel_id != config.bot_channel_id:
            raise CheckMessage(f"Please use this command in <#{config.bot_channel_id}>.")
        return True

    return app_commands.check(predicate)


def admin_only():
    """Only allow server admins. Backs up default_permissions, which server owners can override per command."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not interaction.permissions.administrator:
            raise CheckMessage("Nice try. Only server admins get to pull these levers.")
        return True

    return app_commands.check(predicate)
