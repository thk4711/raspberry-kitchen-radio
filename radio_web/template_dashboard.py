"""Server-rendered templates for dashboard."""

from typing import Any, Dict, Optional

from .template_common import (
    _esc,
    _fmt_bytes,
    _fmt_seconds_ago,
    _fmt_uptime,
    _page,
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


def _playback_controls(*, available: bool, power_on: bool, is_playing: Optional[bool]) -> str:
    controls_enabled = available and power_on
    if not available:
        default_message = "Playback controls unavailable."
    elif not power_on:
        default_message = "Playback controls disabled while power is off."
    else:
        default_message = ""

    buttons = []
    for action, label, symbol in (
        ("previous", "Previous", "&#9198;"),
        ("play", "Play", "&#9654;"),
        ("pause", "Pause", "&#10074;&#10074;"),
        ("next", "Next", "&#9197;"),
    ):
        enabled = controls_enabled
        if action == "play" and is_playing is True:
            enabled = False
        elif action == "pause" and is_playing is False:
            enabled = False
        disabled = "" if enabled else " disabled"
        buttons.append(
            f'<button type="button" class="playback-control" data-playback-action="{action}" '
            f'data-playback-enabled="{str(enabled).lower()}" aria-label="{label}" '
            f'title="{label}"{disabled}><span aria-hidden="true">{symbol}</span></button>'
        )

    return (
        '<div class="playback-controls" role="group" aria-label="Playback controls">'
        f"{''.join(buttons)}</div>"
        '<p class="playback-control-status" role="status" aria-live="polite" '
        f'aria-atomic="true" data-default-message="{_esc(default_message)}">'
        f"{_esc(default_message)}</p>"
    )


def _now_playing_card(player: Dict[str, Any]) -> str:
    available = player.get("available") is True
    raw_metadata = player.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    stale_hidden = "" if available and player.get("stale") else " hidden"
    power_on = bool(player.get("power"))
    power_badge = (
        '<span class="badge on" data-player-power>on</span>'
        if power_on
        else '<span class="badge off" data-player-power>off</span>'
    )
    active_source = player.get("active_source")
    is_playing = player.get("playing") if isinstance(player.get("playing"), bool) else None
    if is_playing is True:
        playback_badge = '<span class="badge on" data-player-playing>Playing</span>'
    elif is_playing is False:
        playback_badge = '<span class="badge off" data-player-playing>Not playing</span>'
    else:
        playback_badge = '<span class="badge off" data-player-playing>Unknown</span>'
    artwork = metadata.get("artwork", {})
    art_url = artwork.get("url") if isinstance(artwork, dict) else ""
    image_hidden = "" if art_url else " hidden"
    fallback_hidden = " hidden" if art_url else ""
    image_source = f' src="{_esc(art_url)}"' if art_url else ""
    unavailable_hidden = " hidden" if available else ""
    details_hidden = "" if available else " hidden"
    return (
        '<div class="card now-playing"><div class="now-playing-content">'
        '<div class="now-playing-media">'
        f'<div class="artwork" data-player-artwork{image_hidden}>'
        f'<img data-player-artwork-image{image_source} '
        f'alt="Artwork for {_esc(metadata.get("name") or "now playing")}" '
        'width="176" height="176"></div>'
        f'<div class="artwork artwork-fallback" data-player-artwork-fallback '
        f'aria-hidden="true"{fallback_hidden}>♪</div>'
        f"{_playback_controls(available=available, power_on=power_on, is_playing=is_playing)}"
        "</div>"
        '<div class="now-playing-details"><h2>Now playing '
        f'<span class="badge off" data-player-stale{stale_hidden}>stale</span></h2>'
        f'<p class="warn" data-player-unavailable{unavailable_hidden}>'
        "Player status unavailable — the radio process may be starting or stopped.</p>"
        f'<dl data-player-details{details_hidden}>'
        f"<dt>Power</dt><dd>{power_badge}</dd>"
        f"<dt>Playback</dt><dd>{playback_badge}</dd>"
        f'<dt>Active source</dt><dd data-player-source>{_esc(active_source)}</dd>'
        f'<dt>Station / app</dt><dd data-player-name>{_esc(metadata.get("name"))}</dd>'
        f'<dt>Track</dt><dd data-player-title>{_esc(metadata.get("title"))}</dd>'
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
            badge_class = ""
        else:
            entry = sources.get(key, {}) or {}
            playing = bool(entry.get("playing"))
            if key == active and playing:
                state = "Active"
                badge_class = "badge on"
            elif playing:
                state = "Playing"
                badge_class = "badge on"
            else:
                state = "Ready"
                badge_class = "badge off"
        rows.append(
            f'<tr><td>{_esc(label)}</td><td><span class="{badge_class}" '
            f'data-player-source-state="{_esc(key)}">{state}</span></td></tr>'
        )
    return (
        '<div class="card sources-card"><h2>Sources</h2><table>'
        "<tr><th>Source</th><th>State</th></tr>"
        f"{''.join(rows)}</table></div>"
    )


def dashboard(status: Dict[str, Any]) -> str:
    """Render the public dashboard page from sanitized status values.

    Every dynamic value is HTML-escaped. Playback metadata and controls use the
    passwordless public player API; administration remains authenticated separately.
    """
    host = _esc(status.get("hostname") or "PiSonic")
    incomplete = status.get("provisioning", [])
    setup_warning = ""
    if incomplete:
        labels = ", ".join(_esc(value) for value in incomplete)
        setup_warning = (
            '<div class="setup-warning" role="alert"><strong>Setup incomplete.</strong> '
            f"Configure {labels} in <code>pisonic-config.txt</code> on the SD card. "
            "Known example credentials are rejected."
            "</div>"
        )
    body = (
        f"<h1>{host}</h1>"
        '<p class="sub">Playback dashboard</p>'
        f"{setup_warning}"
        '<div class="dashboard-grid">'
        '<div id="now-playing" class="now-playing-region">'
        f"{_now_playing_card(status.get('player', {}))}"
        "</div>"
        f"{_sources_card(status)}"
        f"{_system_card(status)}"
        f"{_bluetooth_card(status)}"
        "</div>"
        '<p class="note">Logs and this status are stored in volatile memory '
        "(tmpfs) and are cleared on reboot.</p>"
    )
    return _page("PiSonic", body, script="/static/app.js?v=4")


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
