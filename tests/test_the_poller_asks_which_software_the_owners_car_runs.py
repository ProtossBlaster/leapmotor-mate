"""The poller asks the cloud which software the owner's car runs.

The vehicle-update endpoint answers the account that owns the car with the installed version and
its install time, and — while a campaign is open — the version waiting, its release notes and its
packages. An account the car is only shared with is refused with code 40 (checked on a B10, 25 and
28.09.2026). So the poller asks per car, only for a car the account owns, once in six hours, and
keeps the answer in a per-car setting for the Overview, the bundle and Home Assistant.

What must not happen: the packages of a waiting update come with signed download links. They stay
in the client; nothing of them reaches a setting or a log line.
"""
import json
import logging
import types

import client as C
import db as D
from poll_cycle_fixture import VIN, poller_main

PM = poller_main("poller_main_software")

SIGNED = "https://ota-cdn.example.invalid/pkg/LP-TBOX_V3.42.1.icsw.encrypt?Expires=1&OSSAccessKeyId=K&Signature=S"

# The answer of an up-to-date car, as the endpoint gave it on 28.09.2026: no `newVersion` key at all.
UP_TO_DATE = {"currentVersion": {"time": 1790224725000, "generalVersion": "3.41.30", "logJsonMultiLang": []}}

# A campaign, in the documented shape: notes per language, one package per controller.
CAMPAIGN = {
    "currentVersion": {"time": 1790224725, "generalVersion": "3.41.30", "logJsonMultiLang": []},
    "newVersion": {
        "generalVersion": "3.42.1", "requestVersionId": 77, "state": 1,
        "logJsonMultiLang": [
            {"language": "zh-CN", "data": [{"title": "更新", "content": "……"}]},
            {"language": "en-US", "data": [{"title": "Improvements", "content": "Charging is smoother."},
                                           {"title": "Fixes", "content": "Fewer false alarms."}]},
            {"language": "pl-PL", "data": [{"title": "Ulepszenia", "content": "Ładowanie jest płynniejsze."}]},
            {"language": "de", "data": [{"title": "Verbesserungen", "content": "Laden ist gleichmäßiger."}]},
        ],
        "updateItemList": [
            {"product": "LP-TBOX", "softversion": "1.2.3", "fileName": "a.icsw.encrypt", "fileSize": 3 * 2**30,
             "md5": "m", "sha256": "s", "sign": "sig", "downloadURL": SIGNED, "downloadURLFE": SIGNED},
            {"product": "LP-VIU", "softversion": "4.5.6", "fileName": "b.bin.encrypt", "fileSize": 2**29,
             "md5": "m", "sha256": "s", "sign": "sig", "downloadURL": SIGNED, "downloadURLFE": SIGNED},
        ],
    },
}


# ── the view the client keeps of an answer ────────────────────────────────────

def test_an_up_to_date_car_has_the_same_installed_and_latest_version():
    v = C._software_view(UP_TO_DATE, "en")
    assert v["state"] == "ok"
    assert v["installed"] == v["latest"] == "3.41.30"
    assert v["installed_at_ms"] == 1790224725000
    assert v["notes"] == "" and v["size_bytes"] is None


def test_a_waiting_update_brings_its_version_notes_and_size():
    v = C._software_view(CAMPAIGN, "en")
    assert v["installed"] == "3.41.30" and v["latest"] == "3.42.1"
    assert v["notes"] == "Improvements\nCharging is smoother.\nFixes\nFewer false alarms."
    assert v["size_bytes"] == 3 * 2**30 + 2**29
    assert v["installed_at_ms"] == 1790224725000, "seconds and milliseconds give the same instant"


def test_the_notes_come_in_the_language_mate_speaks_or_the_nearest():
    """As the official app interprets the list: the exact code, then the same language under
    another country, then English, then whatever comes first."""
    notes = CAMPAIGN["newVersion"]["logJsonMultiLang"]
    assert C._release_notes(notes, "pl-PL").startswith("Ulepszenia")
    assert C._release_notes(notes, "pl").startswith("Ulepszenia"), "the same language, another country"
    assert C._release_notes(notes, "de-DE").startswith("Verbesserungen")
    assert C._release_notes(notes, "fr").startswith("Improvements"), "English when the language is missing"
    assert C._release_notes([{"language": "zh-CN", "data": [{"title": "更新", "content": ""}]}], "fr") == "更新"
    assert C._release_notes([], "en") == "" and C._release_notes(None, "en") == ""


