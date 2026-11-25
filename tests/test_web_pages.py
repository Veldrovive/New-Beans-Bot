"""Every read-only page of the website answers, with IDs as strings and coins as integers plus text."""

import datetime

from beans_bot.config.schema import DayPassConfig, OnePercentConfig
from beans_bot.db.models import CoinTransactionKind, JailReason

from conftest import BIG_GUILD, GUILD, HOUSE, MANIFESTO_AUTHOR

BIG_USER = 2**60 + 11


async def fund(services, user_id, beans, guild_id=GUILD):
    async with services.db.transaction() as session:
        await services.coins.move(session, guild_id=guild_id, amount=beans * 100, kind=CoinTransactionKind.REACTION, to_user_id=user_id)


async def get(client, path, **params):
    response = await client.get(f"/api/guilds/{GUILD}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_members_and_coins(web_client, web_services):
    await fund(web_services, BIG_USER, 12)
    await web_services.coins.admin_give(GUILD, 42, BIG_USER, 50, shadow=True)
    await web_services.jail.start_sentence(
        guild_id=GUILD, user_id=BIG_USER, channel_id=1, offending_message_id=None, length=datetime.timedelta(hours=1), reason=JailReason.VOTE
    )

    members = await get(web_client, "/members")
    assert [m["user_id"] for m in members] == [str(BIG_USER)]  # The house isn't a member
    (member,) = members
    assert member["balance"] == {"value": 1200, "text": "12 BeanCoins"}
    assert member["shadow_balance"]["value"] == 50 and member["jailed"] is True and member["in_guild"] is None

    accounts = await get(web_client, "/coins/accounts")
    assert accounts["house"]["user_id"] == str(HOUSE) and accounts["house"]["balance"]["value"] == -50
    assert [a["user_id"] for a in accounts["accounts"]] == [str(BIG_USER)]

    ledger = await get(web_client, "/coins/ledger", user_id=str(BIG_USER))
    assert ledger["total"] == 2 and "admin_give" in ledger["kinds"]
    newest = ledger["items"][0]
    assert (newest["kind"], newest["to_user_id"], newest["from_user_id"], newest["actor_user_id"], newest["to_shadow"]) == (
        "admin_give", str(BIG_USER), str(HOUSE), "42", True,
    )  # fmt: skip
    assert (await get(web_client, "/coins/ledger", kind="reaction", limit=1))["total"] == 1
    assert (await web_client.get(f"/api/guilds/{GUILD}/coins/ledger", params={"limit": 500})).status_code == 400


async def test_jail_pages(web_client, web_services):
    jail = web_services.jail
    await jail.start_sentence(guild_id=GUILD, user_id=7, channel_id=1, offending_message_id=99, length=datetime.timedelta(hours=1), reason=JailReason.VOTE)
    await jail.record_tomato(guild_id=GUILD, thrower_user_id=8, target_user_id=7, message_id=3, innocent=False)
    current = await get(web_client, "/jail/current")
    assert [(s["user_id"], s["offending_message_id"], s["reason"]) for s in current] == [("7", "99", "vote")]
    assert (await get(web_client, "/jail/sentences", user_id="7"))["total"] == 1
    stats = await get(web_client, "/jail/stats")
    assert stats["most_jailed"] == [{"user_id": "7", "count": 1}]
    assert {t["user_id"]: t["received"] for t in stats["tomatoes"]} == {"7": 1, "8": 0}


async def test_market_pages_reveal_the_house_bet(web_client, web_services):
    markets = web_services.markets
    await fund(web_services, 3, 10)
    await markets.create(guild_id=GUILD, thread_id=BIG_USER, creator_id=3, choices=["yes", "no"])
    await markets.place_bet(BIG_USER, 3, "yes", 200)
    await markets.house_bet(BIG_USER, "no", 300)

    listing = await get(web_client, "/markets", status="open")
    assert listing["total"] == 1 and listing["items"][0]["thread_id"] == str(BIG_USER)
    assert listing["items"][0]["pool"]["value"] == 500
    detail = await get(web_client, f"/markets/{BIG_USER}")
    secret = [b for b in detail["bets"] if b["secret"]]
    assert [(b["user_id"], b["choice"], b["amount"]["value"]) for b in secret] == [(str(HOUSE), "no", 300)]
    assert {t["choice"]: t["amount"]["value"] for t in detail["totals"]} == {"yes": 200, "no": 300}
    assert len(detail["ledger"]) == 2 and detail["auto_bet"] is None
    assert (await web_client.get(f"/api/guilds/{GUILD}/markets/12345")).status_code == 404


async def test_auto_bets_page(web_client):
    page = await get(web_client, "/auto-bets")
    assert page["enabled"] and page["error"] is None and page["round"] == 1
    (bet,) = page["bets"]
    assert bet["params"] == {"who": ["Andy", "Aidan"]} and bet["choices"] == ["yes", "no"] and bet["admins"] == ["5"]
    assert [a["value"] for a in bet["bot_bet"]] == [100, 200] and bet["used_this_round"] is False


async def test_auto_bets_page_shows_a_broken_file(web_client, web_services):
    guild = web_services.config.guilds[GUILD]
    assert guild.auto_bets is not None
    guild.auto_bets.bets_file.write_text("[{]")
    page = await get(web_client, "/auto-bets")
    assert page["error"] and page["bets"] == []


async def test_vip_page(web_client, web_services):
    vip = web_services.vip
    await fund(web_services, 1, 20)
    await vip.buy_day_pass(GUILD, 1, DayPassConfig(role_id=40))
    await vip.claim_one_percent(GUILD, 1, 1000, OnePercentConfig(role_id=41))
    page = await get(web_client, "/vip")
    assert page["one_percent"]["holder_user_id"] == "1" and page["one_percent"]["paid"]["value"] == 1000
    assert page["next_price"]["value"] == 1100
    assert [p["user_id"] for p in page["passes"]] == ["1"] and page["history"]["total"] == 1


async def test_snipe_and_heist_pages(web_client, web_services):
    await web_services.snipes.start(GUILD, 50, 500, 1, 2)
    await web_services.snipes.vote(500, 101, needed=2)
    page = await get(web_client, "/snipes")
    assert page["majority"] == 2 and [c["user_id"] for c in page["council"]] == ["101", "102", "103"]
    assert page["items"][0]["status"] == "pending" and page["items"][0]["votes_for"] == ["101"]

    config = web_services.config.guilds[GUILD].heists
    assert config is not None
    await web_services.heists.begin(GUILD, 1, 2, "badger", config)
    heists = await get(web_client, "/heists", active="true")
    assert heists["total"] == 1 and heists["items"][0]["status"] == "active" and heists["items"][0]["crew"][0]["user_id"] == "1"


async def test_manifesto_page(web_client, web_services):
    await web_services.manifesto.add(GUILD, MANIFESTO_AUTHOR, "Beans are a vegetable.")
    (manifesto,) = await get(web_client, "/manifestos")
    assert manifesto["author_ids"] == [str(MANIFESTO_AUTHOR)]
    assert [(line["index"], line["from_file"]) for line in manifesto["lines"]] == [(1, True), (2, True), (3, False)]
    assert manifesto["lines"][2]["added_by"] == str(MANIFESTO_AUTHOR) and manifesto["lines"][2]["added_at"]


async def test_small_pages(web_client):
    posts = await get(web_client, "/daily-posts")
    assert posts["birthdays"]["total"] == 0 and posts["action_holidays"]["items"] == []
    assert await get(web_client, "/fat-cow") == {"visits": [], "members": []}
    assert (await get(web_client, "/role-polls"))["total"] == 0
    assert (await get(web_client, "/classifier/guesses"))["total"] == 0


async def test_config_is_shown_with_secrets_masked(web_client, web_services):
    web_services.config.gemini.api_key = web_services.config.gemini.api_key or __import__("pydantic").SecretStr("hunter2")
    jail = await get(web_client, "/config/jail")
    assert jail["jailed_role_id"] == 20 and jail["jail_length"] == "PT1H"
    manifesto = await get(web_client, "/config/manifesto")
    assert manifesto["authors"] == {str(MANIFESTO_AUTHOR): "andy"}
    assert await get(web_client, "/config/birthday") is None
    assert (await web_client.get(f"/api/guilds/{GUILD}/config/nonsense")).status_code == 404
    snipes_guild = (await web_client.get(f"/api/guilds/{BIG_GUILD}/config/name")).json()
    assert snipes_guild == "Big"

    everything = (await web_client.get("/api/config/global")).json()
    assert "guilds" not in everything and "hunter2" not in str(everything)
    assert everything["bot_token"] == "**********" and everything["web"]["actor_user_id"] == 42
