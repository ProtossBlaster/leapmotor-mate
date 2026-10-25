"""On a car with the simple charge scheduler the Charges page's target card says it is the official
app's upper limit (#436).

The card writes its number into the car's charge plan. Its description said the plan's target is not
the upper limit in the car's own app. On a T03 it is: @sarifarm set 92 % and then 90 % from the card,
and the official app showed each as its upper charging limit. A car with the weekly scheduler keeps
the old description, which nobody has measured wrong.
"""
import json
import pathlib

import pytest
from markupsafe import escape

pytest.importorskip("httpx", reason="Starlette TestClient needs httpx")

import db as D
import db_reader
from test_the_cable_shows_while_the_charger_holds import car  # noqa: F401  (car: fixture)

T03_ABILITIES = [1, 2, 3, 5, 7, 10, 11, 14, 15, 17, 18, 20, 30, 31, 34, 35, 36, 52, 61]   # @sarifarm's, #433
LOCALES = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"


def _tr(key, lang="en"):
    """The text as the page carries it, escaped by the template."""
    return str(escape(json.loads((LOCALES / f"{lang}.json").read_text())["translations"][key]))


def _abilities(value):
    c = D.Database(db_reader.DB_PATH)._conn
    c.execute("UPDATE vehicles SET abilities = ? WHERE id = 1", (json.dumps(value),))
    c.commit()
    c.close()


def test_a_t03_reads_that_the_target_is_the_apps_upper_limit(car):
    _abilities(T03_ABILITIES)
    page = car.get("/charges")
    assert _tr("charge_limit_desc_simple") in page
    assert _tr("charge_limit_desc") not in page


def test_a_car_with_the_weekly_scheduler_keeps_its_description(car):
    _abilities(T03_ABILITIES + [25, 26, 47, 51])
    page = car.get("/charges")
    assert _tr("charge_limit_desc") in page
    assert _tr("charge_limit_desc_simple") not in page


def test_every_language_has_the_t03_description():
    for f in LOCALES.glob("*.json"):
        text = json.loads(f.read_text())["translations"].get("charge_limit_desc_simple", "")
        assert text, f.name
