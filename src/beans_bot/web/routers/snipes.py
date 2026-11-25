"""Snipes and the Council of Teds. Read-only: outcomes come from Discord reactions."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.web.deps import Ctx, Guild, Paged
from beans_bot.web.schemas import Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/snipes", tags=["snipes"])


class CouncilMember(BaseModel):
    key: str
    name: str
    council_name: str
    user_id: Snowflake
    crest_emoji: str


class SnipeOut(BaseModel):
    message_id: Snowflake
    channel_id: Snowflake
    sniper_user_id: Snowflake
    target_user_id: Snowflake
    status: str  # "pending", "verified", "rejected" or "expired"
    created_at: datetime.datetime
    decided_at: datetime.datetime | None
    votes_for: list[Snowflake]
    votes_against: list[Snowflake]


class SnipesOut(BaseModel):
    council: list[CouncilMember]
    nay_emoji: str | None
    majority: int
    items: list[SnipeOut]
    total: int


@router.get("")
async def snipes(guild_id: int, ctx: Ctx, guild: Guild, page: Paged, status: str | None = None, user_id: int | None = None) -> SnipesOut:
    rows, total = await ctx.services.reports.snipes(guild_id, status or None, user_id, page.limit, page.offset)
    council = guild.council_of_teds
    return SnipesOut(
        council=[
            CouncilMember(key=key, name=m.internal_name, council_name=m.council_name, user_id=m.user_id, crest_emoji=m.crest_emoji)
            for key, m in (council.members.items() if council else [])
        ],
        nay_emoji=council.nay_vote_emoji if council else None,
        majority=ctx.services.council.majority(guild_id) if council else 0,
        items=[
            SnipeOut(
                message_id=r.snipe.message_id,
                channel_id=r.snipe.channel_id,
                sniper_user_id=r.snipe.sniper_user_id,
                target_user_id=r.snipe.target_user_id,
                status=r.status,
                created_at=r.snipe.created_at,
                decided_at=r.snipe.verified_at or r.snipe.rejected_at or r.snipe.expired_at,
                votes_for=r.votes_for,
                votes_against=r.votes_against,
            )
            for r in rows
        ],
        total=total,
    )
