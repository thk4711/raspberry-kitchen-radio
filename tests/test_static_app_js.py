"""Guard against JavaScript syntax breakage in the bundled static app.js.

A dropped brace in ``radio_web/static/app.js`` silently disables the whole
front-end (the equalizer graph fails to render and the EQ form falls back to a
full-page reload). These checks catch that class of regression without a Node
toolchain in CI: they run ``node --check`` when Node is available and always run
a lightweight bracket-balance check that ignores strings, template literals,
comments and regex-ish literals well enough for this file.
"""

import shutil
import subprocess
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "radio_web" / "static" / "app.js"


def _strip_noncode(source: str) -> str:
    """Return ``source`` with strings, template literals and comments blanked.

    Not a full JS lexer — just enough to make bracket counting reliable for the
    hand-written app.js (no regex literals containing unbalanced brackets).
    """
    out = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            i += 2
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < n and not (source[i] == "*" and i + 1 < n and source[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def test_app_js_brackets_balanced():
    code = _strip_noncode(APP_JS.read_text(encoding="utf-8"))
    for open_ch, close_ch in (("{", "}"), ("(", ")"), ("[", "]")):
        assert code.count(open_ch) == code.count(close_ch), (
            f"Unbalanced {open_ch}{close_ch} in app.js "
            f"({code.count(open_ch)} vs {code.count(close_ch)})"
        )


def test_app_js_node_check_when_available():
    node = shutil.which("node")
    if node is None:
        return  # Node not installed here; the balance check above still guards.
    result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        [node, "--check", str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
