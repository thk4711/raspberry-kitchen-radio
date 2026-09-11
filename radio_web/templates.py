"""Server-rendered, HTML-escaped templates for the web administration UI.

Pages are built from Python strings without a template-engine dependency, with
:func:`html.escape` applied to **every** dynamic value. The shared stylesheet
and radio mark are local, explicitly allowlisted assets; no page needs a CDN,
web font, or JavaScript to function.
"""

import html
import re
from typing import Any, Dict, List, Mapping, Optional

_NAV_ITEMS = (
    ("Kitchen Radio", "/", "Dashboard"),
    ("Radio stations", "/stations", "Stations"),
    ("Music sources", "/sources", "Sources"),
    ("Display", "/settings", "Display"),
    ("Audio", "/audio-hardware", "Audio"),
    ("WiFi & network", "/network", "Network"),
    ("Device settings", "/device", "Device"),
    ("Maintenance", "/maintenance", "Maintenance"),
)


def _esc(value: Any) -> str:
    """HTML-escape any value, rendering ``None`` as an em dash placeholder."""
    if value is None:
        return "&mdash;"
    return html.escape(str(value))


def _fmt_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "&mdash;"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return _esc(" ".join(parts))


def _fmt_bytes(num: Optional[int]) -> str:
    if num is None:
        return "&mdash;"
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return _esc(f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}")
        value /= 1024
    return _esc(num)


def _fmt_seconds_ago(seconds: Optional[float]) -> str:
    if seconds is None:
        return "&mdash;"
    return _esc(f"{seconds:.0f}s ago")


def _page(title: str, body: str, *, script: Optional[str] = None) -> str:
    """Wrap page ``body`` in the shared responsive application shell."""
    page_class = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    nav = []
    for item_title, path, label in _NAV_ITEMS:
        current = ' aria-current="page"' if title == item_title else ""
        nav.append(f'<a href="{path}"{current}>{_esc(label)}</a>')
    script_tag = f'<script src="{_esc(script)}" defer></script>' if script else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="theme-color" content="#132238">'
        f"<title>{_esc(title)}</title>"
        '<link rel="icon" href="/static/radio.svg?v=1" type="image/svg+xml">'
        '<link rel="stylesheet" href="/static/app.css?v=16">'
        f"{script_tag}"
        '</head><body><a class="skip-link" href="#content">Skip to content</a>'
        '<header class="site-header"><div class="header-inner">'
        '<a class="brand" href="/">'
        '<img src="/static/radio.svg?v=1" alt="" width="38" height="38">'
        '<span class="brand-copy">Kitchen Radio<small>Control panel</small></span>'
        '</a><nav class="primary-nav" aria-label="Main navigation">'
        f"{''.join(nav)}"
        "</nav></div></header>"
        f'<main id="content" class="page page-{page_class}">{body}</main>'
        '<footer class="site-footer">Raspberry Kitchen Radio &middot; Local control panel</footer>'
        "</body></html>"
    )


def _system_card(status: Dict[str, Any]) -> str:
    mem = status.get("memory", {})
    root = status.get("rootfs", {})
    wifi = status.get("wifi", {})
    total_kib = mem.get("total_kib")
    avail_kib = mem.get("available_kib")
    mem_text = "&mdash;"
    if total_kib is not None and avail_kib is not None:
        used_kib = max(0, total_kib - avail_kib)
        mem_text = f"{_fmt_bytes(used_kib * 1024)} / {_fmt_bytes(total_kib * 1024)}"
    temp = status.get("cpu_temperature_c")
    temp_text = _esc(f"{temp:.1f} °C") if temp is not None else "&mdash;"
    signal = wifi.get("signal_dbm")
    signal_text = _esc(f"{signal} dBm") if signal is not None else "&mdash;"
    return (
        '<div class="card system-card"><h2>System</h2><dl>'
        f"<dt>SSID</dt><dd>{_esc(wifi.get('ssid'))}</dd>"
        f"<dt>IP address</dt><dd>{_esc(wifi.get('ip'))}</dd>"
        f"<dt>WiFi signal</dt><dd>{signal_text}</dd>"
        f"<dt>Uptime</dt><dd>{_fmt_uptime(status.get('uptime_seconds'))}</dd>"
        f"<dt>CPU temperature</dt><dd>{temp_text}</dd>"
        f"<dt>Memory used</dt><dd>{mem_text}</dd>"
        f"<dt>Root FS free</dt><dd>{_fmt_bytes(root.get('free'))} of "
        f"{_fmt_bytes(root.get('total'))}</dd>"
        f"<dt>Heartbeat</dt><dd>{_fmt_seconds_ago(status.get('heartbeat_age_seconds'))}</dd>"
        f"<dt>App version</dt><dd>{_esc(status.get('version'))}</dd>"
        "</dl></div>"
    )


