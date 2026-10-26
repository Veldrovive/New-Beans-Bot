import datetime
import json

import pytest
from pydantic import ValidationError
from sqlmodel import select

from beans_bot.db.models import AutoBetPost, PredictionMarket
from beans_bot.db.types import utcnow
from beans_bot.services.auto_bets import AutoBet, load_bets
from beans_bot.services.errors import UserFacingError

from conftest import GUILD, HOUSE

ADMIN = 7
MEMBER = 8


def bet(template: str, **kwargs) -> AutoBet:
    return AutoBet.model_validate({"bet": template, "admins": [ADMIN], **kwargs})


def test_bet_defaults_and_coin_range():
    b = bet("{person} leaves before {time}", params={"person": ["Andy", "Aidan"], "time": "5pm"}, bot_bet=[1, 2.5])
    assert b.choices == ["yes", "no"]
    assert b.bot_bet == (100, 250)
    assert b.options("time") == ["5pm"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bet": "{person} leaves"},  # Placeholder with no params
        {"bet": "Andy leaves", "params": {"person": ["Andy"]}},  # Param never used
        {"bet": "{person} leaves", "params": {"person": []}},
        {"bet": "Rain?", "choices": ["yes"]},
        {"bet": "Rain?", "choices": ["yes", "no way"]},
        {"bet": "Rain?", "bot_bet": [5, 1]},
        {"bet": "Rain?", "admins": []},
        {"bet": "Rain?", "surprise": True},
    ],
)
def test_invalid_bets(kwargs):
    with pytest.raises(ValidationError):
        AutoBet.model_validate({"admins": [ADMIN], **kwargs})


def test_load_bets_rejects_duplicates(tmp_path):
    path = tmp_path / "bets.json"
    path.write_text(json.dumps([{"bet": "Rain?", "admins": [ADMIN]}] * 2))
    with pytest.raises(ValueError):
        load_bets(path)


async def test_draw_fills_in_values(auto_bets):
    b = bet("{person} leaves before {time}", params={"person": ["Andy", "Aidan"], "time": "5pm"})
    draw = await auto_bets.draw(GUILD, [b])
    assert draw.values["time"] == "5pm" and draw.values["person"] in ("Andy", "Aidan")
    assert draw.text == f"{draw.values['person']} leaves before 5pm"
    assert draw.round == 1


async def test_record_opens_a_market_the_admins_manage(auto_bets, markets):
    draw = await auto_bets.draw(GUILD, [bet("Rain?", choices=["rain", "shine"])])
    await auto_bets.record(GUILD, draw, thread_id=500, creator_id=HOUSE)

    market = await markets.get(500)
    assert market is not None
    assert market.creator_id == HOUSE and market.admin_ids == [ADMIN] and market.choices == ["rain", "shine"]
    with pytest.raises(UserFacingError):
        await markets.close(500, MEMBER)
    await markets.close(500, ADMIN)
    assert (await markets.resolve(500, ADMIN, "rain")).winning_choice == "rain"


async def test_every_bet_is_used_before_a_new_round(auto_bets, db):
    bets = [bet("One?"), bet("Two?"), bet("Three?")]
    seen = []
    for thread_id in range(1, 7):
        draw = await auto_bets.draw(GUILD, bets)
        await auto_bets.record(GUILD, draw, thread_id=thread_id, creator_id=HOUSE)
        seen.append((draw.round, draw.bet.bet))

    assert [r for r, _ in seen] == [1, 1, 1, 2, 2, 2]
    assert sorted(t for r, t in seen if r == 1) == ["One?", "Three?", "Two?"]
    assert sorted(t for r, t in seen if r == 2) == ["One?", "Three?", "Two?"]

    # Another guild has its own rounds.
    assert (await auto_bets.draw(GUILD + 1, bets)).round == 1
    async with db.transaction() as session:
        assert len((await session.exec(select(AutoBetPost))).all()) == 6


async def test_new_bets_join_the_current_round(auto_bets):
    first = await auto_bets.draw(GUILD, [bet("One?")])
    await auto_bets.record(GUILD, first, thread_id=1, creator_id=HOUSE)
    draw = await auto_bets.draw(GUILD, [bet("One?"), bet("Two?")])
    assert (draw.round, draw.bet.bet) == (1, "Two?")


async def test_posted_since(auto_bets):
    before = utcnow()
    assert not await auto_bets.posted_since(GUILD, before - datetime.timedelta(days=1))
    draw = await auto_bets.draw(GUILD, [bet("Rain?")])
    await auto_bets.record(GUILD, draw, thread_id=1, creator_id=HOUSE)
    assert await auto_bets.posted_since(GUILD, before)
    assert not await auto_bets.posted_since(GUILD, utcnow() + datetime.timedelta(seconds=1))
    assert not await auto_bets.posted_since(GUILD + 1, before)


def test_bot_bet_amount(auto_bets):
    assert auto_bets.bot_bet_amount(bet("Rain?")) is None
    amounts = {auto_bets.bot_bet_amount(bet("Rain?", bot_bet=[1, 3])) for _ in range(200)}
    assert amounts == {100, 200, 300}
    assert auto_bets.bot_bet_amount(bet("Rain?", bot_bet=[0, 0])) is None
    assert auto_bets.bot_bet_amount(bet("Rain?", bot_bet=[0.5, 0.75])) is None  # No whole coin fits


async def test_failed_record_leaves_no_post(auto_bets, markets, db):
    await markets.create(guild_id=GUILD, thread_id=1, creator_id=MEMBER, choices=["yes", "no"])
    draw = await auto_bets.draw(GUILD, [bet("Rain?")])
    with pytest.raises(Exception):
        await auto_bets.record(GUILD, draw, thread_id=1, creator_id=HOUSE)  # Thread already has a market
    async with db.transaction() as session:
        assert (await session.exec(select(AutoBetPost))).all() == []
        market = await session.get(PredictionMarket, 1)
        assert market is not None and market.creator_id == MEMBER
