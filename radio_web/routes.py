"""Explicit route table for the web administration interface.

Routing is a **table lookup**, never a chain of URL conditionals. Handlers
receive a :class:`Request` context (method,
path, parsed form, the authenticated session, and shared auth state) and return
a :class:`Response`. The server writes the response verbatim, including any
``Set-Cookie`` and ``Location`` headers.

Phase 3 exposed a GET-only read-only dashboard. Phase 4 adds the authentication
flow — first-use setup, login, logout — plus the machinery (sessions, CSRF,
rate limiting) that gates the mutating routes introduced in later phases. The
read-only dashboard stays public.

Adding a route means adding one entry to :data:`ROUTES`. Unknown paths resolve
to a 404 and disallowed methods to a 405.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Union

from . import (
    actions,
    adc_store,
    artwork,
    audio_hardware_store,
    audio_store,
    auth,
    data_backup,
    device_store,
    diagnostics,
    display_store,
    equalizer_store,
    firmware_authorization,
    firmware_tracking,
    logo_store,
    network_store,
    sources_store,
    static_assets,
    stations_store,
    system_status,
    templates,
    usb_audio_store,
    validators,
)
from .auth import Session

# A response: (status, content_type, body, extra_headers). ``body`` is text for
# HTML/plain responses and raw ``bytes`` for binary downloads (diagnostics).
# ``extra_headers`` is a list of (name, value) pairs for cookies, redirects, etc.
Response = Tuple[int, str, Union[str, bytes], List[Tuple[str, str]]]


@dataclass
class UploadedFile:
    """One uploaded form file; routes use only its bounded in-memory bytes."""

    filename: str
    content_type: str
    data: bytes


_HTML = "text/html; charset=utf-8"
_TEXT = "text/plain; charset=utf-8"
_JSON = "application/json; charset=utf-8"

# Cookie name for the session token.
SESSION_COOKIE = "radio_session"


@dataclass
class Request:
    """Per-request context handed to a route handler.

    Attributes:
        method: Upper-case HTTP method.
        path: Path component only (query string stripped by the server).
        form: Parsed ``application/x-www-form-urlencoded`` fields (last value
            wins). Empty for GET.
        query: Parsed query-string fields (last value wins). Used by GET
            handlers to surface a PRG flash message (e.g. ``?msg=saved``).
        client_ip: Remote address, used as the rate-limit key.
        session: The validated session, or ``None`` when unauthenticated.
        sessions: The shared session store (so login/logout can mutate it).
        rate_limiter: The shared login rate limiter.
    """

    method: str
    path: str
    form: Dict[str, str] = field(default_factory=dict)
    files: Dict[str, UploadedFile] = field(default_factory=dict)
    query: Dict[str, str] = field(default_factory=dict)
    client_ip: str = ""
    session: Optional[Session] = None
    sessions: Optional[auth.SessionStore] = None
    rate_limiter: Optional[auth.RateLimiter] = None


Handler = Callable[[Request], Response]


def _ok(body: str, content_type: str = _HTML) -> Response:
    return 200, content_type, body, []


def _redirect(location: str, extra_headers: Optional[List[Tuple[str, str]]] = None) -> Response:
    # 303 See Other: the Post/Redirect/Get idiom.
    headers = list(extra_headers or [])
    headers.append(("Location", location))
    body = f'<p>Redirecting to <a href="{location}">{location}</a>.</p>'
    return 303, _HTML, body, headers


def _set_cookie_header(token: str) -> Tuple[str, str]:
    # HttpOnly + SameSite=Strict. No Secure flag in v1
    # because the prototype serves plain HTTP on the trusted LAN.
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/",
    )


def _clear_cookie_header() -> Tuple[str, str]:
    return (
        "Set-Cookie",
        f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0",
    )


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


# --- Station editing (Phase 5) ----------------------------------------------

# PRG flash messages: a short whitelist of status codes carried in the redirect
# query string (?msg=...) so a success survives the Post/Redirect/Get without
# server-side session state. Anything not listed renders no banner.
_STATION_FLASHES = {
    "saved": "Preset saved.",
    "moved": "Preset order updated.",
    "applied": "Radio restarted; presets reloaded.",
    "restored": "Built-in stations restored.",
    "logo-uploaded": "Logo uploaded. Choose it from a preset dropdown and save that preset.",
}


def _stations_flash(req: Request) -> Optional[str]:
    return _STATION_FLASHES.get(req.query.get("msg", ""))


def _stations_get(req: Request) -> Response:
    # Editing presets is a privileged, authenticated operation.
    if not auth.require_auth(req.session):
        return _redirect("/login")
    assert req.session is not None
    slots = stations_store.load_stations()
    page = templates.stations_page(
        slots, req.session.csrf_token, logo_store.available_logos(), message=_stations_flash(req)
    )
    return _ok(page)


def _read_submitted_slots(req: Request) -> List[Dict[str, str]]:
    """Merge the single edited slot from the form into the persisted list.

    Each preset card posts one slot (its ``index`` plus name/url/logo). We load
    the current six slots and overlay the submitted one, so a save/test/move
    keeps the other five intact without hidden fields for every preset.
    """
    slots = stations_store.load_stations()
    try:
        index = int(req.form.get("index", ""))
    except ValueError:
        return slots
    if 0 <= index < len(slots):
        slots[index] = {
            "name": req.form.get("name", ""),
            "url": req.form.get("url", ""),
            "logo": req.form.get("logo", ""),
        }
    return slots


def _stations_page_response(
    req: Request,
    slots: List[Dict[str, str]],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.stations_page(
        slots, req.session.csrf_token, logo_store.available_logos(), message=message, error=error
    )
    return status, _HTML, page, []


def _stations_post(req: Request) -> Response:
    # State-changing: require a valid session and a matching CSRF token.
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")

    if op == "restore":
        stations_store.restore_builtin()
        return _redirect("/stations?msg=restored")

    if op == "apply":
        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/stations?msg=applied")
        return _stations_page_response(req, stations_store.load_stations(), error=detail)

    if op == "upload_logo":
        uploaded = req.files.get("logo_file")
        if uploaded is None:
            return _stations_page_response(
                req, stations_store.load_stations(), error="Choose an image file."
            )
        try:
            logo_store.save_upload(uploaded.data, uploaded.filename)
        except ValueError as exc:
            return _stations_page_response(req, stations_store.load_stations(), error=str(exc))
        return _redirect("/stations?msg=logo-uploaded")

    slots = _read_submitted_slots(req)

    if op in ("move_up", "move_down"):
        return _stations_move(req, slots, op)
    if op == "test":
        return _stations_test(req, slots)
    if op == "save":
        return _stations_save(req, slots)
    # Unknown op: re-render without changes rather than mutate anything.
    return _stations_page_response(req, slots, error="Unknown action.")


def _stations_move(req: Request, slots: List[Dict[str, str]], op: str) -> Response:
    try:
        index = int(req.form.get("index", ""))
    except ValueError:
        return _stations_page_response(req, slots, error="Invalid preset.")
    target = index - 1 if op == "move_up" else index + 1
    if not (0 <= index < len(slots)) or not (0 <= target < len(slots)):
        return _stations_page_response(req, slots, error="Cannot move preset.")
    # Validate the visible edits before persisting the reordered list.
    try:
        slots = stations_store.validate_slots(slots)
    except ValueError as exc:
        return _stations_page_response(req, slots, error=str(exc))
    slots[index], slots[target] = slots[target], slots[index]
    try:
        stations_store.save_stations(slots)
    except ValueError as exc:
        return _stations_page_response(req, slots, error=str(exc))
    return _redirect("/stations?msg=moved")


def _stations_test(req: Request, slots: List[Dict[str, str]]) -> Response:
    try:
        index = int(req.form.get("index", ""))
    except ValueError:
        index = -1
    url = slots[index]["url"] if 0 <= index < len(slots) else req.form.get("url", "")
    ok, detail = stations_store.test_stream(url)
    if ok:
        return _stations_page_response(req, slots, message=detail)
    return _stations_page_response(req, slots, error=detail)


def _stations_save(req: Request, slots: List[Dict[str, str]]) -> Response:
    try:
        stations_store.save_stations(slots)
    except ValueError as exc:
        return _stations_page_response(req, slots, error=str(exc))
    return _redirect("/stations?msg=saved")


# --- Source feature flags (Phase 6) -----------------------------------------

_SOURCE_FLASHES = {
    "saved": "Sources saved.",
    "applied": "Radio restarted; sources reloaded.",
    "restored": "All sources re-enabled.",
}


def _sources_flash(req: Request) -> Optional[str]:
    return _SOURCE_FLASHES.get(req.query.get("msg", ""))


def _sources_page_response(
    req: Request,
    flags: Dict[str, bool],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.sources_page(
        flags,
        sources_store.SOURCE_KEYS,
        system_status.player_status(),
        req.session.csrf_token,
        message=message,
        error=error,
    )
    return status, _HTML, page, []


def _sources_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _sources_page_response(req, sources_store.load_sources(), message=_sources_flash(req))


def _sources_post(req: Request) -> Response:
    # State-changing: require a valid session and a matching CSRF token.
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")

    if op == "restore":
        sources_store.restore_builtin()
        return _redirect("/sources?msg=restored")

    # Strip the non-flag control fields before whitelisting the checkboxes.
    checkboxes = {key: value for key, value in req.form.items() if key not in ("op", "csrf_token")}
    try:
        flags = validators.validate_source_flags(checkboxes)
    except ValueError as exc:
        return _sources_page_response(req, sources_store.load_sources(), error=str(exc))

    if op == "apply":
        try:
            sources_store.save_sources(flags)
        except ValueError as exc:
            return _sources_page_response(req, flags, error=str(exc))
        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/sources?msg=applied")
        return _sources_page_response(req, sources_store.load_sources(), error=detail)

    if op == "save":
        try:
            sources_store.save_sources(flags)
        except ValueError as exc:
            return _sources_page_response(req, flags, error=str(exc))
        return _redirect("/sources?msg=saved")

    # Unknown op: re-render current state without mutating anything.
    return _sources_page_response(req, sources_store.load_sources(), error="Unknown action.")


# --- Device settings + maintenance (Phase 7) --------------------------------

_DEVICE_FLASHES = {
    "saved": "Device settings saved.",
    "restored": "Device settings reset to defaults.",
}

_MAINTENANCE_FLASHES = {
    "restart_radio": "Radio restarted.",
    "restart_mpd": "MPD restarted.",
}

# Human labels for the two confirmed destructive actions.
_CONFIRM_LABELS = {"reboot": "Reboot device", "shutdown": "Shut down device"}


def _device_flash(req: Request) -> Optional[str]:
    return _DEVICE_FLASHES.get(req.query.get("msg", ""))


def _device_page_response(
    req: Request,
    settings: Dict[str, str],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.device_page(
        settings,
        req.session.csrf_token,
        hostname_locked=device_store.sd_card_hostname_locked(),
        timezones=device_store.available_timezones(),
        message=message,
        error=error,
    )
    return status, _HTML, page, []


def _device_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _device_page_response(req, device_store.load_device(), message=_device_flash(req))


def _validate_device_form(submitted: Dict[str, str], locked: bool) -> Dict[str, str]:
    cleaned: Dict[str, str] = {
        "name": "",
        "timezone": "",
        "ntp_server": "",
        "ssh_enabled": "false",
    }
    if locked:
        # The field is read-only; preserve whatever is currently stored.
        cleaned["name"] = device_store.load_device().get("name", "")
    elif submitted["name"]:
        cleaned["name"] = validators.validate_device_name(submitted["name"])
    if submitted["timezone"]:
        cleaned["timezone"] = validators.validate_timezone(submitted["timezone"])
    if submitted["ntp_server"]:
        cleaned["ntp_server"] = validators.validate_ntp_server(submitted["ntp_server"])
    if submitted["ssh_enabled"] not in ("true", "false"):
        raise ValueError("Invalid SSH setting.")
    cleaned["ssh_enabled"] = submitted["ssh_enabled"]
    return cleaned


def _apply_device(cleaned: Dict[str, str], locked: bool) -> List[str]:
    errors: List[str] = []
    if not locked and cleaned.get("name"):
        ok, detail = actions.run_action("set_hostname", name=cleaned["name"])
        if not ok:
            errors.append(detail)
    if cleaned.get("timezone") or cleaned.get("ntp_server"):
        ok, detail = actions.run_action(
            "set_time",
            timezone=cleaned.get("timezone", ""),
            ntp_server=cleaned.get("ntp_server", ""),
        )
        if not ok:
            errors.append(detail)
    if cleaned.get("ssh_enabled"):
        ok, detail = actions.run_action("restart_ssh")
        if not ok:
            errors.append(detail)
    return errors


def _device_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")

    if op == "restore":
        device_store.restore_builtin()
        ok, detail = actions.run_action("restart_ssh")
        if not ok:
            return _device_page_response(req, device_store.load_device(), error=detail)
        return _redirect("/device?msg=restored")

    if op != "save":
        return _device_page_response(req, device_store.load_device(), error="Unknown action.")

    locked = device_store.sd_card_hostname_locked()
    submitted = {
        "name": req.form.get("name", "").strip(),
        "timezone": req.form.get("timezone", "").strip(),
        "ntp_server": req.form.get("ntp_server", "").strip(),
        "ssh_enabled": req.form.get("ssh_enabled", "false").strip(),
    }
    try:
        cleaned = _validate_device_form(submitted, locked)
    except ValueError as exc:
        return _device_page_response(req, submitted, error=str(exc))

    device_store.save_device(cleaned)
    errors = _apply_device(cleaned, locked)
    if errors:
        return _device_page_response(req, cleaned, error=" ".join(errors))
    return _redirect("/device?msg=saved")


def _maintenance_flash(req: Request) -> Optional[str]:
    return _MAINTENANCE_FLASHES.get(req.query.get("msg", ""))


def _maintenance_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    assert req.session is not None
    ok, detail, firmware = actions.query_firmware_status()
    page = templates.maintenance_page(
        req.session.csrf_token,
        firmware=firmware,
        message=_maintenance_flash(req),
        error=None if ok else detail,
    )
    return _ok(page)


def _firmware_get(req: Request) -> Response:
    """Retain old bookmarks while keeping firmware management on Maintenance."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _redirect("/maintenance")


