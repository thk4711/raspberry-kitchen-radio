"""Server-rendered templates for stations sources."""

from typing import Any, Dict, List, Optional

from .template_common import (
    _esc,
    _flash_block,
    _page,
    csrf_field,
)


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
