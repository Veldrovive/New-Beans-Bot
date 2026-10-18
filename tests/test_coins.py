import datetime

import pytest

from beans_bot.db.models import CoinAccount, CoinTransaction, CoinTransactionKind
from beans_bot.services.coins import InsufficientFunds, ReactionGrantResult
from sqlmodel import select

from conftest import GUILD, HOUSE

MIDNIGHT = datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


async def grant(coins, giver, receiver, message_id, max_per_day=2):
    return await coins.grant_for_reaction(
        guild_id=GUILD, giver_id=giver, receiver_id=receiver, message_id=message_id,
        amount=100, max_per_day=max_per_day, day_start=MIDNIGHT,
    )  # fmt: skip


async def test_reaction_grants_and_limits(coins):
    assert await grant(coins, 1, 2, 10) is ReactionGrantResult.GRANTED
    assert await grant(coins, 1, 2, 10) is ReactionGrantResult.DUPLICATE
    assert await grant(coins, 1, 2, 11) is ReactionGrantResult.GRANTED
    assert await grant(coins, 1, 2, 12) is ReactionGrantResult.DAILY_LIMIT
    assert await coins.balance(GUILD, 2) == 200
    assert await coins.reaction_totals(GUILD, 2) == (200, 0)
    assert await coins.reaction_totals(GUILD, 1) == (0, 200)


async def test_members_cannot_overspend(coins):
    await grant(coins, 1, 2, 10)
    with pytest.raises(InsufficientFunds):
        await coins.pay_house(guild_id=GUILD, user_id=2, amount=500, kind=CoinTransactionKind.BRIBE)
    assert await coins.balance(GUILD, 2) == 100
    await coins.pay_house(guild_id=GUILD, user_id=2, amount=100, kind=CoinTransactionKind.BRIBE)
    assert await coins.balance(GUILD, 2) == 0
    assert await coins.balance(GUILD, HOUSE) == 100


async def test_house_can_go_negative(coins, db):
    async with db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=300, kind=CoinTransactionKind.BET_PLACE, from_user_id=HOUSE)
    assert await coins.balance(GUILD, HOUSE) == -300


async def test_failed_move_rolls_back(coins, db):
    await grant(coins, 1, 2, 10)
    with pytest.raises(InsufficientFunds):
        async with db.transaction() as session:
            await coins.move(session, guild_id=GUILD, amount=100, kind=CoinTransactionKind.BET_PLACE, from_user_id=2)
            await coins.move(session, guild_id=GUILD, amount=100, kind=CoinTransactionKind.BET_PLACE, from_user_id=2)
    assert await coins.balance(GUILD, 2) == 100


async def test_ledger_matches_balances(coins, db):
    await grant(coins, 1, 2, 10)
    await grant(coins, 3, 2, 11)
    await coins.pay_house(guild_id=GUILD, user_id=2, amount=100, kind=CoinTransactionKind.BRIBE)
    async with db.transaction() as session:
        accounts = (await session.exec(select(CoinAccount))).all()
        transactions = (await session.exec(select(CoinTransaction))).all()
    for account in accounts:
        credits = sum(t.amount for t in transactions if t.to_user_id == account.user_id)
        debits = sum(t.amount for t in transactions if t.from_user_id == account.user_id)
        assert credits - debits == account.balance


async def test_leaderboard_only_positive(coins, db):
    await grant(coins, 1, 2, 10)
    async with db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=300, kind=CoinTransactionKind.BET_PLACE, from_user_id=HOUSE)
    assert [a.user_id for a in await coins.leaderboard(GUILD)] == [2]