def _firmware_status_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return 401, _JSON, json.dumps({"ok": False, "message": "Authentication required."}), []
    ok, detail, status = actions.query_firmware_status()
    payload = {"ok": ok, "message": detail, "status": status}
    return 200, _JSON, json.dumps(payload, separators=(",", ":")), [("Cache-Control", "no-store")]


def _firmware_authorize_post(req: Request) -> Response:
    """Fresh-authenticate a wizard and issue a short-lived one-upload grant."""
    if not auth.require_auth(req.session):
        return 401, _JSON, json.dumps({"ok": False, "message": "Authentication required."}), []
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _JSON, json.dumps({"ok": False, "message": "The CSRF token is invalid."}), []
    assert req.session is not None
    limiter = req.rate_limiter
    key = req.client_ip or "unknown"
    if limiter is not None and limiter.is_blocked(key):
        return (
            429,
            _JSON,
            json.dumps({"ok": False, "message": "Too many attempts. Try again later."}),
            [],
        )
    if not auth.check_password(req.form.get("password", "")):
        if limiter is not None:
            limiter.register_failure(key)
        return 401, _JSON, json.dumps({"ok": False, "message": "Incorrect password."}), []
    if limiter is not None:
        limiter.register_success(key)
    tracking_token = firmware_tracking.create()
    grant = firmware_authorization.issue(req.session.token, tracking_token)
    payload = {
        "ok": True,
        "message": "Password confirmed.",
        "grant": grant,
        "tracking_token": tracking_token,
    }
    return 200, _JSON, json.dumps(payload, separators=(",", ":")), [("Cache-Control", "no-store")]


