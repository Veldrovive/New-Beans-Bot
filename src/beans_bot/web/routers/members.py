"""Members: one row per member with any record, or who is in the guild."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.web.deps import Ctx, Guild
from beans_bot.web.schemas import Amount, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["members"])


class MemberRow(BaseModel):
    user_id: Snowflake
    in_guild: bool | None  # None when the bot isn't connected, so nobody knows
    balance: Amount
    shadow_balance: Amount
    jailed: bool
    vip: list[str]  # "pass", "one_percent", "henchman"
    times_jailed: int
    tomatoes_received: int


@router.get("/members")
async def members(guild_id: int, ctx: Ctx, _: Guild) -> list[MemberRow]:
    currency = ctx.services.currency
    summaries = {s.user_id: s for s in await ctx.services.reports.member_summaries(guild_id)}
    live = ctx.bot.get_guild(guild_id) if ctx.bot is not None and ctx.connected else None
    in_guild = {m.id for m in live.members if not m.bot} if live is not None else None
    house = ctx.services.coins.house_user_id
    user_ids = (set(summaries) | (in_guild or set())) - {house}
    rows = []
    for user_id in user_ids:
        s = summaries.get(user_id)
        rows.append(
            MemberRow(
                user_id=user_id,
                in_guild=None if in_guild is None else user_id in in_guild,
                balance=Amount.of(s.balance if s else 0, currency),
                shadow_balance=Amount.of(s.shadow_balance if s else 0, currency),
                jailed=s.jailed if s else False,
                vip=s.vip if s else [],
                times_jailed=s.times_jailed if s else 0,
                tomatoes_received=s.tomatoes_received if s else 0,
            )
        )
    return sorted(rows, key=lambda r: r.balance.value, reverse=True)
