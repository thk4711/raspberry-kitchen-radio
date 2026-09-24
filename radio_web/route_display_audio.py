"""Web route handlers for display audio."""

import json
from typing import Dict, List, Optional, Tuple

from . import (
    actions,
    artwork_store,
    audio_hardware_store,
    audio_store,
    auth,
    display_store,
    equalizer_store,
    templates,
    usb_audio_store,
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

_SETTINGS_FLASHES = {
    "saved": "Settings saved.",
    "applied": "Radio restarted; settings applied.",
    "restored": "Display and online artwork settings reset to defaults.",
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
        artwork=artwork_store.load_artwork(),
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
        artwork_store.restore_builtin()
        return _redirect("/settings?msg=restored")

    # The form carries the display [display] panel field and [ui] theme fields;
    # audio settings live on the Audio page (/audio-hardware).
    display_form = {
        key: req.form.get(key, "")
        for key in (
            "panel",
            "theme_preset",
            "animations",
            "rotate_180",
            "idle_timeout",
            "crossfade_ms",
            "clock_size",
            "osd_duration",
            "toast_duration",
        )
    }
    # Unchecked checkboxes are simply absent from the POST body.
    display_form["animations"] = req.form.get("animations", "")
    display_form["rotate_180"] = req.form.get("rotate_180", "")

    artwork_form = {"enabled": req.form.get("online_artwork_enabled", "")}

    try:
        display_store.validate_settings(display_form)
        artwork_store.validate_settings(artwork_form)
        display_store.save_display(display_form)
        artwork_store.save_artwork(artwork_form)
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


# --- Audio: hardware, USB, volume, and parametric-EQ settings

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
    return _audio_hardware_page_response(req, audio_hardware_store.load_profile(), error=detail)


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
