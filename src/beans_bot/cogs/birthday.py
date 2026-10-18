"""
Birthday messages. Every day at the configured time, read the guild's
birthdays message and wish a happy birthday to anyone whose day it is, with
a fun holiday and a historical event from the same date.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

import dateparser
import discord
from sqlmodel import select

from beans_bot.cogs.base import BeansCog
from beans_bot.config.schema import BirthdayConfig
from beans_bot.db.models import BirthdayMessage, UsedHoliday, UsedOnThisDay
from beans_bot.scrapers import checkiday, onthisday
from beans_bot.services.scheduling import DailyJob

BIRTHDAY_LINE = re.compile(r"- (.*): (.*)")
# dateparser fills a missing year from this base, so year 9999 means "no year given".
_NO_YEAR = datetime.datetime(9999, 1, 1)


@dataclass
class Birthday:
    person_name: str
    month: int
    day: int
    year: int | None


def parse_birthdays(content: str) -> list[Birthday]:
    """
    Parse lines like "- Aidan: April 2nd, 2000", "- Merlin: July 21st" or "- Bean: unknown".
    Other lines are ignored.
    """
    birthdays = []
    for line in content.split("\n"):
        match = BIRTHDAY_LINE.match(line)
        if not match:
            continue
        name, date_text = match.group(1), match.group(2)
        if date_text.strip().lower() == "unknown":
            continue
        parsed = dateparser.parse(date_text, settings={"REQUIRE_PARTS": ["day", "month"], "RELATIVE_BASE": _NO_YEAR})
        if parsed is None:
            continue
        birthdays.append(Birthday(name, parsed.month, parsed.day, None if parsed.year == 9999 else parsed.year))
    return birthdays


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class BirthdayCog(BeansCog):
    async def cog_load(self):
        self.job = DailyJob(self.bot, "birthdays", self.config.daily_post_time_tz, self.run_all, self.self_test)
        self.job.start()

    async def cog_unload(self):
        self.job.stop()

    async def self_test(self) -> None:
        """Check on startup that the scrapers and Gemini still work."""
        now = datetime.datetime.now(self.config.tz)
        holidays = await checkiday.get_holidays(now.day, now.month, now.year)
        self.logger.info(f"Test scrapers (birthday): Found {len(holidays)} holidays today.")
        if holidays and (answer := await checkiday.ask_best_holiday(holidays, self.services.gemini)):
            self.logger.info(f"Test scrapers (birthday): Best holiday: {answer[1].name} because {answer[0]}")
        events = await onthisday.get_on_this_day(now.day, now.month)
        self.logger.info(f"Test scrapers (birthday): Found {len(events)} on this day events today.")
        if events and (answer := await onthisday.ask_best_event(events, self.services.gemini)):
            self.logger.info(f"Test scrapers (birthday): Best on this day: {answer[1].description} because {answer[0]}")

    async def run_all(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self.send_birthday_messages(guild)
            except Exception:
                self.logger.exception(f"Birthday messages failed for guild {guild.id}")

    async def read_birthdays(self, guild: discord.Guild, config: BirthdayConfig) -> list[Birthday]:
        channel = guild.get_channel(config.birthdays_message_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise ValueError(f"Birthdays channel {config.birthdays_message_channel_id} not found")
        message = await channel.fetch_message(config.birthdays_message_id)
        return parse_birthdays(message.content)

    async def send_birthday_messages(self, guild: discord.Guild) -> None:
        guild_config = self.guild_config(guild.id)
        if guild_config is None or guild_config.birthday is None or guild_config.bot_channel_id is None:
            return
        config = guild_config.birthday
        channel = guild.get_channel(guild_config.bot_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            self.logger.error(f"Bot channel {guild_config.bot_channel_id} not found in guild {guild.id}")
            return

        today = datetime.datetime.now(self.config.tz).date()
        birthdays = [b for b in await self.read_birthdays(guild, config) if (b.month, b.day) == (today.month, today.day)]
        for birthday in birthdays:
            if await self.already_sent(guild.id, birthday, today.year):
                self.logger.info(f"{birthday.person_name}'s birthday has already been messaged")
                continue
            text, holiday, event = await self.compose(guild.id, birthday, today, config)
            self.logger.info(f"Sending birthday message for {birthday.person_name}: {text}")
            sent = await channel.send(text)
            async with self.services.db.transaction() as session:
                session.add(
                    BirthdayMessage(
                        guild_id=guild.id,
                        sent_on=today,
                        channel_id=sent.channel.id,
                        message_id=sent.id,
                        person_name=birthday.person_name,
                        for_year=today.year,
                        used_holiday=holiday.name if holiday else None,
                        used_on_this_day=event.description if event else None,
                    )
                )
                if holiday is not None:
                    session.add(
                        UsedHoliday(
                            guild_id=guild.id,
                            used_on=today,
                            holiday_name=holiday.name,
                            holiday_link=holiday.link,
                            day=holiday.day,
                            month=holiday.month,
                        )
                    )
                if event is not None:
                    session.add(
                        UsedOnThisDay(
                            guild_id=guild.id,
                            used_on=today,
                            text=event.description[:200],
                            day=event.day,
                            month=event.month,
                            year=event.year,
                        )
                    )

    async def already_sent(self, guild_id: int, birthday: Birthday, year: int) -> bool:
        async with self.services.db.transaction() as session:
            result = await session.exec(
                select(BirthdayMessage).where(
                    BirthdayMessage.guild_id == guild_id,
                    BirthdayMessage.person_name == birthday.person_name,
                    BirthdayMessage.for_year == year,
                )
            )
            return result.first() is not None

    async def unused_holidays(self, guild_id: int, today: datetime.date) -> list[checkiday.Holiday]:
        holidays = await checkiday.get_holidays(today.day, today.month, today.year)
        async with self.services.db.transaction() as session:
            used = set(
                (
                    await session.exec(
                        select(UsedHoliday.holiday_name).where(
                            UsedHoliday.guild_id == guild_id, UsedHoliday.day == today.day, UsedHoliday.month == today.month
                        )
                    )
                ).all()
            )
        return [h for h in holidays if h.type == checkiday.HolidayType.SINGLE_DAY and h.name not in used]

    async def unused_events(self, guild_id: int, today: datetime.date) -> list[onthisday.OnThisDayEvent]:
        events = await onthisday.get_on_this_day(today.day, today.month)
        async with self.services.db.transaction() as session:
            # An event counts as used if one from the same year was already mentioned.
            used_years = set(
                (
                    await session.exec(
                        select(UsedOnThisDay.year).where(
                            UsedOnThisDay.guild_id == guild_id,
                            UsedOnThisDay.day == today.day,
                            UsedOnThisDay.month == today.month,
                        )
                    )
                ).all()
            )
        return [e for e in events if e.year not in used_years]

    async def compose(
        self, guild_id: int, birthday: Birthday, today: datetime.date, config: BirthdayConfig
    ) -> tuple[str, checkiday.Holiday | None, onthisday.OnThisDayEvent | None]:
        holiday = None
        if config.enable_holidays:
            try:
                if holidays := await self.unused_holidays(guild_id, today):
                    holiday = await checkiday.pick_holiday(holidays, self.services.gemini)
            except Exception as e:
                self.logger.error(f"Failed to get holidays: {e}")

        event = None
        if config.enable_on_this_day:
            try:
                if events := await self.unused_events(guild_id, today):
                    event = await onthisday.pick_event(events, self.services.gemini)
            except Exception as e:
                self.logger.error(f"Failed to get on this day events: {e}")

        if birthday.year is not None:
            text = f"Happy {ordinal(today.year - birthday.year)} birthday, {birthday.person_name}!"
        else:
            text = f"Happy birthday, {birthday.person_name}!"
        if holiday is not None:
            text += f"\n\nIt just so happens that it is also [{holiday.name}]({holiday.link})! Maybe you should celebrate?"
        if event is not None:
            text += (
                f"\n\nAlso, on this day way back in {event.year}, {event.description}\n"
                f"You can learn more [here]({event.link})."
            )
        return text, holiday, event
