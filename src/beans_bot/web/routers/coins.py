"""Coins: every account's two pockets, and the ledger."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from beans_bot.currency import CurrencySystem
from beans_bot.db.models import CoinTransaction, CoinTransactionKind
from beans_bot.services.coins import InsufficientFunds
from beans_bot.web.actions import actor_user_id, feature, house_id, logger
from beans_bot.web.deps import Actor, Ctx, Guild, Paged
from beans_bot.web.errors import bad_request, conflict
from beans_bot.web.schemas import Amount, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/coins", tags=["coins"])


class AccountRow(BaseModel):
    user_id: Snowflake
    balance: Amount
    shadow_balance: Amount


class Accounts(BaseModel):
    house: AccountRow | None  # None until the bot has logged in
    accounts: list[AccountRow]  # Members only, richest first


class LedgerRow(BaseModel):
    id: int
    created_at: datetime.datetime
    kind: str
    amount: Amount
    from_user_id: Snowflake | None
    to_user_id: Snowflake | None
    from_shadow: bool
    to_shadow: bool
    actor_user_id: Snowflake | None
    message_id: Snowflake | None
    thread_id: Snowflake | None

    @classmethod
    def of(cls, row: CoinTransaction, currency: CurrencySystem) -> LedgerRow:
        return cls(
            id=row.id or 0,
            created_at=row.created_at,
            kind=row.kind,
            amount=Amount.of(row.amount, currency),
            from_user_id=row.from_user_id,
            to_user_id=row.to_user_id,
            from_shadow=row.from_shadow,
            to_shadow=row.to_shadow,
            actor_user_id=row.actor_user_id,
            message_id=row.message_id,
            thread_id=row.thread_id,
        )


class LedgerPage(BaseModel):
    items: list[LedgerRow]
    total: int
    kinds: list[str]  # Every kind there is, for the filter


@router.get("/accounts")
async def accounts(guild_id: int, ctx: Ctx, _: Guild) -> Accounts:
    currency = ctx.services.currency
    house_id = ctx.services.coins.house_user_id
    rows = [
        AccountRow(user_id=a.user_id, balance=Amount.of(a.balance, currency), shadow_balance=Amount.of(a.shadow_balance, currency))
        for a in await ctx.services.reports.accounts(guild_id)
    ]
    house = next((r for r in rows if r.user_id == house_id), None)
    if house is None and house_id is not None:
        house = AccountRow(user_id=house_id, balance=Amount.of(0, currency), shadow_balance=Amount.of(0, currency))
    return Accounts(house=house, accounts=[r for r in rows if r.user_id != house_id])


@router.get("/ledger")
async def ledger(guild_id: int, ctx: Ctx, _: Guild, page: Paged, user_id: int | None = None, kind: str | None = None) -> LedgerPage:
    currency = ctx.services.currency
    rows, total = await ctx.services.reports.ledger(guild_id, user_id, kind or None, page.limit, page.offset)
    return LedgerPage(
        items=[LedgerRow.of(r, currency) for r in rows],
        total=total,
        kinds=[k.value for k in CoinTransactionKind],
    )


# --- Actions ---------------------------------------------------------------------------


class GiveTake(BaseModel):
    user_id: Snowflake
    amount: str = Field(description='Like "5" or "3 cheddar", as in the slash commands')
    shadow: bool = False  # ShadowBeans instead of the regular balance


class PocketResult(BaseModel):
    balance: Amount  # The pocket's new balance
    message: str


def _check_target(ctx, guild_id: int, user_id: int, house: int) -> None:
    if user_id == house:
        raise bad_request("The house doesn't pay itself. That's called embezzlement.")
    live = ctx.bot.get_guild(guild_id) if ctx.bot is not None and ctx.connected else None
    member = live.get_member(user_id) if live is not None else None
    if member is not None and member.bot:
        raise bad_request("Bots don't need coins. They need oil.")


@router.post("/give")
async def give(guild_id: int, body: GiveTake, ctx: Ctx, guild: Guild, actor: Actor) -> PocketResult:
    feature(guild, "coins")
    currency = ctx.services.currency
    house = house_id(ctx)
    admin = actor_user_id(ctx)
    amount = currency.parse(body.amount)
    _check_target(ctx, guild_id, body.user_id, house)
    logger.info(f"Web {actor}: give {amount} ({'shadow' if body.shadow else 'regular'}) to {body.user_id} in guild {guild_id}")
    balance = await ctx.services.coins.admin_give(guild_id, admin, body.user_id, amount, shadow=body.shadow)
    pocket = "ShadowBeans" if body.shadow else "balance"
    return PocketResult(
        balance=Amount.of(balance, currency),
        message=f"The house slipped them {currency.format(amount)}. Their {pocket} is now {currency.format(balance)}. They won't be told.",
    )


@router.post("/take")
async def take(guild_id: int, body: GiveTake, ctx: Ctx, guild: Guild, actor: Actor) -> PocketResult:
    feature(guild, "coins")
    currency = ctx.services.currency
    house = house_id(ctx)
    admin = actor_user_id(ctx)
    amount = currency.parse(body.amount)
    _check_target(ctx, guild_id, body.user_id, house)
    pocket = "ShadowBeans" if body.shadow else "balance"
    logger.info(f"Web {actor}: take {amount} ({'shadow' if body.shadow else 'regular'}) from {body.user_id} in guild {guild_id}")
    try:
        balance = await ctx.services.coins.admin_take(guild_id, admin, body.user_id, amount, shadow=body.shadow)
    except InsufficientFunds as e:
        raise conflict(
            f"They only have {currency.format(e.balance)} in their {pocket}, not {currency.format(amount)}. You can't squeeze blood from a bean."
        )
    return PocketResult(
        balance=Amount.of(balance, currency),
        message=f"Confiscated {currency.format(amount)}. Their {pocket} is now {currency.format(balance)}. They won't be told.",
    )
