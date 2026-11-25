"""The bot's own actions, on the Overview page: speak as the bot, DM a member, sync slash commands."""

from __future__ import annotations

import discord
from fastapi import APIRouter
from pydantic import BaseModel, Field

from beans_bot.web.actions import logger
from beans_bot.web.context import WebContext
from beans_bot.web.deps import Actor, Ctx, DiscordGuild
from beans_bot.web.errors import NOT_CONNECTED, bad_gateway, bad_request, conflict, not_found, unavailable
from beans_bot.web.schemas import Done, Snowflake

router = APIRouter(prefix="/api", tags=["bot"])

# Puppeting can ping members, but never @everyone or roles by accident. Same as /admin say.
PUPPET_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


class Say(BaseModel):
    channel_id: Snowflake
    message: str = Field(min_length=1, max_length=2000)


class Dm(BaseModel):
    user_id: Snowflake
    message: str = Field(min_length=1, max_length=2000)


class Said(BaseModel):
    jump_url: str
    message: str


class Synced(BaseModel):
    count: int
    message: str


def _bot(ctx: WebContext):
    if ctx.bot is None or not ctx.connected:
        raise unavailable(NOT_CONNECTED)
    return ctx.bot


@router.post("/guilds/{guild_id}/admin/say")
async def say(guild_id: int, body: Say, ctx: Ctx, live: DiscordGuild, actor: Actor) -> Said:
    channel = live.get_channel_or_thread(body.channel_id)
    if channel is None:
        raise not_found("That channel isn't in this server, or I can't see it.")
    if not isinstance(channel, discord.abc.Messageable) or isinstance(channel, (discord.ForumChannel, discord.CategoryChannel)):
        raise bad_request("I can't talk in there. Pick a text channel or thread and I'll be your mouthpiece.")
    logger.info(f"Web {actor}: say in channel {body.channel_id} of guild {guild_id}: {body.message!r}")
    try:
        sent = await channel.send(body.message, allowed_mentions=PUPPET_MENTIONS)
    except discord.HTTPException as e:
        raise bad_gateway(f"Discord gagged me ({e.text or e.status}). Check my permissions in that channel.")
    return Said(jump_url=sent.jump_url, message="Said it. Your hand is invisible.")


@router.post("/guilds/{guild_id}/admin/dm")
async def dm(guild_id: int, body: Dm, ctx: Ctx, live: DiscordGuild, actor: Actor) -> Done:
    bot = _bot(ctx)
    try:
        user = await bot.fetch_user(body.user_id)
    except discord.NotFound:
        raise not_found("Discord has never heard of that user.")
    if user.bot:
        raise bad_request("Bots don't read their DMs. Trust me.")
    logger.info(f"Web {actor}: DM {body.user_id} (from guild {guild_id}): {body.message!r}")
    try:
        await user.send(body.message, allowed_mentions=PUPPET_MENTIONS)
    except discord.Forbidden:
        raise conflict("Their DMs are closed. Whisper denied.")
    except discord.HTTPException as e:
        raise bad_gateway(f"Discord ate the DM ({e.text or e.status}).")
    return Done(message="Slid into their DMs. They'll never know it was you.")


@router.post("/admin/sync")
async def sync(ctx: Ctx, actor: Actor) -> Synced:
    bot = _bot(ctx)
    logger.info(f"Web {actor}: sync slash commands")
    synced = await bot.tree.sync()
    return Synced(count=len(synced), message=f"Synced {len(synced)} commands with Discord.")
