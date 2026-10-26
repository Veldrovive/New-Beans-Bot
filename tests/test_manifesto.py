import json
import random

import pytest

from beans_bot.cogs.manifesto import chunk_lines
from beans_bot.config.schema import BotConfig, GuildConfig, ManifestoConfig
from beans_bot.services.manifesto import ManifestoService, NoManifesto, NoSuchWisdom, NotYourWisdom, WrittenInStone

from conftest import GUILD

SOPHIA = 10
TED = 20


@pytest.fixture
def manifesto(tmp_path, db):
    (tmp_path / "manifestos").mkdir()
    for name, wisdom in {"sophia": ["Oreos go in water.", "Rugs are bad.", "Octagonal windows."], "ted": ["Be Ted."]}.items():
        (tmp_path / "manifestos" / name).mkdir()
        (tmp_path / "manifestos" / name / "wisdom.json").write_text(json.dumps(wisdom))
    guild = GuildConfig(name="test", manifesto=ManifestoConfig(authors={SOPHIA: "sophia", TED: "ted"}))
    return ManifestoService(BotConfig(bot_token="x", manifestos_dir=tmp_path / "manifestos", guilds={GUILD: guild}), db)


async def test_pick_by_member_and_index(manifesto):
    wisdom = await manifesto.pick(GUILD, SOPHIA, 2)
    assert (wisdom.author_id, wisdom.index, wisdom.total, wisdom.text) == (SOPHIA, 2, 3, "Rugs are bad.")
    assert (await manifesto.pick(GUILD, TED, 1)).text == "Be Ted."


async def test_random_picks_stay_in_range(manifesto):
    for _ in range(50):
        wisdom = await manifesto.pick(GUILD)
        assert wisdom.author_id in (SOPHIA, TED) and 1 <= wisdom.index <= wisdom.total
        assert (await manifesto.pick(GUILD, TED)).index == 1


async def test_random_picks_are_even_per_manifesto(manifesto):
    # A second member sharing Ted's manifesto mustn't double its odds, and Sophia's longer one mustn't outweigh it.
    manifesto.config.guilds[GUILD].manifesto.authors[30] = "ted"
    random.seed(0)
    picks = [await manifesto.pick(GUILD) for _ in range(4000)]
    ted = sum(p.author_id in (TED, 30) for p in picks) / len(picks)
    assert 0.46 < ted < 0.54
    # Then even within the manifesto
    sophia = [p.index for p in picks if p.author_id == SOPHIA]
    assert all(abs(sophia.count(i) / len(sophia) - 1 / 3) < 0.04 for i in (1, 2, 3))


async def test_index_without_member_picks_a_deep_enough_author(manifesto):
    for _ in range(20):
        assert (await manifesto.pick(GUILD, index=3)).author_id == SOPHIA


async def test_missing_manifesto_or_wisdom(manifesto):
    with pytest.raises(NoManifesto):
        await manifesto.pick(GUILD, 30)
    with pytest.raises(NoManifesto):
        await manifesto.pick(GUILD + 1)  # No manifesto config for this guild
    with pytest.raises(NoSuchWisdom) as e:
        await manifesto.pick(GUILD, TED, 2)
    assert e.value.most == 1
    with pytest.raises(NoSuchWisdom) as e:
        await manifesto.pick(GUILD, index=4)
    assert e.value.most == 3


def test_bad_wisdom_file(manifesto, tmp_path):
    (tmp_path / "manifestos" / "ted" / "wisdom.json").write_text(json.dumps({"not": "a list"}))
    manifesto._wisdom.clear()
    with pytest.raises(ValueError):
        manifesto.wisdom("ted")


