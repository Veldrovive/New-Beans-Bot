"""All tables. Importing this module registers every model on SQLModel.metadata."""

from beans_bot.db.models.classifier import ClassifierProcessedMessage, ClassifierScriptLastUsed
from beans_bot.db.models.coins import CoinAccount, CoinTransaction, CoinTransactionKind
from beans_bot.db.models.daily_posts import ActionHolidayPost, BirthdayMessage, UsedHoliday, UsedOnThisDay
from beans_bot.db.models.fat_cow import FatCowConsumption, FatCowVisit
from beans_bot.db.models.jail import JailSentence, JailUsedMessage, PendingRename, TomatoThrow
from beans_bot.db.models.markets import MarketBet, MarketStatus, PredictionMarket
from beans_bot.db.models.role_name import RoleNamePoll
from beans_bot.db.models.vip import OnePercentTitle, VipDayPass

__all__ = [
    "ActionHolidayPost",
    "BirthdayMessage",
    "ClassifierProcessedMessage",
    "ClassifierScriptLastUsed",
    "CoinAccount",
    "CoinTransaction",
    "CoinTransactionKind",
    "FatCowConsumption",
    "FatCowVisit",
    "JailSentence",
    "JailUsedMessage",
    "MarketBet",
    "MarketStatus",
    "OnePercentTitle",
    "PendingRename",
    "PredictionMarket",
    "RoleNamePoll",
    "TomatoThrow",
    "UsedHoliday",
    "UsedOnThisDay",
    "VipDayPass",
]
