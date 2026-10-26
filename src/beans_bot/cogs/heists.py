"""
Heists: rob a member by sniping them, with a crew recruited in real life.

- /heist begin picks a random noun as the codeword and privately tells the leader how
  much each crew size would steal. The leader shares the codeword out loud, not in Discord.
- /heist join puts you in the crew. /heist snitch turns the crew in. Both take the codeword
  and only you see the reply.
- When the snipes cog verifies a snipe it tells us, and the crew's payday is announced as a
  reply to the photo. A loop also catches any we missed, expires old heists (the crew gets a
  DM) and pays snitches once their delay is up (by DM, so nobody sees it).
- Snitching jails the crew and the snitch alike. The announcement lists everyone jailed in a
  random order, so the snitch can't be picked out.
"""

from __future__ import annotations

import random

import discord
import httpx
from discord import app_commands
from discord.ext import tasks

from beans_bot.cogs.base import BeansCog, requires_feature
from beans_bot.config.schema import GuildConfig, HeistConfig
from beans_bot.db.models import Heist, JailReason, Snipe
from beans_bot.services import scripts
from beans_bot.services.heists import (
    AlreadyInHeist,
    CantRobYourself,
    CodewordTaken,
    HeistSuccess,
    NoSuchHeist,
    TargetAlreadyHeisted,
    cut_percent,
    percent_of,
)
from beans_bot.services.jail import JailOutcome

NOUN_API = "https://random-words-api.vercel.app/word/english/noun"