def _firmware_switch_confirm_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    ok, detail, status = actions.query_firmware_status()
    if not ok or not status.get("switch_allowed"):
        reason = (
            detail
            if not ok
            else str(status.get("switch_reason", "Firmware switching unavailable."))
        )
        return (
            409,
            _HTML,
            templates.maintenance_page(req.session.csrf_token, firmware=status, error=reason),
            [],
        )
    return _ok(templates.firmware_switch_confirm_page(req.session.csrf_token, status))


def _firmware_switch_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    ok, detail, status = actions.query_firmware_status()
    if not ok or not status.get("switch_allowed"):
        reason = (
            detail
            if not ok
            else str(status.get("switch_reason", "Firmware switching unavailable."))
        )
        return (
            409,
            _HTML,
            templates.maintenance_page(req.session.csrf_token, firmware=status, error=reason),
            [],
        )
    if req.form.get("confirmed") != "1":
        return _ok(templates.firmware_switch_confirm_page(req.session.csrf_token, status))
    if not auth.check_password(req.form.get("password", "")):
        return (
            401,
            _HTML,
            templates.firmware_switch_confirm_page(
                req.session.csrf_token, status, error="Incorrect password."
            ),
            [],
        )
    switched, switch_detail = actions.run_action("prepare_rollback")
    return (
        200 if switched else 409,
        _HTML,
        templates.maintenance_page(
            req.session.csrf_token,
            firmware=status,
            message=switch_detail if switched else None,
            error=None if switched else switch_detail,
        ),
        [],
    )


