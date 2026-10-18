import datetime

from beans_bot.db.types import utcnow

from conftest import GUILD


async def start(jail, user_id, length=datetime.timedelta(hours=1)):
    return await jail.start_sentence(guild_id=GUILD, user_id=user_id, channel_id=5, offending_message_id=6, length=length)


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
