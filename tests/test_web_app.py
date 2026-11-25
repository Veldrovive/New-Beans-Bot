"""The website's skeleton: status pages, the cross-site check, unknown guilds and IDs as strings."""

import httpx
from pydantic import BaseModel

from beans_bot.web.app import create_app
from beans_bot.web.context import WebContext
from beans_bot.web.schemas import Snowflake

from conftest import BIG_GUILD, GUILD, HOUSE


async def test_meta_without_discord(web_client):
    response = await web_client.get("/api/meta")
    assert response.status_code == 200
    meta = response.json()
    assert meta["bot"] is None and meta["connected"] is False
    assert meta["house_user_id"] == str(HOUSE)
    assert meta["classifier_loaded"] is False and meta["cogs"] == []
    assert [c["name"] for c in meta["currencies"]] == ["BeanCoin"]


async def test_guilds_list_features(web_client):
    response = await web_client.get("/api/guilds")
    assert response.status_code == 200
    by_id = {g["id"]: g for g in response.json()}
    test = by_id[str(GUILD)]
    assert (test["name"], test["active"], test["connected"], test["bot_channel_id"]) == ("Test", True, False, "10")
    assert test["features"]["coins"] is True and test["features"]["birthday"] is False
    assert "name" not in test["features"] and "bot_channel_id" not in test["features"]
    assert by_id[str(BIG_GUILD)]["active"] is False


async def test_ids_round_trip_as_strings(web_client):
    # 2**60 + 7 would round to 2**60 in JavaScript.
    guilds = (await web_client.get("/api/guilds")).json()
    assert str(BIG_GUILD) in [g["id"] for g in guilds]
    response = await web_client.get(f"/api/guilds/{BIG_GUILD}/directory")
    assert response.status_code == 200
    assert response.json() == {"members": {}, "roles": {}, "channels": {}}

    class Thing(BaseModel):
        id: Snowflake

    assert Thing.model_validate_json(f'{{"id": "{BIG_GUILD}"}}').id == BIG_GUILD
    assert Thing(id=BIG_GUILD).model_dump_json() == f'{{"id":"{BIG_GUILD}"}}'
    assert Thing(id=BIG_GUILD).model_dump() == {"id": BIG_GUILD}


async def test_unknown_guild_is_404(web_client):
    response = await web_client.get("/api/guilds/12345/directory")
    assert response.status_code == 404
    assert "12345" in response.json()["detail"]


async def test_post_without_the_admin_header_is_403(web_services):
    app = create_app(WebContext(config=web_services.config, services=web_services))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/guilds/1/coins/give", json={})
        assert response.status_code == 403
        assert (await client.get("/api/meta")).status_code == 200


async def test_frontend_is_served_with_a_fallback_to_index(web_services, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>beans</html>")
    (dist / "assets" / "app-123.js").write_text("console.log('beans')")
    (tmp_path / "secret.txt").write_text("nope")
    web_services.config.web.static_dir = dist
    app = create_app(WebContext(config=web_services.config, services=web_services))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).text == "<html>beans</html>"
        assert (await client.get(f"/g/{GUILD}/coins")).text == "<html>beans</html>"
        asset = await client.get("/assets/app-123.js")
        assert asset.text == "console.log('beans')" and "immutable" in asset.headers["cache-control"]
        assert "nope" not in (await client.get("/../secret.txt")).text
        assert (await client.get("/api/nothing-here")).status_code == 404


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_web_cog_serves_and_stops(tmp_path):
    import asyncio

    from beans_bot.bot import BeansBot
    from beans_bot.cogs.web import WebCog
    from beans_bot.config.schema import BotConfig, WebConfig

    port = _free_port()
    bot = BeansBot(BotConfig(bot_token="x", db_path=tmp_path / "beans.db", web=WebConfig(enabled=True, port=port, static_dir=tmp_path / "none")))
    try:
        await bot.add_cog(WebCog(bot))
        async with httpx.AsyncClient() as client:
            response = None
            for _ in range(50):
                try:
                    response = await client.get(f"http://127.0.0.1:{port}/api/meta")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            assert response is not None and response.status_code == 200 and response.json()["connected"] is False

        # A second server on the same port fails to bind, and only logs it.
        other = BeansBot(BotConfig(bot_token="x", db_path=tmp_path / "other.db", web=WebConfig(enabled=True, port=port, static_dir=tmp_path / "none")))
        await other.add_cog(WebCog(other))
        cog = other.get_cog("WebCog")
        assert isinstance(cog, WebCog) and cog.server is not None
        await asyncio.wait_for(cog.server, timeout=5)
        await other.remove_cog("WebCog")
        await other.services.db.close()

        await bot.remove_cog("WebCog")
        async with httpx.AsyncClient() as client:
            try:
                await client.get(f"http://127.0.0.1:{port}/api/meta")
                raise AssertionError("still serving")
            except httpx.ConnectError:
                pass
    finally:
        await bot.services.db.close()