def _maintenance_confirm_post(req: Request) -> Response:
    """Show the fresh-auth confirmation page for a destructive action."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    action = req.form.get("action", "")
    if action not in _CONFIRM_LABELS:
        return _redirect("/maintenance")
    assert req.session is not None
    page = templates.confirm_page(req.session.csrf_token, action, _CONFIRM_LABELS[action])
    return _ok(page)


def _maintenance_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    action = req.form.get("action", "")

    if action in ("restart_radio", "restart_mpd"):
        ok, detail = actions.run_action(action)
        if ok:
            return _redirect(f"/maintenance?msg={action}")
        return _ok(templates.maintenance_page(req.session.csrf_token, error=detail))

    if action in _CONFIRM_LABELS:
        if req.form.get("confirmed") != "1":
            return _maintenance_confirm_post(req)
        if not auth.check_password(req.form.get("password", "")):
            page = templates.confirm_page(
                req.session.csrf_token,
                action,
                _CONFIRM_LABELS[action],
                error="Incorrect password.",
            )
            return 401, _HTML, page, []
        ok, detail = actions.run_action(action)
        return _ok(
            templates.maintenance_page(req.session.csrf_token, message=detail)
            if ok
            else templates.maintenance_page(req.session.csrf_token, error=detail)
        )

    return _ok(templates.maintenance_page(req.session.csrf_token, error="Unknown action."))


def _diagnostics_get(req: Request) -> Response:
    """Auth-gated download of the volatile diagnostics bundle (tar.gz)."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    filename, data = diagnostics.build_bundle()
    headers = [("Content-Disposition", f'attachment; filename="{filename}"')]
    return 200, "application/gzip", data, headers


