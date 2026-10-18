"""
Prediction markets. A new thread in the guild's gambling forum, whose first
message lists `Choices:`, becomes a betting pool. Members bet BeanCoins with
/bet, and the thread's creator closes, cancels or resolves it.
"""

from __future__ import annotations

import asyncio
import random

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, owner_only
from beans_bot.services.errors import UserFacingError
from beans_bot.services.markets import InvalidMarket, parse_choices


def market_instructions(choices: list[str], creator_id: int) -> str:
    lines = ["Time to gamble!", "", "Place a bet with `/bet place`:"]
    lines += [f"- `/bet place choice:{choice} amount:<coins>`" for choice in choices]
    lines += [
        "You may only choose one option.",
        "However, if you want to lose more money you can use `/bet add` to increase your bet.",
        "Check the pool and odds: `/bet stats`",
        "",
        f"Commands for the prediction market manager, <@{creator_id}>:",
        "- Close betting and refund everyone: `/bet cancel`",
        "- Close betting without naming a winner yet: `/bet close`",
        "- Close betting and name a winner: `/bet resolve`",
    ]
    return "\n".join(lines)


class ConfirmBotBet(discord.ui.View):
    def __init__(self, cog: GamblingCog, owner_id: int, thread_id: int, choice: str, amount: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id
        self.thread_id = thread_id
        self.choice = choice
        self.amount = amount

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Place bet", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        try:
            await self.cog.services.markets.house_bet(self.thread_id, self.choice, self.amount)
        except UserFacingError as e:
            await interaction.response.edit_message(content=str(e), view=None)
            return
        text = f'Bet placed successfully on "{self.choice}" for {self.cog.services.currency.format(self.amount)}!'
        await interaction.response.edit_message(content=text, view=None)
        thread = self.cog.bot.get_channel(self.thread_id)
        if isinstance(thread, discord.Thread):
            await thread.send(text)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Bot bet canceled.", view=None)


class GamblingCog(BeansCog):
    bet = app_commands.Group(name="bet", description="Bet BeanCoins on a prediction market thread.", guild_only=True)

    def forum_channel_id(self, guild_id: int | None) -> int | None:
        config = self.guild_config(guild_id)
        return config.gambling.forum_channel_id if config and config.gambling else None

    # --- Market threads -----------------------------------------------------------------

    @BeansCog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        forum_id = self.forum_channel_id(thread.guild.id)
        if forum_id is None or thread.parent_id != forum_id:
            return
        config = self.guild_config(thread.guild.id)
        assert config is not None and config.gambling is not None

        # The starter message isn't always available the instant the thread is created.
        await asyncio.sleep(1)
        try:
            starter = thread.starter_message or await thread.fetch_message(thread.id)
        except discord.NotFound:
            return

        try:
            choices = parse_choices(starter.content)
        except InvalidMarket as e:
            await thread.send(f"This is not a valid prediction. {e} Closing thread.")
            await thread.edit(locked=True, archived=True)
            return

        await self.services.markets.create(
            guild_id=thread.guild.id, thread_id=thread.id, creator_id=starter.author.id, choices=choices
        )
        await thread.send(market_instructions(choices, starter.author.id))

        amount = await self.services.markets.auto_bet_amount(thread.guild.id, config.gambling.bot_betting)
        if amount is not None:
            choice = random.choice(choices)
            await self.services.markets.house_bet(thread.id, choice, amount)
            await thread.send(f"I am joining the fray! I bet {self.services.currency.format(amount)} on {choice}!")

    @BeansCog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent):
        forum_id = self.forum_channel_id(payload.guild_id)
        if forum_id is None or payload.parent_id != forum_id:
            return
        market = await self.services.markets.get(payload.thread_id)
        if market is None:
            return
        try:
            refunded = await self.services.markets.cancel(payload.thread_id, user_id=None)
        except UserFacingError:
            return  # Already resolved or canceled
        self.logger.info(f"Market {payload.thread_id} canceled because its thread was deleted; refunded {refunded} bets")

    # --- /bet ---------------------------------------------------------------------------

    async def _choice_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        if interaction.channel_id is None:
            return []
        market = await self.services.markets.get(interaction.channel_id)
        if market is None:
            return []
        return [app_commands.Choice(name=c, value=c) for c in market.choices if current.lower() in c][:25]

    def _thread_id(self, interaction: discord.Interaction) -> int:
        if interaction.channel_id is None or not isinstance(interaction.channel, discord.Thread):
            raise UserFacingError("Use this in a prediction market thread.")
        return interaction.channel_id

    @bet.command(name="place", description="Bet on one of this market's choices.")
    @app_commands.describe(choice="What you think will happen", amount="How many coins to bet, like 10 or 2.5")
    async def place(self, interaction: discord.Interaction, choice: str, amount: str):
        coins = self.services.currency.parse(amount)
        bet = await self.services.markets.place_bet(self._thread_id(interaction), interaction.user.id, choice, coins)
        await interaction.response.send_message(
            f"{interaction.user.mention} bet {self.services.currency.format(bet.amount)} on **{bet.choice}**!"
        )

    place.autocomplete("choice")(_choice_autocomplete)

    @bet.command(name="add", description="Add more coins to your bet.")
    @app_commands.describe(amount="How many coins to add")
    async def add(self, interaction: discord.Interaction, amount: str):
        coins = self.services.currency.parse(amount)
        bet = await self.services.markets.add_to_bet(self._thread_id(interaction), interaction.user.id, coins)
        currency = self.services.currency
        await interaction.response.send_message(
            f"{interaction.user.mention} added {currency.format(coins)} to their bet on **{bet.choice}**! "
            f"New total: {currency.format(bet.amount)}."
        )

    @bet.command(name="stats", description="Show this market's pool and odds.")
    async def stats(self, interaction: discord.Interaction):
        stats = await self.services.markets.stats(self._thread_id(interaction))
        if stats.pool == 0:
            await interaction.response.send_message("No bets have been placed yet.")
            return
        currency = self.services.currency
        lines = [f"**Current Pool:** {currency.format(stats.pool)}", "", "**Odds:**"]
        for choice, total in stats.by_choice.items():
            if total == 0:
                lines.append(f"- **{choice}**: nothing bet (No payout)")
            else:
                lines.append(f"- **{choice}**: {currency.format(total)} bet (Payout ratio: {stats.pool / total:.2f}x)")
        await interaction.response.send_message("\n".join(lines))

    @bet.command(name="close", description="Market creator: stop accepting bets without naming a winner yet.")
    async def close(self, interaction: discord.Interaction):
        await self.services.markets.close(self._thread_id(interaction), interaction.user.id)
        await interaction.response.send_message("Betting has been closed.")

    @bet.command(name="cancel", description="Market creator: cancel the market and refund everyone.")
    async def cancel(self, interaction: discord.Interaction):
        await self.services.markets.cancel(self._thread_id(interaction), interaction.user.id)
        await interaction.response.send_message("Prediction canceled. All bets have been refunded.")

    @bet.command(name="resolve", description="Market creator: name the winning choice and pay out.")
    @app_commands.describe(winner="The choice that won")
    async def resolve(self, interaction: discord.Interaction, winner: str):
        resolution = await self.services.markets.resolve(self._thread_id(interaction), interaction.user.id, winner)
        currency = self.services.currency
        if resolution.pool == 0:
            text = "The prediction resolved, but no one bet anything!"
        elif resolution.refunded:
            text = (
                f"The prediction resolved to **{resolution.winning_choice}**. "
                "But nobody bet differently so everyone gets their beans back."
            )
        else:
            lines = [f"The prediction resolved to **{resolution.winning_choice}**! Win amounts:"]
            lines += [f"- <@{user_id}>: {currency.format(amount)}" for user_id, amount in resolution.payouts]
            lines.append(f"And the house took {currency.format(resolution.house_remainder)}.")
            text = "\n".join(lines)
        await interaction.response.send_message(text)

    resolve.autocomplete("winner")(_choice_autocomplete)

    # --- /botbet (secret, owner only, DMs) ----------------------------------------------

    @app_commands.command(name="botbet", description="Owner only: have the bot place a bet.")
    @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
    @app_commands.describe(
        guild_id="ID of the server the market is in",
        thread_id="ID of the market's thread",
        choice="The choice to bet on",
        amount="How many coins the bot bets",
    )
    @owner_only()
    async def botbet(self, interaction: discord.Interaction, guild_id: str, thread_id: str, choice: str, amount: str):
        try:
            guild_id_int, thread_id_int = int(guild_id), int(thread_id)
        except ValueError:
            raise UserFacingError("Server and thread IDs must be numbers.")
        coins = self.services.currency.parse(amount)
        if self.bot.get_guild(guild_id_int) is None:
            raise UserFacingError("Server not found.")
        market = await self.services.markets.get(thread_id_int)
        if market is None or market.guild_id != guild_id_int:
            raise UserFacingError("Prediction market not found for that thread ID.")
        if market.status != "open":
            raise UserFacingError(f"This market is currently {market.status}.")
        choice = choice.lower()
        if choice not in market.choices:
            raise UserFacingError(f"Invalid choice '{choice}'. Valid choices are: {', '.join(market.choices)}")

        thread = self.bot.get_channel(thread_id_int)
        thread_name = getattr(thread, "name", None) or f"Thread {thread_id_int}"
        view = ConfirmBotBet(self, interaction.user.id, thread_id_int, choice, coins)
        await interaction.response.send_message(
            f'I will bet on the bet "{thread_name}" on choice "{choice}" for {self.services.currency.format(coins)}. Proceed?',
            view=view,
        )
