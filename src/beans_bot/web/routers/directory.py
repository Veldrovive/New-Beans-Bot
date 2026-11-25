"""
Names for the IDs every other page shows: members, roles and channels, from the bot's cache.
Empty when the bot isn't connected, so pages fall back to raw IDs.
"""

from __future__ import annotations

import discord
from fastapi import APIRouter
from pydantic import BaseModel

from beans_bot.web.deps import Ctx, Guild

router = APIRouter(prefix="/api/guilds/{guild_id}", tags=["directory"])


class DirectoryMember(BaseModel):
    name: str
    display_name: str
    avatar_url: str | None
    bot: bool


class DirectoryRole(BaseModel):
    name: str
    color: str | None


class DirectoryChannel(BaseModel):
    name: str
    type: str  # "text", "forum", "thread", "voice", ...
    parent_id: str | None = None


class Directory(BaseModel):
    # Keys are Discord IDs, as strings.
    members: dict[str, DirectoryMember]
    roles: dict[str, DirectoryRole]
    channels: dict[str, DirectoryChannel]


@router.get("/directory")
async def directory(guild_id: int, ctx: Ctx, _: Guild) -> Directory:
    live = ctx.bot.get_guild(guild_id) if ctx.bot is not None and ctx.connected else None
    if live is None:
        return Directory(members={}, roles={}, channels={})
    members = {
        str(m.id): DirectoryMember(name=m.name, display_name=m.display_name, avatar_url=m.display_avatar.url, bot=m.bot)
        for m in live.members
    }
    roles = {str(r.id): DirectoryRole(name=r.name, color=str(r.color) if r.color.value else None) for r in live.roles}
    channels: dict[str, DirectoryChannel] = {}
    for channel in live.channels:
        if isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.StageChannel)):
            channels[str(channel.id)] = DirectoryChannel(name=channel.name, type=str(channel.type))
    for thread in live.threads:
        channels[str(thread.id)] = DirectoryChannel(name=thread.name, type="thread", parent_id=str(thread.parent_id))
    return Directory(members=members, roles=roles, channels=channels)
