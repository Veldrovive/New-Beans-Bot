"""
Run the bot.

    uv run scripts/run_bot.py            # dev guilds (configs/env/dev.yaml)
    uv run scripts/run_bot.py env=prod   # prod guilds

Any config value can be overridden hydra-style, e.g. `db_path=/tmp/test.db`.
"""

import logging

import hydra
from dotenv import load_dotenv
from omegaconf import DictConfig, OmegaConf

from beans_bot.bot import BeansBot
from beans_bot.config.schema import BotConfig

# Before hydra resolves ${oc.env:...} interpolations.
load_dotenv()


@hydra.main(config_path="../configs", config_name="config", version_base=None)
def main(cfg: DictConfig) -> None:
    config = BotConfig.model_validate(OmegaConf.to_container(cfg, resolve=True))
    logging.getLogger().setLevel(config.log_level)
    # discord.py's own loggers are chatty at DEBUG.
    logging.getLogger("discord").setLevel(max(logging.INFO, logging.getLevelName(config.log_level)))
    logging.info(f"Loaded config for guilds: {', '.join(f'{g.name} ({gid})' for gid, g in config.guilds.items())}")

    bot = BeansBot(config)
    bot.run(config.bot_token.get_secret_value(), log_handler=None)


if __name__ == "__main__":
    main()
