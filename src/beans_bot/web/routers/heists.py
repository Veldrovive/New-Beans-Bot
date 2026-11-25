"""Heists. Read-only, and secret from members: outcomes come from snipes and Discord reactions."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.services.reports import heist_status
from beans_bot.web.deps import Ctx, Guild, Paged
from beans_bot.web.schemas import Amount, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/heists", tags=["heists"])


class CrewMember(BaseModel):
    user_id: Snowflake
    joined_at: datetime.datetime
    share: Amount | None  # ShadowBeans, once the heist succeeded
    fine: Amount | None  # Paid to the house, once it was snitched on


class HeistOut(BaseModel):
    id: int
    code: str
    status: str  # "active", "succeeded", "snitched" or "expired"
    leader_user_id: Snowflake
    target_user_id: Snowflake
    started_at: datetime.datetime
    ends_at: datetime.datetime
    finished_at: datetime.datetime | None
    snipe_message_id: Snowflake | None
    loot: Amount | None
    snitch_user_id: Snowflake | None
    snitch_reward: Amount | None
    crew: list[CrewMember]


@router.get("")
async def heists(guild_id: int, ctx: Ctx, _: Guild, page: Paged, active: bool | None = None, user_id: int | None = None) -> Page[HeistOut]:
    currency = ctx.services.currency

    def amount(value: int | None) -> Amount | None:
        return Amount.of(value, currency) if value is not None else None

    rows, total = await ctx.services.reports.heists(guild_id, active, user_id, page.limit, page.offset)
    return Page(
        items=[
            HeistOut(
                id=h.id or 0,
                code=h.code,
                status=heist_status(h),
                leader_user_id=h.leader_user_id,
                target_user_id=h.target_user_id,
                started_at=h.started_at,
                ends_at=h.ends_at,
                finished_at=h.succeeded_at or h.snitched_at or h.expired_at,
                snipe_message_id=h.snipe_message_id,
                loot=amount(h.loot),
                snitch_user_id=h.snitch_user_id,
                snitch_reward=amount(h.snitch_reward),
                crew=[CrewMember(user_id=m.user_id, joined_at=m.joined_at, share=amount(m.share), fine=amount(m.fine)) for m in r.crew],
            )
            for r in rows
            for h in [r.heist]
        ],
        total=total,
    )
