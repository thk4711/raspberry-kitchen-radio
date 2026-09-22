"""Root-owned privileged helper for the web administration interface.

The web UI (``radio-web``) runs as a dedicated **unprivileged** user and cannot
restart services, set the hostname or reboot the board on its own. Those
privileged operations live here, in a small **root-owned** service started by
``/etc/init.d/S79radio-helper`` *before* ``S80radio-web``.
The two processes talk over a local unix socket
(:mod:`radio_web.helper_protocol`).

Security model — every request is treated as hostile:

* Only the fixed action identifiers in
  :data:`radio_web.helper_protocol.ACTION_IDS` are accepted; anything else is
  rejected without running a thing.
* Each action maps to exactly one hard-coded operation invoked with
  ``shell=False`` (an argument list), mirroring
  :func:`lib.utilities.UtilityLibrary.restart_systemd_service`.
* The one action that carries an argument (``set_hostname`` / ``set_time``)
  **re-validates** it here with the same validators the web layer uses, so a
  compromised or buggy web process still cannot inject an arbitrary value.
* The socket is created ``0660`` and owned ``root:<web-group>`` (done by the
  init script), so only the web user's group can connect.

The helper never reads HTTP, never parses a shell string, and never logs a
secret.
"""

import logging
import os
import socketserver
import subprocess
from typing import Any, Callable, Dict, Optional, Tuple, Union

from . import (
    audio_hardware_apply,
    data_backup,
    device_store,
    equalizer_store,
    firmware_installer,
    firmware_slots,
    helper_protocol,
    network_apply,
    persistent_config,
    validators,
)
from .device_store import apply_hostname_files, apply_time_files

logger = logging.getLogger("radio_web.helper")

OperationResult = Union[Tuple[bool, str], Tuple[bool, str, Dict[str, Any]]]

# SysV init scripts whose restart re-reads the managed config. Same paths
# lib.utilities.restart_systemd_service() falls back to; invoked as argument
# lists, never through a shell.
_RADIO_INIT_SCRIPT = "/etc/init.d/S90radio"
_MPD_INIT_SCRIPT = "/etc/init.d/S50mpd"
_SSH_INIT_SCRIPT = "/etc/init.d/S50dropbear"
_BLUETOOTH_INIT_SCRIPT = "/etc/init.d/S42bluetooth"
_USB_AUDIO_INIT_SCRIPT = "/etc/init.d/S39usb-audio"

# Fixed argv for the two power operations (BusyBox). Argument lists, shell=False.
_REBOOT_CMD = ["/sbin/reboot"]
_SHUTDOWN_CMD = ["/sbin/poweroff"]

# Fixed argv for the batch password setter (BusyBox). Reads ``user:password``
# lines on stdin; used to set the root login password. Argument list, shell=False.
_CHPASSWD_CMD = ["/usr/sbin/chpasswd"]

# Subprocess timeout: a restart returns promptly; cap it so a wedged operation
# can never tie up the worker thread indefinitely.
_ACTION_TIMEOUT_SECONDS = 30.0


