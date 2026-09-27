"""The three energy-source labels must read as three sources, in every language.

@matttiaromano (#332, T03, 4.3.0) asked what "GetEC" means. It is the name of a Leapmotor cloud
endpoint, and it was sitting in the trips list as a user-facing label beside two labels written for
people: "Leapmotor cloud" and "Mate estimate". He had two 1-km trips whose SoC never moved
(100 -> 100 %, 17 -> 17 %), which is exactly when the SoC estimate has nothing to work with, so the
metered figure was used and the endpoint's name was what he saw.

Two things are checked here, because the question had two halves:

  * no label is the endpoint's name. "getEC" is ours, not the user's vocabulary.
  * "cloud" does not claim the cloud for itself. Both the trip-history figure and the metered figure
    come from the cloud, so a label that says only "cloud" on one of them says the other one is not
    from the cloud, which is false. It has to say WHICH cloud figure it is.

The labels render in web/templates/partials/trip_row.html and web/templates/trip_detail.html via
t('trip_energy_source_' ~ trip.energy_source).
"""
import json
import pathlib

import pytest

LOCALES = pathlib.Path(__file__).resolve().parent.parent / "web" / "locales"
LANGS = ("en", "it", "fr", "de", "pl", "pt-PT", "nl", "es")
KEYS = ("trip_energy_source_cloud", "trip_energy_source_getec", "trip_energy_source_mate")


def _labels(lang):
    payload = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))["translations"]
    return {key: payload.get(key) for key in KEYS}


@pytest.mark.parametrize("lang", LANGS)
def test_every_language_carries_the_three_labels(lang):
    labels = _labels(lang)
    for key, value in labels.items():
        assert isinstance(value, str) and value.strip(), f"{lang}: {key} is missing"


@pytest.mark.parametrize("lang", LANGS)
def test_no_label_is_the_name_of_our_endpoint(lang):
    for key, value in _labels(lang).items():
        assert "getec" not in value.lower(), f"{lang}: {key} still shows the endpoint name {value!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_the_metered_label_says_the_car_measured_it(lang):
    value = _labels(lang)["trip_energy_source_getec"]
    assert "leapmotor" not in value.lower(), (
        f"{lang}: the metered figure is the car's own, not a second Leapmotor label: {value!r}"
    )
    assert len(value) > len("getEC"), f"{lang}: {value!r} is not a phrase a user can read"


@pytest.mark.parametrize("lang", LANGS)
def test_the_cloud_label_says_which_cloud_figure_it_is(lang):
    value = _labels(lang)["trip_energy_source_cloud"]
    bare = value.lower().replace("leapmotor", "").replace("-", " ").strip()
    assert bare and bare not in ("cloud", "nube", "nuvem", "chmura"), (
        f"{lang}: {value!r} claims the cloud for one of two cloud figures"
    )


@pytest.mark.parametrize("lang", LANGS)
def test_the_three_labels_are_told_apart(lang):
    values = [v.lower() for v in _labels(lang).values()]
    assert len(set(values)) == 3, f"{lang}: two sources read the same: {values}"
