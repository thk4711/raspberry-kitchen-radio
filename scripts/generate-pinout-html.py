#!/usr/bin/env python3
"""Generate a color-coded HTML pinout page from doc/sound-devices.md.

Each ``<!-- device:TAG -->`` annotation in the Markdown source wraps the
following text (up to the next ``|`` cell boundary) in a ``<span>`` with a
background color that identifies the owning subsystem:

    display   – SPI display (ST7789)         blue tint
    adc       – ADS1115 ADC on I2C           amber tint
    sound     – I2S/PCM sound card signals   green tint
    amp-gpio  – amplifier-control GPIOs      gold tint
    uart      – UART0 TX/RX                  red tint
    free      – pin not used by this profile grey tint

Usage (from the repository root)::

    python3 scripts/generate-pinout-html.py
    # writes doc/sound-devices.html

    python3 scripts/generate-pinout-html.py --check
    # exits non-zero if the output file is out of date
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "doc" / "sound-devices.md"
OUTPUT = ROOT / "doc" / "sound-devices.html"

COLORS: dict[str, tuple[str, str]] = {
    "display": ("#d0eaff", "#004080"),
    "adc": ("#fff0d0", "#804000"),
    "sound": ("#d0ffd4", "#005010"),
    "amp-gpio": ("#fffdd0", "#504000"),
    "uart": ("#ffd4d4", "#500000"),
    "free": ("#f0f0f0", "#555555"),
}

LABELS: dict[str, str] = {
    "display": "SPI display",
    "adc": "ADC (ADS1115)",
    "sound": "Sound card (I2S/PCM)",
    "amp-gpio": "Amp GPIO controls",
    "uart": "UART",
    "free": "Free / available",
}

_ANNOTATION_RE = re.compile(
    r"<!-- device:([a-z\-]+) -->(.*?)(?=\s*(?:\||$))",
    re.DOTALL,
)


def _replace_annotation(match: re.Match) -> str:
    tag = match.group(1)
    text = match.group(2)
    if tag not in COLORS:
        return match.group(0)
    bg, fg = COLORS[tag]
    style = f"background:{bg};color:{fg};" "padding:1px 4px;border-radius:3px;font-size:0.95em;"
    return f'<span style="{style}">{text}</span>'


def _inline(text: str) -> str:
    """Convert inline Markdown (bold, code, links) and device annotations."""
    text = _ANNOTATION_RE.sub(_replace_annotation, text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def convert(source: str) -> str:  # noqa: C901
    lines = source.splitlines()
    html_parts: list[str] = []
    in_table = False
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            html_parts.append(f"<p>{_inline(' '.join(paragraph_lines))}</p>")
        paragraph_lines.clear()

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # fenced code block
        if stripped.startswith("```"):
            if not in_code:
                flush_paragraph()
                code_lang = stripped[3:].strip()
                in_code = True
                code_lines = []
            else:
                lang_cls = f' class="language-{code_lang}"' if code_lang else ""
                body = "\n".join(_escape(ln) for ln in code_lines)
                html_parts.append(f"<pre><code{lang_cls}>{body}</code></pre>")
                in_code = False
                code_lines = []
            i += 1
            continue

        if in_code:
            code_lines.append(raw)
            i += 1
            continue

        # HTML comment – skip
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            i += 1
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            flush_paragraph()
            if in_table:
                html_parts.append("</table>")
                in_table = False
            level = len(m.group(1))
            heading_text = _inline(m.group(2))
            anchor = re.sub(r"[^a-z0-9\-]", "", heading_text.lower().replace(" ", "-"))
            anchor = re.sub(r"-+", "-", anchor).strip("-")
            html_parts.append(f'<h{level} id="{anchor}">{heading_text}</h{level}>')
            i += 1
            continue

        # table rows
        if stripped.startswith("|"):
            flush_paragraph()
            if not in_table:
                html_parts.append(
                    '<table border="1" cellpadding="4" cellspacing="0" '
                    'style="border-collapse:collapse;width:100%;">'
                )
                in_table = True
            # separator row
            if re.match(r"^\|[\s\-:|]+\|", stripped):
                i += 1
                continue
            cells = [c.strip() for c in stripped.split("|")]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            # first data row → <th>
            is_header = html_parts and html_parts[-1].startswith("<table")
            tag = "th" if is_header else "td"
            row = "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells)
            html_parts.append(f"<tr>{row}</tr>")
            i += 1
            continue
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False

        # blank line
        if not stripped:
            flush_paragraph()
            i += 1
            continue

        # unordered list
        if stripped.startswith("- ") or stripped.startswith("* "):
            flush_paragraph()
            html_parts.append("<ul>")
            while i < len(lines) and (
                lines[i].strip().startswith("- ") or lines[i].strip().startswith("* ")
            ):
                item = lines[i].strip()[2:]
                html_parts.append(f"<li>{_inline(item)}</li>")
                i += 1
            html_parts.append("</ul>")
            continue

        # blockquote
        if stripped.startswith("> "):
            flush_paragraph()
            html_parts.append(f"<blockquote><p>{_inline(stripped[2:])}</p></blockquote>")
            i += 1
            continue

        # paragraph
        paragraph_lines.append(stripped)
        i += 1

    flush_paragraph()
    if in_table:
        html_parts.append("</table>")

    return "\n".join(html_parts)


def _legend_html() -> str:
    items = []
    for tag, label in LABELS.items():
        bg, fg = COLORS[tag]
        style = (
            f"display:inline-block;background:{bg};color:{fg};"
            "padding:2px 8px;border-radius:3px;margin:2px 4px;"
            "font-size:0.9em;border:1px solid #ccc;"
        )
        items.append(f'<span style="{style}">{label}</span>')
    return (
        '<div style="'
        "position:sticky;top:0;z-index:100;"
        "margin:0 -1.5em;padding:0.5em 1.5em;"
        "background:#fafafa;border-bottom:1px solid #ddd;"
        'box-shadow:0 2px 4px rgba(0,0,0,0.12);">'
        "<strong>Pin description color key:</strong>&nbsp;" + " ".join(items) + "</div>"
    )


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Supported sound devices — Raspberry Pi Radio</title>
<style>
  body {{ font-family: system-ui, sans-serif; line-height: 1.5;
          max-width: 1100px; margin: 0 auto; padding: 1em 1.5em; color: #222; }}
  h1 {{ border-bottom: 2px solid #ccc; padding-bottom: .3em; }}
  h2 {{ border-bottom: 1px solid #eee; padding-bottom: .2em; margin-top: 2em; }}
  h3 {{ margin-top: 1.8em; }}
  table {{ font-size: 0.88em; margin: 0.8em 0 1.2em; }}
  th {{ background: #f0f0f0; text-align: left; }}
  td, th {{ padding: 3px 8px; vertical-align: top; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px;
          font-size: 0.92em; }}
  pre {{ background: #f6f8fa; padding: 1em; border-radius: 4px;
         overflow-x: auto; font-size: 0.85em; }}
  blockquote {{ border-left: 4px solid #ccc; margin: 0;
                padding: 0 1em; color: #555; }}
  a {{ color: #0366d6; }}
</style>
</head>
<body>
{legend}
{body}
</body>
</html>
"""


def build(source_text: str) -> str:
    body = convert(source_text)
    legend = _legend_html()
    return _HTML_TEMPLATE.format(legend=legend, body=body)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the output HTML is missing or out of date.",
    )
    args = parser.parse_args()

    source_text = SOURCE.read_text(encoding="utf-8")
    html = build(source_text)

    if args.check:
        if not OUTPUT.exists():
            print(f"FAIL: {OUTPUT} does not exist; run generate-pinout-html.py")
            sys.exit(1)
        existing = OUTPUT.read_text(encoding="utf-8")
        if existing != html:
            print(f"FAIL: {OUTPUT} is out of date; run generate-pinout-html.py")
            sys.exit(1)
        print(f"OK: {OUTPUT} is up to date")
        return

    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Written: {OUTPUT}")


if __name__ == "__main__":
    main()
