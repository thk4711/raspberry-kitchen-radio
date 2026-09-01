import atexit
import logging
import os
import re
import shlex
import subprocess
import threading
from typing import Any, Dict, List, Optional, Union

import dbus
import requests

logger = logging.getLogger(__name__)


class UtilityLibrary:
    """A utility library for managing external processes, making HTTP requests,
    reading configuration files, and interacting with systemd services.

    Implemented as a singleton so that every module sharing the ``utility =
    UtilityLibrary()`` idiom gets the *same* instance. This ensures a single
    shared ``process_array`` and a single ``atexit`` cleanup handler instead of
    one per importing module.
    """

    _instance: Optional["UtilityLibrary"] = None
    _lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "UtilityLibrary":
        """Return the shared singleton instance, creating it once."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the UtilityLibrary with an empty process registry (once)."""
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        # One current process per named backend (keyed by the resolved binary
        # basename) instead of an append-only list, so repeated restarts cannot
        # grow memory without bound. ``_processes_lock`` guards both structures.
        self._processes_lock = threading.Lock()
        self._processes: Dict[str, subprocess.Popen] = {}
        self._supervisors: List[threading.Thread] = []
        # A single shared stop event lets ``cleanup`` halt every restart loop so
        # it can never relaunch a child after shutdown has started.
        self._stop = threading.Event()
        atexit.register(self.cleanup)

    @property
    def process_array(self) -> List[subprocess.Popen]:
        """Snapshot of the currently tracked backend processes.

        Retained for backward compatibility with callers/tests that inspected
        the previous append-only list; it now reflects the one-per-backend
        registry.
        """
        with self._processes_lock:
            return list(self._processes.values())



    def start_external_program_in_background(self, cmd: str) -> None:
        """Starts an external program in the background and monitors it.

        Args:
            cmd (str): The command to start the external program.
        """
        cmd_array = shlex.split(cmd)
        if not cmd_array:
            logger.error("Refusing to start empty command: %r", cmd)
            return
        name = os.path.basename(cmd_array[0])
        monitor_thread = threading.Thread(
            target=self._start_and_monitor_binary, args=(cmd_array, name),
            daemon=True, name=f'supervisor-{name}'
        )
        with self._processes_lock:
            self._supervisors.append(monitor_thread)
        monitor_thread.start()

    def _register_process(self, name: str, process: subprocess.Popen) -> None:
        """Record ``process`` as the current instance for ``name``."""
        with self._processes_lock:
            self._processes[name] = process

    def _restart_backoff(self, delay: float) -> float:
        """Wait ``delay`` seconds (interruptible) and return the next delay."""
        self._stop.wait(delay)
        return min(delay * 2, 30.0)

    def _start_and_monitor_binary(self, cmd_array: List[str], name: str,
                                  check_interval: int = 5) -> None:
        """Starts and monitors a binary command in a loop.

        Appliance logging policy: by default the child's stdout/stderr are sent
        to ``/dev/null`` so no backend logs are produced (this is a fast-booting
        appliance with a small SD card and a RAM-backed ``/tmp``). File logging
        is opt-in for developers via the ``RADIO_PROCESS_LOG_DIR`` env var; when
        enabled, each per-process log is *truncated on (re)start* and *size
        capped* (``RADIO_PROCESS_LOG_MAX_BYTES``, default 256 KiB) so a chatty
        or crash-looping backend can never exhaust the tmpfs / RAM.

        Restarts use a capped exponential backoff and the loop exits as soon as
        the shared stop event is set, so ``cleanup`` cannot race a relaunch.

        Args:
            cmd_array (List[str]): The command and arguments to execute.
            name (str): Registry key for this backend (binary basename).
            check_interval (int): Seconds between process-status checks.
        """
        cmd = shlex.join(cmd_array)
        log_dir = os.environ.get('RADIO_PROCESS_LOG_DIR')
        try:
            max_bytes = int(os.environ.get('RADIO_PROCESS_LOG_MAX_BYTES', 256 * 1024))
        except (TypeError, ValueError):
            max_bytes = 256 * 1024

        # Default (no RADIO_PROCESS_LOG_DIR, or explicitly "none"): discard all
        # backend output to /dev/null. Nothing is written to disk or tmpfs.
        if not log_dir or log_dir.lower() == 'none':
            self._monitor_with_devnull(cmd, cmd_array, name, check_interval)
            return

        process_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        log_path = os.path.join(log_dir, f'{process_name}.log')

        while not self._stop.is_set():
            delay = 2.0
            try:
                os.makedirs(log_dir, exist_ok=True)
                # Truncate on every (re)start ('w') so the file can only ever
                # grow within a single run, never across restarts.
                with open(log_path, 'w', buffering=1) as log_file:
                    log_file.write(f"--- starting: {cmd}\n")

                    # Start the binary. Keep stdout/stderr in a size-capped log so
                    # embedded target failures are diagnosable after a daemon
                    # exits, instead of only reporting an exit code.
                    new_process = subprocess.Popen(
                        cmd_array,
                        stdout=log_file,
                        stderr=subprocess.STDOUT
                    )
                    self._register_process(name, new_process)
                    logger.info(
                        f"Started process with PID: {new_process.pid}: {cmd}; "
                        f"logging to {log_path} (cap {max_bytes} bytes)"
                    )

                    # Monitor the process
                    while not self._stop.is_set():
                        retcode = new_process.poll()
                        if retcode is not None:  # Process has terminated
                            logger.error(
                                f"Process with PID: {new_process.pid} "
                                f"terminated with exit code {retcode}: {cmd}; "
                                f"see {log_path}"
                            )
                            break
                        # Enforce the size cap: if the log grew past the limit,
                        # truncate it in place so it can never fill the tmpfs.
                        if max_bytes > 0:
                            try:
                                if log_file.tell() > max_bytes:
                                    log_file.seek(0)
                                    log_file.truncate()
                                    log_file.write(
                                        f"--- log truncated at {max_bytes} bytes: {cmd}\n"
                                    )
                            except OSError:
                                pass
                        self._stop.wait(check_interval)
            except OSError as e:
                logger.error(f"Unable to start process '{cmd}': {e}")
            if self._stop.is_set():
                break
            delay = self._restart_backoff(delay)


    def _monitor_with_devnull(self, cmd: str, cmd_array: List[str], name: str,
                              check_interval: int) -> None:
        """Run and monitor ``cmd`` with stdout/stderr discarded to /dev/null.

        This is the appliance default: no backend log files are created, so
        neither the SD card nor the RAM-backed tmpfs can be filled by backend
        output. Restarts back off exponentially and the loop stops promptly when
        the shared stop event is set.
        """
        while not self._stop.is_set():
            delay = 2.0
            try:
                new_process = subprocess.Popen(
                    cmd_array,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                self._register_process(name, new_process)
                logger.info(
                    f"Started process with PID: {new_process.pid}: {cmd}; "
                    "output discarded (set RADIO_PROCESS_LOG_DIR to log)"
                )

                # Monitor the process
                while not self._stop.is_set():
                    retcode = new_process.poll()
                    if retcode is not None:  # Process has terminated
                        logger.error(
                            f"Process with PID: {new_process.pid} "
                            f"terminated with exit code {retcode}: {cmd}"
                        )
                        break
                    self._stop.wait(check_interval)
            except OSError as e:
                logger.error(f"Unable to start process '{cmd}': {e}")
            if self._stop.is_set():
                break
            delay = self._restart_backoff(delay)

    def cleanup(self, timeout: float = 5.0) -> None:
        """Stop restart loops and reap every tracked backend process.

        Signals all supervisor threads to stop (so none can relaunch a child),
        then terminates each running process, waits up to ``timeout`` seconds,
        and force-kills any that do not exit. Exited records are dropped.
        """
        logger.info("Terminating all processes.")
        self._stop.set()
        with self._processes_lock:
            processes = dict(self._processes)
            self._processes.clear()
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Process PID %s did not exit; killing.", process.pid
                )
                process.kill()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.error("Process PID %s could not be reaped.", process.pid)

    @staticmethod
    def make_request(url: str, method: str = 'GET',
                     timeout: float = 5) -> Optional[Union[Dict[str, Any], bytes]]:
        """Makes an HTTP request to the specified URL.

        Args:
            url (str): The URL to make the request to.
            method (str): The HTTP method to use ('GET' or 'POST').
            timeout (float): Maximum time in seconds to wait for the server
                before giving up. Prevents a hung host from blocking the
                calling (metadata) thread indefinitely.

        Returns:
            dict or bytes: The response in JSON format if applicable, otherwise the raw content.
        """
        try:
            if method.upper() == 'GET':
                response = requests.get(url, timeout=timeout)
            elif method.upper() == 'POST':
                response = requests.post(url, timeout=timeout)
            else:
                raise ValueError("Unsupported HTTP method. Use 'GET' or 'POST'.")

            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '')
            media_type = content_type.split(';', 1)[0].strip().lower()
            if media_type == 'application/json':
                return response.json()
            return response.content

        except (requests.RequestException, ValueError) as e:
            logger.error(f"An error occurred during the request: {e}")
            return None

    @staticmethod
    def request_json(url: str, method: str = 'GET',
                     timeout: float = 5) -> Optional[Dict[str, Any]]:
        """Return a successful JSON object response, otherwise ``None``."""
        result = UtilityLibrary.make_request(url, method, timeout)
        return result if isinstance(result, dict) else None

    @staticmethod
    def request_image(url: str, timeout: float = 5,
                      max_bytes: int = 5 * 1024 * 1024) -> Optional[bytes]:
        """Return a bounded successful image response, otherwise ``None``."""
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            media_type = response.headers.get(
                'Content-Type', ''
            ).split(';', 1)[0].strip().lower()
            data = response.content
            if not media_type.startswith('image/') or not data:
                return None
            return data if len(data) <= max_bytes else None
        except requests.RequestException as e:
            logger.error(f"Image request failed: {e}")
            return None

    @staticmethod
    def read_config(config_file: str) -> Dict[str, Dict[str, Any]]:
        """Reads a configuration file in a simple INI format.

        Args:
            config_file (str): Path to the configuration file.

        Returns:
            dict: Parsed configuration data.
        """
        if not os.path.isfile(config_file):
            logger.error(f"Config file '{config_file}' does not exist.")
            exit(1)

        conf = {}
        with open(config_file) as f:
            content = f.readlines()

        section = ''
        for line in content:
            line = line.strip()
            if line.startswith("#"):
                continue
            if line.startswith("["):
                section = re.findall(r"^\[(.+)\]$", line)[0]
                conf[section] = {}
                continue
            if '=' in line:
                key, value = map(str.strip, line.split('=', 1))
                if value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                elif re.match(r'^(\d+)$', value):
                    value = int(value)
                conf[section][key] = value
        return conf

    @staticmethod
    def restart_systemd_service(service_name: str) -> None:
        """Restart a system service in an init-system-agnostic way.

        On a systemd host this uses the ``org.freedesktop.systemd1`` D-Bus
        manager. On a minimal appliance image (e.g. the Buildroot build, which
        uses BusyBox init and has no systemd), it falls back to the SysV init
        script ``/etc/init.d/<name>`` and finally to ``service``. Any failure
        is logged but never raised, so a missing init backend degrades to a
        no-op rather than crashing the caller.

        Args:
            service_name (str): The service to restart, e.g. ``mpd`` or
                ``mpd.service`` (the ``.service`` suffix is optional and is
                stripped for the SysV/``service`` fallbacks).
        """
        # 1) Try systemd via D-Bus (Raspberry Pi OS / any systemd host).
        try:
            bus = dbus.SystemBus()
            systemd_manager = bus.get_object(
                'org.freedesktop.systemd1', '/org/freedesktop/systemd1'
            )
            systemd_interface = dbus.Interface(
                systemd_manager, 'org.freedesktop.systemd1.Manager'
            )
            unit = service_name if service_name.endswith('.service') \
                else f'{service_name}.service'
            systemd_interface.RestartUnit(unit, 'replace')
            logger.info(f"Service '{unit}' restarted via systemd.")
            return
        except dbus.DBusException as e:
            logger.debug(
                f"systemd D-Bus restart unavailable for '{service_name}': {e}; "
                "falling back to SysV init."
            )

        # 2) Fall back to SysV init scripts (BusyBox init / Buildroot image).
        short_name = service_name[:-len('.service')] \
            if service_name.endswith('.service') else service_name
        candidates = [
            ['/etc/init.d/S50mpd', 'restart'] if short_name == 'mpd'
            else [f'/etc/init.d/{short_name}', 'restart'],
            ['service', short_name, 'restart'],
        ]
        for cmd in candidates:
            try:
                if cmd[0].startswith('/etc/init.d/') and not os.path.exists(cmd[0]):
                    continue
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    logger.info(f"Service '{short_name}' restarted via {cmd[0]}.")
                    return
                logger.debug(
                    f"'{' '.join(cmd)}' failed: {result.stderr.strip()}"
                )
            except (FileNotFoundError, OSError) as e:
                logger.debug(f"'{' '.join(cmd)}' not runnable: {e}")

        logger.error(
            f"Failed to restart service '{service_name}': no working init backend."
        )
