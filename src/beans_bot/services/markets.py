"""
Prediction markets: betting pools on threads in a guild's gambling forum.

Payouts are pari-mutuel. Winners split the whole pool in proportion to their
bets, rounded down to the smallest coin, and the house keeps what's left.
If nobody (or everybody) picked the winner, every bet is refunded.
Members can swap their whole bet to another choice while betting is open.
The cog makes them pay for it with jail and an @everyone.
- Members in jail can't place, add to or swap bets.
- A member who beats the house when nobody else bet, and the market resolves
  soon after it opened, looks like they rigged it. Everyone gets their own
  stake back, and the cog sends the member to jail.
"""

from __future__ import annotations

import datetime
import logging
import random
from dataclasses import dataclass, field

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.config.schema import BotBettingConfig
from beans_bot.db.engine import Database
from beans_bot.db.models import CoinTransactionKind, MarketBet, MarketStatus, PredictionMarket
from beans_bot.db.types import utcnow
from beans_bot.services.coins import CoinService, InsufficientFunds
from beans_bot.services.errors import UserFacingError
from beans_bot.services.jail import JailService

logger = logging.getLogger(__name__)

RESERVED_CHOICE_WORDS = ("close", "cancel", "wins")


class InvalidMarket(UserFacingError):
    pass


def parse_choices(content: str) -> list[str]:
    """
    Parse the choices from a thread's starter message: a `Choices:` line followed
    by one single-word choice per line. Raises InvalidMarket with a reason.
    """
    choices = []
    in_choices = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("choices:"):
            in_choices = True
            continue
        if in_choices:
            choices.append(line.lower())

    if not choices:
        raise InvalidMarket("It must contain `Choices:` followed by a list of choices on new lines.")
    if len(choices) < 2:
        raise InvalidMarket("It must contain at least two choices.")
    if any(word in choice for choice in choices for word in RESERVED_CHOICE_WORDS):
        raise InvalidMarket("The choices may not include the words 'close', 'cancel', or 'wins'.")
    if any(" " in choice for choice in choices):
        raise InvalidMarket("The choices may not include spaces. Please use underscores or hyphens instead.")
    duplicates = sorted({c for c in choices if choices.count(c) > 1})
    if duplicates:
        raise InvalidMarket(f"There are duplicates of the same choice: {', '.join(duplicates)}.")
    return choices


@dataclass
class MarketStats:
    pool: int
    by_choice: dict[str, int]


@dataclass
class Swap:
    bet: MarketBet
    old_choice: str


@dataclass
class Resolution:
    winning_choice: str
    pool: int
    refunded: bool = False  # Nobody, or everybody, picked the winner
    payouts: list[tuple[int, int]] = field(default_factory=list)  # (user_id, amount)
    house_remainder: int = 0
    rigged_by: int | None = None  # The member who beat the house alone. Refunded, and the cog jails them.
    denied: int = 0  # The house's stake that rigged_by didn't get


