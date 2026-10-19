"""
Coin amounts.

Every amount the bot stores or moves is an integer number of *base units*.
One BeanCoin is BASE_UNITS_PER_BEAN base units, so smaller denominations
(CheddarCoin = 1/10 BeanCoin) are just currencies with a smaller value and
never need a data migration. Conversion to and from human text happens only
at the edges: config parsing, command arguments and messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BASE_UNITS_PER_BEAN = 100


@dataclass(frozen=True)
class Currency:
    name: str
    plural: str
    value: int  # In base units
    aliases: tuple[str, ...]

    def label(self, count: int) -> str:
        return self.name if count == 1 else self.plural


BEANCOIN = Currency("BeanCoin", "BeanCoins", BASE_UNITS_PER_BEAN, ("bean", "beans", "beancoin", "beancoins", "b"))
CHEDDARCOIN = Currency("CheddarCoin", "CheddarCoins", BASE_UNITS_PER_BEAN // 10, ("cheddar", "cheddars", "cheddarcoin", "cheddarcoins", "c"))

ALL_CURRENCIES = (BEANCOIN, CHEDDARCOIN)

# Words that mean "my whole balance" in commands that accept it (see CurrencySystem.parse_bet).
ALL_IN_WORDS = frozenset({"all", "all in", "allin", "everything", "max"})

_AMOUNT_RE = re.compile(r"^\s*(?P<number>[+-]?\d+(?:\.\d+)?|[+-]?\.\d+)\s*(?P<unit>[a-z]+)?\s*$", re.IGNORECASE)


class AmountError(ValueError):
    """The text could not be turned into a valid coin amount. The message is safe to show users."""


class CurrencySystem:
    """The set of currencies that are currently enabled, used for parsing and formatting."""

    def __init__(self, cheddar_enabled: bool = False):
        self.currencies = [BEANCOIN, CHEDDARCOIN] if cheddar_enabled else [BEANCOIN]
        self.currencies.sort(key=lambda c: c.value, reverse=True)

    @property
    def smallest(self) -> Currency:
        return self.currencies[-1]

    @property
    def granularity(self) -> int:
        """The smallest amount (in base units) that users can hold or move."""
        return self.smallest.value

    def floor(self, amount: int) -> int:
        """Round an amount down to something users can hold."""
        return amount - (amount % self.granularity)

    def parse(self, text: str) -> int:
        """
        Parse user input like "5", "1.5", "3 cheddar" or "2 beans" into base units.
        A bare number is in BeanCoins. The result must be positive and a whole number
        of the smallest enabled currency.
        """
        amount = parse_amount(text, self.currencies)
        if amount <= 0:
            raise AmountError("Amount must be greater than 0.")
        if amount % self.granularity != 0:
            raise AmountError(f"The smallest amount you can use is 1 {self.smallest.name}.")
        return amount

    def parse_bet(self, text: str) -> int | None:
        """
        Like parse, but "all in" (or "all", "everything", ...) returns None, meaning
        the member's whole balance. The caller resolves None inside its transaction.
        """
        if text.strip().lower() in ALL_IN_WORDS:
            return None
        return self.parse(text)

    def format(self, amount: int) -> str:
        """Format base units as text, e.g. "12 BeanCoins" or "1 BeanCoin 5 CheddarCoins"."""
        sign = "-" if amount < 0 else ""
        remaining = abs(amount)
        parts = []
        for currency in self.currencies:
            count, remaining = divmod(remaining, currency.value)
            if count:
                parts.append(f"{count} {currency.label(count)}")
        if remaining:
            # Leftover base units below the smallest enabled currency (should not normally happen).
            parts.append(f"{remaining} base units")
        if not parts:
            return f"0 {self.currencies[0].plural}"
        return sign + " ".join(parts)


def parse_amount(text: str | int | float, currencies: list[Currency] | tuple[Currency, ...] = ALL_CURRENCIES) -> int:
    """Parse an amount into base units without any positivity or granularity checks."""
    if isinstance(text, bool):
        raise AmountError(f"Invalid amount {text!r}.")
    if isinstance(text, (int, float)):
        text = str(text)
    match = _AMOUNT_RE.match(text)
    if not match:
        raise AmountError(f"'{text}' is not a valid amount. Try something like `5` or `5 beans`.")

    unit_text = (match.group("unit") or "").lower()
    if unit_text:
        currency = next((c for c in currencies if unit_text in c.aliases), None)
        if currency is None:
            names = ", ".join(c.plural for c in currencies)
            raise AmountError(f"Unknown currency '{unit_text}'. Valid currencies: {names}.")
    else:
        currency = BEANCOIN

    try:
        base_units = Decimal(match.group("number")) * currency.value
    except InvalidOperation as e:
        raise AmountError(f"'{text}' is not a valid amount.") from e
    if base_units != base_units.to_integral_value():
        raise AmountError(f"'{text}' is smaller than the smallest coin.")
    return int(base_units)
