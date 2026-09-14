"""Per-field whitelists and validators for the web administration UI.

Every value submitted through an HTML form is validated here **before** it is
persisted (strict whitelist; reject unknown
keys"). Validators return a cleaned value on success and raise
:class:`ValueError` with a short, user-facing message on rejection — the route
handler turns that message into an inline form error. No validator ever runs a
shell, touches the network, or writes a file.

Phase 5 covers the station-editing fields: display name, stream URL and the
optional logo filename. Logo *uploads* are out of scope for v1 (§7.1); only the
filename of an already-shipped logo is accepted, and it is whitelisted tightly
so it can never escape the logo directory.
"""
import re
from urllib.parse import urlsplit

# Display name: any printable text, but bounded and free of control characters
# and INI structural characters that would break the ``[section]`` header the
# name becomes (see radio_web.stations_store.serialize_stations).
MAX_NAME_LENGTH = 64
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Stream URL: HTTP(S) only, must have a host. Anything else (file://, data:,
# javascript:, an empty host, ...) is rejected.
ALLOWED_URL_SCHEMES = ("http", "https")
MAX_URL_LENGTH = 2048

# Logo filename: a bare filename (no path separators, no traversal) with an
# image extension. Empty is allowed and means "use the generated fallback tile"
# (lib/display/logo_fallback.py).
_LOGO_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpg|jpeg)$", re.IGNORECASE)
MAX_LOGO_LENGTH = 128


def validate_station_name(value: str) -> str:
    """Return a cleaned station display name, or raise :class:`ValueError`.

    The name must be non-empty after trimming, within :data:`MAX_NAME_LENGTH`,
    contain no control characters, and no ``[`` / ``]`` (which would corrupt the
    INI section header it is written as).
    """
    name = (value or "").strip()
    if not name:
        raise ValueError("Name is required.")
    if len(name) > MAX_NAME_LENGTH:
        raise ValueError(f"Name must be at most {MAX_NAME_LENGTH} characters.")
    if _CONTROL_CHARS.search(name):
        raise ValueError("Name contains invalid control characters.")
    if "[" in name or "]" in name:
        raise ValueError("Name must not contain square brackets.")
    return name


def validate_stream_url(value: str) -> str:
    """Return a cleaned stream URL, or raise :class:`ValueError`.

    Only ``http`` and ``https`` URLs with a non-empty host are accepted.
    """
    url = (value or "").strip()
    if not url:
        raise ValueError("Stream URL is required.")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError("Stream URL is too long.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise ValueError("Stream URL must start with http:// or https://.")
    if not parts.netloc:
        raise ValueError("Stream URL must include a host.")
    return url


def validate_logo_filename(value: str) -> str:
    """Return a cleaned logo filename, or raise :class:`ValueError`.

    An empty value is valid and returned as ``""`` (fallback tile). A non-empty
    value must be a bare image filename: no directory separators, no ``..``, and
    an allowed image extension. This guarantees the value can never point
    outside the shipped logo directory.
    """
    logo = (value or "").strip()
    if not logo:
        return ""
    if len(logo) > MAX_LOGO_LENGTH:
        raise ValueError("Logo filename is too long.")
    if "/" in logo or "\\" in logo or ".." in logo:
        raise ValueError("Logo must be a plain filename, not a path.")
    if not _LOGO_RE.match(logo):
        raise ValueError("Logo must be a .png, .jpg or .jpeg filename.")
    return logo


# Source feature-flag keys accepted by the Sources page (Phase 6). Kept in sync
# with radio_web.sources_store.SOURCE_KEYS; duplicated here as a bare tuple so
# this module stays dependency-light (no import cycle with the store).
SOURCE_FLAG_KEYS = (
    "internet_radio",
    "airplay",
    "spotify",
    "bluetooth",
    "usb_audio",
)


def validate_source_flags(form: dict) -> dict:
    """Return a ``{key: bool}`` map from a submitted checkbox form.

    An HTML checkbox is absent from the POST body when unchecked and present
    (value ``"true"``) when checked, so every known key defaults to ``False``
    and is set ``True`` only when it appears. Any submitted key outside the
    whitelist raises :class:`ValueError` (strict whitelist). Non-flag form
    fields (``op``, ``csrf_token``, ``index``) are ignored by the caller before
    this runs.
    """
    unknown = set(form) - set(SOURCE_FLAG_KEYS)
    if unknown:
        raise ValueError(f"Unknown source '{sorted(unknown)[0]}'.")
    return {key: key in form for key in SOURCE_FLAG_KEYS}


# --- Device settings (Phase 7) ----------------------------------------------

# Device name = the system hostname. Match the provisioning rule enforced by
# usr/sbin/provision-from-boot apply_hostname (letters/digits/hyphens only) plus
# the DNS-label constraints: no leading/trailing hyphen, 1..63 characters.
MAX_HOSTNAME_LENGTH = 63
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")

# Timezone name: a zoneinfo path like "Europe/Berlin". Restrict to safe
# characters and forbid traversal so it can never escape the zoneinfo dir; the
# helper additionally checks the file exists under /usr/share/zoneinfo.
_TIMEZONE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+/-]*$")
MAX_TIMEZONE_LENGTH = 64