def _backup_download_post(req: Request) -> Response:
    """Fresh-authenticated export of the fixed helper-created archive."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    if not auth.check_password(req.form.get("password", "")):
        assert req.session is not None
        return 401, _HTML, templates.maintenance_page(
            req.session.csrf_token, error="Incorrect password."
        ), []
    ok, detail = actions.run_action("create_data_backup")
    if not ok:
        assert req.session is not None
        return 500, _HTML, templates.maintenance_page(req.session.csrf_token, error=detail), []
    try:
        data = data_backup.BACKUP_PATH.read_bytes()
    except OSError:
        assert req.session is not None
        return 500, _HTML, templates.maintenance_page(
            req.session.csrf_token, error="The completed backup could not be read."
        ), []
    filename = time.strftime("kitchen-radio-backup-%Y%m%d-%H%M%S.tar.gz")
    return 200, "application/gzip", data, [
        ("Content-Disposition", f'attachment; filename="{filename}"'),
        ("Cache-Control", "no-store"),
    ]


def _restore_post(req: Request) -> Response:
    """Authenticate, stage, validate, and apply one confirmed backup file."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    if req.form.get("confirmed") != "1":
        return 400, _HTML, templates.maintenance_page(
            req.session.csrf_token, error="Confirm that the current settings may be replaced."
        ), []
    if not auth.check_password(req.form.get("password", "")):
        return 401, _HTML, templates.maintenance_page(
            req.session.csrf_token, error="Incorrect password."
        ), []
    upload = req.files.get("backup_file")
    if upload is None:
        return 400, _HTML, templates.maintenance_page(
            req.session.csrf_token, error="Choose a backup archive."
        ), []
    try:
        data_backup.stage_restore(upload.data)
    except data_backup.BackupError as exc:
        return 400, _HTML, templates.maintenance_page(req.session.csrf_token, error=str(exc)), []
    ok, detail = actions.run_action("inspect_data_restore")
    if not ok:
        actions.run_action("cancel_data_restore")
        return 400, _HTML, templates.maintenance_page(req.session.csrf_token, error=detail), []
    ok, detail = actions.run_action("apply_data_restore")
    return (200 if ok else 409), _HTML, templates.maintenance_page(
        req.session.csrf_token, message=detail if ok else None, error=None if ok else detail
    ), []


# --- Display & audio settings (Area A) --------------------------------------

_SETTINGS_FLASHES = {
    "saved": "Settings saved.",
    "applied": "Radio restarted; settings applied.",
    "restored": "Display settings reset to defaults.",
}

# Theme presets surfaced in the <select>, as (value, label) pairs.
_THEME_PRESET_OPTIONS = [
    ("default", "Default"),
    ("high_contrast", "High contrast"),
    ("dim_night", "Dim night"),
    ("no_animations", "No animations"),
]


def _settings_flash(req: Request) -> Optional[str]:
    return _SETTINGS_FLASHES.get(req.query.get("msg", ""))


