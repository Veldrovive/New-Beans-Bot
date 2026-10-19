import datetime

import pytest

from beans_bot.db.models import CoinTransactionKind, JailReason
from beans_bot.db.types import utcnow
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.jail import AlreadyJailed

from conftest import GUILD, HOUSE


async def start(jail, user_id, length=datetime.timedelta(hours=1)):
    return await jail.start_sentence(guild_id=GUILD, user_id=user_id, channel_id=5, offending_message_id=6, length=length, reason=JailReason.VOTE)


async def test_one_sentence_at_a_time(jail):
    assert await start(jail, 1) is not None
    assert await start(jail, 1) is None
    assert await jail.is_jailed(GUILD, 1)
    assert await jail.end_sentence(GUILD, 1, "served")
    assert not await jail.is_jailed(GUILD, 1)
    assert not await jail.end_sentence(GUILD, 1, "served")
    assert await start(jail, 1) is not None
    assert await jail.times_jailed(GUILD, 1) == 2


async def test_message_jails_once(jail):
    assert await jail.claim_message(GUILD, 6)
    assert not await jail.claim_message(GUILD, 6)
    await jail.unclaim_message(GUILD, 6)
    assert await jail.claim_message(GUILD, 6)


async def test_humiliated_once(jail):
    sentence = await start(jail, 1)
    assert await jail.mark_humiliated(sentence.id)
    assert not await jail.mark_humiliated(sentence.id)


async def test_due_for_release(jail):
    await start(jail, 1, datetime.timedelta(seconds=-1))
    await start(jail, 2, datetime.timedelta(hours=1))
    assert [s.user_id for s in await jail.due_for_release(utcnow())] == [1]


async def test_leaderboard(jail):
    for _ in range(2):
        await start(jail, 1)
        await jail.end_sentence(GUILD, 1, "served")
    await start(jail, 2)
    assert await jail.leaderboard(GUILD) == [(1, 2), (2, 1)]


async def test_pending_rename_claimed_once(jail):
    assert await jail.create_pending_rename(guild_id=GUILD, prompt_message_id=9, target_user_id=1, offending_message_id=6)
    assert not await jail.create_pending_rename(guild_id=GUILD, prompt_message_id=10, target_user_id=1, offending_message_id=6)
    assert (await jail.claim_pending_rename(GUILD, 9)).target_user_id == 1
    assert await jail.claim_pending_rename(GUILD, 9) is None


async def test_tomatoes(jail):
    await jail.record_tomato(guild_id=GUILD, thrower_user_id=1, target_user_id=2, message_id=3, innocent=True)
    assert await jail.tomatoes_received(GUILD, 2) == 1


async def fund(coins, user_id, beans):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def frame(jail, framer_id, target_id, cost):
    return await jail.frame(
        guild_id=GUILD, framer_id=framer_id, target_id=target_id, cost=cost, channel_id=5, length=datetime.timedelta(hours=1)
    )


async def test_frame_charges_framer_and_jails_target(jail, coins):
    await fund(coins, 1, 5)
    sentence = await frame(jail, 1, 2, 300)
    assert await coins.balance(GUILD, 1) == 200
    assert await coins.balance(GUILD, HOUSE) == 300
    assert sentence.user_id == 2 and sentence.offending_message_id is None
    assert await jail.is_jailed(GUILD, 2)
    assert not await jail.is_jailed(GUILD, 1)


async def test_frame_without_enough_coins_changes_nothing(jail, coins):
    await fund(coins, 1, 5)
    with pytest.raises(InsufficientFunds):
        await frame(jail, 1, 2, 600)
    assert await coins.balance(GUILD, 1) == 500
    assert await coins.balance(GUILD, HOUSE) == 0
    assert not await jail.is_jailed(GUILD, 2)


async def test_frame_already_jailed_costs_nothing(jail, coins):
    await fund(coins, 1, 5)
    await start(jail, 2)
    with pytest.raises(AlreadyJailed):
        await frame(jail, 1, 2, 300)
    assert await coins.balance(GUILD, 1) == 500
    assert await coins.balance(GUILD, HOUSE) == 0
    assert await jail.times_jailed(GUILD, 2) == 1


async def test_refund_frame_undoes_it(jail, coins):
    await fund(coins, 1, 5)
    await frame(jail, 1, 2, 300)
    await jail.refund_frame(guild_id=GUILD, framer_id=1, target_id=2, cost=300)
    assert await coins.balance(GUILD, 1) == 500
    assert await coins.balance(GUILD, HOUSE) == 0
    assert not await jail.is_jailed(GUILD, 2)


async def test_resentence_records_the_new_crime(jail):
    first = await jail.start_sentence(
        guild_id=GUILD, user_id=1, channel_id=5, offending_message_id=6, length=datetime.timedelta(hours=1), reason=JailReason.VOTE
    )
    assert first is not None
    await jail.mark_humiliated(first.id)

    sentence = await jail.resentence(guild_id=GUILD, user_id=1, channel_id=5, length=datetime.timedelta(hours=2), reason=JailReason.RIGGED)
    assert sentence is not None
    current = await jail.current_sentence(GUILD, 1)
    assert current is not None
    assert (current.id, current.reason, current.humiliated) == (sentence.id, JailReason.RIGGED, True)
    assert current.ends_at > first.ends_at
    # The vote sentence stays in the history.
    history = await jail.times_jailed(GUILD, 1)
    assert history == 2


async def test_resentence_never_shortens(jail):
    first = await jail.start_sentence(
        guild_id=GUILD, user_id=1, channel_id=5, offending_message_id=None, length=datetime.timedelta(days=3), reason=JailReason.VOTE
    )
    assert first is not None
    sentence = await jail.resentence(guild_id=GUILD, user_id=1, channel_id=5, length=datetime.timedelta(hours=1), reason=JailReason.RIGGED)
    assert sentence is not None and sentence.ends_at == first.ends_at


async def test_resentence_needs_a_prisoner(jail):
    assert await jail.resentence(guild_id=GUILD, user_id=1, channel_id=5, length=datetime.timedelta(hours=1), reason=JailReason.RIGGED) is None
    assert await jail.current_sentence(GUILD, 1) is None
