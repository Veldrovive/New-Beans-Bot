import pytest

from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.services.auto_bets import AutoBetService
from beans_bot.services.classifier import ClassifierService
from beans_bot.services.coins import CoinService
from beans_bot.services.heists import HeistService
from beans_bot.services.jail import JailService
from beans_bot.services.markets import MarketService
from beans_bot.services.reports import ReportService
from beans_bot.services.snipes import SnipeService
from beans_bot.services.vip import VipService

GUILD = 1
HOUSE = 999


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    await database.close()


@pytest.fixture
def coins(db):
    service = CoinService(db, CurrencySystem(cheddar_enabled=False))
    service.house_user_id = HOUSE
    return service


@pytest.fixture
def jail(db, coins):
    return JailService(db, coins)


@pytest.fixture
def markets(db, coins, jail):
    return MarketService(db, coins, jail)


@pytest.fixture
def vip(db, coins):
    return VipService(db, coins)


@pytest.fixture
def snipes(db):
    return SnipeService(db)


@pytest.fixture
def auto_bets(db, coins):
    return AutoBetService(db, coins.currency)


@pytest.fixture
def heists(db, coins, vip):
    return HeistService(db, coins, vip)


@pytest.fixture
def reports(db, coins):
    return ReportService(db, coins)


@pytest.fixture
def classifier(db, tmp_path):
    return ClassifierService(db, tmp_path / "datasets", val_fraction=0.2)


# --- Website ------------------------------------------------------------------------------

BIG_GUILD = 2**60 + 7  # Doesn't fit in a JavaScript number


MANIFESTO_AUTHOR = 2**59 + 3


@pytest.fixture
def web_config(tmp_path):
    """A guild with most features on, plus an inactive one whose ID doesn't fit in a JavaScript number."""
    import datetime
    import json

    from beans_bot.config.schema import (
        AutoBetsConfig,
        BotConfig,
        ClassifierCorrectionsConfig,
        ClassifierGlobalConfig,
        ClassifierGuildConfig,
        ClassifierModelConfig,
        CoinsConfig,
        CouncilConfig,
        CouncilMemberConfig,
        DayPassConfig,
        GamblingConfig,
        GuildConfig,
        HeistConfig,
        JailConfig,
        ManifestoConfig,
        OnePercentConfig,
        SnipesConfig,
        VipConfig,
        WebConfig,
    )

    (tmp_path / "manifestos" / "andy").mkdir(parents=True)
    (tmp_path / "manifestos" / "andy" / "wisdom.json").write_text(json.dumps(["Go piss girl.", "Plastic tastes like plastic."]))
    bets_file = tmp_path / "bets.json"
    bets_file.write_text(json.dumps([{"bet": "{who} leaves early", "params": {"who": ["Andy", "Aidan"]}, "bot_bet": [1, 2], "admins": [5]}]))
    council = CouncilConfig(
        nay_vote_emoji="honse",
        members={
            name: CouncilMemberConfig(internal_name=name.title(), ted_id=i, council_name=f"Ted {i}", user_id=100 + i, crest_emoji=f":{name}:")
            for i, name in enumerate(["aidan", "andy", "katie"], start=1)
        },
    )
    return BotConfig(
        bot_token="x",
        db_path=tmp_path / "web.db",
        manifestos_dir=tmp_path / "manifestos",
        classifier=ClassifierGlobalConfig(datasets_dir=tmp_path / "datasets"),
        web=WebConfig(static_dir=tmp_path / "no-frontend", actor_user_id=42),
        guilds={
            GUILD: GuildConfig(
                name="Test",
                bot_channel_id=10,
                coins=CoinsConfig(emoji="coin", max_reactions_per_day=5),
                jail=JailConfig(
                    jail_emoji="jail", tomato_emoji="tomato", to_jail_threshold=3, mega_jail_threshold=6,
                    jail_length=datetime.timedelta(hours=1), jailed_role_id=20,
                ),  # fmt: skip
                gambling=GamblingConfig(forum_channel_id=30),
                auto_bets=AutoBetsConfig(bets_file=bets_file),
                vip=VipConfig(day_pass=DayPassConfig(role_id=40), one_percent=OnePercentConfig(role_id=41)),
                council_of_teds=council,
                snipes=SnipesConfig(channel_id=50),
                heists=HeistConfig(),
                manifesto=ManifestoConfig(authors={MANIFESTO_AUTHOR: "andy"}),
                classifier=ClassifierGuildConfig(
                    models={
                        "cats": ClassifierModelConfig(
                            model_path="cats", confidence_thresholds={"HIGH": 0.5}, scripts=[], corrections=ClassifierCorrectionsConfig()
                        )
                    }
                ),
            ),
            BIG_GUILD: GuildConfig(name="Big", active=False),
        },
    )


@pytest.fixture
async def web_services(web_config):
    from beans_bot.services import Services

    services = Services.build(web_config)
    services.coins.house_user_id = HOUSE
    yield services
    await services.db.close()


@pytest.fixture
async def web_client(web_services):
    """The website's API with no Discord connection, as an httpx client that sends the admin header."""
    import httpx

    from beans_bot.web.app import create_app
    from beans_bot.web.context import WebContext

    app = create_app(WebContext(config=web_services.config, services=web_services))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"X-Beans-Admin": "1"}) as client:
        yield client
