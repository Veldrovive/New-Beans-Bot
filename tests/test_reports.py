"""The website's read-only queries, against state made by the real services."""

import datetime

from beans_bot.config.schema import DayPassConfig, HeistConfig, OnePercentConfig
from beans_bot.db.models import (
    ActionHolidayPost,
    BirthdayMessage,
    ClassifierCorrection,
    ClassifierProcessedMessage,
    CoinTransactionKind,
    FatCowConsumption,
    FatCowVisit,
    JailReason,
    PendingRename,
    RoleNamePoll,
)
from beans_bot.services.auto_bets import AutoBet, Draw
from beans_bot.services.heists import HeistService
from beans_bot.services.snipes import SnipeService

from conftest import GUILD, HOUSE

DAY = datetime.timedelta(days=1)


async def fund(coins, user_id, beans):
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def sentence(jail, user_id, reason=JailReason.VOTE):
    return await jail.start_sentence(guild_id=GUILD, user_id=user_id, channel_id=5, offending_message_id=None, length=DAY, reason=reason)


async def test_member_summaries_merge_every_table(reports, coins, jail, vip):
    await fund(coins, 1, 20)
    await coins.admin_give(GUILD, 7, 1, 300, shadow=True)
    await sentence(jail, 2)
    await jail.end_sentence(GUILD, 2, "served")
    await sentence(jail, 2)
    await jail.record_tomato(guild_id=GUILD, thrower_user_id=1, target_user_id=2, message_id=9, innocent=False)
    await vip.buy_day_pass(GUILD, 1, DayPassConfig(role_id=1))
    await vip.claim_one_percent(GUILD, 1, 1000, OnePercentConfig(role_id=2))
    await vip.appoint_henchman(GUILD, 1, 3)
    await fund(coins, 4, 1)  # Another guild's records don't count
    async with coins.db.transaction() as session:
        await coins.move(session, guild_id=GUILD + 1, amount=100, kind=CoinTransactionKind.REACTION, to_user_id=5)

    by_user = {s.user_id: s for s in await reports.member_summaries(GUILD)}
    assert set(by_user) == {1, 2, 3, 4, HOUSE}
    one, two, three = by_user[1], by_user[2], by_user[3]
    assert (one.balance, one.shadow_balance, sorted(one.vip), one.jailed) == (2000 - 200 - 1000, 300, ["one_percent", "pass"], False)
    assert (two.times_jailed, two.jailed, two.tomatoes_received, two.balance) == (2, True, 1, 0)
    assert three.vip == ["henchman"]


async def test_ledger_filters_and_pages(reports, coins):
    await fund(coins, 1, 5)
    await coins.transfer(GUILD, 1, 2, 100)
    await coins.admin_give(GUILD, 7, 3, 100, shadow=False)

    rows, total = await reports.ledger(GUILD)
    assert total == 3 and [r.kind for r in rows] == ["admin_give", "transfer", "reaction"]
    rows, total = await reports.ledger(GUILD, user_id=2)
    assert total == 1 and rows[0].kind == "transfer"
    assert (await reports.ledger(GUILD, user_id=7))[1] == 1  # The actor counts
    rows, total = await reports.ledger(GUILD, kind="reaction")
    assert total == 1 and rows[0].to_user_id == 1
    rows, total = await reports.ledger(GUILD, limit=1, offset=1)
    assert total == 3 and [r.kind for r in rows] == ["transfer"]
    assert [a.user_id for a in await reports.accounts(GUILD)] == [1, 2, 3, HOUSE]


