"""Tests for the local player-control protocol and socket server."""

import grp
import os
import socket
import stat
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest import mock

import playback_control_server
import pytest
from playback_control_server import PlaybackControlServer

from radio_web import player_protocol, route_player, routes


def _exchange(path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(path)
        client.sendall(payload)
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
    return player_protocol.decode_response(b"".join(chunks))


def _server(path, handler, *, timeout=0.1):
    group_name = grp.getgrgid(os.getgid()).gr_name
    return PlaybackControlServer(
        handler,
        socket_path=path,
        group_name=group_name,
        request_timeout=timeout,
    )


def test_protocol_round_trip_and_validation():
    raw = player_protocol.encode_request("next")
    assert player_protocol.decode_request(raw) == "next"

    response = player_protocol.encode_response(True, "accepted", "Accepted", "spotify")
    assert player_protocol.decode_response(response) == {
        "ok": True,
        "code": "accepted",
        "message": "Accepted",
        "source": "spotify",
    }

    with pytest.raises(ValueError):
        player_protocol.decode_request(b'{"action":"play","args":{"volume":1}}\n')
    with pytest.raises(ValueError):
        player_protocol.decode_request(b'{"action":"play","extra":true}\n')
    with pytest.raises(ValueError):
        player_protocol.decode_response(b'{"ok":true}\n')


@pytest.mark.parametrize("action", player_protocol.ACTION_IDS)
def test_server_dispatches_whitelisted_action_and_returns_result(action):
    seen = []

    def handle(action):
        seen.append(action)
        return SimpleNamespace(
            ok=True,
            code="accepted",
            message="Playback command accepted",
            source="mpd",
        )

    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        server = _server(path, handle)
        server.start()
        try:
            result = _exchange(path, player_protocol.encode_request(action))
        finally:
            server.close()

    assert seen == [action]
    assert result == {
        "ok": True,
        "code": "accepted",
        "message": "Playback command accepted",
        "source": "mpd",
    }


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b'{"action":"stop","args":{}}\n', "unknown_action"),
        (b'{"action":"play","args":{"value":1}}\n', "invalid_request"),
        (b"not json\n", "invalid_request"),
        (b'{"action":"play","args":{}}', "invalid_request"),
    ],
)
def test_server_rejects_unknown_malformed_and_unterminated_requests(payload, code):
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        server = _server(path, lambda _action: pytest.fail("must not dispatch"))
        server.start()
        try:
            if payload.endswith(b"\n"):
                result = _exchange(path, payload)
            else:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    client.connect(path)
                    client.sendall(payload)
                    client.shutdown(socket.SHUT_WR)
                    result = player_protocol.decode_response(client.recv(4096))
        finally:
            server.close()

    assert result["ok"] is False
    assert result["code"] == code
    assert set(result) == {"ok", "code", "message", "source"}


def test_server_bounds_message_size_and_read_time():
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        server = _server(path, lambda _action: pytest.fail("must not dispatch"), timeout=0.05)
        server.start()
        try:
            oversized = b"x" * (player_protocol.MAX_MESSAGE_BYTES + 1) + b"\n"
            too_large = _exchange(path, oversized)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(path)
                timed_out = player_protocol.decode_response(client.recv(4096))
        finally:
            server.close()

    assert too_large["code"] == "request_too_large"
    assert timed_out["code"] == "request_timeout"


def test_server_accepts_fragmented_request_and_exact_size_limit():
    seen = []
    response = SimpleNamespace(ok=True, code="accepted", message="Accepted", source="mpd")
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        server = _server(path, lambda action: seen.append(action) or response)
        server.start()
        try:
            request = player_protocol.encode_request("next")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(path)
                for byte in request:
                    client.sendall(bytes((byte,)))
                fragmented = player_protocol.decode_response(client.recv(4096))

            padding = b" " * (player_protocol.MAX_MESSAGE_BYTES - len(request))
            exact = request[:-1] + padding + b"\n"
            at_limit = _exchange(path, exact)
        finally:
            server.close()

    assert fragmented["code"] == "accepted"
    assert at_limit["code"] == "accepted"
    assert seen == ["next", "next"]


