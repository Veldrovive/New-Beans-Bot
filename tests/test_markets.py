import pytest

from beans_bot.config.schema import BotBettingConfig
from beans_bot.db.models import CoinTransactionKind
from beans_bot.services.errors import UserFacingError
from beans_bot.services.markets import InvalidMarket, parse_choices

from conftest import GUILD, HOUSE

THREAD = 500
CREATOR = 7


def test_parse_choices():
    assert parse_choices("Will it rain?\nChoices:\nYes\nNo\n\nMaybe") == ["yes", "no", "maybe"]


@pytest.mark.parametrize(
    "content",
    ["no choices here", "Choices:\nyes", "Choices:\nyes\nclose it", "Choices:\nyes\nno way", "Choices:\nyes\nYES"],
)
def test_invalid_choices(content):
    with pytest.raises(InvalidMarket):
        parse_choices(content)


async def fund(coins, db, user_id, amount):
    async with db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=amount, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


@pytest.fixture
async def market(markets):
    return await markets.create(guild_id=GUILD, thread_id=THREAD, creator_id=CREATOR, choices=["yes", "no"])


async def test_pari_mutuel_payout_with_house_remainder(markets, coins, db, market):
    for user, amount in ((1, 100), (2, 200), (3, 400)):
        await fund(coins, db, user, amount)
    await markets.place_bet(THREAD, 1, "yes", 100)
    await markets.place_bet(THREAD, 2, "yes", 200)
    await markets.place_bet(THREAD, 3, "no", 400)

    resolution = await markets.resolve(THREAD, CREATOR, "yes")
    # Pool 700, winners bet 300: 1 gets floor(233.3) -> 200, 2 gets floor(466.6) -> 400 (whole BeanCoins).
    assert resolution.payouts == [(1, 200), (2, 400)]
    assert resolution.house_remainder == 100
    assert await coins.balance(GUILD, 1) == 200
    assert await coins.balance(GUILD, 2) == 400
    assert await coins.balance(GUILD, 3) == 0
    assert await coins.balance(GUILD, HOUSE) == 100


@pytest.mark.parametrize("winner", ["yes", "no"])
async def test_refund_when_nobody_or_everybody_wins(markets, coins, db, market, winner):
    await fund(coins, db, 1, 100)
    await fund(coins, db, 2, 100)
    await markets.place_bet(THREAD, 1, "yes", 100)
    await markets.place_bet(THREAD, 2, "yes", 100)
    resolution = await markets.resolve(THREAD, CREATOR, winner)
    assert resolution.refunded
    assert await coins.balance(GUILD, 1) == 100
    assert await coins.balance(GUILD, 2) == 100


async def test_betting_rules(markets, coins, db, market):
    await fund(coins, db, 1, 300)
    with pytest.raises(UserFacingError, match="enough"):
        await markets.place_bet(THREAD, 1, "yes", 500)
    with pytest.raises(UserFacingError, match="Invalid option"):
        await markets.place_bet(THREAD, 1, "maybe", 100)
    with pytest.raises(UserFacingError, match="not placed a bet"):
        await markets.add_to_bet(THREAD, 1, 100)
    await markets.place_bet(THREAD, 1, "yes", 100)
    with pytest.raises(UserFacingError, match="already placed"):
        await markets.place_bet(THREAD, 1, "no", 100)
    bet = await markets.add_to_bet(THREAD, 1, 100)
    assert bet.amount == 200
    assert await coins.balance(GUILD, 1) == 100


async def test_only_creator_manages(markets, market):
    with pytest.raises(UserFacingError, match="creator"):
        await markets.close(THREAD, 1)
    await markets.close(THREAD, CREATOR)
    with pytest.raises(UserFacingError, match="currently closed"):
        await markets.place_bet(THREAD, 1, "yes", 100)


async def test_cancel_refunds(markets, coins, db, market):
    await fund(coins, db, 1, 100)
    await markets.place_bet(THREAD, 1, "yes", 100)
    assert await markets.cancel(THREAD, None) == 1
    assert await coins.balance(GUILD, 1) == 100
    with pytest.raises(UserFacingError, match="already canceled"):
        await markets.cancel(THREAD, CREATOR)


async def test_house_bet_replaces_on_new_choice(markets, coins, market):
    await markets.house_bet(THREAD, "yes", 300)
    await markets.house_bet(THREAD, "yes", 100)
    assert await coins.balance(GUILD, HOUSE) == -400
    bet = await markets.house_bet(THREAD, "no", 200)
    assert (bet.choice, bet.amount) == ("no", 200)
    assert await coins.balance(GUILD, HOUSE) == -200


async def test_auto_bet_amount(markets, coins, db):
    # Config amounts are BeanCoins.
    config = BotBettingConfig(enabled=True, bet_probability=1.0, bet_proportion=0.1, max_bet=50, min_bet_range=(1, 1))
    assert await markets.auto_bet_amount(GUILD, config) == 100  # Broke house bets the floor
    await fund(coins, db, HOUSE, 10_000)
    assert await markets.auto_bet_amount(GUILD, config) == 1000
    assert await markets.auto_bet_amount(GUILD, config.model_copy(update={"enabled": False})) is None
