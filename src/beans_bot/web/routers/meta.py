"""The bot's own status, and the guilds it's configured for."""

from __future__ import annotations

import math

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.config.schema import GuildConfig
from beans_bot.db.types import utcnow
from beans_bot.web.deps import Ctx
from beans_bot.web.schemas import Snowflake

router = APIRouter(prefix="/api", tags=["meta"])

# Every optional config section on GuildConfig is a feature that's off when the section is missing.
FEATURES = [name for name, field in GuildConfig.model_fields.items() if field.default is None and name != "bot_channel_id"]


class BotUser(BaseModel):
    id: Snowflake
    name: str
    avatar_url: str | None


class CurrencyUnit(BaseModel):
    name: str
    plural: str
    base_units: int


class Meta(BaseModel):
    bot: BotUser | None
    connected: bool
    house_user_id: Snowflake | None
    uptime_seconds: float
    latency_ms: float | None
    timezone: str
    currencies: list[CurrencyUnit]
    cogs: list[str]
    classifier_loaded: bool


class GuildSummary(BaseModel):
    id: Snowflake
    name: str
    active: bool
    connected: bool
    member_count: int | None
    bot_channel_id: Snowflake | None
    features: dict[str, bool]


@router.get("/meta")
async def meta(ctx: Ctx) -> Meta:
    bot = ctx.bot
    user = bot.user if bot is not None else None
    latency = bot.latency * 1000 if bot is not None and ctx.connected else None
    return Meta(
        bot=BotUser(id=user.id, name=user.name, avatar_url=user.display_avatar.url) if user is not None else None,
        connected=ctx.connected,
        house_user_id=ctx.services.coins.house_user_id,
        uptime_seconds=(utcnow() - ctx.started_at).total_seconds(),
        latency_ms=latency if latency is not None and math.isfinite(latency) else None,
        timezone=ctx.config.timezone,
        currencies=[CurrencyUnit(name=c.name, plural=c.plural, base_units=c.value) for c in ctx.services.currency.currencies],
        cogs=sorted(bot.cogs) if bot is not None else [],
        classifier_loaded=bot is not None and "ClassifierCog" in bot.cogs,
    )


@router.get("/guilds")
async def guilds(ctx: Ctx) -> list[GuildSummary]:
    summaries = []
    for guild_id, config in ctx.config.guilds.items():
        live = ctx.bot.get_guild(guild_id) if ctx.bot is not None and ctx.connected else None
        summaries.append(
            GuildSummary(
                id=guild_id,
                name=live.name if live is not None else config.name,
                active=config.active,
                connected=live is not None,
                member_count=live.member_count if live is not None else None,
                bot_channel_id=config.bot_channel_id,
                features={name: getattr(config, name) is not None for name in FEATURES},
            )
        )
    return summaries
