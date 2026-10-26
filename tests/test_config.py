from pathlib import Path

import pytest
from hydra import compose, initialize_config_dir
from omegaconf import OmegaConf

from beans_bot.config.schema import BotConfig
from beans_bot.services.auto_bets import load_bets
from beans_bot.services.manifesto import ManifestoService

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


@pytest.mark.parametrize("env", ["dev", "prod"])
async def test_manifesto_authors_have_wisdom(env, monkeypatch, db, vip):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    config = load(env)
    config.manifestos_dir = CONFIG_DIR.parent / "manifestos"
    manifesto = ManifestoService(config, db, vip)
    for guild_id, guild in config.guilds.items():
        for name in manifesto.authors(guild_id).values():
            assert manifesto.wisdom(name), f"{name} has no wisdom"


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_auto_bets_files_load(env, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    config = load(env)
    for guild in config.guilds.values():
        if guild.auto_bets is not None:
            assert load_bets(CONFIG_DIR.parent / guild.auto_bets.bets_file)
