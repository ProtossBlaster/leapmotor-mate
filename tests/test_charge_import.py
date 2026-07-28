"""Bulk charge-import CSV parsing + STRICT validation (#111). One typo must never block the whole file
nor land dirty data in the DB: good lines import, bad lines come back with a reason. Pure function, no DB."""
from datetime import date, timedelta, timezone

import charge_import as ci

TODAY = date(2026, 7, 3)
ROME = timezone(timedelta(hours=2), "CEST")     # a stand-in for a real +2 zone, no tzdata needed


def _parse(text, tz=timezone.utc):
    # tz is REQUIRED by the parser on purpose (#181): a charge CSV can't be read without knowing whose
    # clock its times are on. UTC here keeps the expected strings below readable.
    return ci.parse_charge_csv(text, tz=tz, today=TODAY)


def test_valid_rows_with_header_comments_and_blanks():
    rows, errors = _parse(
        "# instructions line, ignored\n"
        "date,energy_kwh,cost,type\n"
        "2025-11-03 21:30,42.5,8.10,AC\n"
        "\n"                                          # blank line ignored
        "2026-01-15,18,9.5,DC\n"
    )
    assert errors == []
    assert len(rows) == 2
    assert rows[0] == {"started_at": "2025-11-03T21:30:00+00:00", "ended_at": None, "energy_kwh": 42.5,
                       "cost": 8.1, "charge_type": "AC", "start_soc": None, "end_soc": None}
    # no time given → noon default (no day-shift), DC preserved
    assert rows[1] == {"started_at": "2026-01-15T12:00:00+00:00", "ended_at": None, "energy_kwh": 18.0,
                       "cost": 9.5, "charge_type": "DC", "start_soc": None, "end_soc": None}


def test_optional_fields_blank():
    rows, errors = _parse("2025-05-01,30\n")           # no cost, no type
    assert errors == []
    assert rows[0]["cost"] is None
    assert rows[0]["charge_type"] == "AC"              # blank type → AC


def test_european_semicolon_csv_with_comma_decimals():
    # European Excel (IT/FR/DE): ';' separator + ',' decimal. Must parse as one number, not two cells.
    rows, errors = _parse(
        "date;energy_kwh;cost;type\n"
        "2025-05-01 08:00;30,5;8,10;AC\n"
        "2025-06-02;12;;DC\n"
    )
    assert errors == []
    assert rows[0] == {"started_at": "2025-05-01T08:00:00+00:00", "ended_at": None, "energy_kwh": 30.5,
                       "cost": 8.1, "charge_type": "AC", "start_soc": None, "end_soc": None}
    assert rows[1]["energy_kwh"] == 12.0 and rows[1]["cost"] is None and rows[1]["charge_type"] == "DC"


def test_optional_end_time_gives_duration():
    rows, errors = _parse(
        "date,energy_kwh,cost,type,start_soc,end_soc,end\n"
        "2025-11-03 23:35,42.5,8.10,AC,23,60,2025-11-04 03:42\n"   # crosses midnight
        "2025-06-02 10:00,20,,DC,,,\n"                             # no end → None
    )
    assert errors == []
    assert rows[0]["started_at"] == "2025-11-03T23:35:00+00:00" and rows[0]["ended_at"] == "2025-11-04T03:42:00+00:00"
    assert rows[1]["ended_at"] is None


def test_end_before_start_and_bad_end_rejected():
    rows, errors = _parse(
        "2025-11-03 20:00,30,,AC,,,2025-11-03 18:00\n"   # end before start
        "2025-11-04 10:00,30,,AC,,,notadate\n"           # bad end
    )
    assert rows == []
    assert len(errors) == 2
    assert "before the start" in errors[0] and "end" in errors[1]


def test_optional_soc_columns():
    rows, errors = _parse(
        "date,energy_kwh,cost,type,start_soc,end_soc\n"
        "2025-05-01,30,5,AC,23,80\n"       # both SoC
        "2025-05-02,20,,DC,,\n"            # blank SoC → None
    )
    assert errors == []
    assert rows[0]["start_soc"] == 23.0 and rows[0]["end_soc"] == 80.0
    assert rows[1]["start_soc"] is None and rows[1]["end_soc"] is None


def test_soc_out_of_range_and_nonnumeric_rejected():
    rows, errors = _parse(
        "2025-05-01,30,,AC,120,80\n"       # start_soc > 100
        "2025-05-02,30,,AC,10,pieno\n"     # end_soc not a number
    )
    assert rows == []
    assert len(errors) == 2
    assert "start_soc" in errors[0] and "end_soc" in errors[1]


def test_comma_csv_keeps_dot_decimals():
    rows, errors = _parse("2025-05-01 08:00,30.5,8.10,AC\n")   # US/UK style: ',' sep + '.' decimal
    assert errors == []
    assert rows[0]["energy_kwh"] == 30.5 and rows[0]["cost"] == 8.1


def test_bad_date_rejected():
    rows, errors = _parse("03/11/2025,42\n")
    assert rows == []
    assert len(errors) == 1 and "bad date" in errors[0]


