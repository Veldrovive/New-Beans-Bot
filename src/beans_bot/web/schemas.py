"""
Types shared by the API's responses.

Discord IDs don't fit in a JavaScript number, so every ID goes out as a string (`Snowflake`) and is
accepted as a string or a number. Dict keys that are IDs are strings too.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, WithJsonSchema

from beans_bot.currency import CurrencySystem

Snowflake = Annotated[
    int,
    BeforeValidator(int),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": "^[0-9]+$"}),
]

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int


class Amount(BaseModel):
    """Coins: the integer base units, and the same amount as text."""

    value: int
    text: str

    @classmethod
    def of(cls, value: int, currency: CurrencySystem) -> Amount:
        return cls(value=value, text=currency.format(value))


class Done(BaseModel):
    """A plain answer for actions with nothing else to say."""

    message: str


class Out(BaseModel):
    """A response model that can be built straight from a database row."""

    model_config = ConfigDict(from_attributes=True)
