# Beans Bot

A Discord bot for the beans server. It's a rewrite of `robot_discord_robot`:

- **Jail:** enough jail emoji reactions put a message's author in the stocks. Jailed members get humiliated, tomatoed, and at the mega jail threshold renamed by the crowd.
- **Framing:** `/jail frame` pays the bot to jail someone else, with your name in the announcement. Paying the higher anonymous price keeps your name out of it.
- **Tomatoes:** throw them at jailed members, or get scolded for hitting an innocent.
- **BeanCoins:** earned when others react to your messages with the coin emoji. Spend them on bribes and bets.
- **ShadowBean Coins and transfers:** heist loot and snitch rewards are paid in ShadowBean Coins, a hidden pocket that can't be spent. Only you see them, in `/coins wealth` with `quiet` on. `/coins launder` turns them into regular coins, privately, as much or as little at a time as you like, so nobody notices your balance jump. The bot channel hears how much someone laundered, but not who. `/coins transfer <member> <amount>` gives someone your coins. With `quiet` on it's DMed to them instead of announced. An optional `message` always goes to them by DM, never in the announcement. With `anonymous` on, nobody is told who sent it, not even them. Server admins can `/admin give` and `/admin take` either kind, with the house on the other side.
- **VIP:** spend BeanCoins on a role that sees the VIP channel: a day pass into the bourgeoisie, or the one permanent "1%" title, which anyone can usurp by paying more than its holder did. The 1% can appoint a henchman who shares the channel until the title changes hands.
- **Prediction markets:** betting pools on threads in the gambling forum, with pari-mutuel payouts. `/bet place` and `/bet add` accept `all in` as the amount. `/bet swap` moves your whole bet to another choice, at the cost of an @everyone and a trip to jail. If a market resolves within a couple of hours of opening (`rigged_window`) and the house's bet is the only one that lost, the winners get refunds instead of winnings, plus a trip to jail, where they can't bet. The house's own bet is a secret: `/bet stats` leaves it out, and it's revealed when the market resolves. Server admins can `/admin nullifybet` a resolved market to take back its payouts and refund every stake, even if that puts the winners in debt.
- **Automatic bets:** once a day (at `post_time`, if a `post_probability` roll lands) the bot opens a market in the gambling forum by itself, picked from the guild's JSON bets file in `auto_bets/`. Each bet is a template like `"{person} will leave the building before {time}"`, with the values each placeholder can take, its choices (yes/no if left out), an optional `bot_bet` range that sets how much the bot bets on it, and the `admins` who close, cancel and resolve it. Bets are used up a round at a time, so every bet gets posted before any repeats. Server admins can post one right away with `/autobet post`.
- **Snipes:** post a photo of a member in the snipe channel and tag them (or the bot asks who it is). The Council of Teds verifies it by reacting with their crests, or rejects it with the nay emoji (honse), and whichever side reaches a majority first wins. Snipes the council ignores for two days expire. Only a crest's owner may react with it, and the bot removes anyone else's.
- **Heists:** `/heist begin <member>` privately gives you a codeword (a random noun) to share in real life, and a table of how much each crew size would steal. Others join with `/heist join <codeword>`. Crew members can check on their heist privately with `/heist status`. If anyone in a crew of at least 4 (leader included) gets a verified snipe of the target, the crew splits a cut of the target's coins in ShadowBeans (10% for 4 people, up to 30% for 8 or more), and a target who held the 1% loses it, which resets its price. You can rob the bot too, when it isn't in debt. Anyone with the codeword can `/heist snitch` instead: the whole crew and the snitch (for cover) go to jail and pay the house 25% of their coins, and the snitch gets half of the fines in ShadowBeans on the spot. A successful heist only names the target and the sniper; the rest of the crew stays anonymous. Heists fall apart after 3 days. Being in a crew doesn't stop a council member from verifying the snipe. Corruption is encouraged.
- **Manifestos:** `/manifesto wisdom [member] [wisdom_index]` has the bot espouse one piece of wisdom from a member's manifesto, picked at random if you leave either out. `/manifesto list [member]` privately shows the numbered wisdom so you can choose. To impart wisdom as a reply, use **Apps > Impart wisdom** on the message. Any member can add to their own manifesto with `/manifesto add <statement>` (the 1% can pass `member` to add to anyone's), and fix or take back what they added with `/manifesto edit` and `/manifesto remove`. The original manifesto lines can't be changed from Discord. All of these work in any channel. The bot only quotes the wisdom pulled out of each manifesto, never the manifesto itself.
- **Birthdays, action holidays, fat cow stats, the robotics student role poll,** and the **image classifier** (cats and moods). Anyone can confirm a cat guess, correct it or add a new cat with the buttons under it (or overrule a decision later from the message menu), and the photo joins the model's dataset. `/admin retrain` (or the website) retrains the model on its train photos in the background and scores it on its val photos.
- **Photo drops:** `/photos drop name:<cat>` lets a member DM the bot a pile of photos of one cat. Each DM gets a tally, and the drop ends when they DM `done`, run `/photos done`, or go quiet, with a summary. Photos join the dataset with the source "drop", up to a daily limit per member.
- **Auto retrain:** models with an `auto_retrain` section retrain themselves once enough photos have changed since the last run, the changes have stopped for a while, no drop is open and the last run wasn't too recent. These runs show as "auto" on the website.
- **Admin website:** a management site served by the bot itself (see [Website](#website)). It shows every feature's records and config, the house's secret bets and the heists members can't see. From it, admins can give and take coins, speak or DM as the bot, sync commands, jail and release members, bet for the house and close, cancel or nullify markets, post an automatic bet, end day passes, revoke the 1%, and add, edit or remove manifesto wisdom. It also manages the classifier's photos: labels, the train/val split, uploads, retraining and each retrain's scores.

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
| `src/beans_bot/web/` | The admin website's API (FastAPI). `cogs/web.py` serves it from inside the bot with Hypercorn. |
| `frontend/` | The admin website's pages (Vite, React, TypeScript, Tailwind). Built into `frontend/dist`, which the API serves. |
| `scripts/` | Entrypoints: `run_bot.py`, `run_web.py`, `export_openapi.py`, `import_classifier_dataset.py`, `migrate_legacy_data.py`, `train_classifier.py`. |
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

## Website

The bot serves an admin website on port 8377 (`web:` in `configs/config.yaml`). There is no login: it only listens on localhost, and Cloudflare Access sits in front of it. Never route it to the internet without Access.

Every page is per server, picked at the top left. Actions sit on the page of the thing they act on, and each one says what it will do (and whether it posts in Discord) before it does it. Config is shown, never edited: it stays in the YAML files.

Set `web.actor_user_id` to your Discord user ID. Coins given or taken on the website are recorded in the ledger as done by that user, and give and take refuse to work while it's unset.

### Working on it

```bash
cp data/beans.db /tmp/beans-copy.db                       # never point it at data/beans.db: opening a database migrates it
uv run scripts/run_web.py db_path=/tmp/beans-copy.db      # the API, without Discord, on http://127.0.0.1:8377
cd frontend && npm install && npm run dev                 # the pages, with /api passed through to run_web.py
```

Without Discord, names show as raw IDs and anything that needs Discord (say, DM, jail roles, posting a bet, retraining) answers 503.

After changing an endpoint or a response model, regenerate the API types and commit both files:

```bash
uv run scripts/export_openapi.py && (cd frontend && npm run gen:api)
```

`npm run build` in `frontend/` builds the pages into `frontend/dist`. The Docker image does that itself.

### The classifier's photos

Each model's photos live in `data/classifier_datasets/<model>/images/`, shrunk to JPEGs of at most 2048 pixels and indexed in the database. Corrections from Discord join them automatically. To bring in the original photos from the Mac (`models/classifier/datasets/<model>/<label>/`) and the old `data/classifier_corrections/` folder:

```bash
uv run scripts/import_classifier_dataset.py --source models/classifier/datasets --corrections data/classifier_corrections --dry-run
uv run scripts/import_classifier_dataset.py --source models/classifier/datasets --corrections data/classifier_corrections
```

It skips photos already imported, never touches the source, and is safe to run while the bot is up. Seed photos get the same train/val split as `scripts/train_classifier.py`, so the first retrain's scores compare with today's model. Until photos are imported, retraining answers that there's nothing to train on.

### Rolling it out on haven

These are the steps for the first deploy. The prod bot runs on haven as the Compose project `~/containers/New-Beans-Bot/`.

1. On haven, add these to `~/containers/New-Beans-Bot/docker-compose.yml` under the `bot` service (that file isn't in git): `ports: ["127.0.0.1:8377:8377"]`, and `- WEB_HOST=0.0.0.0` in `environment`. Then `git pull && docker compose up -d --build`, and check `curl -s localhost:8377/api/meta` on haven.
2. From the Mac, copy the original photos to scratch space, which isn't backed up (everything under `~/containers` is, every night, and the originals are 1.8 GB):
   `rsync -a --info=progress2 models/classifier/datasets/ haven:/srv/ssd/beans-import/datasets/`
3. On haven, import them with the scratch folder mounted read-only into a one-off container. Run it with `--dry-run` first and read the per-label counts (cats 269 photos, emotions 77, before duplicates):
   `cd ~/containers/New-Beans-Bot && docker compose run --rm -v /srv/ssd/beans-import:/import:ro bot uv run --no-sync scripts/import_classifier_dataset.py --source /import/datasets --corrections data/classifier_corrections`
4. Open the site through an SSH tunnel (`ssh -L 8377:localhost:8377 haven`, then http://localhost:8377), check the photo counts per cat, press Retrain, and compare the accuracy with the last local `train_classifier.py` run. Until this step, `/admin retrain` answers that no photos are imported.
5. Add a Cloudflare route (for example `beans.aidandevelops.com` -> `http://localhost:8377`) **with an Access policy, like `seerr.` and `torrent.`** The site has no login of its own, so it must never be routed without Access.
6. Once the retrain looks right: `rm -r /srv/ssd/beans-import` and `rm -r ~/containers/New-Beans-Bot/data/classifier_corrections` on haven.
7. Record it in `infra-management`: the `New-Beans-Bot` row in `docs/hosts/haven.md` (port 8377, the photos under `data/classifier_datasets/`, the website), a row in the table in `docs/network.md`, and a dated `CHANGELOG.md` entry.

To try the Discord-side actions safely, use the recipe in `haven.md` for running the dev bot on haven against the Test Server with a copy of the database. Add `-p 127.0.0.1:8378:8377 -e WEB_HOST=0.0.0.0` to that `docker compose run` command and tunnel port 8378.

## Development

```bash
uv run pytest
uv run pyrefly check
(cd frontend && npm run build)
uv run alembic revision --autogenerate -m "describe the change"   # after changing a table
```
