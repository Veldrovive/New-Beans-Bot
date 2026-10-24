"""
Snipes: photos of members posted in the snipe channel, judged by the Council of Teds.

- A snipe starts pending once we know who's in the photo.
- Council members vote for it with their crest, or against it with the council's nay emoji. Taking the
  reaction back takes the vote back. A member may hold both, and both count.
- The first side to reach the council majority decides the snipe, exactly once: verified or rejected.
  Later votes change nothing. When a recount finds both sides at a majority, verified wins.
- A snipe the council hasn't decided within the vote timeout expires.
"""

from __future__ import annotations

import datetime
import logging

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.db.engine import Database
from beans_bot.db.models import Snipe, SnipeVote
from beans_bot.db.types import utcnow

logger = logging.getLogger(__name__)


class AlreadySniped(Exception):
    """This photo is already a snipe."""


class SnipeService:
    def __init__(self, db: Database):
        self.db = db

    # --- Building blocks (run inside a caller's transaction) --------------------------

    async def _voter_ids(self, session: AsyncSession, message_id: int, nay: bool) -> list[int]:
        result = await session.exec(
            select(SnipeVote.council_user_id)
            .where(SnipeVote.snipe_message_id == message_id, SnipeVote.nay == nay, col(SnipeVote.removed_at).is_(None))
            .order_by(col(SnipeVote.voted_at))
        )
        return list(result.all())

    async def _decide_if_ready(self, session: AsyncSession, snipe: Snipe, needed: int) -> bool:
        """Verify or reject a pending snipe if either side has a majority. True if this call decided it."""
        if not snipe.is_pending:
            return False
        if len(await self._voter_ids(session, snipe.message_id, nay=False)) >= needed:
            snipe.verified_at = utcnow()
            outcome = "verified"
        elif len(await self._voter_ids(session, snipe.message_id, nay=True)) >= needed:
            snipe.rejected_at = utcnow()
            outcome = "rejected"
        else:
            return False
        session.add(snipe)
        logger.info(f"Snipe {snipe.message_id} of {snipe.target_user_id} by {snipe.sniper_user_id} {outcome} in guild {snipe.guild_id}")
        return True

    async def _set_vote(self, session: AsyncSession, message_id: int, council_user_id: int, nay: bool) -> None:
        vote = await session.get(SnipeVote, (message_id, council_user_id, nay))
        if vote is None:
            session.add(SnipeVote(snipe_message_id=message_id, council_user_id=council_user_id, nay=nay))
        elif vote.removed_at is not None:
            vote.removed_at = None
            vote.voted_at = utcnow()
            session.add(vote)

    # --- Operations -------------------------------------------------------------------

    async def start(self, guild_id: int, channel_id: int, message_id: int, sniper_user_id: int, target_user_id: int) -> Snipe:
        """Start a pending snipe. Raises AlreadySniped if the photo is already one."""
        async with self.db.transaction() as session:
            if await session.get(Snipe, message_id) is not None:
                raise AlreadySniped()
            snipe = Snipe(
                message_id=message_id,
                guild_id=guild_id,
                channel_id=channel_id,
                sniper_user_id=sniper_user_id,
                target_user_id=target_user_id,
            )
            session.add(snipe)
        logger.info(f"Snipe {message_id} of {target_user_id} by {sniper_user_id} started in guild {guild_id}")
        return snipe

    async def get(self, message_id: int) -> Snipe | None:
        async with self.db.transaction() as session:
            return await session.get(Snipe, message_id)

    async def set_alert_message(self, message_id: int, alert_message_id: int) -> None:
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is not None:
                snipe.alert_message_id = alert_message_id
                session.add(snipe)

    async def pending(self) -> list[Snipe]:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(Snipe).where(col(Snipe.verified_at).is_(None), col(Snipe.rejected_at).is_(None), col(Snipe.expired_at).is_(None))
            )
            return list(result.all())

    async def voter_ids(self, message_id: int, nay: bool = False) -> list[int]:
        """Council members with a live vote on the snipe (for it, or against it with nay), earliest first."""
        async with self.db.transaction() as session:
            return await self._voter_ids(session, message_id, nay)

    async def vote(self, message_id: int, council_user_id: int, needed: int, nay: bool = False) -> bool:
        """Record a council vote. True if this vote decided the snipe. Not a snipe: does nothing."""
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is None:
                return False
            await self._set_vote(session, message_id, council_user_id, nay)
            await session.flush()
            return await self._decide_if_ready(session, snipe, needed)

    async def unvote(self, message_id: int, council_user_id: int, nay: bool = False) -> None:
        """Take back a vote. Decided snipes stay decided, so their votes stay too."""
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            vote = await session.get(SnipeVote, (message_id, council_user_id, nay))
            if snipe is None or not snipe.is_pending or vote is None or vote.removed_at is not None:
                return
            vote.removed_at = utcnow()
            session.add(vote)

    async def sync_votes(self, message_id: int, yea_user_ids: set[int], nay_user_ids: set[int], needed: int) -> bool:
        """
        Make a pending snipe's votes match the reactions actually on the photo (for reactions missed
        while the bot was down). True if this decided the snipe.
        """
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is None or not snipe.is_pending:
                return False
            result = await session.exec(select(SnipeVote).where(SnipeVote.snipe_message_id == message_id))
            votes = {(v.council_user_id, v.nay): v for v in result.all()}
            on_photo = {(u, False) for u in yea_user_ids} | {(u, True) for u in nay_user_ids}
            for key, vote in votes.items():
                if vote.removed_at is None and key not in on_photo:
                    vote.removed_at = utcnow()
                    session.add(vote)
            for user_id, nay in on_photo:
                await self._set_vote(session, message_id, user_id, nay)
            await session.flush()
            return await self._decide_if_ready(session, snipe, needed)

    async def expire_if_overdue(self, message_id: int, vote_timeout: datetime.timedelta) -> bool:
        """Expire a snipe the council has left pending for too long. True if this expired it."""
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is None or not snipe.is_pending or utcnow() < snipe.created_at + vote_timeout:
                return False
            snipe.expired_at = utcnow()
            session.add(snipe)
        logger.info(f"Snipe {message_id} of {snipe.target_user_id} by {snipe.sniper_user_id} expired in guild {snipe.guild_id}")
        return True
