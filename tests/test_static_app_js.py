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


def test_dashboard_uses_public_player_api_when_node_available():
    node = shutil.which("node")
    if node is None:
        return
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");

function button(action) {
  return {
    dataset: { playbackAction: action, playbackEnabled: "true" },
    disabled: false,
    closest() { return this; },
    getAttribute(name) { return name === "aria-label" ? action : ""; },
  };
}
function element(dataset = {}) {
  return {
    dataset, hidden: false, textContent: "", className: "", attributes: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
  };
}
const status = {
  textContent: "",
  dataset: { defaultMessage: "" },
  classList: { toggle() {} },
};
const buttons = [button("previous"), button("play"), button("pause"), button("next")];
let clickHandler;
let intervalCallback;
const elements = {
  "[data-player-stale]": element(),
  "[data-player-unavailable]": element(),
  "[data-player-details]": element(),
  "[data-player-power]": element(),
  "[data-player-playing]": element(),
  "[data-player-source]": element(),
  "[data-player-name]": element(),
  "[data-player-title]": element(),
  "[data-player-artwork]": element(),
  "[data-player-artwork-image]": element(),
  "[data-player-artwork-fallback]": element(),
};
const sourceState = element({ playerSourceState: "spotify" });
const region = {
  addEventListener(type, handler) { if (type === "click") clickHandler = handler; },
  querySelectorAll() { return buttons; },
  querySelector(selector) {
    return selector === ".playback-control-status" ? status : elements[selector] || null;
  },
  contains(candidate) { return buttons.includes(candidate); },
};
global.document = {
  hidden: false,
  getElementById(id) { return id === "now-playing" ? region : null; },
  querySelectorAll(selector) {
    return selector === "[data-player-source-state]" ? [sourceState] : [];
  },
  addEventListener() {},
};
global.window = {
  setInterval(callback) { intervalCallback = callback; },
  clearTimeout() {},
  location: {},
};
const pending = [];
const apiPaths = [];
let metadataCalls = 0;
let player = {
  available: true,
  stale: false,
  power: true,
  active_source: "spotify",
  playing: true,
  metadata: {
    name: "Artist",
    title: "Track",
    artwork: { id: "spotify", version: "abc", url: "/dashboard/artwork?id=spotify&v=abc" },
  },
  sources: { spotify: { playing: true } },
};
global.fetch = (path, options) => {
  if (path === "/api/v1/player" && !options.method) {
    assert.strictEqual(options.credentials, "omit");
    assert.strictEqual(options.cache, "no-store");
    assert.strictEqual(options.headers.Accept, "application/json");
    metadataCalls += 1;
    return Promise.resolve({ ok: true, json: async () => player });
  }
  apiPaths.push([path, options]);
  return new Promise((resolve) => pending.push(resolve));
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
(async () => {
  await settle(); await settle();
  assert.strictEqual(metadataCalls, 1, "dashboard must fetch metadata immediately");
  assert.strictEqual(elements["[data-player-name]"].textContent, "Artist");
  assert.strictEqual(elements["[data-player-title]"].textContent, "Track");
  assert.strictEqual(elements["[data-player-artwork-image]"].attributes.src,
    "/dashboard/artwork?id=spotify&v=abc");
  assert.strictEqual(sourceState.textContent, "Active");
  assert.strictEqual(buttons[1].disabled, true, "Play is disabled while playing");

  clickHandler({ target: buttons[0] });
  assert(buttons.every((item) => item.disabled));
  clickHandler({ target: buttons[1] });
  assert.strictEqual(apiPaths.length, 1, "in-flight command must suppress duplicate clicks");
  assert.strictEqual(apiPaths[0][0], "/api/v1/player/previous");
  assert.strictEqual(apiPaths[0][1].method, "POST");
  assert.strictEqual(apiPaths[0][1].credentials, "omit");
  assert.strictEqual(apiPaths[0][1].headers["Content-Type"], "application/json");
  assert.strictEqual(apiPaths[0][1].body, "{}");
  pending.shift()({ ok: true, json: async () => ({ ok: true, message: "Accepted" }) });
  await settle(); await settle();
  assert.strictEqual(metadataCalls, 2, "accepted command must refresh public metadata");
  assert.strictEqual(status.textContent, "", "accepted commands must not show a status message");

  clickHandler({ target: buttons[3] });
  assert.strictEqual(apiPaths[1][0], "/api/v1/player/next");
  pending.shift()({ ok: true, json: async () => ({ ok: true, message: "Accepted" }) });
  await settle(); await settle();

  player = {};
  await intervalCallback();
  assert.strictEqual(elements["[data-player-unavailable]"].hidden, false);
  assert.strictEqual(elements["[data-player-unavailable]"].textContent,
    "Player metadata API unavailable.");
  assert(buttons.every((item) => item.disabled));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness, str(APP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
