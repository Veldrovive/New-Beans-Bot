import datetime
from enum import StrEnum

from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint

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


class ClassifierSplit(StrEnum):
    TRAIN = "train"
    VAL = "val"  # Held out to score each retrain


class ClassifierImageSource(StrEnum):
    SEED = "seed"  # The original dataset from Aidan's Mac, brought in by scripts/import_classifier_dataset.py
    CORRECTION = "correction"  # A member confirmed or corrected the bot's guess in Discord
    UPLOAD = "upload"  # Added on the admin website
    DROP = "drop"  # A member handed the bot photos by DM


class ClassifierImage(SQLModel, table=True):
    """One photo in a classifier's dataset. The file is <datasets_dir>/<dataset>/images/<filename>."""

    __tablename__ = "classifier_image"
    __table_args__ = (UniqueConstraint("dataset", "content_hash"),)

    id: int | None = Field(default=None, primary_key=True)
    dataset: str = Field(index=True)  # The model's model_path, like "cats"
    content_hash: str  # md5 of the bytes as they arrived, hex. Also the embedding cache key.
    filename: str  # "<content_hash>.jpg": the shrunk copy that is kept
    original_name: str | None = None
    label: str = Field(index=True)
    split: str  # A ClassifierSplit
    source: str  # A ClassifierImageSource
    guild_id: int | None = None  # Set for corrections
    message_id: int | None = Field(default=None, index=True)
    added_by: int | None = None  # The member who confirmed, corrected or dropped it
    added_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    removed_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)  # Soft delete: not trained on, can be restored


class ClassifierTrainingRun(SQLModel, table=True):
    """One retrain. The newest succeeded run of a dataset describes the model the bot is using."""

    __tablename__ = "classifier_training_run"

    id: int | None = Field(default=None, primary_key=True)
    dataset: str = Field(index=True)
    status: str  # "running", "succeeded" or "failed"
    triggered_by: str  # "web:<email>" or "discord:<user id>"
    started_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    finished_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    error: str | None = None
    train_count: int | None = None
    val_count: int | None = None
    accuracy: float | None = None
    log_loss: float | None = None
    # labels, per_label, confusion, predictions, skipped, unseen_val_labels. See training.RunResults.
    results: dict | None = Field(default=None, sa_column=Column(JSON))


class ClassifierDropSession(SQLModel, table=True):
    """
    A member handing the bot photos of one cat by DM, from /photos drop until /photos done or they go quiet.
    Ending sets ended_at. Rows are never deleted.
    """

    __tablename__ = "classifier_drop_session"

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int  # Where /photos drop was run
    user_id: int = Field(index=True)
    classifier_name: str
    dataset: str  # The model's model_path
    label: str
    idle_timeout: int  # Seconds without photos before it ends, from the model's config when it started
    started_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    last_photo_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)  # Starts as started_at
    ended_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)  # None while open
    kept: int = 0
    duplicates: int = 0  # Already in the dataset
    failed: int = 0  # Couldn't be read

    def expires_at(self) -> datetime.datetime:
        return self.last_photo_at + datetime.timedelta(seconds=self.idle_timeout)

    def is_open(self, now: datetime.datetime) -> bool:
        """Not ended, and not past its idle timeout. An expired session the timer hasn't closed yet counts as closed."""
        return self.ended_at is None and now < self.expires_at()
