import datetime
import itertools

import pytest
from sqlmodel import select

from beans_bot.config.schema import HeistConfig, OnePercentConfig
from beans_bot.currency import CurrencySystem
from beans_bot.db.models import CoinTransactionKind, Heist, HeistMember, OnePercentTitle
from beans_bot.db.types import utcnow
from beans_bot.services.coins import CoinService
from beans_bot.services.heists import AlreadyInHeist, CantRobYourself, CodewordTaken, NoSuchHeist, TargetAlreadyHeisted, cut_percent

from conftest import GUILD, HOUSE

CONFIG = HeistConfig()  # Crew of 4+, cuts 10/15/20/25/30%, 25% fines, half to the snitch
TARGET = 50
CREW = [1, 2, 3, 4]
COUNCIL = 77
_message_ids = itertools.count(1000)


@pytest.fixture
def coins(db):
    """Cheddar on, so amounts round to 10 base units."""
    service = CoinService(db, CurrencySystem(cheddar_enabled=True))
    service.house_user_id = HOUSE
    return service


async def fund(coins, user_id, beans):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def plot(heists, crew=CREW, target=TARGET, code="badger"):
    heist = await heists.begin(GUILD, crew[0], target, code, CONFIG)
    for user_id in crew[1:]:
        await heists.join(GUILD, user_id, code)
    return heist


async def snipe(snipes, sniper, target=TARGET):
    message_id = next(_message_ids)
    await snipes.start(GUILD, 1, message_id, sniper, target)
    assert await snipes.vote(message_id, COUNCIL, needed=1)
    return message_id


async def rows(db, model):
    async with db.transaction() as session:
        return list((await session.exec(select(model))).all())


def test_cut_percent():
    assert [cut_percent(n, CONFIG) for n in range(2, 11)] == [0, 0, 10, 15, 20, 25, 30, 30, 30]


async def test_begin_and_join(heists):
    heist = await heists.begin(GUILD, 1, TARGET, "  Badger ", CONFIG)
    assert heist.code == "badger"
    assert heist.ends_at - heist.started_at == CONFIG.duration
    joined, size = await heists.join(GUILD, 2, "BADGER")
    assert joined.id == heist.id and size == 2
    assert await heists.crew(heist.id) == [1, 2]


async def test_current_heist_only_for_the_crew(heists):
    heist = await heists.begin(GUILD, 1, TARGET, "badger", CONFIG)
    await heists.join(GUILD, 2, "badger")
    for user_id in (1, 2):
        current = await heists.current_heist(GUILD, user_id)
        assert current is not None and current[0].id == heist.id and current[1] == 2
    assert await heists.current_heist(GUILD, TARGET) is None
    assert await heists.current_heist(GUILD, 3) is None
    assert await heists.current_heist(GUILD + 1, 1) is None

    await heists.snitch(GUILD, 3, "badger", CONFIG)  # Over, so nobody is in it anymore
    assert await heists.current_heist(GUILD, 1) is None


async def test_rejections_change_nothing(heists, db):
    heist = await heists.begin(GUILD, 1, TARGET, "badger", CONFIG)
    with pytest.raises(CantRobYourself):
        await heists.begin(GUILD, 5, 5, "otter", CONFIG)
    with pytest.raises(TargetAlreadyHeisted):
        await heists.begin(GUILD, 5, TARGET, "otter", CONFIG)
    with pytest.raises(AlreadyInHeist):
        await heists.begin(GUILD, 1, 60, "otter", CONFIG)
    with pytest.raises(CodewordTaken):
        await heists.begin(GUILD, 5, 60, "badger", CONFIG)
    with pytest.raises(CantRobYourself):
        await heists.join(GUILD, TARGET, "badger")
    with pytest.raises(AlreadyInHeist):
        await heists.join(GUILD, 1, "badger")
    with pytest.raises(NoSuchHeist):
        await heists.join(GUILD, 2, "weasel")
    with pytest.raises(NoSuchHeist):
        await heists.join(GUILD + 1, 2, "badger")  # Codewords are per guild
    assert [h.id for h in await rows(db, Heist)] == [heist.id]
    assert [m.user_id for m in await rows(db, HeistMember)] == [1]


