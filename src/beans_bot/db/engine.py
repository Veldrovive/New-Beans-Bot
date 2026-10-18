"""
The single database every guild and every cog shares.

Each `Database.transaction()` is one SQLite transaction started with
BEGIN IMMEDIATE, so a read-check-write sequence (like checking a balance
before spending it) cannot interleave with another one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from beans_bot.db.migrations import upgrade_database


def _configure_sqlite(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection, connection_record):
        # Let us issue BEGIN ourselves instead of the driver's implicit deferred BEGIN.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


class Database:
    def __init__(self, path: Path | str, *, migrate: bool = True):
        self.path = Path(path)
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if migrate:
            upgrade_database(self.path)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.path}")
        _configure_sqlite(self.engine)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """A session inside one transaction. Commits on success, rolls back on error."""
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            async with session.begin():
                yield session

    async def close(self) -> None:
        await self.engine.dispose()
