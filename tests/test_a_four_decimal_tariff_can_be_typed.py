"""A 0.3024 €/kWh tariff could not be entered, so a charge was priced at 0.30 (#308, @adoewa).

His last charge cost him 18.10 € and Mate wrote 17.95. The arithmetic says which number Mate held:
17.95 / 0.30 = 59.83 kWh, and 59.83 × 0.3024 = 18.09 — his figure. So the stored price was two
decimals, not four.

The price field on the Costs page carried `step="0.01"`. A browser validates a number input against
its step: a value that is not a whole multiple is rejected on submit, so a four-decimal tariff could
never be saved, and the two-decimal one it fell back to priced every home charge about half a
percent low. Four-decimal electricity tariffs are ordinary in Europe.

`min="0"` stays, and so does the absence of a max — a ceiling would block ISK/JPY/KRW/HUF, which
price electricity in tens or hundreds per kWh (discussion #265). Only the step goes.

Nothing else rounds it: the field renders the stored value raw, and the `price3` filter with its
three decimals is used for computed AVERAGES (avg_price, blended €/kWh), never for the tariff typed
here.
"""
import pathlib
import re

import pytest

COSTS = pathlib.Path(__file__).parents[1] / "web" / "templates" / "costs.html"


@pytest.fixture()
def price_input() -> str:
    html = COSTS.read_text()
    m = re.search(r"<input[^>]*class=\"price-input[^>]*>", html, re.S)
    if not m:
        m = re.search(r"<input[^>]*id=\"price-input-\{\{ key \}\}\".*?>", html, re.S)
    assert m, "the €/kWh price field is not in costs.html any more"
    return m.group(0)


def test_the_tariff_field_does_not_round_to_the_cent(price_input):
    """`step="0.01"` is what made 0.3024 unsaveable."""
    step = re.search(r'step="([^"]+)"', price_input)
    assert step, "the field has no step at all"
    assert step.group(1) == "any", \
        f'step="{step.group(1)}" rejects a four-decimal tariff such as 0.3024'


def test_a_price_cannot_go_negative(price_input):
    """The guard that has to survive the change."""
    assert 'min="0"' in price_input


def test_no_ceiling_is_introduced(price_input):
    """ISK/JPY/KRW/HUF price a kWh in tens or hundreds (discussion #265)."""
    assert 'max="' not in price_input


def test_the_cost_of_a_charge_uses_every_decimal_of_the_tariff(tmp_path, monkeypatch):
    """The point of the field: his 59.83 kWh at 0.3024 is 18.09 €, not 17.95."""
    import db as poller_db
    import db_reader
    path = str(tmp_path / "price.db")
    poller_db.Database(path)
    monkeypatch.setattr(db_reader, "DB_PATH", path)
    db_reader.set_setting("price_home_kwh", "0.3024")
    assert db_reader.get_charge_prices()["price_home_kwh"] == pytest.approx(0.3024)
    assert round(59.83 * db_reader.get_charge_prices()["price_home_kwh"], 2) == pytest.approx(18.09)
