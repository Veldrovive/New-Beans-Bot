"""Services shared by every cog. The bot builds one Services and hands it to each cog."""

from __future__ import annotations

from dataclasses import dataclass

from beans_bot.config.schema import BotConfig
from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.services.coins import CoinService
from beans_bot.services.council import CouncilService
from beans_bot.services.gemini import GeminiService
from beans_bot.services.jail import JailService
from beans_bot.services.markets import MarketService
from beans_bot.services.snipes import SnipeService
from beans_bot.services.vip import VipService


@dataclass
class Services:
    config: BotConfig
    db: Database
    currency: CurrencySystem
    coins: CoinService
    jail: JailService
    markets: MarketService
    vip: VipService
    council: CouncilService
    snipes: SnipeService
    gemini: GeminiService

    @classmethod
    def build(cls, config: BotConfig) -> Services:
        db = Database(config.db_path)
        currency = CurrencySystem(cheddar_enabled=config.currency.cheddar_enabled)
        coins = CoinService(db, currency)
        jail = JailService(db, coins)
        return cls(
            config=config,
            db=db,
            currency=currency,
            coins=coins,
            jail=jail,
            markets=MarketService(db, coins, jail),
            vip=VipService(db, coins),
            council=CouncilService(config),
            snipes=SnipeService(db),
            gemini=GeminiService(config.gemini),
        )