async def test_sentences_and_jail_stats(reports, jail):
    await sentence(jail, 1)
    await jail.end_sentence(GUILD, 1, "served")
    await sentence(jail, 1)
    await sentence(jail, 2)
    await jail.record_tomato(guild_id=GUILD, thrower_user_id=3, target_user_id=1, message_id=1, innocent=False)
    await jail.record_tomato(guild_id=GUILD, thrower_user_id=3, target_user_id=4, message_id=2, innocent=True)
    await jail.create_pending_rename(guild_id=GUILD, prompt_message_id=50, target_user_id=1, offending_message_id=40)
    await jail.create_pending_rename(guild_id=GUILD, prompt_message_id=51, target_user_id=2, offending_message_id=41)
    await jail.claim_pending_rename(GUILD, 51)

    rows, total = await reports.sentences(GUILD)
    assert total == 3 and rows[-1].released_at is not None
    assert (await reports.sentences(GUILD, user_id=2))[1] == 1
    stats = await reports.jail_stats(GUILD)
    assert stats.most_jailed == [(1, 2), (2, 1)]
    tomatoes = {t.user_id: t for t in stats.tomatoes}
    assert (tomatoes[3].thrown, tomatoes[3].at_innocents, tomatoes[3].received) == (2, 1, 0)
    assert (tomatoes[1].received, tomatoes[4].received) == (1, 1)
    assert [r.prompt_message_id for r in stats.pending_renames] == [50]
    assert isinstance(stats.pending_renames[0], PendingRename)


async def test_markets_show_the_house_bet(reports, markets, coins):
    await fund(coins, 1, 10)
    await fund(coins, 2, 10)
    await markets.create(guild_id=GUILD, thread_id=100, creator_id=1, choices=["yes", "no"])
    await markets.create(guild_id=GUILD, thread_id=200, creator_id=2, choices=["a", "b"])
    await markets.place_bet(100, 2, "yes", 300)
    await markets.house_bet(100, "no", 200)
    await markets.cancel(200, None)

    rows, total = await reports.markets(GUILD)
    assert total == 2
    by_id = {r.market.thread_id: r for r in rows}
    assert (by_id[100].pool, by_id[100].bets) == (500, 2) and by_id[200].pool == 0
    assert [r.market.thread_id for r in (await reports.markets(GUILD, status="canceled"))[0]] == [200]
    assert [r.market.thread_id for r in (await reports.markets(GUILD, user_id=2))[0]] == [200, 100]  # Created one, bet on the other
    assert (await reports.markets(GUILD, user_id=1))[1] == 1

    detail = await reports.market_detail(100)
    assert detail is not None
    assert {(b.user_id, b.choice, b.amount) for b in detail.bets} == {(2, "yes", 300), (HOUSE, "no", 200)}
    assert detail.totals == {"yes": 300, "no": 200}
    assert [t.kind for t in detail.ledger] == ["bet_place", "bet_place"]
    assert detail.auto_bet is None
    assert await reports.market_detail(999) is None


async def test_auto_bet_history(reports, auto_bets):
    bet = AutoBet(bet="{who} wins", params={"who": ["a", "b"]}, admins=[1])
    for thread_id, round_ in ((10, 1), (11, 2)):
        draw = Draw(bet=bet, values={"who": "a"}, text="a wins", round=round_)
        await auto_bets.record(GUILD, draw, thread_id=thread_id, creator_id=HOUSE)
    history = await reports.auto_bet_posts(GUILD)
    assert (history.total, history.round, history.used_this_round) == (2, 2, ["{who} wins"])
    assert [p.thread_id for p in history.posts] == [11, 10]
    empty = await reports.auto_bet_posts(GUILD + 1)
    assert (empty.total, empty.round, empty.used_this_round) == (0, 1, [])
    detail = await reports.market_detail(11)
    assert detail is not None and detail.auto_bet is not None and detail.auto_bet.text == "a wins"


async def test_vip_pass_history(reports, vip, coins):
    await fund(coins, 1, 10)
    await fund(coins, 2, 10)
    first = await vip.buy_day_pass(GUILD, 1, DayPassConfig(role_id=1))
    await vip.buy_day_pass(GUILD, 2, DayPassConfig(role_id=1))
    assert first.id is not None
    assert await vip.end_pass(first.id, early=True)
    rows, total = await reports.vip_pass_history(GUILD)
    assert total == 2 and {r.user_id for r in rows} == {1, 2}
    rows, total = await reports.vip_pass_history(GUILD, user_id=1)
    assert total == 1 and rows[0].ended_at is not None


