"""
Manifestos: members write one, and the bot quotes the wisdom in it.

- A manifesto lives in manifestos/<name>/ next to a wisdom.json, which is a list of the
  opinions pulled out of it. The raw manifesto can be personal, so the bot never reads it.
- Each guild's config maps a member's user ID to their manifesto folder.
- Any member can add wisdom to their own manifesto with /manifesto add, even without a folder.
  Added wisdom lives in the database and comes after the wisdom.json lines. Members can edit or
  remove what they added, but the wisdom.json lines are written in stone.
- Wisdom is numbered from 1. Leave out the member or the number and one is picked at random:
  first a manifesto, each equally likely, then a line within it.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import random
from dataclasses import dataclass, field

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.config.schema import BotConfig
from beans_bot.db.engine import Database
from beans_bot.db.models import ManifestoWisdom
from beans_bot.db.types import utcnow

logger = logging.getLogger(__name__)


class NoManifesto(Exception):
    """The member hasn't written a manifesto, or the guild has none at all."""


class NoSuchWisdom(Exception):
    def __init__(self, most: int):
        super().__init__(f"There are only {most} pieces of wisdom")
        self.most = most


class WrittenInStone(Exception):
    """That wisdom came from the manifesto file, so it can't be edited from Discord."""


class NotYourWisdom(Exception):
    """Someone else sharing the manifesto added that wisdom."""


@dataclass
class Wisdom:
    author_id: int
    index: int  # From 1
    total: int
    text: str


@dataclass
class Line:
    text: str
    added_by: int | None = None  # Who added it with /manifesto add. None for wisdom.json lines.
    added_id: int | None = None  # Its ManifestoWisdom row


@dataclass
class Manifesto:
    author_ids: list[int]  # Members sharing it. The first is the one it's shown under.
    lines: list[Line] = field(default_factory=list)

    @property
    def author_id(self) -> int:
        return self.author_ids[0]

    @property
    def wisdom(self) -> list[str]:
        return [line.text for line in self.lines]

    def wisdom_at(self, index: int, author_id: int | None = None) -> Wisdom:
        line = self.lines[index - 1]
        if line.added_by is not None:
            author = line.added_by
        else:
            author = author_id if author_id is not None else random.choice(self.author_ids)
        return Wisdom(author_id=author, index=index, total=len(self.lines), text=line.text)


