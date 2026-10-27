"""
Admin tools, for server admins. Every reply here is ephemeral, so nobody sees who is working the puppet.
- /admin say posts a message as the bot in a channel.
- /admin dm sends a member a DM from the bot.
- /admin botbet has the bot (the house) bet in a prediction market thread, after a confirm button.
- /admin give and /admin take add or remove a member's coins or ShadowBeans. The house is the
  other side, so coins never appear from nowhere or vanish. The member isn't told.
"""

from __future__ import annotations

import discord
from discord import app_commands

from beans_bot.cogs.base import BeansCog, admin_only, requires_feature
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.errors import UserFacingError

# Discord's message length limit.
MessageText = app_commands.Range[str, 1, 2000]
# Admin puppeting can ping members, but never @everyone or roles by accident.
PUPPET_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


class ConfirmBotBet(discord.ui.View):
    def __init__(self, cog: BeansCog, owner_id: int, thread_id: int, choice: str, amount: int):
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
        amount = self.cog.services.currency.format(self.amount)
        await interaction.response.edit_message(content=f'Done. The bot bet {amount} on "{self.choice}".', view=None)
        self.cog.logger.info(f"Admin {interaction.user.id} had the house bet {self.amount} on {self.choice!r} in market {self.thread_id}")
        # Same voice as the bot's automatic bets, so it looks like the bot's own idea.
        thread = self.cog.bot.get_channel(self.thread_id)
        if isinstance(thread, discord.Thread):
            try:
                await thread.send(f"I am joining the fray! I bet {amount} on {self.choice}!")
            except discord.HTTPException:
                self.cog.logger.warning(f"Could not announce the house bet in market {self.thread_id}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Bot bet canceled.", view=None)


