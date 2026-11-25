"""Birthday messages and action holiday posts the bot has sent."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from beans_bot.web.deps import Ctx, Guild
from beans_bot.web.schemas import Out, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/daily-posts", tags=["daily posts"])


class BirthdayOut(Out):
    id: int
    sent_on: datetime.date
    channel_id: Snowflake
    message_id: Snowflake
    person_name: str
    for_year: int
    used_holiday: str | None
    used_on_this_day: str | None


class HolidayOut(Out):
    id: int
    sent_on: datetime.date
    holiday_name: str
    holiday_link: str


class DailyPostsOut(BaseModel):
    birthdays: Page[BirthdayOut]
    action_holidays: Page[HolidayOut]


@router.get("")
async def daily_posts(
    guild_id: int,
    ctx: Ctx,
    _: Guild,
    limit: int = Query(50, ge=1, le=200),
    birthdays_offset: int = Query(0, ge=0),
    holidays_offset: int = Query(0, ge=0),
) -> DailyPostsOut:
    reports = ctx.services.reports
    birthdays, birthday_total = await reports.birthday_messages(guild_id, limit, birthdays_offset)
    holidays, holiday_total = await reports.action_holiday_posts(guild_id, limit, holidays_offset)
    return DailyPostsOut(
        birthdays=Page(items=[BirthdayOut.model_validate(b) for b in birthdays], total=birthday_total),
        action_holidays=Page(items=[HolidayOut.model_validate(h) for h in holidays], total=holiday_total),
    )
