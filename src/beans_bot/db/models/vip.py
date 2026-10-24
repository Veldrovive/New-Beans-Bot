import datetime

from sqlmodel import Field, Index, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class VipDayPass(SQLModel, table=True):
    """A bought day in the VIP channel. A pass is active while ended_at is None; buying again extends it."""

    __tablename__ = "vip_day_pass"
    __table_args__ = (Index("ix_vip_day_pass_guild_user", "guild_id", "user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int
    user_id: int
    started_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    ends_at: datetime.datetime = Field(sa_type=UTCDateTime)
    ended_at: datetime.datetime | None = Field(default=None, sa_type=UTCDateTime, index=True)


class OnePercentTitle(SQLModel, table=True):
    """The guild's one permanent VIP holder and their henchman. No row means nobody has claimed it yet."""

    __tablename__ = "vip_one_percent"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    holder_user_id: int
    paid: int  # What the holder paid, in base units
    price: int  # The least the next claimant must pay
    claimed_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    henchman_user_id: int | None = None  # Appointed by the holder, cleared when the title changes hands
