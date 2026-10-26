import datetime

from sqlmodel import Field, Index, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class Heist(SQLModel, table=True):
    """
    A crew's plot to rob one member. Active until exactly one of succeeded_at, snitched_at or
    expired_at is set. Codewords are only unique among active heists.
    """

    __tablename__ = "heist"
    __table_args__ = (Index("ix_heist_guild_code", "guild_id", "code"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    code: str  # Lowercase
    leader_user_id: int
    target_user_id: int
    started_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    ends_at: datetime.datetime = Field(sa_type=UTCDateTime)
    # A crew member's verified snipe of the target
    succeeded_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    snipe_message_id: int | None = None
    loot: int | None = None  # Base units taken from the target
    # Somebody used the codeword to turn the crew in
    snitched_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    snitch_user_id: int | None = None
    snitch_reward: int | None = None  # Paid by the house once the delay has passed
    reward_paid_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)
    expired_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime)

    @property
    def is_active(self) -> bool:
        return self.succeeded_at is None and self.snitched_at is None and self.expired_at is None


class HeistMember(SQLModel, table=True):
    """One member of a heist's crew. The leader has a row too."""

    __tablename__ = "heist_member"

    heist_id: int = Field(foreign_key="heist.id", primary_key=True, sa_column_kwargs={"autoincrement": False})
    user_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    joined_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    share: int | None = None  # Their cut of the loot, once the heist succeeds
    fine: int | None = None  # What they paid the house, once the heist is snitched on
