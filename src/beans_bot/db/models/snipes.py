import datetime

from sqlmodel import Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class Snipe(SQLModel, table=True):
    """A photo of a member posted in the snipe channel. Pending while verified_at is None."""

    __tablename__ = "snipe"

    message_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    guild_id: int = Field(index=True)
    channel_id: int
    sniper_user_id: int
    target_user_id: int
    # The message pinging the council, deleted once the snipe is verified.
    alert_message_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    verified_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime, index=True)


class SnipeVote(SQLModel, table=True):
    """A council member's crest on a snipe. Taking the crest back sets removed_at; adding it again clears it."""

    __tablename__ = "snipe_vote"

    snipe_message_id: int = Field(foreign_key="snipe.message_id", primary_key=True, sa_column_kwargs={"autoincrement": False})
    council_user_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    voted_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    removed_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
