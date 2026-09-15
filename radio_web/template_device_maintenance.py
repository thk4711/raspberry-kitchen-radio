"""Server-rendered templates for device maintenance."""

from typing import Any, Dict, List, Optional

from .template_common import (
    _PLAIN_HTTP_NOTICE,
    _error_block,
    _esc,
    _flash_block,
    _page,
    csrf_field,
)


def device_page(
    settings: Dict[str, str],
    csrf_token: str,
    *,
    hostname_locked: bool = False,
    timezones: Optional[List[str]] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the device-settings editor (name / time / remote access).

    ``settings`` carries the current ``name``/``timezone``/``ntp_server`` and
    ``ssh_enabled`` values.
    When ``hostname_locked`` the name field is shown read-only with a notice
    that the SD card ``radio-config.txt`` currently overrides it.
    """
    csrf = csrf_field(csrf_token)
    name = _esc(settings.get("name", ""))
    current_tz = settings.get("timezone", "")
    ntp = _esc(settings.get("ntp_server", ""))
    timezone_names = list(timezones or [])
    if current_tz and current_tz not in timezone_names:
        timezone_names.append(current_tz)
    if not timezone_names:
        timezone_names.append("UTC")
    timezone_options = "".join(
        f'<option value="{_esc(zone)}"{" selected" if zone == current_tz else ""}>{_esc(zone)}</option>'
        for zone in sorted(set(timezone_names))
    )
    if hostname_locked:
        name_field = (
            f'<input type="text" value="{name}" maxlength="63" disabled>'
            '<span class="note">The device name is currently set by the SD '
            "card (radio-config.txt) and cannot be changed here.</span>"
        )
    else:
        name_field = (
            f'<input type="text" name="name" value="{name}" maxlength="63" '
            'placeholder="e.g. kitchen-radio" required>'
        )
    body = (
        "<h1>Device settings</h1>"
        '<p class="sub">Name, timezone, time server and remote access. The device name is '
        "applied after a reboot.</p>"
        f"{_flash_block(message, error)}"
        '<form method="post" action="/device" id="device-settings">'
        f"{csrf}"
        '<div class="card"><h2>Identity</h2>'
        f"<p><label>Device name<br>{name_field}</label></p>"
        "</div>"
        '<div class="card"><h2>Time</h2>'
        '<p><label>Timezone<br><select name="timezone" required>'
        f"{timezone_options}</select>"
        "</label></p>"
        f'<p><label>NTP server<br><input type="text" name="ntp_server" '
        f'value="{ntp}" maxlength="253" placeholder="e.g. pool.ntp.org" required>'
        "</label></p>"
        "</div>"
        '<div class="card"><h2>Remote access</h2>'
        '<input type="hidden" name="ssh_enabled" value="false">'
        '<p><label><input type="checkbox" name="ssh_enabled" value="true"'
        f"{' checked' if settings.get('ssh_enabled', 'false') == 'true' else ''}>"
        " Enable SSH</label></p>"
        '<p class="note">Allows root SSH access on the local network. Changes '
        "take effect immediately. A unique root password must first be provisioned "
        "through radio-config.txt; its enable_ssh setting can still disable access.</p>"
        "</div>"
        "</form>"
        '<div class="card physical-controls-card"><h2>Physical controls</h2>'
        "<p>Inspect the volume knob, preset buttons and power switch in real time, "
        "and calibrate how their ADS1115 readings are interpreted.</p>"
        '<p class="btnrow"><a class="button btn-secondary" href="/debug/adc">'
        "ADC debug &amp; calibration</a></p>"
        "</div>"
        '<p class="note">Timezone and NTP changes take effect once saved; the '
        "device name takes effect after the next reboot.</p>"
        '<p class="btnrow">'
        '<button class="btn-primary" type="submit" form="device-settings" name="op" '
        'value="save">Save</button> '
        '<button class="btn-danger" type="submit" form="device-settings" name="op" '
        'value="restore">Restore defaults</button>'
        "</p>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Device settings", body)


def _maintenance_action_form(
    csrf_token: str,
    op: str,
    label: str,
    *,
    confirm: bool = False,
    button_class: str = "btn-warning",
) -> str:
    csrf = csrf_field(csrf_token)
    action = "/maintenance/confirm" if confirm else "/maintenance"
    return (
        f'<form method="post" action="{action}">'
        f"{csrf}"
        f'<input type="hidden" name="action" value="{_esc(op)}">'
        f'<button class="{_esc(button_class)}" type="submit">{_esc(label)}</button>'
        "</form>"
    )


def maintenance_page(
    csrf_token: str,
    *,
    firmware: Optional[Dict[str, Any]] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the maintenance page: restart, reboot, shutdown, diagnostics.

    Restart player / restart MPD run immediately. Reboot and shutdown route
    through a confirmation page with a fresh auth check. Diagnostics is a GET
    download.
    """
    firmware_state = firmware or {}
    active = bool(firmware_state.get("active")) or firmware_state.get("phase") in {
        "queued",
        "inspecting",
        "validating",
        "installing",
        "syncing",
        "preparing_trial_boot",
        "rebooting",
        "checking_health",
    }
    if firmware_state.get("switch_allowed"):
        switch_html = (
            '<form method="post" action="/firmware/switch/confirm">'
            f"{csrf_field(csrf_token)}"
            '<button class="btn-warning" type="submit">Switch to previous firmware…</button>'
            "</form>"
        )
    else:
        reason = _firmware_value(
            firmware_state.get("switch_reason"), "No compatible previous firmware is available."
        )
        switch_html = (
            '<button class="btn-warning" type="button" disabled>'
            "Switch to previous firmware…</button>"
            f'<p class="note">Switching unavailable: {_esc(reason)}</p>'
        )
    update_disabled = " disabled" if active else ""
    firmware_html = (
        '<dl class="firmware-details">'
        f"<dt>Running version</dt><dd>{_esc(_firmware_value(firmware_state.get('running_version')))}</dd>"
        f"<dt>Running slot</dt><dd>{_esc(_firmware_value(firmware_state.get('running_slot')))}</dd>"
        f"<dt>Previous version</dt><dd>{_esc(_firmware_value(firmware_state.get('other_version')))}</dd>"
        f"<dt>Previous slot</dt><dd>{_esc(_firmware_value(firmware_state.get('other_slot')))}</dd>"
        f"<dt>Previous-slot health</dt><dd>{_esc(_firmware_value(firmware_state.get('other_health')))}</dd>"
        "</dl>"
        '<div class="btnrow firmware-actions maintenance-actions">'
        f"{switch_html}"
        f'<button class="btn-primary" type="button" id="firmware-update-open"'
        f"{update_disabled}>Update firmware…</button>"
        "</div>"
    )
    wizard = (
        '<dialog id="firmware-wizard" aria-labelledby="firmware-wizard-title" '
        'data-authorize-url="/firmware/authorize" data-upload-url="/firmware/upload" '
        'data-tracking-url="/firmware/tracking" '
        f'data-target-slot="{_esc(_firmware_value(firmware_state.get("other_slot")))}">'
        '<div class="wizard-shell"><div class="wizard-header">'
        '<div><p class="wizard-eyebrow" id="firmware-step-label">Step 1 of 4</p>'
        '<h2 id="firmware-wizard-title">Update firmware</h2></div>'
        '<button type="button" class="wizard-close btn-secondary" '
        'aria-label="Close firmware update">&times;</button>'
        '</div><ol class="wizard-steps" aria-label="Update steps">'
        '<li class="current">Password</li><li>File</li><li>Confirm</li><li>Update</li></ol>'
        f'<input type="hidden" id="firmware-csrf" value="{_esc(csrf_token)}">'
        '<section class="wizard-panel" data-step="1"><h3>Confirm your password</h3>'
        "<p>Firmware updates change the operating system. Enter your administrator password to continue.</p>"
        '<form id="firmware-auth-form"><label>Admin password<br>'
        '<input id="firmware-password" type="password" autocomplete="current-password" required></label>'
        '<p class="warn">This interface uses plain HTTP. Continue only on your trusted local network.</p>'
        '<p class="btnrow"><button class="btn-primary" type="submit">'
        "Continue</button></p></form></section>"
        '<section class="wizard-panel" data-step="2" hidden><h3>Select the update file</h3>'
        "<p>Choose a trusted <code>kitchen-radio-&lt;version&gt;.swu</code> package.</p>"
        '<label>Firmware file<br><input id="firmware-file" type="file" '
        'accept=".swu,application/octet-stream"></label>'
        '<p class="note">Maximum package size: 768 MiB.</p>'
        '<p class="btnrow"><button class="btn-primary" type="button" data-next="3">'
        "Review update</button></p></section>"
        '<section class="wizard-panel" data-step="3" hidden><h3>Ready to install</h3>'
        '<dl class="firmware-details"><dt>File</dt><dd id="firmware-review-name">—</dd>'
        '<dt>Size</dt><dd id="firmware-review-size">—</dd>'
        f"<dt>Target</dt><dd>Inactive slot {_esc(_firmware_value(firmware_state.get('other_slot')))}</dd></dl>"
        '<p class="warn">Packages are not cryptographically signed. Keep stable power connected. '
        "The radio will upload, validate, install, and reboot automatically.</p>"
        '<label class="confirm-check"><input id="firmware-confirm" type="checkbox"> '
        "I understand that playback will stop and the device will reboot.</label>"
        '<p class="btnrow"><button class="btn-danger" id="firmware-start" type="button">'
        "Install and reboot</button></p></section>"
        '<section class="wizard-panel" data-step="4" hidden><h3 id="firmware-progress-title">Updating firmware</h3>'
        '<p id="firmware-phase" role="status" aria-live="polite">Preparing the update…</p>'
        '<progress id="firmware-progress" max="100" value="0">0%</progress>'
        '<p id="firmware-progress-detail" class="note" aria-live="polite"></p>'
        '<p id="firmware-result" role="alert"></p>'
        '<p class="btnrow"><button class="btn-primary" id="firmware-done" type="button" '
        "hidden>Done</button></p></section>"
        "</div></dialog>"
    )
    body = (
        "<h1>Maintenance</h1>"
        '<p class="sub">Restart audio services, control the complete device, and '
        "download diagnostics.</p>"
        f"{_flash_block(message, error)}"
        '<div class="card"><h2>Audio services</h2>'
        "<p>Restart playback software without rebooting the Raspberry Pi.</p>"
        '<div class="btnrow maintenance-actions">'
        f"{_maintenance_action_form(csrf_token, 'restart_radio', 'Restart player')}"
        f"{_maintenance_action_form(csrf_token, 'restart_mpd', 'Restart MPD')}"
        "</div>"
        '<p class="note"><strong>Restart player</strong> restarts the main radio '
        "application and audio integrations; the operating system and this web "
        "interface stay available. <strong>Restart MPD</strong> restarts only the "
        "internet-radio playback service.</p></div>"
        '<div class="card danger-zone"><h2>Device power</h2>'
        "<p>These actions affect the complete Raspberry Pi and interrupt the web interface.</p>"
        '<div class="btnrow maintenance-actions">'
        f"{_maintenance_action_form(csrf_token, 'reboot', 'Reboot device…', confirm=True)}"
        f"{_maintenance_action_form(csrf_token, 'shutdown', 'Shut down device…', confirm=True, button_class='btn-danger')}"
        "</div>"
        '<p class="note"><strong>Reboot device</strong> restarts the operating system. '
        "<strong>Shut down device</strong> safely stops it; physical power cycling is "
        "required to start it again. Both ask for your password.</p></div>"
        '<div class="card"><h2>Firmware</h2>'
        '<p class="note">Install a trusted update or return to the firmware retained in the other slot.</p>'
        f"{firmware_html}</div>{wizard}"
        '<div class="card"><h2>Diagnostics</h2>'
        '<p><a href="/diagnostics">Download diagnostics bundle</a></p>'
        '<p class="note">A small archive of the volatile in-memory logs '
        "(cleared on reboot). Secrets and WiFi credentials are excluded.</p>"
        "</div>"
        '<div class="card"><h2>Backup &amp; restore</h2>'
        '<p>Save your radio settings and connections to a backup file. You can use '
        'this file to restore the radio after reinstalling or replacing the SD card.</p>'
        '<div class="btnrow maintenance-actions">'
        '<button class="btn-primary" type="button" id="backup-open">Create backup</button>'
        '<button class="btn-warning" type="button" id="restore-open">Restore backup…</button>'
        '</div></div>'
        '<dialog id="backup-dialog" class="maintenance-dialog" aria-labelledby="backup-title">'
        '<div class="wizard-shell"><div class="wizard-header">'
        '<h2 id="backup-title">Create backup</h2>'
        '<button type="button" class="dialog-close btn-secondary" '
        'aria-label="Close backup window">&times;</button></div>'
        '<p>Download a file containing your radio settings, WiFi configuration, '
        'device identities, and Bluetooth pairings.</p>'
        '<p class="warn"><strong>Keep this file private.</strong> It contains sensitive '
        'settings that can provide access to your radio and network.</p>'
        '<form method="post" action="/backup/download" id="backup-form">'
        f'{csrf_field(csrf_token)}<label>Admin password<br>'
        '<input id="backup-password" type="password" name="password" '
        'autocomplete="current-password" required></label>'
        '<p class="btnrow"><button class="btn-primary" type="submit">Download backup</button> '
        '<button class="btn-secondary dialog-cancel" type="button">Cancel</button></p>'
        '</form></div></dialog>'
        '<dialog id="restore-dialog" class="maintenance-dialog" aria-labelledby="restore-title">'
        '<div class="wizard-shell"><div class="wizard-header">'
        '<h2 id="restore-title">Restore backup</h2>'
        '<button type="button" class="dialog-close btn-secondary" '
        'aria-label="Close restore window">&times;</button></div>'
        '<p>Select a backup file previously downloaded from this radio interface.</p>'
        '<form method="post" action="/restore" enctype="multipart/form-data" id="restore-form">'
        f'{csrf_field(csrf_token)}<label>Backup file<br>'
        '<input id="restore-file" type="file" name="backup_file" '
        'accept=".tar.gz,.gz,application/gzip" required></label>'
        '<p><label>Admin password<br><input id="restore-password" type="password" '
        'name="password" autocomplete="current-password" required></label></p>'
        '<p class="warn">Restoring replaces radio settings, WiFi credentials, device '
        'identities, and Bluetooth pairings. Your connection and next login may change.</p>'
        '<label class="confirm-check"><input type="checkbox" name="confirmed" value="1" '
        'required> I understand that the current settings will be replaced.</label>'
        '<p class="btnrow"><button class="btn-danger" type="submit">Restore backup</button> '
        '<button class="btn-secondary dialog-cancel" type="button">Cancel</button></p>'
        '</form></div></dialog>'
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Maintenance", body, script="/static/app.js?v=10")


def _firmware_value(value: Any, fallback: str = "Unknown") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def firmware_switch_confirm_page(
    csrf_token: str,
    firmware: Dict[str, Any],
    *,
    error: Optional[str] = None,
) -> str:
    """Confirm a helper-authoritative switch to the validated other slot."""
    label = _firmware_value(firmware.get("switch_label"), "Switch to other firmware")
    body = (
        "<h1>Confirm firmware switch</h1>"
        f'<p class="sub">{_esc(label)}. This starts a trial boot; an unhealthy firmware '
        "automatically returns to the firmware currently running.</p>"
        f"{_error_block(error)}"
        '<div class="card danger-zone">'
        '<dl class="firmware-details">'
        f"<dt>Current</dt><dd>{_esc(_firmware_value(firmware.get('running_version')))} "
        f"(slot {_esc(_firmware_value(firmware.get('running_slot')))})</dd>"
        f"<dt>Other</dt><dd>{_esc(_firmware_value(firmware.get('other_version')))} "
        f"(slot {_esc(_firmware_value(firmware.get('other_slot')))})</dd></dl>"
        '<p class="note">Persistent configuration in <code>/data</code> is shared. Firmware-owned '
        "files are not copied or restored.</p>"
        '<form method="post" action="/firmware/switch">'
        f"{csrf_field(csrf_token)}"
        '<input type="hidden" name="confirmed" value="1">'
        '<label>Admin password<br><input type="password" name="password" '
        'autocomplete="current-password" required autofocus></label>'
        f'<p class="btnrow"><button class="btn-warning" type="submit">{_esc(label)}</button> '
        '<a href="/maintenance">Cancel</a></p></form></div>'
        f"{_PLAIN_HTTP_NOTICE}"
    )
    return _page("Confirm firmware switch", body)


def confirm_page(
    csrf_token: str,
    action: str,
    label: str,
    *,
    error: Optional[str] = None,
) -> str:
    """Render the destructive-action confirmation page (fresh auth check).

    The user must re-enter the admin password to proceed with ``action``.
    ``label`` is the human verb (e.g. "Reboot").
    """
    csrf = csrf_field(csrf_token)
    body = (
        f"<h1>Confirm: {_esc(label)}</h1>"
        '<p class="sub">This will interrupt playback. Re-enter your password '
        "to continue.</p>"
        f"{_error_block(error)}"
        '<form method="post" action="/maintenance">'
        f"{csrf}"
        f'<input type="hidden" name="action" value="{_esc(action)}">'
        '<input type="hidden" name="confirmed" value="1">'
        '<div class="card">'
        "<label>Password<br>"
        '<input type="password" name="password" '
        'autocomplete="current-password" required autofocus></label>'
        f'<p class="btnrow"><button class="{("btn-danger" if action == "shutdown" else "btn-warning")}" '
        f'type="submit">{_esc(label)}</button> '
        '<a href="/maintenance">Cancel</a></p>'
        "</div></form>"
        f"{_PLAIN_HTTP_NOTICE}"
    )
    return _page(f"Confirm {label}", body)
