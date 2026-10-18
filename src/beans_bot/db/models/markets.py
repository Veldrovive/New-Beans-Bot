import datetime
from enum import StrEnum

from sqlmodel import JSON, Column, Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class MarketStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"  # No more bets, waiting for a winner
    RESOLVED = "resolved"
    CANCELED = "canceled"


class PredictionMarket(SQLModel, table=True):
    """A betting pool attached to a thread in the gambling forum channel."""

    __tablename__ = "prediction_market"

    thread_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    guild_id: int = Field(index=True)
    creator_id: int
    status: str = MarketStatus.OPEN
    choices: list[str] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    resolved_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    winning_choice: str | None = None


class MarketBet(SQLModel, table=True):
    """One member's bet on a market. Members get one choice per market."""

    __tablename__ = "market_bet"

    market_thread_id: int = Field(
        foreign_key="prediction_market.thread_id", primary_key=True, sa_column_kwargs={"autoincrement": False}
    )
    user_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    choice: str
    amount: int  # Base units
    placed_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
