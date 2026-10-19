"""BeanCoins: earned when others react to your messages with the coin emoji."""

import datetime

import discord
from discord import app_commands

from beans_bot.cogs.base import QUIET_HELP, BeansCog, in_bot_channel, requires_feature
from beans_bot.services import emoji
from beans_bot.services.coins import ReactionGrantResult


class CoinsCog(BeansCog):
    coins_group = app_commands.Group(name="coins", description="BeanCoin balances.", guild_only=True)

    def start_of_today(self) -> datetime.datetime:
        now = datetime.datetime.now(self.config.tz)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or self.is_own_user(payload.user_id):
            return
        config = self.guild_config(payload.guild_id)
        if config is None or config.coins is None or not emoji.matches(payload.emoji, config.coins.emoji):
            return
        message = await self.fetch_message(payload.channel_id, payload.message_id)
        if message is None or message.author.id == payload.user_id:
            return  # Can't give yourself coins
        if discord.utils.utcnow() - message.created_at > config.coins.max_message_age:
            return

        result = await self.services.coins.grant_for_reaction(
            guild_id=payload.guild_id,
            giver_id=payload.user_id,
            receiver_id=message.author.id,
            message_id=message.id,
            amount=config.coins.reaction_value,
            max_per_day=config.coins.max_reactions_per_day,
            day_start=self.start_of_today(),
        )
        if result is ReactionGrantResult.DAILY_LIMIT and payload.member is not None:
            self.logger.info(f"{payload.user_id} hit the daily coin limit in guild {payload.guild_id}; removing reaction")
            try:
                await message.remove_reaction(payload.emoji, payload.member)
            except discord.HTTPException:
                pass

    @coins_group.command(name="wealth", description="Check your or someone else's BeanCoin wealth.")
    @app_commands.describe(member="Whose wealth to check (defaults to you)", quiet=QUIET_HELP)
    @requires_feature("coins")
    @in_bot_channel()
    async def wealth(self, interaction: discord.Interaction, member: discord.Member | None = None, quiet: bool = False):
        assert interaction.guild_id is not None
        target = member or interaction.user
        currency = self.services.currency
        balance = await self.services.coins.balance(interaction.guild_id, target.id)
        received, gifted = await self.services.coins.reaction_totals(interaction.guild_id, target.id)
        await interaction.response.send_message(
            f"**Current wealth for {target.display_name}**\n"
            f"- Balance: {currency.format(balance)}\n\n"
            f"**Overall**\n"
            f"- Total Received: {currency.format(received)}\n"
            f"- Total Gifted to others: {currency.format(gifted)}",
            ephemeral=quiet,
        )

    @coins_group.command(name="leaderboard", description="Show who has the most BeanCoins.")
    @app_commands.describe(quiet=QUIET_HELP)
    @requires_feature("coins")
    @in_bot_channel()
    async def leaderboard(self, interaction: discord.Interaction, quiet: bool = False):
        assert interaction.guild_id is not None
        accounts = await self.services.coins.leaderboard(interaction.guild_id)
        if not accounts:
            await interaction.response.send_message("No one has any BeanCoins yet!", ephemeral=quiet)
            return
        lines = ["**BeanCoin Leaderboard**"] + [
            f"{i}. <@{a.user_id}>: {self.services.currency.format(a.balance)}" for i, a in enumerate(accounts, 1)
        ]
        await interaction.response.send_message("\n".join(lines), allowed_mentions=discord.AllowedMentions.none(), ephemeral=quiet)
