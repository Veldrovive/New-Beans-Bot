import datetime

from sqlmodel import Field, Index, SQLModel, UniqueConstraint

from beans_bot.db.types import UTCDateTime, utcnow


class JailSentence(SQLModel, table=True):
    """One stay in jail. A sentence is current while released_at is None."""

    __tablename__ = "jail_sentence"
    __table_args__ = (Index("ix_jail_sentence_guild_user", "guild_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    user_id: int
    channel_id: int
    offending_message_id: int | None = None  # None when re-jailed by the desync check, framed, or jailed for a bet swap
    started_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    ends_at: datetime.datetime = Field(sa_type=UTCDateTime)
    released_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime, index=True)
    release_reason: str | None = None  # "served", "bribe", "frame_refunded", "migrated"
    humiliated: bool = False


class JailUsedMessage(SQLModel, table=True):
    """A message that has already put someone in jail. It can't do so again."""

    __tablename__ = "jail_used_message"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    message_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})


class PendingRename(SQLModel, table=True):
    """A mega jail prompt. The first reply to the prompt becomes the target's nickname."""

    __tablename__ = "pending_rename"
    __table_args__ = (UniqueConstraint("guild_id", "offending_message_id"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    prompt_message_id: int = Field(index=True)
    target_user_id: int
    offending_message_id: int
    used: bool = False


class TomatoThrow(SQLModel, table=True):
    __tablename__ = "tomato_throw"
    __table_args__ = (Index("ix_tomato_throw_guild_target", "guild_id", "target_user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    # thrower/message/thrown_at are None only for throws carried over from the old bot's
    # counter without matching history.
    thrower_user_id: int | None = None
    target_user_id: int
    message_id: int | None = None
    thrown_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    innocent: bool  # The target was not in jail
