"""Every cog loads and its slash commands build valid Discord payloads, without connecting to Discord."""

from pathlib import Path

from beans_bot.bot import BeansBot
from beans_bot.cogs.action_holiday import ActionHolidayCog
from beans_bot.cogs.admin import AdminCog
from beans_bot.cogs.basic import BasicCog
from beans_bot.cogs.birthday import BirthdayCog
from beans_bot.cogs.coins import CoinsCog
from beans_bot.cogs.fat_cow import FatCowCog
from beans_bot.cogs.gambling import GamblingCog
from beans_bot.cogs.jail import JailCog
from beans_bot.cogs.role_name import RoleNameCog
from beans_bot.cogs.snipes import SnipesCog
from beans_bot.cogs.tomatoes import TomatoesCog
from beans_bot.cogs.vip import VipCog
from beans_bot.config.schema import BotConfig

COGS = (BasicCog, AdminCog, RoleNameCog, JailCog, TomatoesCog, CoinsCog, GamblingCog, FatCowCog, BirthdayCog, ActionHolidayCog, VipCog, SnipesCog)


async def test_commands_register(tmp_path: Path):
    bot = BeansBot(BotConfig(bot_token="x", db_path=tmp_path / "beans.db"))
    try:
        for cog in COGS:
            await bot.add_cog(cog(bot))
        names = {c.qualified_name for c in bot.tree.walk_commands()}
        assert {
            "ping", "admin sync", "admin say", "admin dm", "robrole", "jail bribe", "jail frame", "jail current", "jail info", "jail leaderboard",
            "coins wealth", "coins leaderboard", "bet place", "bet add", "bet swap", "bet stats", "bet close", "bet cancel",
            "bet resolve", "admin botbet", "fatcow start", "fatcow consumed", "fatcow went", "fatcow leaderboard",
            "vip daypass", "vip onepercent", "vip status",
        } <= names  # fmt: skip
        for command in bot.tree.get_commands():
            command.to_dict(bot.tree)
        admin = bot.tree.get_command("admin")
        assert admin is not None and admin.guild_only
        assert admin.default_permissions is not None and admin.default_permissions.administrator
    finally:
        for name in list(bot.cogs):
            await bot.remove_cog(name)
        await bot.services.db.close()
