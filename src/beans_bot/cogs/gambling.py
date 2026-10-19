"""
Prediction markets. A new thread in the guild's gambling forum, whose first
message lists `Choices:`, becomes a betting pool. Members bet BeanCoins with
/bet, and the thread's creator closes, cancels or resolves it.
- /bet place and /bet add take "all in" as an amount to bet the member's whole balance.
- /bet swap moves a member's whole bet to another choice, but it pings
  @everyone in the bot channel and sends them to jail.
"""

from __future__ import annotations

import asyncio
import random

import discord
from discord import app_commands

from beans_bot.cogs.base import QUIET_HELP, BeansCog, requires_feature
from beans_bot.currency import AmountError
from beans_bot.services import scripts
from beans_bot.services.errors import UserFacingError
from beans_bot.services.jail import JailOutcome
from beans_bot.services.markets import InvalidMarket, parse_choices


def market_instructions(choices: list[str], creator_id: int) -> str:
    lines = ["Time to gamble!", "", "Place a bet with `/bet place`:"]
    lines += [f"- `/bet place choice:{choice} amount:<coins>`" for choice in choices]
    lines += [
        "You may only choose one option.",
        "However, if you want to lose more money you can use `/bet add` to increase your bet.",
        "Changed your mind? `/bet swap` moves your whole bet, but it tells @everyone and sends you to jail.",
        "Check the pool and odds: `/bet stats`",
        "",
        f"Commands for the prediction market manager, <@{creator_id}>:",
        "- Close betting and refund everyone: `/bet cancel`",
        "- Close betting without naming a winner yet: `/bet close`",
        "- Close betting and name a winner: `/bet resolve`",
    ]
    return "\n".join(lines)


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

    async def _amount_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        # Suggestions only: members can still submit whatever they typed.
        currency = self.services.currency
        options = []
        if current.strip():
            try:
                parsed = currency.parse_bet(current)
                label = "All in" if parsed is None else currency.format(parsed)
            except AmountError as e:
                label = str(e)
            options.append(app_commands.Choice(name=label[:100], value=current[:100]))
        if interaction.guild_id is not None:
            balance = await self.services.coins.balance(interaction.guild_id, interaction.user.id)
            options.append(app_commands.Choice(name=f"All in ({currency.format(currency.floor(max(balance, 0)))})", value="all in"))
        return options

    def _thread_id(self, interaction: discord.Interaction) -> int:
        if interaction.channel_id is None or not isinstance(interaction.channel, discord.Thread):
            raise UserFacingError("Use this in a prediction market thread.")
        return interaction.channel_id

    @bet.command(name="place", description="Bet on one of this market's choices.")
    @app_commands.describe(choice="What you think will happen", amount="How many coins to bet, like 10, 2.5 or all in")
    async def place(self, interaction: discord.Interaction, choice: str, amount: str):
        coins = self.services.currency.parse_bet(amount)
        bet = await self.services.markets.place_bet(self._thread_id(interaction), interaction.user.id, choice, coins)
        flourish = " ALL IN! Every last bean." if coins is None else ""
        await interaction.response.send_message(
            f"{interaction.user.mention} bet {self.services.currency.format(bet.amount)} on **{bet.choice}**!{flourish}"
        )

    place.autocomplete("choice")(_choice_autocomplete)
    place.autocomplete("amount")(_amount_autocomplete)

    @bet.command(name="add", description="Add more coins to your bet.")
    @app_commands.describe(amount="How many coins to add, like 10, 2.5 or all in")
    async def add(self, interaction: discord.Interaction, amount: str):
        coins = self.services.currency.parse_bet(amount)
        bet, added = await self.services.markets.add_to_bet(self._thread_id(interaction), interaction.user.id, coins)
        currency = self.services.currency
        flourish = " ALL IN! Every last bean." if coins is None else ""
        await interaction.response.send_message(
            f"{interaction.user.mention} added {currency.format(added)} to their bet on **{bet.choice}**!{flourish} "
            f"New total: {currency.format(bet.amount)}."
        )

    add.autocomplete("amount")(_amount_autocomplete)

    @bet.command(
        name="swap", description="Move your whole bet to another choice. Pings @everyone and sends you to jail."
    )
    @app_commands.describe(choice="The choice to move your whole bet to. Everyone will know, and you go to jail.")
    @requires_feature("jail")
    async def swap(self, interaction: discord.Interaction, choice: str):
        assert interaction.guild is not None
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.jail is not None and config.gambling is not None
        thread_id = self._thread_id(interaction)
        # Swapping from a cell would be free, so it isn't allowed.
        if await self.services.jail.is_jailed(interaction.guild.id, interaction.user.id):
            raise UserFacingError("You're already in jail. Serve your time before you flip-flop again.")

        swap = await self.services.markets.swap_bet(thread_id, interaction.user.id, choice)
        currency = self.services.currency
        await interaction.response.send_message(
            f"{interaction.user.mention} has abandoned **{swap.old_choice}** and moved "
            f"{currency.format(swap.bet.amount)} to **{swap.bet.choice}**. Traitor. To the stocks with you!"
        )

        result = await self.services.jail.imprison(
            interaction.guild,
            config.jail,
            interaction.user.id,
            channel_id=thread_id,
            offending_message_id=None,
            audit_reason="Swapped their bet.",
        )
        if result.outcome is not JailOutcome.JAILED:
            self.logger.warning(f"Could not jail {interaction.user.id} for swapping in guild {interaction.guild.id}: {result.outcome}")

        values = {
            "member": interaction.user.mention,
            "old_choice": swap.old_choice,
            "new_choice": swap.bet.choice,
            "amount": currency.format(swap.bet.amount),
            "market": f"<#{thread_id}>",
        }
        text = scripts.pick(config.gambling.swap_scripts, **values) or (
            f"{values['member']} switched their {values['amount']} bet in {values['market']} "
            f"from **{values['old_choice']}** to **{values['new_choice']}**. Shame them."
        )
        channel = interaction.guild.get_channel(config.bot_channel_id) if config.bot_channel_id else None
        if not isinstance(channel, discord.abc.Messageable):
            channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(f"@everyone {text}", allowed_mentions=discord.AllowedMentions(everyone=True, users=True))
        except discord.HTTPException:
            self.logger.warning(f"Could not announce {interaction.user.id}'s bet swap in guild {interaction.guild.id}")

    swap.autocomplete("choice")(_choice_autocomplete)

    @bet.command(name="stats", description="Show this market's pool and odds.")
    @app_commands.describe(quiet=QUIET_HELP)
    async def stats(self, interaction: discord.Interaction, quiet: bool = False):
        stats = await self.services.markets.stats(self._thread_id(interaction))
        if stats.pool == 0:
            await interaction.response.send_message("No bets have been placed yet.", ephemeral=quiet)
            return
        currency = self.services.currency
        lines = [f"**Current Pool:** {currency.format(stats.pool)}", "", "**Odds:**"]
        for choice, total in stats.by_choice.items():
            if total == 0:
                lines.append(f"- **{choice}**: nothing bet (No payout)")
            else:
                lines.append(f"- **{choice}**: {currency.format(total)} bet (Payout ratio: {stats.pool / total:.2f}x)")
        await interaction.response.send_message("\n".join(lines), ephemeral=quiet)

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
