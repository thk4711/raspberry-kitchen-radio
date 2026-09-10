"""Tests for secure now-playing artwork resolution."""

import base64

from radio_web import artwork

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB")
JPEG = b"\xff\xd8\xff\xe0image"


def test_runtime_artwork(monkeypatch, tmp_path):
    image = tmp_path / "spotify.jpg"
    image.write_bytes(JPEG)
    monkeypatch.setitem(artwork.RUNTIME_ARTWORK, "spotify", str(image))
    assert artwork.load("spotify") == ("image/jpeg", JPEG)


def test_station_logo_must_be_configured(monkeypatch, tmp_path):
    (tmp_path / "logo.png").write_bytes(PNG)
    monkeypatch.setattr(artwork, "LOGO_DIR", str(tmp_path))
    monkeypatch.setattr(artwork.stations_store, "load_stations", lambda: [{"logo": "other.png"}])
    assert artwork.load("station:logo.png") is None


def test_rejects_unknown_signature(monkeypatch, tmp_path):
    image = tmp_path / "spotify.jpg"
    image.write_bytes(b"not an image")
    monkeypatch.setitem(artwork.RUNTIME_ARTWORK, "spotify", str(image))
    assert artwork.load("spotify") is None


def test_rejects_oversized_file(monkeypatch, tmp_path):
    image = tmp_path / "spotify.jpg"
    image.write_bytes(JPEG)
    monkeypatch.setitem(artwork.RUNTIME_ARTWORK, "spotify", str(image))
    monkeypatch.setattr(artwork, "MAX_ARTWORK_BYTES", 4)
    assert artwork.load("spotify") is None


def test_rejects_unknown_identifier():
    assert artwork.load("../../etc/passwd") is None
