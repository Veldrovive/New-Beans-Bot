import discord
import pytest
from pydantic import ValidationError

from beans_bot.config.schema import BotConfig, CouncilConfig, CouncilMemberConfig, GuildConfig, SnipesConfig
from beans_bot.db.models import SnipeVote
from beans_bot.services.council import CouncilService
from beans_bot.services.snipes import AlreadySniped

from conftest import GUILD

PHOTO = 500
SNIPER = 1
TARGET = 2
TEDS = (10, 20, 30, 40)
NEEDED = 3  # More than half of four


def council_config(user_ids=TEDS) -> CouncilConfig:
    return CouncilConfig(
        members={
            f"ted{i}": CouncilMemberConfig(
                internal_name=f"Ted {i}", ted_id=i, council_name=f"The Ted Number {i}", user_id=user_id, crest_emoji=f":crest{i}:"
            )
            for i, user_id in enumerate(user_ids, start=1)
        }
    )


def council_service(user_ids=TEDS) -> CouncilService:
    guild = GuildConfig(name="Test", council_of_teds=council_config(user_ids))
    return CouncilService(BotConfig(bot_token="x", guilds={GUILD: guild}))


async def start(snipes):
    return await snipes.start(GUILD, 100, PHOTO, SNIPER, TARGET)


def test_council_majority_and_crests():
    council = council_service()
    assert council.majority(GUILD) == 3
    assert council_service((10,)).majority(GUILD) == 1
    assert council_service((10, 20)).majority(GUILD) == 2

    owner = council.crest_owner(GUILD, discord.PartialEmoji(name="crest2", id=1234))
    assert owner is not None and owner.user_id == 20
    owner = council.crest_owner(GUILD, discord.PartialEmoji(name="crest3", id=5678))
    assert owner is not None and owner.user_id == 30
    assert council.crest_owner(GUILD, "🍅") is None
    member = council.member(GUILD, 40)
    assert member is not None and member.council_name == "The Ted Number 4"
    assert council.member(GUILD, SNIPER) is None
    assert council.members(12345) == [] and council.crest_owner(12345, discord.PartialEmoji(name="crest1", id=1)) is None


def test_snipes_need_a_council():
    with pytest.raises(ValidationError):
        GuildConfig(name="Test", snipes=SnipesConfig(channel_id=1))
    GuildConfig(name="Test", snipes=SnipesConfig(channel_id=1), council_of_teds=council_config())


async def test_start_once(snipes):
    snipe = await start(snipes)
    assert snipe.verified_at is None and snipe.target_user_id == TARGET
    with pytest.raises(AlreadySniped):
        await snipes.start(GUILD, 100, PHOTO, 3, 4)
    assert (await snipes.get(PHOTO)).sniper_user_id == SNIPER
    assert [s.message_id for s in await snipes.pending()] == [PHOTO]


async def test_majority_verifies_once(snipes):
    await start(snipes)
    assert not await snipes.vote(PHOTO, 10, NEEDED)
    assert not await snipes.vote(PHOTO, 10, NEEDED)  # The same crest twice is one vote
    assert not await snipes.vote(PHOTO, 20, NEEDED)
    assert (await snipes.get(PHOTO)).verified_at is None

    assert await snipes.vote(PHOTO, 30, NEEDED)
    assert (await snipes.get(PHOTO)).verified_at is not None
    assert await snipes.pending() == []

    assert not await snipes.vote(PHOTO, 40, NEEDED)  # Already verified
    assert await snipes.voter_ids(PHOTO) == [10, 20, 30, 40]


async def test_taking_a_crest_back(snipes):
    await start(snipes)
    await snipes.vote(PHOTO, 10, NEEDED)
    await snipes.vote(PHOTO, 20, NEEDED)
    await snipes.unvote(PHOTO, 20)
    assert await snipes.voter_ids(PHOTO) == [10]
    assert not await snipes.vote(PHOTO, 30, NEEDED)  # Only two live votes
    assert await snipes.vote(PHOTO, 20, NEEDED)  # Put it back

    await snipes.unvote(PHOTO, 10)  # Verified snipes keep their votes
    assert await snipes.voter_ids(PHOTO) == [10, 30, 20]
    assert (await snipes.get(PHOTO)).verified_at is not None


async def test_votes_on_other_messages_do_nothing(snipes):
    assert not await snipes.vote(PHOTO, 10, 1)
    await snipes.unvote(PHOTO, 10)
    assert not await snipes.sync_votes(PHOTO, {10}, 1)
    async with snipes.db.transaction() as session:
        assert await session.get(SnipeVote, (PHOTO, 10)) is None


async def test_sync_matches_reactions(snipes):
    await start(snipes)
    await snipes.vote(PHOTO, 10, NEEDED)
    await snipes.vote(PHOTO, 20, NEEDED)

    # 20 took their crest back and 30 added theirs while the bot was down.
    assert not await snipes.sync_votes(PHOTO, {10, 30}, NEEDED)
    assert sorted(await snipes.voter_ids(PHOTO)) == [10, 30]

    assert await snipes.sync_votes(PHOTO, {10, 20, 30}, NEEDED)
    assert not await snipes.sync_votes(PHOTO, {10, 20, 30, 40}, NEEDED)  # Already verified
    assert sorted(await snipes.voter_ids(PHOTO)) == [10, 20, 30]


async def test_alert_message_is_remembered(snipes):
    await start(snipes)
    await snipes.set_alert_message(PHOTO, 777)
    assert (await snipes.get(PHOTO)).alert_message_id == 777