def _settings_page_response(
    req: Request,
    display: Dict[str, str],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.settings_page(
        display,
        req.session.csrf_token,
        presets=_THEME_PRESET_OPTIONS,
        message=message,
        error=error,
    )
    return status, _HTML, page, []


def _settings_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _settings_page_response(
        req,
        display_store.load_display(),
        message=_settings_flash(req),
    )


def _settings_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")

    if op == "restore":
        display_store.restore_builtin()
        return _redirect("/settings?msg=restored")

    # The form carries the display [ui] fields only; audio settings live on the
    # Audio page (/audio-hardware).
    display_form = {
        key: req.form.get(key, "")
        for key in (
            "theme_preset",
            "animations",
            "idle_timeout",
            "crossfade_ms",
            "clock_size",
            "osd_duration",
            "toast_duration",
        )
    }
    # An unchecked checkbox is simply absent from the POST body.
    display_form["animations"] = req.form.get("animations", "")

    try:
        display_store.save_display(display_form)
    except ValueError as exc:
        return _settings_page_response(
            req,
            display_store.load_display(),
            error=str(exc),
        )

    if op == "apply":
        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/settings?msg=applied")
        return _settings_page_response(
            req,
            display_store.load_display(),
            error=detail,
        )
    if op == "save":
        return _redirect("/settings?msg=saved")
    return _settings_page_response(
        req,
        display_store.load_display(),
        error="Unknown action.",
    )


# --- Audio: hardware, USB, volume, and parametric-EQ settings ----------------

_AUDIO_HW_FLASHES = {
    "restored": "Audio settings reset to the defaults. Reboot to apply the sound card.",
    "unchanged": "No audio settings changed.",
    "applied": "Audio settings applied; the player restarted.",
    "volume_saved": "Maximum volume saved.",
    "volume_applied": "Radio restarted; maximum volume applied.",
    "equalizer_saved": "Equalizer settings saved. Apply them to hear the change.",
    "equalizer_applied": "Equalizer applied to every audio source.",
    "equalizer_restored": "Equalizer reset to flat and disabled.",
}


def _audio_hardware_flash(req: Request) -> Optional[str]:
    return _AUDIO_HW_FLASHES.get(req.query.get("msg", ""))


def _equalizer_result(ajax: bool, msg_key: str) -> Response:
    """Return the EQ success response: JSON for fetch, a PRG redirect otherwise.

    ``msg_key`` is one of the ``_AUDIO_HW_FLASHES`` equalizer keys. The AJAX
    branch returns ``{"ok": true, "message": ...}`` so the page can update its
    banner in place without reloading (keeping the user's scroll position). The
    no-JS branch keeps the original Post/Redirect/Get behaviour unchanged.
    """
    if ajax:
        body = json.dumps({"ok": True, "message": _AUDIO_HW_FLASHES[msg_key]})
        return 200, _JSON, body, []
    return _redirect(f"/audio-hardware?msg={msg_key}")


def _equalizer_error(req: Request, detail: str, ajax: bool) -> Response:
    """Return the EQ failure response: JSON for fetch, a re-rendered page otherwise."""
    if ajax:
        return 200, _JSON, json.dumps({"ok": False, "message": detail}), []
    return _audio_hardware_page_response(
        req, audio_hardware_store.load_profile(), error=detail
    )



def _audio_hardware_profiles() -> List[Tuple[str, str, str, str, str, str]]:
    """Return declarative profile details for the picker, in catalog order."""
    return [
        (
            profile_id,
            profile.label,
            profile.kind,
            profile.family,
            profile.support_level,
            profile.compatibility_note,
        )
        for profile_id, profile in audio_hardware_store.PROFILES.items()
    ]


def _audio_hardware_page_response(
    req: Request,
    current: str,
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.audio_hardware_page(
        _audio_hardware_profiles(),
        current,
        audio_store.load_audio()["max_volume"],
        usb_audio_store.load_mode(),
        equalizer_store.load_equalizer(),
        req.session.csrf_token,
        message=message,
        error=error,
    )
    return status, _HTML, page, []


def _audio_hardware_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _audio_hardware_page_response(
        req,
        audio_hardware_store.load_profile(),
        message=_audio_hardware_flash(req),
    )


def _audio_volume_post(req: Request) -> Response:
    """Handle the maximum-volume form (save or apply-with-radio-restart)."""
    op = req.form.get("op", "")
    # Validate and persist the max-volume cap, preserving the managed mixer/amp
    # written by the sound-card apply (audio_store keeps them when absent).
    try:
        audio_store.save_audio({"max_volume": req.form.get("max_volume", "")})
    except ValueError as exc:
        return _audio_hardware_page_response(
            req, audio_hardware_store.load_profile(), error=str(exc)
        )
    if op == "apply_volume":
        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/audio-hardware?msg=volume_applied")
        return _audio_hardware_page_response(req, audio_hardware_store.load_profile(), error=detail)
    return _redirect("/audio-hardware?msg=volume_saved")


def _audio_hardware_post(req: Request) -> Response:
    # State-changing: require a valid session and a matching CSRF token.
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    op = req.form.get("op", "")

    # The EQ card submits either as a classic form (no-JS: Post/Redirect/Get) or
    # via fetch (progressive enhancement). The client marks the latter with a
    # hidden ``ajax=1`` field so we can answer with a small JSON body and let the
    # page stay put — the user keeps their scroll position at the EQ. Request
    # headers are not plumbed through to route handlers, so this form field is the
    # clean detection point.
    ajax = req.form.get("ajax") == "1"

    if op in ("save_equalizer", "apply_equalizer"):
        try:
            equalizer_store.save_equalizer(req.form)
        except ValueError as exc:
            return _equalizer_error(req, str(exc), ajax)
        if op == "save_equalizer":
            return _equalizer_result(ajax, "equalizer_saved")
        ok, detail = actions.run_action("apply_equalizer")
        if ok:
            return _equalizer_result(ajax, "equalizer_applied")
        return _equalizer_error(req, detail, ajax)

    if op == "restore_equalizer":
        equalizer_store.restore_builtin()
        ok, detail = actions.run_action("apply_equalizer")
        if ok:
            return _equalizer_result(ajax, "equalizer_restored")
        return _equalizer_error(req, detail, ajax)

    if op == "apply_all":
        current_profile = audio_hardware_store.load_profile()
        current_usb_mode = usb_audio_store.load_mode()
        current_volume = audio_store.load_audio()["max_volume"]
        try:
            profile_id = validators.validate_audio_profile(req.form.get("profile", ""))
            usb_mode = usb_audio_store.validate_mode(req.form.get("usb_audio_mode", ""))
            max_volume = audio_store.validate_settings(
                {"max_volume": req.form.get("max_volume", "")}
            )["max_volume"]
        except ValueError as exc:
            return _audio_hardware_page_response(req, current_profile, error=str(exc))

        profile_changed = profile_id != current_profile
        usb_mode_changed = usb_mode != current_usb_mode
        volume_changed = max_volume != current_volume
        if not any((profile_changed, usb_mode_changed, volume_changed)):
            return _redirect("/audio-hardware?msg=unchanged")

        detail = ""
        if profile_changed:
            ok, detail = actions.run_action("set_audio_hardware", profile=profile_id)
            if not ok:
                return _audio_hardware_page_response(req, current_profile, error=detail)
        try:
            if profile_changed:
                audio_hardware_store.save_profile(profile_id)
            if usb_mode_changed:
                usb_audio_store.save_mode(usb_mode)
            if volume_changed:
                audio_store.save_audio({"max_volume": max_volume})
        except (OSError, ValueError) as exc:
            return _audio_hardware_page_response(req, current_profile, error=str(exc))

        if profile_changed or usb_mode_changed:
            label = audio_hardware_store.PROFILES[profile_id].label
            return _ok(
                templates.audio_hardware_applied_page(
                    label,
                    req.session.csrf_token,
                    detail=detail or None,
                    usb_mode_changed=usb_mode_changed,
                )
            )

        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/audio-hardware?msg=applied")
        return _audio_hardware_page_response(req, profile_id, error=detail)

    if op in ("save_volume", "apply_volume"):
        return _audio_volume_post(req)

    if op == "save_usb_mode":
        try:
            usb_audio_store.save_mode(req.form.get("usb_audio_mode", ""))
        except ValueError as exc:
            return _audio_hardware_page_response(
                req, audio_hardware_store.load_profile(), error=str(exc)
            )
        return _ok(
            templates.audio_hardware_applied_page(
                audio_hardware_store.PROFILES[audio_hardware_store.load_profile()].label,
                req.session.csrf_token,
                detail="USB Audio mode saved. Reboot the radio and reconnect the USB host before using the new mode.",
            )
        )

    if op == "restore":
        # Restore is a real card apply, not merely deletion of the UI override:
        # config.txt, ALSA, MPD and modules must all return to the headphone
        # profile before the managed selection/audio files are removed.
        ok, detail = actions.run_action(
            "set_audio_hardware", profile=audio_hardware_store.DEFAULT_PROFILE
        )
        if not ok:
            return _audio_hardware_page_response(
                req, audio_hardware_store.load_profile(), error=detail
            )
        audio_hardware_store.restore_builtin()
        audio_store.restore_builtin()
        usb_audio_store.restore_builtin()
        return _redirect("/audio-hardware?msg=restored")

    if op != "apply":
        return _audio_hardware_page_response(
            req, audio_hardware_store.load_profile(), error="Unknown action."
        )

    # Validate the submitted profile id (web-layer check; the helper re-validates).
    try:
        profile_id = validators.validate_audio_profile(req.form.get("profile", ""))
    except ValueError as exc:
        return _audio_hardware_page_response(
            req, audio_hardware_store.load_profile(), error=str(exc)
        )

    # Ask the privileged helper to apply the complete selection first. Persist
    # the web-visible profile only after all privileged writes succeed, so a
    # helper failure cannot leave the UI claiming that an unapplied card is active.
    ok, detail = actions.run_action("set_audio_hardware", profile=profile_id)
    if not ok:
        return _audio_hardware_page_response(req, audio_hardware_store.load_profile(), error=detail)
    try:
        audio_hardware_store.save_profile(profile_id)
    except ValueError as exc:
        return _audio_hardware_page_response(
            req, audio_hardware_store.load_profile(), error=str(exc)
        )

    # Reboot-level change: show the "Reboot required" confirmation.
    label = audio_hardware_store.PROFILES[profile_id].label
    return _ok(templates.audio_hardware_applied_page(label, req.session.csrf_token, detail=detail))


# --- ADC diagnostics and calibration ---------------------------------------

_ADC_FLASHES = {
    "saved": "ADC calibration saved. Restart the radio to apply it.",
    "applied": "ADC calibration saved and the radio restarted.",
    "restored": "Built-in ADC calibration restored. Restart the radio to apply it.",
}


def _adc_page_response(
    req: Request,
    settings: Dict[str, str],
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> Response:
    assert req.session is not None
    return _ok(
        templates.adc_debug_page(
            settings,
            req.session.csrf_token,
            message=message,
            error=error,
        )
    )


def _adc_debug_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _adc_page_response(
        req,
        adc_store.load_adc(),
        message=_ADC_FLASHES.get(req.query.get("msg", "")),
    )


def _adc_debug_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")
    if op == "restore":
        adc_store.restore_builtin()
        return _redirect("/debug/adc?msg=restored")
    submitted = {key: req.form.get(key, "") for key in adc_store.DEFAULTS}
    try:
        adc_store.save_adc(submitted)
    except ValueError as exc:
        return _adc_page_response(req, submitted, error=str(exc))
    if op == "apply":
        ok, detail = actions.run_action("restart_radio")
        if ok:
            return _redirect("/debug/adc?msg=applied")
        return _adc_page_response(req, adc_store.load_adc(), error=detail)
    if op == "save":
        return _redirect("/debug/adc?msg=saved")
    return _adc_page_response(req, adc_store.load_adc(), error="Unknown action.")


# --- Network / WiFi (Area C) -------------------------------------------------

_NETWORK_FLASHES = {
    "trying": "Trying the new WiFi settings — confirm below once reconnected.",
    "confirmed": "WiFi settings confirmed.",
    "reverted": "Reverted to the previous WiFi settings.",
}


def _network_flash(req: Request) -> Optional[str]:
    return _NETWORK_FLASHES.get(req.query.get("msg", ""))


def _network_page_response(
    req: Request,
    *,
    form: Optional[Dict[str, str]] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    status: int = 200,
) -> Response:
    assert req.session is not None
    page = templates.network_page(
        network_store.current_status(),
        req.session.csrf_token,
        form=form,
        message=message,
        error=error,
    )
    return status, _HTML, page, []


def _network_get(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    return _network_page_response(req, message=_network_flash(req))


def _network_post(req: Request) -> Response:
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    op = req.form.get("op", "")

    if op == "confirm":
        ok, detail = actions.run_action("wifi_confirm")
        if ok:
            return _redirect("/network?msg=confirmed")
        return _network_page_response(req, error=detail)

    if op == "rollback":
        ok, detail = actions.run_action("wifi_rollback")
        if ok:
            return _redirect("/network?msg=reverted")
        return _network_page_response(req, error=detail)

    if op == "apply":
        submitted = {
            key: req.form.get(key, "")
            for key in (
                "ssid",
                "psk",
                "country",
                "ip_mode",
                "ip_address",
                "ip_prefix",
                "ip_gateway",
                "ip_dns",
            )
        }
        try:
            cleaned = network_store.validate_wifi_form(submitted)
        except ValueError as exc:
            return _network_page_response(req, form=submitted, error=str(exc))
        ok, detail = actions.run_action("set_wifi", **cleaned)
        if ok:
            return _network_page_response(req, form=submitted, message=detail)
        return _network_page_response(req, form=submitted, error=detail)

    return _network_page_response(req, error="Unknown action.")


# (method, path) -> handler. Methods are upper-case; paths are exact matches.
ROUTES: Dict[Tuple[str, str], Handler] = {
    ("GET", "/"): _dashboard,
    ("GET", "/dashboard/now-playing"): _now_playing,
    ("GET", "/dashboard/artwork"): _artwork,
    ("GET", "/healthz"): _healthz,
    ("GET", "/static/app.css"): _static,
    ("GET", "/static/app.js"): _static,
    ("GET", "/static/radio.svg"): _static,
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
