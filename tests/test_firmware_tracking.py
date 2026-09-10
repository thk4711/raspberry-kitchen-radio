import json

from radio_web import firmware_tracking as tracking


def test_tracker_persists_only_token_hash_and_returns_public_status(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "TRACKING_PATH", tmp_path / "current.json")
    token = tracking.create()
    tracking.update("installing", percent=42, message="Writing firmware", secret="hidden")

    stored = json.loads(tracking.TRACKING_PATH.read_text(encoding="utf-8"))
    assert token not in tracking.TRACKING_PATH.read_text(encoding="utf-8")
    assert stored["token_sha256"]
    assert tracking.status("wrong") is None
    assert tracking.status(token) == {
        "state": "installing",
        "message": "Writing firmware",
        "percent": 42,
        "updated_at": stored["updated_at"],
    }


def test_terminal_tracker_cannot_be_overwritten(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "TRACKING_PATH", tmp_path / "current.json")
    token = tracking.create()
    tracking.update("accepted", target_slot="B", message="accepted")
    tracking.update("checking_health", target_slot="B", message="stale")
    assert tracking.status(token)["state"] == "accepted"


def test_tracker_ignores_an_update_for_a_different_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "TRACKING_PATH", tmp_path / "current.json")
    token = tracking.create()
    tracking.update("installing", target_slot="B")
    tracking.update("accepted", target_slot="A")
    assert tracking.status(token)["state"] == "installing"


def test_tracker_file_is_group_readable_when_written_as_root(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "TRACKING_PATH", tmp_path / "current.json")
    ownership = []
    monkeypatch.setattr(tracking.os, "geteuid", lambda: 0)
    monkeypatch.setattr(tracking.os, "chown", lambda path, uid, gid: ownership.append((uid, gid)))
    tracking.create()
    assert ownership == [(0, tracking.WEB_GID)]
    assert tracking.TRACKING_PATH.stat().st_mode & 0o777 == 0o640
