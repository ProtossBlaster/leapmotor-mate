"""A frame without a charge level is not a reading, for the web as for the poller.

Since 1.21.4 the poller refuses a status that carries no usable SoC — neither `100003` nor `1204`, or
a SoC of 0 beside a battery range above 5 km — as no live data: `client.get_status` raises
`EmptyStatusError` and no position is stored. The web stores positions too, for the Refresh button and
for the check after a command, and its writer never learned the rule: `save_fresh_signals` kept
`sigf("100003") or sigf("1204")`, unchanged since 1.0.0, and stored 0 % for a SoC the car did not
send. Found in a bundle on #67: a position at 0 % that the poller's log has no line for.

One rule for the two writers of `positions.soc`, as for the range (#365): it lives in
`capability_profile`, one copy per process, and both writers read it.
"""
import sqlite3
from types import SimpleNamespace

import pytest

import client
import db as D
import db_reader

VIN = "TESTVIN"

NOT_READINGS = [
    {"1010": 0, "1318": 3628, "3260": 300},          # no charge level at all
    {"1010": 0},                                     # nor a range
    {"1204": None, "3260": 300},                     # sent, as null
    {"1204": 0, "3260": 300},                        # 0 % beside 300 km: a partial read
    {"100003": "0.0", "1204": 62, "3260": 300},      # the precise SoC is the one the rule reads
]

READINGS = [
    ({"1204": 62, "1318": 3628, "3260": 300}, 62.0),
    ({"100003": "61.5", "1204": 62}, 61.5),
    ({"1204": 0, "3260": 0}, 0.0),                   # an empty battery is a reading
    ({"1204": 0}, 0.0),
    ({"1204": 0, "3260": 5}, 0.0),                   # 5 km is not above 5
    ({"1204": 3, "3260": 12}, 3.0),
]


@pytest.fixture
def stored_soc(tmp_path, monkeypatch):
    """The web's database, and what its positions hold."""
    path = str(tmp_path / "web.db")
    D.Database(path).ensure_vehicle(VIN, "C10")
    monkeypatch.setattr(db_reader, "DB_PATH", path)

    def read():
        con = sqlite3.connect(path)
        try:
            return [r[0] for r in con.execute("SELECT soc FROM positions ORDER BY id")]
        finally:
            con.close()
    return read


@pytest.mark.parametrize("sig", NOT_READINGS)
def test_the_web_stores_nothing_from_a_frame_without_a_charge_level(sig, stored_soc):
    db_reader.save_fresh_signals(dict(sig))
    assert stored_soc() == []


@pytest.mark.parametrize("sig, soc", READINGS)
def test_the_web_stores_a_reading_with_its_charge_level(sig, soc, stored_soc):
    db_reader.save_fresh_signals(dict(sig))
    assert stored_soc() == [soc]


def _poller(sig, monkeypatch):
    import session_share
    monkeypatch.setattr(session_share, "ensure_account_cert", lambda api: True)
    poller = object.__new__(client.LeapmotorMateClient)
    poller._api, poller._named_mode_logged = None, False
    poller._vehicle = SimpleNamespace(vin=VIN, car_type="C10")
    poller._raw_status = lambda vehicle=None: {"data": {"signal": dict(sig)}}
    return poller


@pytest.mark.parametrize("sig", NOT_READINGS)
def test_the_poller_refuses_the_same_frames(sig, monkeypatch):
    with pytest.raises(client.EmptyStatusError):
        _poller(sig, monkeypatch).get_status()


@pytest.mark.parametrize("sig, soc", READINGS)
def test_the_poller_reads_the_same_charge_level(sig, soc, monkeypatch):
    assert _poller(sig, monkeypatch).get_status().soc == soc
