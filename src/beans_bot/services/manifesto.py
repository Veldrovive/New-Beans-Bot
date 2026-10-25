"""
Manifestos: members write one, and the bot quotes the wisdom in it.

- A manifesto lives in manifestos/<name>/ next to a wisdom.json, which is a list of the
  opinions pulled out of it. The raw manifesto can be personal, so the bot never reads it.
- Each guild's config maps a member's user ID to their manifesto folder.
- Wisdom is numbered from 1. Leave out the member or the number and one is picked at random.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass

from beans_bot.config.schema import BotConfig

logger = logging.getLogger(__name__)


class NoManifesto(Exception):
    """The member hasn't written a manifesto, or the guild has none at all."""


class NoSuchWisdom(Exception):
    def __init__(self, most: int):
        super().__init__(f"There are only {most} pieces of wisdom")
        self.most = most


@dataclass
class Wisdom:
    author_id: int
    index: int  # From 1
    total: int
    text: str


class ManifestoService:
    def __init__(self, config: BotConfig):
        self.config = config
        self._wisdom: dict[str, list[str]] = {}

    def authors(self, guild_id: int | None) -> dict[int, str]:
        guild = self.config.guild(guild_id)
        return guild.manifesto.authors if guild and guild.manifesto else {}

    def wisdom(self, name: str) -> list[str]:
        """The wisdom from one manifesto folder. Read once, then cached."""
        if name not in self._wisdom:
            path = self.config.manifestos_dir / name / "wisdom.json"
            wisdom = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(wisdom, list) or not wisdom or not all(isinstance(w, str) for w in wisdom):
                raise ValueError(f"{path} should be a non-empty list of strings")
            self._wisdom[name] = wisdom
        return self._wisdom[name]

    def pick(self, guild_id: int | None, user_id: int | None = None, index: int | None = None) -> Wisdom:
        """
        A piece of wisdom. With no user, a random author who has at least `index` pieces.
        Raises NoManifesto or NoSuchWisdom.
        """
        authors = self.authors(guild_id)
        if user_id is not None:
            if user_id not in authors:
                raise NoManifesto()
            candidates = [user_id]
        else:
            candidates = list(authors)
            if not candidates:
                raise NoManifesto()
            if index is not None:
                # Only authors deep enough to have that many thoughts
                candidates = [a for a in candidates if len(self.wisdom(authors[a])) >= index] or candidates

        author_id = random.choice(candidates)
        wisdom = self.wisdom(authors[author_id])
        if index is None:
            index = random.randint(1, len(wisdom))
        elif not 1 <= index <= len(wisdom):
            raise NoSuchWisdom(max(len(self.wisdom(authors[a])) for a in candidates))
        return Wisdom(author_id=author_id, index=index, total=len(wisdom), text=wisdom[index - 1])
