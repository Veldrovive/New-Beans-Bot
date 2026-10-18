from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from beans_bot.config.schema import BotConfig

CONFIG_DIR = Path(__file__).parent.parent / "configs"


def load(env: str) -> BotConfig:
    with initialize_config_dir(config_dir=str(CONFIG_DIR), version_base=None):
        cfg = compose("config", overrides=[f"env={env}"])
    return BotConfig.model_validate(OmegaConf.to_container(cfg, resolve=True))


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_configs_validate(env, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    config = load(env)
    assert config.guilds
    for guild in config.guilds.values():
        assert guild.jail is not None and guild.jail.bribe is not None
        # Config amounts are BeanCoins, stored as base units.
        assert guild.jail.bribe.cost % 100 == 0


def test_inactive_guilds_are_hidden(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    config = load("dev")
    guild_id = next(iter(config.guilds))
    assert config.guild(guild_id) is not None
    config.guilds[guild_id].active = False
    assert config.guild(guild_id) is None
