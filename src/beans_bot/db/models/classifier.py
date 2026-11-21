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
    predicted_label: str | None = None  # The top guess. Missing on rows from before corrections existed.
    reply_message_id: int | None = Field(default=None, index=True)  # The bot's reply. Missing on older rows.
    processed_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class ClassifierScriptLastUsed(SQLModel, table=True):
    """When a reply script was last used, so the least recently used one can be picked."""

    __tablename__ = "classifier_script_last_used"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    classifier_name: str = Field(primary_key=True)
    script_text: str = Field(primary_key=True)
    last_used_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class ClassifierCorrection(SQLModel, table=True):
    """
    A member saying what a classified photo really shows. Rows are only ever added, so a post that gets
    corrected twice has two rows and the newest one counts. A row whose label matches previous_label
    is a member confirming the bot's guess.
    """

    __tablename__ = "classifier_correction"

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int = Field(index=True)
    message_id: int = Field(index=True)  # The photo, not the bot's reply
    classifier_name: str
    previous_label: str | None  # What the post was labeled before: the bot's guess, or the last correction
    label: str
    corrected_by: int
    corrected_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
