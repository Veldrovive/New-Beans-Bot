"""Jail: who's in, every sentence, and the leaderboards."""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.config.schema import JailConfig
from beans_bot.db.models import JailReason
from beans_bot.services.jail import JailOutcome
from beans_bot.web.actions import feature, logger
from beans_bot.web.deps import Actor, Ctx, DiscordGuild, Guild, Paged
from beans_bot.web.errors import bad_request, conflict
from beans_bot.web.schemas import Done, Out, Page, Snowflake

router = APIRouter(prefix="/api/guilds/{guild_id}/jail", tags=["jail"])


class SentenceRow(Out):
    id: int
    user_id: Snowflake
    channel_id: Snowflake
    offending_message_id: Snowflake | None
    started_at: datetime.datetime
    ends_at: datetime.datetime
    released_at: datetime.datetime | None
    release_reason: str | None
    humiliated: bool
    reason: str | None


class Count(BaseModel):
    user_id: Snowflake
    count: int


class TomatoRow(Out):
    user_id: Snowflake
    thrown: int
    received: int
    at_innocents: int


class RenameRow(Out):
    id: int
    prompt_message_id: Snowflake
    target_user_id: Snowflake
    offending_message_id: Snowflake


class JailStatsOut(BaseModel):
    most_jailed: list[Count]
    tomatoes: list[TomatoRow]
    pending_renames: list[RenameRow]


@router.get("/current")
async def current(guild_id: int, ctx: Ctx, _: Guild) -> list[SentenceRow]:
    return [SentenceRow.model_validate(s) for s in await ctx.services.jail.current_sentences(guild_id)]


@router.get("/sentences")
async def sentences(guild_id: int, ctx: Ctx, _: Guild, page: Paged, user_id: int | None = None) -> Page[SentenceRow]:
    rows, total = await ctx.services.reports.sentences(guild_id, user_id, page.limit, page.offset)
    return Page(items=[SentenceRow.model_validate(s) for s in rows], total=total)


@router.get("/stats")
async def stats(guild_id: int, ctx: Ctx, _: Guild) -> JailStatsOut:
    result = await ctx.services.reports.jail_stats(guild_id)
    return JailStatsOut(
        most_jailed=[Count(user_id=user_id, count=times) for user_id, times in result.most_jailed],
        tomatoes=[TomatoRow.model_validate(t) for t in result.tomatoes],
        pending_renames=[RenameRow.model_validate(r) for r in result.pending_renames],
    )


# --- Actions ---------------------------------------------------------------------------


class Target(BaseModel):
    user_id: Snowflake


OUTCOME_MESSAGES = {
    JailOutcome.ALREADY_JAILED: "They're already in jail. Double jeopardy, but the fun kind, isn't a thing.",
    JailOutcome.MEMBER_NOT_FOUND: "They aren't in the server. You can't jail a ghost.",
    JailOutcome.ROLE_NOT_FOUND: "The jail role is missing from the server, so the stocks are out of order.",
    JailOutcome.MISSING_PERMISSIONS: "Discord won't let me hand out the jail role. Check my role is above it.",
    JailOutcome.MESSAGE_ALREADY_USED: "That message already sent someone to jail.",
}


@router.post("/release")
async def release(guild_id: int, body: Target, ctx: Ctx, guild: Guild, live: DiscordGuild, actor: Actor) -> Done:
    config: JailConfig = feature(guild, "jail")
    if not await ctx.services.jail.is_jailed(guild_id, body.user_id):
        raise conflict("They aren't in jail. You can't free the free.")
    logger.info(f"Web {actor}: release {body.user_id} from jail in guild {guild_id}")
    # release() takes the role off too, or the desync check would put half of them back in.
    member = await ctx.services.jail.release(live, config, body.user_id, reason="admin", audit_reason="Released from the admin site.")
    if member is None:
        return Done(message="Released. They've left the server, so there was no role to take off.")
    return Done(message="Released, quietly. Nobody was told.")


@router.post("/imprison")
async def imprison(guild_id: int, body: Target, ctx: Ctx, guild: Guild, live: DiscordGuild, actor: Actor) -> Done:
    config: JailConfig = feature(guild, "jail")
    if guild.bot_channel_id is None:
        raise bad_request("This server has no bot_channel_id in its config, and a sentence needs a channel to belong to.")
    logger.info(f"Web {actor}: jail {body.user_id} in guild {guild_id}")
    result = await ctx.services.jail.imprison(
        live,
        config,
        body.user_id,
        channel_id=guild.bot_channel_id,
        offending_message_id=None,
        audit_reason="Jailed from the admin site.",
        reason=JailReason.ADMIN,
    )
    if result.outcome is not JailOutcome.JAILED:
        raise conflict(OUTCOME_MESSAGES[result.outcome])
    hours = config.jail_length.total_seconds() / 3600
    return Done(message=f"Into the stocks for {hours:g} hours. No announcement was made.")
