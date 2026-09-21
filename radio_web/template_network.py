"""Server-rendered templates for network."""

from typing import Any, Dict, Optional

from .template_common import (
    _PLAIN_HTTP_NOTICE,
    _esc,
    _flash_block,
    _page,
    csrf_field,
)


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
        "<code>pisonic-config.txt</code> stays the recovery route.</p>"
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
