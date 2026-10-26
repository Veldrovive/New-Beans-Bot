"""
Automatic bets: prediction markets the bot posts by itself, from a JSON file of bet templates.

- The file is a list of bets. Each has a template with {placeholders}, the values each
  placeholder can take, who manages the market, and optionally its choices and how much
  the bot bets on it. See `AutoBet`.
- The bot picks a bet at random and a random value for each placeholder.
- Bets are used up a round at a time: a bet isn't picked again until every bet in the
  file has been posted, then the next round starts from the full list.
- A bet is identified by its template text, so rewording a template makes it a new bet.
"""

from __future__ import annotations

import datetime
import logging
import random
import string
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field, TypeAdapter, field_validator, model_validator
from sqlmodel import col, func, select

from beans_bot.config.schema import CoinAmount, StrictModel
from beans_bot.currency import CurrencySystem
from beans_bot.db.engine import Database
from beans_bot.db.models import AutoBetPost, PredictionMarket
from beans_bot.services.markets import InvalidMarket, parse_choices

logger = logging.getLogger(__name__)

# Discord's limit on a thread name.
MAX_TITLE = 100


class AutoBet(StrictModel):
    """
    One bet in the bets file, for example:

        {"bet": "{person} will leave the building before {time}",
         "params": {"person": ["Andy", "Aidan"], "time": "5pm"},
         "bot_bet": [1, 5],
         "admins": [123456789012345678]}
    """

    bet: str
    # Placeholder -> the values it can take. A single string is the only value.
    params: dict[str, str | list[str]] = {}
    choices: list[str] = ["yes", "no"]
    # The range the bot bets on this one, in BeanCoins. Missing means the gambling config's usual house bet.
    bot_bet: tuple[CoinAmount, CoinAmount] | None = None
    # Discord user IDs that may close, cancel and resolve the market.
    admins: list[int] = Field(min_length=1)

    @field_validator("params")
    @classmethod
    def _values_not_empty(cls, params: dict[str, str | list[str]]) -> dict[str, str | list[str]]:
        empty = [name for name, values in params.items() if isinstance(values, list) and not values]
        if empty:
            raise ValueError(f"params with no values: {', '.join(empty)}")
        return params

    @field_validator("choices")
    @classmethod
    def _valid_choices(cls, choices: list[str]) -> list[str]:
        try:
            return parse_choices("Choices:\n" + "\n".join(choices))
        except InvalidMarket as e:
            raise ValueError(str(e)) from e

    @field_validator("bot_bet")
    @classmethod
    def _valid_range(cls, bot_bet: tuple[int, int] | None) -> tuple[int, int] | None:
        if bot_bet is not None and not 0 <= bot_bet[0] <= bot_bet[1]:
            raise ValueError("bot_bet must be [low, high] with 0 <= low <= high")
        return bot_bet

    @model_validator(mode="after")
    def _placeholders_match_params(self) -> AutoBet:
        placeholders = self.placeholders()
        if missing := placeholders - self.params.keys():
            raise ValueError(f"{self.bet!r} has placeholders with no params: {', '.join(sorted(missing))}")
        if unused := self.params.keys() - placeholders:
            raise ValueError(f"{self.bet!r} has params it never uses: {', '.join(sorted(unused))}")
        return self

    def placeholders(self) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(self.bet) if name}

    def options(self, name: str) -> list[str]:
        values = self.params[name]
        return [values] if isinstance(values, str) else values


_BETS = TypeAdapter(list[AutoBet])


def load_bets(path: Path) -> list[AutoBet]:
    """Read and check a bets file. Raises pydantic's ValidationError or ValueError if it's wrong."""
    bets = _BETS.validate_json(path.read_text())
    templates = [b.bet for b in bets]
    if duplicates := sorted({t for t in templates if templates.count(t) > 1}):
        raise ValueError(f"{path} lists the same bet more than once: {duplicates}")
    return bets


@dataclass
class Draw:
    bet: AutoBet
    values: dict[str, str]
    text: str
    round: int

    @property
    def title(self) -> str:
        return self.text if len(self.text) <= MAX_TITLE else self.text[: MAX_TITLE - 1] + "…"


class AutoBetService:
    def __init__(self, db: Database, currency: CurrencySystem):
        self.db = db
        self.currency = currency

    async def draw(self, guild_id: int, bets: list[AutoBet]) -> Draw:
        """Pick a bet nobody has seen this round, and fill it in. Starts a new round when they're all used up."""
        if not bets:
            raise ValueError("There are no bets to pick from")
        async with self.db.transaction() as session:
            current = (await session.exec(select(func.max(AutoBetPost.round)).where(AutoBetPost.guild_id == guild_id))).one()
            current = current or 1
            used = set(
                (
                    await session.exec(
                        select(AutoBetPost.bet).where(AutoBetPost.guild_id == guild_id, AutoBetPost.round == current)
                    )
                ).all()
            )
        fresh = [b for b in bets if b.bet not in used]
        if not fresh:
            current += 1
            fresh = bets
        bet = random.choice(fresh)
        values = {name: random.choice(bet.options(name)) for name in bet.placeholders()}
        return Draw(bet=bet, values=values, text=bet.bet.format_map(values), round=current)

    async def record(self, guild_id: int, draw: Draw, *, thread_id: int, creator_id: int) -> PredictionMarket:
        """Open the market for a posted bet and mark the bet used for this round, together."""
        async with self.db.transaction() as session:
            market = PredictionMarket(
                guild_id=guild_id,
                thread_id=thread_id,
                creator_id=creator_id,
                admin_ids=draw.bet.admins,
                choices=draw.bet.choices,
            )
            session.add(market)
            await session.flush()  # The post points at the market, so the market goes in first
            session.add(
                AutoBetPost(
                    guild_id=guild_id, bet=draw.bet.bet, text=draw.text, values=draw.values, round=draw.round, thread_id=thread_id
                )
            )
        logger.info(f"Posted automatic bet {draw.text!r} (round {draw.round}) as market {thread_id} in guild {guild_id}")
        return market

    async def posted_since(self, guild_id: int, since: datetime.datetime) -> bool:
        async with self.db.transaction() as session:
            result = await session.exec(
                select(AutoBetPost.id).where(AutoBetPost.guild_id == guild_id, col(AutoBetPost.posted_at) >= since).limit(1)
            )
            return result.first() is not None

    def bot_bet_amount(self, bet: AutoBet) -> int | None:
        """A random amount in the bet's own range, or None if it has no range (or drew zero)."""
        if bet.bot_bet is None:
            return None
        low, high = bet.bot_bet
        granularity = self.currency.granularity
        steps = (-(-low // granularity), high // granularity)  # Whole coin steps inside the range
        if steps[0] > steps[1]:
            return None
        amount = random.randint(*steps) * granularity
        return amount if amount > 0 else None