def _bluetooth_card(status: Dict[str, Any]) -> str:
    """Render read-only Bluetooth adapter status for the dashboard.

    Informational only: there are no controls here.
    The visible adapter name follows the device hostname. When no device is
    connected the adapter advertises itself with no PIN automatically, so no
    manual pairing action is needed.
    """
    bt = status.get("bluetooth", {}) or {}
    if not bt.get("available"):
        body = (
            '<p class="note">Bluetooth adapter status is unavailable. It may be '
            "disabled in Music sources, or still starting.</p>"
        )
    else:
        powered = (
            '<span class="badge on">on</span>'
            if bt.get("powered")
            else '<span class="badge off">off</span>'
        )
        discoverable = (
            '<span class="badge on">discoverable</span>'
            if bt.get("discoverable")
            else '<span class="badge off">hidden</span>'
        )
        body = (
            "<dl>"
            f"<dt>Name</dt><dd>{_esc(bt.get('alias'))}</dd>"
            f"<dt>Powered</dt><dd>{powered}</dd>"
            f"<dt>Discoverable</dt><dd>{discoverable}</dd>"
            "</dl>"
            '<p class="note">The name comes from the device name and is set under '
            "Device settings. When nothing is connected the radio is discoverable "
            "and pairs with no PIN automatically.</p>"
        )
    return f'<div class="card bluetooth-card"><h2>Bluetooth</h2>{body}</div>'


def _now_playing_card(player: Dict[str, Any]) -> str:
    if not player.get("available"):
        return (
            '<div class="card now-playing"><div class="now-playing-content">'
            '<div class="artwork artwork-fallback" aria-hidden="true">♪</div>'
            '<div class="now-playing-details"><h2>Now playing</h2>'
            '<p class="warn">Player status unavailable — the radio process may be '
            "starting or stopped.</p></div></div></div>"
        )
    now = player.get("now_playing", {}) or {}
    stale = '<span class="badge off">stale</span>' if player.get("stale") else ""
    power_on = bool(player.get("power"))
    power_badge = (
        '<span class="badge on">on</span>' if power_on else '<span class="badge off">off</span>'
    )
    active_source = player.get("active_source")
    active_entry = (player.get("sources", {}) or {}).get(active_source, {})
    if isinstance(active_entry, dict) and "playing" in active_entry:
        is_playing = bool(active_entry.get("playing"))
    elif "state" in now:
        is_playing = bool(now.get("state"))
    else:
        is_playing = None
    if is_playing is True:
        playback_badge = '<span class="badge on">Playing</span>'
    elif is_playing is False:
        playback_badge = '<span class="badge off">Not playing</span>'
    else:
        playback_badge = '<span class="badge off">Unknown</span>'
    artwork = now.get("artwork", {})
    art_id = artwork.get("id") if isinstance(artwork, dict) else None
    art_version = artwork.get("version") if isinstance(artwork, dict) else None
    if art_id:
        artwork_html = (
            '<div class="artwork">'
            f'<img src="/dashboard/artwork?id={_esc(art_id)}&amp;v={_esc(art_version or "1")}" '
            f'alt="Artwork for {_esc(now.get("name") or "now playing")}" '
            'width="176" height="176">'
            "</div>"
        )
    else:
        artwork_html = '<div class="artwork artwork-fallback" aria-hidden="true">♪</div>'
    return (
        '<div class="card now-playing"><div class="now-playing-content">'
        f"{artwork_html}"
        f'<div class="now-playing-details"><h2>Now playing {stale}</h2><dl>'
        f"<dt>Power</dt><dd>{power_badge}</dd>"
        f"<dt>Playback</dt><dd>{playback_badge}</dd>"
        f"<dt>Active source</dt><dd>{_esc(active_source)}</dd>"
        f"<dt>Station / app</dt><dd>{_esc(now.get('name'))}</dd>"
        f"<dt>Track</dt><dd>{_esc(now.get('title'))}</dd>"
        "</dl></div></div></div>"
    )


def _sources_card(status: Dict[str, Any]) -> str:
    player = status.get("player", {})
    available = player.get("available")
    sources = player.get("sources", {}) or {}
    active = player.get("active_source")
    rows = []
    for key, label in status.get("source_labels", []):
        if not available:
            state = "&mdash;"
        else:
            entry = sources.get(key, {}) or {}
            playing = bool(entry.get("playing"))
            if key == active and playing:
                state = '<span class="badge on">Active</span>'
            elif playing:
                state = '<span class="badge on">Playing</span>'
            else:
                state = '<span class="badge off">Ready</span>'
        rows.append(f"<tr><td>{_esc(label)}</td><td>{state}</td></tr>")
    return (
        '<div class="card sources-card"><h2>Sources</h2><table>'
        "<tr><th>Source</th><th>State</th></tr>"
        f"{''.join(rows)}</table></div>"
    )


def dashboard(status: Dict[str, Any]) -> str:
    """Render the read-only dashboard page from a :func:`system_status.collect`.

    Every dynamic value is HTML-escaped. No auth and no mutating controls are
    present in Phase 3.
    """
    host = _esc(status.get("hostname") or "Kitchen Radio")
    body = (
        f"<h1>{host}</h1>"
        '<p class="sub">Read-only dashboard</p>'
        '<div class="dashboard-grid">'
        '<div id="now-playing" class="now-playing-region" aria-live="polite">'
        f"{_now_playing_card(status.get('player', {}))}"
        "</div>"
        f"{_sources_card(status)}"
        f"{_system_card(status)}"
        f"{_bluetooth_card(status)}"
        "</div>"
        '<p class="note">Logs and this status are stored in volatile memory '
        "(tmpfs) and are cleared on reboot.</p>"
    )
    return _page("Kitchen Radio", body, script="/static/app.js?v=1")


def now_playing_fragment(player: Dict[str, Any]) -> str:
    """Render the replaceable dashboard Now Playing region."""
    return _now_playing_card(player)


def not_found() -> str:
    """Render the 404 page."""
    return _page(
        "Not found",
        "<h1>Not found</h1><p>The requested page does not exist. "
        '<a href="/">Back to the dashboard</a>.</p>',
    )


