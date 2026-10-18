import discord

from beans_bot.cogs.birthday import Birthday, ordinal, parse_birthdays
from beans_bot.cogs.jail import renamed_nickname
from beans_bot.cogs.role_name import validate_adjective
from beans_bot.services import emoji, scripts


def test_parse_birthdays():
    content = "# Birthdays\n## Old\n- Aidan: April 2nd, 2000\n- Merlin: July 21st\n- Bean: Unknown\nnot a line"
    assert parse_birthdays(content) == [Birthday("Aidan", 4, 2, 2000), Birthday("Merlin", 7, 21, None)]


def test_ordinal():
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "101st", "111th",
    ]  # fmt: skip


def test_renamed_nickname_keeps_parenthetical():
    assert renamed_nickname("Aidan (he/him)", "Stinky") == "Stinky (he/him)"
    assert renamed_nickname("Aidan", "x" * 40) == "x" * 32
    assert len(renamed_nickname("Aidan (he/him)", "x" * 40)) == 32


def test_validate_adjective():
    assert validate_adjective("  shiny ") == "Shiny"
    assert validate_adjective("") is None
    assert validate_adjective("x" * 21) is None


def test_emoji_matching():
    assert emoji.matches("🍅", "🍅")
    custom = discord.PartialEmoji(name="tojail", id=123)
    assert emoji.matches(custom, "tojail")
    assert emoji.matches(custom, ":tojail:")
    assert not emoji.matches(custom, "BeanCoin")


def test_emoji_resolve_without_guild():
    assert emoji.resolve(None, "cow, 🇬") == ["🐮", "🇬"]


def test_scripts_fill_known_placeholders_only():
    assert scripts.pick(["Hi {jailed_user} {other}"], jailed_user="@a") == "Hi @a {other}"
    assert scripts.pick([]) is None
