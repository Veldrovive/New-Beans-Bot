"""
Snipes: photos of members posted in the snipe channel, verified by the Council of Teds.

- A snipe starts unverified once we know who's in the photo.
- Council members vote by reacting with their crest. Taking the crest back takes the vote back.
- Once votes reach the council majority the snipe is verified, exactly once. Later votes change nothing.
- Fake snipes are the jail cog's problem: jail reactions on the photo jail the sniper as usual.
"""

from __future__ import annotations

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

    async def _voter_ids(self, session: AsyncSession, message_id: int) -> list[int]:
        result = await session.exec(
            select(SnipeVote.council_user_id)
            .where(SnipeVote.snipe_message_id == message_id, col(SnipeVote.removed_at).is_(None))
            .order_by(col(SnipeVote.voted_at))
        )
        return list(result.all())

    async def _verify_if_ready(self, session: AsyncSession, snipe: Snipe, needed: int) -> bool:
        """Mark a pending snipe verified if it has enough votes. True if this call verified it."""
        if snipe.verified_at is not None or len(await self._voter_ids(session, snipe.message_id)) < needed:
            return False
        snipe.verified_at = utcnow()
        session.add(snipe)
        logger.info(f"Snipe {snipe.message_id} of {snipe.target_user_id} by {snipe.sniper_user_id} verified in guild {snipe.guild_id}")
        return True

    # --- Operations -------------------------------------------------------------------

    async def start(self, guild_id: int, channel_id: int, message_id: int, sniper_user_id: int, target_user_id: int) -> Snipe:
        """Start an unverified snipe. Raises AlreadySniped if the photo is already one."""
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
            result = await session.exec(select(Snipe).where(col(Snipe.verified_at).is_(None)))
            return list(result.all())

    async def voter_ids(self, message_id: int) -> list[int]:
        """Council members with a live vote on the snipe, earliest first."""
        async with self.db.transaction() as session:
            return await self._voter_ids(session, message_id)

    async def vote(self, message_id: int, council_user_id: int, needed: int) -> bool:
        """Record a council vote. True if this vote verified the snipe. Not a snipe: does nothing."""
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is None:
                return False
            vote = await session.get(SnipeVote, (message_id, council_user_id))
            if vote is None:
                session.add(SnipeVote(snipe_message_id=message_id, council_user_id=council_user_id))
            elif vote.removed_at is not None:
                vote.removed_at = None
                vote.voted_at = utcnow()
                session.add(vote)
            await session.flush()
            return await self._verify_if_ready(session, snipe, needed)

    async def unvote(self, message_id: int, council_user_id: int) -> None:
        """Take back a vote. Verified snipes stay verified, so their votes stay too."""
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            vote = await session.get(SnipeVote, (message_id, council_user_id))
            if snipe is None or snipe.verified_at is not None or vote is None or vote.removed_at is not None:
                return
            vote.removed_at = utcnow()
            session.add(vote)

    async def sync_votes(self, message_id: int, council_user_ids: set[int], needed: int) -> bool:
        """
        Make a pending snipe's votes match the crests actually on the photo (for reactions missed
        while the bot was down). True if this verified the snipe.
        """
        async with self.db.transaction() as session:
            snipe = await session.get(Snipe, message_id)
            if snipe is None or snipe.verified_at is not None:
                return False
            result = await session.exec(select(SnipeVote).where(SnipeVote.snipe_message_id == message_id))
            votes = {v.council_user_id: v for v in result.all()}
            for user_id, vote in votes.items():
                live = vote.removed_at is None
                if live and user_id not in council_user_ids:
                    vote.removed_at = utcnow()
                    session.add(vote)
                elif not live and user_id in council_user_ids:
                    vote.removed_at = None
                    vote.voted_at = utcnow()
                    session.add(vote)
            for user_id in council_user_ids - votes.keys():
                session.add(SnipeVote(snipe_message_id=message_id, council_user_id=user_id))
            await session.flush()
            return await self._verify_if_ready(session, snipe, needed)
