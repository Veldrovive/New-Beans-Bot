"""
Jail: sentences, the jail role, mega jail renames and tomato records.

Any cog that needs to know whether someone is jailed, or to jail or release
someone, goes through JailService. It is the only code that touches the jail role.

Members can also frame each other: pay the house to start someone else's sentence.
The payment and the sentence happen in one transaction, so nobody pays for a
sentence that never starts (or that someone else already started).
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from enum import Enum

import discord
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.config.schema import JailConfig
from beans_bot.db.engine import Database
from beans_bot.db.models import CoinTransactionKind, JailSentence, JailUsedMessage, PendingRename, TomatoThrow
from beans_bot.db.types import utcnow
from beans_bot.services.coins import CoinService

logger = logging.getLogger(__name__)


class JailOutcome(Enum):
    JAILED = "jailed"
    MESSAGE_ALREADY_USED = "message_already_used"
    ALREADY_JAILED = "already_jailed"
    MEMBER_NOT_FOUND = "member_not_found"
    ROLE_NOT_FOUND = "role_not_found"
    MISSING_PERMISSIONS = "missing_permissions"


class AlreadyJailed(Exception):
    """The member is already serving a sentence."""


@dataclass
class JailResult:
    outcome: JailOutcome
    member: discord.Member | None = None


async def get_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            return None
    return member


class JailService:
    def __init__(self, db: Database, coins: CoinService):
        self.db = db
        self.coins = coins

    # --- Building blocks (run inside a caller's transaction) --------------------------

    async def _current_sentence(self, session: AsyncSession, guild_id: int, user_id: int) -> JailSentence | None:
        result = await session.exec(
            select(JailSentence).where(
                JailSentence.guild_id == guild_id,
                JailSentence.user_id == user_id,
                col(JailSentence.released_at).is_(None),
            )
        )
        return result.first()

    async def _end_sentence(self, session: AsyncSession, guild_id: int, user_id: int, reason: str) -> bool:
        result = await session.exec(
            update(JailSentence)
            .where(
                col(JailSentence.guild_id) == guild_id,
                col(JailSentence.user_id) == user_id,
                col(JailSentence.released_at).is_(None),
            )
            .values(released_at=utcnow(), release_reason=reason)
        )
        return result.rowcount > 0

    # --- Records ------------------------------------------------------------------------

    async def current_sentence(self, guild_id: int, user_id: int) -> JailSentence | None:
        async with self.db.transaction() as session:
            return await self._current_sentence(session, guild_id, user_id)

    async def is_jailed(self, guild_id: int, user_id: int) -> bool:
        return await self.current_sentence(guild_id, user_id) is not None

    async def current_sentences(self, guild_id: int | None = None) -> list[JailSentence]:
        async with self.db.transaction() as session:
            query = select(JailSentence).where(col(JailSentence.released_at).is_(None))
            if guild_id is not None:
                query = query.where(JailSentence.guild_id == guild_id)
            return list((await session.exec(query.order_by(col(JailSentence.started_at)))).all())

    async def due_for_release(self, now: datetime.datetime | None = None) -> list[JailSentence]:
        now = now or utcnow()
        async with self.db.transaction() as session:
            result = await session.exec(
                select(JailSentence).where(col(JailSentence.released_at).is_(None), JailSentence.ends_at <= now)
            )
            return list(result.all())

    async def claim_message(self, guild_id: int, message_id: int) -> bool:
        """Mark a message as having jailed someone. False if it already had."""
        try:
            async with self.db.transaction() as session:
                session.add(JailUsedMessage(guild_id=guild_id, message_id=message_id))
        except IntegrityError:
            return False
        return True

    async def unclaim_message(self, guild_id: int, message_id: int) -> None:
        async with self.db.transaction() as session:
            used = await session.get(JailUsedMessage, (guild_id, message_id))
            if used is not None:
                await session.delete(used)

    async def start_sentence(
        self, *, guild_id: int, user_id: int, channel_id: int, offending_message_id: int | None, length: datetime.timedelta
    ) -> JailSentence | None:
        """Record a new sentence, or None if the member is already serving one."""
        async with self.db.transaction() as session:
            if await self._current_sentence(session, guild_id, user_id) is not None:
                return None
            now = utcnow()
            sentence = JailSentence(
                guild_id=guild_id,
                user_id=user_id,
                channel_id=channel_id,
                offending_message_id=offending_message_id,
                started_at=now,
                ends_at=now + length,
            )
            session.add(sentence)
            return sentence

    async def end_sentence(self, guild_id: int, user_id: int, reason: str) -> bool:
        """Mark the current sentence released. False if the member wasn't jailed."""
        async with self.db.transaction() as session:
            return await self._end_sentence(session, guild_id, user_id, reason)

    async def mark_humiliated(self, sentence_id: int) -> bool:
        """Mark a sentence humiliated. True only for the call that changed it, so it happens once."""
        async with self.db.transaction() as session:
            result = await session.exec(
                update(JailSentence)
                .where(col(JailSentence.id) == sentence_id, col(JailSentence.humiliated) == False)  # noqa: E712
                .values(humiliated=True)
            )
            return result.rowcount > 0

    async def times_jailed(self, guild_id: int, user_id: int) -> int:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(func.count())
                .select_from(JailSentence)
                .where(JailSentence.guild_id == guild_id, JailSentence.user_id == user_id)
            )
            return result.one()

    async def leaderboard(self, guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
        """(user_id, times jailed), most jailed first."""
        async with self.db.transaction() as session:
            count = func.count().label("times")
            result = await session.exec(
                select(JailSentence.user_id, count)
                .where(JailSentence.guild_id == guild_id)
                .group_by(col(JailSentence.user_id))
                .order_by(count.desc())
                .limit(limit)
            )
            return [(user_id, times) for user_id, times in result.all()]

    # --- Framing ------------------------------------------------------------------------

    async def frame(
        self, *, guild_id: int, framer_id: int, target_id: int, cost: int, channel_id: int, length: datetime.timedelta
    ) -> JailSentence:
        """
        Charge the framer and start the target's sentence. The caller hands out the jail role,
        and calls refund_frame if it can't. Raises AlreadyJailed or InsufficientFunds.
        """
        async with self.db.transaction() as session:
            if await self._current_sentence(session, guild_id, target_id) is not None:
                raise AlreadyJailed()
            await self.coins.move(
                session,
                guild_id=guild_id,
                amount=cost,
                kind=CoinTransactionKind.FRAME,
                from_user_id=framer_id,
                to_user_id=self.coins.house_user_id,
            )
            now = utcnow()
            sentence = JailSentence(
                guild_id=guild_id,
                user_id=target_id,
                channel_id=channel_id,
                offending_message_id=None,
                started_at=now,
                ends_at=now + length,
            )
            session.add(sentence)
        logger.info(f"{framer_id} paid {cost} to frame {target_id} in guild {guild_id}")
        return sentence

    async def refund_frame(self, *, guild_id: int, framer_id: int, target_id: int, cost: int) -> None:
        """Undo a frame whose jail role never went on: end the sentence and pay the framer back."""
        async with self.db.transaction() as session:
            await self._end_sentence(session, guild_id, target_id, "frame_refunded")
            await self.coins.move(
                session,
                guild_id=guild_id,
                amount=cost,
                kind=CoinTransactionKind.FRAME_REFUND,
                from_user_id=self.coins.house_user_id,
                to_user_id=framer_id,
            )
        logger.info(f"Refunded {framer_id} {cost} for framing {target_id} in guild {guild_id}")

    # --- Discord side -------------------------------------------------------------------

    async def jail(
        self, guild: discord.Guild, config: JailConfig, user_id: int, offending_message: discord.Message
    ) -> JailResult:
        """
        Jail the author of a message: give them the jail role and start a sentence.
        Each message can only ever jail once.
        """
        if not await self.claim_message(guild.id, offending_message.id):
            return JailResult(JailOutcome.MESSAGE_ALREADY_USED)

        result = await self.imprison(
            guild,
            config,
            user_id,
            channel_id=offending_message.channel.id,
            offending_message_id=offending_message.id,
            audit_reason="Sent to the stocks by popular vote.",
        )
        if result.outcome in (JailOutcome.MEMBER_NOT_FOUND, JailOutcome.ROLE_NOT_FOUND, JailOutcome.MISSING_PERMISSIONS):
            # Nobody went to jail, so the message can still jail them later.
            await self.unclaim_message(guild.id, offending_message.id)
        return result

    async def imprison(
        self,
        guild: discord.Guild,
        config: JailConfig,
        user_id: int,
        *,
        channel_id: int,
        offending_message_id: int | None,
        audit_reason: str,
    ) -> JailResult:
        """Give a member the jail role and start a sentence, for any reason (a voted message, a swapped bet)."""
        member = await get_member(guild, user_id)
        if member is None:
            logger.error(f"Could not find member {user_id} to jail in guild {guild.id}")
            return JailResult(JailOutcome.MEMBER_NOT_FOUND)

        if await self.is_jailed(guild.id, user_id):
            return JailResult(JailOutcome.ALREADY_JAILED, member)

        role = guild.get_role(config.jailed_role_id)
        if role is None:
            logger.error(f"Jail role {config.jailed_role_id} not found in guild {guild.id}")
            return JailResult(JailOutcome.ROLE_NOT_FOUND, member)
        try:
            await member.add_roles(role, reason=audit_reason)
        except discord.Forbidden:
            return JailResult(JailOutcome.MISSING_PERMISSIONS, member)

        await self.start_sentence(
            guild_id=guild.id,
            user_id=user_id,
            channel_id=channel_id,
            offending_message_id=offending_message_id,
            length=config.jail_length,
        )
        logger.info(f"Jailed {user_id} in guild {guild.id} ({audit_reason})")
        return JailResult(JailOutcome.JAILED, member)

    async def remove_role(self, guild: discord.Guild, config: JailConfig, member: discord.Member, reason: str) -> bool:
        role = guild.get_role(config.jailed_role_id)
        if role is None:
            return False
        try:
            await member.remove_roles(role, reason=reason)
        except discord.Forbidden:
            logger.warning(f"Could not remove jail role from {member.id} in guild {guild.id}")
            return False
        return True

    async def release(
        self, guild: discord.Guild, config: JailConfig, user_id: int, *, reason: str, audit_reason: str
    ) -> discord.Member | None:
        """End a member's sentence and take away the jail role. Returns the member if they're still in the guild."""
        await self.end_sentence(guild.id, user_id, reason)
        member = await get_member(guild, user_id)
        if member is not None:
            await self.remove_role(guild, config, member, audit_reason)
        logger.info(f"Released {user_id} in guild {guild.id} ({reason})")
        return member

    # --- Mega jail renames --------------------------------------------------------------

    async def create_pending_rename(
        self, *, guild_id: int, prompt_message_id: int, target_user_id: int, offending_message_id: int
    ) -> bool:
        try:
            async with self.db.transaction() as session:
                session.add(
                    PendingRename(
                        guild_id=guild_id,
                        prompt_message_id=prompt_message_id,
                        target_user_id=target_user_id,
                        offending_message_id=offending_message_id,
                    )
                )
        except IntegrityError:
            return False
        return True

    async def has_pending_rename(self, guild_id: int, offending_message_id: int) -> bool:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(PendingRename).where(
                    PendingRename.guild_id == guild_id, PendingRename.offending_message_id == offending_message_id
                )
            )
            return result.first() is not None

    async def claim_pending_rename(self, guild_id: int, prompt_message_id: int) -> PendingRename | None:
        """Take the unused rename for a prompt message. Only the first caller gets it."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(PendingRename).where(
                    PendingRename.guild_id == guild_id,
                    PendingRename.prompt_message_id == prompt_message_id,
                    PendingRename.used == False,  # noqa: E712
                )
            )
            rename = result.first()
            if rename is not None:
                rename.used = True
            return rename

    # --- Tomatoes -----------------------------------------------------------------------

    async def record_tomato(self, *, guild_id: int, thrower_user_id: int, target_user_id: int, message_id: int, innocent: bool) -> None:
        async with self.db.transaction() as session:
            session.add(
                TomatoThrow(
                    guild_id=guild_id,
                    thrower_user_id=thrower_user_id,
                    target_user_id=target_user_id,
                    message_id=message_id,
                    thrown_at=utcnow(),
                    innocent=innocent,
                )
            )

    async def tomatoes_received(self, guild_id: int, user_id: int) -> int:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(func.count())
                .select_from(TomatoThrow)
                .where(TomatoThrow.guild_id == guild_id, TomatoThrow.target_user_id == user_id)
            )
            return result.one()
