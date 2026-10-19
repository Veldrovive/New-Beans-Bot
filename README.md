# Beans Bot

A Discord bot for the beans server. It's a rewrite of `robot_discord_robot`:

- **Jail:** enough jail emoji reactions put a message's author in the stocks. Jailed members get humiliated, tomatoed, and at the mega jail threshold renamed by the crowd.
- **Tomatoes:** throw them at jailed members, or get scolded for hitting an innocent.
- **BeanCoins:** earned when others react to your messages with the coin emoji. Spend them on bribes and bets.
- **VIP:** spend BeanCoins on a role that sees the VIP channel: a day pass into the bourgeoisie, or the one permanent "1%" title, which anyone can usurp by paying more than its holder did.
- **Prediction markets:** betting pools on threads in the gambling forum, with pari-mutuel payouts. `/bet place` and `/bet add` accept `all in` as the amount. `/bet swap` moves your whole bet to another choice, at the cost of an @everyone and a trip to jail.
- **Birthdays, action holidays, fat cow stats, the robotics student role poll,** and the **image classifier** (cats and moods).

Every command is a slash command, so Discord shows the arguments as you type.

## Running

```bash
uv sync                                  # add --extra classifier for the image classifier
cp .env.example .env                     # then fill in BOT_TOKEN etc.
uv run scripts/run_bot.py                # dev guilds
uv run scripts/run_bot.py env=prod       # prod guilds
```

Any config value can be overridden on the command line, hydra-style: `uv run scripts/run_bot.py env=prod log_level=DEBUG`.

With Docker: `cp docker-compose.yml.example docker-compose.yml`, then run `docker compose up -d --build`.

The bot creates and migrates `data/beans.db` on startup.

## Layout

| Path | What's there |
|---|---|
| `src/beans_bot/config/schema.py` | Pydantic schema for the config. Hydra composes `configs/`, and the result is validated against this. |
| `src/beans_bot/db/` | The single SQLite database (SQLModel + aiosqlite) and its tables. Alembic migrations are in `db/migrations`. |
| `src/beans_bot/services/` | Shared systems that several cogs use: `CoinService`, `JailService`, `MarketService`, Gemini, scheduling, emoji helpers. |
| `src/beans_bot/cogs/` | One cog per feature. Cogs talk to each other only through services. |
| `src/beans_bot/currency.py` | Coin amounts. See below. |
| `scripts/` | Entrypoints: `run_bot.py`, `migrate_legacy_data.py`, `train_classifier.py`. |
| `configs/` | `config.yaml` holds global settings. `env/dev.yaml` and `env/prod.yaml` hold the per-guild settings. |

The code uses Discord's API names. What the Discord app calls a "server" is a **guild** here (`guild_id`, `guilds:` in config). Messages the bot sends still say "server".

## Coins and CheddarCoin

Balances, bets and costs are stored as integer **base units**, with 1 BeanCoin = 100 base units. CheddarCoin (1/10 BeanCoin = 10 base units) is already defined in `currency.py`. To turn it on, set `currency.cheddar_enabled: true`. Members can then use amounts like `1.5` or `3 cheddar`, and payouts round to the nearest CheddarCoin instead of the nearest BeanCoin. No data migration is needed.

In config files, amounts are written in BeanCoins (`cost: 10`) or with a unit (`cost: 5 cheddar`).

## Migrating from the old bot

1. Stop the old bot.
2. Back up its folder (the one holding `db.sqlite3` and `data/`).
3. Do a dry run. It prints a report and writes nothing:
   ```bash
   uv run scripts/migrate_legacy_data.py --legacy-dir /path/to/old/bot --dry-run
   ```
4. Run it for real. This creates `./data/beans.db`:
   ```bash
   uv run scripts/migrate_legacy_data.py --legacy-dir /path/to/old/bot
   ```
5. Start the new bot.

The script asks you to confirm you have a backup. It never changes the legacy folder: it reads copies of the databases. It refuses to overwrite an existing `beans.db`, and it only keeps the result if every check passes. The checks cover row counts, each member's balance against the old bot, ledger totals, and market bet totals.

## Development

```bash
uv run pytest
uv run alembic revision --autogenerate -m "describe the change"   # after changing a table
```
