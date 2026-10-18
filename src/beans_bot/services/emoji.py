"""Matching configured emoji against reactions, and resolving configured emoji to something we can react with."""

from __future__ import annotations

import discord
import emoji as emoji_lib


def matches(reaction_emoji: discord.PartialEmoji | discord.Emoji | str, configured: str) -> bool:
    """
    True if a reaction is the configured emoji. The config can hold a unicode emoji,
    a custom emoji name ("tojail") or a :shortcode:.
    """
    configured = configured.strip()
    if str(reaction_emoji) == configured:
        return True
    name = getattr(reaction_emoji, "name", None)
    if name is not None and name == configured.strip(":"):
        return True
    return False


def resolve(guild: discord.Guild | None, spec: str) -> list[discord.Emoji | str]:
    """
    Turn a configured emoji spec into emoji to react with. The spec can be a comma-separated
    list. Each entry is a custom guild emoji name (with or without colons), an alias
    shortcode like "cow", or a unicode emoji.
    """
    resolved: list[discord.Emoji | str] = []
    for entry in (e.strip() for e in spec.split(",")):
        if not entry:
            continue
        raw_name = entry.strip(":")
        custom = discord.utils.get(guild.emojis, name=raw_name) if guild else None
        if custom is not None:
            resolved.append(custom)
        elif raw_name.isascii():
            resolved.append(emoji_lib.emojize(f":{raw_name}:", language="alias"))
        else:
            resolved.append(raw_name)
    return resolved


async def react(message: discord.Message, spec: str) -> None:
    """React to a message with every emoji in a spec. Raises discord.HTTPException on failure."""
    for e in resolve(message.guild, spec):
        await message.add_reaction(e)