# NTP server: a hostname or IP. Bounded, and free of shell metacharacters /
# whitespace (it is written verbatim into chrony.conf).
_NTP_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")
MAX_NTP_LENGTH = 253


def validate_device_name(value: str) -> str:
    """Return a cleaned device name (hostname), or raise :class:`ValueError`.

    The rule matches the provisioning path: letters,
    digits and hyphens only; no spaces; no leading/trailing hyphen; 1..63
    characters.
    """
    name = (value or "").strip()
    if not name:
        raise ValueError("Device name is required.")
    if len(name) > MAX_HOSTNAME_LENGTH:
        raise ValueError(
            f"Device name must be at most {MAX_HOSTNAME_LENGTH} characters."
        )
    if not _HOSTNAME_RE.match(name):
        raise ValueError(
            "Device name may use letters, digits and hyphens only, and must "
            "not start or end with a hyphen."
        )
    return name


def validate_timezone(value: str) -> str:
    """Return a cleaned timezone name, or raise :class:`ValueError`.

    Only characters valid in a zoneinfo name are accepted and traversal
    (``..``) is rejected, so the value can never escape the zoneinfo directory.
    Existence on the image is checked separately by the helper.
    """
    tz = (value or "").strip()
    if not tz:
        raise ValueError("Timezone is required.")
    if len(tz) > MAX_TIMEZONE_LENGTH:
        raise ValueError("Timezone is too long.")
    if ".." in tz or tz.startswith("/"):
        raise ValueError("Invalid timezone.")
    if not _TIMEZONE_RE.match(tz):
        raise ValueError("Invalid timezone (e.g. Europe/Berlin).")
    return tz


def validate_ntp_server(value: str) -> str:
    """Return a cleaned NTP server hostname/IP, or raise :class:`ValueError`.

    A bounded hostname or IP with no whitespace or shell metacharacters (it is
    written verbatim into ``chrony.conf``).
    """
    server = (value or "").strip()
    if not server:
        raise ValueError("NTP server is required.")
    if len(server) > MAX_NTP_LENGTH:
        raise ValueError("NTP server is too long.")
    if not _NTP_RE.match(server):
        raise ValueError("NTP server must be a plain hostname or IP address.")
    return server


# --- Display & audio settings ------------------------------------------------

# The user-facing display fields we expose. Everything
# maps into the ``[ui]`` section of the managed ``display.ini`` and is re-parsed
# by ``lib/display/theme.build_theme`` (which itself clamps/defaults
# again — this layer only rejects hostile input and gives a friendly message).
_MAX_MS = 5000
_MAX_IDLE_TIMEOUT = 86400
_MAX_OVERLAY_DURATION = 30.0

# The four named theme presets surfaced on the settings page. ``default`` means
# "no preset overrides" — the individual fields win.
THEME_PRESETS = ("default", "high_contrast", "dim_night", "no_animations")


def _validate_bounded_int(value: str, label: str, low: int, high: int) -> int:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if not low <= number <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    return number


def validate_bool_flag(value: object) -> bool:
    """Coerce an HTML checkbox / string to bool.

    A checkbox present in the form (any truthy string) is True; absence is
    handled by the caller. Accepts the usual on/off spellings; anything else
    raises :class:`ValueError`.
    """
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("", "false", "0", "no", "off"):
        return False
    raise ValueError("Expected a yes/no value.")


def validate_idle_timeout(value: str) -> int:
    """Return the screensaver idle timeout in whole seconds (0..86400)."""
    return _validate_bounded_int(value, "Idle timeout", 0, _MAX_IDLE_TIMEOUT)


def validate_crossfade_ms(value: str) -> int:
    """Return the crossfade duration in milliseconds (0..5000)."""
    return _validate_bounded_int(value, "Crossfade", 0, _MAX_MS)