def _run_argv(argv: list, ok_message: str, fail_message: str) -> Tuple[bool, str]:
    """Run a fixed ``argv`` (``shell=False``); return ``(ok, message)``."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            argv,
            # Some init scripts launch long-lived background children which
            # inherit stdout/stderr. PIPEs would then remain open after the
            # script exits and subprocess.run would wait until its timeout.
            # Runtime logs are already owned by those services; discard the
            # fixed restart command's small status output here.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_ACTION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.error("%s: not found", argv[0])
        return False, fail_message
    except subprocess.TimeoutExpired:
        logger.error("%s: timed out", argv[0])
        return False, fail_message
    except OSError as exc:
        logger.error("%s: %s", argv[0], exc)
        return False, fail_message
    if result.returncode == 0:
        logger.info("%s: ok", argv[0])
        return True, ok_message
    logger.error("%s: exit %s", argv[0], result.returncode)
    return False, fail_message


def _restart_radio(_args: Dict[str, Any]) -> Tuple[bool, str]:
    return _run_argv(
        [_RADIO_INIT_SCRIPT, "restart"],
        "Radio restarted.",
        "Restarting the radio failed.",
    )


def _restart_mpd(_args: Dict[str, Any]) -> Tuple[bool, str]:
    return _run_argv(
        [_MPD_INIT_SCRIPT, "restart"],
        "MPD restarted.",
        "Restarting MPD failed.",
    )


def _apply_equalizer(args: Dict[str, Any]) -> Tuple[bool, str]:
    """Apply the EQ, restarting PCM consumers only for a structural change.

    ``audio_hardware_apply.apply_equalizer`` pushes the new parameters to the
    runtime file the LADSPA plugin re-reads live, so an ordinary tweak needs no
    restart (it returns a ``"live:"`` message). It only rewrites asound.conf when
    the EQ stage is inserted or removed (a ``"restart:"`` message); in that case
    every long-lived ALSA client must reopen the route.
    """
    if args:
        return False, "Equalizer apply does not accept arguments."
    ok, message = audio_hardware_apply.apply_equalizer()
    if not ok:
        return ok, message
    if not message.startswith("restart:"):
        return True, "Equalizer applied live to all audio sources."
    for script in (
        _MPD_INIT_SCRIPT,
        _RADIO_INIT_SCRIPT,
        _BLUETOOTH_INIT_SCRIPT,
        _USB_AUDIO_INIT_SCRIPT,
    ):
        ok, message = _run_argv(
            [script, "restart"],
            "Equalizer applied.",
            "The equalizer was saved, but restarting an audio service failed.",
        )
        if not ok:
            return ok, message
    return True, "Equalizer applied to all audio sources."


def _restart_ssh(_args: Dict[str, Any]) -> Tuple[bool, str]:
    return _run_argv(
        [_SSH_INIT_SCRIPT, "restart"],
        "SSH settings applied.",
        "Applying SSH settings failed.",
    )


def _set_root_password(args: Dict[str, Any]) -> Tuple[bool, str]:
    """Set the root (device/SSH) login password and persist only its hash.

    Re-validates the password server-side (never trusts the web process), then
    pipes ``root:<password>`` to BusyBox ``chpasswd`` with ``shell=False`` so the
    secret never touches a shell or an argument vector. The plaintext is never
    logged. On success the resulting hash is captured to the persistent identity
    file (so it survives A/B firmware updates) and the non-secret
    "credential provisioned" marker is created, matching what the SD-card
    ``provision-from-boot`` path does. This unlocks the "Enable SSH" control.
    """
    raw = args.get("password", "")
    if not isinstance(raw, str):
        return False, "Invalid root password."
    try:
        password = validators.validate_root_password(raw)
    except ValueError as exc:
        return False, str(exc)
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, shell=False
            _CHPASSWD_CMD,
            input=f"root:{password}\n",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_ACTION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        logger.error("set_root_password: chpasswd not found")
        return False, "Could not change the root password."
    except subprocess.TimeoutExpired:
        logger.error("set_root_password: chpasswd timed out")
        return False, "Could not change the root password."
    except OSError as exc:
        logger.error("set_root_password: chpasswd failed: %s", exc)
        return False, "Could not change the root password."
    if result.returncode != 0:
        logger.error("set_root_password: chpasswd exit %s", result.returncode)
        return False, "Could not change the root password."
    try:
        persistent_config.capture_root_password()
    except (OSError, ValueError) as exc:
        logger.error("set_root_password: hash persistence failed: %s", exc)
        return False, "The password was changed but could not be persisted for updates."
    try:
        _mark_root_credential_provisioned()
    except OSError as exc:
        logger.warning("set_root_password: could not write credential marker: %s", exc)
    logger.info("set_root_password: root password updated and hash persisted")
    return True, "Root password changed."


def _mark_root_credential_provisioned() -> None:
    """Create the non-secret marker used to gate the Enable SSH control."""
    marker = device_store.ROOT_CREDENTIAL_MARKER
    directory = os.path.dirname(os.path.abspath(marker))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(marker, "w", encoding="utf-8"):
        pass
    try:
        os.chmod(marker, 0o644)
    except OSError:
        pass


def _reboot(_args: Dict[str, Any]) -> Tuple[bool, str]:
    if firmware_installer.installation_active():
        return False, "Reboot is unavailable while firmware installation is active."
    return _run_argv(_REBOOT_CMD, "Rebooting now.", "Reboot failed.")


def _shutdown(_args: Dict[str, Any]) -> Tuple[bool, str]:
    if firmware_installer.installation_active():
        return False, "Shutdown is unavailable while firmware installation is active."
    return _run_argv(_SHUTDOWN_CMD, "Shutting down now.", "Shutdown failed.")


def _set_hostname(args: Dict[str, Any]) -> Tuple[bool, str]:
    """Persist the device name to ``/etc/hostname`` (+ ``/etc/hosts``).

    Re-validates the name server-side (never trusts the web process), then
    writes the files. The new name takes effect on the next reboot
    — the helper deliberately does not run a live
    ``hostname`` call.
    """
    raw = args.get("name", "")
    if not isinstance(raw, str):
        return False, "Invalid device name."
    try:
        name = validators.validate_device_name(raw)
    except ValueError as exc:
        return False, str(exc)
    try:
        apply_hostname_files(name)
    except OSError as exc:
        logger.error("set_hostname: %s", exc)
        return False, "Could not save the device name."
    logger.info("set_hostname: saved (applies on reboot)")
    return True, "Device name saved. It takes effect after a reboot."


def _set_time(args: Dict[str, Any]) -> Tuple[bool, str]:
    """Persist timezone and/or NTP server, mirroring ``provision-from-boot``."""
    timezone = args.get("timezone", "")
    ntp_server = args.get("ntp_server", "")
    if not isinstance(timezone, str) or not isinstance(ntp_server, str):
        return False, "Invalid time settings."
    try:
        clean_tz = validators.validate_timezone(timezone) if timezone else ""
        clean_ntp = validators.validate_ntp_server(ntp_server) if ntp_server else ""
    except ValueError as exc:
        return False, str(exc)
    try:
        apply_time_files(clean_tz, clean_ntp)
    except OSError as exc:
        logger.error("set_time: %s", exc)
        return False, "Could not save the time settings."
    logger.info("set_time: saved timezone=%r ntp=%r", clean_tz, clean_ntp)
    return True, "Time settings saved."


# --- Network operations ------------------------------------------------------
#
# The WiFi write goes through network_apply, which re-validates, saves the
# previous config, tries the new one and arms an auto-rollback watcher
# (auto-rollback watcher). The helper only forwards the (already-validated) args.


def _set_wifi(args: Dict[str, Any]) -> Tuple[bool, str]:
    config = {
        key: str(args.get(key, ""))
        for key in (
            "ssid",
            "psk",
            "country",
            "ip_address",
            "ip_prefix",
            "ip_gateway",
            "ip_dns",
        )
    }
    return network_apply.apply_wifi(config)


def _wifi_confirm(_args: Dict[str, Any]) -> Tuple[bool, str]:
    return network_apply.confirm()


def _wifi_rollback(_args: Dict[str, Any]) -> Tuple[bool, str]:
    return network_apply.rollback()


# --- Sound-card selection ----------------------------------------------------
#
# One fixed action carries a validated profile *id* (never overlay strings or
# file content). audio_hardware_apply re-validates the id server-side, then owns
# the concrete config.txt/asound.conf/mpd.conf/modules-load.d/audio.ini edits.
# The change is applied on the next reboot (a device-tree overlay is resolved by
# the firmware at boot); the helper does no live overlay reload.


def _set_audio_hardware(args: Dict[str, Any]) -> Tuple[bool, str]:
    if firmware_installer.installation_active():
        return False, "Sound-card changes are unavailable during firmware installation."
    raw = args.get("profile", "")
    if not isinstance(raw, str):
        return False, "Invalid sound card."
    return audio_hardware_apply.apply(raw)


def _no_firmware_args(args: Dict[str, Any]) -> Optional[Tuple[bool, str]]:
    if args:
        return False, "Firmware operations do not accept arguments."
    return None


def _inspect_firmware(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_firmware_args(args)
    return rejected if rejected is not None else firmware_installer.inspect_firmware()


def _install_firmware(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_firmware_args(args)
    return rejected if rejected is not None else firmware_installer.install_firmware()


def _cancel_staged_firmware(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_firmware_args(args)
    return rejected if rejected is not None else firmware_installer.cancel_staged_firmware()


def _firmware_status(args: Dict[str, Any]) -> OperationResult:
    rejected = _no_firmware_args(args)
    if rejected is not None:
        return rejected
    return True, "Firmware status available.", firmware_installer.firmware_status()


def _prepare_rollback(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_firmware_args(args)
    return rejected if rejected is not None else firmware_slots.prepare_rollback()


def _no_backup_args(args: Dict[str, Any]) -> Optional[Tuple[bool, str]]:
    if args:
        return False, "Backup operations do not accept arguments."
    return None


def _create_data_backup(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_backup_args(args)
    return rejected if rejected is not None else data_backup.create_backup()


def _inspect_data_restore(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_backup_args(args)
    if rejected is not None:
        return rejected
    ok, message, _metadata = data_backup.inspect_restore()
    return ok, message


def _apply_data_restore(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_backup_args(args)
    return rejected if rejected is not None else data_backup.apply_restore()


def _cancel_data_restore(args: Dict[str, Any]) -> Tuple[bool, str]:
    rejected = _no_backup_args(args)
    return rejected if rejected is not None else data_backup.cancel_restore()


# Fixed dispatch table: action id -> operation. Keys must match
# helper_protocol.ACTION_IDS exactly (asserted below and covered by a test).
_OPERATIONS: Dict[str, Callable[[Dict[str, Any]], OperationResult]] = {
    "restart_radio": _restart_radio,
    "restart_mpd": _restart_mpd,
    "restart_ssh": _restart_ssh,
    "set_root_password": _set_root_password,
    "reboot": _reboot,
    "shutdown": _shutdown,
    "set_hostname": _set_hostname,
    "set_time": _set_time,
    "set_wifi": _set_wifi,
    "wifi_confirm": _wifi_confirm,
    "wifi_rollback": _wifi_rollback,
    "set_audio_hardware": _set_audio_hardware,
    "apply_equalizer": _apply_equalizer,
    "inspect_firmware": _inspect_firmware,
    "install_firmware": _install_firmware,
    "cancel_staged_firmware": _cancel_staged_firmware,
    "firmware_status": _firmware_status,
    "prepare_rollback": _prepare_rollback,
    "create_data_backup": _create_data_backup,
    "inspect_data_restore": _inspect_data_restore,
    "apply_data_restore": _apply_data_restore,
    "cancel_data_restore": _cancel_data_restore,
}

assert set(_OPERATIONS) == set(
    helper_protocol.ACTION_IDS
), "helper operations must match the protocol whitelist exactly"


def dispatch(action: str, args: Dict[str, Any]) -> OperationResult:
    """Run the whitelisted ``action``; reject anything unknown.

    This is the single trust boundary: an unknown id (including attempts like
    ``"/bin/sh -c ..."``) is rejected here without running anything.
    """
    operation = _OPERATIONS.get(action)
    if operation is None:
        logger.warning("dispatch: rejected unknown action id")
        return False, "Unknown action."
    return operation(args)


class _Handler(socketserver.StreamRequestHandler):
    """Handle exactly one framed request per connection."""

    def handle(self) -> None:
        raw = self.rfile.readline(helper_protocol.MAX_MESSAGE_BYTES + 1)
        if len(raw) > helper_protocol.MAX_MESSAGE_BYTES:
            self.wfile.write(helper_protocol.encode_response(False, "Request too large."))
            return
        try:
            action, args = helper_protocol.decode_request(raw)
        except (ValueError, UnicodeDecodeError):
            self.wfile.write(helper_protocol.encode_response(False, "Malformed request."))
            return
        result = dispatch(action, args)
        if len(result) == 3:
            ok, message, data = result
            self.wfile.write(helper_protocol.encode_response(ok, message, data))
        else:
            ok, message = result
            self.wfile.write(helper_protocol.encode_response(ok, message))


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def _seed_equalizer_runtime() -> None:
    """Regenerate the live EQ runtime file from persistent settings at startup.

    The runtime file lives on tmpfs (``/run/radio``) and is wiped by every boot,
    so playback would otherwise open the LADSPA plugin before any web apply has
    written it. Seeding here (root, before S90radio) means the persisted
    ``equalizer.ini`` on the data partition is always reflected in live audio.
    Best effort: a failure here must never stop the privileged helper.
    """
    try:
        equalizer_store.write_runtime(equalizer_store.load_equalizer())
    except OSError as exc:
        logger.warning("seed equalizer runtime: %s", exc)


def serve(socket_path: str = "") -> None:
    """Bind the unix socket and serve privileged requests forever.

    Removes any stale socket file first, then creates the listener. The init
    script tightens ownership/permissions to ``root:<web-group> 0660`` after
    start so only the web user's group can connect.
    """
    path = socket_path or helper_protocol.SOCKET_PATH
    firmware_installer.recover_interrupted_install()
    _seed_equalizer_runtime()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    server = _Server(path, _Handler)
    logger.info("radio-helper listening on %s", path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def main() -> None:
    """Entry point for ``python3 -m radio_web.helper`` (run as root)."""
    level = os.environ.get("RADIO_LOG_LEVEL", "").strip()
    if not level:
        resolved = logging.INFO
    elif level.isdigit():
        resolved = int(level)
    else:
        resolved = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=resolved)
    serve()


if __name__ == "__main__":
    main()
