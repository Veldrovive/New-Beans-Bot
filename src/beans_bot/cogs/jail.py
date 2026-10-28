"""
Jail: enough jail emoji reactions on a message put its author in the stocks.

- The jailed member gets the jail role, a tomato, and an announcement.
- The first thing they say in jail gets a humiliating reply; later messages
  sometimes get a tomato.
- At the mega jail threshold, the first reply to the bot's prompt becomes
  the member's nickname.
- Sentences end on a timer, or early with /jail bribe.
- /jail frame pays the house to jail someone else. Paying the anonymous price
  keeps the framer's name out of the announcement.
"""

from __future__ import annotations

import random
import re

import discord
from discord import app_commands
from discord.ext import tasks

from beans_bot.cogs.base import QUIET_HELP, BeansCog, in_bot_channel, requires_feature
from beans_bot.config.schema import JailConfig
from beans_bot.db.models import CoinTransactionKind, JailReason
from beans_bot.services import emoji, scripts
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.jail import AlreadyJailed, JailOutcome, get_member

MAX_NICKNAME_LENGTH = 32


def renamed_nickname(current_display_name: str, new_base: str) -> str:
    """The new nickname, keeping any "(...)" at the end of the current name and fitting Discord's limit."""
    match = re.match(r"^(.*?)\s*(\(.*?\))?$", current_display_name)
    parenthetical = match.group(2) if match and match.group(2) else ""
    new_base = new_base.strip()
    if parenthetical:
        return f"{new_base[: MAX_NICKNAME_LENGTH - len(parenthetical) - 1]} {parenthetical}"
    return new_base[:MAX_NICKNAME_LENGTH]


