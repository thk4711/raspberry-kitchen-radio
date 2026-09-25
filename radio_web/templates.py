"""Public compatibility facade for domain-specific HTML templates."""

from .template_auth import login_page, setup_page
from .template_common import csrf_field
from .template_dashboard import (
    dashboard,
    forbidden,
    method_not_allowed,
    not_found,
)
from .template_device_maintenance import (
    confirm_page,
    device_page,
    firmware_switch_confirm_page,
    maintenance_page,
)
from .template_display_audio import (
    adc_debug_page,
    audio_hardware_applied_page,
    audio_hardware_page,
    settings_page,
)
from .template_network import network_page
from .template_stations_sources import sources_page, stations_page

__all__ = [
    "adc_debug_page",
    "audio_hardware_applied_page",
    "audio_hardware_page",
    "confirm_page",
    "csrf_field",
    "dashboard",
    "device_page",
    "forbidden",
    "firmware_switch_confirm_page",
    "login_page",
    "maintenance_page",
    "method_not_allowed",
    "network_page",
    "not_found",
    "settings_page",
    "setup_page",
    "sources_page",
    "stations_page",
]