def test_nothing_of_the_packages_leaves_the_view():
    """Signed links are a download for whoever holds them. The view carries versions, notes and a
    size — and no string of a package."""
    dumped = json.dumps(C._software_view(CAMPAIGN, "en"))
    for secret in ("Signature", "OSSAccessKeyId", "ota-cdn", "icsw", "sha256", "LP-TBOX"):
        assert secret not in dumped, secret


# ── the client, in front of the cloud ─────────────────────────────────────────

def _client(api):
    c = C.LeapmotorMateClient.__new__(C.LeapmotorMateClient)
    c._api = api
    c._vehicle = types.SimpleNamespace(vin=VIN)
    return c


def _refusal(*codes):
    err = RuntimeError(f"New API rejected request: HTTP 200, code {list(codes)!r}")
    err.api_codes = tuple(codes)
    return err


def test_code_40_is_the_account_not_owning_the_car():
    def read(vin):
        raise _refusal(40, 40)
    assert _client(types.SimpleNamespace(get_software_version=read)).get_software_version() == {"state": "refused"}


def test_any_other_failure_is_no_answer_so_the_caller_keeps_what_it_had():
    def read(vin):
        raise RuntimeError("cloud down")
    assert _client(types.SimpleNamespace(get_software_version=read)).get_software_version() is None
    assert _client(types.SimpleNamespace()).get_software_version() is None, "the bundled SDK has no such read"


def test_the_client_asks_about_the_car_it_is_given():
    seen = []
    api = types.SimpleNamespace(get_software_version=lambda vin: seen.append(vin) or UP_TO_DATE)
    c = _client(api)
    assert c.get_software_version(types.SimpleNamespace(vin="LFZB10OTHER000002"))["installed"] == "3.41.30"
    assert seen == ["LFZB10OTHER000002"]


# ── the poller, once in six hours, per car the account owns ───────────────────

class _Vehicle:
    def __init__(self, vin=VIN, shared=False):
        self.vin, self.car_type, self.year, self.abilities, self.is_shared = vin, "B10", 2025, None, shared


class _Cloud:
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def get_software_version(self, vehicle, language):
        self.asked.append((vehicle.vin, language))
        return self.answers.pop(0)


def _setup(tmp_path, monkeypatch, *vehicles):
    db = D.Database(str(tmp_path / "poll.db"))
    clock = {"t": 1_790_000_000.0}
    monkeypatch.setattr(PM.time, "time", lambda: clock["t"])
    PM._last_software_check.clear()
    ctxs = [PM.VehicleContext(db, v, db.ensure_vehicle(v.vin, v.car_type)) for v in vehicles]
    return db, clock, ctxs


def _stored(db, vin=VIN):
    return json.loads(db.get_setting(f"software_{vin.lower()}", "") or "null")


def test_the_answer_is_kept_per_car_and_asked_once_in_six_hours(tmp_path, monkeypatch):
    db, clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle())
    db.set_setting("language", "pl")
    cloud = _Cloud([C._software_view(CAMPAIGN, "pl"), C._software_view(UP_TO_DATE, "pl")])
    PM._maybe_check_software(db, cloud, ctx)
    kept = _stored(db)
    assert kept["state"] == "ok" and kept["installed"] == "3.41.30" and kept["latest"] == "3.42.1"
    assert kept["notes"].startswith("Ulepszenia") and kept["checked_at"].startswith("2026-")
    assert cloud.asked == [(VIN, "pl")], "asked in the language Mate speaks"
    clock["t"] += 3600
    PM._maybe_check_software(db, cloud, ctx)
    assert len(cloud.asked) == 1, "an hour later the cached answer stands"
    clock["t"] += 6 * 3600
    PM._maybe_check_software(db, cloud, ctx)
    assert len(cloud.asked) == 2 and _stored(db)["latest"] == "3.41.30"


