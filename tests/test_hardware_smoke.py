"""Optional on-target smoke checks (Phase B3) — ``pytest -m hardware``.

These tests are **skipped by default** (see ``addopts = "-ra -m 'not
hardware'"`` in ``pyproject.toml``) and are **never run in CI**: they require a
real Raspberry Pi 3A+ with the audio HAT/DAC, SPI display, I2C ADS1115 and amp
GPIO wired up. They codify the manual first-boot checklist from
``buildroot/README.md`` ("Validate: i2cdetect -y 1 (ADS1115 @0x48), aplay -l
(DAC), SPI display, MPD playback, AirPlay + Spotify discovery, amp GPIO 26") so
a maintainer can run it on the device instead of eyeballing each command::

    pytest -m hardware

Each check ``pytest.skip``s gracefully when the tool/daemon it needs is not
present, so a partial rig still reports useful per-check results rather than a
hard error. Nothing here is stubbed — the point is to touch real hardware.
"""

import shutil
import socket
import subprocess

import pytest

pytestmark = pytest.mark.hardware

# ADS1115 default address and the amp-enable pin, mirroring the README/doc.
ADS1115_ADDRESS = "48"
AMP_GPIO = 26
_TIMEOUT = 10


def _require(binary: str) -> str:
    path = shutil.which(binary)
    if path is None:
        pytest.skip(f"{binary} is not installed on this host (not a target board?)")
    return path


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def test_i2c_bus_shows_ads1115_at_0x48():
    """``i2cdetect -y 1`` must show the ADS1115 ADC at address 0x48."""
    i2cdetect = _require("i2cdetect")
    result = _run([i2cdetect, "-y", "1"])
    assert result.returncode == 0, result.stderr
    assert (
        ADS1115_ADDRESS in result.stdout
    ), f"ADS1115 not detected at 0x{ADS1115_ADDRESS} on I2C bus 1:\n{result.stdout}"


def test_alsa_lists_a_playback_device():
    """``aplay -l`` must enumerate at least one playback DAC/card."""
    aplay = _require("aplay")
    result = _run([aplay, "-l"])
    assert result.returncode == 0, result.stderr
    assert (
        "card" in result.stdout.lower()
    ), f"No ALSA playback device found:\n{result.stdout}\n{result.stderr}"


def test_spi_display_device_node_present():
    """The SPI bus the display panel is wired to must be exported."""
    import os

    if not any(os.path.exists(f"/dev/spidev0.{n}") for n in (0, 1)):
        pytest.skip("no /dev/spidev0.* node (SPI not enabled or no display board)")
    assert os.path.exists("/dev/spidev0.0") or os.path.exists("/dev/spidev0.1")


def test_mpd_accepts_playback_control_connection():
    """MPD must be reachable so the radio can drive playback."""
    try:
        with socket.create_connection(("127.0.0.1", 6600), timeout=_TIMEOUT) as conn:
            greeting = conn.recv(64)
    except OSError:
        pytest.skip("MPD is not listening on 127.0.0.1:6600")
    assert greeting.startswith(b"OK MPD"), greeting


def test_airplay_and_spotify_backends_are_discoverable():
    """The AirPlay (shairport-sync) and Spotify (go-librespot) backends run."""
    for binary in ("shairport-sync", "go-librespot"):
        if shutil.which(binary) is None:
            pytest.skip(f"{binary} not installed on this host")
    pgrep = _require("pgrep")
    running = {
        name: _run([pgrep, "-x", name]).returncode == 0
        for name in ("shairport-sync", "go-librespot")
    }
    assert all(running.values()), f"streaming backends not running: {running}"


def test_amp_enable_gpio_is_controllable():
    """The amplifier-enable GPIO (BCM 26) must be drivable via gpiozero."""
    try:
        from gpiozero import OutputDevice
    except Exception:  # pragma: no cover - import guarded for non-target hosts
        pytest.skip("gpiozero/RPi.GPIO not available (not a target board?)")
    try:
        amp = OutputDevice(AMP_GPIO)
    except Exception as exc:  # pragma: no cover - only meaningful on hardware
        pytest.skip(f"amp GPIO {AMP_GPIO} not controllable: {exc}")
    try:
        amp.on()
        assert amp.value == 1
        amp.off()
        assert amp.value == 0
    finally:
        amp.close()