def test_future_date_rejected():
    rows, errors = _parse("2026-07-04,42\n")           # tomorrow relative to TODAY
    assert rows == []
    assert "future" in errors[0]


def test_non_positive_and_absurd_energy_rejected():
    rows, errors = _parse("2025-05-01,0\n2025-05-02,-3\n2025-05-03,999\n")
    assert rows == []
    assert len(errors) == 3
    assert all("energy_kwh" in e for e in errors)


def test_non_numeric_energy_rejected():
    rows, errors = _parse("2025-05-01,lots\n")
    assert rows == [] and "not a number" in errors[0]


def test_negative_and_bad_cost_rejected():
    rows, errors = _parse("2025-05-01,20,-4\n2025-05-02,20,free\n")
    assert rows == []
    assert len(errors) == 2


def test_bad_type_rejected():
    rows, errors = _parse("2025-05-01,20,5,PLUG\n")
    assert rows == [] and "must be AC or DC" in errors[0]


def test_fast_hpc_map_to_dc():
    rows, _ = _parse("2025-05-01,20,5,FAST\n2025-05-02,20,5,HPC\n")
    assert [r["charge_type"] for r in rows] == ["DC", "DC"]


def test_good_and_bad_mixed_partial_import():
    rows, errors = _parse(
        "date,energy_kwh,cost,type\n"
        "2025-05-01,20,5,AC\n"          # good
        "2025-05-02,oops,5,AC\n"        # bad energy
        "2025-05-03,25,,DC\n"           # good, no cost
    )
    assert len(rows) == 2                              # the two good ones imported
    assert len(errors) == 1 and "line 3" in errors[0]  # 1-based incl. header → the bad row is line 3


def test_empty_file_and_template_only():
    assert _parse("") == ([], [])
    # feeding our own blank template back in must import nothing and error on nothing
    rows, errors = _parse(ci.TEMPLATE)
    assert rows == [] and errors == []


def test_mate_own_export_reimports_by_header_name():
    """#182: /api/export/charges.csv is a raw DB dump — first column `id`, ISO stamps carrying an
    offset. Read positionally it failed on the very first cell ('bad date id') and then on each row's
    id (4, 3, 2, 1) — exactly the five errors the reporter pasted. Mapping by header NAME closes the
    round trip; the unknown columns (id, vehicle_id, latitude, location_type…) are simply ignored."""
    export = (
        "id,vehicle_id,started_at,ended_at,start_soc,end_soc,energy_added_kwh,duration_min,"
        "latitude,longitude,charge_type,location_type,max_power_kw,cost,ac_energy_kwh\n"
        "4,1,2026-06-21T12:44:00+02:00,2026-06-21T20:58:00+02:00,29.5,65.1,10.1,494,"
        "43.5,11.0,AC,HOME,3.2,2.53,11.4\n"
        "3,1,2026-06-15T09:21:00+02:00,2026-06-15T09:51:00+02:00,30.6,90.4,17.1,30,"
        "43.3,11.2,DC,HPC,116.0,14.90,\n"
    )
    rows, errors = _parse(export)
    assert errors == []
    assert len(rows) == 2
    # the offset written in the file is the truth, not a guess: 12:44+02:00 is 10:44 UTC
    assert rows[0]["started_at"] == "2026-06-21T10:44:00+00:00"
    assert rows[0]["ended_at"] == "2026-06-21T18:58:00+00:00"
    assert rows[0]["energy_kwh"] == 10.1 and rows[0]["cost"] == 2.53
    assert rows[1]["charge_type"] == "DC" and rows[1]["start_soc"] == 30.6


def test_typed_time_is_anchored_to_the_users_zone_not_utc():
    """#181: 21:30 typed by someone on +02:00 is 19:30 UTC. Storing the 21:30 verbatim made the
    renderer — which reads a zone-less value AS UTC — put the charge at 23:30 on screen."""
    rows, errors = _parse("2025-11-03 21:30,42.5\n", tz=ROME)
    assert errors == []
    assert rows[0]["started_at"] == "2025-11-03T19:30:00+00:00"


def test_zoned_and_bare_times_can_be_mixed_in_one_file():
    # A hand-edited export: zoned start, bare end. Ordering them needs both in one frame, or Python
    # refuses to compare an aware datetime with a naive one.
    rows, errors = _parse(
        "date,energy_kwh,cost,type,start_soc,end_soc,end\n"
        "2026-06-21T12:44:00+02:00,10.1,,AC,,,2026-06-21 21:00\n", tz=ROME)
    assert errors == []
    assert rows[0]["started_at"] == "2026-06-21T10:44:00+00:00"
    assert rows[0]["ended_at"] == "2026-06-21T19:00:00+00:00"


def test_line_numbers_are_one_based_including_header():
    _, errors = _parse("date,energy_kwh\n2025-05-01,20\nbad-date,5\n")
    assert "line 3" in errors[0]                       # header=1, good=2, bad=3
