"""
The website's actions, through the API. Success changes exactly what it should (both sides of every
coin move); failure changes nothing. Discord is either missing (503) or a small fake.
"""

import datetime
from typing import Any, cast

import discord
import httpx
import pytest

from beans_bot.bot import BeansBot
from beans_bot.config.schema import DayPassConfig, OnePercentConfig
from beans_bot.db.models import CoinTransactionKind, JailReason
from beans_bot.web.app import create_app
from beans_bot.web.context import WebContext

from conftest import GUILD, HOUSE, MANIFESTO_AUTHOR

MEMBER = 2**60 + 5
THREAD = 2**60 + 9


async def fund(services, user_id, beans):
    async with services.db.transaction() as session:
        await services.coins.move(session, guild_id=GUILD, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def post(client, path, json=None):
    return await client.post(f"/api/guilds/{GUILD}{path}", json=json)


async def balances(services, user_id=MEMBER):
    coins = services.coins
    return (await coins.balance(GUILD, user_id), await coins.shadow_balance(GUILD, user_id), await coins.balance(GUILD, HOUSE))


async def ledger_size(services):
    return (await services.reports.ledger(GUILD))[1]


# --- Coins ------------------------------------------------------------------------------


async def test_give_and_take_move_coins_with_the_house(web_client, web_services):
    response = await post(web_client, "/coins/give", {"user_id": str(MEMBER), "amount": "5"})
    assert response.status_code == 200, response.text
    assert response.json()["balance"] == {"value": 500, "text": "5 BeanCoins"}
    assert await balances(web_services) == (500, 0, -500)

    response = await post(web_client, "/coins/give", {"user_id": str(MEMBER), "amount": "2", "shadow": True})
    assert response.json()["balance"]["value"] == 200
    assert await balances(web_services) == (500, 200, -700)

    response = await post(web_client, "/coins/take", {"user_id": str(MEMBER), "amount": "3"})
    assert response.status_code == 200 and response.json()["balance"]["value"] == 200
    assert await balances(web_services) == (200, 200, -400)

    rows, _ = await web_services.reports.ledger(GUILD)
    assert {(r.kind, r.actor_user_id) for r in rows} == {("admin_give", 42), ("admin_take", 42)}


async def test_take_more_than_they_have_changes_nothing(web_client, web_services):
    await fund(web_services, MEMBER, 1)
    response = await post(web_client, "/coins/take", {"user_id": str(MEMBER), "amount": "5"})
    assert response.status_code == 409
    assert "1 BeanCoin" in response.json()["detail"] and "5 BeanCoins" in response.json()["detail"]
    assert await balances(web_services) == (100, 0, 0)
    assert await ledger_size(web_services) == 1


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"user_id": str(MEMBER), "amount": "lots"}, 400),
        ({"user_id": str(MEMBER), "amount": "-5"}, 400),
        ({"user_id": str(MEMBER), "amount": "1 cheddar"}, 400),  # Cheddar is off in the tests
        ({"user_id": "not a number", "amount": "5"}, 400),
        ({"user_id": str(HOUSE), "amount": "5"}, 400),
        ({"amount": "5"}, 400),
    ],
)
async def test_bad_give_requests_change_nothing(web_client, web_services, body, status):
    response = await post(web_client, "/coins/give", body)
    assert response.status_code == status, response.text
    assert isinstance(response.json()["detail"], str)
    assert await ledger_size(web_services) == 0


async def test_give_needs_the_actor_and_the_house(web_client, web_services):
    web_services.config.web.actor_user_id = None
    response = await post(web_client, "/coins/give", {"user_id": str(MEMBER), "amount": "5"})
    assert response.status_code == 400 and "actor_user_id" in response.json()["detail"]
    web_services.config.web.actor_user_id = 42
    web_services.coins.house_user_id = None
    assert (await post(web_client, "/coins/take", {"user_id": str(MEMBER), "amount": "5"})).status_code == 503
    assert await ledger_size(web_services) == 0


# --- Discord-only actions without Discord ------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/admin/say", {"channel_id": "10", "message": "hello"}),
        ("/admin/dm", {"user_id": str(MEMBER), "message": "hello"}),
        ("/jail/release", {"user_id": str(MEMBER)}),
        ("/jail/imprison", {"user_id": str(MEMBER)}),
        ("/auto-bets/post", None),
    ],
)
async def test_discord_actions_need_the_bot(web_client, path, body):
    response = await post(web_client, path, body)
    assert response.status_code == 503 and response.json()["detail"]


async def test_sync_needs_the_bot(web_client):
    assert (await web_client.post("/api/admin/sync")).status_code == 503


# --- Markets ------------------------------------------------------------------------------