def test_server_times_out_partial_request_and_bounds_handler_errors():
    calls = 0

    def handle(_action):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("backend detail")
        return SimpleNamespace(
            ok=True,
            code="accepted",
            message="x" * player_protocol.MAX_MESSAGE_BYTES,
            source="mpd",
        )

    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        server = _server(path, handle, timeout=0.05)
        server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(path)
                client.sendall(b'{"action":')
                partial = player_protocol.decode_response(client.recv(4096))
            raised = _exchange(path, player_protocol.encode_request("play"))
            oversized = _exchange(path, player_protocol.encode_request("pause"))
        finally:
            server.close()

    assert partial["code"] == "request_timeout"
    assert raised["code"] == "internal_error"
    assert oversized["code"] == "internal_error"


def test_server_replaces_stale_socket_secures_it_and_cleans_up():
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        with open(path, "w", encoding="utf-8") as stale:
            stale.write("stale")

        server = _server(
            path,
            lambda _action: SimpleNamespace(
                ok=False,
                code="command_failed",
                message="Rejected",
                source="usb",
            ),
        )
        server.start()
        try:
            socket_stat = os.stat(path)
            assert stat.S_ISSOCK(socket_stat.st_mode)
            assert stat.S_IMODE(socket_stat.st_mode) == 0o660
            assert socket_stat.st_gid == os.getgid()
        finally:
            server.close()

        assert not os.path.exists(path)


def test_server_replaces_stale_unix_socket():
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(path)
        stale.close()

        server = _server(
            path,
            lambda _action: SimpleNamespace(
                ok=True, code="accepted", message="Accepted", source="mpd"
            ),
        )
        server.start()
        try:
            result = _exchange(path, player_protocol.encode_request("play"))
        finally:
            server.close()

    assert result["code"] == "accepted"


def test_default_socket_group_and_mode_are_applied(monkeypatch):
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        group = SimpleNamespace(gr_gid=1234)
        getgrnam = mock.Mock(return_value=group)
        chown = mock.Mock()
        chmod = mock.Mock(wraps=os.chmod)
        monkeypatch.setattr(playback_control_server.grp, "getgrnam", getgrnam)
        monkeypatch.setattr(playback_control_server.os, "chown", chown)
        monkeypatch.setattr(playback_control_server.os, "chmod", chmod)
        server = PlaybackControlServer(lambda _action: None, socket_path=path)

        server.start()
        try:
            getgrnam.assert_called_once_with("radio-web")
            chown.assert_called_once_with(path, -1, 1234)
            chmod.assert_called_once_with(path, 0o660)
        finally:
            server.close()


def _serve_raw_once(path, response, *, delay=0):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)

    def serve():
        try:
            connection, _address = listener.accept()
            with connection:
                connection.recv(4096)
                if delay:
                    time.sleep(delay)
                if response:
                    connection.sendall(response)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"not json\n",
        b'{"ok":true}\n',
        b"{}\n{}\n",
        b"x" * (player_protocol.MAX_MESSAGE_BYTES + 1),
    ],
    ids=["empty", "not-json", "invalid-schema", "multiple-frames", "oversized"],
)
def test_client_rejects_invalid_response_framing(monkeypatch, response):
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        thread = _serve_raw_once(path, response)
        monkeypatch.setattr(player_protocol, "SOCKET_PATH", path)

        with pytest.raises(route_player.PlayerUnavailable):
            route_player._exchange("play")
        thread.join(timeout=2)


def test_web_control_times_out_when_player_stops_responding(monkeypatch):
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        thread = _serve_raw_once(path, b"", delay=0.15)
        monkeypatch.setattr(player_protocol, "SOCKET_PATH", path)
        monkeypatch.setattr(player_protocol, "SOCKET_TIMEOUT_SECONDS", 0.03)

        response = routes.resolve(
            routes.Request(
                method="POST",
                path="/api/v1/player/play",
                media_type="application/json",
                body=b"{}",
            )
        )

        assert response[0] == 503
        assert response[1] == "application/json; charset=utf-8"
        assert '"code":"player_unavailable"' in response[2]
        thread.join(timeout=2)


def test_web_control_recovers_across_player_restarts(monkeypatch):
    with tempfile.TemporaryDirectory() as short_dir:
        path = os.path.join(short_dir, "control.sock")
        monkeypatch.setattr(player_protocol, "SOCKET_PATH", path)
        request = routes.Request(
            method="POST",
            path="/api/v1/player/next",
            media_type="application/json",
            body=b"{}",
        )

        assert routes.resolve(request)[0] == 503
        for _restart in range(2):
            server = _server(
                path,
                lambda _action: SimpleNamespace(
                    ok=True, code="accepted", message="Accepted", source="spotify"
                ),
            )
            server.start()
            try:
                assert routes.resolve(request)[0] == 200
            finally:
                server.close()
            assert routes.resolve(request)[0] == 503
