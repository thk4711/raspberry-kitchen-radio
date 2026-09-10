"""Atomic publication of live ADS1115 readings for the web diagnostics page."""

import json
import logging
import os
import tempfile
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
ADC_STATUS_FILE_MODE = 0o644


def resolve_adc_status_file(config: Dict[str, Any], env_value: Optional[str]) -> str:
    adc_status = config.get("adc_status", {})
    if adc_status.get("enabled", True) is False:
        return ""
    path = env_value.strip() if env_value is not None else str(
        adc_status.get("file", "/tmp/radio-adc.json")
    ).strip()
    if not path:
        return ""
    try:
        with open(path, "a"):
            pass
    except OSError as exc:
        logger.warning("ADC status disabled; cannot write %s: %s", path, exc)
        return ""
    return path


def write_adc_snapshot(path: str, snapshot: Dict[str, Any]) -> None:
    if not path:
        return
    payload = dict(snapshot)
    payload["updated_at"] = time.time()
    tmp_path = ""
    try:
        data = json.dumps(payload, allow_nan=False)
        directory = os.path.dirname(path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        with os.fdopen(fd, "w") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, ADC_STATUS_FILE_MODE)
        os.replace(tmp_path, path)
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("Unable to publish ADC status: %s", exc)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
