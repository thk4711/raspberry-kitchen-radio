"""Read the privacy-safe first-boot provisioning status marker."""

import os
from typing import List

STATUS_FILE = os.environ.get("RADIO_PROVISIONING_STATUS_FILE", "/data/radio/provisioning-status")

_LABELS = {
    "wifi_ssid": "WiFi network name",
    "wifi_psk": "WiFi password",
    "hostname": "device name",
    "root_password": "root password",
    "enable_ssh": "SSH setting",
}


def incomplete_settings() -> List[str]:
    """Return known incomplete setting labels without exposing submitted values."""
    try:
        with open(STATUS_FILE, encoding="utf-8") as handle:
            keys = handle.read(1024).split()
    except OSError:
        return []
    return [_LABELS[key] for key in keys if key in _LABELS]
