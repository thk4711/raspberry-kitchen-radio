"""Web route handlers for stations sources."""

from typing import Dict, List, Optional

from . import (
    actions,
    auth,
    logo_store,
    sources_store,
    stations_store,
    system_status,
    templates,
    validators,
)
from .route_common import (
    _HTML,
    Request,
    Response,
    _ok,
    _redirect,
)

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


# --- Source feature flags (Phase 6)---

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