class AdminCog(BeansCog):
    # default_permissions hides the group from members' command list. admin_only is the real check.
    admin = app_commands.Group(
        name="admin",
        description="Admin tools.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @admin.command(name="sync", description="Re-sync slash commands with Discord.")
    @admin_only()
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        synced = await self.bot.tree.sync()
        await interaction.followup.send(f"Synced {len(synced)} commands.", ephemeral=True)

    @admin.command(name="say", description="Post a message as the bot. Only you see that you sent it.")
    @app_commands.describe(message="What the bot says", channel="Where to say it (defaults to here)")
    @admin_only()
    async def say(
        self, interaction: discord.Interaction, message: MessageText, channel: discord.TextChannel | discord.Thread | None = None
    ):
        target = channel or interaction.channel
        if not isinstance(target, discord.abc.Messageable):
            raise UserFacingError("I can't talk in here. Pick a text channel and I'll be your mouthpiece.")
        try:
            sent = await target.send(message, allowed_mentions=PUPPET_MENTIONS)
        except discord.HTTPException as e:
            raise UserFacingError(f"Discord gagged me ({e.text or e.status}). Check my permissions in that channel.")
        self.logger.info(f"Admin {interaction.user.id} had the bot say in channel {sent.channel.id}: {message!r}")
        await interaction.response.send_message(f"Said it. Your hand is invisible. {sent.jump_url}", ephemeral=True)

    @admin.command(name="dm", description="Send a member a DM from the bot. Only you see that you sent it.")
    @app_commands.describe(member="Who gets the DM", message="What the bot says")
    @admin_only()
    async def dm(self, interaction: discord.Interaction, member: discord.User, message: MessageText):
        if member.bot:
            raise UserFacingError("Bots don't read their DMs. Trust me.")
        try:
            await member.send(message, allowed_mentions=PUPPET_MENTIONS)
        except discord.Forbidden:
            raise UserFacingError(f"{member.mention} has their DMs locked. Whisper denied.")
        except discord.HTTPException as e:
            raise UserFacingError(f"Discord ate the DM ({e.text or e.status}).")
        self.logger.info(f"Admin {interaction.user.id} had the bot DM {member.id}: {message!r}")
        await interaction.response.send_message(f"Slid into {member.mention}'s DMs. They'll never know it was you.", ephemeral=True)

    async def _market_choice_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        market = await self.services.markets.get(interaction.channel_id) if interaction.channel_id else None
        if market is None:
            return []
        return [app_commands.Choice(name=c, value=c) for c in market.choices if current.lower() in c][:25]

    @admin.command(name="botbet", description="Have the bot bet in this prediction market. Only you see that you did it.")
    @app_commands.describe(choice="The choice the bot bets on", amount="How many coins the bot bets")
    @admin_only()
    async def botbet(self, interaction: discord.Interaction, choice: str, amount: str):
        if interaction.channel_id is None or not isinstance(interaction.channel, discord.Thread):
            raise UserFacingError("Use this in a prediction market thread. The bot can't bet on thin air.")
        thread_id = interaction.channel_id
        coins = self.services.currency.parse(amount)
        market = await self.services.markets.get(thread_id)
        if market is None:
            raise UserFacingError("This thread isn't a prediction market.")
        if market.status != "open":
            raise UserFacingError(f"This market is currently {market.status}.")
        choice = choice.lower()
        if choice not in market.choices:
            raise UserFacingError(f"Invalid choice '{choice}'. Valid choices are: {', '.join(market.choices)}")

        view = ConfirmBotBet(self, interaction.user.id, thread_id, choice, coins)
        # Ephemeral, so nobody sees who is pulling the bot's strings. The bet itself is announced on confirm.
        await interaction.response.send_message(
            f'I will bet {self.services.currency.format(coins)} on "{choice}". Proceed?', view=view, ephemeral=True
        )

    botbet.autocomplete("choice")(_market_choice_autocomplete)

    @admin.command(name="give", description="Give a member coins or ShadowBeans from the house. Only you see it.")
    @app_commands.describe(member="Who gets them", amount='How much, like "5" or "3 cheddar"', shadow="Give ShadowBeans instead of regular coins")
    @requires_feature("coins")
    @admin_only()
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: str, shadow: bool = False):
        assert interaction.guild_id is not None
        currency = self.services.currency
        coins = currency.parse(amount)
        if member.bot:
            raise UserFacingError("The house doesn't pay itself. That's called embezzlement.")
        balance = await self.services.coins.admin_give(interaction.guild_id, interaction.user.id, member.id, coins, shadow=shadow)
        pocket = "ShadowBeans" if shadow else "balance"
        await interaction.response.send_message(
            f"The house slipped {member.mention} {currency.format(coins)}. Their {pocket} is now {currency.format(balance)}. They won't be told.",
            ephemeral=True,
        )

    @admin.command(name="take", description="Take a member's coins or ShadowBeans for the house. Only you see it.")
    @app_commands.describe(member="Who loses them", amount='How much, like "5" or "3 cheddar"', shadow="Take ShadowBeans instead of regular coins")
    @requires_feature("coins")
    @admin_only()
    async def take(self, interaction: discord.Interaction, member: discord.Member, amount: str, shadow: bool = False):
        assert interaction.guild_id is not None
        currency = self.services.currency
        coins = currency.parse(amount)
        if member.bot:
            raise UserFacingError("Robbing the house is what heists are for.")
        pocket = "ShadowBeans" if shadow else "balance"
        try:
            balance = await self.services.coins.admin_take(interaction.guild_id, interaction.user.id, member.id, coins, shadow=shadow)
        except InsufficientFunds as e:
            raise UserFacingError(
                f"{member.mention} only has {currency.format(e.balance)} in their {pocket}, not {currency.format(coins)}. "
                "You can't squeeze blood from a bean."
            )
        await interaction.response.send_message(
            f"Confiscated {currency.format(coins)} from {member.mention}. Their {pocket} is now {currency.format(balance)}. They won't be told.",
            ephemeral=True,
        )
