import json

import pytest

from beans_bot.config.schema import BotConfig, GuildConfig, ManifestoConfig
from beans_bot.services.manifesto import ManifestoService, NoManifesto, NoSuchWisdom

from conftest import GUILD

SOPHIA = 10
TED = 20


@pytest.fixture
def manifesto(tmp_path):
    for name, wisdom in {"sophia": ["Oreos go in water.", "Rugs are bad.", "Octagonal windows."], "ted": ["Be Ted."]}.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "wisdom.json").write_text(json.dumps(wisdom))
    guild = GuildConfig(name="test", manifesto=ManifestoConfig(authors={SOPHIA: "sophia", TED: "ted"}))
    return ManifestoService(BotConfig(bot_token="x", manifestos_dir=tmp_path, guilds={GUILD: guild}))


def test_pick_by_member_and_index(manifesto):
    wisdom = manifesto.pick(GUILD, SOPHIA, 2)
    assert (wisdom.author_id, wisdom.index, wisdom.total, wisdom.text) == (SOPHIA, 2, 3, "Rugs are bad.")
    assert manifesto.pick(GUILD, TED, 1).text == "Be Ted."


def test_random_picks_stay_in_range(manifesto):
    for _ in range(50):
        wisdom = manifesto.pick(GUILD)
        assert wisdom.author_id in (SOPHIA, TED) and 1 <= wisdom.index <= wisdom.total
        assert manifesto.pick(GUILD, TED).index == 1


def test_index_without_member_picks_a_deep_enough_author(manifesto):
    for _ in range(20):
        assert manifesto.pick(GUILD, index=3).author_id == SOPHIA


def test_missing_manifesto_or_wisdom(manifesto):
    with pytest.raises(NoManifesto):
        manifesto.pick(GUILD, 30)
    with pytest.raises(NoManifesto):
        manifesto.pick(GUILD + 1)  # No manifesto config for this guild
    with pytest.raises(NoSuchWisdom) as e:
        manifesto.pick(GUILD, TED, 2)
    assert e.value.most == 1
    with pytest.raises(NoSuchWisdom) as e:
        manifesto.pick(GUILD, index=4)
    assert e.value.most == 3


def test_bad_wisdom_file(manifesto, tmp_path):
    (tmp_path / "ted" / "wisdom.json").write_text(json.dumps({"not": "a list"}))
    manifesto._wisdom.clear()
    with pytest.raises(ValueError):
        manifesto.wisdom("ted")
