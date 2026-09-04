"""Browser-side hardening for the standalone deployment.

Mate's API can unlock the doors, so the interesting attacker isn't someone on the network —
it's a page in the *victim's own browser*, which is already inside the network. Two shapes of
that attack, and they need different answers — and, on purpose, different environment variables,
because they are different permissions:

  * cross-site submission — evil.example posts a form at http://192.168.1.x:4000/api/command/...
    The browser sends it; the reply is unreadable to the attacker, but the door already opened.
    Answered by `origin_allowed()`: a mutating request that declares a foreign origin is refused,
    except one named in MATE_TRUSTED_ORIGINS — e.g. a reverse proxy that rewrites Host.
  * clickjacking — evil.example frames Mate invisibly and lines a "claim your prize" button up
    with the real Unlock. The click happens INSIDE Mate, same-origin, so no origin check can
    see it. Answered by refusing to be framed at all, except by an origin you've explicitly
    named in MATE_FRAME_ANCESTORS — e.g. your own Home Assistant dashboard (`security_headers()`).

Naming an origin in MATE_TRUSTED_ORIGINS does NOT grant it framing, and naming one in
MATE_FRAME_ANCESTORS does NOT grant it write access — trusting a reverse proxy host is not the
same decision as trusting every page that origin might ever serve (a HACS card, a Lovelace
resource, an add-on) with `/api/command/unlock`. Set whichever one you mean; set both if you
mean both.

Both are skipped as a Home Assistant add-on: there the browser talks to HA and HA proxies to
us, so every request legitimately carries HA's origin and HA frames the panel on purpose.
Keeping either rule on would break the add-on UI for everyone while protecting nobody — ingress
already authenticates, and it isn't reachable cross-origin without an HA session.
"""
import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

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

# scheme://host[:port] and NOTHING else — no userinfo, no path/query/fragment, no wildcard, no
# whitespace, ASCII only. Anchored on both ends, so a trailing ";" or a second directive smuggled
# in after the origin fails the match rather than riding along into the header. ASCII-only also
# means a value that passes this can never fail the latin-1 encode Starlette does when it writes
# the header out — that failure mode (a 500 on every response, /healthz included) is exactly what
# unrestricted values risked before. Host is a DNS name/IPv4, or an IPv6 literal in brackets
# (e.g. https://[::1]:4000) — CSP host-source syntax requires the brackets there too.
_FRAME_ANCESTOR_RE = re.compile(
    r"^https?://"
    r"(?:"
    r"\[[0-9a-fA-F:]+\]"
    r"|[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
    r")"
    r"(?::[0-9]{1,5})?$"
)


# Entries already reported. `security_headers()` runs on EVERY response, so a warning written
# straight into it repeats with the traffic — Mate's own polling alone is thousands of requests a
# day, and one typo would bury everything else in the very log the message sends you to read.
# Warned per bad value instead: the set is bounded by what the operator put in the variable.
_warned: set[str] = set()


def _valid_frame_ancestors() -> list[str]:
    """MATE_FRAME_ANCESTORS, split, stripped, and filtered to entries that are exactly
    scheme://host[:port]. A single trailing "/" is forgiven before checking — that's the bare
    form a browser's address bar shows for an origin with no path, and rejecting it outright
    would silently break the single most likely way to paste one in. Anything else malformed
    (an actual path, a wildcard, a stray ";") is dropped and logged rather than passed through,
    so a typo degrades to 'nothing configured, and it says why in the log', never to 'whatever
    slipped past the regex'."""
    raw = os.environ.get("MATE_FRAME_ANCESTORS", "")
    seen: list[str] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        candidate = entry
        if not _FRAME_ANCESTOR_RE.match(candidate) and candidate.endswith("/"):
            candidate = candidate[:-1]
        if _FRAME_ANCESTOR_RE.match(candidate):
            if candidate not in seen:
                seen.append(candidate)
        elif entry not in _warned:
            _warned.add(entry)
            log.warning("MATE_FRAME_ANCESTORS: rejected %r — must be exactly "
                        "scheme://host[:port], no path/query/wildcard", entry)
    return seen


def security_headers() -> dict[str, str]:
    """SECURITY_HEADERS, relaxed to name specific trusted embedders when MATE_FRAME_ANCESTORS is
    set — e.g. a Home Assistant dashboard you control, embedding Mate as a panel. X-Frame-Options
    has no multi-origin form (ALLOW-FROM is dead in every current browser), so once a trusted
    embedder is configured we rely on CSP frame-ancestors alone and drop X-Frame-Options, rather
    than send a value that would just re-block the very origin we were asked to trust. Only the
    named origins are listed — 'self' is not added for free, since nobody asked for Mate to frame
    itself. No valid MATE_FRAME_ANCESTORS (unset, or every entry rejected) means no change: still
    'none', still DENY."""
    ancestors = _valid_frame_ancestors()
    if not ancestors:
        return dict(SECURITY_HEADERS)
    headers = dict(SECURITY_HEADERS)
    del headers["X-Frame-Options"]
    headers["Content-Security-Policy"] = "frame-ancestors " + " ".join(ancestors)
    return headers


def is_addon() -> bool:
    """Running under the HA Supervisor (add-on), where ingress owns the browser relationship."""
    return bool(os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN"))


def _raw_trusted_origins() -> list[str]:
    """MATE_TRUSTED_ORIGINS, split and stripped, scheme intact — e.g. ['https://mate.example.com'].
    Kept separate from trusted_origins() because that one deliberately drops the scheme for the
    origin-match comparison. Unlike MATE_FRAME_ANCESTORS, these values never reach a response
    header — they are only ever compared for equality against `Origin`/`Referer` — so a malformed
    entry is harmless noise rather than a header-injection risk, and isn't validated here."""
    raw = os.environ.get("MATE_TRUSTED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def trusted_origins() -> set[str]:
    """Extra origins to accept, comma-separated in MATE_TRUSTED_ORIGINS. The escape hatch for a
    reverse proxy that rewrites Host, where the browser's (correct) origin can't match what we
    see. Values are compared as scheme://host[:port], e.g. https://mate.example.com."""
    return {_host_of(o) or o.lower() for o in _raw_trusted_origins()}


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
