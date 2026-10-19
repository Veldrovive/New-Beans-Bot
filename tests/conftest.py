import pytest

from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.services.coins import CoinService
from beans_bot.services.jail import JailService
from beans_bot.services.markets import MarketService
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
def markets(db, coins):
    return MarketService(db, coins)


@pytest.fixture
def jail(db, coins):
    return JailService(db, coins)


@pytest.fixture
def vip(db, coins):
    return VipService(db, coins)