def validate_overlay_duration(value: str, label: str) -> float:
    """Return an OSD/toast overlay duration in seconds (0..30)."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    try:
        seconds = float(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not 0.0 <= seconds <= _MAX_OVERLAY_DURATION:
        raise ValueError(
            f"{label} must be between 0 and {_MAX_OVERLAY_DURATION:g} seconds."
        )
    return seconds


def validate_theme_preset(value: str) -> str:
    """Return a whitelisted theme preset id, or raise :class:`ValueError`."""
    preset = (value or "").strip().lower() or "default"
    if preset not in THEME_PRESETS:
        raise ValueError("Unknown theme preset.")
    return preset


# ALSA mixer name (advanced): the control name pyalsaaudio drives, e.g.
# "Digital" or "Master". Bounded, printable — written into the managed
# ``[audio]`` section and passed to ALSAController(mixer_name=).
MAX_MIXER_LENGTH = 64
_MIXER_RE = re.compile(r"^[A-Za-z0-9 ._-]+$")


def validate_mixer_name(value: str) -> str:
    """Return a cleaned ALSA mixer control name, or raise :class:`ValueError`."""
    name = (value or "").strip()
    if not name:
        raise ValueError("Mixer name is required.")
    if len(name) > MAX_MIXER_LENGTH:
        raise ValueError(
            f"Mixer name must be at most {MAX_MIXER_LENGTH} characters."
        )
    if not _MIXER_RE.match(name):
        raise ValueError("Mixer name may use letters, digits, spaces, . _ - only.")
    return name


def validate_volume_percent(value: str, label: str = "Volume") -> int:
    """Return an integer volume percentage in 0..100."""
    return _validate_bounded_int(value, label, 0, 100)


# Amplifier-enable GPIO (per sound card): the BCM pin ``radio.py`` toggles high
# when the radio is "on", written into the managed ``[gpio] amp`` value. A card
# with no power amp (e.g. the built-in headphone jack) has *no* pin: the fixed
# sentinel ``NO_AMP_GPIO`` ("none") is accepted and means "do not drive any amp
# GPIO". Any numeric value is range-checked to a safe BCM pin so an arbitrary
# string can never reach ``radio.conf`` and ``GPIO.setup``.
NO_AMP_GPIO = "none"
# BCM pins on the 40-pin header run 0..27 (GPIO0..GPIO27); the amp enable is 26
# by default. This is an inclusive range check, not a conflict check.
MIN_AMP_GPIO = 0
MAX_AMP_GPIO = 27


def validate_amp_gpio(value: str) -> str:
    """Return a cleaned amp-GPIO value: a BCM pin string, or ``"none"``.

    Accepts the fixed sentinel ``none`` (also an empty value or ``false``) for a
    board without a power amp, or a BCM pin integer in
    ``MIN_AMP_GPIO..MAX_AMP_GPIO``. Raises :class:`ValueError` on anything else.
    Returns a string so it round-trips through the ``audio.ini`` writer; the
    player re-parses it (a bare digit is coerced to ``int``).
    """
    cleaned = (value or "").strip().lower()
    if cleaned in ("", NO_AMP_GPIO, "false"):
        return NO_AMP_GPIO
    pin = _validate_bounded_int(cleaned, "Amplifier GPIO", MIN_AMP_GPIO, MAX_AMP_GPIO)
    return str(pin)


# Sound-card profile id (Step 2): the id the /audio-hardware picker submits.
# Validated here as a *dynamic* membership check against the single profile
# catalog (radio_web.audio_hardware_store.PROFILES) so adding a future sound
# card touches only that catalog — never this validator. Lazily imports the
# store so this dependency-light module keeps no module-level import cycle
# (mirrors the lazy-import idiom in diagnostics.py / system_status.py). The
# privileged helper re-validates the same id server-side before touching files.
def validate_audio_profile(value: str) -> str:
    """Return a whitelisted sound-card profile id, or raise :class:`ValueError`.

    Strict, dynamic membership check against
    :data:`radio_web.audio_hardware_store.PROFILES` (strict whitelist:
    reject unknown keys). Returns the cleaned id on success.
    """
    from . import audio_hardware_store

    profile_id = (value or "").strip()
    if profile_id not in audio_hardware_store.PROFILES:
        raise ValueError(f"Unknown sound card '{value}'.")
    return profile_id


# --- Bluetooth ---------------------------------------------------------------

# A colon-separated 48-bit MAC address (AA:BB:CC:DD:EE:FF). This is the only
# shape ever forwarded to a bluetoothctl subcommand; the
# privileged helper re-validates it before running anything.
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

def validate_mac_address(value: str) -> str:
    """Return an upper-cased MAC address ``AA:BB:CC:DD:EE:FF``, or raise."""
    mac = (value or "").strip()
    if not _MAC_RE.match(mac):
        raise ValueError("Invalid Bluetooth address.")
    return mac.upper()


# --- Display panel -----------------------------------------------------------

# Whitelisted panel driver names (must match panel_factory._PANELS keys).
PANEL_NAMES = ("st7789", "gc9a01")

# Geometry implied by each panel name: (width, height). Written alongside the
# panel name in display.ini so the player never needs to derive it.
PANEL_GEOMETRY = {
    "st7789": (240, 280),
    "gc9a01": (240, 240),
}


def validate_panel(value: str) -> str:
    """Return a normalised panel driver name, or raise :class:`ValueError`.

    Accepted values (case-insensitive): ``"st7789"`` and ``"gc9a01"``.
    An empty or absent value is treated as ``"st7789"`` (the shipped default)
    so the form works correctly when the managed file pre-dates this field.
    """
    name = (value or "").strip().lower()
    if not name:
        return "st7789"
    if name not in PANEL_NAMES:
        raise ValueError(
            f"Unknown display panel '{value}'. "
            f"Supported values: {', '.join(PANEL_NAMES)}."
        )
    return name


# --- Network (WiFi + static IP) ----------------------------------------------

# WiFi SSID: 1..32 bytes (the 802.11 limit), no control characters. Stored
# verbatim (double-quoted, escaped) in wpa_supplicant.conf by the helper.
MAX_SSID_LENGTH = 32
# WPA-PSK passphrase: 8..63 printable characters (the WPA2-personal range
# enforced everywhere else in the image — see provision-from-boot).
WPA_PSK_MIN = 8
WPA_PSK_MAX = 63

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def validate_ssid(value: str) -> str:
    """Return a cleaned WiFi SSID, or raise :class:`ValueError`."""
    ssid = (value or "").strip()
    if not ssid:
        raise ValueError("WiFi network name (SSID) is required.")
    if len(ssid.encode("utf-8")) > MAX_SSID_LENGTH:
        raise ValueError(f"SSID must be at most {MAX_SSID_LENGTH} bytes.")
    if _CONTROL_CHARS.search(ssid):
        raise ValueError("SSID contains invalid control characters.")
    return ssid


def validate_wifi_passphrase(value: str) -> str:
    """Return a cleaned WPA-PSK passphrase (8..63 chars), or raise."""
    psk = value or ""
    if not (WPA_PSK_MIN <= len(psk) <= WPA_PSK_MAX):
        raise ValueError(
            f"WiFi password must be {WPA_PSK_MIN}..{WPA_PSK_MAX} characters."
        )
    if _CONTROL_CHARS.search(psk):
        raise ValueError("WiFi password contains invalid control characters.")
    return psk


def validate_country_code(value: str) -> str:
    """Return an upper-cased ISO-3166 alpha-2 country code, or raise.

    An empty value is valid and returned as ``""`` (keep the worldwide default).
    """
    code = (value or "").strip()
    if not code:
        return ""
    if not _COUNTRY_RE.match(code):
        raise ValueError("Country must be a two-letter code (e.g. DE).")
    return code.upper()


def validate_ipv4(value: str, label: str) -> str:
    """Return a cleaned dotted-quad IPv4 address, or raise :class:`ValueError`."""
    text = (value or "").strip()
    match = _IPV4_RE.match(text)
    if not match:
        raise ValueError(f"{label} must be an IPv4 address (e.g. 192.168.1.10).")
    for octet in match.groups():
        if not 0 <= int(octet) <= 255:
            raise ValueError(f"{label} has an octet out of range (0..255).")
    return text


def validate_ipv4_prefix(value: str) -> int:
    """Return an IPv4 CIDR prefix length in 1..32."""
    return _validate_bounded_int(value, "Prefix length", 1, 32)


def validate_dns_list(value: str) -> str:
    """Return a normalised, space-separated list of up to three DNS servers.

    Empty is valid (returned as ``""``) meaning "use the gateway / DHCP DNS".
    Accepts commas or whitespace as separators.
    """
    text = (value or "").strip()
    if not text:
        return ""
    parts = [p for p in re.split(r"[\s,]+", text) if p]
    if len(parts) > 3:
        raise ValueError("At most three DNS servers are allowed.")
    cleaned = [validate_ipv4(part, "DNS server") for part in parts]
    return " ".join(cleaned)