class ManifestoService:
    def __init__(self, config: BotConfig, db: Database):
        self.config = config
        self.db = db
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

    # --- Building blocks (run inside a caller's transaction) --------------------------

    async def _all(self, session: AsyncSession, guild_id: int | None) -> list[Manifesto]:
        """Every manifesto in the guild: folders by name, then members who only have added wisdom."""
        authors = self.authors(guild_id)
        by_key: dict[str | int, Manifesto] = {}
        for name in sorted(set(authors.values())):
            by_key[name] = Manifesto(author_ids=[a for a, n in authors.items() if n == name], lines=[Line(t) for t in self.wisdom(name)])
        rows = await session.exec(
            select(ManifestoWisdom)
            .where(ManifestoWisdom.guild_id == guild_id, col(ManifestoWisdom.removed_at).is_(None))
            .order_by(col(ManifestoWisdom.id))
        )
        for row in rows.all():
            # Added wisdom joins the member's folder manifesto if they have one, else their own
            manifesto = by_key.setdefault(authors.get(row.author_user_id, row.author_user_id), Manifesto(author_ids=[row.author_user_id]))
            manifesto.lines.append(Line(row.text, added_by=row.author_user_id, added_id=row.id))
        return list(by_key.values())

    async def _of(self, session: AsyncSession, guild_id: int | None, user_id: int) -> Manifesto:
        """The member's manifesto, shown under them. Raises NoManifesto."""
        for manifesto in await self._all(session, guild_id):
            if user_id in manifesto.author_ids:
                others = [a for a in manifesto.author_ids if a != user_id]
                return dataclasses.replace(manifesto, author_ids=[user_id, *others])
        raise NoManifesto()

    async def _own_row(self, session: AsyncSession, guild_id: int, user_id: int, index: int) -> ManifestoWisdom:
        """The row behind wisdom #index of the member's manifesto, if they added it."""
        manifesto = await self._of(session, guild_id, user_id)
        if not 1 <= index <= len(manifesto.lines):
            raise NoSuchWisdom(len(manifesto.lines))
        line = manifesto.lines[index - 1]
        if line.added_id is None:
            raise WrittenInStone()
        if line.added_by != user_id:
            raise NotYourWisdom()
        row = await session.get(ManifestoWisdom, line.added_id)
        assert row is not None
        return row

    # --- Reading ----------------------------------------------------------------------

    async def manifestos(self, guild_id: int | None, user_id: int | None = None) -> list[Manifesto]:
        """One member's manifesto, or every one in the guild (once each, even if members share one). Raises NoManifesto."""
        async with self.db.transaction() as session:
            if user_id is not None:
                return [await self._of(session, guild_id, user_id)]
            manifestos = await self._all(session, guild_id)
        if not manifestos:
            raise NoManifesto()
        return manifestos

    async def pick(self, guild_id: int | None, user_id: int | None = None, index: int | None = None) -> Wisdom:
        """
        A piece of wisdom. With no user, from a random manifesto that has at least `index` pieces.
        Raises NoManifesto or NoSuchWisdom.
        """
        candidates = await self.manifestos(guild_id, user_id)
        if index is not None:
            # Only manifestos deep enough to have that many thoughts. Picking evenly across manifestos,
            # not lines, stops one long manifesto drowning out the short ones.
            deep = [m for m in candidates if len(m.lines) >= index]
            if not deep:
                raise NoSuchWisdom(max(len(m.lines) for m in candidates))
            candidates = deep
        manifesto = random.choice(candidates)
        return manifesto.wisdom_at(index if index is not None else random.randint(1, len(manifesto.lines)), user_id)

    async def own_wisdom(self, guild_id: int, user_id: int) -> list[Wisdom]:
        """The wisdom the member added themselves, numbered as in their manifesto."""
        try:
            manifesto = (await self.manifestos(guild_id, user_id))[0]
        except NoManifesto:
            return []
        return [manifesto.wisdom_at(i, user_id) for i, line in enumerate(manifesto.lines, start=1) if line.added_by == user_id]

    # --- Writing ----------------------------------------------------------------------

    async def add(self, guild_id: int, user_id: int, text: str) -> Wisdom:
        """Add wisdom to the end of the member's manifesto, starting one if they have none."""
        async with self.db.transaction() as session:
            row = ManifestoWisdom(guild_id=guild_id, author_user_id=user_id, text=text)
            session.add(row)
            await session.flush()
            manifesto = await self._of(session, guild_id, user_id)
        index = next(i for i, line in enumerate(manifesto.lines, start=1) if line.added_id == row.id)
        logger.info(f"{user_id} added wisdom {index} (row {row.id}) to their manifesto in guild {guild_id}")
        return manifesto.wisdom_at(index, user_id)

    async def edit(self, guild_id: int, user_id: int, index: int, text: str) -> str:
        """Rewrite wisdom the member added. Returns the old text. Raises NoManifesto, NoSuchWisdom, WrittenInStone or NotYourWisdom."""
        async with self.db.transaction() as session:
            row = await self._own_row(session, guild_id, user_id, index)
            old = row.text
            row.text = text
            row.edited_at = utcnow()
            session.add(row)
        logger.info(f"{user_id} edited wisdom {index} (row {row.id}) of their manifesto in guild {guild_id}")
        return old

    async def remove(self, guild_id: int, user_id: int, index: int) -> str:
        """Take back wisdom the member added. Later wisdom moves up a number. Returns the removed text. Raises like edit."""
        async with self.db.transaction() as session:
            row = await self._own_row(session, guild_id, user_id, index)
            row.removed_at = utcnow()
            session.add(row)
        logger.info(f"{user_id} removed wisdom {index} (row {row.id}) from their manifesto in guild {guild_id}")
        return row.text