class JailCog(BeansCog):
    jail_group = app_commands.Group(name="jail", description="The stocks.", guild_only=True)

    async def cog_load(self):
        self.check_jail_timers.start()

    async def cog_unload(self):
        self.check_jail_timers.cancel()

    def jail_config(self, guild_id: int | None) -> JailConfig | None:
        config = self.guild_config(guild_id)
        return config.jail if config else None

    def announce_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        config = self.guild_config(guild.id)
        if config is None or config.bot_channel_id is None:
            return None
        channel = guild.get_channel(config.bot_channel_id)
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def throw_tomato(self, message: discord.Message, config: JailConfig) -> None:
        try:
            await emoji.react(message, config.tomato_emoji)
        except discord.HTTPException:
            self.logger.info(f"Failed to add tomato to message {message.id}")

    # --- Jailing ------------------------------------------------------------------------

    @BeansCog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or self.is_own_user(payload.user_id):
            return
        config = self.jail_config(payload.guild_id)
        if config is None or not emoji.matches(payload.emoji, config.jail_emoji):
            return
        message = await self.fetch_message(payload.channel_id, payload.message_id)
        if message is None or message.guild is None:
            return

        count = next((r.count for r in message.reactions if emoji.matches(r.emoji, config.jail_emoji)), 0)
        self.logger.info(f"Jail reaction count for message {message.id} in guild {payload.guild_id}: {count}")

        if count >= config.mega_jail_threshold:
            await self.mega_jail(message, config)
        if count >= config.to_jail_threshold:
            await self.jail(message, config)

    async def jail(self, message: discord.Message, config: JailConfig) -> None:
        assert message.guild is not None
        result = await self.services.jail.jail(message.guild, config, message.author.id, message)
        if result.outcome is JailOutcome.JAILED:
            assert result.member is not None
            await self.throw_tomato(message, config)
            if text := scripts.pick(config.on_jailed_scripts, jailed_user=result.member.mention):
                await message.reply(text)
        elif result.outcome is JailOutcome.ALREADY_JAILED:
            if text := scripts.pick(config.already_in_jail_scripts, jailed_user=message.author.mention):
                await message.channel.send(text)
        elif result.outcome is JailOutcome.MISSING_PERMISSIONS:
            await message.reply("I lack the power to jail this person (Missing Permissions).")
        elif result.outcome is JailOutcome.ROLE_NOT_FOUND:
            await message.reply("System Error: Jail role not found.")

    async def mega_jail(self, message: discord.Message, config: JailConfig) -> None:
        assert message.guild is not None
        if await self.services.jail.has_pending_rename(message.guild.id, message.id):
            return
        text = scripts.pick(config.mega_jail_scripts, jailed_user=message.author.mention)
        if text is None:
            return
        prompt = await message.channel.send(text)
        await self.services.jail.create_pending_rename(
            guild_id=message.guild.id,
            prompt_message_id=prompt.id,
            target_user_id=message.author.id,
            offending_message_id=message.id,
        )
        self.logger.info(f"Mega jailed {message.author.id} in guild {message.guild.id}")

    # --- Messages from jailed members and rename replies --------------------------------

    @BeansCog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        config = self.jail_config(message.guild.id)
        if config is None:
            return
        await self.humiliate(message, config)
        await self.handle_rename_reply(message)

    async def humiliate(self, message: discord.Message, config: JailConfig) -> None:
        assert message.guild is not None
        sentence = await self.services.jail.current_sentence(message.guild.id, message.author.id)
        if sentence is None or sentence.id is None:
            return

        if not sentence.humiliated and await self.services.jail.mark_humiliated(sentence.id):
            self.logger.info(f"Humiliating {message.author.id} in guild {message.guild.id}")
            if text := scripts.pick(config.humiliate_scripts, jailed_user=message.author.mention):
                try:
                    await message.reply(text)
                except discord.HTTPException:
                    await message.channel.send(text)
            await self.throw_tomato(message, config)
        elif random.random() < config.subsequent_tomato_probability:
            await self.throw_tomato(message, config)

    async def handle_rename_reply(self, message: discord.Message) -> None:
        if message.guild is None or message.reference is None or message.reference.message_id is None:
            return
        rename = await self.services.jail.claim_pending_rename(message.guild.id, message.reference.message_id)
        if rename is None:
            return

        prompt = await self.fetch_message(message.channel.id, rename.prompt_message_id)
        target = await get_member(message.guild, rename.target_user_id)
        if target is None:
            return

        if target.id == message.guild.owner_id:
            result_text = "The server owner is the supreme entity. I cannot harm them!"
        else:
            new_nickname = renamed_nickname(target.display_name, message.clean_content)
            try:
                await target.edit(nick=new_nickname)
                result_text = f"**{message.author.display_name} has dubbed them: {new_nickname}!**"
            except discord.Forbidden:
                self.logger.error(f"Failed to rename {target.id} in guild {message.guild.id}")
                result_text = (
                    f"**{message.author.display_name} tried to dub them {new_nickname}, "
                    f"but I lack the power to rename this user!**"
                )

        if prompt is not None:
            try:
                await prompt.edit(content=f"~~{prompt.content}~~ \n\n{result_text}")
            except (discord.NotFound, discord.Forbidden):
                pass

    # --- Timers -------------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def check_jail_timers(self):
        await self.release_due()
        await self.fix_desynced_roles()

    @check_jail_timers.before_loop
    async def before_check_jail_timers(self):
        await self.bot.wait_until_ready()

    async def release_due(self) -> None:
        for sentence in await self.services.jail.due_for_release():
            config = self.jail_config(sentence.guild_id)
            guild = self.bot.get_guild(sentence.guild_id)
            if config is None or guild is None:
                continue
            member = await self.services.jail.release(
                guild, config, sentence.user_id, reason="served", audit_reason="Served their time in the stocks."
            )
            channel = self.announce_channel(guild)
            if member is not None and channel is not None:
                if text := scripts.pick(config.release_scripts, jailed_user=member.mention):
                    await channel.send(text)

    async def fix_desynced_roles(self) -> None:
        """Members with the jail role but no sentence are either set free or jailed again."""
        for guild in self.bot.guilds:
            config = self.jail_config(guild.id)
            if config is None:
                continue
            role = guild.get_role(config.jailed_role_id)
            channel = self.announce_channel(guild)
            if role is None or channel is None:
                continue
            for member in role.members:
                if await self.services.jail.is_jailed(guild.id, member.id):
                    continue
                self.logger.info(f"{member.id} has the jail role but no sentence in guild {guild.id}")
                if random.random() < config.desync_release_probability:
                    if await self.services.jail.remove_role(guild, config, member, "Clerical error: not in the jail records."):
                        if text := scripts.pick(config.scot_free_scripts, jailed_user=member.mention):
                            await channel.send(text)
                else:
                    sentence = await self.services.jail.start_sentence(
                        guild_id=guild.id,
                        user_id=member.id,
                        channel_id=channel.id,  # type: ignore[attr-defined]
                        offending_message_id=None,
                        length=config.jail_length,
                        reason=JailReason.DESYNC,
                    )
                    if sentence is not None:
                        if text := scripts.pick(config.forgotten_prisoner_scripts, jailed_user=member.mention):
                            await channel.send(text)

    # --- Commands -----------------------------------------------------------------------

    @jail_group.command(name="bribe", description="Bribe the bot to let you out of jail.")
    @requires_feature("jail")
    @in_bot_channel()
    async def bribe(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        config = self.jail_config(interaction.guild_id)
        assert config is not None
        if config.bribe is None:
            await interaction.response.send_message("The guards here can't be bought.", ephemeral=True)
            return
        if not await self.services.jail.is_jailed(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message("You are not currently in jail!")
            return

        names = {"bot_name": self.bot.user.display_name if self.bot.user else "the bot", "jailed_user": interaction.user.mention}
        try:
            await self.services.coins.pay_house(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
                amount=config.bribe.cost,
                kind=CoinTransactionKind.BRIBE,
            )
        except InsufficientFunds:
            text = scripts.pick(config.bribe.failure_scripts, **names) or "Not enough coins!"
            await interaction.response.send_message(text)
            # Throw a tomato for trying to bribe without enough coins.
            await self.throw_tomato(await interaction.original_response(), config)
            return

        await self.services.jail.release(
            interaction.guild, config, interaction.user.id, reason="bribe", audit_reason="Bribed the bot."
        )
        await interaction.response.send_message(scripts.pick(config.bribe.success_scripts, **names) or "You are free.")

    @jail_group.command(name="frame", description="Pay the bot to send someone to jail. Pay extra to keep your name out of it.")
    @app_commands.describe(member="Who belongs in the stocks", anonymous="Pay the anonymous price so nobody knows it was you")
    @requires_feature("jail")
    @in_bot_channel()
    async def frame(self, interaction: discord.Interaction, member: discord.Member, anonymous: bool = False):
        assert interaction.guild is not None and interaction.channel_id is not None
        config = self.jail_config(interaction.guild_id)
        assert config is not None
        if config.frame is None:
            await interaction.response.send_message("The guards here only jail people the honest way: by mob rule.", ephemeral=True)
            return
        role = interaction.guild.get_role(config.jailed_role_id)
        if role is None or not role.is_assignable():
            self.logger.error(f"Can't assign jail role {config.jailed_role_id} in guild {interaction.guild.id}")
            await interaction.response.send_message("The cell door is jammed (I can't hand out the jail role). Keep your coins.", ephemeral=True)
            return

        currency = self.services.currency
        cost = config.frame.anonymous_cost if anonymous else config.frame.cost
        try:
            sentence = await self.services.jail.frame(
                guild_id=interaction.guild.id,
                framer_id=interaction.user.id,
                target_id=member.id,
                cost=cost,
                channel_id=interaction.channel_id,
                length=config.jail_length,
            )
        except AlreadyJailed:
            await interaction.response.send_message(
                f"{member.mention} is already in jail. You can't frame them twice, however much they deserve it.", ephemeral=True
            )
            return
        except InsufficientFunds as e:
            await interaction.response.send_message(
                f"Framing {member.mention}{' anonymously' if anonymous else ''} costs {currency.format(cost)} "
                f"and you have {currency.format(e.balance)}. Justice isn't cheap.",
                ephemeral=True,
            )
            return

        try:
            await member.add_roles(role, reason="Framed by a paying member.")
        except discord.HTTPException:
            self.logger.warning(f"Could not jail {member.id} for {interaction.user.id}'s frame in guild {interaction.guild.id}")
            await self.services.jail.refund_frame(
                guild_id=interaction.guild.id, framer_id=interaction.user.id, target_id=member.id, cost=cost
            )
            await interaction.response.send_message(
                f"The guards wouldn't lay a hand on {member.mention}. Your {currency.format(cost)} has been returned, "
                f"minus nothing, because I'm honest.",
                ephemeral=True,
            )
            return

        paid = currency.format(cost)
        release = discord.utils.format_dt(sentence.ends_at, "R")
        # Framing the bot itself gets its own lines. The coins land in its own pocket, which it notices.
        framing_me = self.is_own_user(member.id)
        if not anonymous:
            values = {"framer": interaction.user.mention, "paid": paid}
            if framing_me:
                text = scripts.pick(config.frame.bot_public_scripts, bot_name=member.mention, **values)
                text = text or f"{values['framer']} paid me {paid} to jail myself. I respect the hustle. To the stocks I go."
            else:
                text = scripts.pick(config.frame.public_scripts, jailed_user=member.mention, **values)
                text = text or f"{values['framer']} makes a very good point that {member.mention} should be in jail."
            await interaction.response.send_message(f"{text}\n-# {interaction.user.mention} paid {paid}. Released {release}.")
            return

        # The interaction reply would show who ran the command, so the announcement is a separate message.
        await interaction.response.send_message(
            f"Done. {member.mention} is in the stocks and your name stays out of it.\n-# Paid {paid}.", ephemeral=True
        )
        if framing_me:
            text = scripts.pick(config.frame.bot_anonymous_scripts, bot_name=member.mention, paid=paid)
            text = text or f"Someone paid me {paid} to lock myself up and won't say who. I'll find you. From jail."
        else:
            text = scripts.pick(config.frame.anonymous_scripts, jailed_user=member.mention, paid=paid)
            text = text or f"An anonymous benefactor makes a very good point that {member.mention} should be in jail."
        channel = interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(f"{text}\n-# Someone paid {paid} to stay anonymous. Released {release}.")
        except discord.HTTPException:
            self.logger.warning(f"Could not announce {member.id}'s anonymous frame in guild {interaction.guild.id}")

    @jail_group.command(name="current", description="Show who is currently in jail.")
    @app_commands.describe(quiet=QUIET_HELP)
    @requires_feature("jail")
    @in_bot_channel()
    async def current(self, interaction: discord.Interaction, quiet: bool = False):
        sentences = await self.services.jail.current_sentences(interaction.guild_id)
        if not sentences:
            await interaction.response.send_message("No one is currently in jail!", ephemeral=quiet)
            return
        mentions = ", ".join(f"<@{s.user_id}>" for s in sentences)
        await interaction.response.send_message(
            f"Currently in jail: {mentions}", allowed_mentions=discord.AllowedMentions.none(), ephemeral=quiet
        )

    @jail_group.command(name="info", description="Show someone's jail record.")
    @app_commands.describe(member="Whose record to show", quiet=QUIET_HELP)
    @requires_feature("jail")
    @in_bot_channel()
    async def info(self, interaction: discord.Interaction, member: discord.Member, quiet: bool = False):
        assert interaction.guild_id is not None
        times = await self.services.jail.times_jailed(interaction.guild_id, member.id)
        tomatoes = await self.services.jail.tomatoes_received(interaction.guild_id, member.id)
        await interaction.response.send_message(
            f"**Jail Stats for {member.display_name}**\n- Times in jail: {times}\n- Tomatoes thrown at them: {tomatoes}",
            ephemeral=quiet,
        )

    @jail_group.command(name="leaderboard", description="Show who has been jailed the most.")
    @app_commands.describe(quiet=QUIET_HELP)
    @requires_feature("jail")
    @in_bot_channel()
    async def leaderboard(self, interaction: discord.Interaction, quiet: bool = False):
        assert interaction.guild_id is not None
        rows = await self.services.jail.leaderboard(interaction.guild_id)
        if not rows:
            await interaction.response.send_message("No one has been jailed yet!", ephemeral=quiet)
            return
        lines = ["**Jail Leaderboard**"] + [f"{i}. <@{user_id}>: {count} times" for i, (user_id, count) in enumerate(rows, 1)]
        await interaction.response.send_message("\n".join(lines), allowed_mentions=discord.AllowedMentions.none(), ephemeral=quiet)
