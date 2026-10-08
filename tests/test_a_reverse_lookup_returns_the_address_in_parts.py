"""A reverse lookup answers with the address in parts, under the same keys for every provider.

The four providers already sent the parts (street, number, town…); Mate kept only the one line of
text. The text is still what the Navigation page and the 🧭 buttons get, unchanged, and a keyed
provider that fails still hands over to the keyless one. The answers below are made up, in the
shape each provider sends.
"""
import geocode
import pytest

NOMINATIM = {
    "category": "amenity", "type": "cafe", "name": "Caffè Aurora",
    "display_name": "Caffè Aurora, 12, Via dei Mille, Borgo Nuovo, Torino, Piemonte, 10123, Italia",
    "address": {"amenity": "Caffè Aurora", "house_number": "12", "road": "Via dei Mille",
                "suburb": "Borgo Nuovo", "city": "Torino", "state": "Piemonte", "postcode": "10123",
                "country": "Italia", "country_code": "it"},
}
LOCATIONIQ = {**NOMINATIM, "class": "amenity"}
GEOAPIFY = {"features": [{"properties": {
    "name": "Caffè Aurora", "result_type": "amenity", "housenumber": "12", "street": "Via dei Mille",
    "suburb": "Borgo Nuovo", "city": "Torino", "postcode": "10123", "country_code": "it",
    "formatted": "Caffè Aurora, Via dei Mille 12, 10123 Torino TO, Italy"}}]}
TOMTOM = {"addresses": [{"address": {
    "streetNumber": "12", "streetName": "Via dei Mille", "municipalitySubdivision": "Borgo Nuovo",
    "municipality": "Torino", "postalCode": "10123", "countryCode": "IT",
    "freeformAddress": "Via dei Mille 12, 10123 Torino"}}]}

PARTS = {"house_number": "12", "road": "Via dei Mille", "suburb": "Borgo Nuovo", "locality": "Torino",
         "postcode": "10123", "country_code": "it"}


@pytest.fixture
def answers(monkeypatch):
    """Each provider's host answers with its sample; the URLs asked are kept."""
    by_host = {"nominatim.openstreetmap.org": NOMINATIM, "us1.locationiq.com": LOCATIONIQ,
               "api.geoapify.com": GEOAPIFY, "api.tomtom.com": TOMTOM}
    asked = []

    def fake_get(url):
        asked.append(url)
        host = url.split("/")[2]
        answer = by_host[host]
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(geocode, "_get", fake_get)
    return by_host, asked


@pytest.mark.parametrize("provider, name, text", [
    ("geoapify", "Caffè Aurora", GEOAPIFY["features"][0]["properties"]["formatted"]),
    ("locationiq", "Caffè Aurora", NOMINATIM["display_name"]),
    ("tomtom", None, TOMTOM["addresses"][0]["address"]["freeformAddress"]),
])
def test_a_keyed_provider_answers_in_parts(answers, provider, name, text):
    place = geocode._REVERSE[provider](45.07, 7.68, "key")
    assert place == {**PARTS, "name": name, "display_name": text}


def test_the_keyless_provider_answers_in_parts(answers):
    assert geocode._nominatim_reverse(45.07, 7.68) == {**PARTS, "name": "Caffè Aurora",
                                                       "display_name": NOMINATIM["display_name"]}


@pytest.mark.parametrize("provider, text", [
    ("geoapify", GEOAPIFY["features"][0]["properties"]["formatted"]),
    ("locationiq", NOMINATIM["display_name"]),
    ("tomtom", TOMTOM["addresses"][0]["address"]["freeformAddress"]),
    ("", NOMINATIM["display_name"]),
])
def test_the_text_is_the_providers_full_address_as_before(answers, provider, text):
    assert geocode.reverse_geocode(45.07, 7.68, provider, "key") == text


def test_a_keyed_provider_that_fails_hands_over_to_the_keyless_one(answers):
    by_host, asked = answers
    by_host["api.geoapify.com"] = OSError("timed out")
    assert geocode.reverse_geocode(45.07, 7.68, "geoapify", "key") == NOMINATIM["display_name"]
    assert asked[-1].startswith("https://nominatim.openstreetmap.org/")


def test_a_keyed_provider_with_nothing_there_hands_over_too(answers):
    by_host, _ = answers
    by_host["api.tomtom.com"] = {"addresses": []}
    assert geocode.reverse_geocode(45.07, 7.68, "tomtom", "key") == NOMINATIM["display_name"]


def test_nothing_anywhere_is_no_address(answers):
    by_host, _ = answers
    by_host["nominatim.openstreetmap.org"] = {"error": "Unable to geocode"}
    assert geocode.reverse_geocode(45.07, 7.68) is None


@pytest.mark.parametrize("host, answer, ask", [
    ("nominatim.openstreetmap.org",
     {"category": "military", "name": "Area 7", "display_name": "Area 7, Via dei Mille, Torino",
      "address": {"military": "Area 7", "road": "Via dei Mille", "city": "Torino"}},
     lambda: geocode._nominatim_reverse(45.07, 7.68)),
    ("api.geoapify.com",
     {"features": [{"properties": {"name": "Area 7", "result_type": "building", "street": "Via dei Mille",
                                   "city": "Torino", "formatted": "Area 7, Via dei Mille, Torino"}}]},
     lambda: geocode._REVERSE["geoapify"](45.07, 7.68, "key")),
])
def test_only_a_place_someone_goes_to_gives_its_name(answers, host, answer, ask):
    """A military area, a building or a street is named in OpenStreetMap too; a trip does not end at it."""
    by_host, _ = answers
    by_host[host] = answer
    assert ask()["name"] is None


def test_a_name_that_is_only_the_street_is_not_a_name(answers):
    by_host, _ = answers
    by_host["nominatim.openstreetmap.org"] = {
        "display_name": "Via dei Mille, Torino",
        "address": {"amenity": "Via dei Mille", "road": "Via dei Mille", "city": "Torino"}}
    assert geocode._nominatim_reverse(45.07, 7.68)["name"] is None
