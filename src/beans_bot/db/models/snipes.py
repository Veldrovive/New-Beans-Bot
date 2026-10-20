import datetime

from sqlmodel import Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class Snipe(SQLModel, table=True):
    """
    A photo of a member posted in the snipe channel. Pending until exactly one of verified_at,
    rejected_at or expired_at is set.
    """

    __tablename__ = "snipe"

    message_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    guild_id: int = Field(index=True)
    channel_id: int
    sniper_user_id: int
    target_user_id: int
    # The message pinging the council, deleted once the snipe is decided.
    alert_message_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    verified_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime, index=True)
    rejected_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)  # A council majority voted nay
    expired_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)  # The council never decided

    @property
    def is_pending(self) -> bool:
        return self.verified_at is None and self.rejected_at is None and self.expired_at is None


class SnipeVote(SQLModel, table=True):
    """
    A council member's crest (or nay emoji, when nay is set) on a snipe. Taking it back sets
    removed_at; adding it again clears it. A member can hold both at once, like the reactions do.
    """

    __tablename__ = "snipe_vote"

    snipe_message_id: int = Field(foreign_key="snipe.message_id", primary_key=True, sa_column_kwargs={"autoincrement": False})
    council_user_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    nay: bool = Field(default=False, primary_key=True)
    voted_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    removed_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
