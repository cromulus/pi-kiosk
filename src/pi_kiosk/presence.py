from __future__ import annotations

import json
import logging
import os
import select
import struct
import time
from dataclasses import dataclass
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import KioskConfig


ACTIVE_HA_STATES = {"on", "home", "detected", "open", "true"}
INPUT_EVENT_STRUCT = struct.Struct("llHHi")
INPUT_ACTIVITY_TYPES = {1, 2, 3}


class HomeAssistantMotion:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._logger = logger
        self._base_url = (config.ha_api_base_url or "").rstrip("/")
        self._token = config.ha_access_token
        self._entity_id = config.ha_motion_entity_id
        self._poll_interval = config.ha_motion_poll_interval_sec
        self._next_poll = 0.0
        self._state: Optional[bool] = None
        self._last_warning = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._token and self._entity_id)

    def poll(self, now: float) -> Optional[bool]:
        if not self.enabled or now < self._next_poll:
            return self._state
        self._next_poll = now + self._poll_interval

        request = Request(
            f"{self._base_url}/api/states/{self._entity_id}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )

        try:
            timeout = max(0.2, min(1.0, self._poll_interval))
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            self._warn(now, f"Unable to poll Home Assistant motion sensor: {exc}")
            return self._state

        state = str(payload.get("state", "")).lower()
        self._state = state in ACTIVE_HA_STATES
        return self._state

    def _warn(self, now: float, message: str) -> None:
        if now - self._last_warning < 60:
            return
        self._last_warning = now
        self._logger.warning(message)


@dataclass(slots=True)
class _InputDevice:
    path: str
    fd: int


class InputActivityMonitor:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._logger = logger
        self._devices: list[_InputDevice] = []

        for raw_path in config.input_activity_devices.split(","):
            path = raw_path.strip()
            if not path:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                self._logger.warning("Unable to monitor input device %s: %s", path, exc)
                continue
            self._devices.append(_InputDevice(path=path, fd=fd))

        if self._devices:
            self._logger.info(
                "Monitoring input activity from: %s",
                ", ".join(device.path for device in self._devices),
            )

    def close(self) -> None:
        for device in self._devices:
            try:
                os.close(device.fd)
            except OSError:
                pass
        self._devices.clear()

    def poll(self) -> bool:
        if not self._devices:
            return False

        activity = False
        readable, _, _ = select.select([device.fd for device in self._devices], [], [], 0)
        for fd in readable:
            while True:
                try:
                    data = os.read(fd, INPUT_EVENT_STRUCT.size * 32)
                except BlockingIOError:
                    break
                except OSError as exc:
                    self._logger.warning("Input device read failed: %s", exc)
                    break
                if not data:
                    break

                for offset in range(0, len(data) - INPUT_EVENT_STRUCT.size + 1, INPUT_EVENT_STRUCT.size):
                    _, _, event_type, _, _ = INPUT_EVENT_STRUCT.unpack_from(data, offset)
                    if event_type in INPUT_ACTIVITY_TYPES:
                        activity = True

                if len(data) < INPUT_EVENT_STRUCT.size * 32:
                    break

        return activity
