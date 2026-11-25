"""VIP: the 1%, active day passes and their history."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.web.actions import feature, logger
from beans_bot.web.deps import Actor, Ctx, Guild, Paged
from beans_bot.web.errors import conflict, not_found
from beans_bot.web.schemas import Amount, Done, Out, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/vip", tags=["vip"])


class OnePercentOut(BaseModel):
    holder_user_id: Snowflake
    paid: Amount
    claimed_at: datetime.datetime
    henchman_user_id: Snowflake | None


class PassOut(Out):
    id: int
    user_id: Snowflake
    started_at: datetime.datetime
    ends_at: datetime.datetime
    ended_at: datetime.datetime | None


class VipOut(BaseModel):
    one_percent: OnePercentOut | None
    next_price: Amount | None  # None when VIP isn't set up here
    passes: list[PassOut]  # Active
    history: Page[PassOut]


@router.get("")
async def vip(guild_id: int, ctx: Ctx, guild: Guild, page: Paged, user_id: int | None = None) -> VipOut:
    currency = ctx.services.currency
    service = ctx.services.vip
    title = await service.one_percent(guild_id)
    price = await service.one_percent_price(guild_id, guild.vip.one_percent) if guild.vip else None
    history, total = await ctx.services.reports.vip_pass_history(guild_id, user_id, page.limit, page.offset)
    return VipOut(
        one_percent=OnePercentOut(
            holder_user_id=title.holder_user_id, paid=Amount.of(title.paid, currency), claimed_at=title.claimed_at, henchman_user_id=title.henchman_user_id
        )
        if title
        else None,
        next_price=Amount.of(price, currency) if price is not None else None,
        passes=[PassOut.model_validate(p) for p in sorted(await service.active_passes(guild_id), key=lambda p: p.ends_at)],
        history=Page(items=[PassOut.model_validate(p) for p in history], total=total),
    )


# --- Actions ---------------------------------------------------------------------------
# Only the records change here. VipCog.sync_roles moves the roles within a minute.


@router.post("/passes/{pass_id}/end")
async def end_pass(guild_id: int, pass_id: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "vip")
    day_pass = await ctx.services.reports.day_pass(pass_id)
    if day_pass is None or day_pass.guild_id != guild_id:
        raise not_found(f"There's no day pass {pass_id} here.")
    logger.info(f"Web {actor}: end day pass {pass_id} of {day_pass.user_id} in guild {guild_id}")
    if not await ctx.services.vip.end_pass(pass_id, early=True):
        raise conflict("That pass has already ended.")
    return Done(message="Pass revoked, no refund. The role comes off within a minute. Back to the proletariat with them.")


@router.post("/one-percent/revoke")
async def revoke_one_percent(guild_id: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "vip")
    logger.info(f"Web {actor}: revoke the 1% in guild {guild_id}")
    title = await ctx.services.vip.revoke_one_percent(guild_id)
    if title is None:
        raise conflict("Nobody holds the 1%. You can't dethrone an empty chair.")
    return Done(message="Dethroned, no refund. The price is back to the start and the henchman is out of a job. Roles follow within a minute.")
