"""Tests for the HTTP helpers and init-agnostic service restart in utilities.

Exercise ``make_request`` / ``request_json`` / ``request_image`` (JSON, bytes,
error and bound handling) and ``restart_systemd_service`` (D-Bus success, then
the SysV init and ``service`` fallbacks) without any real network or init
system: ``requests`` and ``subprocess`` are stubbed and ``dbus`` is the
conftest stub.
"""

import subprocess
from unittest import mock

import dbus
import pytest
import requests
from utilities import UtilityLibrary


class _Response:
    def __init__(self, *, json_data=None, content=b"", headers=None, raise_exc=None):
        self._json = json_data
        self.content = content
        self.headers = headers or {}
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise

    def json(self):
        return self._json


def test_make_request_returns_json(monkeypatch):
    resp = _Response(json_data={"a": 1}, headers={"Content-Type": "application/json"})
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    assert UtilityLibrary.make_request("http://x") == {"a": 1}


def test_make_request_returns_bytes_for_non_json(monkeypatch):
    resp = _Response(content=b"raw", headers={"Content-Type": "text/plain"})
    monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
    assert UtilityLibrary.make_request("http://x", method="POST") == b"raw"


def test_make_request_rejects_unknown_method(monkeypatch):
    # An unsupported verb raises ValueError internally and returns None.
    assert UtilityLibrary.make_request("http://x", method="DELETE") is None


def test_make_request_handles_request_exception(monkeypatch):
    def boom(*_a, **_k):
        raise requests.RequestException("down")

    monkeypatch.setattr(requests, "get", boom)
    assert UtilityLibrary.make_request("http://x") is None


def test_request_json_filters_non_dict(monkeypatch):
    monkeypatch.setattr(UtilityLibrary, "make_request", staticmethod(lambda *a, **k: b"bytes"))
    assert UtilityLibrary.request_json("http://x") is None
    monkeypatch.setattr(UtilityLibrary, "make_request", staticmethod(lambda *a, **k: {"ok": True}))
    assert UtilityLibrary.request_json("http://x") == {"ok": True}


def test_request_image_returns_bounded_bytes(monkeypatch):
    resp = _Response(content=b"\x89PNG", headers={"Content-Type": "image/png"})
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    assert UtilityLibrary.request_image("http://x") == b"\x89PNG"


def test_request_image_rejects_non_image(monkeypatch):
    resp = _Response(content=b"nope", headers={"Content-Type": "text/plain"})
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    assert UtilityLibrary.request_image("http://x") is None


def test_request_image_rejects_oversized(monkeypatch):
    resp = _Response(content=b"x" * 100, headers={"Content-Type": "image/png"})
    monkeypatch.setattr(requests, "get", lambda *a, **k: resp)
    assert UtilityLibrary.request_image("http://x", max_bytes=10) is None


def test_request_image_handles_error(monkeypatch):
    def boom(*_a, **_k):
        raise requests.RequestException("no")

    monkeypatch.setattr(requests, "get", boom)
    assert UtilityLibrary.request_image("http://x") is None


def test_restart_service_via_systemd_dbus(monkeypatch):
    restarts = []
    interface = mock.Mock()
    interface.RestartUnit = lambda unit, mode: restarts.append((unit, mode))
    bus = mock.Mock()
    bus.get_object.return_value = object()
    monkeypatch.setattr(dbus, "SystemBus", lambda: bus)
    monkeypatch.setattr(dbus, "Interface", lambda *a, **k: interface)
    UtilityLibrary.restart_systemd_service("mpd")
    assert restarts == [("mpd.service", "replace")]


def _dbus_unavailable(monkeypatch):
    def boom():
        raise dbus.DBusException("no systemd")

    monkeypatch.setattr(dbus, "SystemBus", boom)


def test_restart_service_falls_back_to_sysv(monkeypatch):
    _dbus_unavailable(monkeypatch)
    monkeypatch.setattr("utilities.os.path.exists", lambda p: p == "/etc/init.d/S50mpd")
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    UtilityLibrary.restart_systemd_service("mpd.service")
    assert calls == [["/etc/init.d/S50mpd", "restart"]]


def test_restart_service_falls_back_to_service_command(monkeypatch):
    _dbus_unavailable(monkeypatch)
    # No init script on disk -> skip it, fall through to `service <name> restart`.
    monkeypatch.setattr("utilities.os.path.exists", lambda _p: False)
    calls = []

    def fake_run(cmd, **_k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    UtilityLibrary.restart_systemd_service("dropbear")
    assert calls == [["service", "dropbear", "restart"]]


def test_restart_service_all_backends_fail(monkeypatch):
    _dbus_unavailable(monkeypatch)
    monkeypatch.setattr("utilities.os.path.exists", lambda _p: False)

    def boom(*_a, **_k):
        raise FileNotFoundError("no service binary")

    monkeypatch.setattr(subprocess, "run", boom)
    # Must not raise even when nothing works (logged error, no-op).
    UtilityLibrary.restart_systemd_service("mpd")
