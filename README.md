# Beans Bot

A Discord bot for the beans server. It's a rewrite of `robot_discord_robot`:

- **Jail:** enough jail emoji reactions put a message's author in the stocks. Jailed members get humiliated, tomatoed, and at the mega jail threshold renamed by the crowd.
- **Framing:** `/jail frame` pays the bot to jail someone else, with your name in the announcement. Paying the higher anonymous price keeps your name out of it.
- **Tomatoes:** throw them at jailed members, or get scolded for hitting an innocent.
- **BeanCoins:** earned when others react to your messages with the coin emoji. Spend them on bribes and bets.
- **ShadowBean Coins and transfers:** heist loot and snitch rewards are paid in ShadowBean Coins, a hidden pocket that can't be spent. Only you see them, in `/coins wealth` with `quiet` on. `/coins launder` turns them into regular coins, privately, as much or as little at a time as you like, so nobody notices your balance jump. `/coins transfer <member> <amount>` gives someone your coins. With `quiet` on it's DMed to them instead of announced. An optional `message` always goes to them by DM, never in the announcement. With `anonymous` on, nobody is told who sent it, not even them. Server admins can `/admin give` and `/admin take` either kind, with the house on the other side.
- **VIP:** spend BeanCoins on a role that sees the VIP channel: a day pass into the bourgeoisie, or the one permanent "1%" title, which anyone can usurp by paying more than its holder did. The 1% can appoint a henchman who shares the channel until the title changes hands.
- **Prediction markets:** betting pools on threads in the gambling forum, with pari-mutuel payouts. `/bet place` and `/bet add` accept `all in` as the amount. `/bet swap` moves your whole bet to another choice, at the cost of an @everyone and a trip to jail. If a market resolves within a couple of hours of opening (`rigged_window`) and the house's bet is the only one that lost, the winners get refunds instead of winnings, plus a trip to jail, where they can't bet.
- **Automatic bets:** once a day (at `post_time`, if a `post_probability` roll lands) the bot opens a market in the gambling forum by itself, picked from the guild's JSON bets file in `auto_bets/`. Each bet is a template like `"{person} will leave the building before {time}"`, with the values each placeholder can take, its choices (yes/no if left out), an optional `bot_bet` range that sets how much the bot bets on it, and the `admins` who close, cancel and resolve it. Bets are used up a round at a time, so every bet gets posted before any repeats. Server admins can post one right away with `/autobet post`.
- **Snipes:** post a photo of a member in the snipe channel and tag them (or the bot asks who it is). The Council of Teds verifies it by reacting with their crests, or rejects it with the nay emoji (honse), and whichever side reaches a majority first wins. Snipes the council ignores for two days expire. Only a crest's owner may react with it, and the bot removes anyone else's.
- **Heists:** `/heist begin <member>` privately gives you a codeword (a random noun) to share in real life, and a table of how much each crew size would steal. Others join with `/heist join <codeword>`. Crew members can check on their heist privately with `/heist status`. If anyone in a crew of at least 4 (leader included) gets a verified snipe of the target, the crew splits a cut of the target's coins in ShadowBeans (10% for 4 people, up to 30% for 8 or more), and a target who held the 1% loses it, which resets its price. You can rob the bot too, when it isn't in debt. Anyone with the codeword can `/heist snitch` instead: the whole crew and the snitch (for cover) go to jail and pay the house 25% of their coins, and the snitch gets half of the fines in ShadowBeans on the spot. A successful heist only names the target and the sniper; the rest of the crew stays anonymous. Heists fall apart after 3 days. Being in a crew doesn't stop a council member from verifying the snipe. Corruption is encouraged.
- **Manifestos:** `/manifesto wisdom [member] [wisdom_index]` has the bot espouse one piece of wisdom from a member's manifesto, picked at random if you leave either out. `/manifesto list [member]` privately shows the numbered wisdom so you can choose. To impart wisdom as a reply, use **Apps > Impart wisdom** on the message. Any member can add to their own manifesto with `/manifesto add <statement>` (the 1% can pass `member` to add to anyone's), and fix or take back what they added with `/manifesto edit` and `/manifesto remove`. The original manifesto lines can't be changed from Discord. All of these work in any channel. The bot only quotes the wisdom pulled out of each manifesto, never the manifesto itself.
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
