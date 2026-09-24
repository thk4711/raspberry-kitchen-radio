"""Server-rendered templates for display audio."""

from typing import Any, Dict, List, Mapping, Optional

from . import equalizer_store
from .template_common import (
    _esc,
    _flash_block,
    _page,
    csrf_field,
)

_PANEL_OPTIONS = [
    ("st7789", "ST7789 — 1.69\u2033 240\u00d7280 rectangular"),
    ("gc9a01", "GC9A01 — 1.28\u2033 240\u00d7240 round"),
]


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


def _loudness_level_select(current: Any) -> str:
    """Render the loudness-level ``<select>`` with integer options 0..max.

    The persisted amount is a float (e.g. ``5.0``); the dropdown only offers the
    whole integers in ``[LOUDNESS_MIN_AMOUNT, LOUDNESS_MAX_AMOUNT]``, so pick the
    option nearest the stored value, clamped into range.
    """
    minimum = int(equalizer_store.LOUDNESS_MIN_AMOUNT)
    maximum = int(equalizer_store.LOUDNESS_MAX_AMOUNT)
    try:
        selected_value = int(round(float(current)))
    except (TypeError, ValueError):
        selected_value = minimum
    selected_value = max(minimum, min(maximum, selected_value))
    options = "".join(
        f'<option value="{value}"{" selected" if value == selected_value else ""}>'
        f"{value}</option>"
        for value in range(minimum, maximum + 1)
    )
    return (
        '<select name="loudness_amount" '
        'title="How strong the low-volume bass/treble boost is. 0 disables it; 10 is the '
        'full Fletcher-Munson smile. The boost always tapers to nothing at full volume.">'
        f"{options}</select>"
    )


def settings_page(
    display: Dict[str, str],
    csrf_token: str,
    *,
    artwork: Optional[Mapping[str, str]] = None,
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
    online_artwork_on = str((artwork or {}).get("enabled", "false")).lower() == "true"
    body = (
        "<h1>Display</h1>"
        '<p class="sub">A few safe display options. Changes take '
        "effect after the radio restarts.</p>"
        f"{_flash_block(message, error)}"
        '<form method="post" action="/settings" id="display-settings">'
        f"{csrf}"
        '<div class="card"><h2>Display</h2>'
        "<p><label>Display panel<br>"
        f"{_select('panel', _PANEL_OPTIONS, display.get('panel', 'st7789'))}"
        "</label></p>"
        '<p class="note">Changing the panel type takes effect after the radio restarts. '
        "ST7789 is the default 1.69\u2033 rectangular panel; "
        "GC9A01 is the 1.28\u2033 round panel.</p>"
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
        '<div class="card"><h2>Bluetooth cover art</h2>'
        f"<p>{_checkbox('online_artwork_enabled', online_artwork_on, 'Fetch missing Bluetooth cover art from MusicBrainz / Cover Art Archive')}</p>"
        '<p class="note">When enabled, the artist, title, and possibly album are sent '
        'to MusicBrainz. Artwork is downloaded from Cover Art Archive or Internet '
        'Archive. No account or API key is required. Disabling this option prevents '
        'those requests, and the Bluetooth glyph remains the fallback.</p>'
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
        f'{" checked" if equalizer["enabled"] else ""}> Enable equalizer</label></div>'
        '<div class="eq-loudness-row">'
        '<label class="eq-loudness-level">Loudness level '
        f'{_loudness_level_select(equalizer["loudness_amount"])}'
        '</label>'
        '<span class="note eq-loudness-hint">Boosts bass and a little treble at low '
        'listening volumes and fades out as you turn up (Fletcher-Munson loudness '
        'compensation). It follows the volume knob live and needs the equalizer enabled. '
        'Set the level to 0 to turn loudness off.</span>'
        '</div>'
        '<p class="eq-preamp" '
        'title="Set automatically to prevent clipping from your enabled bands and '
        'loudness level. It equals minus the largest boost in the chain, clamped to '
        'the -24..0 dB range.">'
        '<span class="eq-preamp-label">Preamp gain</span> '
        f'<span class="eq-preamp-value" data-eq-preamp>{_esc(equalizer["preamp_db"])}</span>'
        ' dB</p>'
        '<p class="note eq-preamp-hint">Preamp gain is set automatically to reserve '
        "headroom so your enabled bands and loudness level never clip. It equals about "
        "minus the largest boost in the chain (0 dB when nothing is boosted).</p>"
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
    return _page("Audio", body, script="/static/app.js?v=20")


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
