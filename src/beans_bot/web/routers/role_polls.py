"""Polls to rename the robotics student role."""

from __future__ import annotations

import datetime

from fastapi import APIRouter

from beans_bot.web.deps import Ctx, Guild, Paged
from beans_bot.web.schemas import Out, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/role-polls", tags=["role polls"])


class PollOut(Out):
    poll_message_id: Snowflake
    channel_id: Snowflake
    initiator_user_id: Snowflake
    adjective: str
    created_at: datetime.datetime
    active: bool


@router.get("")
async def role_polls(guild_id: int, ctx: Ctx, _: Guild, page: Paged) -> Page[PollOut]:
    rows, total = await ctx.services.reports.role_polls(guild_id, page.limit, page.offset)
    return Page(items=[PollOut.model_validate(p) for p in rows], total=total)
