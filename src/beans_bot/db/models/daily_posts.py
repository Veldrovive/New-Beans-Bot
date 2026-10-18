"""Records for the daily birthday and action holiday posts. Dates are calendar dates in the bot's timezone."""

import datetime

from sqlmodel import Field, Index, SQLModel


class BirthdayMessage(SQLModel, table=True):
    __tablename__ = "birthday_message"
    __table_args__ = (Index("ix_birthday_message_guild_person_year", "guild_id", "person_name", "for_year"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    sent_on: datetime.date
    channel_id: int
    message_id: int
    person_name: str  # As written in the birthdays message, not a Discord user
    for_year: int
    used_holiday: str | None = None
    used_on_this_day: str | None = None


class UsedHoliday(SQLModel, table=True):
    """A checkiday.com holiday already mentioned in a birthday message, so it isn't reused."""

    __tablename__ = "used_holiday"
    __table_args__ = (Index("ix_used_holiday_guild_date", "guild_id", "month", "day"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    used_on: datetime.date
    holiday_name: str
    holiday_link: str
    day: int
    month: int


class UsedOnThisDay(SQLModel, table=True):
    """An onthisday.com event already mentioned in a birthday message, so it isn't reused."""

    __tablename__ = "used_on_this_day"
    __table_args__ = (Index("ix_used_on_this_day_guild_date", "guild_id", "month", "day"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    used_on: datetime.date
    text: str
    day: int
    month: int
    year: int  # The year of the event


class ActionHolidayPost(SQLModel, table=True):
    __tablename__ = "action_holiday_post"
    __table_args__ = (Index("ix_action_holiday_post_guild_date", "guild_id", "sent_on"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    sent_on: datetime.date
    holiday_name: str
    holiday_link: str
