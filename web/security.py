"""Browser-side hardening for the standalone deployment.

Mate's API can unlock the doors, so the interesting attacker isn't someone on the network —
it's a page in the *victim's own browser*, which is already inside the network. Two shapes of
that attack, and they need different answers:

  * cross-site submission — evil.example posts a form at http://192.168.1.x:4000/api/command/...
    The browser sends it; the reply is unreadable to the attacker, but the door already opened.
    Answered by `origin_allowed()`: a mutating request that declares a foreign origin is refused.
  * clickjacking — evil.example frames Mate invisibly and lines a "claim your prize" button up
    with the real Unlock. The click happens INSIDE Mate, same-origin, so no origin check can
    see it. Answered by refusing to be framed at all (`SECURITY_HEADERS`).

Both are skipped as a Home Assistant add-on: there the browser talks to HA and HA proxies to
us, so every request legitimately carries HA's origin and HA frames the panel on purpose.
Keeping either rule on would break the add-on UI for everyone while protecting nobody — ingress
already authenticates, and it isn't reachable cross-origin without an HA session.
"""
import os
from urllib.parse import urlsplit

# Requests that can change something. GET/HEAD/OPTIONS are not checked: they must stay reachable
# for the browser to render anything, and Mate has no state-changing GET.
MUTATING = frozenset(("POST", "PUT", "PATCH", "DELETE"))

# Paths that must answer before any UI exists — a monitoring probe has no origin to declare.
EXEMPT_PREFIXES = ("/static/", "/healthz")

SECURITY_HEADERS = {
    # No framing at all: clickjacking needs a frame, and Mate is never legitimately embedded
    # outside add-on mode (where this whole module is skipped). X-Frame-Options for older
    # browsers, frame-ancestors for the ones that have moved on.
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    # Don't let a browser second-guess a Content-Type and run something as script.
    "X-Content-Type-Options": "nosniff",
    # A Mate URL can carry a trip id; don't hand it to whatever a user clicks through to.
    "Referrer-Policy": "same-origin",
}


def is_addon() -> bool:
    """Running under the HA Supervisor (add-on), where ingress owns the browser relationship."""
    return bool(os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN"))


def trusted_origins() -> set[str]:
    """Extra origins to accept, comma-separated in MATE_TRUSTED_ORIGINS. The escape hatch for a
    reverse proxy that rewrites Host, where the browser's (correct) origin can't match what we
    see. Values are compared as scheme://host[:port], e.g. https://mate.example.com."""
    raw = os.environ.get("MATE_TRUSTED_ORIGINS", "")
    return {_host_of(o.strip()) or o.strip().lower()
            for o in raw.split(",") if o.strip()}


def _host_of(value: str) -> str:
    """host[:port] of a URL or origin string; '' if it isn't one.

    Scheme is deliberately NOT compared. Behind an HTTPS reverse proxy the browser's origin is
    https://… while we usually see http://… — uvicorn only trusts X-Forwarded-Proto from
    127.0.0.1, so a proxy on a Docker network doesn't correct it — and comparing schemes would
    hand every one of those users a 403 on their own UI. What the check is for is the host: the
    attack is a page on some *other* site, and it cannot spell our host whatever scheme it uses.
    Anyone who can serve our exact host over http already owns the connection.
    """
    if not value:
        return ""
    parts = urlsplit(value)
    if not parts.scheme or not parts.netloc:
        return ""
    return parts.netloc.lower()


def origin_allowed(method: str, path: str, headers, base_url: str) -> bool:
    """May this request change something?

    A declared origin must match the host the request arrived on (or be explicitly trusted).
    An ABSENT origin passes: browsers always declare one on a cross-origin submission, which is
    the attack; what has none is curl, a script, an HA automation — refusing those would break
    real automation without stopping anything. `Referer` is the fallback for the handful of
    browser cases that omit `Origin` on same-origin navigations.
    """
    if is_addon() or method.upper() not in MUTATING:
        return True
    if any(path.startswith(p) for p in EXEMPT_PREFIXES):
        return True
    declared = _host_of(headers.get("origin", "")) or _host_of(headers.get("referer", ""))
    if not declared:
        return True
    return declared == _host_of(base_url) or declared in trusted_origins()
