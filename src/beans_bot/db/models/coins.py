import datetime
from enum import StrEnum

from sqlmodel import Field, Index, SQLModel

from beans_bot.db.types import UTCDateTime, utcnow


class CoinAccount(SQLModel, table=True):
    """
    A member's balances in one guild, in base units (see beans_bot.currency). ShadowBean Coins are
    a hidden second pocket: they can't be spent, only laundered into the regular balance.
    """

    __tablename__ = "coin_account"

    guild_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    user_id: int = Field(primary_key=True, sa_column_kwargs={"autoincrement": False})
    balance: int = 0
    shadow_balance: int = Field(default=0, sa_column_kwargs={"server_default": "0"})


class CoinTransactionKind(StrEnum):
    REACTION = "reaction"  # Minted to a message author when someone reacts with the coin emoji
    BRIBE = "bribe"  # Jailed member -> house
    FRAME = "frame"  # Member -> house, to jail someone else
    FRAME_REFUND = "frame_refund"  # House -> member, when the bot couldn't hand out the jail role after all
    BET_PLACE = "bet_place"  # Bettor -> market escrow
    BET_REFUND = "bet_refund"  # Market escrow -> bettor
    BET_PAYOUT = "bet_payout"  # Market escrow -> winner
    HOUSE_REMAINDER = "house_remainder"  # Market escrow -> house (rounding leftovers)
    BET_NULLIFY = "bet_nullify"  # Winner -> market escrow, when an admin nullifies a resolved market (may leave them in debt)
    VIP_DAY_PASS = "vip_day_pass"  # Member -> house
    VIP_ONE_PERCENT = "vip_one_percent"  # Member -> house, to claim the one permanent VIP title
    HEIST_LOOT = "heist_loot"  # Target -> crew member (old heists, before loot went into the shadow pocket)
    HEIST_SHADOW_LOOT = "heist_shadow_loot"  # Target -> crew member's shadow pocket, when a crew member's snipe of them is verified
    HEIST_FINE = "heist_fine"  # Crew member (or the snitch) -> house, when a heist is snitched on
    HEIST_SNITCH_REWARD = "heist_snitch_reward"  # House -> snitch (old heists, before rewards went into the shadow pocket)
    SNITCH_SHADOW_REWARD = "snitch_shadow_reward"  # House -> snitch's shadow pocket
    SHADOW_LAUNDER = "shadow_launder"  # Member's shadow pocket -> their own balance
    TRANSFER = "transfer"  # Member -> member
    ADMIN_GIVE = "admin_give"  # House -> member (either pocket), by a server admin
    ADMIN_TAKE = "admin_take"  # Member (either pocket) -> house, by a server admin
    MIGRATION_OPENING = "migration_opening"  # Balance carried over from the old bot


class CoinTransaction(SQLModel, table=True):
    """
    Ledger of every balance change. `from_user_id` is debited and `to_user_id` is
    credited; None on either side means outside any account (minted coins, or a
    market's escrow). `from_shadow` and `to_shadow` say which pocket each side touches. For every
    account and pocket, credits minus debits equals that pocket's balance.
    """

    __tablename__ = "coin_transaction"
    __table_args__ = (
        Index("ix_coin_transaction_actor_kind_time", "guild_id", "actor_user_id", "kind", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    guild_id: int = Field(index=True)
    kind: str
    amount: int
    from_user_id: int | None = Field(default=None, index=True)
    to_user_id: int | None = Field(default=None, index=True)
    from_shadow: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    to_shadow: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    # Who caused this, when it is not the debited account (e.g. the reacting member for REACTION).
    actor_user_id: int | None = None
    message_id: int | None = None
    thread_id: int | None = None
    created_at: datetime.datetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
