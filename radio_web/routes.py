"""Public route table and compatibility facade for the web interface.

Handlers are grouped by stable administration domain. This module retains the
public request contracts and exact table-based dispatch API used by the server.
"""

from typing import Dict, Optional, Tuple

from . import artwork, auth, static_assets, system_status, templates
from .route_adc_network import _adc_debug_get, _adc_debug_post, _network_get, _network_post
from .route_common import (
    _HTML,
    _TEXT,
    SESSION_COOKIE,
    Handler,
    Request,
    Response,
    UploadedFile,
    _clear_cookie_header,
    _ok,
    _redirect,
    _set_cookie_header,
)

__all__ = ["Request", "SESSION_COOKIE", "UploadedFile", "resolve"]
from .route_device_maintenance import (
    _backup_download_post,
    _device_get,
    _device_post,
    _device_root_password_post,
    _diagnostics_get,
    _firmware_authorize_post,
    _firmware_get,
    _firmware_status_get,
    _firmware_switch_confirm_post,
    _firmware_switch_post,
    _maintenance_confirm_post,
    _maintenance_get,
    _maintenance_post,
    _restore_post,
)
from .route_display_audio import (
    _audio_hardware_get,
    _audio_hardware_post,
    _settings_get,
    _settings_post,
)
from .route_stations_sources import _sources_get, _sources_post, _stations_get, _stations_post

# --- Handlers ---------------------------------------------------------------


def _dashboard(_req: Request) -> Response:
    return _ok(templates.dashboard(system_status.collect()))


def _now_playing(_req: Request) -> Response:
    """Return the live Now Playing fragment used by dashboard polling."""
    return _ok(templates.now_playing_fragment(system_status.player_status()))


def _artwork(req: Request) -> Response:
    """Return the active artwork selected by its constrained snapshot ID."""
    identifier = req.query.get("id", "")
    image = artwork.load(identifier)
    if image is None:
        return 404, _TEXT, "Artwork not found.", []
    content_type, body = image
    return 200, content_type, body, [("Cache-Control", "public, max-age=86400")]


def _healthz(_req: Request) -> Response:
    """Liveness probe for the web service itself (not the player)."""
    return _ok("ok", _TEXT)


def _static(req: Request) -> Response:
    """Serve one explicitly allowlisted local UI asset."""
    asset = static_assets.get(req.path)
    if asset is None:
        return 404, _HTML, templates.not_found(), []
    content_type, body = asset
    return 200, content_type, body, [("Cache-Control", "public, max-age=3600")]


def _login_get(req: Request) -> Response:
    # First-use setup takes precedence until a password exists.
    if not auth.is_configured():
        return _redirect("/setup")
    if req.session is not None:
        return _redirect("/")
    return _ok(templates.login_page())


def _login_post(req: Request) -> Response:
    if not auth.is_configured():
        return _redirect("/setup")
    limiter = req.rate_limiter
    key = req.client_ip or "unknown"
    if limiter is not None and limiter.is_blocked(key):
        page = templates.login_page(error="Too many attempts. Try again later.")
        return 429, _HTML, page, []
    password = req.form.get("password", "")
    if auth.check_password(password):
        if limiter is not None:
            limiter.register_success(key)
        assert req.sessions is not None
        session = req.sessions.create()
        return _redirect("/", [_set_cookie_header(session.token)])
    if limiter is not None:
        limiter.register_failure(key)
    return 401, _HTML, templates.login_page(error="Incorrect password."), []


def _setup_get(req: Request) -> Response:
    if auth.is_configured():
        return _redirect("/login")
    return _ok(templates.setup_page())


