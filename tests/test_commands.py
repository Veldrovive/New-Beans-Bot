"""Every cog loads and its slash commands build valid Discord payloads, without connecting to Discord."""

from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from beans_bot.bot import BeansBot
from beans_bot.cogs.action_holiday import ActionHolidayCog
from beans_bot.cogs.admin import AdminCog
from beans_bot.cogs.auto_bets import AutoBetsCog
from beans_bot.cogs.base import CheckMessage, in_bot_channel
from beans_bot.cogs.basic import BasicCog
from beans_bot.cogs.birthday import BirthdayCog
from beans_bot.cogs.coins import CoinsCog
from beans_bot.cogs.fat_cow import FatCowCog
from beans_bot.cogs.gambling import GamblingCog
from beans_bot.cogs.heists import HeistCog
from beans_bot.cogs.jail import JailCog
from beans_bot.cogs.manifesto import ManifestoCog
from beans_bot.cogs.role_name import RoleNameCog
from beans_bot.cogs.snipes import SnipesCog
from beans_bot.cogs.tomatoes import TomatoesCog
from beans_bot.cogs.vip import VipCog
from beans_bot.config.schema import BotConfig, GuildConfig

COGS = (BasicCog, AdminCog, RoleNameCog, JailCog, TomatoesCog, CoinsCog, GamblingCog, AutoBetsCog, FatCowCog, BirthdayCog, ActionHolidayCog, VipCog, SnipesCog, ManifestoCog, HeistCog)


async def test_commands_register(tmp_path: Path):
    bot = BeansBot(BotConfig(bot_token="x", db_path=tmp_path / "beans.db"))
    try:
        for cog in COGS:
            await bot.add_cog(cog(bot))
        names = {c.qualified_name for c in bot.tree.walk_commands()}
        assert {
            "ping", "admin sync", "admin say", "admin dm", "robrole", "jail bribe", "jail frame", "jail current", "jail info", "jail leaderboard",
            "coins wealth", "coins leaderboard", "coins launder", "coins transfer", "admin give", "admin take", "bet place", "bet add", "bet swap", "bet stats", "bet close", "bet cancel",
            "bet resolve", "admin botbet", "autobet post", "fatcow start", "fatcow consumed", "fatcow went", "fatcow leaderboard",
            "vip daypass", "vip henchman", "vip onepercent", "vip status", "manifesto wisdom", "manifesto list",
            "manifesto add", "manifesto edit", "manifesto remove", "heist begin", "heist join", "heist snitch", "heist status",
        } <= names  # fmt: skip
        assert bot.tree.get_command("Impart wisdom", type=discord.AppCommandType.message) is not None
        for command in bot.tree.get_commands():
            command.to_dict(bot.tree)
        admin = bot.tree.get_command("admin")
        assert admin is not None and admin.guild_only
        assert admin.default_permissions is not None and admin.default_permissions.administrator
    finally:
        for name in list(bot.cogs):
            await bot.remove_cog(name)
        await bot.services.db.close()


async def test_bot_channel_check_lets_quiet_commands_through():
    config = BotConfig(bot_token="x", guilds={1: GuildConfig(name="test", bot_channel_id=10)})

    @in_bot_channel()
    async def command(interaction: discord.Interaction): ...

    check = getattr(command, "__discord_app_commands_checks__")[0]

    def interaction(channel_id: int, **options: object):
        return SimpleNamespace(guild_id=1, channel_id=channel_id, client=SimpleNamespace(config=config), namespace=SimpleNamespace(**options))

    assert await check(interaction(10))
    assert await check(interaction(20, quiet=True))
    for loud in (interaction(20), interaction(20, quiet=False)):
        with pytest.raises(CheckMessage):
            await check(loud)
