"""The served() helper survives losing its port between choosing it and using it.

It picks a free port by binding to 0, reading the number, and closing the socket — then the child
process binds it. Anything else on the machine may take it in that window, and in a full suite run
something does: measured twice in six runs, the browser tests erroring with
`[Errno 48] error while attempting to bind on address ('0.0.0.0', 56496): address already in use`.
Nothing about the test that failed was wrong; it lost a race to a neighbour.

Here the race is not waited for, it is staged: the port served() is about to hand the child is
already occupied. The helper has to find another one rather than fail the test that asked for a
server.
"""
import socket

import pytest

pytest.importorskip("fastapi", reason="web/main.py needs the production web dependencies")
pytest.importorskip("uvicorn", reason="the page has to be SERVED")
import urllib.request

from web_in_a_browser import seed_database, served

VIN = "LVINPORTRACE00001"


@pytest.fixture
def data(tmp_path):
    db = tmp_path / "store.db"
    seed_database(db, VIN)
    return tmp_path, db


def test_a_server_comes_up_even_when_the_first_port_is_taken(data, monkeypatch):
    d, db = data
    real = socket.socket
    squatters = []

    class Squatter(socket.socket):
        """The first port served() picks is occupied the moment it lets go of it."""
        def __exit__(self, *exc):
            if len(squatters) < 1:
                port = self.getsockname()[1]
                super().__exit__(*exc)
                s = real(socket.AF_INET, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.listen(1)
                squatters.append(s)
                return
            super().__exit__(*exc)

    monkeypatch.setattr(socket, "socket", Squatter)
    try:
        with served(d, db) as url:
            monkeypatch.undo()
            assert urllib.request.urlopen(url + "/", timeout=10).status == 200
    finally:
        for s in squatters:
            s.close()
    assert squatters, "the race was never staged — the test proves nothing"
