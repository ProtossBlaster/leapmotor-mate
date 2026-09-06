"""Beta #13: slow bundle preparation must not stall the web event loop."""
import asyncio
import threading

import pytest

pytest.importorskip("fastapi")
import main


class ProbeComplete(Exception):
    pass


def test_export_leaves_event_loop_responsive(monkeypatch):
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    released = threading.Event()

    def slow_read():
        responsive = released.wait(.3)
        raise ProbeComplete(responsive)

    monkeypatch.setattr(main.db_reader, "get_raw_signal_rows", slow_read)

    async def run():
        asyncio.get_running_loop().call_later(.02, released.set)
        with pytest.raises(ProbeComplete) as result:
            await main.research_export()
        assert result.value.args == (True,)

    asyncio.run(run())


def test_export_keeps_its_vehicle_when_sidebar_selection_changes(tmp_path, monkeypatch):
    import db
    import db_reader
    database = db.Database(str(tmp_path / "vehicles.db"))
    monkeypatch.setattr(db_reader, "DB_PATH", str(tmp_path / "vehicles.db"))
    database.ensure_vehicle("FIRST", "B10", 2026)
    database.ensure_vehicle("SECOND", "B10", 2026)
    db_reader.set_setting("active_vehicle_vin", "FIRST")
    first_id = db_reader._current_vehicle_id()
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)

    def switch_during_export():
        from types import SimpleNamespace
        client = main.command_client.LeapmotorSession()
        client._vehicles = [SimpleNamespace(vin="FIRST"), SimpleNamespace(vin="SECOND")]
        db_reader.set_setting("active_vehicle_vin", "SECOND")
        assert client._target().vin == "FIRST"
        raise ProbeComplete(db_reader._current_vehicle_id())

    monkeypatch.setattr(db_reader, "get_raw_signal_rows", switch_during_export)
    with pytest.raises(ProbeComplete) as result:
        asyncio.run(main.research_export())
    assert result.value.args == (first_id,)
    assert db_reader._current_vehicle_id() != first_id
    database._conn.close()


def test_duplicate_export_is_rejected_and_error_releases_slot(monkeypatch):
    monkeypatch.setattr(main.research, "research_enabled", lambda: True)
    entered, release = threading.Event(), threading.Event()

    def slow_read():
        entered.set()
        release.wait(.5)
        raise ProbeComplete()

    monkeypatch.setattr(main.db_reader, "get_raw_signal_rows", slow_read)

    async def run():
        first = asyncio.create_task(main.research_export())
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            second = await main.research_export()
            assert second.status_code == 409
        finally:
            release.set()
            with pytest.raises(ProbeComplete):
                await first
        with pytest.raises(ProbeComplete):
            await main.research_export()

    asyncio.run(run())
