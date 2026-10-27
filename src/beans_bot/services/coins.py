"""
BeanCoin balances and the ledger. Every balance change in the bot goes through here.

- Each account has two pockets: the regular balance everyone can see, and ShadowBean Coins.
- ShadowBeans come from heist loot and snitch rewards. They can't be spent or transferred, and
  only their owner sees them. Laundering moves them into the regular balance, a bit at a time
  if you'd rather nobody noticed.
- Members can transfer regular coins to each other. Admins can give or take either kind,
  with the house on the other side so coins never appear from nowhere or vanish.
"""

from __future__ import annotations

import datetime
import logging
from enum import Enum

from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.db.models import CoinAccount, CoinTransaction, CoinTransactionKind

logger = logging.getLogger(__name__)


class InsufficientFunds(Exception):
    def __init__(self, balance: int, needed: int):
        super().__init__(f"Balance {balance} is less than {needed}")
        self.balance = balance
        self.needed = needed


class NothingToLaunder(Exception):
    """The member's shadow pocket is empty."""


class CantPayYourself(Exception):
    """A member tried to transfer coins to themselves."""


class ReactionGrantResult(Enum):
    GRANTED = "granted"
    DUPLICATE = "duplicate"  # This member already gave a coin to this message
    DAILY_LIMIT = "daily_limit"