async def test_list_one_or_all_manifestos(manifesto):
    [sophia] = await manifesto.manifestos(GUILD, SOPHIA)
    assert (sophia.author_id, sophia.wisdom) == (SOPHIA, ["Oreos go in water.", "Rugs are bad.", "Octagonal windows."])
    # Members sharing a manifesto show it once, under the first of them
    manifesto.config.guilds[GUILD].manifesto.authors[30] = "ted"
    assert [(m.author_id, len(m.wisdom)) for m in await manifesto.manifestos(GUILD)] == [(SOPHIA, 3), (TED, 1)]
    with pytest.raises(NoManifesto):
        await manifesto.manifestos(GUILD, 40)
    with pytest.raises(NoManifesto):
        await manifesto.manifestos(GUILD + 1)


async def test_add_goes_after_the_file_and_mixes_into_picks(manifesto):
    wisdom = await manifesto.add(GUILD, TED, "Ted again.")
    assert (wisdom.author_id, wisdom.index, wisdom.total, wisdom.text) == (TED, 2, 2, "Ted again.")
    assert (await manifesto.manifestos(GUILD, TED))[0].wisdom == ["Be Ted.", "Ted again."]
    assert (await manifesto.pick(GUILD, TED, 2)).text == "Ted again."
    assert manifesto.wisdom("ted") == ["Be Ted."]  # The file itself is untouched


async def test_anyone_can_start_a_manifesto(manifesto):
    with pytest.raises(NoManifesto):
        await manifesto.pick(GUILD, 30)
    await manifesto.add(GUILD, 30, "First thought.")
    await manifesto.add(GUILD, 30, "Second thought.")
    assert [(m.author_id, m.wisdom) for m in await manifesto.manifestos(GUILD)][-1] == (30, ["First thought.", "Second thought."])
    assert (await manifesto.pick(GUILD, 30, 1)).author_id == 30
    # Other guilds don't see it
    with pytest.raises(NoManifesto):
        await manifesto.pick(GUILD + 1, 30)


async def test_edit_and_remove_only_added_wisdom(manifesto):
    await manifesto.add(GUILD, SOPHIA, "Fourth.")
    await manifesto.add(GUILD, SOPHIA, "Fifth.")
    assert await manifesto.edit(GUILD, SOPHIA, 4, "Fourth, revised.") == "Fourth."
    assert await manifesto.remove(GUILD, SOPHIA, 4) == "Fourth, revised."
    [sophia] = await manifesto.manifestos(GUILD, SOPHIA)
    assert sophia.wisdom == ["Oreos go in water.", "Rugs are bad.", "Octagonal windows.", "Fifth."]
    assert [w.index for w in await manifesto.own_wisdom(GUILD, SOPHIA)] == [4]

    with pytest.raises(WrittenInStone):
        await manifesto.edit(GUILD, SOPHIA, 1, "Rugs are good.")
    with pytest.raises(WrittenInStone):
        await manifesto.remove(GUILD, SOPHIA, 3)
    with pytest.raises(NoSuchWisdom) as e:
        await manifesto.edit(GUILD, SOPHIA, 5, "Nope.")
    assert e.value.most == 4
    with pytest.raises(NoManifesto):
        await manifesto.remove(GUILD, 30, 1)
    assert (await manifesto.manifestos(GUILD, SOPHIA))[0].wisdom[:3] == ["Oreos go in water.", "Rugs are bad.", "Octagonal windows."]


async def test_shared_manifesto_lines_belong_to_who_added_them(manifesto):
    manifesto.config.guilds[GUILD].manifesto.authors[30] = "ted"
    await manifesto.add(GUILD, 30, "Ted's twin speaks.")
    assert (await manifesto.pick(GUILD, TED, 2)).author_id == 30
    with pytest.raises(NotYourWisdom):
        await manifesto.edit(GUILD, TED, 2, "Hijacked.")
    assert await manifesto.own_wisdom(GUILD, TED) == []
    assert await manifesto.edit(GUILD, 30, 2, "Ted's twin speaks louder.") == "Ted's twin speaks."


def test_chunk_lines_stays_under_the_limit():
    lines = [f"line {i} " + "x" * 30 for i in range(20)]
    chunks = chunk_lines(lines, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines
    assert chunk_lines(["short"]) == ["short"]
