"""Tests for the passwordless public player API."""

import json
import time
import urllib.request

import pytest

from radio_web import auth, route_player, routes, server


def _request(method, path, media_type="", body=b"{}"):
    return routes.Request(method=method, path=path, media_type=media_type, body=body)


def _payload(response):
    status, content_type, body, headers = response
    assert content_type == "application/json; charset=utf-8"
    assert headers == [("Cache-Control", "no-store")]
    return status, json.loads(body)


@pytest.fixture
def running_api_server(monkeypatch):
    monkeypatch.setattr(server, "_wlan_ipv4", lambda: None)
    servers = server.serve(port=0, forever=False)
    host, port = servers[0].server_address
    time.sleep(0.05)
    try:
        yield host, port
    finally:
        for instance in servers:
            instance.shutdown()
            instance.server_close()


def test_metadata_is_public_and_projects_only_safe_snapshot_fields(monkeypatch):
    monkeypatch.setattr(
        route_player.system_status,
        "player_status",
        lambda: {
            "available": True,
            "stale": False,
            "power": True,
            "active_source": "spotify",
            "updated_at": 123,
            "now_playing": {
                "name": "Artist",
                "title": "Track",
                "album": "Album",
                "state": False,
                "cover": "/tmp/private-cover.jpg",
                "stream_url": "https://secret.example/stream",
                "artwork": {"id": "spotify", "version": "abc123", "path": "/tmp/x"},
            },
            "sources": {
                "spotify": {"playing": True, "backend": "private"},
                "../../secret": {"playing": True},
            },
            "credentials": "secret",
        },
    )

    status, payload = _payload(routes.resolve(_request("GET", "/api/v1/player")))

    assert status == 200
    assert payload == {
        "available": True,
        "stale": False,
        "power": True,
        "active_source": "spotify",
        "playing": True,
        "metadata": {
            "name": "Artist",
            "title": "Track",
            "album": "Album",
            "artwork": {
                "id": "spotify",
                "version": "abc123",
                "url": "/dashboard/artwork?id=spotify&v=abc123",
            },
        },
        "sources": {"spotify": {"playing": True}},
    }
    assert "/tmp" not in json.dumps(payload)
    assert "secret.example" not in json.dumps(payload)


def test_metadata_reports_unavailable_snapshot_with_stable_schema(monkeypatch):
    monkeypatch.setattr(
        route_player.system_status,
        "player_status",
        lambda: {
            "available": False,
            "stale": True,
            "power": None,
            "active_source": None,
            "now_playing": {},
            "sources": {},
        },
    )

    status, payload = _payload(routes.resolve(_request("GET", "/api/v1/player")))

    assert status == 200
    assert payload == {
        "available": False,
        "stale": True,
        "power": None,
        "active_source": None,
        "playing": None,
        "metadata": {"name": "", "title": "", "album": "", "artwork": {}},
        "sources": {},
    }


@pytest.mark.parametrize("action", ["play", "pause", "next", "previous"])
def test_control_routes_are_passwordless_and_dispatch_fixed_actions(monkeypatch, action):
    seen = []
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda selected: seen.append(selected)
        or {
            "ok": True,
            "code": "accepted",
            "message": "internal message is not exposed",
            "source": "mpd",
        },
    )

    status, payload = _payload(
        routes.resolve(_request("POST", f"/api/v1/player/{action}", media_type="application/json"))
    )

    assert status == 200
    assert payload == {
        "ok": True,
        "code": "accepted",
        "message": "Playback command accepted",
        "source": "mpd",
    }
    assert seen == [action]


def test_player_api_does_not_use_administration_authentication(monkeypatch):
    monkeypatch.setattr(auth, "require_auth", lambda _session: pytest.fail("auth must not run"))
    monkeypatch.setattr(route_player.system_status, "player_status", lambda: {})
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda _action: {
            "ok": True,
            "code": "accepted",
            "message": "Accepted",
            "source": "mpd",
        },
    )

    assert routes.resolve(_request("GET", "/api/v1/player"))[0] == 200
    assert (
        routes.resolve(_request("POST", "/api/v1/player/play", media_type="application/json"))[0]
        == 200
    )


