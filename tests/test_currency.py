import pytest

from beans_bot.currency import AmountError, CurrencySystem, parse_amount


def test_parse_beans_by_default():
    assert CurrencySystem().parse("5") == 500
    assert CurrencySystem().parse("2 beans") == 200


def test_fractional_beans_need_cheddar():
    with pytest.raises(AmountError):
        CurrencySystem(cheddar_enabled=False).parse("1.5")
    assert CurrencySystem(cheddar_enabled=True).parse("1.5") == 150
    assert CurrencySystem(cheddar_enabled=True).parse("3 cheddar") == 30


def test_cheddar_unit_rejected_when_disabled():
    with pytest.raises(AmountError):
        CurrencySystem(cheddar_enabled=False).parse("3 cheddar")


@pytest.mark.parametrize("text", ["0", "-1", "abc", "1.25", "5 dollars", ""])
def test_invalid_amounts(text):
    with pytest.raises(AmountError):
        CurrencySystem(cheddar_enabled=True).parse(text)


@pytest.mark.parametrize("text", ["all", "All In", " allin ", "everything", "MAX"])
def test_parse_bet_all_in(text):
    assert CurrencySystem().parse_bet(text) is None


def test_parse_bet_amounts():
    assert CurrencySystem().parse_bet("5") == 500
    with pytest.raises(AmountError):
        CurrencySystem().parse_bet("all of it")
    with pytest.raises(AmountError):
        CurrencySystem().parse("all")


def test_format():
    assert CurrencySystem().format(1200) == "12 BeanCoins"
    assert CurrencySystem().format(100) == "1 BeanCoin"
    assert CurrencySystem().format(0) == "0 BeanCoins"
    assert CurrencySystem().format(-300) == "-3 BeanCoins"
    assert CurrencySystem(cheddar_enabled=True).format(150) == "1 BeanCoin 5 CheddarCoins"


def test_floor_to_smallest_enabled_coin():
    assert CurrencySystem().floor(199) == 100
    assert CurrencySystem(cheddar_enabled=True).floor(199) == 190


def test_config_amounts():
    assert parse_amount(10) == 1000
    assert parse_amount("5 cheddar") == 50
