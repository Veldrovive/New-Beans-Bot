import datetime

import pytest

from beans_bot.db.models import CoinAccount, CoinTransaction, CoinTransactionKind
from beans_bot.services.coins import CantPayYourself, InsufficientFunds, NothingToLaunder, ReactionGrantResult
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
    await coins.admin_give(GUILD, 9, 2, 300, shadow=True)
    await coins.launder(GUILD, 2, 100)
    await coins.transfer(GUILD, 2, 1, 50)
    await coins.admin_take(GUILD, 9, 2, 100, shadow=True)
    async with db.transaction() as session:
        accounts = (await session.exec(select(CoinAccount))).all()
        transactions = (await session.exec(select(CoinTransaction))).all()
    for account in accounts:
        # Each pocket balances on its own
        credits = sum(t.amount for t in transactions if t.to_user_id == account.user_id and not t.to_shadow)
        debits = sum(t.amount for t in transactions if t.from_user_id == account.user_id and not t.from_shadow)
        assert credits - debits == account.balance
        credits = sum(t.amount for t in transactions if t.to_user_id == account.user_id and t.to_shadow)
        debits = sum(t.amount for t in transactions if t.from_user_id == account.user_id and t.from_shadow)
        assert credits - debits == account.shadow_balance


async def test_leaderboard_only_positive(coins, db):
    await grant(coins, 1, 2, 10)
    async with db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=300, kind=CoinTransactionKind.BET_PLACE, from_user_id=HOUSE)
    assert [a.user_id for a in await coins.leaderboard(GUILD)] == [2]


async def shadow(coins, user_id, amount):
    async with coins.db.transaction() as session:
        await coins.move(
            session, guild_id=GUILD, amount=amount, kind=CoinTransactionKind.SNITCH_SHADOW_REWARD, from_user_id=HOUSE, to_user_id=user_id, to_shadow=True
        )


async def test_shadowbeans_are_hidden_and_unspendable(coins):
    await shadow(coins, 2, 500)
    assert await coins.shadow_balance(GUILD, 2) == 500
    assert await coins.balance(GUILD, 2) == 0
    assert await coins.leaderboard(GUILD) == []
    with pytest.raises(InsufficientFunds):
        await coins.pay_house(guild_id=GUILD, user_id=2, amount=100, kind=CoinTransactionKind.BRIBE)
    with pytest.raises(InsufficientFunds):
        await coins.transfer(GUILD, 2, 1, 100)
    assert await coins.shadow_balance(GUILD, 2) == 500 and await coins.balance(GUILD, 1) == 0


async def test_house_has_no_shadow_pocket(coins, db):
    with pytest.raises(ValueError):
        async with db.transaction() as session:
            await coins.move(session, guild_id=GUILD, amount=100, kind=CoinTransactionKind.ADMIN_GIVE, to_user_id=HOUSE, to_shadow=True)
    assert await coins.shadow_balance(GUILD, HOUSE) == 0


async def test_launder_some_then_all(coins):
    await shadow(coins, 2, 500)
    assert await coins.launder(GUILD, 2, 200) == (200, 300)
    assert await coins.balance(GUILD, 2) == 200 and await coins.shadow_balance(GUILD, 2) == 300
    assert await coins.launder(GUILD, 2, None) == (300, 0)
    assert await coins.balance(GUILD, 2) == 500 and await coins.shadow_balance(GUILD, 2) == 0
    assert await coins.balance(GUILD, HOUSE) == -500  # Laundering moves between pockets; the house isn't involved


async def test_failed_launders_change_nothing(coins):
    with pytest.raises(NothingToLaunder):
        await coins.launder(GUILD, 2, None)
    await shadow(coins, 2, 100)
    with pytest.raises(InsufficientFunds):
        await coins.launder(GUILD, 2, 200)
    assert await coins.balance(GUILD, 2) == 0 and await coins.shadow_balance(GUILD, 2) == 100


async def test_transfer(coins):
    await grant(coins, 1, 2, 10)
    await coins.transfer(GUILD, 2, 3, 60)
    assert await coins.balance(GUILD, 2) == 40 and await coins.balance(GUILD, 3) == 60
    with pytest.raises(InsufficientFunds):
        await coins.transfer(GUILD, 2, 3, 50)
    with pytest.raises(CantPayYourself):
        await coins.transfer(GUILD, 2, 2, 10)
    assert await coins.balance(GUILD, 2) == 40 and await coins.balance(GUILD, 3) == 60


async def test_admin_give_and_take_both_pockets(coins):
    assert await coins.admin_give(GUILD, 9, 2, 300, shadow=False) == 300
    assert await coins.admin_give(GUILD, 9, 2, 200, shadow=True) == 200
    assert await coins.balance(GUILD, HOUSE) == -500
    assert await coins.admin_take(GUILD, 9, 2, 100, shadow=False) == 200
    assert await coins.admin_take(GUILD, 9, 2, 50, shadow=True) == 150
    assert await coins.balance(GUILD, HOUSE) == -350

    with pytest.raises(InsufficientFunds):
        await coins.admin_take(GUILD, 9, 2, 300, shadow=False)
    with pytest.raises(InsufficientFunds):
        await coins.admin_take(GUILD, 9, 2, 200, shadow=True)
    assert await coins.balance(GUILD, 2) == 200 and await coins.shadow_balance(GUILD, 2) == 150
    assert await coins.balance(GUILD, HOUSE) == -350
