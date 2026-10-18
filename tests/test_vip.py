import datetime

import pytest

from beans_bot.config.schema import DayPassConfig, OnePercentConfig
from beans_bot.db.models import CoinTransactionKind
from beans_bot.db.types import utcnow
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.vip import AlreadyOnePercent, BidTooLow

from conftest import GUILD, HOUSE

DAY_PASS = DayPassConfig(role_id=1, cost=2)
ONE_PERCENT = OnePercentConfig(role_id=2, starting_price=10, price_increment=1)


async def fund(coins, user_id, beans):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def test_day_pass_charges_and_extends(vip, coins):
    await fund(coins, 1, 5)
    first = await vip.buy_day_pass(GUILD, 1, DAY_PASS)
    assert await coins.balance(GUILD, 1) == 300
    assert await coins.balance(GUILD, HOUSE) == 200
    assert first.ends_at - first.started_at == datetime.timedelta(days=1)

    second = await vip.buy_day_pass(GUILD, 1, DAY_PASS)
    assert second.id == first.id
    assert second.ends_at == first.ends_at + datetime.timedelta(days=1)
    assert [p.user_id for p in await vip.active_passes(GUILD)] == [1]

    with pytest.raises(InsufficientFunds):
        await vip.buy_day_pass(GUILD, 1, DAY_PASS)
    assert await coins.balance(GUILD, 1) == 100


async def test_day_pass_expires(vip, coins):
    await fund(coins, 1, 2)
    day_pass = await vip.buy_day_pass(GUILD, 1, DAY_PASS)
    assert await vip.due_to_expire(utcnow()) == []
    assert not await vip.end_pass(day_pass.id)  # Not due yet

    later = utcnow() + datetime.timedelta(days=2)
    assert [p.id for p in await vip.due_to_expire(later)] == [day_pass.id]
    async with vip.db.transaction() as session:
        (await session.get(type(day_pass), day_pass.id)).ends_at = utcnow() - datetime.timedelta(seconds=1)
    assert await vip.end_pass(day_pass.id)
    assert not await vip.end_pass(day_pass.id)
    assert await vip.active_pass(GUILD, 1) is None


async def test_one_percent_price_climbs(vip, coins):
    await fund(coins, 1, 30)
    await fund(coins, 2, 30)
    assert await vip.one_percent_price(GUILD, ONE_PERCENT) == 1000

    with pytest.raises(BidTooLow) as e:
        await vip.claim_one_percent(GUILD, 1, 900, ONE_PERCENT)
    assert e.value.price == 1000

    first = await vip.claim_one_percent(GUILD, 1, 1000, ONE_PERCENT)
    assert first.previous_holder_id is None and first.new_price == 1100
    assert await coins.balance(GUILD, 1) == 2000

    with pytest.raises(AlreadyOnePercent):
        await vip.claim_one_percent(GUILD, 1, 2000, ONE_PERCENT)
    with pytest.raises(BidTooLow):
        await vip.claim_one_percent(GUILD, 2, 1000, ONE_PERCENT)

    # Overpaying sets the next price from what was paid.
    second = await vip.claim_one_percent(GUILD, 2, 1500, ONE_PERCENT)
    assert second.previous_holder_id == 1 and second.new_price == 1600
    title = await vip.one_percent(GUILD)
    assert (title.holder_user_id, title.paid, title.price) == (2, 1500, 1600)
    assert await coins.balance(GUILD, HOUSE) == 2500


async def test_one_percent_needs_funds(vip, coins):
    await fund(coins, 1, 5)
    with pytest.raises(InsufficientFunds):
        await vip.claim_one_percent(GUILD, 1, 1000, ONE_PERCENT)
    assert await vip.one_percent(GUILD) is None
    assert await coins.balance(GUILD, 1) == 500
