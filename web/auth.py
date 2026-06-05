"""OIDC authentication + a user whitelist for standalone mode.

LeapMotor Mate can lock/unlock the car, open the trunk/windows, drive the climate,
push navigation and set the charge limit — so an unauthenticated web UI on a reachable
port is "keys to the car". This module adds:

  • OIDC login (Authorization Code flow) via Authlib, against any OpenID provider.
  • A whitelist: only the identities listed in OIDC_ALLOWED_USERS may sign in.
  • A same-origin CSRF guard on every state-changing request.

Config is read from the ENVIRONMENT only (never the in-app Settings UI) so the auth
boundary is not configured through the very UI it protects.

Enforcement is STANDALONE-ONLY. When running as a Home Assistant add-on (SUPERVISOR_TOKEN
present) HA ingress already authenticates and proxies the panel, so this module makes
itself a no-op there — matching ha_client.py / run.sh, which use the same signal.

    OIDC_ISSUER          issuer base URL; discovery at {issuer}/.well-known/openid-configuration
    OIDC_CLIENT_ID       OAuth client id
    OIDC_CLIENT_SECRET   OAuth client secret
    OIDC_ALLOWED_USERS   comma-separated allow-list (matched against email / preferred_username / sub)
    OIDC_SESSION_SECRET  cookie-signing key (auto-generated if unset → sessions reset on restart)
    OIDC_SCOPES          requested scopes (default "openid email profile")
    OIDC_REDIRECT_URI    explicit callback URL (for reverse-proxy / TLS); else derived from the request
    OIDC_COOKIE_SECURE   "true" to mark the session cookie Secure (set this behind TLS); default false
"""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

log = logging.getLogger("auth")