def method_not_allowed() -> str:
    """Render the 405 page (the request method is not allowed for this path)."""
    return _page(
        "Method not allowed",
        "<h1>Method not allowed</h1><p>That method is not allowed for this "
        'page. <a href="/">Back to the dashboard</a>.</p>',
    )


def forbidden() -> str:
    """Render the 403 page (failed CSRF / auth check)."""
    return _page(
        "Forbidden",
        "<h1>Forbidden</h1><p>The request could not be verified. "
        '<a href="/">Back to the dashboard</a>.</p>',
    )


# --- Authentication pages (Phase 4) -----------------------------------------

# Plain HTTP on a trusted LAN in v1: state that credentials are not encrypted.
_PLAIN_HTTP_NOTICE = (
    '<p class="note">This connection is not encrypted (plain HTTP). '
    "Only use it on your trusted home network.</p>"
)


def csrf_field(token: str) -> str:
    """Return a hidden CSRF input for embedding in a state-changing form."""
    return f'<input type="hidden" name="csrf_token" value="{_esc(token)}">'


def _error_block(error: Optional[str]) -> str:
    if not error:
        return ""
    return f'<p class="warn">{_esc(error)}</p>'


def login_page(error: Optional[str] = None) -> str:
    """Render the login form."""
    body = (
        "<h1>Sign in</h1>"
        '<p class="sub">Kitchen Radio administration</p>'
        f"{_error_block(error)}"
        '<form method="post" action="/login">'
        '<div class="card">'
        "<label>Password<br>"
        '<input type="password" name="password" autocomplete="current-password" '
        "required autofocus></label>"
        '<p class="btnrow"><button class="btn-primary" type="submit">Sign in</button></p>'
        "</div></form>"
        f"{_PLAIN_HTTP_NOTICE}"
    )
    return _page("Sign in", body)


def setup_page(error: Optional[str] = None) -> str:
    """Render the first-use admin-password setup form."""
    body = (
        "<h1>Set an admin password</h1>"
        '<p class="sub">First-use setup — choose a password to protect this '
        "interface.</p>"
        f"{_error_block(error)}"
        '<form method="post" action="/setup">'
        '<div class="card">'
        "<label>Password<br>"
        '<input type="password" name="password" autocomplete="new-password" '
        "required autofocus></label>"
        "<p><label>Confirm password<br>"
        '<input type="password" name="confirm" autocomplete="new-password" '
        "required></label></p>"
        '<p class="btnrow"><button class="btn-primary" type="submit">Save password</button></p>'
        "</div></form>"
        f"{_PLAIN_HTTP_NOTICE}"
    )
    return _page("Set an admin password", body)


# --- Station editing pages (Phase 5) ----------------------------------------


def _flash_block(message: Optional[str], error: Optional[str]) -> str:
    """Render an optional success message and/or error banner."""
    parts = []
    if message:
        parts.append(f'<p class="ok">{_esc(message)}</p>')
    if error:
        parts.append(f'<p class="warn">{_esc(error)}</p>')
    return "".join(parts)


def _preset_card(index: int, slot: Dict[str, str], csrf_token: str, logos: List[str]) -> str:
    """Render one preset slot as a self-contained form (no JavaScript).

    ``index`` is 0-based; the card shows the 1-based preset number. Each button
    is a POST to ``/stations`` carrying the CSRF token, the slot index and an
    ``op`` naming the action, so every control works without client scripting.
    """
    number = index + 1
    csrf = csrf_field(csrf_token)
    name = _esc(slot.get("name", ""))
    url = _esc(slot.get("url", ""))
    selected_logo = slot.get("logo", "")
    logo_options = ['<option value="">No logo — generate initials tile</option>']
    if selected_logo and selected_logo not in logos:
        logo_options.append(
            f'<option value="{_esc(selected_logo)}" selected>{_esc(selected_logo)}</option>'
        )
    for filename in logos:
        selected = " selected" if filename == selected_logo else ""
        logo_options.append(f'<option value="{_esc(filename)}"{selected}>{_esc(filename)}</option>')
    up_disabled = " disabled" if index == 0 else ""
    down_disabled = " disabled" if number >= 6 else ""
    return (
        f'<div class="card"><h2>Preset {number}</h2>'
        '<form method="post" action="/stations">'
        f"{csrf}"
        f'<input type="hidden" name="index" value="{index}">'
        f'<p><label>Name<br><input type="text" name="name" value="{name}" '
        'maxlength="64"></label></p>'
        f'<p><label>Stream URL<br><input type="url" name="url" value="{url}" '
        'maxlength="2048"></label></p>'
        f'<p><label>Logo<br><select name="logo">{"".join(logo_options)}</select></label></p>'
        '<p class="btnrow">'
        f'<button class="btn-secondary" type="submit" name="op" value="move_up"{up_disabled}>'
        "Move up</button> "
        f'<button class="btn-secondary" type="submit" name="op" value="move_down"{down_disabled}>'
        "Move down</button> "
        '<button class="btn-secondary" type="submit" name="op" value="test">Test</button> '
        '<button class="btn-primary" type="submit" name="op" value="save">Save</button>'
        "</p>"
        "</form></div>"
    )


