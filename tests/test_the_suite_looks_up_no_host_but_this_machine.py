"""The suite looks up no host but this machine (tests/no_internet.py): in its own process, and in a
web server a test spawns through it. A lookup that got out would be answered or not depending on
the network, and the code that makes it swallows the failure, so nothing else would notice."""
import pathlib
import socket
import subprocess
import sys
import urllib.request

import paho.mqtt.client as mqtt
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_a_lookup_of_another_host_is_refused():
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("api.open-meteo.com", 443)


@pytest.mark.parametrize("connect", [
    lambda host: urllib.request.urlopen(f"https://{host}/", timeout=5),
    lambda host: socket.create_connection((host, 443), timeout=5),
    lambda host: mqtt.Client(mqtt.CallbackAPIVersion.VERSION2).connect(host, 1883),
], ids=["urllib", "create_connection", "paho"])
def test_the_ways_mate_connects_ask_it_first(connect):
    # .invalid never resolves, so a lookup that went around the guard fails as well, but not in its words
    with pytest.raises(OSError, match="the suite looks up only this machine"):
        connect("example.invalid")


def test_this_machine_still_resolves():
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo("127.0.0.1", 80)


def test_a_name_that_only_looks_like_this_machine_is_refused(monkeypatch):
    asked = []
    monkeypatch.setattr("no_internet._getaddrinfo", lambda host, *a, **kw: asked.append(host))
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("127.example.com", 443)       # a name, which can point anywhere
    socket.getaddrinfo("127.0.0.2", 443)
    assert asked == ["127.0.0.2"]


def test_a_script_run_through_it_is_refused_too(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import socket, sys\n"
        "try:\n"
        "    socket.getaddrinfo('api.github.com', 443)\n"
        "    print('reached')\n"
        "except socket.gaierror:\n"
        "    print('refused')\n"
        "print(__name__, sys.argv[1:], sys.path[0])\n")
    out = subprocess.run([sys.executable, str(ROOT / "tests" / "no_internet.py"), str(probe), "x"],
                         capture_output=True, text=True, check=True).stdout
    # run as `python probe.py x` would be: as __main__, with its own arguments and directory
    assert out.splitlines() == ["refused", f"__main__ ['x'] {tmp_path}"]
