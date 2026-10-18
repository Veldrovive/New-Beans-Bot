"""Run the legacy migration against small databases built with the old bot's exact schemas."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from beans_bot.db.models import CoinAccount, JailSentence, MarketBet, PredictionMarket, TomatoThrow

SCRIPT = Path(__file__).parent.parent / "scripts" / "migrate_legacy_data.py"
spec = importlib.util.spec_from_file_location("migrate_legacy_data", SCRIPT)
migration = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = migration
spec.loader.exec_module(migration)

G1, G2 = 1001, 1002
BOT = 900

JAIL_SCHEMA = """
CREATE TABLE "historicaljaileduser" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "channel_id" INTEGER NOT NULL, "offending_message_id" INTEGER NOT NULL, "start_time" INTEGER NOT NULL, "end_time" INTEGER NOT NULL, "has_been_humiliated" INTEGER NOT NULL);
CREATE TABLE "jaileduser" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "channel_id" INTEGER NOT NULL, "offending_message_id" INTEGER NOT NULL, "start_time" INTEGER NOT NULL, "end_time" INTEGER NOT NULL, "has_been_humiliated" INTEGER NOT NULL);
CREATE TABLE "tomatocounter" ("server_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "count" INTEGER NOT NULL, PRIMARY KEY ("server_id", "user_id"));
CREATE TABLE "tomatohistory" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "thrower_user_id" INTEGER NOT NULL, "attacked_user_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL, "timestamp" INTEGER NOT NULL, "is_innocent" INTEGER NOT NULL);
CREATE TABLE "usedmessage" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL);
CREATE TABLE "pendingrename" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "prompt_message_id" INTEGER NOT NULL, "target_user_id" INTEGER NOT NULL, "offending_message_id" INTEGER NOT NULL, "is_used" INTEGER NOT NULL);
CREATE TABLE "beancoincounter" ("server_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "count" INTEGER NOT NULL, PRIMARY KEY ("server_id", "user_id"));
CREATE TABLE "beancoinhistory" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "giver_user_id" INTEGER NOT NULL, "receiver_user_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL, "timestamp" INTEGER NOT NULL);
CREATE TABLE "predictionmarket" ("thread_id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "creator_id" INTEGER NOT NULL, "status" VARCHAR(255) NOT NULL, "choices" TEXT NOT NULL, "created_at" INTEGER NOT NULL, "resolved_at" INTEGER, "winning_choice" VARCHAR(255));
CREATE TABLE "marketbet" ("market_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "choice" VARCHAR(255) NOT NULL, "amount" INTEGER NOT NULL, "timestamp" INTEGER NOT NULL, PRIMARY KEY ("market_id", "user_id"), FOREIGN KEY ("market_id") REFERENCES "predictionmarket" ("thread_id"));
"""
STATE_SCHEMA = """
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE role_change_messages (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, change_poll_message_id INTEGER PRIMARY KEY, original_message_id INTEGER NOT NULL, change_initiator_user_id INTEGER NOT NULL, new_adjective TEXT NOT NULL, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, active INTEGER DEFAULT 1);
CREATE TABLE classifier_v2_processed_messages (message_id INTEGER NOT NULL, classifier_name TEXT NOT NULL, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (message_id, classifier_name));
CREATE TABLE classifier_v2_script_last_used_time (script_text TEXT NOT NULL, guild_id INTEGER NOT NULL, classifier_name TEXT NOT NULL, last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (script_text, guild_id, classifier_name));
"""
T = 1_780_000_000_000  # ms


def make_db(path: Path, schema: str, inserts: list[tuple[str, tuple]]):
    conn = sqlite3.connect(path)
    conn.executescript(schema)
    for sql, params in inserts:
        conn.execute(sql, params)
    conn.commit()
    conn.close()


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "old_bot"
    data = root / "data"
    data.mkdir(parents=True)
    make_db(root / "db.sqlite3", STATE_SCHEMA, [
        ("INSERT INTO state VALUES ('message_count', '0')", ()),
        ("INSERT INTO role_change_messages VALUES (?, 5, 77, 76, 1, 'Shiny', '2026-04-23 19:23:55', 1)", (G1,)),
        ("INSERT INTO classifier_v2_processed_messages VALUES (88, 'cats', ?, 5, '2025-12-15 23:05:26')", (G1,)),
        ("INSERT INTO classifier_v2_script_last_used_time VALUES ('STINKY!', ?, 'cats', '2025-12-15 19:10:39.768774')", (G1,)),
    ])  # fmt: skip
    make_db(data / "jail_cog.db", JAIL_SCHEMA, [
        ("INSERT INTO jaileduser VALUES (1, ?, 1, 5, 10, ?, ?, 1)", (G1, T, T + 86_400_000)),
        ("INSERT INTO historicaljaileduser VALUES (1, ?, 2, 5, 11, ?, ?, 0)", (G1, T, T + 1000)),
        ("INSERT INTO usedmessage VALUES (1, ?, 10)", (G1,)),
        ("INSERT INTO usedmessage VALUES (2, ?, 10)", (G1,)),  # The old bot didn't enforce uniqueness
        ("INSERT INTO pendingrename VALUES (1, ?, 20, 1, 10, 1)", (G1,)),
        ("INSERT INTO tomatohistory VALUES (1, ?, 2, 1, 10, ?, 0)", (G1, T)),
        ("INSERT INTO tomatocounter VALUES (?, 1, 3)", (G1,)),
        ("INSERT INTO beancoincounter VALUES (?, 1, 4)", (G1,)),
        ("INSERT INTO beancoincounter VALUES (?, 2, 1)", (G1,)),
        ("INSERT INTO beancoincounter VALUES (?, ?, -7)", (G1, BOT)),
        ("INSERT INTO beancoinhistory VALUES (1, ?, 2, 1, 30, ?)", (G1, T)),
        ("INSERT INTO beancoinhistory VALUES (2, ?, 2, 1, 31, ?)", (G1, T)),
        ("INSERT INTO predictionmarket VALUES (40, ?, 1, 'open', '[\"yes\", \"no\"]', ?, NULL, NULL)", (G1, T)),
        ("INSERT INTO predictionmarket VALUES (41, ?, 1, 'resolved', '[\"a\", \"b\"]', ?, ?, 'a')", (G1, T, T + 5)),
        ("INSERT INTO marketbet VALUES (40, 1, 'yes', 2, ?)", (T,)),
        ("INSERT INTO marketbet VALUES (40, ?, 'no', 3, ?)", (BOT, T)),
    ])  # fmt: skip
    make_db(data / "birthday_cog.db", """
        CREATE TABLE "sentbirthdaymessage" ("id" INTEGER NOT NULL PRIMARY KEY, "sent_date" DATE NOT NULL, "server_id" INTEGER NOT NULL, "channel_id" INTEGER NOT NULL, "message_id" INTEGER NOT NULL, "user_name" VARCHAR(255) NOT NULL, "for_year" INTEGER NOT NULL, "used_holiday" VARCHAR(255), "used_on_this_day" VARCHAR(255));
        CREATE TABLE "usedholidays" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "sent_date" DATE NOT NULL, "holiday_name" VARCHAR(255) NOT NULL, "holiday_link" VARCHAR(255) NOT NULL, "day" INTEGER NOT NULL, "month" INTEGER NOT NULL);
        CREATE TABLE "usedonthisday" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "sent_date" DATE NOT NULL, "on_this_day_text" VARCHAR(255) NOT NULL, "day" INTEGER NOT NULL, "month" INTEGER NOT NULL, "year" INTEGER NOT NULL);
    """, [
        ("INSERT INTO sentbirthdaymessage VALUES (1, '2026-04-08', ?, 5, 50, 'Harvey', 2026, 'Some Day', NULL)", (G1,)),
        ("INSERT INTO usedholidays VALUES (1, ?, '2026-04-08', 'Some Day', 'https://x', 8, 4)", (G1,)),
        ("INSERT INTO usedonthisday VALUES (1, ?, '2026-04-08', 'Something happened', 8, 4, 2003)", (G1,)),
    ])  # fmt: skip
    make_db(data / "action_holiday_cog.db", """
        CREATE TABLE "sentactionholiday" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "sent_date" DATE NOT NULL, "holiday_name" VARCHAR(255) NOT NULL, "holiday_link" VARCHAR(255) NOT NULL, "year_used" INTEGER NOT NULL);
    """, [("INSERT INTO sentactionholiday VALUES (1, ?, '2026-04-09', 'Adopt a Ferret Month', 'https://x', 2026)", (G1,))])
    make_db(data / "fatcow_cog.db", """
        CREATE TABLE "fatcowuservisitentry" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "user_id" INTEGER NOT NULL, "amount" REAL NOT NULL, "timestamp" INTEGER NOT NULL);
        CREATE TABLE "fatcowvisitentry" ("id" INTEGER NOT NULL PRIMARY KEY, "server_id" INTEGER NOT NULL, "timestamp" INTEGER NOT NULL);
    """, [
        ("INSERT INTO fatcowvisitentry VALUES (1, ?, 1777258123)", (G1,)),
        ("INSERT INTO fatcowuservisitentry VALUES (1, ?, 1, 1.5, 1777258137)", (G1,)),
    ])  # fmt: skip

    # G2 only has the even older JSON data. G1 also has JSON, which must be ignored.
    for guild_id, user_id in ((G1, 1), (G2, 3)):
        jail_dir = data / str(guild_id) / "JailCog"
        jail_dir.mkdir(parents=True)
        (jail_dir / "currently_jailed_data.json").write_text(json.dumps({
            str(user_id): {"channelId": 5, "offendingMessageId": 60, "startTime": T, "endTime": T + 1000, "hasBeenHumiliated": False}
        }))  # fmt: skip
        (jail_dir / "tomato_history.json").write_text(json.dumps([[4, user_id, 61, T, True]]))
        (jail_dir / "used_messages.json").write_text(json.dumps([60]))
        (jail_dir / "tomato_counters.json.migrated").write_text(json.dumps({str(user_id): 100}))
    return root


def run(legacy: Path, out: Path):
    sources = migration.discover(legacy, None, None)
    report = migration.migrate(sources, out)
    return report, Session(create_engine(f"sqlite:///{out}"))


def test_migration_passes_checks(legacy, tmp_path):
    report, session = run(legacy, tmp_path / "beans.db")
    report.print()
    assert report.ok

    balances = {(a.guild_id, a.user_id): a.balance for a in session.exec(select(CoinAccount))}
    assert balances == {(G1, 1): 400, (G1, 2): 100, (G1, BOT): -700}
    assert {(b.user_id, b.amount) for b in session.exec(select(MarketBet))} == {(1, 200), (BOT, 300)}
    assert session.get(PredictionMarket, 40).choices == ["yes", "no"]

    sentences = session.exec(select(JailSentence)).all()
    current = {(s.guild_id, s.user_id) for s in sentences if s.released_at is None}
    # G1's JSON is skipped because jail_cog.db already has G1 rows; G2's JSON is imported.
    assert current == {(G1, 1), (G2, 3)}
    assert len(sentences) == 3

    # The counter said 3 tomatoes for G1 user 1 but history has 1, so 2 undated throws are added.
    throws = session.exec(select(TomatoThrow).where(TomatoThrow.guild_id == G1)).all()
    assert len(throws) == 3
    assert sum(t.thrown_at is None for t in throws) == 2
    # *.migrated files are ignored.
    assert len(session.exec(select(TomatoThrow).where(TomatoThrow.guild_id == G2)).all()) == 1


def test_legacy_files_untouched(legacy, tmp_path):
    before = {p: p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    run(legacy, tmp_path / "beans.db")
    after = {p: p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    assert before == after


def test_main_requires_backup_confirmation(legacy, tmp_path, monkeypatch, capsys):
    out_dir = tmp_path / "data"
    monkeypatch.setattr("builtins.input", lambda _: "no")
    monkeypatch.setattr("sys.argv", ["migrate", "--legacy-dir", str(legacy), "--out-dir", str(out_dir)])
    assert migration.main() == 1
    assert not (out_dir / "beans.db").exists()

    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert migration.main() == 0
    assert (out_dir / "beans.db").exists()
    # Never overwrites.
    assert migration.main() == 1
