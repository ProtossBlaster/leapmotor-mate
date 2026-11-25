"""Regression tests for the Statistics consumption-vs-temperature tooltip (beta #38)."""

from pathlib import Path


TEMPLATE = (Path(__file__).resolve().parents[1] / "web" / "templates" / "statistics.html")


def test_efficiency_temperature_tooltip_uses_plotted_y():
    source = TEMPLATE.read_text(encoding="utf-8")

    assert "one(p.y) + ' ' + c.dataset.unit" in source
    assert "p.l + ' L/100km" not in source
    assert "p.e + ' kWh/100km" not in source
