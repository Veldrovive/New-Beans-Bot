"""
Automatic bets. Once a day the bot opens a prediction market in the gambling forum,
picked from the guild's bets file (see services/auto_bets.py), and bets on it.

- The bot owns the thread. The admins the bet names manage the market with the usual /bet commands.
- Each guild posts at its own `post_time`, on a `post_probability` coin flip. A restart
  after that time posts it then, unless one went out today already.
- /autobet post lets a server admin post one now, on top of the daily one.
"""

from __future__ import annotations

import datetime
import functools
import random

import discord
from discord import app_commands
from pydantic import ValidationError

from beans_bot.cogs.base import BeansCog, admin_only
from beans_bot.services import scripts
from beans_bot.services.auto_bets import load_bets
from beans_bot.services.errors import UserFacingError
from beans_bot.services.markets import SECRET_BET_ANNOUNCEMENT, market_instructions
from beans_bot.services.scheduling import DailyJob


class AutoBetsCog(BeansCog):
    # default_permissions hides the group from members' command list. admin_only is the real check.
    autobet = app_commands.Group(
        name="autobet",
        description="Bets the bot posts by itself.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    async def cog_load(self):
        self.jobs = [
            DailyJob(
                self.bot,
                f"auto-bets-{guild_id}",
                guild.auto_bets.post_time.replace(tzinfo=self.config.tz),
                functools.partial(self.daily_post, guild_id),
            )
            for guild_id, guild in self.config.guilds.items()
            if guild.active and guild.auto_bets is not None
        ]
        for job in self.jobs:
            job.start()

    async def cog_unload(self):
        for job in self.jobs:
            job.stop()

    # --- Posting ------------------------------------------------------------------------

    async def daily_post(self, guild_id: int) -> None:
        config = self.guild_config(guild_id)
        guild = self.bot.get_guild(guild_id)
        if config is None or config.auto_bets is None or guild is None:
            return
        midnight = datetime.datetime.now(self.config.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if await self.services.auto_bets.posted_since(guild_id, midnight):
            return
        if random.random() >= config.auto_bets.post_probability:
            self.logger.info(f"Skipping today's automatic bet in guild {guild_id}")
            return
        try:
            await self.post(guild)
        except UserFacingError as e:
            self.logger.error(f"Could not post an automatic bet in guild {guild_id}: {e}")

    async def post(self, guild: discord.Guild) -> discord.Thread:
        """Post a fresh automatic bet. Raises UserFacingError when the setup is broken."""
        config = self.guild_config(guild.id)
        assert config is not None and config.auto_bets is not None and config.gambling is not None
        assert self.bot.user is not None
        forum = guild.get_channel(config.gambling.forum_channel_id)
        if not isinstance(forum, discord.ForumChannel):
            raise UserFacingError("The gambling forum has gone missing. No forum, no bets, no fun.")
        try:
            bets = load_bets(config.auto_bets.bets_file)
        except (OSError, ValueError, ValidationError) as e:
            self.logger.error(f"Bad bets file {config.auto_bets.bets_file} for guild {guild.id}: {e}")
            raise UserFacingError("The bets file is broken, so the bookie is closed. Check the logs.") from e
        if not bets:
            raise UserFacingError("The bets file is empty. I can't gamble on nothing. Well, I could, but I won't.")

        draw = await self.services.auto_bets.draw(guild.id, bets)
        intro = scripts.pick(config.auto_bets.intro_scripts, bet=f"**{draw.text}**") or f"The house wants to know: **{draw.text}**"
        content = "\n".join([intro, "", "Choices:", *draw.bet.choices])
        try:
            thread, _ = await forum.create_thread(name=draw.title, content=content)
        except discord.HTTPException as e:
            raise UserFacingError("Discord wouldn't let me open the thread. The bet is off.") from e

        await self.services.auto_bets.record(guild.id, draw, thread_id=thread.id, creator_id=self.bot.user.id)
        await thread.send(market_instructions(draw.bet.choices, draw.bet.admins))

        if draw.bet.bot_bet is not None:
            amount = self.services.auto_bets.bot_bet_amount(draw.bet)
        else:
            amount = await self.services.markets.auto_bet_amount(guild.id, config.gambling.bot_betting)
        if amount is not None:
            choice = random.choice(draw.bet.choices)
            await self.services.markets.house_bet(thread.id, choice, amount)
            await thread.send(SECRET_BET_ANNOUNCEMENT)
        return thread

    # --- /autobet -----------------------------------------------------------------------

    @autobet.command(name="post", description="Post an automatic bet right now, on top of the daily one.")
    @admin_only()
    async def post_now(self, interaction: discord.Interaction):
        assert interaction.guild is not None
        config = self.guild_config(interaction.guild_id)
        if config is None or config.auto_bets is None:
            raise UserFacingError("Automatic bets aren't set up for this server. The bookie is on vacation.")
        await interaction.response.defer(ephemeral=True)
        thread = await self.post(interaction.guild)
        await interaction.followup.send(f"Fresh bet posted: {thread.mention}. Somebody's losing money today.", ephemeral=True)