async def test_success_splits_loot_evenly(heists, snipes, coins):
    await fund(coins, TARGET, 100)
    await fund(coins, 3, 1)
    heist = await plot(heists, crew=[1, 2, 3, 4, 5])
    message_id = await snipe(snipes, sniper=3)

    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    # 15% of 10,000 is 1,500: 300 each
    assert success.heist.loot == 1500 and success.heist.snipe_message_id == message_id
    assert success.sniper_user_id == 3 and success.shares == {1: 300, 2: 300, 3: 300, 4: 300, 5: 300}
    assert not success.dethroned
    assert await coins.balance(GUILD, TARGET) == 8500
    # The loot is in ShadowBeans, so nobody can spot the crew by their balances
    assert await coins.balance(GUILD, 3) == 100 and await coins.shadow_balance(GUILD, 3) == 300
    assert await coins.balance(GUILD, 5) == 0 and await coins.shadow_balance(GUILD, 5) == 300

    assert await heists.resolve_snipes(GUILD, CONFIG) == []  # Only steals once
    assert await coins.balance(GUILD, TARGET) == 8500
    with pytest.raises(NoSuchHeist):
        await heists.join(GUILD, 6, "badger")
    assert heist.id is not None


async def test_leader_keeps_the_change(heists, snipes, coins):
    await fund(coins, TARGET, 10)  # 10% of 1,000 is 10 cheddar: 2 each, and the leader gets the 2 left over
    await plot(heists)
    await snipe(snipes, sniper=2)
    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.shares == {1: 40, 2: 20, 3: 20, 4: 20}
    assert await coins.balance(GUILD, TARGET) == 900


async def test_loot_rounds_down_to_a_cheddar(heists, snipes, coins):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=1290, kind=CoinTransactionKind.REACTION, to_user_id=TARGET)
    await plot(heists)
    await snipe(snipes, sniper=1)
    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.heist.loot == 120  # 10% of 1,290 is 129, rounded down to 12 cheddar
    assert success.shares == {1: 30, 2: 30, 3: 30, 4: 30}
    assert await coins.balance(GUILD, TARGET) == 1170


async def test_snipes_that_dont_count(heists, snipes, coins):
    await fund(coins, TARGET, 100)
    await snipe(snipes, sniper=1)  # Before the heist began
    await plot(heists, crew=[1, 2, 3])
    await snipe(snipes, sniper=2)  # Crew too small
    await snipe(snipes, sniper=9)  # Not in the crew
    await snipe(snipes, sniper=1, target=60)  # Wrong target
    assert await heists.resolve_snipes(GUILD, CONFIG) == []

    await heists.join(GUILD, 4, "badger")  # Big enough now, but the old snipes were verified before
    assert await heists.resolve_snipes(GUILD, CONFIG) == []
    assert await coins.balance(GUILD, TARGET) == 10000

    await snipe(snipes, sniper=4)
    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.sniper_user_id == 4 and success.heist.loot == 1000


async def test_success_dethrones_the_one_percent(heists, snipes, vip, coins, db):
    one_percent = OnePercentConfig(role_id=1, starting_price=10, price_increment=1)
    await fund(coins, TARGET, 30)
    await vip.claim_one_percent(GUILD, TARGET, 2000, one_percent)
    await vip.appoint_henchman(GUILD, TARGET, 9)
    await plot(heists)
    await snipe(snipes, sniper=4)

    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.dethroned and success.previous_henchman_id == 9
    assert success.heist.loot == 100  # 10% of what was left after buying the title
    assert await rows(db, OnePercentTitle) == []
    assert await vip.one_percent_price(GUILD, one_percent) == 1000


async def test_heisting_the_house(heists, snipes, coins):
    await fund(coins, 1, 10)
    await coins.pay_house(guild_id=GUILD, user_id=1, amount=1000, kind=CoinTransactionKind.BRIBE)
    await plot(heists, target=HOUSE)
    await snipe(snipes, sniper=1, target=HOUSE)
    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.heist.loot == 100 and await coins.balance(GUILD, HOUSE) == 900


async def test_heisting_the_house_in_debt_steals_nothing(heists, snipes, coins):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=500, kind=CoinTransactionKind.BET_PAYOUT, from_user_id=HOUSE, to_user_id=1)
    await plot(heists, target=HOUSE)
    await snipe(snipes, sniper=1, target=HOUSE)
    [success] = await heists.resolve_snipes(GUILD, CONFIG)
    assert success.heist.loot == 0 and set(success.shares.values()) == {0}
    assert await coins.balance(GUILD, HOUSE) == -500
    assert await coins.balance(GUILD, 1) == 500