@pytest.fixture
async def market(web_services):
    await fund(web_services, 1, 10)
    await fund(web_services, 2, 10)
    await web_services.markets.create(guild_id=GUILD, thread_id=THREAD, creator_id=1, choices=["yes", "no"])
    await web_services.markets.place_bet(THREAD, 1, "yes", 300)
    await web_services.markets.place_bet(THREAD, 2, "no", 100)


async def test_house_bet_is_secret_and_paid_by_the_house(web_client, web_services, market):
    response = await post(web_client, f"/markets/{THREAD}/house-bet", {"choice": "No", "amount": "2"})
    assert response.status_code == 200, response.text
    assert "couldn't post" in response.json()["message"]  # No Discord here
    assert await web_services.coins.balance(GUILD, HOUSE) == -200
    stats = await web_services.markets.stats(THREAD)
    assert stats.pool == 400  # Still a secret from members
    assert (await post(web_client, f"/markets/{THREAD}/house-bet", {"choice": "maybe", "amount": "2"})).status_code == 400
    assert await web_services.coins.balance(GUILD, HOUSE) == -200


async def test_close_then_cancel_refunds_everyone(web_client, web_services, market):
    assert (await post(web_client, f"/markets/{THREAD}/close")).status_code == 200
    assert (await web_services.markets.get(THREAD)).status == "closed"
    assert (await post(web_client, f"/markets/{THREAD}/close")).status_code == 400  # Already closed
    response = await post(web_client, f"/markets/{THREAD}/cancel")
    assert response.status_code == 200 and "2 bets refunded" in response.json()["message"]
    assert (await web_services.coins.balance(GUILD, 1), await web_services.coins.balance(GUILD, 2)) == (1000, 1000)
    assert (await post(web_client, f"/markets/{THREAD}/cancel")).status_code == 400


async def test_nullify_takes_back_the_payouts(web_client, web_services, market):
    assert (await post(web_client, f"/markets/{THREAD}/nullify")).status_code == 400  # Not resolved yet
    await web_services.markets.resolve(THREAD, 1, "yes", rigged_window=datetime.timedelta(0))
    assert await web_services.coins.balance(GUILD, 1) == 1100
    response = await post(web_client, f"/markets/{THREAD}/nullify")
    assert response.status_code == 200, response.text
    assert (await web_services.coins.balance(GUILD, 1), await web_services.coins.balance(GUILD, 2)) == (1000, 1000)
    assert (await web_services.markets.get(THREAD)).status == "nullified"


async def test_market_actions_stay_in_their_guild(web_client, web_services, market):
    other = "/api/guilds/12345/markets"
    assert (await web_client.post(f"{other}/{THREAD}/close")).status_code == 404
    assert (await post(web_client, "/markets/777/cancel")).status_code == 404
    assert (await web_services.markets.get(THREAD)).status == "open"


# --- VIP ------------------------------------------------------------------------------------


async def test_end_a_day_pass_early(web_client, web_services):
    await fund(web_services, 1, 10)
    day_pass = await web_services.vip.buy_day_pass(GUILD, 1, DayPassConfig(role_id=40))
    response = await post(web_client, f"/vip/passes/{day_pass.id}/end")
    assert response.status_code == 200, response.text
    assert await web_services.vip.active_pass(GUILD, 1) is None
    assert await web_services.coins.balance(GUILD, 1) == 800  # No refund
    assert (await post(web_client, f"/vip/passes/{day_pass.id}/end")).status_code == 409
    assert (await post(web_client, "/vip/passes/9999/end")).status_code == 404


async def test_revoke_the_one_percent(web_client, web_services):
    assert (await post(web_client, "/vip/one-percent/revoke")).status_code == 409
    await fund(web_services, 1, 20)
    await web_services.vip.claim_one_percent(GUILD, 1, 1500, OnePercentConfig(role_id=41))
    await web_services.vip.appoint_henchman(GUILD, 1, 2)
    assert (await post(web_client, "/vip/one-percent/revoke")).status_code == 200
    assert await web_services.vip.one_percent(GUILD) is None
    assert await web_services.coins.balance(GUILD, 1) == 500  # No refund
    assert await web_services.vip.one_percent_price(GUILD, OnePercentConfig(role_id=41)) == 1000


# --- Manifestos -------------------------------------------------------------------------------