def test_a_shared_car_is_never_asked_about_and_says_so(tmp_path, monkeypatch):
    db, _clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle(shared=True))
    cloud = _Cloud([C._software_view(UP_TO_DATE, "en")])
    PM._maybe_check_software(db, cloud, ctx)
    assert cloud.asked == []
    assert _stored(db)["state"] == "shared", "the screens say why there is no version"


def test_an_answer_from_the_owners_account_does_not_outlive_the_move_to_a_shared_one(tmp_path, monkeypatch):
    """The setting is per car. Mate first set up on the owner's account, then moved to an account
    the car is shared with — the setup the README recommends — would otherwise show the old
    version as current for ever, with "up to date" beside it."""
    db, _clock, (owned,) = _setup(tmp_path, monkeypatch, _Vehicle())
    PM._maybe_check_software(db, _Cloud([C._software_view(UP_TO_DATE, "en")]), owned)
    assert _stored(db)["installed"] == "3.41.30"
    PM._last_software_check.clear()                     # a restart on the other account
    shared = PM.VehicleContext(db, _Vehicle(shared=True), owned.vehicle_id)
    PM._maybe_check_software(db, _Cloud([]), shared)
    assert _stored(db)["state"] == "shared" and "installed" not in _stored(db)


def test_a_refusal_for_a_car_listed_as_owned_is_kept_apart_from_sharing(tmp_path, monkeypatch, caplog):
    """Code 40 for a car the account lists as its own is not "the car is shared": the screens must
    not claim a sharing that is not there."""
    db, _clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle())
    with caplog.at_level(logging.INFO):
        PM._maybe_check_software(db, _Cloud([{"state": "refused"}]), ctx)
    assert _stored(db)["state"] == "refused" and "installed" not in _stored(db)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Software version")]
    assert lines == [f"Software version of {VIN[-6:]}: not told — the cloud refused (code 40)"]


def test_a_shared_car_says_so_in_the_log(tmp_path, monkeypatch, caplog):
    db, _clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle(shared=True))
    with caplog.at_level(logging.INFO):
        PM._maybe_check_software(db, _Cloud([]), ctx)
    assert "the car is shared with this account" in caplog.text


def test_no_answer_leaves_the_previous_one_alone(tmp_path, monkeypatch):
    db, clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle())
    PM._maybe_check_software(db, _Cloud([C._software_view(UP_TO_DATE, "en")]), ctx)
    clock["t"] += 7 * 3600
    PM._maybe_check_software(db, _Cloud([None]), ctx)
    assert _stored(db)["installed"] == "3.41.30"


def test_two_cars_keep_two_answers(tmp_path, monkeypatch):
    db, _clock, (one, two) = _setup(tmp_path, monkeypatch, _Vehicle(), _Vehicle("LFZB10OTHER000002"))
    cloud = _Cloud([C._software_view(UP_TO_DATE, "en"), C._software_view(CAMPAIGN, "en")])
    PM._maybe_check_software(db, cloud, one)
    PM._maybe_check_software(db, cloud, two)
    assert _stored(db, VIN)["latest"] == "3.41.30"
    assert _stored(db, "LFZB10OTHER000002")["latest"] == "3.42.1"


def test_the_version_is_logged_when_it_changes_and_not_every_time(tmp_path, monkeypatch, caplog):
    db, clock, (ctx,) = _setup(tmp_path, monkeypatch, _Vehicle())
    cloud = _Cloud([C._software_view(UP_TO_DATE, "en")] * 2 + [C._software_view(CAMPAIGN, "en")])
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            PM._maybe_check_software(db, cloud, ctx)
            clock["t"] += 7 * 3600
    lines = [r.getMessage() for r in caplog.records if "Software version" in r.getMessage()]
    assert lines == [f"Software version of {VIN[-6:]}: 3.41.30, up to date",
                     f"Software version of {VIN[-6:]}: 3.41.30 installed, 3.42.1 waiting"]
    assert "Signature" not in caplog.text and "ota-cdn" not in caplog.text