async def test_outside_snitch_fines_everyone(heists, coins):
    for user_id in [*CREW, 9]:
        await fund(coins, user_id, 4)
    await plot(heists)

    result = await heists.snitch(GUILD, 9, "Badger", CONFIG)
    assert result.fines == {1: 100, 2: 100, 3: 100, 4: 100, 9: 100}
    assert result.total == 500 and result.heist.snitch_reward == 250
    # The reward goes straight into the snitch's shadow pocket
    assert await coins.balance(GUILD, 9) == 300 and await coins.shadow_balance(GUILD, 9) == 250
    assert await coins.balance(GUILD, HOUSE) == 250

    with pytest.raises(NoSuchHeist):
        await heists.snitch(GUILD, 9, "badger", CONFIG)
    assert await coins.balance(GUILD, HOUSE) == 250


async def test_crew_snitch_is_fined_once(heists, coins):
    await fund(coins, 2, 4)
    await plot(heists)
    result = await heists.snitch(GUILD, 2, "badger", CONFIG)
    assert result.fines == {1: 0, 2: 100, 3: 0, 4: 0}
    assert await coins.balance(GUILD, 2) == 300


async def test_fines_and_reward_round_down_to_a_cheddar(heists, coins):
    for user_id in CREW[:3]:
        async with coins.db.transaction() as session:
            await coins.move(session, guild_id=GUILD, amount=130, kind=CoinTransactionKind.REACTION, to_user_id=user_id)
    await plot(heists)
    result = await heists.snitch(GUILD, 4, "badger", CONFIG)
    assert result.fines == {1: 30, 2: 30, 3: 30, 4: 0}  # 25% of 130 is 32.5, rounded down to 3 cheddar
    assert result.heist.snitch_reward == 40  # Half of 90 is 45, rounded down to 4 cheddar
    assert await coins.shadow_balance(GUILD, 4) == 40
    assert await coins.balance(GUILD, HOUSE) == 50


async def test_snitch_reward_is_paid_on_the_spot(heists, coins):
    for user_id in CREW:
        await fund(coins, user_id, 4)
    heist = await plot(heists)
    await heists.snitch(GUILD, 2, "badger", CONFIG)
    assert heist.id is not None
    assert await coins.balance(GUILD, 2) == 300 and await coins.shadow_balance(GUILD, 2) == 200
    assert await coins.balance(GUILD, HOUSE) == 200
    assert await heists.unpaid_rewards(GUILD) == []
    assert await heists.pay_reward(heist.id) is None  # Already paid


async def test_rewards_owed_from_before_are_paid_once(heists, coins, db):
    for user_id in CREW:
        await fund(coins, user_id, 4)
    heist = await plot(heists)
    await heists.snitch(GUILD, 2, "badger", CONFIG)
    async with db.transaction() as session:  # As if it was snitched while rewards still waited a day
        old = await session.get(Heist, heist.id)
        assert old is not None
        old.reward_paid_at = None

    assert heist.id is not None
    assert [h.id for h in await heists.unpaid_rewards(GUILD)] == [heist.id]
    assert await heists.pay_reward(heist.id) == 200
    assert await heists.pay_reward(heist.id) is None
    assert await heists.unpaid_rewards(GUILD) == []
    assert await coins.shadow_balance(GUILD, 2) == 400


async def test_snitched_heist_cant_succeed(heists, snipes, coins):
    await fund(coins, TARGET, 100)
    await plot(heists)
    await heists.snitch(GUILD, 9, "badger", CONFIG)
    await snipe(snipes, sniper=1)
    assert await heists.resolve_snipes(GUILD, CONFIG) == []
    assert await coins.balance(GUILD, TARGET) == 10000


async def test_overdue_heists_expire(heists, snipes, coins, db):
    await fund(coins, TARGET, 100)
    old = await plot(heists)
    fresh = await heists.begin(GUILD, 5, 60, "otter", CONFIG)
    async with db.transaction() as session:
        heist = await session.get(Heist, old.id)
        assert heist is not None
        heist.ends_at = utcnow() - datetime.timedelta(seconds=1)

    with pytest.raises(NoSuchHeist):
        await heists.snitch(GUILD, 9, "badger", CONFIG)  # Closed, even before it's marked expired
    await snipe(snipes, sniper=1)  # Too late to count
    assert await heists.resolve_snipes(GUILD, CONFIG) == []
    assert [h.id for h in await heists.expire_overdue(GUILD)] == [old.id]
    assert await heists.expire_overdue(GUILD) == []
    assert (await heists.join(GUILD, 6, "otter"))[0].id == fresh.id
    assert await heists.begin(GUILD, 1, TARGET, "badger", CONFIG)  # The old crew and codeword are free again
