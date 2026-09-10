"""Tests for managed station-logo uploads and lookup."""

from io import BytesIO

import pytest
from PIL import Image

from radio_web import config_store, logo_store


def _image_bytes(size=(800, 200), fmt="PNG"):
    output = BytesIO()
    Image.new("RGBA", size, (10, 20, 30, 128)).save(output, format=fmt)
    return output.getvalue()


@pytest.fixture()
def managed(monkeypatch, tmp_path):
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(logo_store, "BUILTIN_LOGO_DIR", str(tmp_path / "builtins"))
    return tmp_path


def test_upload_converts_and_downscales_png(managed):
    filename = logo_store.save_upload(_image_bytes())
    path = managed / "logos" / filename
    assert filename.startswith("upload-") and filename.endswith(".png")
    assert (path.stat().st_mode & 0o777) == 0o644
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.size == (512, 128)


@pytest.mark.parametrize("payload", [b"not an image", b"GIF89a"])
def test_upload_rejects_invalid_image(managed, payload):
    with pytest.raises(ValueError):
        logo_store.save_upload(payload)


def test_upload_rejects_excessive_dimensions(managed):
    with pytest.raises(ValueError, match="dimensions"):
        logo_store.save_upload(_image_bytes((2001, 2000)))


def test_available_logos_and_managed_precedence(managed):
    builtin = managed / "builtins"
    builtin.mkdir()
    (builtin / "builtin.png").write_bytes(_image_bytes((1, 1)))
    uploaded = logo_store.save_upload(_image_bytes((1, 1)))
    assert logo_store.available_logos() == sorted(["builtin.png", uploaded], key=str.casefold)
    assert logo_store.resolve_logo_path(uploaded) == str(managed / "logos" / uploaded)
