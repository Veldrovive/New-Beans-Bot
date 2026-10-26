"""
Heists: a crew robs a member by sniping them.

- The leader starts a heist on a target and gets a codeword to pass around in real life.
  Anyone with the codeword can join. The leader counts toward the crew.
- If a crew member's snipe of the target (posted after the heist began) is verified while the
  crew has at least `min_crew` people, the heist succeeds. The crew splits a cut of the target's
  balance evenly, and a target who held the 1% loses it.
- Anyone with the codeword can snitch instead. Everyone in the crew, and the snitch too so
  nobody can tell who it was, pays a fine to the house and goes to jail. Once the delay has
  passed, the house quietly pays the snitch a share of all the fines.
- A heist that hasn't succeeded within its duration expires, and nothing happens to anyone.

Snipes are judged by the council as usual. Being in a crew doesn't stop a council member from
verifying the snipe.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.config.schema import HeistConfig
from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.db.models import CoinTransactionKind, Heist, HeistMember, Snipe
from beans_bot.db.types import utcnow
from beans_bot.services.coins import CoinService
from beans_bot.services.vip import VipService

logger = logging.getLogger(__name__)


class NoSuchHeist(Exception):
    """No active heist has that codeword."""


class TargetAlreadyHeisted(Exception):
    """Someone is already plotting against the target."""


class AlreadyInHeist(Exception):
    """The member is already in an active heist's crew."""


class CantRobYourself(Exception):
    """The target tried to join their own heist, or the leader tried to rob themselves."""


class CodewordTaken(Exception):
    """An active heist already uses the codeword."""


@dataclass
class HeistSuccess:
    heist: Heist
    sniper_user_id: int
    shares: dict[int, int]  # Crew member -> their cut, in base units
    dethroned: bool  # The target held the 1% and lost it
    previous_henchman_id: int | None = None  # Fired along with the dethroned 1%


@dataclass
class HeistSnitch:
    heist: Heist
    fines: dict[int, int]  # Everyone being jailed (the crew and the snitch) -> what they paid

    @property
    def total(self) -> int:
        return sum(self.fines.values())


def cut_percent(crew_size: int, config: HeistConfig) -> int:
    """The percent of the target's balance a crew this size steals: 0 below the minimum."""
    if crew_size < config.min_crew:
        return 0
    sizes = [size for size in config.cuts if size <= crew_size]
    return config.cuts[max(sizes)] if sizes else 0


