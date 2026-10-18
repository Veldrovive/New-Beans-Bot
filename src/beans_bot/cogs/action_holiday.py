"""Once a day, post a "Verb a Thing Day" holiday from checkiday.com that people can go and do."""

from __future__ import annotations

import datetime
import random

import discord
from sqlmodel import select

from beans_bot.cogs.base import BeansCog
from beans_bot.db.models import ActionHolidayPost
from beans_bot.scrapers import checkiday
from beans_bot.services.scheduling import DailyJob


def announcement(holiday: checkiday.Holiday) -> str:
    link = f"**[{holiday.name}]({holiday.link})**"
    return random.choice(
        [
            f"Ok everyone! It's {link} so get on that.",
            f"Today is {link}. Make sure that's done by tonight.",
            f"Hear ye, hear ye! 'Tis {link}, verily! Fetch one, if thou wilt!",
        ]
    )


class ActionHolidayCog(BeansCog):
    async def cog_load(self):
        self.job = DailyJob(self.bot, "action_holiday", self.config.daily_post_time_tz, self.run_all, self.self_test)
        self.job.start()

    async def cog_unload(self):
        self.job.stop()

    async def self_test(self) -> None:
        now = datetime.datetime.now(self.config.tz)
        holidays = await checkiday.get_action_holidays(now.day, now.month, now.year)
        self.logger.info(f"Test scrapers (action_holidays): Found {len(holidays)} action holidays today.")
        if holidays and (answer := await checkiday.ask_best_holiday(holidays, self.services.gemini)):
            self.logger.info(f"Test scrapers (action_holidays): Best action holiday: {answer[1].name} because {answer[0]}")

    async def run_all(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.post(guild)
            except Exception:
                self.logger.exception(f"Action holiday failed for guild {guild.id}")

    async def post(self, guild: discord.Guild) -> None:
        config = self.guild_config(guild.id)
        if config is None or config.action_holiday is None or config.bot_channel_id is None:
            return
        channel = guild.get_channel(config.bot_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            self.logger.error(f"Bot channel {config.bot_channel_id} not found in guild {guild.id}")
            return

        today = datetime.datetime.now(self.config.tz).date()
        async with self.services.db.transaction() as session:
            posts_this_year = (
                await session.exec(
                    select(ActionHolidayPost).where(
                        ActionHolidayPost.guild_id == guild.id,
                        ActionHolidayPost.sent_on >= datetime.date(today.year, 1, 1),
                    )
                )
            ).all()
        if any(p.sent_on == today for p in posts_this_year):
            self.logger.info(f"Already sent an action holiday today for guild {guild.id}")
            return

        used_names = {p.holiday_name for p in posts_this_year}
        holidays = [h for h in await checkiday.get_action_holidays(today.day, today.month, today.year) if h.name not in used_names]
        if not holidays:
            self.logger.info(f"No unused action holidays today for guild {guild.id}")
            return

        holiday = random.choice(holidays)
        await channel.send(announcement(holiday))
        async with self.services.db.transaction() as session:
            session.add(
                ActionHolidayPost(guild_id=guild.id, sent_on=today, holiday_name=holiday.name, holiday_link=holiday.link)
            )
        self.logger.info(f"Sent action holiday {holiday.name} to guild {guild.id}")