@pytest.mark.parametrize("media_type", ["", "text/plain", "application/problem+json"])
def test_control_requires_json_without_contacting_player(monkeypatch, media_type):
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda _action: pytest.fail("player must not be contacted"),
    )

    status, payload = _payload(
        routes.resolve(_request("POST", "/api/v1/player/play", media_type=media_type))
    )

    assert status == 415
    assert payload == {
        "ok": False,
        "code": "unsupported_media_type",
        "message": "Use application/json.",
        "source": "",
    }


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "invalid_json"),
        (b"not-json", "invalid_json"),
        (b"[]", "invalid_request"),
        (b'{"source":"mpd"}', "invalid_request"),
    ],
)
def test_control_requires_an_empty_json_object_without_contacting_player(monkeypatch, body, code):
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda _action: pytest.fail("player must not be contacted"),
    )

    status, payload = _payload(
        routes.resolve(
            _request(
                "POST",
                "/api/v1/player/play",
                media_type="application/json",
                body=body,
            )
        )
    )

    assert status == 400
    assert payload["code"] == code
    assert payload["source"] == ""


@pytest.mark.parametrize(
    ("code", "http_status"),
    [("power_off", 409), ("command_failed", 409), ("internal_error", 500)],
)
def test_control_maps_player_rejections(monkeypatch, code, http_status):
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda _action: {"ok": False, "code": code, "message": "detail", "source": "usb"},
    )

    status, payload = _payload(
        routes.resolve(_request("POST", "/api/v1/player/next", media_type="application/json"))
    )

    assert status == http_status
    assert payload == {
        "ok": False,
        "code": code,
        "message": {
            "power_off": "Power switch is off",
            "command_failed": "Playback command was rejected",
            "internal_error": "Playback command failed",
        }[code],
        "source": "usb",
    }
    assert "detail" not in json.dumps(payload)


@pytest.mark.parametrize(
    "result",
    [
        {"ok": True, "code": "unknown", "message": "detail", "source": "mpd"},
        {"ok": False, "code": "accepted", "message": "detail", "source": "mpd"},
    ],
)
def test_control_rejects_invalid_player_results(monkeypatch, result):
    monkeypatch.setattr(route_player, "_exchange", lambda _action: result)

    status, payload = _payload(
        routes.resolve(_request("POST", "/api/v1/player/play", media_type="application/json"))
    )

    assert status == 503
    assert payload == {
        "ok": False,
        "code": "player_unavailable",
        "message": "The player is unavailable.",
        "source": "",
    }


def test_control_returns_503_when_player_socket_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(route_player.player_protocol, "SOCKET_PATH", str(tmp_path / "missing.sock"))

    status, payload = _payload(
        routes.resolve(_request("POST", "/api/v1/player/pause", media_type="application/json"))
    )

    assert status == 503
    assert payload["code"] == "player_unavailable"


def test_api_unknown_paths_and_wrong_methods_return_json():
    wrong_method, wrong_payload = _payload(routes.resolve(_request("GET", "/api/v1/player/play")))
    not_found, missing_payload = _payload(routes.resolve(_request("GET", "/api/v1/unknown")))

    assert wrong_method == 405
    assert wrong_payload["code"] == "method_not_allowed"
    assert not_found == 404
    assert missing_payload["code"] == "not_found"


def test_http_api_parses_json_media_type_and_emits_safe_headers(monkeypatch, running_api_server):
    monkeypatch.setattr(
        route_player,
        "_exchange",
        lambda _action: {
            "ok": True,
            "code": "accepted",
            "message": "accepted",
            "source": "spotify",
        },
    )
    host, port = running_api_server
    request = urllib.request.Request(
        f"http://{host}:{port}/api/v1/player/play",
        data=b"{}",
        headers={"Content-Type": "Application/JSON; charset=UTF-8"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        payload = json.load(response)
        assert response.status == 200
        assert response.headers["Content-Type"] == "application/json; charset=utf-8"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers.get("Access-Control-Allow-Origin") is None
    assert payload["code"] == "accepted"