async def test_snipes_with_votes(reports, db):
    snipes = SnipeService(db)
    await snipes.start(GUILD, 5, 100, 1, 2)
    await snipes.start(GUILD, 5, 101, 3, 4)
    await snipes.vote(100, 10, needed=2)
    await snipes.vote(100, 20, needed=2, nay=True)
    await snipes.vote(100, 30, needed=2)
    await snipes.vote(101, 10, needed=2)
    await snipes.unvote(101, 10)

    rows, total = await reports.snipes(GUILD)
    assert total == 2
    by_id = {r.snipe.message_id: r for r in rows}
    assert (by_id[100].status, by_id[100].votes_for, by_id[100].votes_against) == ("verified", [10, 30], [20])
    assert (by_id[101].status, by_id[101].votes_for) == ("pending", [])
    assert [r.snipe.message_id for r in (await reports.snipes(GUILD, status="pending"))[0]] == [101]
    assert [r.snipe.message_id for r in (await reports.snipes(GUILD, user_id=2))[0]] == [100]


async def test_heists_with_crews(reports, db, coins, vip):
    heists = HeistService(db, coins, vip)
    config = HeistConfig()
    await heists.begin(GUILD, 1, 50, "badger", config)
    await heists.join(GUILD, 2, "badger")
    await heists.begin(GUILD, 3, 60, "otter", config)
    await heists.snitch(GUILD, 4, "otter", config)

    rows, total = await reports.heists(GUILD)
    assert total == 2
    active, _ = await reports.heists(GUILD, active=True)
    assert [r.heist.code for r in active] == ["badger"] and [m.user_id for m in active[0].crew] == [1, 2]
    finished, _ = await reports.heists(GUILD, active=False)
    assert [r.heist.code for r in finished] == ["otter"] and finished[0].heist.snitch_user_id == 4
    assert [r.heist.code for r in (await reports.heists(GUILD, user_id=2))[0]] == ["badger"]
    assert [r.heist.code for r in (await reports.heists(GUILD, user_id=4))[0]] == ["otter"]


async def test_daily_posts_fat_cow_role_polls_and_guesses(reports, db):
    today = datetime.date(2026, 11, 25)
    async with db.transaction() as session:
        session.add(BirthdayMessage(guild_id=GUILD, sent_on=today, channel_id=1, message_id=2, person_name="Andy", for_year=2026, used_holiday="Pie Day"))
        session.add(ActionHolidayPost(guild_id=GUILD, sent_on=today, holiday_name="Hug a Bean", holiday_link="https://example.com"))
        session.add(FatCowVisit(guild_id=GUILD))
        session.add(FatCowConsumption(guild_id=GUILD, user_id=1, meters=1.5))
        session.add(FatCowConsumption(guild_id=GUILD, user_id=1, meters=0.5))
        session.add(FatCowConsumption(guild_id=GUILD, user_id=2, meters=0))
        session.add(RoleNamePoll(poll_message_id=7, guild_id=GUILD, channel_id=1, initiator_user_id=1, adjective="Soggy"))
        session.add(ClassifierProcessedMessage(guild_id=GUILD, message_id=300, classifier_name="cats", channel_id=1, predicted_label="andy"))

    births, total = await reports.birthday_messages(GUILD)
    assert total == 1 and births[0].used_holiday == "Pie Day"
    assert (await reports.action_holiday_posts(GUILD))[0][0].holiday_name == "Hug a Bean"
    cow = await reports.fat_cow_stats(GUILD)
    assert len(cow.visits) == 1
    assert [(m.user_id, m.visits, m.total_meters, m.average_meters) for m in cow.members] == [(1, 2, 2.0, 1.0), (2, 1, 0.0, 0.0)]
    polls, _ = await reports.role_polls(GUILD)
    assert polls[0].adjective == "Soggy" and polls[0].active

    guesses, total = await reports.classifier_guesses(GUILD)
    assert total == 1 and guesses[0].correction is None
    async with db.transaction() as session:
        session.add(ClassifierCorrection(guild_id=GUILD, message_id=300, classifier_name="cats", previous_label="andy", label="bean", corrected_by=4))
        session.add(ClassifierCorrection(guild_id=GUILD, message_id=300, classifier_name="cats", previous_label="bean", label="merlin", corrected_by=5))
    guesses, _ = await reports.classifier_guesses(GUILD)
    correction = guesses[0].correction
    assert correction is not None and (correction.label, correction.corrected_by) == ("merlin", 5)
