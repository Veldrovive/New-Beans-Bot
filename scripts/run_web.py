"""
Serve the admin website's API without connecting to Discord, for working on the website.

    cp data/beans.db /tmp/beans-copy.db
    uv run scripts/run_web.py db_path=/tmp/beans-copy.db [house_user_id=999] [env=prod]

Then run `npm run dev` in frontend/ and open the address it prints.

Run it against a **copy** of a database, never data/beans.db: constructing the database migrates it.
This script refuses to open data/beans.db for that reason.

Pages that need Discord (names, say, DM, jail roles, posting bets) answer 503, and IDs show as raw
numbers. `house_user_id` stands in for the bot's own user ID (default 999). Other config values can be
overridden hydra-style, like run_bot.py.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import cast

import hydra
from hypercorn.asyncio import serve
from hypercorn.config import Config as HypercornConfig
from hypercorn.typing import ASGIFramework
from omegaconf import DictConfig, OmegaConf

from beans_bot.config.schema import BotConfig
from beans_bot.services import Services
from beans_bot.web.app import create_app
from beans_bot.web.context import WebContext

LIVE_DB = Path("data/beans.db")

# Not a config value, so it's taken off the command line before hydra sees it.
house_user_id = 999
for arg in list(sys.argv[1:]):
    if arg.startswith("house_user_id="):
        house_user_id = int(arg.split("=", 1)[1])
        sys.argv.remove(arg)

# No Discord here, so the token is never used. .env isn't loaded either.
os.environ.setdefault("BOT_TOKEN", "unused-by-run_web")


@hydra.main(config_path="../configs", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    config = BotConfig.model_validate(OmegaConf.to_container(cfg, resolve=True))
    logging.getLogger().setLevel(config.log_level)
    if config.db_path.resolve() == LIVE_DB.resolve():
        raise SystemExit(f"Refusing to open {LIVE_DB}: it would be migrated. Copy it and pass db_path=<the copy>.")

    services = Services.build(config)
    services.coins.house_user_id = house_user_id
    app = create_app(WebContext(config=config, services=services, bot=None))
    hypercorn_config = HypercornConfig()
    hypercorn_config.bind = [f"{config.web.host}:{config.web.port}"]
    hypercorn_config.errorlog = logging.getLogger("hypercorn.error")  # Through the root logger, once
    logging.info(f"Serving the API for {config.db_path} on http://{config.web.host}:{config.web.port}")
    # Starlette's ASGI types are looser than Hypercorn's, but the same protocol.
    asyncio.run(serve(cast(ASGIFramework, app), hypercorn_config))


if __name__ == "__main__":
    main()