class MarketService:
    def __init__(self, db: Database, coins: CoinService, jail: JailService):
        self.db = db
        self.coins = coins
        self.jail = jail

    @property
    def currency(self):
        return self.coins.currency

    async def _market(self, session: AsyncSession, thread_id: int) -> PredictionMarket:
        market = await session.get(PredictionMarket, thread_id)
        if market is None:
            raise UserFacingError("This thread is not a prediction market.")
        return market

    async def _bets(self, session: AsyncSession, thread_id: int) -> list[MarketBet]:
        result = await session.exec(select(MarketBet).where(MarketBet.market_thread_id == thread_id))
        return list(result.all())

    async def _require_free(self, session: AsyncSession, market: PredictionMarket, user_id: int, message: str) -> None:
        if await self.jail._current_sentence(session, market.guild_id, user_id) is not None:
            raise UserFacingError(message)

    async def _refund_all(self, session: AsyncSession, market: PredictionMarket, bets: list[MarketBet]) -> None:
        for bet in bets:
            await self.coins.move(
                session,
                guild_id=market.guild_id,
                amount=bet.amount,
                kind=CoinTransactionKind.BET_REFUND,
                to_user_id=bet.user_id,
                thread_id=market.thread_id,
            )

    async def _stake(self, session: AsyncSession, market: PredictionMarket, user_id: int, amount: int | None) -> int:
        """Move a member's stake to the market. None means all in. Returns the amount staked."""
        if amount is None:
            # Read the balance in this transaction so nothing can change it before the move.
            amount = self.currency.floor(await self.coins.balance_in(session, market.guild_id, user_id))
            if amount <= 0:
                raise UserFacingError("You went all in with nothing. Bold. Also broke. You have 0 BeanCoins to bet.")
        try:
            await self.coins.move(
                session,
                guild_id=market.guild_id,
                amount=amount,
                kind=CoinTransactionKind.BET_PLACE,
                from_user_id=user_id,
                thread_id=market.thread_id,
            )
        except InsufficientFunds as e:
            raise UserFacingError(
                f"You don't have enough BeanCoins! You only have {self.currency.format(e.balance)}."
            ) from e
        return amount

    # --- Queries ------------------------------------------------------------------------

    async def get(self, thread_id: int) -> PredictionMarket | None:
        async with self.db.transaction() as session:
            return await session.get(PredictionMarket, thread_id)

    async def stats(self, thread_id: int) -> MarketStats:
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            by_choice = {c: 0 for c in market.choices}
            for bet in await self._bets(session, thread_id):
                by_choice[bet.choice] = by_choice.get(bet.choice, 0) + bet.amount
            return MarketStats(pool=sum(by_choice.values()), by_choice=by_choice)

    # --- Lifecycle ----------------------------------------------------------------------

    async def create(self, *, guild_id: int, thread_id: int, creator_id: int, choices: list[str]) -> PredictionMarket:
        async with self.db.transaction() as session:
            market = PredictionMarket(guild_id=guild_id, thread_id=thread_id, creator_id=creator_id, choices=choices)
            session.add(market)
        return market

    def _require_creator(self, market: PredictionMarket, user_id: int) -> None:
        if user_id != market.creator_id:
            raise UserFacingError("Only the creator of the prediction can use this command.")

    async def close(self, thread_id: int, user_id: int) -> None:
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            self._require_creator(market, user_id)
            if market.status != MarketStatus.OPEN:
                raise UserFacingError(f"Market is already {market.status}.")
            market.status = MarketStatus.CLOSED

    async def cancel(self, thread_id: int, user_id: int | None) -> int:
        """
        Cancel and refund every bet. `user_id` None skips the creator check (thread deleted).
        Returns the number of refunded bets.
        """
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            if user_id is not None:
                self._require_creator(market, user_id)
            if market.status in (MarketStatus.RESOLVED, MarketStatus.CANCELED):
                raise UserFacingError(f"Market is already {market.status}.")
            bets = await self._bets(session, thread_id)
            await self._refund_all(session, market, bets)
            market.status = MarketStatus.CANCELED
            return len(bets)

    async def resolve(
        self, thread_id: int, user_id: int, winning_choice: str, *, rigged_window: datetime.timedelta | None = None
    ) -> Resolution:
        """Name the winner and pay out. `rigged_window` None means a solo win over the house always counts as rigged."""
        winning_choice = winning_choice.lower()
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            self._require_creator(market, user_id)
            if market.status in (MarketStatus.RESOLVED, MarketStatus.CANCELED):
                raise UserFacingError(f"Market is already {market.status}.")
            if winning_choice not in market.choices:
                raise UserFacingError(f"Invalid option '{winning_choice}'. Valid choices: {', '.join(market.choices)}")

            market.status = MarketStatus.RESOLVED
            market.resolved_at = utcnow()
            market.winning_choice = winning_choice

            bets = await self._bets(session, thread_id)
            pool = sum(b.amount for b in bets)
            winning_bets = [b for b in bets if b.choice == winning_choice]
            winning_total = sum(b.amount for b in winning_bets)
            resolution = Resolution(winning_choice=winning_choice, pool=pool)

            if pool == 0:
                return resolution
            if winning_total == 0 or winning_total == pool:
                await self._refund_all(session, market, bets)
                resolution.refunded = True
                return resolution

            # One member against the house, and the member won fast: probably whoever resolved it is in on it.
            # Nobody wins the house's coins, and the cog jails the member so they don't try again.
            # Markets that ran a while are left alone, since speed is what makes the scam work.
            house = self.coins.house_user_id
            solo_win = len(bets) == 2 and any(b.user_id == house for b in bets) and winning_bets[0].user_id != house
            too_fast = rigged_window is None or market.resolved_at - market.created_at < rigged_window
            if solo_win and too_fast:
                await self._refund_all(session, market, bets)
                resolution.refunded = True
                resolution.rigged_by = winning_bets[0].user_id
                resolution.denied = pool - winning_total
                logger.info(
                    f"{resolution.rigged_by} beat the house alone on market {thread_id} in guild {market.guild_id}; "
                    f"refunded instead of paying {resolution.denied}"
                )
                return resolution

            for bet in winning_bets:
                payout = self.currency.floor(pool * bet.amount // winning_total)
                if payout > 0:
                    await self.coins.move(
                        session,
                        guild_id=market.guild_id,
                        amount=payout,
                        kind=CoinTransactionKind.BET_PAYOUT,
                        to_user_id=bet.user_id,
                        thread_id=thread_id,
                    )
                resolution.payouts.append((bet.user_id, payout))

            resolution.house_remainder = pool - sum(p for _, p in resolution.payouts)
            if resolution.house_remainder > 0:
                await self.coins.move(
                    session,
                    guild_id=market.guild_id,
                    amount=resolution.house_remainder,
                    kind=CoinTransactionKind.HOUSE_REMAINDER,
                    to_user_id=self.coins.house_user_id,
                    thread_id=thread_id,
                )
            return resolution

    # --- Betting ------------------------------------------------------------------------

    async def place_bet(self, thread_id: int, user_id: int, choice: str, amount: int | None) -> MarketBet:
        """Place a bet. An amount of None goes all in."""
        choice = choice.lower()
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            if market.status != MarketStatus.OPEN:
                raise UserFacingError(f"Betting is currently {market.status}.")
            if choice not in market.choices:
                raise UserFacingError(f"Invalid option '{choice}'. Valid choices: {', '.join(market.choices)}")
            if await session.get(MarketBet, (thread_id, user_id)) is not None:
                raise UserFacingError("You have already placed a bet on this prediction!")
            await self._require_free(
                session, market, user_id, "Prisoners don't gamble. Serve your time, then lose your money like a free member."
            )
            amount = await self._stake(session, market, user_id, amount)
            bet = MarketBet(market_thread_id=thread_id, user_id=user_id, choice=choice, amount=amount)
            session.add(bet)
            return bet

    async def add_to_bet(self, thread_id: int, user_id: int, amount: int | None) -> tuple[MarketBet, int]:
        """Add to a bet. An amount of None goes all in. Returns the bet and the amount added."""
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            if market.status != MarketStatus.OPEN:
                raise UserFacingError(f"Betting is currently {market.status}.")
            bet = await session.get(MarketBet, (thread_id, user_id))
            if bet is None:
                raise UserFacingError("You have not placed a bet yet. Place a bet first before adding to it.")
            await self._require_free(
                session, market, user_id, "You can't raise from a cell. Your bet stays where it is until you're out."
            )
            added = await self._stake(session, market, user_id, amount)
            bet.amount += added
            return bet, added

    async def swap_bet(self, thread_id: int, user_id: int, choice: str) -> Swap:
        """Move a member's whole bet to another choice. No coins move: the stake stays in the pool."""
        choice = choice.lower()
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            if market.status != MarketStatus.OPEN:
                raise UserFacingError(f"Betting is currently {market.status}. Your bet is stuck where you put it.")
            if choice not in market.choices:
                raise UserFacingError(f"Invalid option '{choice}'. Valid choices: {', '.join(market.choices)}")
            bet = await session.get(MarketBet, (thread_id, user_id))
            if bet is None:
                raise UserFacingError("You can't flip-flop on a bet you never placed. Use `/bet place` first.")
            if bet.choice == choice:
                raise UserFacingError(f"Your bet is already on **{choice}**. Nothing to swap, coward.")
            # Swapping from a cell would be free, since the jail is what swapping costs.
            await self._require_free(
                session, market, user_id, "You're already in jail. Serve your time before you flip-flop again."
            )
            swap = Swap(bet=bet, old_choice=bet.choice)
            bet.choice = choice
            logger.info(
                f"{user_id} swapped their {bet.amount} bet on market {thread_id} in guild {market.guild_id} "
                f"from {swap.old_choice} to {choice}"
            )
            return swap

    async def house_bet(self, thread_id: int, choice: str, amount: int) -> MarketBet:
        """
        Bet from the house account (it may go negative). Betting again on the same choice
        adds to the bet; betting on a different choice refunds the old bet and replaces it.
        """
        house = self.coins.house_user_id
        assert house is not None, "House account is not set"
        choice = choice.lower()
        async with self.db.transaction() as session:
            market = await self._market(session, thread_id)
            if market.status != MarketStatus.OPEN:
                raise UserFacingError(f"This market is currently {market.status}.")
            if choice not in market.choices:
                raise UserFacingError(f"Invalid choice '{choice}'. Valid choices are: {', '.join(market.choices)}")

            bet = await session.get(MarketBet, (thread_id, house))
            if bet is not None and bet.choice != choice:
                await self.coins.move(
                    session,
                    guild_id=market.guild_id,
                    amount=bet.amount,
                    kind=CoinTransactionKind.BET_REFUND,
                    to_user_id=house,
                    thread_id=thread_id,
                )
                bet.choice = choice
                bet.amount = 0
            await self._stake(session, market, house, amount)
            if bet is None:
                bet = MarketBet(market_thread_id=thread_id, user_id=house, choice=choice, amount=amount)
                session.add(bet)
            else:
                bet.amount += amount
                bet.placed_at = utcnow()
            return bet

    async def auto_bet_amount(self, guild_id: int, config: BotBettingConfig) -> int | None:
        """
        How much the house bets on a new market, or None if it sits this one out.
        It bets a proportion of its (non-negative) balance, capped at max_bet,
        but never less than a random floor from min_bet_range.
        """
        if not config.enabled or random.random() >= config.bet_probability:
            return None
        assert self.coins.house_user_id is not None
        balance = await self.coins.balance(guild_id, self.coins.house_user_id)
        normal_bet = int(min(max(balance, 0) * config.bet_proportion, config.max_bet))
        low, high = config.min_bet_range
        granularity = self.currency.granularity
        floor_amount = random.randint(low // granularity, high // granularity) * granularity
        amount = self.currency.floor(max(normal_bet, floor_amount))
        return amount if amount > 0 else None
