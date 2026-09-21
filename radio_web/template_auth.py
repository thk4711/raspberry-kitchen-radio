"""Server-rendered templates for auth."""

from typing import Optional

from .template_common import (
    _PLAIN_HTTP_NOTICE,
    _error_block,
    _page,
)


def login_page(error: Optional[str] = None) -> str:
    """Render the login form."""
    body = (
        "<h1>Sign in</h1>"
        '<p class="sub">PiSonic administration</p>'
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
