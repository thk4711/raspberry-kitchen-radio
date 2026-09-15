"""Web route handlers for device maintenance."""

import json
import time
from typing import Dict, List, Optional

from . import (
    actions,
    auth,
    data_backup,
    device_store,
    diagnostics,
    firmware_authorization,
    firmware_tracking,
    templates,
    validators,
)
from .route_common import (
    _HTML,
    _JSON,
    Request,
    Response,
    _ok,
    _redirect,
)

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
    if submitted["ssh_enabled"] == "true" and not device_store.root_password_is_provisioned():
        raise ValueError("Set a unique root_password in radio-config.txt before enabling SSH.")
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
        return (
            401,
            _HTML,
            templates.maintenance_page(req.session.csrf_token, error="Incorrect password."),
            [],
        )
    ok, detail = actions.run_action("create_data_backup")
    if not ok:
        assert req.session is not None
        return 500, _HTML, templates.maintenance_page(req.session.csrf_token, error=detail), []
    try:
        data = data_backup.BACKUP_PATH.read_bytes()
    except OSError:
        assert req.session is not None
        return (
            500,
            _HTML,
            templates.maintenance_page(
                req.session.csrf_token, error="The completed backup could not be read."
            ),
            [],
        )
    filename = time.strftime("kitchen-radio-backup-%Y%m%d-%H%M%S.tar.gz")
    return (
        200,
        "application/gzip",
        data,
        [
            ("Content-Disposition", f'attachment; filename="{filename}"'),
            ("Cache-Control", "no-store"),
        ],
    )


def _restore_post(req: Request) -> Response:
    """Authenticate, stage, validate, and apply one confirmed backup file."""
    if not auth.require_auth(req.session):
        return _redirect("/login")
    if not auth.check_csrf(req.session, req.form.get("csrf_token")):
        return 403, _HTML, templates.forbidden(), []
    assert req.session is not None
    if req.form.get("confirmed") != "1":
        return (
            400,
            _HTML,
            templates.maintenance_page(
                req.session.csrf_token, error="Confirm that the current settings may be replaced."
            ),
            [],
        )
    if not auth.check_password(req.form.get("password", "")):
        return (
            401,
            _HTML,
            templates.maintenance_page(req.session.csrf_token, error="Incorrect password."),
            [],
        )
    upload = req.files.get("backup_file")
    if upload is None:
        return (
            400,
            _HTML,
            templates.maintenance_page(req.session.csrf_token, error="Choose a backup archive."),
            [],
        )
    try:
        data_backup.stage_restore(upload.data)
    except data_backup.BackupError as exc:
        return 400, _HTML, templates.maintenance_page(req.session.csrf_token, error=str(exc)), []
    ok, detail = actions.run_action("inspect_data_restore")
    if not ok:
        actions.run_action("cancel_data_restore")
        return 400, _HTML, templates.maintenance_page(req.session.csrf_token, error=detail), []
    ok, detail = actions.run_action("apply_data_restore")
    return (
        (200 if ok else 409),
        _HTML,
        templates.maintenance_page(
            req.session.csrf_token, message=detail if ok else None, error=None if ok else detail
        ),
        [],
    )
