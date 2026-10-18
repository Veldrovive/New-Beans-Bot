"""
Migrate the old bot's data into the new single database.

    uv run scripts/migrate_legacy_data.py --legacy-dir /path/to/old/bot [--out-dir ./data] [--dry-run]

`--legacy-dir` is the old bot's deployment folder: the one holding `db.sqlite3`
and the `data/` folder (pointing straight at the `data/` folder also works).
The new database is written to `<out-dir>/beans.db`.

What it reads:
- db.sqlite3: robrole polls and the classifier's tables
- data/{jail_cog,birthday_cog,action_holiday_cog,fatcow_cog}.db: the peewee stores
- data/<guild_id>/JailCog/*.json: the even older jail data (`*.migrated` files are skipped)

Safety:
- You must confirm you have a backup before anything happens.
- Legacy databases are copied to a temporary folder and read from there, so
  nothing in the legacy folder is changed (not even SQLite's -wal/-shm files).
- It refuses to overwrite an existing beans.db.
- Everything is written to beans.db.partial first, then checked. Only when every
  check passes is it renamed to beans.db. On any failure the partial file is deleted.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, create_engine, select

from beans_bot.currency import BASE_UNITS_PER_BEAN
from beans_bot.db.migrations import upgrade_database
from beans_bot.db.models import (
    ActionHolidayPost,
    BirthdayMessage,
    ClassifierProcessedMessage,
    ClassifierScriptLastUsed,
    CoinAccount,
    CoinTransaction,
    CoinTransactionKind,
    FatCowConsumption,
    FatCowVisit,
    JailSentence,
    JailUsedMessage,
    MarketBet,
    PendingRename,
    PredictionMarket,
    RoleNamePoll,
    TomatoThrow,
    UsedHoliday,
    UsedOnThisDay,
)

UTC = datetime.UTC
PEEWEE_STORES = ("jail_cog", "birthday_cog", "action_holiday_cog", "fatcow_cog")
LEGACY_JSON_FILES = (
    "currently_jailed_data.json",
    "historical_jailed_data.json",
    "used_messages.json",
    "tomato_counters.json",
    "tomato_history.json",
)
# Legacy tables that are deliberately not carried over.
SKIPPED_TABLES = {
    ("db.sqlite3", "state"): "only held the unused message_count key",
}


# --- Value conversions ------------------------------------------------------------------


def from_ms(value: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(value / 1000, UTC)


def from_seconds(value: float) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(value, UTC)


def from_timestamp_text(value: str) -> datetime.datetime:
    """SQLite TIMESTAMP text. The old bot ran in Docker, where local time was UTC."""
    return datetime.datetime.fromisoformat(value).replace(tzinfo=UTC)


def from_date_text(value: str) -> datetime.date:
    return datetime.date.fromisoformat(value[:10])


def coins(count: int) -> int:
    return count * BASE_UNITS_PER_BEAN


# --- Reading legacy data ----------------------------------------------------------------


class LegacyDB:
    """
    A legacy SQLite file, read from a private copy. Even a read-only open of a WAL-mode
    database creates -shm/-wal files next to it, so we never open the original.
    """

    def __init__(self, path: Path, snapshot_dir: Path):
        self.path = path
        self.name = path.name
        copy = snapshot_dir / f"{len(list(snapshot_dir.iterdir()))}-{path.name}"
        shutil.copy2(path, copy)
        for suffix in ("-wal", "-shm"):
            side_file = path.with_name(path.name + suffix)
            if side_file.exists():
                shutil.copy2(side_file, copy.with_name(copy.name + suffix))
        self.conn = sqlite3.connect(copy)
        self.conn.row_factory = sqlite3.Row

    def tables(self) -> set[str]:
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        return {r[0] for r in rows}

    def rows(self, table: str) -> list[sqlite3.Row]:
        if table not in self.tables():
            return []
        return list(self.conn.execute(f'SELECT * FROM "{table}"'))

    def close(self) -> None:
        self.conn.close()


@dataclass
class Sources:
    db_file: Path | None
    data_dir: Path | None
    peewee: dict[str, Path] = field(default_factory=dict)
    json_dirs: dict[int, Path] = field(default_factory=dict)  # guild_id -> data/<guild_id>/JailCog
    missing: list[str] = field(default_factory=list)


def discover(legacy_dir: Path, db_file: Path | None, data_dir: Path | None) -> Sources:
    if data_dir is None:
        # Accept either the deployment folder or the data folder itself.
        data_dir = legacy_dir if (legacy_dir / "jail_cog.db").exists() else legacy_dir / "data"
    if db_file is None:
        candidates = [legacy_dir / "db.sqlite3", data_dir.parent / "db.sqlite3"]
        db_file = next((c for c in candidates if c.exists()), candidates[0])

    sources = Sources(db_file=db_file if db_file.exists() else None, data_dir=data_dir if data_dir.is_dir() else None)
    if sources.db_file is None:
        sources.missing.append(str(db_file))
    if sources.data_dir is None:
        sources.missing.append(str(data_dir))
        return sources

    for store in PEEWEE_STORES:
        path = data_dir / f"{store}.db"
        if path.exists():
            sources.peewee[store] = path
        else:
            sources.missing.append(str(path))
    for guild_dir in sorted(data_dir.iterdir()):
        jail_dir = guild_dir / "JailCog"
        if guild_dir.is_dir() and guild_dir.name.isdigit() and jail_dir.is_dir():
            if any((jail_dir / name).exists() for name in LEGACY_JSON_FILES):
                sources.json_dirs[int(guild_dir.name)] = jail_dir
    return sources


# --- Report -----------------------------------------------------------------------------


@dataclass
class Report:
    rows: list[tuple[str, str, int, int]] = field(default_factory=list)  # source, target table, read, written
    notes: list[str] = field(default_factory=list)
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def moved(self, source: str, target: type[SQLModel], read: int, written: int) -> None:
        self.rows.append((source, target.__tablename__, read, written))  # type: ignore[attr-defined]

    def note(self, text: str) -> None:
        self.notes.append(text)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def print(self) -> None:
        print("\n=== Rows migrated ===")
        width = max([len(r[0]) for r in self.rows] + [6])
        print(f"{'Source':<{width}}  {'New table':<30} {'Read':>6} {'Written':>8}")
        for source, target, read, written in self.rows:
            print(f"{source:<{width}}  {target:<30} {read:>6} {written:>8}")
        if self.notes:
            print("\n=== Notes ===")
            for text in self.notes:
                print(f"- {text}")
        print("\n=== Checks ===")
        for name, ok, detail in self.checks:
            print(f"[{'PASS' if ok else 'FAIL'}] {name}{f': {detail}' if detail else ''}")


# --- Migrators --------------------------------------------------------------------------


class Migration:
    def __init__(self, session: Session, sources: Sources, report: Report, snapshot_dir: Path):
        self.session = session
        self.sources = sources
        self.report = report
        self.peewee = {name: LegacyDB(path, snapshot_dir) for name, path in sources.peewee.items()}
        self.state_db = LegacyDB(sources.db_file, snapshot_dir) if sources.db_file else None
        # Table name -> guild IDs with rows, so legacy JSON for a guild already in peewee isn't imported twice.
        jail = self.peewee.get("jail_cog")
        self.jail_guilds: dict[str, set[int]] = defaultdict(set)
        if jail is not None:
            for table in jail.tables():
                if "server_id" in self._columns(jail, table):
                    for row in jail.conn.execute(f'SELECT DISTINCT server_id FROM "{table}"'):
                        self.jail_guilds[table].add(row[0])
        # Expected per-account balances for the coin check.
        self.legacy_balances: dict[tuple[int, int], int] = {}

    @staticmethod
    def _columns(db: LegacyDB, table: str) -> set[str]:
        return {r[1] for r in db.conn.execute(f'PRAGMA table_info("{table}")')}

    def close(self) -> None:
        for db in self.peewee.values():
            db.close()
        if self.state_db is not None:
            self.state_db.close()

    def run(self) -> None:
        self.check_for_unknown_tables()
        self.migrate_jail()
        self.migrate_coins()
        self.migrate_markets()
        self.migrate_birthdays()
        self.migrate_action_holidays()
        self.migrate_fat_cow()
        self.migrate_role_name_polls()
        self.migrate_classifier()
        self.session.flush()

    def check_for_unknown_tables(self) -> None:
        known = {
            "jail_cog.db": {"jaileduser", "historicaljaileduser", "pendingrename", "tomatocounter", "tomatohistory",
                            "usedmessage", "beancoincounter", "beancoinhistory", "predictionmarket", "marketbet"},
            "birthday_cog.db": {"sentbirthdaymessage", "usedholidays", "usedonthisday"},
            "action_holiday_cog.db": {"sentactionholiday"},
            "fatcow_cog.db": {"fatcowvisitentry", "fatcowuservisitentry"},
            "db.sqlite3": {"state", "role_change_messages", "classifier_v2_processed_messages",
                           "classifier_v2_script_last_used_time"},
        }  # fmt: skip
        dbs = list(self.peewee.values()) + ([self.state_db] if self.state_db else [])
        for db in dbs:
            for table in sorted(db.tables() - known.get(db.name, set())):
                self.report.note(f"{db.name}: table '{table}' is not migrated (unknown table)")
        for (db_name, table), reason in SKIPPED_TABLES.items():
            self.report.note(f"{db_name}: table '{table}' is not migrated ({reason})")

    def _json(self, guild_id: int, name: str):
        path = self.sources.json_dirs[guild_id] / name
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def _use_json(self, guild_id: int, name: str, peewee_table: str) -> bool:
        """Legacy JSON is imported only if the peewee store has nothing for that guild in the matching table."""
        if guild_id not in self.sources.json_dirs or not (self.sources.json_dirs[guild_id] / name).exists():
            return False
        if guild_id in self.jail_guilds[peewee_table]:
            self.report.note(
                f"Skipped {guild_id}/JailCog/{name}: jail_cog.db already has {peewee_table} rows for that guild"
            )
            return False
        return True

    # Jail -------------------------------------------------------------------------------

    def migrate_jail(self) -> None:
        jail = self.peewee.get("jail_cog")
        rows = lambda table: jail.rows(table) if jail else []  # noqa: E731

        def sentence(guild_id, user_id, channel_id, message_id, start, end, humiliated, released: bool) -> JailSentence:
            ends_at = from_ms(end)
            return JailSentence(
                guild_id=guild_id,
                user_id=user_id,
                channel_id=channel_id,
                offending_message_id=message_id or None,
                started_at=from_ms(start),
                ends_at=ends_at,
                released_at=ends_at if released else None,
                release_reason="migrated" if released else None,
                humiliated=bool(humiliated),
            )

        for table, released in (("jaileduser", False), ("historicaljaileduser", True)):
            legacy = rows(table)
            for r in legacy:
                self.session.add(
                    sentence(r["server_id"], r["user_id"], r["channel_id"], r["offending_message_id"],
                             r["start_time"], r["end_time"], r["has_been_humiliated"], released)
                )  # fmt: skip
            self.report.moved(f"jail_cog.db:{table}", JailSentence, len(legacy), len(legacy))

        for guild_id in self.sources.json_dirs:
            if self._use_json(guild_id, "currently_jailed_data.json", "jaileduser"):
                data = self._json(guild_id, "currently_jailed_data.json") or {}
                for user_id, d in data.items():
                    self.session.add(
                        sentence(guild_id, int(user_id), d["channelId"], d["offendingMessageId"],
                                 d["startTime"], d["endTime"], d["hasBeenHumiliated"], False)
                    )  # fmt: skip
                self.report.moved(f"{guild_id}/currently_jailed_data.json", JailSentence, len(data), len(data))
            if self._use_json(guild_id, "historical_jailed_data.json", "historicaljaileduser"):
                data = self._json(guild_id, "historical_jailed_data.json") or []
                for d in data:
                    self.session.add(
                        sentence(guild_id, d["userId"], d["channelId"], d["offendingMessageId"],
                                 d["startTime"], d["endTime"], d["hasBeenHumiliated"], True)
                    )  # fmt: skip
                self.report.moved(f"{guild_id}/historical_jailed_data.json", JailSentence, len(data), len(data))

        # Messages that already jailed someone.
        used: set[tuple[int, int]] = set()
        legacy = rows("usedmessage")
        used.update((r["server_id"], r["message_id"]) for r in legacy)
        read = len(legacy)
        for guild_id in self.sources.json_dirs:
            if self._use_json(guild_id, "used_messages.json", "usedmessage"):
                ids = self._json(guild_id, "used_messages.json") or []
                used.update((guild_id, int(m)) for m in ids)
                read += len(ids)
        for guild_id, message_id in sorted(used):
            self.session.add(JailUsedMessage(guild_id=guild_id, message_id=message_id))
        self.report.moved("jail_cog.db:usedmessage (+json)", JailUsedMessage, read, len(used))

        # Mega jail renames.
        seen: set[tuple[int, int]] = set()
        legacy = rows("pendingrename")
        for r in legacy:
            key = (r["server_id"], r["offending_message_id"])
            if key in seen:
                continue
            seen.add(key)
            self.session.add(
                PendingRename(guild_id=r["server_id"], prompt_message_id=r["prompt_message_id"],
                              target_user_id=r["target_user_id"], offending_message_id=r["offending_message_id"],
                              used=bool(r["is_used"]))
            )  # fmt: skip
        if len(seen) != len(legacy):
            self.report.note(f"Dropped {len(legacy) - len(seen)} duplicate pending renames for the same message")
        self.report.moved("jail_cog.db:pendingrename", PendingRename, len(legacy), len(seen))

        # Tomatoes: every recorded throw, then filler throws where the old counter is higher than the history.
        throws_per_target: Counter[tuple[int, int]] = Counter()

        def add_throw(guild_id, thrower, target, message_id, timestamp_ms, innocent) -> None:
            self.session.add(
                TomatoThrow(guild_id=guild_id, thrower_user_id=thrower, target_user_id=target, message_id=message_id,
                            thrown_at=from_ms(timestamp_ms), innocent=bool(innocent))
            )  # fmt: skip
            throws_per_target[(guild_id, target)] += 1

        legacy = rows("tomatohistory")
        for r in legacy:
            add_throw(r["server_id"], r["thrower_user_id"], r["attacked_user_id"], r["message_id"], r["timestamp"], r["is_innocent"])
        self.report.moved("jail_cog.db:tomatohistory", TomatoThrow, len(legacy), len(legacy))
        for guild_id in self.sources.json_dirs:
            if self._use_json(guild_id, "tomato_history.json", "tomatohistory"):
                entries = [e for e in (self._json(guild_id, "tomato_history.json") or []) if len(e) == 5]
                for thrower, target, message_id, timestamp, innocent in entries:
                    add_throw(guild_id, thrower, target, message_id, timestamp, innocent)
                self.report.moved(f"{guild_id}/tomato_history.json", TomatoThrow, len(entries), len(entries))

        counters: dict[tuple[int, int], int] = {(r["server_id"], r["user_id"]): r["count"] for r in rows("tomatocounter")}
        for guild_id in self.sources.json_dirs:
            if self._use_json(guild_id, "tomato_counters.json", "tomatocounter"):
                for user_id, count in (self._json(guild_id, "tomato_counters.json") or {}).items():
                    counters[(guild_id, int(user_id))] = counters.get((guild_id, int(user_id)), 0) + count
        filler = 0
        for (guild_id, user_id), count in sorted(counters.items()):
            history = throws_per_target[(guild_id, user_id)]
            if count > history:
                for _ in range(count - history):
                    self.session.add(TomatoThrow(guild_id=guild_id, target_user_id=user_id, thrown_at=None, innocent=False))
                filler += count - history
            elif count < history:
                self.report.note(
                    f"Tomato counter for {user_id} in guild {guild_id} was {count} but history has {history} throws; "
                    f"/jail info will now show {history}"
                )
        if filler:
            self.report.note(f"Added {filler} undated tomato throws so tomato totals match the old counters")
        self.report.moved("jail_cog.db:tomatocounter (+json)", TomatoThrow, len(counters), filler)

    # Coins ------------------------------------------------------------------------------

    def migrate_coins(self) -> None:
        jail = self.peewee.get("jail_cog")
        if jail is None:
            return
        counters = jail.rows("beancoincounter")
        for r in counters:
            self.legacy_balances[(r["server_id"], r["user_id"])] = coins(r["count"])
            self.session.add(CoinAccount(guild_id=r["server_id"], user_id=r["user_id"], balance=coins(r["count"])))
        self.report.moved("jail_cog.db:beancoincounter", CoinAccount, len(counters), len(counters))

        reaction_totals: Counter[tuple[int, int]] = Counter()
        history = jail.rows("beancoinhistory")
        for r in history:
            self.session.add(
                CoinTransaction(guild_id=r["server_id"], kind=CoinTransactionKind.REACTION, amount=coins(1),
                                to_user_id=r["receiver_user_id"], actor_user_id=r["giver_user_id"],
                                message_id=r["message_id"], created_at=from_ms(r["timestamp"]))
            )  # fmt: skip
            reaction_totals[(r["server_id"], r["receiver_user_id"])] += coins(1)
        self.report.moved("jail_cog.db:beancoinhistory", CoinTransaction, len(history), len(history))

        # Bets, bribes and payouts were never logged, so one opening entry per account makes
        # its ledger add up to the balance it had in the old bot.
        openings = 0
        for key in sorted(set(self.legacy_balances) | set(reaction_totals)):
            guild_id, user_id = key
            if key not in self.legacy_balances:
                self.legacy_balances[key] = 0
                self.session.add(CoinAccount(guild_id=guild_id, user_id=user_id, balance=0))
                self.report.note(f"{user_id} in guild {guild_id} received coins but had no balance row; balance set to 0")
            difference = self.legacy_balances[key] - reaction_totals[key]
            if difference == 0:
                continue
            self.session.add(
                CoinTransaction(
                    guild_id=guild_id,
                    kind=CoinTransactionKind.MIGRATION_OPENING,
                    amount=abs(difference),
                    to_user_id=user_id if difference > 0 else None,
                    from_user_id=user_id if difference < 0 else None,
                )
            )
            openings += 1
        self.report.moved("(balance adjustments)", CoinTransaction, 0, openings)

    # Markets ----------------------------------------------------------------------------

    def migrate_markets(self) -> None:
        jail = self.peewee.get("jail_cog")
        if jail is None:
            return
        markets = jail.rows("predictionmarket")
        market_ids = set()
        for r in markets:
            market_ids.add(r["thread_id"])
            self.session.add(
                PredictionMarket(thread_id=r["thread_id"], guild_id=r["server_id"], creator_id=r["creator_id"],
                                 status=r["status"], choices=json.loads(r["choices"]), created_at=from_ms(r["created_at"]),
                                 resolved_at=from_ms(r["resolved_at"]) if r["resolved_at"] is not None else None,
                                 winning_choice=r["winning_choice"])
            )  # fmt: skip
        self.report.moved("jail_cog.db:predictionmarket", PredictionMarket, len(markets), len(markets))
        # Bets need their market row to exist before the foreign key is checked.
        self.session.flush()

        bets = jail.rows("marketbet")
        written = 0
        for r in bets:
            if r["market_id"] not in market_ids:
                self.report.note(f"Skipped a bet by {r['user_id']} on missing market {r['market_id']}")
                continue
            self.session.add(
                MarketBet(market_thread_id=r["market_id"], user_id=r["user_id"], choice=r["choice"],
                          amount=coins(r["amount"]), placed_at=from_ms(r["timestamp"]))
            )  # fmt: skip
            written += 1
        self.report.moved("jail_cog.db:marketbet", MarketBet, len(bets), written)

    # Daily posts, fat cow, robrole, classifier ------------------------------------------

    def migrate_birthdays(self) -> None:
        db = self.peewee.get("birthday_cog")
        if db is None:
            return
        legacy = db.rows("sentbirthdaymessage")
        for r in legacy:
            self.session.add(
                BirthdayMessage(guild_id=r["server_id"], sent_on=from_date_text(r["sent_date"]), channel_id=r["channel_id"],
                                message_id=r["message_id"], person_name=r["user_name"], for_year=r["for_year"],
                                used_holiday=r["used_holiday"], used_on_this_day=r["used_on_this_day"])
            )  # fmt: skip
        self.report.moved("birthday_cog.db:sentbirthdaymessage", BirthdayMessage, len(legacy), len(legacy))

        legacy = db.rows("usedholidays")
        for r in legacy:
            self.session.add(
                UsedHoliday(guild_id=r["server_id"], used_on=from_date_text(r["sent_date"]), holiday_name=r["holiday_name"],
                            holiday_link=r["holiday_link"], day=r["day"], month=r["month"])
            )  # fmt: skip
        self.report.moved("birthday_cog.db:usedholidays", UsedHoliday, len(legacy), len(legacy))

        legacy = db.rows("usedonthisday")
        for r in legacy:
            self.session.add(
                UsedOnThisDay(guild_id=r["server_id"], used_on=from_date_text(r["sent_date"]), text=r["on_this_day_text"],
                              day=r["day"], month=r["month"], year=r["year"])
            )  # fmt: skip
        self.report.moved("birthday_cog.db:usedonthisday", UsedOnThisDay, len(legacy), len(legacy))

    def migrate_action_holidays(self) -> None:
        db = self.peewee.get("action_holiday_cog")
        if db is None:
            return
        legacy = db.rows("sentactionholiday")
        for r in legacy:
            sent_on = from_date_text(r["sent_date"])
            if r["year_used"] != sent_on.year:
                self.report.note(f"Action holiday '{r['holiday_name']}' had year_used {r['year_used']} but was sent {sent_on}")
            self.session.add(
                ActionHolidayPost(guild_id=r["server_id"], sent_on=sent_on, holiday_name=r["holiday_name"],
                                  holiday_link=r["holiday_link"])
            )  # fmt: skip
        self.report.moved("action_holiday_cog.db:sentactionholiday", ActionHolidayPost, len(legacy), len(legacy))

    def migrate_fat_cow(self) -> None:
        db = self.peewee.get("fatcow_cog")
        if db is None:
            return
        legacy = db.rows("fatcowvisitentry")
        for r in legacy:
            self.session.add(FatCowVisit(guild_id=r["server_id"], visited_at=from_seconds(r["timestamp"])))
        self.report.moved("fatcow_cog.db:fatcowvisitentry", FatCowVisit, len(legacy), len(legacy))

        legacy = db.rows("fatcowuservisitentry")
        for r in legacy:
            self.session.add(
                FatCowConsumption(guild_id=r["server_id"], user_id=r["user_id"], meters=r["amount"],
                                  recorded_at=from_seconds(r["timestamp"]))
            )  # fmt: skip
        self.report.moved("fatcow_cog.db:fatcowuservisitentry", FatCowConsumption, len(legacy), len(legacy))

    def migrate_role_name_polls(self) -> None:
        if self.state_db is None:
            return
        legacy = self.state_db.rows("role_change_messages")
        for r in legacy:
            self.session.add(
                RoleNamePoll(poll_message_id=r["change_poll_message_id"], guild_id=r["guild_id"], channel_id=r["channel_id"],
                             initiator_user_id=r["change_initiator_user_id"], adjective=r["new_adjective"],
                             created_at=from_timestamp_text(r["added_at"]), active=bool(r["active"]))
            )  # fmt: skip
        self.report.moved("db.sqlite3:role_change_messages", RoleNamePoll, len(legacy), len(legacy))

    def migrate_classifier(self) -> None:
        if self.state_db is None:
            return
        legacy = self.state_db.rows("classifier_v2_processed_messages")
        for r in legacy:
            self.session.add(
                ClassifierProcessedMessage(guild_id=r["guild_id"], message_id=r["message_id"],
                                           classifier_name=r["classifier_name"], channel_id=r["channel_id"],
                                           processed_at=from_timestamp_text(r["processed_at"]))
            )  # fmt: skip
        self.report.moved("db.sqlite3:classifier_v2_processed_messages", ClassifierProcessedMessage, len(legacy), len(legacy))

        legacy = self.state_db.rows("classifier_v2_script_last_used_time")
        for r in legacy:
            self.session.add(
                ClassifierScriptLastUsed(guild_id=r["guild_id"], classifier_name=r["classifier_name"],
                                         script_text=r["script_text"], last_used_at=from_timestamp_text(r["last_used_at"]))
            )  # fmt: skip
        self.report.moved("db.sqlite3:classifier_v2_script_last_used_time", ClassifierScriptLastUsed, len(legacy), len(legacy))


# --- Verification -----------------------------------------------------------------------


def verify(session: Session, migration: Migration, report: Report) -> None:
    # Every row the migrators wrote is in its table.
    written: Counter[str] = Counter()
    for _, table, _, count in report.rows:
        written[table] += count
    models = {m.__tablename__: m for m in SQLModel.__subclasses__() if hasattr(m, "__table__")}  # type: ignore[attr-defined]
    for table, expected in sorted(written.items()):
        actual = session.exec(select(func.count()).select_from(models[table])).one()
        report.check(f"{table} row count", actual == expected, f"expected {expected}, found {actual}")

    # Balances match the old bot exactly (in base units), and every account's ledger adds up to its balance.
    accounts = {(a.guild_id, a.user_id): a.balance for a in session.exec(select(CoinAccount)).all()}
    mismatched = [k for k, v in migration.legacy_balances.items() if accounts.get(k) != v]
    report.check(
        "Coin balances match the old bot",
        not mismatched and len(accounts) == len(migration.legacy_balances),
        f"{len(accounts)} accounts" + (f"; mismatched: {mismatched}" if mismatched else ""),
    )
    ledger: Counter[tuple[int, int]] = Counter()
    for t in session.exec(select(CoinTransaction)).all():
        if t.to_user_id is not None:
            ledger[(t.guild_id, t.to_user_id)] += t.amount
        if t.from_user_id is not None:
            ledger[(t.guild_id, t.from_user_id)] -= t.amount
    unbalanced = [k for k, balance in accounts.items() if ledger[k] != balance]
    report.check("Every account's ledger adds up to its balance", not unbalanced, f"unbalanced: {unbalanced}" if unbalanced else "")

    jail = migration.peewee.get("jail_cog")
    if jail is not None:
        legacy_totals: Counter[int] = Counter()
        for r in jail.rows("marketbet"):
            legacy_totals[r["market_id"]] += coins(r["amount"])
        new_totals: Counter[int] = Counter()
        for bet in session.exec(select(MarketBet)).all():
            new_totals[bet.market_thread_id] += bet.amount
        report.check("Market bet totals match", legacy_totals == new_totals, f"{len(new_totals)} markets with bets")

    # Summaries worth eyeballing.
    for guild_id in sorted({g for g, _ in accounts}):
        balances = [v for (g, _), v in accounts.items() if g == guild_id]
        report.note(f"Guild {guild_id}: {len(balances)} coin accounts, {sum(balances) / BASE_UNITS_PER_BEAN:g} BeanCoins in total")
    pools: Counter[int] = Counter()
    for bet in session.exec(select(MarketBet)).all():
        pools[bet.market_thread_id] += bet.amount
    for market in session.exec(select(PredictionMarket).where(col(PredictionMarket.status).in_(["open", "closed"]))).all():
        pool = pools[market.thread_id]
        report.note(f"Unresolved market {market.thread_id} ({market.status}): pool {pool / BASE_UNITS_PER_BEAN:g} BeanCoins")
    for s in session.exec(select(JailSentence).where(JailSentence.released_at == None)).all():  # noqa: E711
        report.note(f"Currently jailed: {s.user_id} in guild {s.guild_id} until {s.ends_at:%Y-%m-%d %H:%M} UTC")


# --- Main -------------------------------------------------------------------------------


def migrate(sources: Sources, db_path: Path) -> Report:
    upgrade_database(db_path)
    engine = create_engine(f"sqlite:///{db_path}")
    report = Report()
    try:
        with Session(engine) as session, tempfile.TemporaryDirectory() as snapshot_dir:
            migration = Migration(session, sources, report, Path(snapshot_dir))
            try:
                migration.run()
                session.commit()
                verify(session, migration, report)
            finally:
                migration.close()
    finally:
        engine.dispose()
    return report


def confirm_backup(sources: Sources) -> bool:
    print("\nThis will read the following legacy data:")
    if sources.db_file:
        print(f"  {sources.db_file}")
    for path in sources.peewee.values():
        print(f"  {path}")
    for guild_id, path in sources.json_dirs.items():
        print(f"  {path}/*.json")
    print("\nThe legacy files are only ever copied, never opened in place, but make a backup anyway,")
    print("for example: cp -r <legacy-dir> <legacy-dir>.backup")
    answer = input("\nHave you made a backup of this data? Type 'yes' to continue: ")
    return answer.strip().lower() == "yes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy-dir", type=Path, required=True, help="The old bot's folder (with db.sqlite3 and data/)")
    parser.add_argument("--legacy-db-file", type=Path, help="Override the path to the old db.sqlite3")
    parser.add_argument("--legacy-data-dir", type=Path, help="Override the path to the old data/ folder")
    parser.add_argument("--out-dir", type=Path, default=Path("data"), help="Where to create beans.db (default: ./data)")
    parser.add_argument("--dry-run", action="store_true", help="Migrate into a temporary file and print the report only")
    args = parser.parse_args()

    if not args.legacy_dir.is_dir():
        print(f"Legacy folder {args.legacy_dir} does not exist.", file=sys.stderr)
        return 1
    sources = discover(args.legacy_dir, args.legacy_db_file, args.legacy_data_dir)
    if not sources.peewee and sources.db_file is None:
        print(f"No legacy data found under {args.legacy_dir}.", file=sys.stderr)
        return 1
    for path in sources.missing:
        print(f"Not found, skipping: {path}")
    legacy_dbs = [*sources.peewee.values(), *([sources.db_file] if sources.db_file else [])]
    if any(p.with_name(p.name + "-wal").exists() for p in legacy_dbs):
        print("Warning: a legacy database has a -wal file. Make sure the old bot is stopped before migrating.")

    out_path = args.out_dir / "beans.db"
    if not args.dry_run and out_path.exists():
        print(f"{out_path} already exists. Move it away first; this script never overwrites a database.", file=sys.stderr)
        return 1

    if not confirm_backup(sources):
        print("Aborted. Nothing was written.")
        return 1

    if args.dry_run:
        with tempfile.TemporaryDirectory() as tmp:
            report = migrate(sources, Path(tmp) / "beans.db")
            report.print()
        print(f"\nDry run {'passed' if report.ok else 'FAILED'}. Nothing was written.")
        return 0 if report.ok else 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_path.with_name(out_path.name + ".partial")
    for leftover in (partial, partial.with_name(partial.name + "-wal"), partial.with_name(partial.name + "-shm")):
        leftover.unlink(missing_ok=True)
    try:
        report = migrate(sources, partial)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    report.print()
    if not report.ok:
        partial.unlink(missing_ok=True)
        print("\nMigration FAILED its checks. Nothing was written.", file=sys.stderr)
        return 1
    partial.rename(out_path)
    print(f"\nMigration complete: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
