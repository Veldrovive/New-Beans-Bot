import datetime

from sqlmodel import JSON, Column, Field, Index, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class AutoBetPost(SQLModel, table=True):
    """
    An automatic bet the bot posted. Bets are used up one round at a time: a bet posted in
    the current round isn't picked again until every bet has had its turn.
    """

    __tablename__ = "auto_bet_post"
    __table_args__ = (Index("ix_auto_bet_post_guild_round", "guild_id", "round"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    bet: str  # The template, as written in the bets file. Identifies the bet.
    text: str  # The template with the values filled in
    values: dict[str, str] = Field(sa_column=Column(JSON, nullable=False))
    round: int
    thread_id: int = Field(foreign_key="prediction_market.thread_id")
    posted_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
