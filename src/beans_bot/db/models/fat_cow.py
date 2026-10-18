import datetime

from sqlmodel import Field, Index, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class FatCowVisit(SQLModel, table=True):
    """A trip to Palace Tang."""

    __tablename__ = "fat_cow_visit"

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int = Field(index=True)
    visited_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class FatCowConsumption(SQLModel, table=True):
    """How many meters of fat cow a member ate on a visit (0 if they just went)."""

    __tablename__ = "fat_cow_consumption"
    __table_args__ = (Index("ix_fat_cow_consumption_guild_user", "guild_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    user_id: int
    meters: float
    recorded_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