class CoinService:
    def __init__(self, db: Database, currency: CurrencySystem):
        self.db = db
        self.currency = currency
        # The bot's own account. It acts as the house: it takes bribes and market
        # leftovers, places bot bets, and is the only account allowed to go negative.
        self.house_user_id: int | None = None

    # --- Building blocks (run inside a caller's transaction) --------------------------

    async def _account(self, session: AsyncSession, guild_id: int, user_id: int) -> CoinAccount:
        account = await session.get(CoinAccount, (guild_id, user_id))
        if account is None:
            account = CoinAccount(guild_id=guild_id, user_id=user_id, balance=0)
            session.add(account)
        return account

    async def move(
        self,
        session: AsyncSession,
        *,
        guild_id: int,
        amount: int,
        kind: CoinTransactionKind,
        from_user_id: int | None = None,
        to_user_id: int | None = None,
        actor_user_id: int | None = None,
        message_id: int | None = None,
        thread_id: int | None = None,
        from_shadow: bool = False,
        to_shadow: bool = False,
    ) -> CoinTransaction:
        """
        Debit `from_user_id` and credit `to_user_id` (either may be None) and record it. `from_shadow`
        and `to_shadow` use that side's shadow pocket instead of its balance. Raises InsufficientFunds
        if the debited pocket can't cover it, unless it is the house's balance.
        """
        if amount <= 0:
            raise ValueError(f"Coin moves must be positive, got {amount}")
        if self.house_user_id is not None and (
            (from_shadow and from_user_id == self.house_user_id) or (to_shadow and to_user_id == self.house_user_id)
        ):
            raise ValueError("The house doesn't keep ShadowBeans")
        if from_user_id is not None:
            source = await self._account(session, guild_id, from_user_id)
            if from_shadow:
                if source.shadow_balance < amount:
                    raise InsufficientFunds(source.shadow_balance, amount)
                source.shadow_balance -= amount
            else:
                if from_user_id != self.house_user_id and source.balance < amount:
                    raise InsufficientFunds(source.balance, amount)
                source.balance -= amount
        if to_user_id is not None:
            destination = await self._account(session, guild_id, to_user_id)
            if to_shadow:
                destination.shadow_balance += amount
            else:
                destination.balance += amount
        transaction = CoinTransaction(
            guild_id=guild_id,
            kind=kind,
            amount=amount,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            from_shadow=from_shadow,
            to_shadow=to_shadow,
            actor_user_id=actor_user_id,
            message_id=message_id,
            thread_id=thread_id,
        )
        session.add(transaction)
        await session.flush()
        return transaction

    async def balance_in(self, session: AsyncSession, guild_id: int, user_id: int) -> int:
        account = await session.get(CoinAccount, (guild_id, user_id))
        return account.balance if account else 0

    async def shadow_balance_in(self, session: AsyncSession, guild_id: int, user_id: int) -> int:
        account = await session.get(CoinAccount, (guild_id, user_id))
        return account.shadow_balance if account else 0

    # --- Operations -------------------------------------------------------------------

    async def balance(self, guild_id: int, user_id: int) -> int:
        async with self.db.transaction() as session:
            return await self.balance_in(session, guild_id, user_id)

    async def shadow_balance(self, guild_id: int, user_id: int) -> int:
        async with self.db.transaction() as session:
            return await self.shadow_balance_in(session, guild_id, user_id)

    async def launder(self, guild_id: int, user_id: int, amount: int | None) -> tuple[int, int]:
        """
        Move ShadowBeans into the member's regular balance. None launders all of them.
        Returns (laundered, shadow left). Raises NothingToLaunder or InsufficientFunds.
        """
        async with self.db.transaction() as session:
            shadow = await self.shadow_balance_in(session, guild_id, user_id)
            if shadow <= 0:
                raise NothingToLaunder()
            amount = shadow if amount is None else amount
            await self.move(
                session,
                guild_id=guild_id,
                amount=amount,
                kind=CoinTransactionKind.SHADOW_LAUNDER,
                from_user_id=user_id,
                to_user_id=user_id,
                from_shadow=True,
            )
        logger.info(f"{user_id} laundered {amount} ShadowBeans in guild {guild_id}")
        return amount, shadow - amount

    async def transfer(self, guild_id: int, sender_id: int, recipient_id: int, amount: int) -> None:
        """Move regular coins from one member to another. Raises CantPayYourself or InsufficientFunds."""
        if sender_id == recipient_id:
            raise CantPayYourself()
        async with self.db.transaction() as session:
            await self.move(
                session, guild_id=guild_id, amount=amount, kind=CoinTransactionKind.TRANSFER, from_user_id=sender_id, to_user_id=recipient_id
            )
        logger.info(f"{sender_id} transferred {amount} to {recipient_id} in guild {guild_id}")

    async def admin_give(self, guild_id: int, admin_id: int, user_id: int, amount: int, *, shadow: bool) -> int:
        """The house gives a member coins, into either pocket. Returns that pocket's new balance."""
        async with self.db.transaction() as session:
            await self.move(
                session,
                guild_id=guild_id,
                amount=amount,
                kind=CoinTransactionKind.ADMIN_GIVE,
                from_user_id=self.house_user_id,
                to_user_id=user_id,
                actor_user_id=admin_id,
                to_shadow=shadow,
            )
            balance = await (self.shadow_balance_in if shadow else self.balance_in)(session, guild_id, user_id)
        logger.info(f"Admin {admin_id} gave {user_id} {amount} ({'shadow' if shadow else 'regular'}) in guild {guild_id}")
        return balance

    async def admin_take(self, guild_id: int, admin_id: int, user_id: int, amount: int, *, shadow: bool) -> int:
        """Take coins from either of a member's pockets for the house. Returns that pocket's new balance. Raises InsufficientFunds."""
        async with self.db.transaction() as session:
            await self.move(
                session,
                guild_id=guild_id,
                amount=amount,
                kind=CoinTransactionKind.ADMIN_TAKE,
                from_user_id=user_id,
                to_user_id=self.house_user_id,
                actor_user_id=admin_id,
                from_shadow=shadow,
            )
            balance = await (self.shadow_balance_in if shadow else self.balance_in)(session, guild_id, user_id)
        logger.info(f"Admin {admin_id} took {amount} ({'shadow' if shadow else 'regular'}) from {user_id} in guild {guild_id}")
        return balance

    async def grant_for_reaction(
        self,
        *,
        guild_id: int,
        giver_id: int,
        receiver_id: int,
        message_id: int,
        amount: int,
        max_per_day: int,
        day_start: datetime.datetime,
    ) -> ReactionGrantResult:
        """Mint coins for the author of a message someone reacted to, within the giver's daily limit."""
        async with self.db.transaction() as session:
            reaction_gifts = select(CoinTransaction).where(
                CoinTransaction.guild_id == guild_id,
                CoinTransaction.kind == CoinTransactionKind.REACTION,
                CoinTransaction.actor_user_id == giver_id,
            )
            duplicate = await session.exec(reaction_gifts.where(CoinTransaction.message_id == message_id).limit(1))
            if duplicate.first() is not None:
                return ReactionGrantResult.DUPLICATE

            given_today = await session.exec(
                select(func.count())
                .select_from(CoinTransaction)
                .where(
                    CoinTransaction.guild_id == guild_id,
                    CoinTransaction.kind == CoinTransactionKind.REACTION,
                    CoinTransaction.actor_user_id == giver_id,
                    col(CoinTransaction.created_at) >= day_start,
                )
            )
            if given_today.one() >= max_per_day:
                return ReactionGrantResult.DAILY_LIMIT

            await self.move(
                session,
                guild_id=guild_id,
                amount=amount,
                kind=CoinTransactionKind.REACTION,
                to_user_id=receiver_id,
                actor_user_id=giver_id,
                message_id=message_id,
            )
        logger.info(f"Granted {amount} to {receiver_id} from {giver_id}'s reaction in guild {guild_id}")
        return ReactionGrantResult.GRANTED

    async def pay_house(self, *, guild_id: int, user_id: int, amount: int, kind: CoinTransactionKind) -> None:
        """Move coins from a member to the house. Raises InsufficientFunds."""
        async with self.db.transaction() as session:
            await self.move(
                session, guild_id=guild_id, amount=amount, kind=kind, from_user_id=user_id, to_user_id=self.house_user_id
            )

    async def reaction_totals(self, guild_id: int, user_id: int) -> tuple[int, int]:
        """(received, gifted) amounts from coin reactions."""
        async with self.db.transaction() as session:
            base = (
                select(func.coalesce(func.sum(CoinTransaction.amount), 0))
                .select_from(CoinTransaction)
                .where(CoinTransaction.guild_id == guild_id, CoinTransaction.kind == CoinTransactionKind.REACTION)
            )
            received = (await session.exec(base.where(CoinTransaction.to_user_id == user_id))).one()
            gifted = (await session.exec(base.where(CoinTransaction.actor_user_id == user_id))).one()
        return received, gifted

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[CoinAccount]:
        """Accounts with a positive balance, richest first."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(CoinAccount)
                .where(CoinAccount.guild_id == guild_id, CoinAccount.balance > 0)
                .order_by(col(CoinAccount.balance).desc())
                .limit(limit)
            )
            return list(result.all())
