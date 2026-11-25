"""Prediction markets: the list, and one market with every bet, the house's secret one included."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.services.markets import SECRET_BET_ANNOUNCEMENT, nullification_announcement
from beans_bot.web.actions import actor_user_id, feature, house_id, logger
from beans_bot.web.context import WebContext
from beans_bot.web.deps import Actor, Ctx, Guild, Paged
from beans_bot.web.errors import not_found
from beans_bot.web.routers.coins import LedgerRow
from beans_bot.web.schemas import Amount, Done, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/markets", tags=["markets"])


class MarketRowOut(BaseModel):
    thread_id: Snowflake
    creator_id: Snowflake
    admin_ids: list[Snowflake]
    status: str
    choices: list[str]
    created_at: datetime.datetime
    resolved_at: datetime.datetime | None
    winning_choice: str | None
    pool: Amount
    bets: int


class BetOut(BaseModel):
    user_id: Snowflake
    choice: str
    amount: Amount
    placed_at: datetime.datetime
    secret: bool  # The house's bet, hidden from members until the market resolves


class ChoiceTotal(BaseModel):
    choice: str
    amount: Amount


class AutoBetOut(BaseModel):
    bet: str
    text: str
    values: dict[str, str]
    round: int
    posted_at: datetime.datetime


class MarketDetailOut(BaseModel):
    market: MarketRowOut
    bets: list[BetOut]
    totals: list[ChoiceTotal]
    ledger: list[LedgerRow]
    auto_bet: AutoBetOut | None


@router.get("")
async def markets(guild_id: int, ctx: Ctx, _: Guild, page: Paged, status: str | None = None, user_id: int | None = None) -> Page[MarketRowOut]:
    currency = ctx.services.currency
    rows, total = await ctx.services.reports.markets(guild_id, status or None, user_id, page.limit, page.offset)
    return Page(
        items=[MarketRowOut(**r.market.model_dump(exclude={"guild_id"}), pool=Amount.of(r.pool, currency), bets=r.bets) for r in rows],
        total=total,
    )


@router.get("/{thread_id}")
async def market(guild_id: int, thread_id: int, ctx: Ctx, _: Guild) -> MarketDetailOut:
    currency = ctx.services.currency
    detail = await ctx.services.reports.market_detail(thread_id)
    if detail is None or detail.market.guild_id != guild_id:
        raise not_found(f"There's no market in thread {thread_id} here.")
    house = ctx.services.coins.house_user_id
    m = detail.market
    pool = sum(b.amount for b in detail.bets)
    return MarketDetailOut(
        market=MarketRowOut(**m.model_dump(exclude={"guild_id"}), pool=Amount.of(pool, currency), bets=len(detail.bets)),
        bets=[
            BetOut(user_id=b.user_id, choice=b.choice, amount=Amount.of(b.amount, currency), placed_at=b.placed_at, secret=b.user_id == house)
            for b in detail.bets
        ],
        totals=[ChoiceTotal(choice=c, amount=Amount.of(a, currency)) for c, a in detail.totals.items()],
        ledger=[LedgerRow.of(r, currency) for r in detail.ledger],
        auto_bet=AutoBetOut(**detail.auto_bet.model_dump()) if detail.auto_bet is not None else None,
    )


# --- Actions ---------------------------------------------------------------------------
# Resolving stays in Discord: its announcement and the rigging punishment belong to /bet resolve.


class HouseBet(BaseModel):
    choice: str
    amount: str  # Like "5" or "3 cheddar"


async def _market_in(ctx: WebContext, guild_id: int, thread_id: int) -> None:
    market = await ctx.services.markets.get(thread_id)
    if market is None or market.guild_id != guild_id:
        raise not_found(f"There's no market in thread {thread_id} here.")


def _posted(posted: bool) -> str:
    return "" if posted else " I couldn't post in the thread, so nobody was told."


@router.post("/{thread_id}/house-bet")
async def house_bet(guild_id: int, thread_id: int, body: HouseBet, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "gambling")
    house_id(ctx)
    await _market_in(ctx, guild_id, thread_id)
    amount = ctx.services.currency.parse(body.amount)
    logger.info(f"Web {actor}: house bets {amount} on {body.choice!r} in market {thread_id} in guild {guild_id}")
    bet = await ctx.services.markets.house_bet(thread_id, body.choice, amount)
    # Same voice as the bot's automatic bets, so it looks like the bot's own idea.
    posted = await ctx.post(thread_id, SECRET_BET_ANNOUNCEMENT)
    currency = ctx.services.currency
    return Done(message=f'The house now has {currency.format(bet.amount)} on "{bet.choice}". It stays secret until the market resolves.{_posted(posted)}')


@router.post("/{thread_id}/close")
async def close(guild_id: int, thread_id: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "gambling")
    await _market_in(ctx, guild_id, thread_id)
    logger.info(f"Web {actor}: close market {thread_id} in guild {guild_id}")
    await ctx.services.markets.close(thread_id, None)
    posted = await ctx.post(thread_id, "Betting has been closed.")
    return Done(message=f"Betting is closed. Resolve it from Discord with /bet resolve.{_posted(posted)}")


@router.post("/{thread_id}/cancel")
async def cancel(guild_id: int, thread_id: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "gambling")
    await _market_in(ctx, guild_id, thread_id)
    logger.info(f"Web {actor}: cancel market {thread_id} in guild {guild_id}")
    refunded = await ctx.services.markets.cancel(thread_id, None)
    posted = await ctx.post(thread_id, "Prediction canceled. All bets have been refunded.")
    return Done(message=f"Canceled, and {refunded} bet{'' if refunded == 1 else 's'} refunded.{_posted(posted)}")


@router.post("/{thread_id}/nullify")
async def nullify(guild_id: int, thread_id: int, ctx: Ctx, guild: Guild, actor: Actor) -> Done:
    feature(guild, "gambling")
    house = house_id(ctx)
    admin = actor_user_id(ctx)
    await _market_in(ctx, guild_id, thread_id)
    logger.info(f"Web {actor}: nullify market {thread_id} in guild {guild_id}")
    nullification = await ctx.services.markets.nullify(thread_id, admin)
    posted = await ctx.post(thread_id, nullification_announcement(nullification, ctx.services.currency, house))
    return Done(message=f"Nullified. The coins are back where they started, debts and all.{_posted(posted)}")
