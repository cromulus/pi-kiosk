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
EV_KEY = 1
EV_ABS = 3
BTN_TOUCH = 330
ABS_Y = 1
ABS_MT_POSITION_Y = 54
ABS_MT_TRACKING_ID = 57
Y_POSITION_CODES = {ABS_Y, ABS_MT_POSITION_Y}


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


@dataclass(slots=True)
class InputActivity:
    activity: bool = False
    pull_to_refresh: bool = False

    def __bool__(self) -> bool:
        return self.activity


@dataclass(slots=True)
class _TouchGesture:
    active: bool = False
    start_y: Optional[int] = None
    max_y: Optional[int] = None
    last_y: Optional[int] = None
    started_at_edge: bool = False
    refresh_sent: bool = False


class InputActivityMonitor:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._devices: list[_InputDevice] = []
        self._gesture = _TouchGesture()
        self._last_pull_to_refresh = 0.0

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

    def poll(self) -> InputActivity:
        if not self._devices:
            return InputActivity()

        activity = False
        pull_to_refresh = False
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
                    _, _, event_type, event_code, event_value = INPUT_EVENT_STRUCT.unpack_from(data, offset)
                    if event_type in INPUT_ACTIVITY_TYPES:
                        activity = True
                    if self._process_pull_to_refresh_event(
                        event_type,
                        event_code,
                        event_value,
                    ):
                        pull_to_refresh = True

                if len(data) < INPUT_EVENT_STRUCT.size * 32:
                    break

        return InputActivity(activity=activity, pull_to_refresh=pull_to_refresh)

    def _process_pull_to_refresh_event(
        self,
        event_type: int,
        event_code: int,
        event_value: int,
    ) -> bool:
        if not self._config.enable_pull_to_refresh:
            return False

        if event_type == EV_ABS and event_code in Y_POSITION_CODES:
            self._gesture.last_y = event_value
            if self._gesture.active:
                if self._gesture.start_y is None:
                    self._gesture.start_y = event_value
                    self._gesture.max_y = event_value
                    self._gesture.started_at_edge = (
                        event_value <= self._config.pull_to_refresh_start_max_y
                    )
                else:
                    self._gesture.max_y = max(self._gesture.max_y or event_value, event_value)
                return self._pull_to_refresh_ready()
            return False

        if event_type == EV_ABS and event_code == ABS_MT_TRACKING_ID:
            if event_value >= 0:
                self._start_touch()
            else:
                self._end_touch()
            return False

        if event_type == EV_KEY and event_code == BTN_TOUCH:
            if event_value:
                self._start_touch()
            else:
                self._end_touch()
            return False

        return False

    def _start_touch(self) -> None:
        self._gesture.active = True
        self._gesture.start_y = None
        self._gesture.max_y = None
        self._gesture.last_y = None
        self._gesture.started_at_edge = False
        self._gesture.refresh_sent = False

    def _end_touch(self) -> None:
        self._gesture.active = False
        self._gesture.start_y = None
        self._gesture.max_y = None
        self._gesture.last_y = None
        self._gesture.started_at_edge = False
        self._gesture.refresh_sent = False

    def _pull_to_refresh_ready(self) -> bool:
        gesture = self._gesture
        if gesture.refresh_sent or gesture.start_y is None or gesture.max_y is None:
            return False
        if not gesture.started_at_edge:
            return False
        if gesture.max_y - gesture.start_y < self._config.pull_to_refresh_min_drag:
            return False

        now = time.monotonic()
        if now - self._last_pull_to_refresh < self._config.pull_to_refresh_cooldown_sec:
            return False

        gesture.refresh_sent = True
        self._last_pull_to_refresh = now
        self._logger.info(
            "Pull-to-refresh gesture detected: start_y=%s max_y=%s",
            gesture.start_y,
            gesture.max_y,
        )
        return True