def _setup_post(req: Request) -> Response:
    if auth.is_configured():
        # Never allow silently overwriting an existing password without auth.
        return _redirect("/login")
    password = req.form.get("password", "")
    confirm = req.form.get("confirm", "")
    if password != confirm:
        return _ok(templates.setup_page(error="Passwords do not match."))
    try:
        auth.set_password(password)
    except ValueError as exc:
        return _ok(templates.setup_page(error=str(exc)))
    except OSError:
        # A damaged image or incorrectly owned managed-config directory should
        # produce a useful response, not terminate the request with an empty
        # connection.  Keep filesystem details out of the public page.
        return (
            500,
            _HTML,
            templates.setup_page(
                error="Unable to save the password. Check the radio configuration directory."
            ),
            [],
        )
    # Log the admin in immediately after first-use setup.
    assert req.sessions is not None
    session = req.sessions.create()
    return _redirect("/", [_set_cookie_header(session.token)])


def _logout_post(req: Request) -> Response:
    # State-changing: require a valid session and a matching CSRF token.
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    if req.sessions is not None and req.session is not None:
        req.sessions.destroy(req.session.token)
    return _redirect("/login", [_clear_cookie_header()])


ROUTES: Dict[Tuple[str, str], Handler] = {
    ("GET", "/"): _dashboard,
    ("GET", "/dashboard/now-playing"): _now_playing,
    ("GET", "/dashboard/artwork"): _artwork,
    ("GET", "/healthz"): _healthz,
    ("GET", "/static/app.css"): _static,
    ("GET", "/static/app.js"): _static,
    ("GET", "/static/radio.svg"): _static,
    ("GET", "/static/PiSonic-Logo.svg"): _static,
    ("GET", "/static/PiSonic-Logo.png"): _static,
    ("GET", "/login"): _login_get,
    ("POST", "/login"): _login_post,
    ("GET", "/setup"): _setup_get,
    ("POST", "/setup"): _setup_post,
    ("POST", "/logout"): _logout_post,
    ("GET", "/stations"): _stations_get,
    ("POST", "/stations"): _stations_post,
    ("GET", "/sources"): _sources_get,
    ("POST", "/sources"): _sources_post,
    ("GET", "/device"): _device_get,
    ("POST", "/device"): _device_post,
    ("POST", "/device/root-password"): _device_root_password_post,
    ("GET", "/settings"): _settings_get,
    ("POST", "/settings"): _settings_post,
    ("GET", "/audio-hardware"): _audio_hardware_get,
    ("POST", "/audio-hardware"): _audio_hardware_post,
    ("GET", "/debug/adc"): _adc_debug_get,
    ("POST", "/debug/adc"): _adc_debug_post,
    ("GET", "/network"): _network_get,
    ("POST", "/network"): _network_post,
    ("GET", "/maintenance"): _maintenance_get,
    ("GET", "/firmware"): _firmware_get,
    ("GET", "/firmware/status"): _firmware_status_get,
    ("POST", "/firmware/authorize"): _firmware_authorize_post,
    ("POST", "/firmware/switch/confirm"): _firmware_switch_confirm_post,
    ("POST", "/firmware/switch"): _firmware_switch_post,
    ("POST", "/maintenance"): _maintenance_post,
    ("POST", "/maintenance/confirm"): _maintenance_confirm_post,
    ("GET", "/diagnostics"): _diagnostics_get,
    ("POST", "/backup/download"): _backup_download_post,
    ("POST", "/restore"): _restore_post,
}

# Paths that exist for at least one method, used to distinguish 404 from 405.
_KNOWN_PATHS = {path for (_method, path) in ROUTES}


def resolve(req: Request) -> Response:
    """Resolve ``req`` to a response.

    Returns the matched handler's response, a 405 when the path exists but not
    for this method, or a 404 otherwise. The path is matched exactly (the
    server strips any query string first).
    """
    handler: Optional[Handler] = ROUTES.get((req.method.upper(), req.path))
    if handler is not None:
        return handler(req)
    if req.path in _KNOWN_PATHS:
        return 405, _HTML, templates.method_not_allowed(), []
    return 404, _HTML, templates.not_found(), []
