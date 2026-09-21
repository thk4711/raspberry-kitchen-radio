"""Server-rendered templates for dashboard."""

from typing import Any, Dict

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
    host = _esc(status.get("hostname") or "PiSonic")
    incomplete = status.get("provisioning", [])
    setup_warning = ""
    if incomplete:
        labels = ", ".join(_esc(value) for value in incomplete)
        setup_warning = (
            '<div class="setup-warning" role="alert"><strong>Setup incomplete.</strong> '
            f"Configure {labels} in <code>radio-config.txt</code> on the SD card. "
            "Known example credentials are rejected."
            "</div>"
        )
    body = (
        f"<h1>{host}</h1>"
        '<p class="sub">Read-only dashboard</p>'
        f"{setup_warning}"
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
    return _page("PiSonic", body, script="/static/app.js?v=1")


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