async def fetch_noun() -> str | None:
    """A random English noun, or None if the API is down or sends something odd."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(NOUN_API)
            response.raise_for_status()
            word = response.json().get("word")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return word.lower() if isinstance(word, str) and word.isalpha() else None


class HeistCog(BeansCog):
    heist_group = app_commands.Group(name="heist", description="Rob a member. Bring friends. Trust none of them.", guild_only=True)

    async def cog_load(self):
        self.check_heists.start()

    async def cog_unload(self):
        self.check_heists.cancel()

    def heists_config(self, guild_id: int | None) -> HeistConfig | None:
        config = self.guild_config(guild_id)
        return config.heists if config else None

    def announce_channel(self, guild: discord.Guild) -> discord.abc.Messageable | None:
        config = self.guild_config(guild.id)
        if config is None or config.bot_channel_id is None:
            return None
        channel = guild.get_channel(config.bot_channel_id)
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def dm(self, user_id: int, text: str) -> None:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(text)
        except discord.HTTPException:
            self.logger.info(f"Could not DM {user_id} about a heist")

    async def new_codeword(self, config: HeistConfig) -> str:
        noun = await fetch_noun()
        if noun is None and config.fallback_codewords:
            noun = random.choice(config.fallback_codewords).lower()
        return noun or f"beans{random.randint(100, 999)}"

    async def start_heist(self, guild_id: int, leader_id: int, target_id: int, config: HeistConfig) -> Heist:
        """Begin the heist, drawing a new codeword if one is already in use. Raises CodewordTaken after a few tries."""
        for _ in range(4):
            try:
                return await self.services.heists.begin(guild_id, leader_id, target_id, await self.new_codeword(config), config)
            except CodewordTaken:
                pass
        return await self.services.heists.begin(guild_id, leader_id, target_id, await self.new_codeword(config), config)

    def cut_table(self, balance: int, config: HeistConfig) -> str:
        """One line per crew size: how much of the target's current balance it would steal."""
        lines = []
        biggest = max(config.cuts)
        for size in range(config.min_crew, max(biggest, config.min_crew) + 1):
            percent = cut_percent(size, config)
            more = "+" if size == biggest else ""
            lines.append(f"- **{size}{more}** robbers: {percent}%, about {self.services.currency.format(percent_of(balance, percent, self.services.currency))}")
        return "\n".join(lines)

    # --- Commands -----------------------------------------------------------------------

    @heist_group.command(name="begin", description="Plot to rob a member. You'll get a codeword to share in person.")
    @app_commands.describe(target="Who you're robbing. The bot counts, if it isn't broke.")
    @requires_feature("heists")
    async def begin(self, interaction: discord.Interaction, target: discord.Member):
        assert interaction.guild is not None
        config = self.heists_config(interaction.guild.id)
        assert config is not None
        if target.bot and not self.is_own_user(target.id):
            await interaction.response.send_message("That bot doesn't have a wallet. Rob me instead, if you dare.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            heist = await self.start_heist(interaction.guild.id, interaction.user.id, target.id, config)
        except CantRobYourself:
            await interaction.followup.send("Robbing yourself is called moving money between pockets. Pick a victim.", ephemeral=True)
            return
        except TargetAlreadyHeisted:
            await interaction.followup.send(f"Someone's already casing {target.mention}. Find your own mark, or find their crew.", ephemeral=True)
            return
        except AlreadyInHeist:
            await interaction.followup.send("You're already in a crew. One crime at a time, overachiever.", ephemeral=True)
            return
        except CodewordTaken:
            await interaction.followup.send("I ran out of codewords. The criminal underworld is busy today. Try again.", ephemeral=True)
            return

        balance = await self.services.coins.balance(interaction.guild.id, target.id)
        values = {"leader": interaction.user.mention, "target": target.mention, "code": f"**{heist.code}**"}
        text = scripts.pick(config.begin_scripts, **values)
        text = text or f"The heist on {values['target']} is on. Your codeword is {values['code']}. Don't write it down."
        text += (
            f"\nTell people the codeword **in real life**. They join with `/heist join`. You need at least {config.min_crew} "
            f"people, you included. If anyone in the crew gets a verified snipe of {target.mention}, the crew splits:\n"
            f"{self.cut_table(balance, config)}\n"
            f"-# Anyone with the codeword can `/heist snitch`. Then the whole crew goes to jail and pays the house "
            f"{config.snitch_fine_percent}% of their coins. Choose your friends carefully."
            f"\n-# The heist falls apart {discord.utils.format_dt(heist.ends_at, 'R')}. Only snipes posted from now on count."
        )
        await interaction.followup.send(text, ephemeral=True)

    @heist_group.command(name="join", description="Join a heist crew with its codeword.")
    @requires_feature("heists")
    async def join(self, interaction: discord.Interaction, code: str):
        assert interaction.guild is not None
        config = self.heists_config(interaction.guild.id)
        assert config is not None
        try:
            heist, crew_size = await self.services.heists.join(interaction.guild.id, interaction.user.id, code)
        except NoSuchHeist:
            await interaction.response.send_message("No heist answers to that codeword. Either you misheard, or it's over.", ephemeral=True)
            return
        except CantRobYourself:
            await interaction.response.send_message(
                "That's the heist on you. Joining it won't save your coins, but snitching might.", ephemeral=True
            )
            return
        except AlreadyInHeist:
            await interaction.response.send_message("You're already in a crew. Loyalty is a virtue, even among thieves.", ephemeral=True)
            return

        percent = cut_percent(crew_size, config)
        text = f"Welcome to the crew. You're robber number {crew_size} in the heist on <@{heist.target_user_id}>."
        if crew_size < config.min_crew:
            text += f"\n-# The crew needs {config.min_crew - crew_size} more before a snipe pays. Recruit someone, out loud."
        else:
            text += f"\n-# A verified snipe of them by anyone in the crew now steals {percent}% of their coins, split evenly."
        text += f"\n-# The heist falls apart {discord.utils.format_dt(heist.ends_at, 'R')}."
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @heist_group.command(name="snitch", description="Turn in a heist crew. Everyone goes to jail, you included, for cover.")
    @requires_feature("heists")
    async def snitch(self, interaction: discord.Interaction, code: str):
        assert interaction.guild is not None
        guild_config = self.guild_config(interaction.guild.id)
        assert guild_config is not None and guild_config.heists is not None
        config = guild_config.heists
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.services.heists.snitch(interaction.guild.id, interaction.user.id, code, config)
        except NoSuchHeist:
            await interaction.followup.send("No heist answers to that codeword. Nothing to snitch on, you eager little rat.", ephemeral=True)
            return

        channel_id = interaction.channel_id or 0
        for user_id in result.fines:
            await self.jail(interaction.guild, guild_config, user_id, channel_id)

        own_fine = self.services.currency.format(result.fines[interaction.user.id])
        reward = self.services.currency.format(result.heist.snitch_reward or 0)
        payday = discord.utils.format_dt(discord.utils.utcnow() + config.snitch_reward_delay, "R")
        await interaction.followup.send(
            f"Your secret is safe with me. Mostly. You're off to jail with the rest of them and paid {own_fine}, "
            f"so nobody suspects a thing.\n-# Your cut of {reward} arrives quietly {payday}.",
            ephemeral=True,
        )

        jailed = list(result.fines)
        random.shuffle(jailed)  # The snitch is somewhere in here
        values = {
            "target": f"<@{result.heist.target_user_id}>",
            "crew": scripts.join_names([f"<@{u}>" for u in jailed]),
            "fined": self.services.currency.format(result.total),
        }
        text = scripts.pick(config.snitched_scripts, **values)
        text = text or f"A little bird sang. The heist on {values['target']} is busted, and {values['crew']} are off to jail. The house keeps {values['fined']}."
        channel = self.announce_channel(interaction.guild)
        if channel is None:
            return
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[discord.Object(u) for u in jailed]))
        except discord.HTTPException:
            self.logger.warning(f"Could not announce snitched heist {result.heist.id} in guild {interaction.guild.id}")

    async def jail(self, guild: discord.Guild, config: GuildConfig, user_id: int, channel_id: int) -> None:
        assert config.jail is not None
        result = await self.services.jail.imprison(
            guild, config.jail, user_id, channel_id=channel_id, offending_message_id=None, audit_reason="Busted in a heist.", reason=JailReason.HEIST
        )
        if result.outcome is JailOutcome.ALREADY_JAILED:
            await self.services.jail.resentence(
                guild_id=guild.id, user_id=user_id, channel_id=channel_id, length=config.jail.jail_length, reason=JailReason.HEIST
            )
        elif result.outcome is not JailOutcome.JAILED:
            self.logger.warning(f"Could not jail {user_id} for a heist in guild {guild.id}: {result.outcome}")

    # --- Paydays ------------------------------------------------------------------------

    @BeansCog.listener()
    async def on_snipe_verified(self, snipe: Snipe):
        await self.resolve(snipe.guild_id)

    async def resolve(self, guild_id: int) -> None:
        config = self.heists_config(guild_id)
        guild = self.bot.get_guild(guild_id)
        if config is None or guild is None:
            return
        for success in await self.services.heists.resolve_snipes(guild_id, config):
            await self.announce_success(guild, config, success)

    async def announce_success(self, guild: discord.Guild, config: HeistConfig, success: HeistSuccess) -> None:
        heist = success.heist
        robbers = list(success.shares)
        values = {
            "target": f"<@{heist.target_user_id}>",
            "crew": scripts.join_names([f"<@{u}>" for u in robbers]),
            "sniper": f"<@{success.sniper_user_id}>",
            "loot": self.services.currency.format(heist.loot or 0),
        }
        text = scripts.pick(config.success_scripts, **values)
        text = text or f"Heist complete! {values['crew']} robbed {values['target']} blind and made off with {values['loot']}."
        if success.dethroned:
            dethroned = scripts.pick(config.dethroned_scripts, target=values["target"])
            text += "\n" + (dethroned or f"{values['target']} has lost the 1%. The penthouse is back on the market at the starting price.")
        each = self.services.currency.format(min(success.shares.values()))
        text += f"\n-# {len(robbers)} robbers, {each} each, and the leader keeps the change."
        mentions = discord.AllowedMentions(users=[discord.Object(u) for u in [heist.target_user_id, *robbers]], replied_user=False)

        snipes_config = self.guild_config(guild.id)
        photo = None
        if snipes_config is not None and snipes_config.snipes is not None and heist.snipe_message_id is not None:
            photo = await self.fetch_message(snipes_config.snipes.channel_id, heist.snipe_message_id)
        try:
            if photo is not None:
                await photo.reply(text, allowed_mentions=mentions)
            elif channel := self.announce_channel(guild):
                await channel.send(text, allowed_mentions=mentions)
        except discord.HTTPException:
            self.logger.warning(f"Could not announce heist {heist.id} in guild {guild.id}")

    # --- Timers -------------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def check_heists(self):
        for guild in self.bot.guilds:
            config = self.heists_config(guild.id)
            if config is None:
                continue
            await self.resolve(guild.id)  # In case we missed a verification
            await self.expire(guild.id, config)
            await self.pay_snitches(guild.id, config)

    @check_heists.before_loop
    async def before_check_heists(self):
        await self.bot.wait_until_ready()

    async def expire(self, guild_id: int, config: HeistConfig) -> None:
        for heist in await self.services.heists.expire_overdue(guild_id):
            assert heist.id is not None
            text = scripts.pick(config.expired_scripts, target=f"<@{heist.target_user_id}>")
            text = text or f"The heist on <@{heist.target_user_id}> fell apart. Nobody got sniped, nobody got paid, nobody got caught."
            for user_id in await self.services.heists.crew(heist.id):
                await self.dm(user_id, text)

    async def pay_snitches(self, guild_id: int, config: HeistConfig) -> None:
        for heist in await self.services.heists.due_rewards(guild_id, config.snitch_reward_delay):
            assert heist.id is not None and heist.snitch_user_id is not None
            reward = await self.services.heists.pay_reward(heist.id, config.snitch_reward_delay)
            if not reward:
                continue
            amount = self.services.currency.format(reward)
            text = scripts.pick(config.snitch_reward_scripts, reward=amount)
            await self.dm(heist.snitch_user_id, text or f"A plain envelope with {amount} inside. For services rendered. We never spoke.")
