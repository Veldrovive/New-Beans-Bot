"""
VIP: day passes and the one permanent "1%" title, both bought with BeanCoins.

- The 1% can appoint one henchman for free. The henchman is fired when the
  title changes hands, in the same transaction as the claim.

Each purchase pays the house and updates the records in one transaction, so two
members can't both claim the title at the same price. The VIP cog keeps the
Discord roles in line with these records.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.config.schema import DayPassConfig, OnePercentConfig
from beans_bot.db.engine import Database
from beans_bot.db.models import CoinTransactionKind, OnePercentTitle, VipDayPass
from beans_bot.db.types import utcnow
from beans_bot.services.coins import CoinService

logger = logging.getLogger(__name__)


class AlreadyOnePercent(Exception):
    """The member already holds the title."""


class BidTooLow(Exception):
    def __init__(self, price: int):
        super().__init__(f"The price is {price}")
        self.price = price


class NotOnePercent(Exception):
    """Only the 1% can hire and fire henchmen."""


class InvalidHenchman(Exception):
    """The 1% tried to hire themselves."""


class AlreadyHenchman(Exception):
    """That member is already the henchman."""


@dataclass
class OnePercentClaim:
    previous_holder_id: int | None
    paid: int
    new_price: int
    previous_henchman_id: int | None = None  # Fired by the claim


class VipService:
    def __init__(self, db: Database, coins: CoinService):
        self.db = db
        self.coins = coins

    # --- Day passes ---------------------------------------------------------------------

    async def _active_pass(self, session: AsyncSession, guild_id: int, user_id: int) -> VipDayPass | None:
        result = await session.exec(
            select(VipDayPass).where(
                VipDayPass.guild_id == guild_id, VipDayPass.user_id == user_id, col(VipDayPass.ended_at).is_(None)
            )
        )
        return result.first()

    async def active_pass(self, guild_id: int, user_id: int) -> VipDayPass | None:
        async with self.db.transaction() as session:
            return await self._active_pass(session, guild_id, user_id)

    async def active_passes(self, guild_id: int) -> list[VipDayPass]:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(VipDayPass).where(VipDayPass.guild_id == guild_id, col(VipDayPass.ended_at).is_(None))
            )
            return list(result.all())

    async def buy_day_pass(self, guild_id: int, user_id: int, config: DayPassConfig) -> VipDayPass:
        """Charge for a day pass, or extend the current one. Raises InsufficientFunds."""
        async with self.db.transaction() as session:
            await self.coins.move(
                session,
                guild_id=guild_id,
                amount=config.cost,
                kind=CoinTransactionKind.VIP_DAY_PASS,
                from_user_id=user_id,
                to_user_id=self.coins.house_user_id,
            )
            day_pass = await self._active_pass(session, guild_id, user_id)
            if day_pass is None:
                now = utcnow()
                day_pass = VipDayPass(guild_id=guild_id, user_id=user_id, started_at=now, ends_at=now + config.length)
                session.add(day_pass)
            else:
                day_pass.ends_at = max(day_pass.ends_at, utcnow()) + config.length
        logger.info(f"{user_id} bought a VIP day pass in guild {guild_id}, until {day_pass.ends_at}")
        return day_pass

    async def due_to_expire(self, now: datetime.datetime | None = None) -> list[VipDayPass]:
        now = now or utcnow()
        async with self.db.transaction() as session:
            result = await session.exec(
                select(VipDayPass).where(col(VipDayPass.ended_at).is_(None), VipDayPass.ends_at <= now)
            )
            return list(result.all())

    async def end_pass(self, pass_id: int) -> bool:
        """Mark a pass ended. False if it already was, or was extended past now in the meantime."""
        async with self.db.transaction() as session:
            day_pass = await session.get(VipDayPass, pass_id)
            if day_pass is None or day_pass.ended_at is not None or day_pass.ends_at > utcnow():
                return False
            day_pass.ended_at = utcnow()
            return True

    # --- The 1% -------------------------------------------------------------------------

    async def one_percent(self, guild_id: int) -> OnePercentTitle | None:
        async with self.db.transaction() as session:
            return await session.get(OnePercentTitle, guild_id)

    async def one_percent_price(self, guild_id: int, config: OnePercentConfig) -> int:
        title = await self.one_percent(guild_id)
        return title.price if title else config.starting_price

    async def claim_one_percent(self, guild_id: int, user_id: int, amount: int, config: OnePercentConfig) -> OnePercentClaim:
        """
        Pay `amount` to take the title. The next price becomes `amount` plus the increment.
        Raises BidTooLow, AlreadyOnePercent or InsufficientFunds.
        """
        async with self.db.transaction() as session:
            title = await session.get(OnePercentTitle, guild_id)
            if title is not None and title.holder_user_id == user_id:
                raise AlreadyOnePercent()
            price = title.price if title else config.starting_price
            if amount < price:
                raise BidTooLow(price)
            await self.coins.move(
                session,
                guild_id=guild_id,
                amount=amount,
                kind=CoinTransactionKind.VIP_ONE_PERCENT,
                from_user_id=user_id,
                to_user_id=self.coins.house_user_id,
            )
            previous_holder_id = title.holder_user_id if title else None
            previous_henchman_id = title.henchman_user_id if title else None
            new_price = amount + config.price_increment
            if title is None:
                title = OnePercentTitle(guild_id=guild_id, holder_user_id=user_id, paid=amount, price=new_price)
                session.add(title)
            else:
                title.holder_user_id = user_id
                title.paid = amount
                title.price = new_price
                title.claimed_at = utcnow()
                title.henchman_user_id = None  # The old boss's henchman doesn't come with the penthouse
        logger.info(f"{user_id} claimed the 1% in guild {guild_id} for {amount} (was {previous_holder_id})")
        return OnePercentClaim(
            previous_holder_id=previous_holder_id, paid=amount, new_price=new_price, previous_henchman_id=previous_henchman_id
        )

    async def _revoke_one_percent(self, session: AsyncSession, guild_id: int, user_id: int) -> OnePercentTitle | None:
        """
        Take the title from `user_id` if they hold it, and return the old record. With no record the
        price goes back to the starting price, and the henchman goes with it.
        """
        title = await session.get(OnePercentTitle, guild_id)
        if title is None or title.holder_user_id != user_id:
            return None
        await session.delete(title)
        logger.info(f"{user_id} lost the 1% in guild {guild_id} (henchman {title.henchman_user_id} fired)")
        return title

    # --- Henchmen -----------------------------------------------------------------------

    async def appoint_henchman(self, guild_id: int, boss_id: int, henchman_id: int) -> int | None:
        """
        Make `henchman_id` the 1%'s henchman, replacing any current one, whose ID is returned.
        Raises NotOnePercent, InvalidHenchman or AlreadyHenchman.
        """
        async with self.db.transaction() as session:
            title = await session.get(OnePercentTitle, guild_id)
            if title is None or title.holder_user_id != boss_id:
                raise NotOnePercent()
            if henchman_id == boss_id:
                raise InvalidHenchman()
            if title.henchman_user_id == henchman_id:
                raise AlreadyHenchman()
            previous_henchman_id = title.henchman_user_id
            title.henchman_user_id = henchman_id
        logger.info(f"{boss_id} appointed {henchman_id} as henchman in guild {guild_id} (was {previous_henchman_id})")
        return previous_henchman_id

    async def dismiss_henchman(self, guild_id: int, boss_id: int) -> int | None:
        """Fire the 1%'s henchman and return their ID, or None if there wasn't one. Raises NotOnePercent."""
        async with self.db.transaction() as session:
            title = await session.get(OnePercentTitle, guild_id)
            if title is None or title.holder_user_id != boss_id:
                raise NotOnePercent()
            previous_henchman_id = title.henchman_user_id
            title.henchman_user_id = None
        logger.info(f"{boss_id} dismissed henchman {previous_henchman_id} in guild {guild_id}")
        return previous_henchman_id
