from __future__ import annotations

import importlib.util
import logging

import discord
from discord import app_commands
from discord.ext import commands

from beans_bot.cogs.base import CheckMessage
from beans_bot.config.schema import BotConfig
from beans_bot.currency import AmountError
from beans_bot.services import Services
from beans_bot.services.errors import UserFacingError

logger = logging.getLogger(__name__)


class BeansTree(app_commands.CommandTree):
    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        if isinstance(original, (CheckMessage, UserFacingError, AmountError)):
            message = str(original)
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command can only be used in a server."
        elif isinstance(error, app_commands.CheckFailure):
            message = "You can't use this command here."
        else:
            logger.error(f"Error in command {interaction.command and interaction.command.qualified_name}", exc_info=original)
            message = "Something went wrong running that command."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class BeansBot(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.message_content = True  # Humiliation, renames and the classifier read messages
        intents.reactions = True
        intents.members = True  # Jail role bookkeeping
        # Every command is a slash command, so the prefix is never used.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, tree_cls=BeansTree, help_command=None)

        self.config = config
        self.services = Services.build(config)

    async def setup_hook(self) -> None:
        assert self.user is not None
        self.services.coins.house_user_id = self.user.id

        if self.config.gemini.required:
            await self.services.gemini.check()
        else:
            try:
                await self.services.gemini.check()
            except Exception as e:
                logger.warning(f"Gemini API test failed: {e}")

        from beans_bot.cogs.action_holiday import ActionHolidayCog
        from beans_bot.cogs.admin import AdminCog
        from beans_bot.cogs.basic import BasicCog
        from beans_bot.cogs.birthday import BirthdayCog
        from beans_bot.cogs.coins import CoinsCog
        from beans_bot.cogs.fat_cow import FatCowCog
        from beans_bot.cogs.gambling import GamblingCog
        from beans_bot.cogs.jail import JailCog
        from beans_bot.cogs.manifesto import ManifestoCog
        from beans_bot.cogs.role_name import RoleNameCog
        from beans_bot.cogs.snipes import SnipesCog
        from beans_bot.cogs.tomatoes import TomatoesCog
        from beans_bot.cogs.vip import VipCog

        for cog in (BasicCog, AdminCog, RoleNameCog, JailCog, TomatoesCog, CoinsCog, GamblingCog, FatCowCog, BirthdayCog, ActionHolidayCog, VipCog, SnipesCog, ManifestoCog):
            await self.add_cog(cog(self))

        if self._classifier_wanted():
            from beans_bot.cogs.classifier.cog import ClassifierCog

            await self.add_cog(ClassifierCog(self))

        if self.config.sync_commands_on_startup:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands")

    def _classifier_wanted(self) -> bool:
        if not self.config.classifier.enabled:
            return False
        if not any(g.classifier for g in self.config.guilds.values() if g.active):
            return False
        if importlib.util.find_spec("torch") is None:
            logger.warning("A guild has classifier config but the 'classifier' extra isn't installed; skipping it.")
            return False
        return True

    async def on_ready(self) -> None:
        assert self.user is not None
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")

    async def close(self) -> None:
        await super().close()
        await self.services.db.close()
