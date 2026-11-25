"""Fat cow: visits to Palace Tang, and who ate how much."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.web.deps import Ctx, Guild
from beans_bot.web.schemas import Out, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/fat-cow", tags=["fat cow"])


class VisitOut(Out):
    id: int
    visited_at: datetime.datetime


class EaterOut(Out):
    user_id: Snowflake
    visits: int
    total_meters: float
    average_meters: float


class FatCowOut(BaseModel):
    visits: list[VisitOut]
    members: list[EaterOut]


@router.get("")
async def fat_cow(guild_id: int, ctx: Ctx, _: Guild) -> FatCowOut:
    stats = await ctx.services.reports.fat_cow_stats(guild_id)
    return FatCowOut(visits=[VisitOut.model_validate(v) for v in stats.visits], members=[EaterOut.model_validate(m) for m in stats.members])
