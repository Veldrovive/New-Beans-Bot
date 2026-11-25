"""Automatic bets: the bets file, which bets this round has used, and what was posted."""

from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.services.auto_bets import load_bets
from beans_bot.web.actions import feature, logger
from beans_bot.web.deps import Actor, Ctx, DiscordGuild, Guild, Paged
from beans_bot.web.errors import unavailable
from beans_bot.web.schemas import Amount, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/auto-bets", tags=["auto bets"])


class BetTemplate(BaseModel):
    bet: str
    params: dict[str, list[str]]
    choices: list[str]
    bot_bet: list[Amount] | None  # [low, high]
    admins: list[Snowflake]
    used_this_round: bool


class PostOut(BaseModel):
    id: int
    bet: str
    text: str
    values: dict[str, str]
    round: int
    thread_id: Snowflake
    posted_at: datetime.datetime


class AutoBetsOut(BaseModel):
    enabled: bool
    bets_file: str | None
    post_time: str | None
    post_probability: float | None
    error: str | None  # Why the bets file couldn't be read
    bets: list[BetTemplate]
    round: int
    history: Page[PostOut]


@router.get("")
async def auto_bets(guild_id: int, ctx: Ctx, guild: Guild, page: Paged) -> AutoBetsOut:
    currency = ctx.services.currency
    history = await ctx.services.reports.auto_bet_posts(guild_id, page.limit, page.offset)
    config = guild.auto_bets
    templates: list[BetTemplate] = []
    error = None
    if config is not None:
        try:
            bets = await asyncio.to_thread(load_bets, config.bets_file)
        except (OSError, ValueError) as e:  # pydantic's ValidationError is a ValueError
            error = str(e)
        else:
            used = set(history.used_this_round)
            templates = [
                BetTemplate(
                    bet=b.bet,
                    params={name: b.options(name) for name in b.params},
                    choices=b.choices,
                    bot_bet=[Amount.of(v, currency) for v in b.bot_bet] if b.bot_bet is not None else None,
                    admins=b.admins,
                    used_this_round=b.bet in used,
                )
                for b in bets
            ]
    return AutoBetsOut(
        enabled=config is not None,
        bets_file=str(config.bets_file) if config else None,
        post_time=config.post_time.isoformat() if config else None,
        post_probability=config.post_probability if config else None,
        error=error,
        bets=templates,
        round=history.round,
        history=Page(items=[PostOut(**p.model_dump(exclude={"guild_id", "id"}), id=p.id or 0) for p in history.posts], total=history.total),
    )


class Posted(BaseModel):
    thread_id: Snowflake
    jump_url: str
    message: str


@router.post("/post")
async def post_now(guild_id: int, ctx: Ctx, guild: Guild, live: DiscordGuild, actor: Actor) -> Posted:
    """Post an automatic bet now, on top of the daily one. The auto bets cog does the posting."""
    feature(guild, "auto_bets")
    poster = ctx.services.auto_bets.poster
    if poster is None:
        raise unavailable("The automatic bets cog isn't loaded, so there's no bookie to post one.")
    logger.info(f"Web {actor}: post an automatic bet in guild {guild_id}")
    thread = await poster(live)
    return Posted(thread_id=thread.id, jump_url=thread.jump_url, message="Fresh bet posted. Somebody's losing money today.")
