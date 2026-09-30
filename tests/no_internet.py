"""The suite looks up no host but this machine.

Mate looks things up on its own, in threads no test sees: the update check on every page the web app
renders, the elevation and weather sweep behind a trip page, a closed charge's station note. None of
the tests needs an answer, so a lookup of any host but this one fails here as it would offline,
instead of spending the allowance GitHub and Open-Meteo give this machine's address on every run.

It replaces socket.getaddrinfo, which every connection the suite makes by name goes through today;
socket.gethostbyname() or a connect() given a name would go around it. The code that looks up
swallows the failure, so a lookup no test stubs stays harmless here, not visible.

conftest.py imports it before anything else. A web server a test spawns runs through it too:
`python tests/no_internet.py web/main.py` runs main.py as `python web/main.py` would; a process
started any other way is not covered.
"""
import ipaddress
import os
import runpy
import socket
import sys

_getaddrinfo = socket.getaddrinfo


def _is_this_machine(name):
    if name in (None, "", "localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False            # a name, "127.example.com" too, can point anywhere
    return address.is_loopback or address.is_unspecified


def _this_machine_only(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if not _is_this_machine(name):
        raise socket.gaierror(socket.EAI_NONAME, f"{name}: the suite looks up only this machine")
    return _getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = _this_machine_only

if __name__ == "__main__":
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.path[0] = os.path.dirname(os.path.abspath(script))
    runpy.run_path(script, run_name="__main__")
