import datetime

from sqlmodel import Field, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class RoleNamePoll(SQLModel, table=True):
    """A poll to rename the student role to "<adjective> Robotics Student"."""

    __tablename__ = "role_name_poll"

    poll_message_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    guild_id: int
    channel_id: int
    initiator_user_id: int
    adjective: str
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    active: bool = Field(default=True, index=True)