async def test_add_edit_and_remove_wisdom(web_client, web_services):
    author = str(MANIFESTO_AUTHOR)
    response = await post(web_client, f"/manifestos/{author}/wisdom", {"text": "  Beans are a fruit.  "})
    assert response.status_code == 200 and "#3" in response.json()["message"]
    response = await web_client.patch(f"/api/guilds/{GUILD}/manifestos/{author}/wisdom/3", json={"text": "Beans are a vegetable."})
    assert response.status_code == 200
    (manifesto,) = await web_services.manifesto.manifestos(GUILD)
    assert manifesto.wisdom[-1] == "Beans are a vegetable."

    for method, index in (("patch", 1), ("delete", 1), ("delete", 9)):
        kwargs = {"json": {"text": "nope"}} if method == "patch" else {}
        response = await getattr(web_client, method)(f"/api/guilds/{GUILD}/manifestos/{author}/wisdom/{index}", **kwargs)
        assert response.status_code == 409, (method, index)
    assert (await web_client.delete(f"/api/guilds/{GUILD}/manifestos/{author}/wisdom/3")).status_code == 200
    (manifesto,) = await web_services.manifesto.manifestos(GUILD)
    assert len(manifesto.lines) == 2
    assert (await post(web_client, f"/manifestos/{author}/wisdom", {"text": "   "})).status_code == 400
    assert (await web_client.delete(f"/api/guilds/{GUILD}/manifestos/55/wisdom/1")).status_code == 404


# --- With a fake Discord -----------------------------------------------------------------------


class FakeRole:
    def __init__(self, role_id):
        self.id = role_id


class FakeMember:
    def __init__(self, user_id, bot=False):
        self.id = user_id
        self.bot = bot
        self.roles: set[int] = set()

    async def add_roles(self, role, reason=None):
        self.roles.add(role.id)

    async def remove_roles(self, role, reason=None):
        self.roles.discard(role.id)


class FakeMessage:
    jump_url = "https://discord.com/channels/1/10/99"


class FakeChannel(discord.abc.Messageable):
    def __init__(self, channel_id):
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> Any:
        self.sent.append(content or "")
        return FakeMessage()


class FakeGuild:
    def __init__(self, guild_id, members):
        self.id = guild_id
        self.members = {m.id: m for m in members}
        self.channels: dict[int, FakeChannel] = {}

    def get_member(self, user_id):
        return self.members.get(user_id)

    async def fetch_member(self, user_id):
        member = self.members.get(user_id)
        if member is None:
            raise discord.NotFound(type("R", (), {"status": 404, "reason": "gone"})(), "Unknown Member")
        return member

    def get_role(self, role_id):
        return FakeRole(role_id)

    def get_channel_or_thread(self, channel_id):
        return self.channels.get(channel_id)


class FakeBot:
    def __init__(self, guild):
        self.guild = guild
        self.user = None
        self.cogs: dict = {}

    def is_ready(self):
        return True

    def is_closed(self):
        return False

    def get_guild(self, guild_id):
        return self.guild if guild_id == self.guild.id else None

    def get_channel(self, channel_id):
        return self.guild.channels.get(channel_id)


@pytest.fixture
async def discord_client(web_services):
    """The API with a fake Discord: one guild, MEMBER in it."""
    guild = FakeGuild(GUILD, [FakeMember(MEMBER), FakeMember(HOUSE, bot=True)])
    app = create_app(WebContext(config=web_services.config, services=web_services, bot=cast(BeansBot, FakeBot(guild))))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Beans-Admin": "1"}) as client:
        yield client, guild


async def test_imprison_and_release_move_the_role(discord_client, web_services):
    client, guild = discord_client
    member = guild.members[MEMBER]
    response = await post(client, "/jail/imprison", {"user_id": str(MEMBER)})
    assert response.status_code == 200, response.text
    sentence = await web_services.jail.current_sentence(GUILD, MEMBER)
    assert sentence is not None and sentence.reason == JailReason.ADMIN and sentence.channel_id == 10
    assert member.roles == {20}
    assert (await post(client, "/jail/imprison", {"user_id": str(MEMBER)})).status_code == 409

    response = await post(client, "/jail/release", {"user_id": str(MEMBER)})
    assert response.status_code == 200, response.text
    assert await web_services.jail.current_sentence(GUILD, MEMBER) is None
    assert member.roles == set()
    assert (await post(client, "/jail/release", {"user_id": str(MEMBER)})).status_code == 409
    assert (await post(client, "/jail/imprison", {"user_id": "12345"})).status_code == 409  # Not in the server


async def test_say_posts_in_the_channel_and_market_actions_announce(discord_client, web_services, market):
    client, guild = discord_client
    guild.channels[10] = FakeChannel(10)
    guild.channels[THREAD] = FakeChannel(THREAD)
    response = await post(client, "/admin/say", {"channel_id": "10", "message": "Peasants."})
    assert response.status_code == 200 and response.json()["jump_url"].startswith("https://discord.com/")
    assert guild.channels[10].sent == ["Peasants."]
    assert (await post(client, "/admin/say", {"channel_id": "11", "message": "hi"})).status_code == 404
    assert (await post(client, "/admin/say", {"channel_id": "10", "message": ""})).status_code == 400

    assert (await post(client, f"/markets/{THREAD}/close")).status_code == 200
    assert guild.channels[THREAD].sent == ["Betting has been closed."]
