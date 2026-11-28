"""
Read-only queries for the admin website.

- Every method opens one transaction and only reads. Writes stay in the feature services, so the
  money code isn't touched by the website's needs.
- Lists are paged: methods that return many rows take `limit` and `offset` and return the page
  with the total row count.
- Rows come back as the model rows themselves, or small dataclasses when a page needs more than one table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from sqlalchemy import func, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from beans_bot.db.engine import Database
from beans_bot.db.models import (
    ActionHolidayPost,
    AutoBetPost,
    BirthdayMessage,
    ClassifierCorrection,
    ClassifierDropSession,
    ClassifierImage,
    ClassifierImageSource,
    ClassifierProcessedMessage,
    CoinAccount,
    CoinTransaction,
    FatCowConsumption,
    FatCowVisit,
    Heist,
    HeistMember,
    JailSentence,
    MarketBet,
    OnePercentTitle,
    PendingRename,
    PredictionMarket,
    RoleNamePoll,
    Snipe,
    SnipeVote,
    TomatoThrow,
    VipDayPass,
)
from beans_bot.services.coins import CoinService

Row = TypeVar("Row")


@dataclass
class MemberSummary:
    user_id: int
    balance: int = 0
    shadow_balance: int = 0
    jailed: bool = False
    vip: list[str] = field(default_factory=list)  # "pass", "one_percent", "henchman"
    times_jailed: int = 0
    tomatoes_received: int = 0


@dataclass
class TomatoTotals:
    user_id: int
    thrown: int = 0
    received: int = 0
    at_innocents: int = 0  # Thrown by this member at someone who wasn't in jail


@dataclass
class JailStats:
    most_jailed: list[tuple[int, int]]  # (user_id, times), most first
    tomatoes: list[TomatoTotals]
    pending_renames: list[PendingRename]


@dataclass
class MarketRow:
    market: PredictionMarket
    pool: int  # Every stake, the house's included
    bets: int


@dataclass
class MarketDetail:
    market: PredictionMarket
    bets: list[MarketBet]
    totals: dict[str, int]  # Choice -> staked, the house's included
    ledger: list[CoinTransaction]
    auto_bet: AutoBetPost | None


@dataclass
class AutoBetHistory:
    posts: list[AutoBetPost]
    total: int
    round: int  # The current round; 1 before anything was posted
    used_this_round: list[str]  # Templates already posted this round


@dataclass
class SnipeRow:
    snipe: Snipe
    votes_for: list[int]
    votes_against: list[int]

    @property
    def status(self) -> str:
        return snipe_status(self.snipe)


@dataclass
class HeistRow:
    heist: Heist
    crew: list[HeistMember]


@dataclass
class FatCowMember:
    user_id: int
    visits: int
    total_meters: float
    average_meters: float


@dataclass
class FatCowStats:
    visits: list[FatCowVisit]
    members: list[FatCowMember]


@dataclass
class Guess:
    guess: ClassifierProcessedMessage
    correction: ClassifierCorrection | None  # The newest one


@dataclass
class Drop:
    session: ClassifierDropSession
    photos: list[ClassifierImage]  # What the member handed over in it, oldest first. Removed ones included.


def snipe_status(snipe: Snipe) -> str:
    if snipe.verified_at is not None:
        return "verified"
    if snipe.rejected_at is not None:
        return "rejected"
    if snipe.expired_at is not None:
        return "expired"
    return "pending"


def heist_status(heist: Heist) -> str:
    if heist.succeeded_at is not None:
        return "succeeded"
    if heist.snitched_at is not None:
        return "snitched"
    if heist.expired_at is not None:
        return "expired"
    return "active"


class ReportService:
    def __init__(self, db: Database, coins: CoinService):
        self.db = db
        self.coins = coins

    # --- Building blocks ---------------------------------------------------------------

    @staticmethod
    async def _page(session: AsyncSession, query: SelectOfScalar[Row], limit: int, offset: int) -> tuple[list[Row], int]:
        """One page of a query, and how many rows the whole query has."""
        total = (await session.exec(select(func.count()).select_from(query.order_by(None).subquery()))).one()
        rows = (await session.exec(query.limit(limit).offset(offset))).all()
        return list(rows), total

    # --- Members ------------------------------------------------------------------------

    async def member_summaries(self, guild_id: int) -> list[MemberSummary]:
        """Everyone with any record in the guild. One grouped query per table, merged here."""
        summaries: dict[int, MemberSummary] = {}

        def summary(user_id: int) -> MemberSummary:
            return summaries.setdefault(user_id, MemberSummary(user_id=user_id))

        async with self.db.transaction() as session:
            for account in (await session.exec(select(CoinAccount).where(CoinAccount.guild_id == guild_id))).all():
                row = summary(account.user_id)
                row.balance, row.shadow_balance = account.balance, account.shadow_balance
            times = await session.exec(
                select(JailSentence.user_id, func.count()).where(JailSentence.guild_id == guild_id).group_by(col(JailSentence.user_id))
            )
            for user_id, count in times.all():
                summary(user_id).times_jailed = count
            jailed = await session.exec(
                select(JailSentence.user_id).where(JailSentence.guild_id == guild_id, col(JailSentence.released_at).is_(None))
            )
            for user_id in jailed.all():
                summary(user_id).jailed = True
            tomatoes = await session.exec(
                select(TomatoThrow.target_user_id, func.count()).where(TomatoThrow.guild_id == guild_id).group_by(col(TomatoThrow.target_user_id))
            )
            for user_id, count in tomatoes.all():
                summary(user_id).tomatoes_received = count
            passes = await session.exec(
                select(VipDayPass.user_id).where(VipDayPass.guild_id == guild_id, col(VipDayPass.ended_at).is_(None))
            )
            for user_id in passes.all():
                summary(user_id).vip.append("pass")
            title = await session.get(OnePercentTitle, guild_id)
            if title is not None:
                summary(title.holder_user_id).vip.append("one_percent")
                if title.henchman_user_id is not None:
                    summary(title.henchman_user_id).vip.append("henchman")
        return sorted(summaries.values(), key=lambda s: s.balance, reverse=True)

    # --- Coins --------------------------------------------------------------------------

    async def accounts(self, guild_id: int) -> list[CoinAccount]:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(CoinAccount).where(CoinAccount.guild_id == guild_id).order_by(col(CoinAccount.balance).desc())
            )
            return list(result.all())

    async def ledger(
        self, guild_id: int, user_id: int | None = None, kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[CoinTransaction], int]:
        """Ledger rows, newest first. `user_id` matches either side or the actor."""
        query = select(CoinTransaction).where(CoinTransaction.guild_id == guild_id)
        if user_id is not None:
            query = query.where(
                or_(
                    col(CoinTransaction.from_user_id) == user_id,
                    col(CoinTransaction.to_user_id) == user_id,
                    col(CoinTransaction.actor_user_id) == user_id,
                )
            )
        if kind is not None:
            query = query.where(CoinTransaction.kind == kind)
        async with self.db.transaction() as session:
            return await self._page(session, query.order_by(col(CoinTransaction.id).desc()), limit, offset)

    # --- Jail ---------------------------------------------------------------------------

    async def sentences(self, guild_id: int, user_id: int | None = None, limit: int = 50, offset: int = 0) -> tuple[list[JailSentence], int]:
        """Every sentence, current or served, newest first."""
        query = select(JailSentence).where(JailSentence.guild_id == guild_id)
        if user_id is not None:
            query = query.where(JailSentence.user_id == user_id)
        async with self.db.transaction() as session:
            return await self._page(session, query.order_by(col(JailSentence.started_at).desc()), limit, offset)

    async def jail_stats(self, guild_id: int, limit: int = 20) -> JailStats:
        async with self.db.transaction() as session:
            count = func.count().label("times")
            most = await session.exec(
                select(JailSentence.user_id, count)
                .where(JailSentence.guild_id == guild_id)
                .group_by(col(JailSentence.user_id))
                .order_by(count.desc())
                .limit(limit)
            )
            throws = await session.exec(
                select(TomatoThrow.thrower_user_id, TomatoThrow.target_user_id, TomatoThrow.innocent).where(TomatoThrow.guild_id == guild_id)
            )
            totals: dict[int, TomatoTotals] = {}
            for thrower, target, innocent in throws.all():
                totals.setdefault(target, TomatoTotals(user_id=target)).received += 1
                if thrower is not None:
                    by_thrower = totals.setdefault(thrower, TomatoTotals(user_id=thrower))
                    by_thrower.thrown += 1
                    by_thrower.at_innocents += int(innocent)
            renames = await session.exec(
                select(PendingRename).where(PendingRename.guild_id == guild_id, PendingRename.used == False)  # noqa: E712
            )
            return JailStats(
                most_jailed=[(user_id, times) for user_id, times in most.all()],
                tomatoes=sorted(totals.values(), key=lambda t: (t.received, t.thrown), reverse=True),
                pending_renames=list(renames.all()),
            )

    # --- Markets ------------------------------------------------------------------------

    async def markets(
        self, guild_id: int, status: str | None = None, user_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[MarketRow], int]:
        """Markets, newest first. `user_id` matches the creator or anyone who bet."""
        query = select(PredictionMarket).where(PredictionMarket.guild_id == guild_id)
        if status is not None:
            query = query.where(PredictionMarket.status == status)
        if user_id is not None:
            bettors = select(MarketBet.market_thread_id).where(MarketBet.user_id == user_id)
            query = query.where(or_(col(PredictionMarket.creator_id) == user_id, col(PredictionMarket.thread_id).in_(bettors)))
        async with self.db.transaction() as session:
            markets, total = await self._page(session, query.order_by(col(PredictionMarket.created_at).desc()), limit, offset)
            ids = [m.thread_id for m in markets]
            pools: dict[int, tuple[int, int]] = {}
            if ids:
                result = await session.exec(
                    select(MarketBet.market_thread_id, func.sum(MarketBet.amount), func.count())
                    .where(col(MarketBet.market_thread_id).in_(ids))
                    .group_by(col(MarketBet.market_thread_id))
                )
                pools = {thread_id: (pool, bets) for thread_id, pool, bets in result.all()}
        return [MarketRow(market=m, pool=pools.get(m.thread_id, (0, 0))[0], bets=pools.get(m.thread_id, (0, 0))[1]) for m in markets], total

    async def market_detail(self, thread_id: int) -> MarketDetail | None:
        """Everything about one market, the house's secret bet included."""
        async with self.db.transaction() as session:
            market = await session.get(PredictionMarket, thread_id)
            if market is None:
                return None
            bets = list((await session.exec(select(MarketBet).where(MarketBet.market_thread_id == thread_id).order_by(col(MarketBet.placed_at)))).all())
            ledger = await session.exec(select(CoinTransaction).where(CoinTransaction.thread_id == thread_id).order_by(col(CoinTransaction.id)))
            auto_bet = (await session.exec(select(AutoBetPost).where(AutoBetPost.thread_id == thread_id))).first()
        totals = {choice: 0 for choice in market.choices}
        for bet in bets:
            totals[bet.choice] = totals.get(bet.choice, 0) + bet.amount
        return MarketDetail(market=market, bets=bets, totals=totals, ledger=list(ledger.all()), auto_bet=auto_bet)

    # --- Automatic bets -----------------------------------------------------------------

    async def auto_bet_posts(self, guild_id: int, limit: int = 50, offset: int = 0) -> AutoBetHistory:
        query = select(AutoBetPost).where(AutoBetPost.guild_id == guild_id)
        async with self.db.transaction() as session:
            posts, total = await self._page(session, query.order_by(col(AutoBetPost.posted_at).desc()), limit, offset)
            current = (await session.exec(select(func.max(AutoBetPost.round)).where(AutoBetPost.guild_id == guild_id))).one() or 1
            used = await session.exec(select(AutoBetPost.bet).where(AutoBetPost.guild_id == guild_id, AutoBetPost.round == current))
            return AutoBetHistory(posts=posts, total=total, round=current, used_this_round=sorted(set(used.all())))

    # --- VIP ----------------------------------------------------------------------------

    async def day_pass(self, pass_id: int) -> VipDayPass | None:
        async with self.db.transaction() as session:
            return await session.get(VipDayPass, pass_id)

    async def vip_pass_history(self, guild_id: int, user_id: int | None = None, limit: int = 50, offset: int = 0) -> tuple[list[VipDayPass], int]:
        query = select(VipDayPass).where(VipDayPass.guild_id == guild_id)
        if user_id is not None:
            query = query.where(VipDayPass.user_id == user_id)
        async with self.db.transaction() as session:
            return await self._page(session, query.order_by(col(VipDayPass.started_at).desc()), limit, offset)

    # --- Snipes and heists ---------------------------------------------------------------

    async def snipes(
        self, guild_id: int, status: str | None = None, user_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[SnipeRow], int]:
        """Snipes, newest first, with the votes still standing. `user_id` matches the sniper or the target."""
        query = select(Snipe).where(Snipe.guild_id == guild_id)
        undecided = (col(Snipe.verified_at).is_(None), col(Snipe.rejected_at).is_(None), col(Snipe.expired_at).is_(None))
        if status == "pending":
            query = query.where(*undecided)
        elif status == "verified":
            query = query.where(col(Snipe.verified_at).is_not(None))
        elif status == "rejected":
            query = query.where(col(Snipe.rejected_at).is_not(None))
        elif status == "expired":
            query = query.where(col(Snipe.expired_at).is_not(None))
        if user_id is not None:
            query = query.where(or_(col(Snipe.sniper_user_id) == user_id, col(Snipe.target_user_id) == user_id))
        async with self.db.transaction() as session:
            snipes, total = await self._page(session, query.order_by(col(Snipe.created_at).desc()), limit, offset)
            votes: dict[int, tuple[list[int], list[int]]] = {s.message_id: ([], []) for s in snipes}
            if snipes:
                result = await session.exec(
                    select(SnipeVote)
                    .where(col(SnipeVote.snipe_message_id).in_(list(votes)), col(SnipeVote.removed_at).is_(None))
                    .order_by(col(SnipeVote.voted_at))
                )
                for vote in result.all():
                    votes[vote.snipe_message_id][1 if vote.nay else 0].append(vote.council_user_id)
        return [SnipeRow(snipe=s, votes_for=votes[s.message_id][0], votes_against=votes[s.message_id][1]) for s in snipes], total

    async def heists(
        self, guild_id: int, active: bool | None = None, user_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[HeistRow], int]:
        """Heists, newest first, with their crews. `user_id` matches the leader, target, snitch or anyone in the crew."""
        query = select(Heist).where(Heist.guild_id == guild_id)
        undecided = (col(Heist.succeeded_at).is_(None), col(Heist.snitched_at).is_(None), col(Heist.expired_at).is_(None))
        if active is True:
            query = query.where(*undecided)
        elif active is False:
            query = query.where(
                or_(col(Heist.succeeded_at).is_not(None), col(Heist.snitched_at).is_not(None), col(Heist.expired_at).is_not(None))
            )
        if user_id is not None:
            crews = select(HeistMember.heist_id).where(HeistMember.user_id == user_id)
            query = query.where(
                or_(
                    col(Heist.leader_user_id) == user_id,
                    col(Heist.target_user_id) == user_id,
                    col(Heist.snitch_user_id) == user_id,
                    col(Heist.id).in_(crews),
                )
            )
        async with self.db.transaction() as session:
            heists, total = await self._page(session, query.order_by(col(Heist.started_at).desc()), limit, offset)
            crews_by_heist: dict[int, list[HeistMember]] = {h.id: [] for h in heists if h.id is not None}
            if crews_by_heist:
                result = await session.exec(
                    select(HeistMember).where(col(HeistMember.heist_id).in_(list(crews_by_heist))).order_by(col(HeistMember.joined_at))
                )
                for member in result.all():
                    crews_by_heist[member.heist_id].append(member)
        return [HeistRow(heist=h, crew=crews_by_heist.get(h.id or 0, [])) for h in heists], total

    # --- Daily posts, fat cow, role polls --------------------------------------------------

    async def birthday_messages(self, guild_id: int, limit: int = 50, offset: int = 0) -> tuple[list[BirthdayMessage], int]:
        query = select(BirthdayMessage).where(BirthdayMessage.guild_id == guild_id).order_by(col(BirthdayMessage.sent_on).desc(), col(BirthdayMessage.id).desc())
        async with self.db.transaction() as session:
            return await self._page(session, query, limit, offset)

    async def action_holiday_posts(self, guild_id: int, limit: int = 50, offset: int = 0) -> tuple[list[ActionHolidayPost], int]:
        query = select(ActionHolidayPost).where(ActionHolidayPost.guild_id == guild_id).order_by(col(ActionHolidayPost.sent_on).desc(), col(ActionHolidayPost.id).desc())
        async with self.db.transaction() as session:
            return await self._page(session, query, limit, offset)

    async def fat_cow_stats(self, guild_id: int) -> FatCowStats:
        async with self.db.transaction() as session:
            visits = await session.exec(select(FatCowVisit).where(FatCowVisit.guild_id == guild_id).order_by(col(FatCowVisit.visited_at).desc()))
            per_member = await session.exec(
                select(FatCowConsumption.user_id, func.count(), func.sum(FatCowConsumption.meters))
                .where(FatCowConsumption.guild_id == guild_id)
                .group_by(col(FatCowConsumption.user_id))
            )
            members = [
                FatCowMember(user_id=user_id, visits=count, total_meters=float(total or 0), average_meters=float(total or 0) / count)
                for user_id, count, total in per_member.all()
            ]
            return FatCowStats(visits=list(visits.all()), members=sorted(members, key=lambda m: m.total_meters, reverse=True))

    async def role_polls(self, guild_id: int, limit: int = 50, offset: int = 0) -> tuple[list[RoleNamePoll], int]:
        query = select(RoleNamePoll).where(RoleNamePoll.guild_id == guild_id).order_by(col(RoleNamePoll.created_at).desc())
        async with self.db.transaction() as session:
            return await self._page(session, query, limit, offset)

    # --- Classifier -----------------------------------------------------------------------

    async def classifier_guesses(self, guild_id: int, limit: int = 50, offset: int = 0) -> tuple[list[Guess], int]:
        """The bot's guesses, newest first, each with the newest correction members made to it."""
        query = select(ClassifierProcessedMessage).where(ClassifierProcessedMessage.guild_id == guild_id)
        async with self.db.transaction() as session:
            guesses, total = await self._page(session, query.order_by(col(ClassifierProcessedMessage.processed_at).desc()), limit, offset)
            latest: dict[tuple[int, str], ClassifierCorrection] = {}
            if guesses:
                result = await session.exec(
                    select(ClassifierCorrection)
                    .where(
                        ClassifierCorrection.guild_id == guild_id,
                        col(ClassifierCorrection.message_id).in_([g.message_id for g in guesses]),
                    )
                    .order_by(col(ClassifierCorrection.id))
                )
                for correction in result.all():
                    latest[(correction.message_id, correction.classifier_name)] = correction
        return [Guess(guess=g, correction=latest.get((g.message_id, g.classifier_name))) for g in guesses], total

    async def classifier_drops(self, guild_id: int, limit: int = 20, offset: int = 0) -> tuple[list[Drop], int]:
        """
        The photo drops started in this guild, newest first, each with the photos it added. Photos don't
        record their drop, so they're matched by member, dataset and time. A member has one drop open at a time.
        """
        query = select(ClassifierDropSession).where(ClassifierDropSession.guild_id == guild_id)
        async with self.db.transaction() as session:
            sessions, total = await self._page(session, query.order_by(col(ClassifierDropSession.id).desc()), limit, offset)
            drops: list[Drop] = []
            for drop in sessions:
                photos = select(ClassifierImage).where(
                    ClassifierImage.dataset == drop.dataset,
                    ClassifierImage.source == ClassifierImageSource.DROP,
                    ClassifierImage.added_by == drop.user_id,
                    col(ClassifierImage.added_at) >= drop.started_at,
                )
                if drop.ended_at is not None:
                    photos = photos.where(col(ClassifierImage.added_at) <= drop.ended_at)
                drops.append(Drop(session=drop, photos=list((await session.exec(photos.order_by(col(ClassifierImage.id)))).all())))
        return drops, total
