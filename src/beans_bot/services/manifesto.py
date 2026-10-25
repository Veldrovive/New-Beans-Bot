"""
Manifestos: members write one, and the bot quotes the wisdom in it.

- A manifesto lives in manifestos/<name>/ next to a wisdom.json, which is a list of the
  opinions pulled out of it. The raw manifesto can be personal, so the bot never reads it.
- Each guild's config maps a member's user ID to their manifesto folder.
- Wisdom is numbered from 1. Leave out the member or the number and one is picked at random:
  first a manifesto, each equally likely, then a line within it.
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


@dataclass
class Manifesto:
    author_id: int
    wisdom: list[str]


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

    def manifestos(self, guild_id: int | None, user_id: int | None = None) -> list[Manifesto]:
        """One member's manifesto, or every one in the guild (once each, even if members share one). Raises NoManifesto."""
        authors = self.authors(guild_id)
        if user_id is not None:
            if user_id not in authors:
                raise NoManifesto()
            return [Manifesto(author_id=user_id, wisdom=self.wisdom(authors[user_id]))]
        first_author = {name: author_id for author_id, name in reversed(authors.items())}
        if not first_author:
            raise NoManifesto()
        return [Manifesto(author_id=a, wisdom=self.wisdom(name)) for name, a in sorted(first_author.items())]

    def pick(self, guild_id: int | None, user_id: int | None = None, index: int | None = None) -> Wisdom:
        """
        A piece of wisdom. With no user, from a random manifesto that has at least `index` pieces.
        Raises NoManifesto or NoSuchWisdom.
        """
        authors = self.authors(guild_id)
        if user_id is not None:
            if user_id not in authors:
                raise NoManifesto()
            names = [authors[user_id]]
        else:
            # Even odds per manifesto, then per line within it. Picking evenly across all lines
            # would let one long manifesto drown out the short ones.
            names = sorted(set(authors.values()))
            if not names:
                raise NoManifesto()
            if index is not None:
                # Only manifestos deep enough to have that many thoughts
                names = [n for n in names if len(self.wisdom(n)) >= index] or names

        name = random.choice(names)
        author_id = user_id if user_id is not None else random.choice([a for a, n in authors.items() if n == name])
        wisdom = self.wisdom(name)
        if index is None:
            index = random.randint(1, len(wisdom))
        elif not 1 <= index <= len(wisdom):
            raise NoSuchWisdom(max(len(self.wisdom(n)) for n in names))
        return Wisdom(author_id=author_id, index=index, total=len(wisdom), text=wisdom[index - 1])
