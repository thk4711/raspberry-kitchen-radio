"""Web route handlers for adc network."""

from typing import Dict, Optional

from . import (
    actions,
    adc_store,
    auth,
    network_store,
    templates,
)
from .route_common import (
    _HTML,
    Request,
    Response,
    _ok,
    _redirect,
)

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


# --- Network / WiFi (Area C)---

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
