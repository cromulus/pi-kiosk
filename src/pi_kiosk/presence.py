from __future__ import annotations

import json
import logging
import os
from pathlib import Path
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
INPUT_REOPEN_INTERVAL_SEC = 10.0
TOUCH_NAME_MARKERS = ("touch", "goodix", "capacitive")


class HomeAssistantMotion:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._logger = logger
        self._base_url = (config.ha_api_base_url or "").rstrip("/")
        self._token = config.ha_access_token
        self._entity_id = config.ha_motion_entity_id
        self._poll_interval = config.ha_motion_poll_interval_sec
        self._request_timeout = config.ha_motion_request_timeout_sec
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
            with urlopen(request, timeout=self._request_timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            self._warn(now, f"Unable to poll Home Assistant motion sensor: {exc}")
            self._state = None
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
        self._next_reopen = 0.0
        self._last_open_warning = 0.0
        self._requested_paths = [
            path
            for path in (
                raw_path.strip() for raw_path in config.input_activity_devices.split(",")
            )
            if path and path.lower() != "auto"
        ]

        self._refresh_devices(initial=True)

    @property
    def healthy(self) -> bool:
        return bool(self._devices)

    def health_snapshot(self) -> dict[str, object]:
        return {
            "input_devices_ok": self.healthy,
            "input_devices": [device.path for device in self._devices],
            "input_discovery": self._config.input_activity_devices,
        }

    def _refresh_devices(self, initial: bool = False) -> None:
        opened_paths = {device.path for device in self._devices}
        opened_new = False
        now = time.monotonic()

        for path in self._candidate_paths():
            if path in opened_paths:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError as exc:
                self._warn_open_failure(now, path, exc, initial)
                continue
            self._devices.append(_InputDevice(path=path, fd=fd))
            opened_paths.add(path)
            opened_new = True

        if opened_new:
            self._logger.info(
                "Monitoring input activity from: %s",
                ", ".join(device.path for device in self._devices),
            )
        elif initial and not self._devices:
            self._logger.warning(
                "No input activity devices opened from %s; will retry with discovery",
                self._config.input_activity_devices,
            )

        if not self._devices:
            self._next_reopen = now + INPUT_REOPEN_INTERVAL_SEC

    def _warn_open_failure(
        self,
        now: float,
        path: str,
        exc: OSError,
        initial: bool,
    ) -> None:
        if not initial and now - self._last_open_warning < 60:
            return
        self._last_open_warning = now
        self._logger.warning("Unable to monitor input device %s: %s", path, exc)

    def _candidate_paths(self) -> list[str]:
        paths: list[str] = []
        for path in self._requested_paths:
            self._append_unique_path(paths, path)

        for path in self._discover_touch_event_paths():
            self._append_unique_path(paths, path)

        return paths

    def _append_unique_path(self, paths: list[str], path: str) -> None:
        if not path:
            return
        try:
            path_key = str(Path(path).resolve(strict=False))
        except OSError:
            path_key = path
        for existing in paths:
            try:
                existing_key = str(Path(existing).resolve(strict=False))
            except OSError:
                existing_key = existing
            if existing_key == path_key:
                return
        paths.append(path)

    def _discover_touch_event_paths(self) -> list[str]:
        paths: list[str] = []
        proc_path = Path("/proc/bus/input/devices")
        try:
            blocks = proc_path.read_text(encoding="utf-8").split("\n\n")
        except OSError:
            blocks = []

        for block in blocks:
            name = ""
            handlers = ""
            for line in block.splitlines():
                if line.startswith("N: Name="):
                    name = line.partition("=")[2].strip().strip('"')
                elif line.startswith("H: Handlers="):
                    handlers = line.partition("=")[2]

            lower_name = name.lower()
            if not any(marker in lower_name for marker in TOUCH_NAME_MARKERS):
                continue

            for token in handlers.split():
                if token.startswith("event"):
                    self._append_unique_path(paths, f"/dev/input/{token}")

        for glob_pattern in ("/dev/input/by-id/*event*", "/dev/input/by-path/*event*"):
            for candidate in sorted(Path("/").glob(glob_pattern.lstrip("/"))):
                lower_name = candidate.name.lower()
                if any(marker in lower_name for marker in TOUCH_NAME_MARKERS):
                    self._append_unique_path(paths, str(candidate))

        return paths

    def close(self) -> None:
        for device in self._devices:
            try:
                os.close(device.fd)
            except OSError:
                pass
        self._devices.clear()

    def poll(self) -> InputActivity:
        if not self._devices:
            now = time.monotonic()
            if now >= self._next_reopen:
                self._refresh_devices()
            return InputActivity()

        activity = False
        pull_to_refresh = False
        try:
            readable, _, _ = select.select(
                [device.fd for device in self._devices],
                [],
                [],
                0,
            )
        except OSError as exc:
            self._logger.warning("Input device select failed: %s", exc)
            self._close_all_and_schedule_reopen()
            return InputActivity()

        for fd in readable:
            while True:
                try:
                    data = os.read(fd, INPUT_EVENT_STRUCT.size * 32)
                except BlockingIOError:
                    break
                except OSError as exc:
                    self._logger.warning("Input device read failed: %s", exc)
                    self._close_device(fd)
                    break
                if not data:
                    self._close_device(fd)
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

    def _close_device(self, fd: int) -> None:
        kept: list[_InputDevice] = []
        for device in self._devices:
            if device.fd != fd:
                kept.append(device)
                continue
            try:
                os.close(device.fd)
            except OSError:
                pass
            self._logger.warning("Stopped monitoring input device %s", device.path)
        self._devices = kept
        self._next_reopen = time.monotonic() + INPUT_REOPEN_INTERVAL_SEC

    def _close_all_and_schedule_reopen(self) -> None:
        self.close()
        self._next_reopen = time.monotonic() + INPUT_REOPEN_INTERVAL_SEC

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
