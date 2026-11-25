"""FastAPI dependencies: the context, a guild's config, the live Discord guild, and who is asking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import discord
from fastapi import Depends, Query, Request

from beans_bot.config.schema import GuildConfig
from beans_bot.web.context import WebContext
from beans_bot.web.errors import NOT_CONNECTED, not_found, unavailable


def get_ctx(request: Request) -> WebContext:
    return request.app.state.ctx


Ctx = Annotated[WebContext, Depends(get_ctx)]


def guild(guild_id: int, ctx: Ctx) -> GuildConfig:
    config = ctx.config.guilds.get(guild_id)
    if config is None:
        raise not_found(f"There's no server {guild_id} in the config.")
    return config


Guild = Annotated[GuildConfig, Depends(guild)]


def discord_guild(guild_id: int, ctx: Ctx, _: Guild) -> discord.Guild:
    if ctx.bot is None or not ctx.connected:
        raise unavailable(NOT_CONNECTED)
    found = ctx.bot.get_guild(guild_id)
    if found is None:
        raise unavailable(f"The bot isn't in server {guild_id}.")
    return found


DiscordGuild = Annotated[discord.Guild, Depends(discord_guild)]


def actor(request: Request) -> str:
    """Who made the request, as Cloudflare Access tells us, or "local" when nothing sits in front."""
    return request.headers.get("Cf-Access-Authenticated-User-Email") or "local"


Actor = Annotated[str, Depends(actor)]


@dataclass
class Paging:
    limit: int
    offset: int


def paging(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> Paging:
    return Paging(limit=limit, offset=offset)


Paged = Annotated[Paging, Depends(paging)]
