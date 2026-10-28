"""
BeanCoins: earned when others react to your messages with the coin emoji.

- /coins wealth shows a balance. Checking yourself quietly also shows your ShadowBean Coins,
  which nobody else ever sees.
- /coins launder turns ShadowBeans into regular coins. Only you see the reply. Do it a bit at a
  time if you'd rather nobody noticed your balance jump. The bot channel is told that someone
  laundered money, but not who (turn off with announce_launders).
- /coins transfer gives another member some of your coins. A quiet transfer is DMed to the
  recipient instead of announced. A note goes along by DM, never in the announcement. The
  recipient is told who sent it unless the sender chose to stay anonymous.
"""

import datetime

import discord
from discord import app_commands

from beans_bot.cogs.base import QUIET_HELP, BeansCog, in_bot_channel, requires_feature
from beans_bot.config.schema import CoinsConfig
from beans_bot.services import emoji, scripts
from beans_bot.services.coins import CantPayYourself, InsufficientFunds, NothingToLaunder, ReactionGrantResult
from beans_bot.services.errors import UserFacingError


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
        text = f"**Current wealth for {target.display_name}**\n- Balance: {currency.format(balance)}\n"
        # ShadowBeans only show to their owner, and only when nobody else can see the reply.
        if quiet and target.id == interaction.user.id:
            shadow = await self.services.coins.shadow_balance(interaction.guild_id, target.id)
            text += f"- ShadowBean Coins: {currency.format(shadow)}\n-# Only you can see these. `/coins launder` brings them into the light.\n"
        text += f"\n**Overall**\n- Total Received: {currency.format(received)}\n- Total Gifted to others: {currency.format(gifted)}"
        await interaction.response.send_message(text, ephemeral=quiet)

    @coins_group.command(name="launder", description="Turn your ShadowBean Coins into regular coins. Only you see the reply.")
    @app_commands.describe(amount='How many ShadowBeans to launder, like "5" or "3 cheddar", or "all"')
    @requires_feature("coins")
    async def launder(self, interaction: discord.Interaction, amount: str):
        assert interaction.guild_id is not None
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.coins is not None
        currency = self.services.currency
        try:
            laundered, left = await self.services.coins.launder(interaction.guild_id, interaction.user.id, currency.parse_bet(amount))
        except NothingToLaunder:
            raise UserFacingError("Your shadow pocket is empty. Go snitch on someone, or rob them. Then we'll talk.")
        except InsufficientFunds as e:
            raise UserFacingError(
                f"You want to launder {currency.format(e.needed)}, but you only have {currency.format(e.balance)} in ShadowBeans. "
                "Even the mob can't clean money you don't have."
            )
        values = {"amount": currency.format(laundered), "shadow_left": currency.format(left)}
        text = scripts.pick(config.coins.launder_scripts, **values)
        text = text or f"Washed and pressed. {values['amount']} is in your wallet now, and {values['shadow_left']} is still in the shadows."
        text += "\n-# Your balance just went up. Try not to look so pleased about it."
        await interaction.response.send_message(text, ephemeral=True)
        if config.coins.announce_launders and interaction.guild is not None:
            await self.announce_launder(interaction.guild, config.coins, values["amount"])

    async def announce_launder(self, guild: discord.Guild, config: CoinsConfig, amount: str) -> None:
        """Tell the bot channel some money got cleaned. Never says who."""
        guild_config = self.guild_config(guild.id)
        if guild_config is None or guild_config.bot_channel_id is None:
            return
        channel = guild.get_channel(guild_config.bot_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            self.logger.error(f"Bot channel {guild_config.bot_channel_id} not found in guild {guild.id}")
            return
        text = scripts.pick(config.launder_announce_scripts, amount=amount)
        text = text or "Somebody just laundered some ShadowBeans. The money is clean. The hands are not. Nobody saw anything."
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.logger.warning(f"Could not announce a launder in guild {guild.id}")

    @coins_group.command(name="transfer", description="Give another member some of your BeanCoins.")
    @app_commands.describe(
        member="Who gets the coins",
        amount='How much, like "5" or "3 cheddar"',
        message="A note for them. It always goes by DM, never in the announcement.",
        anonymous="Don't tell anyone it was you, not even them",
        quiet="Don't announce it. They get a DM instead.",
    )
    @requires_feature("coins")
    @in_bot_channel()
    async def transfer(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: str,
        message: app_commands.Range[str, 1, 1500] | None = None,
        anonymous: bool = False,
        quiet: bool = False,
    ):
        assert interaction.guild_id is not None
        config = self.guild_config(interaction.guild_id)
        assert config is not None and config.coins is not None
        currency = self.services.currency
        coins = currency.parse(amount)
        if member.bot:
            raise UserFacingError("Bots don't take tips. If you want to give me money, lose a bet like everyone else.")
        try:
            await self.services.coins.transfer(interaction.guild_id, interaction.user.id, member.id, coins)
        except CantPayYourself:
            raise UserFacingError("Moving money from your left pocket to your right pocket isn't a transfer. It's fidgeting.")
        except InsufficientFunds as e:
            raise UserFacingError(f"You tried to send {currency.format(coins)} but only have {currency.format(e.balance)}. Generous of you, in theory.")

        values = {"sender": interaction.user.mention, "recipient": member.mention, "amount": currency.format(coins)}
        ping = discord.AllowedMentions(users=[member])
        if not quiet and not anonymous:
            text = scripts.pick(config.coins.transfer_scripts, **values)
            text = text or f"{values['sender']} just handed {values['recipient']} {values['amount']}. No strings attached. Probably."
            await interaction.response.send_message(text, allowed_mentions=ping)
        else:
            secret = "They'll never know it was you." if anonymous else "They'll know it was you."
            note = " Your note goes by DM." if message else ""
            await interaction.response.send_message(f"Done. {values['amount']} slipped to {member.mention}. {secret}{note}", ephemeral=True)
        if not quiet and anonymous:
            # Sent separately, because replying to the command would show who ran it.
            text = scripts.pick(config.coins.transfer_anonymous_scripts, recipient=values["recipient"], amount=values["amount"])
            text = text or f"An anonymous benefactor just handed {values['recipient']} {values['amount']}. Nobody knows who. Nobody will ever know."
            try:
                if isinstance(interaction.channel, discord.abc.Messageable):
                    await interaction.channel.send(text, allowed_mentions=ping)
            except discord.HTTPException:
                self.logger.warning(f"Could not announce an anonymous transfer to {member.id} in guild {interaction.guild_id}")

        # The DM: always for a quiet transfer, and for a public one only when there's a note.
        if quiet and anonymous:
            text = scripts.pick(config.coins.transfer_anonymous_dm_scripts, amount=values["amount"])
            text = text or f"Someone slipped you {values['amount']} and won't say who. Don't look behind you."
        elif quiet:
            text = scripts.pick(config.coins.transfer_quiet_scripts, sender=values["sender"], amount=values["amount"])
            text = text or f"{values['sender']} slipped you {values['amount']} under the table. Act natural."
        elif message:
            sender = "Someone who won't say who" if anonymous else values["sender"]
            text = f"{sender} sent a note with the {values['amount']}:"
        else:
            return
        if message:
            text += f"\n>>> {message}"
        try:
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.logger.info(f"Could not DM {member.id} about a transfer from {interaction.user.id}")
            if message:
                await interaction.followup.send(
                    f"{member.mention} has their DMs locked, so your note didn't make it. The coins did. Write it on a napkin.", ephemeral=True
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