def percent_of(balance: int, percent: int, currency: CurrencySystem) -> int:
    """
    `percent` of a balance, rounded down to the smallest coin (a CheddarCoin, when it's on).
    Nothing comes out of an empty or overdrawn balance.
    """
    return currency.floor(balance * percent // 100) if balance > 0 else 0


class HeistService:
    def __init__(self, db: Database, coins: CoinService, vip: VipService):
        self.db = db
        self.coins = coins
        self.vip = vip

    # --- Building blocks (run inside a caller's transaction) --------------------------

    @staticmethod
    def _undecided():
        return (col(Heist.succeeded_at).is_(None), col(Heist.snitched_at).is_(None), col(Heist.expired_at).is_(None))

    async def _active_heists(self, session: AsyncSession, guild_id: int) -> list[Heist]:
        """Heists still open for joining and snitching. Overdue ones are closed even before they're marked expired."""
        result = await session.exec(select(Heist).where(Heist.guild_id == guild_id, Heist.ends_at > utcnow(), *self._undecided()))
        return list(result.all())

    async def _active_by_code(self, session: AsyncSession, guild_id: int, code: str) -> Heist | None:
        code = code.strip().lower()
        return next((h for h in await self._active_heists(session, guild_id) if h.code == code), None)

    async def _crew(self, session: AsyncSession, heist_id: int) -> list[HeistMember]:
        result = await session.exec(select(HeistMember).where(HeistMember.heist_id == heist_id).order_by(col(HeistMember.joined_at)))
        return list(result.all())

    async def _in_active_heist(self, session: AsyncSession, guild_id: int, user_id: int) -> bool:
        for heist in await self._active_heists(session, guild_id):
            assert heist.id is not None
            if await session.get(HeistMember, (heist.id, user_id)) is not None:
                return True
        return False

    async def _qualifying_snipe(self, session: AsyncSession, heist: Heist, crew: list[HeistMember], config: HeistConfig) -> Snipe | None:
        """The first verified snipe of the target, by a crew member, while the crew was big enough."""
        result = await session.exec(
            select(Snipe)
            .where(
                Snipe.guild_id == heist.guild_id,
                Snipe.target_user_id == heist.target_user_id,
                col(Snipe.verified_at).is_not(None),
                col(Snipe.created_at) >= heist.started_at,
                col(Snipe.sniper_user_id).in_([m.user_id for m in crew]),
            )
            .order_by(col(Snipe.verified_at))
        )
        for snipe in result.all():
            assert snipe.verified_at is not None
            if snipe.verified_at > heist.ends_at:
                break
            joined = {m.user_id for m in crew if m.joined_at <= snipe.verified_at}
            if snipe.sniper_user_id in joined and len(joined) >= config.min_crew:
                return snipe
        return None

    # --- Operations -------------------------------------------------------------------

    async def begin(self, guild_id: int, leader_id: int, target_id: int, code: str, config: HeistConfig) -> Heist:
        """Start a heist with the leader as its first crew member. Raises CantRobYourself, TargetAlreadyHeisted, AlreadyInHeist or CodewordTaken."""
        code = code.strip().lower()
        async with self.db.transaction() as session:
            if leader_id == target_id:
                raise CantRobYourself()
            active = await self._active_heists(session, guild_id)
            if any(h.target_user_id == target_id for h in active):
                raise TargetAlreadyHeisted()
            if await self._in_active_heist(session, guild_id, leader_id):
                raise AlreadyInHeist()
            if any(h.code == code for h in active):
                raise CodewordTaken()
            now = utcnow()
            heist = Heist(guild_id=guild_id, code=code, leader_user_id=leader_id, target_user_id=target_id, started_at=now, ends_at=now + config.duration)
            session.add(heist)
            await session.flush()
            assert heist.id is not None
            session.add(HeistMember(heist_id=heist.id, user_id=leader_id, joined_at=now))
        logger.info(f"{leader_id} began heist {heist.id} on {target_id} in guild {guild_id}")
        return heist

    async def join(self, guild_id: int, user_id: int, code: str) -> tuple[Heist, int]:
        """Join the heist with this codeword. Returns it and the new crew size. Raises NoSuchHeist, CantRobYourself or AlreadyInHeist."""
        async with self.db.transaction() as session:
            heist = await self._active_by_code(session, guild_id, code)
            if heist is None:
                raise NoSuchHeist()
            assert heist.id is not None
            if heist.target_user_id == user_id:
                raise CantRobYourself()
            if await self._in_active_heist(session, guild_id, user_id):
                raise AlreadyInHeist()
            session.add(HeistMember(heist_id=heist.id, user_id=user_id))
            await session.flush()
            crew_size = len(await self._crew(session, heist.id))
        logger.info(f"{user_id} joined heist {heist.id} on {heist.target_user_id} in guild {guild_id} (crew of {crew_size})")
        return heist, crew_size

    async def current_heist(self, guild_id: int, user_id: int) -> tuple[Heist, int] | None:
        """The active heist the member is in, and its crew size. None if they aren't in one."""
        async with self.db.transaction() as session:
            for heist in await self._active_heists(session, guild_id):
                assert heist.id is not None
                if await session.get(HeistMember, (heist.id, user_id)) is not None:
                    return heist, len(await self._crew(session, heist.id))
        return None

    async def crew(self, heist_id: int) -> list[int]:
        """The crew's user IDs, leader first."""
        async with self.db.transaction() as session:
            return [m.user_id for m in await self._crew(session, heist_id)]

    async def resolve_snipes(self, guild_id: int, config: HeistConfig) -> list[HeistSuccess]:
        """
        Pay out every undecided heist that a verified snipe has won, in one transaction each: the crew
        split the loot evenly (the leader gets any remainder) and the target loses the 1%.
        """
        async with self.db.transaction() as session:
            result = await session.exec(select(Heist.id).where(Heist.guild_id == guild_id, *self._undecided()))
            heist_ids = list(result.all())
        successes = []
        for heist_id in heist_ids:
            assert heist_id is not None
            if (success := await self._resolve(heist_id, config)) is not None:
                successes.append(success)
        return successes

    async def _resolve(self, heist_id: int, config: HeistConfig) -> HeistSuccess | None:
        async with self.db.transaction() as session:
            heist = await session.get(Heist, heist_id)
            if heist is None or not heist.is_active:
                return None
            crew = await self._crew(session, heist_id)
            snipe = await self._qualifying_snipe(session, heist, crew, config)
            if snipe is None or snipe.verified_at is None:
                return None
            robbers = [m for m in crew if m.joined_at <= snipe.verified_at]
            target_balance = await self.coins.balance_in(session, heist.guild_id, heist.target_user_id)
            loot = percent_of(target_balance, cut_percent(len(robbers), config), self.coins.currency)
            # Split in whole coins. The leader keeps the coins that don't divide evenly.
            coin = self.coins.currency.granularity
            each, remainder = (n * coin for n in divmod(loot // coin, len(robbers)))
            shares = {}
            for member in robbers:
                share = each + (remainder if member.user_id == heist.leader_user_id else 0)
                if share > 0:
                    await self.coins.move(
                        session,
                        guild_id=heist.guild_id,
                        amount=share,
                        kind=CoinTransactionKind.HEIST_LOOT,
                        from_user_id=heist.target_user_id,
                        to_user_id=member.user_id,
                        message_id=snipe.message_id,
                    )
                member.share = share
                session.add(member)
                shares[member.user_id] = share
            heist.succeeded_at = utcnow()
            heist.snipe_message_id = snipe.message_id
            heist.loot = loot
            session.add(heist)
            title = await self.vip._revoke_one_percent(session, heist.guild_id, heist.target_user_id)
        logger.info(
            f"Heist {heist_id} on {heist.target_user_id} succeeded in guild {heist.guild_id}: {loot} split across "
            f"{len(robbers)} by snipe {snipe.message_id}, 1% revoked: {title is not None}"
        )
        return HeistSuccess(
            heist=heist,
            sniper_user_id=snipe.sniper_user_id,
            shares=shares,
            dethroned=title is not None,
            previous_henchman_id=title.henchman_user_id if title else None,
        )

    async def snitch(self, guild_id: int, snitch_id: int, code: str, config: HeistConfig) -> HeistSnitch:
        """
        Turn in the heist with this codeword. The crew and the snitch each pay the house a fine, and the
        snitch's reward is set aside for later. The caller jails everyone in the result. Raises NoSuchHeist.
        """
        async with self.db.transaction() as session:
            heist = await self._active_by_code(session, guild_id, code)
            if heist is None:
                raise NoSuchHeist()
            assert heist.id is not None
            crew = {m.user_id: m for m in await self._crew(session, heist.id)}
            fines = {}
            for user_id in [*crew, *([] if snitch_id in crew else [snitch_id])]:
                fine = percent_of(await self.coins.balance_in(session, guild_id, user_id), config.snitch_fine_percent, self.coins.currency)
                if fine > 0:
                    await self.coins.move(
                        session,
                        guild_id=guild_id,
                        amount=fine,
                        kind=CoinTransactionKind.HEIST_FINE,
                        from_user_id=user_id,
                        to_user_id=self.coins.house_user_id,
                    )
                if user_id in crew:
                    crew[user_id].fine = fine
                    session.add(crew[user_id])
                fines[user_id] = fine
            heist.snitched_at = utcnow()
            heist.snitch_user_id = snitch_id
            heist.snitch_reward = percent_of(sum(fines.values()), config.snitch_reward_percent, self.coins.currency)
            session.add(heist)
        logger.info(
            f"{snitch_id} snitched on heist {heist.id} in guild {guild_id}: fined {sum(fines.values())} from {sorted(fines)}, "
            f"reward {heist.snitch_reward} due later"
        )
        return HeistSnitch(heist=heist, fines=fines)

    async def due_rewards(self, guild_id: int, delay: datetime.timedelta) -> list[Heist]:
        """Snitched heists whose snitch is owed their reward now."""
        async with self.db.transaction() as session:
            result = await session.exec(
                select(Heist).where(
                    Heist.guild_id == guild_id,
                    col(Heist.snitched_at).is_not(None),
                    col(Heist.snitched_at) <= utcnow() - delay,
                    col(Heist.reward_paid_at).is_(None),
                )
            )
            return list(result.all())

    async def pay_reward(self, heist_id: int, delay: datetime.timedelta) -> int | None:
        """Pay the snitch their reward from the house. Returns the amount, or None if it isn't owed (yet, or anymore)."""
        async with self.db.transaction() as session:
            heist = await session.get(Heist, heist_id)
            if heist is None or heist.snitched_at is None or heist.reward_paid_at is not None or heist.snitched_at > utcnow() - delay:
                return None
            assert heist.snitch_user_id is not None
            reward = heist.snitch_reward or 0
            if reward > 0:
                await self.coins.move(
                    session,
                    guild_id=heist.guild_id,
                    amount=reward,
                    kind=CoinTransactionKind.HEIST_SNITCH_REWARD,
                    from_user_id=self.coins.house_user_id,
                    to_user_id=heist.snitch_user_id,
                )
            heist.reward_paid_at = utcnow()
            session.add(heist)
        logger.info(f"Paid {heist.snitch_user_id} {reward} for snitching on heist {heist_id} in guild {heist.guild_id}")
        return reward

    async def expire_overdue(self, guild_id: int) -> list[Heist]:
        """Mark undecided heists past their deadline expired, and return them."""
        async with self.db.transaction() as session:
            result = await session.exec(select(Heist).where(Heist.guild_id == guild_id, Heist.ends_at <= utcnow(), *self._undecided()))
            expired = list(result.all())
            for heist in expired:
                heist.expired_at = utcnow()
                session.add(heist)
        for heist in expired:
            logger.info(f"Heist {heist.id} on {heist.target_user_id} expired in guild {guild_id}")
        return expired
