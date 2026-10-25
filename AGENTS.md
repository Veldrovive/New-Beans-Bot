# AGENTS.md

Notes for coding agents working on Beans Bot. Read the README first. This file covers what the README doesn't: how the code is put together, the habits it follows, and the checklist for changing it.

## What this is

A Discord bot for one friend group's server (plus a dev test server). It's a rewrite of an older bot (`robot_discord_robot`), and its features are jokes with real bookkeeping behind them: jail, tomatoes, BeanCoins, prediction markets, VIP roles. The bookkeeping has to be correct, because people care about their coins. The jokes have to be funny, because that's the point.

Feature requests often show up as loose notes in `tasks/`, which is gitignored (for example `tasks/wealth_roles.md`). Treat them as the brief. Fill in sensible details, but don't invent extra features.

## Commands

```bash
uv sync                      # add --extra classifier for torch etc.
uv run pytest                # fast (~2s), no Discord connection needed
uv run pyrefly check         # type check. Must report 0 errors.
uv run alembic revision --autogenerate -m "describe the change"
uv run scripts/run_bot.py    # needs a real BOT_TOKEN in .env. Don't run it unless asked.
```

Run both `pytest` and `pyrefly check` before calling anything done. Typing habits that keep pyrefly clean:

- Wrap model columns in `col()` inside `update(...).where(...)` and `.group_by(...)`. Plain `Model.field == x` types as `bool` there.
- Narrow `Optional` results (`get_command`, `session.get`, `find_spec`) with an `assert x is not None` or an `if` before using them.
- BeautifulSoup attributes (`tag["href"]`) are `str | AttributeValueList`, so wrap them in `str()`.
- Use a different variable name for each walrus binding of a different type in the same scope.
- `bad-override` is turned off for `db/models/` only, because SQLModel's `__tablename__` stub flags every table. Don't add new `# type: ignore` comments. Fix the type instead.

## Architecture

```
scripts/run_bot.py      hydra composes configs/ -> BotConfig.model_validate -> BeansBot
bot.py                  BeansBot: builds Services once, adds every cog, and BeansTree.on_error
services/               the logic and every DB transaction. No discord.py imports for state changes.
cogs/                   one cog per feature: slash commands, listeners, timers, Discord side effects
db/models/              SQLModel tables, one module per feature, all re-exported from models/__init__.py
db/migrations/          Alembic. Database() runs upgrade_database on construction.
config/schema.py        Pydantic schema. StrictModel (extra="forbid") everywhere.
currency.py             integer base units, parsing and formatting
```

Rules the structure depends on:

- **Cogs talk to each other only through services.** A cog never imports another cog. Shared state lives in a service (`self.services.coins`, `.jail`, `.markets`, `.vip`, `.gemini`).
- **Services own transactions.** A public service method opens one `async with self.db.transaction() as session:`. Each transaction is `BEGIN IMMEDIATE`, so a read-check-write (check the balance, then spend it) can't interleave with another one. Building blocks that run inside a caller's transaction take `session` as their first argument (`CoinService.move`, `_account`, `balance_in`, `VipService._active_pass`). When an operation spends coins and changes feature state, do both in **one** transaction. See `VipService.claim_one_percent`.
- **Every balance change goes through `CoinService.move`.** It debits, credits and writes a `CoinTransaction` ledger row. Add a `CoinTransactionKind` for each new reason, with a comment showing the direction (`# Member -> house`). The ledger invariant is that, for each account, credits minus debits equals the balance. The legacy migration checks this, so never write `balance` directly.
- **The house** is the bot's own account (`coins.house_user_id`, set in `setup_hook`). It takes bribes, purchases and market leftovers, and it's the only account allowed to go negative. Members pay the house. Coins don't vanish.
- **The DB is the source of truth. Discord roles follow it.** Cogs that hand out roles also run a `tasks.loop` that reconciles them: they remove roles nobody paid for and restore roles that are missing (see `VipCog.sync_roles` and the jail timers). Use the same pattern for any new paid or timed role.
- **Domain errors are plain exceptions in services** (`InsufficientFunds`, `BidTooLow`, `AlreadyOnePercent`). The cog catches them and replies in character. For an error that's only a message, raise `UserFacingError` (or `CheckMessage` from a check) and `BeansTree.on_error` shows it ephemerally. `AmountError` from `currency.parse` is handled the same way, so cogs can call `parse` without a try block.

## Conventions

**Discord**
- Every command is a slash command. Group a feature's commands with `app_commands.Group(name=..., guild_only=True)` as a class attribute.
- Guard guild commands with `@requires_feature("<config section>")` and usually `@in_bot_channel()`. Use `@owner_only()` for admin commands.
- In code, a server is a **guild** (`guild_id`, `guilds:`). Text the bot sends to members still says "server".
- Errors and private info go in `ephemeral=True` replies. Announcements are public. When a message mentions people but shouldn't ping them (status, leaderboards), pass `allowed_mentions=discord.AllowedMentions.none()`.
- Fine print goes on a `-#` subtext line. Use `discord.utils.format_dt(dt, "R")` for times.
- Handle missing roles, members who left, and `discord.HTTPException` by logging and carrying on. Never crash a loop. `get_member` in `services/jail.py` fetches members safely.

