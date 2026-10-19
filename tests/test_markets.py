import datetime

import pytest

from beans_bot.config.schema import BotBettingConfig
from beans_bot.db.models import CoinTransactionKind, JailReason, PredictionMarket
from beans_bot.db.types import utcnow
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
    bet, added = await markets.add_to_bet(THREAD, 1, 100)
    assert (bet.amount, added) == (200, 100)
    assert await coins.balance(GUILD, 1) == 100


async def test_all_in(markets, coins, db, market):
    await fund(coins, db, 1, 300)
    bet = await markets.place_bet(THREAD, 1, "yes", None)
    assert bet.amount == 300
    assert await coins.balance(GUILD, 1) == 0

    await fund(coins, db, 1, 200)
    bet, added = await markets.add_to_bet(THREAD, 1, None)
    assert (bet.amount, added) == (500, 200)
    assert await coins.balance(GUILD, 1) == 0
    assert (await markets.stats(THREAD)).pool == 500


async def test_all_in_with_nothing_changes_nothing(markets, coins, market):
    with pytest.raises(UserFacingError, match="all in with nothing"):
        await markets.place_bet(THREAD, 1, "yes", None)
    assert await coins.balance(GUILD, 1) == 0
    assert (await markets.stats(THREAD)).pool == 0


async def test_swap_moves_whole_bet(markets, coins, db, market):
    await fund(coins, db, 1, 300)
    await fund(coins, db, 2, 100)
    await markets.place_bet(THREAD, 1, "yes", 100)
    await markets.add_to_bet(THREAD, 1, 100)
    await markets.place_bet(THREAD, 2, "no", 100)

    swap = await markets.swap_bet(THREAD, 1, "NO")
    assert swap.old_choice == "yes"
    assert (swap.bet.choice, swap.bet.amount) == ("no", 200)
    # The stake stays in the pool: no coins move.
    assert await coins.balance(GUILD, 1) == 100
    assert await coins.balance(GUILD, HOUSE) == 0
    stats = await markets.stats(THREAD)
    assert stats.by_choice == {"yes": 0, "no": 300}

    # Everybody is on "no" now, so it resolves as a refund.
    resolution = await markets.resolve(THREAD, CREATOR, "no")
    assert resolution.refunded
    assert await coins.balance(GUILD, 1) == 300


async def test_swap_rules(markets, coins, db, market):
    await fund(coins, db, 1, 100)
    with pytest.raises(UserFacingError, match="never placed"):
        await markets.swap_bet(THREAD, 1, "no")
    await markets.place_bet(THREAD, 1, "yes", 100)
    with pytest.raises(UserFacingError, match="already on"):
        await markets.swap_bet(THREAD, 1, "yes")
    with pytest.raises(UserFacingError, match="Invalid option"):
        await markets.swap_bet(THREAD, 1, "maybe")
    await markets.close(THREAD, CREATOR)
    with pytest.raises(UserFacingError, match="currently closed"):
        await markets.swap_bet(THREAD, 1, "no")
    stats = await markets.stats(THREAD)
    assert stats.by_choice == {"yes": 100, "no": 0}


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


async def lock_up(jail, user_id, reason):
    await jail.start_sentence(
        guild_id=GUILD,
        user_id=user_id,
        channel_id=THREAD,
        offending_message_id=None,
        length=datetime.timedelta(hours=1),
        reason=reason,
    )


async def test_riggers_cant_bet_from_jail(markets, coins, db, jail, market):
    await fund(coins, db, 1, 300)
    await markets.place_bet(THREAD, 1, "yes", 100)
    await fund(coins, db, 2, 100)
    await lock_up(jail, 1, JailReason.RIGGED)
    await lock_up(jail, 2, JailReason.RIGGED)

    with pytest.raises(UserFacingError, match="Fraudsters don't gamble"):
        await markets.place_bet(THREAD, 2, "yes", 100)
    with pytest.raises(UserFacingError, match="from a cell"):
        await markets.add_to_bet(THREAD, 1, 100)
    with pytest.raises(UserFacingError, match="already in jail"):
        await markets.swap_bet(THREAD, 1, "no")
    assert await coins.balance(GUILD, 1) == 200
    assert await coins.balance(GUILD, 2) == 100
    assert (await markets.stats(THREAD)).by_choice == {"yes": 100, "no": 0}


async def test_other_prisoners_can_bet_but_not_swap(markets, coins, db, jail, market):
    await fund(coins, db, 1, 300)
    await lock_up(jail, 1, JailReason.VOTE)
    await markets.place_bet(THREAD, 1, "yes", 100)
    bet, _ = await markets.add_to_bet(THREAD, 1, 100)
    assert bet.amount == 200
    with pytest.raises(UserFacingError, match="already in jail"):
        await markets.swap_bet(THREAD, 1, "no")
    assert await coins.balance(GUILD, 1) == 100


async def test_beating_the_house_alone_refunds_instead(markets, coins, db, market):
    await markets.house_bet(THREAD, "yes", 500)
    await fund(coins, db, CREATOR, 100)
    await markets.place_bet(THREAD, CREATOR, "no", 100)

    resolution = await markets.resolve(THREAD, CREATOR, "no", rigged_window=datetime.timedelta(hours=2))
    assert resolution.refunded
    assert (resolution.rigged_by, resolution.denied) == (CREATOR, 500)
    assert resolution.payouts == []
    assert await coins.balance(GUILD, CREATOR) == 100
    assert await coins.balance(GUILD, HOUSE) == 0


async def test_losing_to_the_house_alone_still_pays_the_house(markets, coins, db, market):
    await markets.house_bet(THREAD, "yes", 500)
    await fund(coins, db, 1, 100)
    await markets.place_bet(THREAD, 1, "no", 100)

    resolution = await markets.resolve(THREAD, CREATOR, "yes")
    assert resolution.rigged_by is None
    assert resolution.payouts == [(HOUSE, 600)]
    assert await coins.balance(GUILD, 1) == 0
    assert await coins.balance(GUILD, HOUSE) == 100


async def test_beating_the_house_with_company_pays_out(markets, coins, db, market):
    await markets.house_bet(THREAD, "yes", 400)
    for user in (1, 2):
        await fund(coins, db, user, 100)
        await markets.place_bet(THREAD, user, "no", 100)

    resolution = await markets.resolve(THREAD, CREATOR, "no")
    assert resolution.rigged_by is None
    assert resolution.payouts == [(1, 300), (2, 300)]
    assert await coins.balance(GUILD, HOUSE) == -400


async def test_beating_the_house_alone_after_the_window_pays_out(markets, coins, db, market):
    async with db.transaction() as session:
        stored = await session.get(PredictionMarket, THREAD)
        assert stored is not None
        stored.created_at = utcnow() - datetime.timedelta(hours=3)
    await markets.house_bet(THREAD, "yes", 500)
    await fund(coins, db, CREATOR, 100)
    await markets.place_bet(THREAD, CREATOR, "no", 100)

    resolution = await markets.resolve(THREAD, CREATOR, "no", rigged_window=datetime.timedelta(hours=2))
    assert resolution.rigged_by is None
    assert resolution.payouts == [(CREATOR, 600)]
    assert await coins.balance(GUILD, CREATOR) == 600
    assert await coins.balance(GUILD, HOUSE) == -500