# ── Config (read once at import) ──────────────────────────────────────────────
IN_ADDON       = bool(os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN"))
ISSUER         = (os.environ.get("OIDC_ISSUER") or "").strip().rstrip("/")
CLIENT_ID      = (os.environ.get("OIDC_CLIENT_ID") or "").strip()
CLIENT_SECRET  = (os.environ.get("OIDC_CLIENT_SECRET") or "").strip()
SCOPES         = (os.environ.get("OIDC_SCOPES") or "openid email profile").strip()
REDIRECT_URI   = (os.environ.get("OIDC_REDIRECT_URI") or "").strip()
COOKIE_SECURE  = (os.environ.get("OIDC_COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes", "on")
SESSION_SECRET = (os.environ.get("OIDC_SESSION_SECRET") or "").strip()

# Whitelist → lowercased set. Empty while OIDC is configured = fail closed (deny all).
_ALLOWED = {u.strip().lower() for u in (os.environ.get("OIDC_ALLOWED_USERS") or "").split(",") if u.strip()}

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_EXEMPT_PREFIXES = ("/auth/", "/static/")
_SESSION_MAX_AGE = 8 * 3600  # 8h — re-auth daily-ish; short enough to bound a stolen cookie

_oauth = None  # lazily built Authlib registry (only when enabled)


def is_enabled() -> bool:
    """True when OIDC credentials are present in the environment."""
    return bool(ISSUER and CLIENT_ID and CLIENT_SECRET)


def is_active() -> bool:
    """True when OIDC is both configured AND should be enforced (i.e. not an add-on)."""
    return is_enabled() and not IN_ADDON


def _identity(claims: dict) -> dict:
    """Pull a minimal, display-friendly identity out of the OIDC claims."""
    return {
        "sub":   claims.get("sub"),
        "email": claims.get("email"),
        "name":  claims.get("name") or claims.get("preferred_username") or claims.get("email") or claims.get("sub"),
    }


def _is_allowed(claims: dict) -> bool:
    """Whitelist check: any of email / preferred_username / sub in OIDC_ALLOWED_USERS."""
    if not _ALLOWED:
        return False  # configured OIDC but empty allow-list → deny everyone (fail closed)
    candidates = {
        str(claims.get(k)).strip().lower()
        for k in ("email", "preferred_username", "sub")
        if claims.get(k)
    }
    return bool(candidates & _ALLOWED)


def _build_oauth():
    global _oauth
    if _oauth is not None:
        return _oauth
    from authlib.integrations.starlette_client import OAuth  # lazy: only when enabled
    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        client_kwargs={"scope": SCOPES},
    )
    _oauth = oauth
    return _oauth


# ── CSRF: same-origin guard on unsafe methods ─────────────────────────────────
class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject state-changing requests whose Origin (or Referer) host doesn't match the
    request host. Combined with SameSite=Lax session cookies this blocks a malicious page
    from forging car commands. Absent both headers → allowed (same-origin form posts may
    omit them); cross-site requests always carry one."""

    async def dispatch(self, request, call_next):
        if request.method not in _SAFE_METHODS:
            host = request.headers.get("host")
            src = request.headers.get("origin") or request.headers.get("referer")
            if src and urlparse(src).netloc != host:
                return PlainTextResponse("CSRF check failed (cross-origin request blocked)", status_code=403)
        return await call_next(request)


# ── OIDC session guard ────────────────────────────────────────────────────────
class AuthGuardMiddleware(BaseHTTPMiddleware):
    """Require a signed-in, whitelisted session for every route except /auth/* and
    /static/*. Unauthenticated browser GETs are redirected to login; API/HTMX calls
    get a 401 so they don't render a login page into a fragment."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/healthz" or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)
        if request.session.get("user"):
            return await call_next(request)
        if request.method != "GET" or request.headers.get("hx-request"):
            return PlainTextResponse("Authentication required", status_code=401)
        # NB: request.url_for() can't be used inside BaseHTTPMiddleware (the router
        # isn't in scope yet), so build the path directly and honor any proxy root_path.
        login = (request.scope.get("root_path", "") or "").rstrip("/") + "/auth/login"
        return RedirectResponse(login)


# ── Wiring ────────────────────────────────────────────────────────────────────
def register(app, templates) -> None:
    """Install CSRF + (when enabled) OIDC session auth on the FastAPI app.

    Middleware added later wraps earlier ones, so SessionMiddleware is added LAST to sit
    outermost — making request.session available inside the auth guard. No-op in add-on
    mode (ingress handles auth)."""
    if IN_ADDON:
        log.info("Add-on mode (Supervisor token present) — relying on HA ingress for auth")
        return

    # CSRF applies in standalone whether or not OIDC is on (it's the privilege boundary
    # in open mode that the localhost bind protects; cheap belt-and-suspenders otherwise).
    app.add_middleware(CSRFMiddleware)

    if not is_enabled():
        log.warning(
            "OIDC is NOT configured — the web UI is UNAUTHENTICATED. Anyone who can reach "
            "it can control the car. Keep it bound to localhost (WEB_HOST) or behind an "
            "authenticating reverse proxy, and set OIDC_* to require login."
        )
        return

    if not _ALLOWED:
        log.warning("OIDC_ALLOWED_USERS is empty — every login will be DENIED (fail-closed). "
                    "Set it to the emails/usernames allowed to sign in.")

    secret = SESSION_SECRET
    if not secret:
        secret = secrets.token_urlsafe(32)
        log.warning("OIDC_SESSION_SECRET unset — generated an ephemeral key; sessions reset on "
                    "restart. Set OIDC_SESSION_SECRET to a stable random value.")

    _register_routes(app, templates)
    app.add_middleware(AuthGuardMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="lm_session",
        same_site="lax",
        https_only=COOKIE_SECURE,
        max_age=_SESSION_MAX_AGE,
    )
    log.info("OIDC auth enabled — issuer=%s, %d user(s) whitelisted", ISSUER, len(_ALLOWED))


def _register_routes(app, templates) -> None:
    oauth = _build_oauth()

    def _redirect_uri(request: Request) -> str:
        return REDIRECT_URI or str(request.url_for("auth_callback"))

    @app.get("/auth/login", name="auth_login")
    async def auth_login(request: Request):
        if request.session.get("user"):
            return RedirectResponse(str(request.base_url))
        return await oauth.oidc.authorize_redirect(request, _redirect_uri(request))

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        try:
            token = await oauth.oidc.authorize_access_token(request)
        except Exception as e:  # noqa: BLE001 — bad/expired code, state mismatch, etc.
            log.warning("OIDC callback failed: %s", e)
            return templates.TemplateResponse(
                request, "auth_denied.html", {"reason": "login_failed"}, status_code=400)
        claims = token.get("userinfo") or {}
        if not claims:
            try:
                claims = await oauth.oidc.userinfo(token=token)
            except Exception:  # noqa: BLE001
                claims = {}
        if not _is_allowed(claims):
            log.warning("OIDC login denied (not whitelisted): %s", _identity(claims).get("email")
                        or _identity(claims).get("sub"))
            request.session.clear()
            return templates.TemplateResponse(
                request, "auth_denied.html", {"reason": "not_allowed"}, status_code=403)
        request.session["user"] = _identity(claims)
        log.info("OIDC login: %s", request.session["user"].get("email") or request.session["user"].get("sub"))
        return RedirectResponse(str(request.base_url))

    @app.get("/auth/logout", name="auth_logout")
    async def auth_logout(request: Request):
        request.session.clear()
        return RedirectResponse(str(request.url_for("auth_login")))
