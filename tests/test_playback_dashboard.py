"""Regression tests for dashboard playback controls."""

import re
from pathlib import Path

from radio_web import template_dashboard

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "radio_web/static/app.js"
APP_CSS = ROOT / "radio_web/static/app.css"


def _player(*, available=True, power=True, playing=None):
    sources = {}
    if playing is not None:
        sources = {"spotify": {"playing": playing}}
    return {
        "available": available,
        "stale": False,
        "power": power,
        "active_source": "spotify",
        "playing": playing,
        "metadata": {"name": "Artist", "title": "Track", "artwork": {}},
        "sources": sources,
    }


def _buttons(html):
    result = []
    for tag in re.findall(r"<button [^>]+data-playback-action=[^>]+>", html):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
        attrs["disabled"] = " disabled" in tag
        result.append(attrs)
    return result


def test_controls_follow_artwork_in_required_accessible_order():
    html = template_dashboard._now_playing_card(_player(playing=True))
    buttons = _buttons(html)

    assert html.index('class="artwork artwork-fallback"') < html.index('class="playback-controls"')
    assert [button["data-playback-action"] for button in buttons] == [
        "previous",
        "play",
        "pause",
        "next",
    ]
    assert [button["aria-label"] for button in buttons] == [
        "Previous",
        "Play",
        "Pause",
        "Next",
    ]
    assert [button["title"] for button in buttons] == ["Previous", "Play", "Pause", "Next"]
    assert 'role="status" aria-live="polite"' in html


def test_control_disabled_states_follow_player_availability_and_state():
    unavailable = _buttons(template_dashboard._now_playing_card(_player(available=False)))
    powered_off = _buttons(template_dashboard._now_playing_card(_player(power=False)))
    playing = _buttons(template_dashboard._now_playing_card(_player(playing=True)))
    stopped = _buttons(template_dashboard._now_playing_card(_player(playing=False)))
    unknown = _buttons(template_dashboard._now_playing_card(_player()))

    assert all(button["disabled"] for button in unavailable)
    assert all(button["disabled"] for button in powered_off)
    assert [button["disabled"] for button in playing] == [False, True, False, False]
    assert [button["disabled"] for button in stopped] == [False, False, True, False]
    assert not any(button["disabled"] for button in unknown)
    assert "Playback controls unavailable." in template_dashboard._now_playing_card(
        _player(available=False)
    )
    assert "Playback controls disabled while power is off." in (
        template_dashboard._now_playing_card(_player(power=False))
    )


def test_javascript_uses_public_api_json_and_separate_request_state():
    source = APP_JS.read_text(encoding="utf-8")

    assert 'region.addEventListener("click"' in source
    assert 'event.target.closest("[data-playback-action]")' in source
    assert 'fetch("/api/v1/player"' in source
    assert 'headers: { Accept: "application/json" }' in source
    assert 'credentials: "omit"' in source
    assert "/dashboard/now-playing" not in source
    assert "renderPlayer(player)" in source
    assert 'headers: { "Content-Type": "application/json", Accept: "application/json" }' in source
    assert "body: JSON.stringify({})" in source
    assert "await refreshNowPlaying(true)" in source
    assert "let refreshRequest = null" in source
    assert "let controlRequestInFlight = false" in source
    assert "button.disabled = controlRequestInFlight" in source
    assert 'controlMessage = ""' in source
    assert 'controlMessage = payload.message || "Playback command accepted."' not in source
    assert "refreshNowPlaying(true);" in source


def test_mobile_css_preserves_touch_targets_and_stacks_controls():
    css = APP_CSS.read_text(encoding="utf-8")
    control_rule = css.split(".playback-control {", 1)[1].split("}", 1)[0]
    tablet = css.split("@media (max-width: 620px) {", 1)[1].split("@media (max-width: 390px) {", 1)[
        0
    ]
    phone = css.split("@media (max-width: 390px) {", 1)[1].split(
        "@media (prefers-reduced-motion", 1
    )[0]

    assert "min-height: 44px" in control_rule
    assert ".playback-controls { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in tablet
    assert ".now-playing-content { grid-template-columns: 1fr; }" in phone
    assert ".now-playing-media { width: min(176px, 100%); justify-self: center; }" in phone
    assert "button:focus-visible" in css


def test_now_playing_spacing_prioritizes_artwork_separation():
    css = APP_CSS.read_text(encoding="utf-8")
    content_rule = css.split(".now-playing-content {", 1)[1].split("}", 1)[0]
    details_rule = css.split(".now-playing dl {", 1)[1].split("}", 1)[0]

    assert "gap: 2.5rem" in content_rule
    assert "grid-template-columns: max-content minmax(0, 1fr)" in details_rule
    assert "column-gap: 0.8rem" in details_rule