def stations_page(
    slots: List[Dict[str, str]],
    csrf_token: str,
    logos: List[str],
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the six-slot station editor.

    ``slots`` is exactly six ``{"name", "url", "logo"}`` dicts (padded/truncated
    upstream). ``message``/``error`` surface the outcome of the previous POST.
    Every dynamic value is HTML-escaped; the two whole-list actions (apply,
    restore) are separate CSRF-protected forms.
    """
    cards = "".join(
        _preset_card(index, slot, csrf_token, logos) for index, slot in enumerate(slots)
    )
    csrf = csrf_field(csrf_token)
    body = (
        "<h1>Radio stations</h1>"
        '<p class="sub">Six numbered presets — one per physical button.</p>'
        f"{_flash_block(message, error)}"
        f'<div class="preset-grid">{cards}</div>'
        '<div class="card"><h2>Upload a logo</h2>'
        '<p class="note">PNG or JPEG, up to 4 MiB. Large images are reduced to '
        "at most 512×512 before being saved. The upload keeps its (cleaned) "
        "file name so it is easy to recognise in the dropdown. Choose the "
        "uploaded logo from a preset dropdown, then save that preset.</p>"
        '<form method="post" action="/stations" enctype="multipart/form-data">'
        f"{csrf}"
        '<p><label>Logo image<br><input type="file" name="logo_file" '
        'accept="image/png,image/jpeg" required></label></p>'
        '<button class="btn-primary" type="submit" name="op" value="upload_logo">Upload logo</button>'
        "</form></div>"
        '<div class="card"><h2>Apply changes</h2>'
        '<p class="note">Saved presets take effect after the radio restarts.</p>'
        '<form method="post" action="/stations" class="btnrow">'
        f"{csrf}"
        '<button class="btn-apply" type="submit" name="op" value="apply">'
        "Apply and restart radio</button> "
        '<button class="btn-danger" type="submit" name="op" value="restore">'
        "Restore built-in list</button>"
        "</form></div>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Radio stations", body)


# --- Source feature-flag page (Phase 6) -------------------------------------


def _source_state(key: str, running: bool, active: bool, enabled: bool) -> str:
    """Render the Running/Active state badge for one source row.

    ``enabled`` is the persisted feature flag; ``running``/``active`` come from
    the live player snapshot. A disabled source shows no Running/Active badge —
    disabling removes it from both Enabled and Running.
    """
    if not enabled:
        return '<span class="badge off">Disabled</span>'
    if active:
        return '<span class="badge on">Active</span>'
    if running:
        return '<span class="badge on">Running</span>'
    return '<span class="badge off">Ready</span>'


def _source_row(
    key: str,
    label: str,
    flags: Dict[str, bool],
    live: Dict[str, Any],
) -> str:
    """Render one source row: the enable checkbox plus its live state."""
    enabled = bool(flags.get(key, True))
    checked = " checked" if enabled else ""
    entry = (live.get("sources", {}) or {}).get(key, {}) or {}
    running = bool(entry.get("playing"))
    active = live.get("active_source") == key and running
    state = _source_state(key, running, active, enabled)
    return (
        "<tr>"
        f'<td><label><input type="checkbox" name="{_esc(key)}" '
        f'value="true"{checked}> {_esc(label)}</label></td>'
        f"<td>{state}</td>"
        "</tr>"
    )


def sources_page(
    flags: Dict[str, bool],
    source_keys: List[Any],
    live: Dict[str, Any],
    csrf_token: str,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the source feature-flag editor.

    ``flags`` maps each source key to its persisted enabled state.
    ``source_keys`` is the ordered ``(key, label)`` list. ``live`` is the player
    status snapshot (for the Running/Active column). Every dynamic value is
    HTML-escaped; the whole form is one CSRF-protected POST (no JavaScript).
    """
    csrf = csrf_field(csrf_token)
    rows = "".join(_source_row(key, label, flags, live) for key, label in source_keys)
    body = (
        "<h1>Music sources</h1>"
        '<p class="sub">Enable or disable each source. Applies to the player '
        "and its background services.</p>"
        f"{_flash_block(message, error)}"
        '<form method="post" action="/sources">'
        f"{csrf}"
        '<div class="card"><h2>Sources</h2><table>'
        "<tr><th>Source</th><th>State</th></tr>"
        f"{rows}</table>"
        '<p class="btnrow">'
        '<button class="btn-primary" type="submit" name="op" value="save">Save</button> '
        '<button class="btn-apply" type="submit" name="op" value="apply">'
        "Apply and restart radio</button> "
        '<button class="btn-danger" type="submit" name="op" value="restore">'
        "Restore defaults</button>"
        "</p></div>"
        "</form>"
        '<p class="note">Enabled means available after boot; Running means the '
        "service is up; Active means it is currently playing. Changes take "
        "effect after the radio restarts.</p>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Music sources", body)


# --- Device settings + maintenance pages (Phase 7) --------------------------


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
        "take effect immediately. The SD-card enable_ssh setting can still disable it.</p>"
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


# --- Display & audio settings page (Area A) ---------------------------------


def _select(name: str, options: List[Any], current: str) -> str:
    """Render a ``<select>`` with ``options`` of ``(value, label)`` pairs."""
    opts = []
    for value, label in options:
        selected = " selected" if str(value) == str(current) else ""
        opts.append(f'<option value="{_esc(value)}"{selected}>{_esc(label)}</option>')
    return f'<select name="{_esc(name)}">{"".join(opts)}</select>'


def _checkbox(name: str, checked: bool, label: str) -> str:
    mark = " checked" if checked else ""
    return (
        f'<label><input type="checkbox" name="{_esc(name)}" value="true"{mark}> '
        f"{_esc(label)}</label>"
    )


def settings_page(
    display: Dict[str, str],
    csrf_token: str,
    *,
    presets: Optional[List[Any]] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the Display settings editor.

    ``display`` carries the ``[ui]`` form fields. Presets and per-field controls
    both post to ``/settings``; changes take effect on the next radio restart.
    """
    csrf = csrf_field(csrf_token)
    preset_options = presets or [("default", "Default")]
    animations_on = str(display.get("animations", "true")).lower() == "true"
    rotate_180_on = str(display.get("rotate_180", "false")).lower() == "true"
    body = (
        "<h1>Display</h1>"
        '<p class="sub">A few safe display options. Changes take '
        "effect after the radio restarts.</p>"
        f"{_flash_block(message, error)}"
        '<form method="post" action="/settings" id="display-settings">'
        f"{csrf}"
        '<div class="card"><h2>Display</h2>'
        "<p><label>Theme preset<br>"
        f"{_select('theme_preset', preset_options, display.get('theme_preset', 'default'))}"
        "</label></p>"
        f"<p>{_checkbox('animations', animations_on, 'Enable animations')}</p>"
        f"<p>{_checkbox('rotate_180', rotate_180_on, 'Rotate display 180°')}</p>"
        "<p><label>Idle timeout (seconds, 0 disables the clock screensaver)<br>"
        f'<input type="number" name="idle_timeout" min="0" max="86400" '
        f'value="{_esc(display.get("idle_timeout", "30"))}"></label></p>'
        "<p><label>Crossfade (milliseconds, 0 = off)<br>"
        f'<input type="number" name="crossfade_ms" min="0" max="5000" '
        f'value="{_esc(display.get("crossfade_ms", "150"))}"></label></p>'
        "<p><label>Clock size (px)<br>"
        f'<input type="number" name="clock_size" min="0" max="100" '
        f'value="{_esc(display.get("clock_size", "24"))}"></label></p>'
        "<p><label>Volume overlay duration (seconds)<br>"
        f'<input type="text" name="osd_duration" '
        f'value="{_esc(display.get("osd_duration", "1.5"))}"></label></p>'
        "<p><label>Preset toast duration (seconds)<br>"
        f'<input type="text" name="toast_duration" '
        f'value="{_esc(display.get("toast_duration", "1.6"))}"></label></p>'
        "</div>"
        '<p class="btnrow">'
        '<button class="btn-primary" type="submit" form="display-settings" name="op" '
        'value="save">Save</button> '
        '<button class="btn-apply" type="submit" form="display-settings" name="op" '
        'value="apply">Apply and restart radio</button> '
        '<button class="btn-danger" type="submit" form="display-settings" name="op" '
        'value="restore">Restore defaults</button>'
        "</p>"
        "</form>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Display", body)


def audio_hardware_page(
    profiles: List[Any],
    current: str,
    max_volume: str,
    usb_audio_mode: str,
    equalizer: Mapping[str, Any],
    csrf_token: str,
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the unified Audio settings form.

    ``profiles`` contains id, label, kind, family, support level and compatibility
    note values generated from the
    :data:`radio_web.audio_hardware_store.PROFILES` catalog (the page is generic
    over the catalog and never branches on a specific card id). ``current`` is
    the selected profile id. ``max_volume`` is the managed maximum-volume cap.

    One Apply action persists the sound-card, USB and volume settings. The EQ has
    separate Save/Apply actions so it can be edited without interrupting playback.
    """
    csrf = csrf_field(csrf_token)
    kind_labels = {"i2s": "I2S DAC/amp", "onboard": "On-board audio"}
    options = []
    current_note = ""
    support_labels = {
        "tested": "Tested",
        "kernel-supported": "Kernel-supported",
        "experimental": "Experimental",
    }
    for profile_id, label, kind, family, support_level, compatibility_note in profiles:
        selected = " selected" if profile_id == current else ""
        kind_note = kind_labels.get(kind, kind)
        support_note = support_labels.get(support_level, support_level)
        # Full per-card detail carried on the option so app.js can update the
        # note line below the select; it also stays correct without JavaScript
        # because the currently selected card's note is rendered server-side.
        data_note = f"{family} · {kind_note} · {support_note} — {compatibility_note}"
        if selected:
            current_note = data_note
        options.append(
            f'<option value="{_esc(profile_id)}"{selected} '
            f'data-note="{_esc(data_note)}">'
            f"{_esc(label)} — {_esc(support_note)}</option>"
        )
    filter_labels = {
        "bell": "Bell",
        "low_shelf": "Low shelf",
        "high_shelf": "High shelf",
        "high_pass": "High-pass",
        "low_pass": "Low-pass",
    }
    band_rows = []
    for index, band in enumerate(equalizer["bands"], 1):
        choices = "".join(
            f'<option value="{name}"{" selected" if band["type"] == name else ""}>'
            f"{label}</option>"
            for name, label in filter_labels.items()
        )
        band_rows.append(
            f'<div class="eq-band" data-eq-band="{index}">'
            f'<span class="eq-dot eq-color-{index}" aria-hidden="true"></span>'
            f'<label class="eq-on"><span class="sr-only">Band {index} enabled</span>'
            f'<input type="checkbox" name="eq_band_{index}_enabled" value="true"'
            f'{" checked" if band["enabled"] else ""}></label>'
            f'<label><span class="sr-only">Band {index} type</span><select name="eq_band_{index}_type">'
            f"{choices}</select></label>"
            f'<label><span class="sr-only">Band {index} gain</span><input type="number" '
            f'name="eq_band_{index}_gain_db" min="-15" max="15" step="0.1" '
            f'value="{_esc(band["gain_db"])}"><small>dB</small></label>'
            f'<label><span class="sr-only">Band {index} Q</span><input type="number" '
            f'name="eq_band_{index}_q" min="0.1" max="10" step="0.05" '
            f'value="{_esc(band["q"])}"></label>'
            f'<label><span class="sr-only">Band {index} frequency</span><input type="number" '
            f'name="eq_band_{index}_frequency" min="20" max="20000" step="1" '
            f'value="{_esc(band["frequency"])}"><small>Hz</small></label></div>'
        )
    equalizer_html = (
        '<form method="post" action="/audio-hardware" id="equalizer-form">'
        f"{csrf}"
        '<input type="hidden" name="ajax" value="">'
        '<div class="card eq-card" id="parametric-equalizer"><h2>Parametric equalizer</h2>'
        '<p class="note">Drag a point to change bell/shelf frequency and gain. The graph previews '
        "changes immediately; Save and Apply updates the sound live without interrupting playback. Enabling or "
        "disabling the whole equalizer briefly restarts the radio application and all audio receivers.</p>"
        '<div class="eq-flash" id="eq-flash" role="status" aria-live="polite"></div>'
        '<div class="eq-toolbar"><label class="switch-label"><input type="checkbox" '
        'name="eq_enabled" value="true"'
        f'{" checked" if equalizer["enabled"] else ""}> Enable equalizer</label>'
        '<label class="eq-preamp">Preamp Gain <input type="number" '
        'title="Reduces the overall level before the filters to avoid clipping when you '
        'boost bands. Defaults to -3 dB for headroom; lower it further (toward minus the '
        'largest positive band gain) when boosting. 0 dB means no reduction." '
        'name="eq_preamp_db" min="-24" max="0" '
        f'step="0.1" value="{_esc(equalizer["preamp_db"])}"> dB</label></div>'
        '<p class="note eq-preamp-hint">Preamp Gain reduces the overall level before the '
        "filters to avoid clipping when you boost bands. It defaults to -3 dB for headroom; "
        "lower it further — toward about minus the largest positive band gain — when you "
        "boost bands. 0 dB means no reduction.</p>"
        '<svg class="eq-graph" id="eq-graph" viewBox="0 0 900 320" role="img" '
        'aria-label="Equalizer frequency response from 20 hertz to 20 kilohertz"></svg>'
        '<div class="eq-band-head" aria-hidden="true"><span></span><span>On</span><span>Curve</span>'
        '<span>Gain</span><span>Q</span><span>Frequency</span></div>'
        f'<div class="eq-bands">{"".join(band_rows)}</div>'
        '<div class="btnrow"><button class="btn-apply" type="submit" '
        'name="op" value="apply_equalizer">Save and Apply</button><button class="btn-danger" '
        'name="op" value="restore_equalizer">Reset flat</button></div></div></form>'
    )
    body = (
        "<h1>Audio</h1>"
        '<p class="sub">Choose the sound card, USB Audio mode, and maximum volume, '
        "then apply them together.</p>"
        '<p class="section-links"><a href="#parametric-equalizer">Parametric equalizer</a> '
        '&middot; <a href="#audio-output-settings">Output settings</a></p>'
        f"{_flash_block(message, error)}"
        f"{equalizer_html}"
        '<form method="post" action="/audio-hardware" id="audio-hardware-form">'
        f"{csrf}"
        '<div class="card" id="audio-output-settings"><h2>Available sound cards</h2>'
        '<p><label>Sound card<br>'
        f'<select name="profile" id="profile-select">{"".join(options)}</select>'
        "</label></p>"
        f'<p class="note" id="profile-note">{_esc(current_note)}</p>'
        '<p class="note">Profiles use stable ALSA card ids so USB Audio or HDMI '
        "cannot silently renumber the selected output. Kernel-supported and "
        "Experimental entries still require the on-device checks in the I2S guide.</p>"
        '<p class="note">Choosing a sound card edits the boot configuration and '
        "takes effect after a reboot.</p>"
        "</div>"
        '<div class="card"><h2>USB Audio mode</h2>'
        '<p class="radio-row"><label>'
        f'<input type="radio" name="usb_audio_mode" value="uac1"{" checked" if usb_audio_mode == "uac1" else ""}> UAC1 — maximum compatibility</label><br>'
        '<span class="note">Stereo 48 kHz / 16-bit; recommended for broad host compatibility.</span></p>'
        '<p class="radio-row"><label>'
        f'<input type="radio" name="usb_audio_mode" value="uac2"{" checked" if usb_audio_mode == "uac2" else ""}> UAC2 — high-resolution mode</label><br>'
        '<span class="note">Experimental; advertises 44.1, 48 and 96 kHz at 24-bit. Host support varies.</span></p>'
        '<p class="note">Changing this mode takes effect after reboot. Disconnect and reconnect the USB host afterward.</p>'
        "</div>"
        '<div class="card"><h2>Volume</h2>'
        "<p><label>Maximum volume (0..100)<br>"
        f'<input type="number" name="max_volume" min="0" max="100" '
        f'value="{_esc(max_volume)}"></label></p>'
        '<p class="note">The maximum volume caps the physical knob — it is '
        "safer than a fixed startup volume the knob would overwrite. It takes "
        "effect after the radio restarts.</p>"
        "</div>"
        '<div class="card"><h2>Apply audio settings</h2>'
        '<p class="note">A sound-card or USB Audio mode change requires a device '
        "reboot. A maximum-volume-only change restarts the player. You will be "
        "asked before a required reboot.</p>"
        '<div class="btnrow">'
        '<button class="btn-apply" type="submit" name="op" value="apply_all">'
        "Apply changes</button>"
        '<button class="btn-danger" type="submit" name="op" value="restore">'
        "Restore defaults</button>"
        "</div></div>"
        "</form>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Audio", body, script="/static/app.js?v=17")


def audio_hardware_applied_page(
    label: str,
    csrf_token: str,
    *,
    detail: Optional[str] = None,
    usb_mode_changed: bool = False,
) -> str:
    """Render the reboot choice after applying reboot-level audio settings.

    The selection is already saved and the boot configuration edited; the change
    only takes effect on reboot (a device-tree overlay is resolved by the
    firmware at boot). The Reboot button reuses the maintenance ``reboot`` action
    (which asks for the password on its own confirmation page).
    """
    csrf = csrf_field(csrf_token)
    detail_html = f'<p class="note">{_esc(detail)}</p>' if detail else ""
    usb_note = (
        '<p class="note">USB Audio mode changed; disconnect and reconnect the USB host '
        "after reboot.</p>"
        if usb_mode_changed
        else ""
    )
    body = (
        "<h1>Reboot required</h1>"
        f'<p class="sub">Audio settings were saved for <strong>{_esc(label)}</strong>. '
        "Reboot to activate the reboot-level changes.</p>"
        f"{detail_html}"
        f"{usb_note}"
        '<div class="card"><h2>Reboot now?</h2>'
        '<form method="post" action="/maintenance">'
        f"{csrf}"
        '<input type="hidden" name="action" value="reboot">'
        '<p class="btnrow">'
        '<button class="btn-warning" type="submit">Reboot device…</button> '
        '<a href="/audio-hardware">Reboot later</a></p>'
        "</form>"
        '<p class="note">Rebooting asks for your password to confirm.</p>'
        "</div>"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("Audio", body)


def adc_debug_page(
    settings: Dict[str, str],
    csrf_token: str,
    *,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render authenticated live ADS1115 diagnostics and calibration controls."""
    csrf = csrf_field(csrf_token)

    def field(name: str, label: str) -> str:
        return (
            f'<label>{_esc(label)} (mV)<input class="adc-setting" type="number" '
            f'name="{name}" id="adc-{name}" min="0" max="6144" step="0.001" '
            f'value="{_esc(settings.get(name, ""))}" required></label>'
        )

    channel_cards = "".join(
        f'<article class="adc-channel" data-channel="{channel}">'
        f"<h2>AIN{channel} · {_esc(name)}</h2>"
        '<div class="adc-reading"><span data-adc-raw>—</span><small> mV</small></div>'
        f'<p class="adc-meaning" data-adc-meaning>{_esc(initial)}</p>'
        '<meter min="0" max="3300" value="0" data-adc-meter></meter>'
        "</article>"
        for channel, name, initial in (
            (0, "Volume", "Waiting for mapped volume…"),
            (1, "Buttons", "Waiting for button interpretation…"),
            (2, "Power", "Waiting for switch interpretation…"),
            (3, "Unused", "Raw wiring diagnostic"),
        )
    )
    body = (
        '<div id="adc-debug" data-ws-path="/debug/adc/ws">'
        "<h1>ADC debug &amp; calibration</h1>"
        '<p class="sub">Live raw ADS1115 values and exactly how the player '
        "interprets the current calibration.</p>"
        f"{_flash_block(message, error)}"
        '<p><span id="adc-connection" class="badge off">Connecting…</span> '
        '<small id="adc-updated">No sample received</small></p>'
        f'<section class="adc-grid">{channel_cards}</section>'
        '<form method="post" action="/debug/adc" id="adc-calibration">'
        f"{csrf}"
        '<div class="card"><h2>Volume calibration</h2>'
        '<p class="note">Turn the knob fully down/up and capture each endpoint.</p>'
        '<div class="adc-fields">'
        f"{field('volume_min_input', 'Minimum')}"
        f"{field('volume_max_input', 'Maximum')}</div>"
        '<p class="btnrow"><button class="btn-secondary" type="button" data-adc-capture="volume_min_input" '
        'data-channel="0">Capture fully down</button>'
        '<button class="btn-secondary" type="button" data-adc-capture="volume_max_input" data-channel="0">'
        "Capture fully up</button></p></div>"
        '<div class="card"><h2>Button ladder</h2>'
        '<p class="note">Capture button 1 and button 6. The page derives the '
        "six-band boundaries; tune tolerance only if necessary.</p>"
        '<div class="adc-fields">'
        f"{field('button_min', 'Ladder minimum')}{field('button_max', 'Ladder maximum')}"
        f"{field('button_tolerance', 'Tolerance')}</div>"
        '<p class="btnrow"><button class="btn-secondary" type="button" data-button-end="1" data-channel="1">'
        'Capture button 1</button><button class="btn-secondary" type="button" data-button-end="6" '
        'data-channel="1">Capture button 6</button></p></div>'
        '<div class="card"><h2>Power switch</h2>'
        '<p class="note">Capture both positions; the midpoint becomes the threshold.</p>'
        f'<div class="adc-fields">{field("switch_threshold", "ON/OFF threshold")}</div>'
        '<p class="btnrow"><button class="btn-secondary" type="button" data-power-end="on" data-channel="2">'
        'Capture ON</button><button class="btn-secondary" type="button" data-power-end="off" data-channel="2">'
        "Capture OFF</button></p></div>"
        '<p class="btnrow"><button class="btn-primary" type="submit" name="op" value="save">Save</button>'
        '<button class="btn-apply" type="submit" name="op" value="apply">Save and restart radio</button>'
        '<button class="btn-danger" type="submit" name="op" value="restore">Restore defaults</button></p>'
        '</form><p><a href="/device">Back to Device settings</a></p></div>'
    )
    return _page("ADC debug & calibration", body, script="/static/app.js?v=4")


# --- Network (WiFi + static IP) page (Area C) -------------------------------


def _wifi_pending_banner(csrf_token: str, pending: Dict[str, Any]) -> str:
    """Render the on-trial confirmation banner when a change is pending."""
    csrf = csrf_field(csrf_token)
    return (
        '<div class="card pending-card">'
        '<h2 class="warn">New WiFi settings on trial</h2>'
        "<p>The radio is trying new WiFi settings. If it does not reconnect "
        "in time it will <strong>automatically revert</strong> to the previous "
        "network. If you can see this page, the connection works — confirm to "
        "keep it.</p>"
        '<form method="post" action="/network" style="display:inline">'
        f"{csrf}"
        '<button class="btn-apply" type="submit" name="op" value="confirm">'
        "Keep these settings</button>"
        "</form> "
        '<form method="post" action="/network" style="display:inline">'
        f"{csrf}"
        '<button class="btn-danger" type="submit" name="op" value="rollback">Revert now</button>'
        "</form>"
        "</div>"
    )


def network_page(
    status: Dict[str, Any],
    csrf_token: str,
    *,
    form: Optional[Dict[str, str]] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    """Render the WiFi + static-IP editor.

    Shows the current SSID/mode read-only, a strong warning that a WiFi change
    is tried with an automatic rollback, and the SSID / passphrase / country /
    DHCP-or-static form. When a change is on trial, a confirm/revert banner is
    shown at the top.
    """
    csrf = csrf_field(csrf_token)
    form = form or {}
    pending = status.get("pending")
    banner = _wifi_pending_banner(csrf_token, pending) if pending else ""
    static_selected = (form.get("ip_mode") or status.get("mode")) == "static"
    body = (
        "<h1>WiFi &amp; network</h1>"
        '<p class="sub">Change the WiFi network or set a static IP address.</p>'
        f"{_flash_block(message, error)}"
        f"{banner}"
        '<div class="card"><h2>Current</h2><dl>'
        f"<dt>SSID</dt><dd>{_esc(status.get('ssid'))}</dd>"
        f"<dt>Addressing</dt><dd>{_esc(status.get('mode'))}</dd>"
        "</dl></div>"
        '<div class="card"><h2>Change WiFi</h2>'
        '<p class="warn">Changing WiFi may briefly drop this connection. The '
        "new settings are tried first and revert automatically if the radio "
        "cannot reconnect, so you cannot lock yourself out. The SD card "
        "<code>radio-config.txt</code> stays the recovery route.</p>"
        '<form method="post" action="/network">'
        f"{csrf}"
        "<p><label>Network name (SSID)<br>"
        f'<input type="text" name="ssid" maxlength="32" '
        f'value="{_esc(form.get("ssid", status.get("ssid") or ""))}" required>'
        "</label></p>"
        "<p><label>Password (8–63 characters)<br>"
        '<input type="password" name="psk" autocomplete="off" required>'
        "</label></p>"
        "<p><label>Country code (optional, e.g. DE)<br>"
        f'<input type="text" name="country" maxlength="2" '
        f'value="{_esc(form.get("country", ""))}"></label></p>'
        "<fieldset><legend>Addressing</legend>"
        "<p><label>"
        f'<input type="radio" name="ip_mode" value="dhcp"'
        f"{'' if static_selected else ' checked'}> Automatic (DHCP)</label></p>"
        "<p><label>"
        f'<input type="radio" name="ip_mode" value="static"'
        f"{' checked' if static_selected else ''}> Static IP</label></p>"
        "<p><label>IP address<br>"
        f'<input type="text" name="ip_address" '
        f'value="{_esc(form.get("ip_address", ""))}" '
        'placeholder="192.168.1.42"></label></p>'
        "<p><label>Prefix length (CIDR)<br>"
        f'<input type="number" name="ip_prefix" min="1" max="32" '
        f'value="{_esc(form.get("ip_prefix", "24"))}"></label></p>'
        "<p><label>Gateway<br>"
        f'<input type="text" name="ip_gateway" '
        f'value="{_esc(form.get("ip_gateway", ""))}" '
        'placeholder="192.168.1.1"></label></p>'
        "<p><label>DNS servers (space-separated, optional)<br>"
        f'<input type="text" name="ip_dns" '
        f'value="{_esc(form.get("ip_dns", ""))}" '
        'placeholder="192.168.1.1 1.1.1.1"></label></p>'
        "</fieldset>"
        '<p class="btnrow">'
        '<button class="btn-apply" type="submit" name="op" value="apply">'
        "Save and try</button>"
        "</p></form></div>"
        f"{_PLAIN_HTTP_NOTICE}"
        '<p><a href="/">Back to the dashboard</a></p>'
    )
    return _page("WiFi & network", body)