**Config**
- Each feature is an optional section on `GuildConfig` (`vip: VipConfig | None = None`). If the section is missing, the feature is off for that guild.
- Coin amounts in the config use the `CoinAmount` type, and their defaults are written `parse_amount(n)`. YAML writes them as BeanCoins (`cost: 2`) or with a unit (`5 cheddar`).
- Durations are `datetime.timedelta`. YAML writes them as seconds or ISO 8601.
- Bot dialogue lives in config as `*_scripts: list[str]`, with an inline comment listing the placeholders (`# {buyer}, {paid}`). Pick one with `scripts.pick(list, **values)`, which returns `None` for an empty list, so always write an `or` fallback in code. Mentions go in as `member.mention` or `f"<@{id}>"`. Amounts go in already formatted.
- Every new guild setting goes in **both** `configs/env/dev.yaml` and `configs/env/prod.yaml`. Role and channel IDs differ between them. If you don't know the real IDs, ask, or leave an obvious placeholder and say so.

**Money**
- Store and move coins as integer base units (1 BeanCoin = 100). Convert only at the edges: `currency.parse` for input, `currency.format` for output. Don't hardcode `100` outside tests, and don't use floats.

**Database**
- Datetimes are always timezone-aware UTC. Use `sa_type=UTCDateTime` and `default_factory=utcnow` from `db/types.py`. Naive datetimes raise an error when stored.
- Set `__tablename__` explicitly in snake_case, prefixed by feature (`vip_day_pass`). For Discord-ID primary keys, add `sa_column_kwargs={"autoincrement": False}`.
- After changing a model, export it from `db/models/__init__.py`, run `alembic revision --autogenerate`, then read the generated file. Keep Alembic's generated style. Don't hand-tidy it.
- Prefer soft state (`ended_at` set instead of deleting the row) so history stays queryable.

**Style**
- Python 3.12+, `from __future__ import annotations`, modern type hints (`X | None`), and dataclasses for small result types (`OnePercentClaim`).
- Long lines are fine (about 120–130 characters). There's no formatter config, so match the surrounding code.
- Every feature module opens with a docstring that explains the feature in plain English, as a few short bullets. Comments explain *why*, are short, and sit next to the line they're about. Write in the README's register: plain words and short sentences, no marketing tone.
- Use `# --- Section ---...` dividers in long classes (building blocks / operations, timers / commands).
- Logging: `logger = logging.getLogger(__name__)` in services and `self.logger` in cogs, with f-strings. Log every coin-spending operation at INFO, with guild and user IDs.

**Tone of the bot's voice**
- Members are "members". The bot's replies are playful, a little theatrical and mean in a friendly way ("Peasant money.", "Back to the proletariat with you."). Each new user-facing message, error replies included, should have a joke in it while still saying exactly what went wrong and what it would cost.

## Adding a feature: checklist

Use the VIP feature (`cogs/vip.py`, `services/vip.py`, `db/models/vip.py`, `tests/test_vip.py`) as the template. It touches every layer.

1. Config models in `config/schema.py`, plus an optional field on `GuildConfig`.
2. Tables in `db/models/<feature>.py`, exported from `db/models/__init__.py`. Add a `CoinTransactionKind` if coins move.
3. Generate the Alembic migration, then review it.
4. A service in `services/<feature>.py`, registered on `Services` in `services/__init__.py` and in `Services.build`.
5. A cog in `cogs/<feature>.py` that subclasses `BeansCog`. Add it to the cog list in `bot.py` **and** to `COGS` and the expected command names in `tests/test_commands.py`.
6. A service fixture in `tests/conftest.py`, and `tests/test_<feature>.py`.
7. Config in both `dev.yaml` and `prod.yaml`, with in-character scripts.
8. One bullet in the README's feature list.
9. Run `uv run pytest` and `uv run pyrefly check`.

## Adding a manifesto

Manifestos live in `manifestos/<name>/`: the member's own file (`manifesto.rtf` or whatever they sent) next to a `wisdom.json`. The bot only ever reads `wisdom.json`, because manifestos can be personal.

1. Move the file to `manifestos/<name>/`.
2. Read it and write `wisdom.json`: a JSON list of strings, one opinion each, in the author's voice. Leave out anything personal: other people's and pets' names, ages, places, confessions, and notes addressed to someone. Generalize an opinion rather than quote a private detail. Skip anything that's only personal.
3. Map the member's Discord user ID to `<name>` under `manifesto.authors` in both `dev.yaml` and `prod.yaml`.
4. Run `uv run pytest`. `test_manifesto_authors_have_wisdom` checks that every mapped folder has valid wisdom.

## Testing

- Tests go through services against a real SQLite file in `tmp_path`, with migrations applied. Don't mock the DB. The `db`, `coins` (house = `HOUSE` = 999, cheddar off), `jail`, `markets` and `vip` fixtures live in `tests/conftest.py`.
- pytest-asyncio is in auto mode, so test functions are plain `async def`.
- Assert balances in base units, and check both sides (member and house). Check that a failed operation changed nothing (balance unchanged, no row created).
- Cogs aren't unit-tested beyond `test_commands.py`, which loads every cog and builds every command payload without a connection. That's why each new cog must be added there.
- `test_migration.py` runs `scripts/migrate_legacy_data.py` against databases built with the old bot's schemas. If you change a table the migration writes to, update the script and this test.

## Things to be careful with

- `data/beans.db` is the live database (gitignored). Don't delete it, reset it or migrate it by hand. The bot migrates it on startup.
- `.env` holds real secrets. Don't print it or commit it.
- `models/classifier/*.pkl` are trained artifacts. Leave them alone unless the task is retraining (`scripts/train_classifier.py`).
- Classifier code may import torch and other packages that are only in the `classifier` extra. Keep those imports inside `cogs/classifier/` so the bot runs without the extra.
- Don't commit unless asked. The working tree often holds the user's own work in progress.
