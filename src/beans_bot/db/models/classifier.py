import datetime

from sqlmodel import Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class ClassifierProcessedMessage(SQLModel, table=True):
    """A message a classifier already replied to."""

    __tablename__ = "classifier_processed_message"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    message_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    classifier_name: str = Field(primary_key=True)
    channel_id: int
    processed_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class ClassifierScriptLastUsed(SQLModel, table=True):
    """When a reply script was last used, so the least recently used one can be picked."""

    __tablename__ = "classifier_script_last_used"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    classifier_name: str = Field(primary_key=True)
    script_text: str = Field(primary_key=True)
    last_used_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
