"""
The Council of Teds: a fixed set of members who vote on things with their crest emoji.

- Each member owns one crest emoji. Nobody else may react with it.
- A vote passes with more than half the council.
- This knows nothing about what's being voted on. Features like snipes ask it who's on the council.
"""

from __future__ import annotations

import discord

from beans_bot.config.schema import BotConfig, CouncilConfig, CouncilMemberConfig
from beans_bot.services import emoji


class CouncilService:
    def __init__(self, config: BotConfig):
        self.config = config

    def council(self, guild_id: int | None) -> CouncilConfig | None:
        guild = self.config.guild(guild_id)
        return guild.council_of_teds if guild else None

    def members(self, guild_id: int | None) -> list[CouncilMemberConfig]:
        council = self.council(guild_id)
        return list(council.members.values()) if council else []

    def member(self, guild_id: int | None, user_id: int) -> CouncilMemberConfig | None:
        return next((m for m in self.members(guild_id) if m.user_id == user_id), None)

    def crest_owner(self, guild_id: int | None, reaction_emoji: discord.PartialEmoji | discord.Emoji | str) -> CouncilMemberConfig | None:
        """The member whose crest this is, or None if it isn't a crest."""
        return next((m for m in self.members(guild_id) if emoji.matches(reaction_emoji, m.crest_emoji)), None)

    def majority(self, guild_id: int | None) -> int:
        """The fewest votes that are more than half the council."""
        return len(self.members(guild_id)) // 2 + 1
