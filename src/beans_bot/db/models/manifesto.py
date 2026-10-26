import datetime

from sqlmodel import Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class ManifestoWisdom(SQLModel, table=True):
    """
    A piece of wisdom a member added to their manifesto with /manifesto add. It comes after the
    wisdom.json lines. Editing it sets edited_at; removing it sets removed_at.
    """

    __tablename__ = "manifesto_wisdom"

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int = Field(index=True)
    author_user_id: int
    text: str
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    edited_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    removed_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
