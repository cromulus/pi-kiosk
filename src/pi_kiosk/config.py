from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    FieldValidationInfo,
    ValidationError,
    field_validator,
    model_validator,
)


DEFAULT_CONFIG_PATH = Path("/etc/pi-kiosk/kiosk.env")


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""


class KioskConfig(BaseModel):
    model_config = {"extra": "ignore"}

    ha_base_url: AnyHttpUrl = Field(validation_alias="HA_BASE_URL")
    ha_extra_query: str = Field(
        default="", validation_alias="HA_EXTRA_QUERY", description="Query string additions appended to the HA URL."
    )

    distance_threshold_mm: int = Field(
        default=1500, validation_alias="DISTANCE_THRESHOLD_MM", ge=100, le=5000
    )
    inactivity_timeout_sec: int = Field(
        default=90, validation_alias="INACTIVITY_TIMEOUT_SEC", ge=5, le=3600
    )
    poll_interval_sec: float = Field(
        default=0.5, validation_alias="POLL_INTERVAL_SEC", ge=0.1, le=5.0
    )

    brightness_min: int = Field(
        default=10, validation_alias="BRIGHTNESS_MIN", ge=0, le=255
    )
    brightness_max: int = Field(
        default=255, validation_alias="BRIGHTNESS_MAX", ge=1, le=255
    )
    brightness_lux_max: float = Field(
        default=400.0, validation_alias="BRIGHTNESS_LUX_MAX", gt=0.0
    )
    default_brightness: int = Field(
        default=120, validation_alias="DEFAULT_BRIGHTNESS", ge=0, le=255
    )
    brightness_schedule: bool = Field(
        default=False, validation_alias="BRIGHTNESS_SCHEDULE"
    )
    day_brightness: int = Field(
        default=120, validation_alias="DAY_BRIGHTNESS", ge=0, le=255
    )
    night_brightness: int = Field(
        default=35, validation_alias="NIGHT_BRIGHTNESS", ge=0, le=255
    )

    daylight_schedule: str = Field(default="fixed", validation_alias="DAYLIGHT_SCHEDULE")
    daylight_light_start: str = Field(
        default="07:00", validation_alias="DAYLIGHT_LIGHT_START"
    )
    daylight_dark_start: str = Field(
        default="19:00", validation_alias="DAYLIGHT_DARK_START"
    )
    daylight_timezone: str | None = Field(
        default=None, validation_alias="DAYLIGHT_TIMEZONE"
    )
    daylight_latitude: float | None = Field(
        default=None, validation_alias="DAYLIGHT_LATITUDE", ge=-90.0, le=90.0
    )
    daylight_longitude: float | None = Field(
        default=None, validation_alias="DAYLIGHT_LONGITUDE", ge=-180.0, le=180.0
    )
    daylight_sunrise_offset_min: int = Field(
        default=0, validation_alias="DAYLIGHT_SUNRISE_OFFSET_MIN", ge=-360, le=360
    )
    daylight_sunset_offset_min: int = Field(
        default=0, validation_alias="DAYLIGHT_SUNSET_OFFSET_MIN", ge=-360, le=360
    )

    ha_api_base_url: str | None = Field(
        default=None, validation_alias="HA_API_BASE_URL"
    )
    ha_access_token: str | None = Field(
        default=None, validation_alias="HA_ACCESS_TOKEN"
    )
    ha_motion_entity_id: str | None = Field(
        default=None, validation_alias="HA_MOTION_ENTITY_ID"
    )
    ha_motion_poll_interval_sec: float = Field(
        default=0.5, validation_alias="HA_MOTION_POLL_INTERVAL_SEC", ge=0.2, le=10.0
    )
    presence_hold_sec: int = Field(
        default=180, validation_alias="PRESENCE_HOLD_SEC", ge=1, le=3600
    )
    input_activity_devices: str = Field(
        default="/dev/input/event1", validation_alias="INPUT_ACTIVITY_DEVICES"
    )
    enable_pull_to_refresh: bool = Field(
        default=True, validation_alias="ENABLE_PULL_TO_REFRESH"
    )
    pull_to_refresh_start_max_y: int = Field(
        default=40, validation_alias="PULL_TO_REFRESH_START_MAX_Y", ge=0, le=4096
    )
    pull_to_refresh_min_drag: int = Field(
        default=260, validation_alias="PULL_TO_REFRESH_MIN_DRAG", ge=1, le=4096
    )
    pull_to_refresh_cooldown_sec: float = Field(
        default=2.0, validation_alias="PULL_TO_REFRESH_COOLDOWN_SEC", ge=0.1, le=60.0
    )
    browser_reload_command: str | None = Field(
        default="/usr/bin/xdotool key --clearmodifiers F5",
        validation_alias="BROWSER_RELOAD_COMMAND",
    )

    enable_idle_screensaver: bool = Field(
        default=False, validation_alias="ENABLE_IDLE_SCREENSAVER"
    )
    idle_brightness: int = Field(
        default=10, validation_alias="IDLE_BRIGHTNESS", ge=0, le=255
    )
    brightness_fade_wake_sec: float = Field(
        default=0.4, validation_alias="BRIGHTNESS_FADE_WAKE_SEC", ge=0.0, le=30.0
    )
    brightness_fade_idle_sec: float = Field(
        default=3.0, validation_alias="BRIGHTNESS_FADE_IDLE_SEC", ge=0.0, le=60.0
    )

    enable_blue_light_reduction: bool = Field(
        default=False, validation_alias="ENABLE_BLUE_LIGHT_REDUCTION"
    )
    xrandr_output: str = Field(default="DSI-2", validation_alias="XRANDR_OUTPUT")
    gamma_day: str = Field(default="1.0:1.0:1.0", validation_alias="GAMMA_DAY")
    gamma_night: str = Field(default="1.0:0.88:0.65", validation_alias="GAMMA_NIGHT")
    gamma_idle: str = Field(default="1.0:0.78:0.45", validation_alias="GAMMA_IDLE")

    brightnessctl_bin: str = Field(
        default="/usr/bin/brightnessctl", validation_alias="BRIGHTNESSCTL_BIN"
    )
    brightnessctl_device: str | None = Field(
        default=None, validation_alias="BRIGHTNESSCTL_DEVICE"
    )
    backlight_path: str | None = Field(
        default=None, validation_alias="BACKLIGHT_PATH"
    )

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=False, validation_alias="LOG_JSON")

    enable_distance_sensor: bool = Field(
        default=True, validation_alias="ENABLE_DISTANCE_SENSOR"
    )
    enable_light_sensor: bool = Field(
        default=True, validation_alias="ENABLE_LIGHT_SENSOR"
    )

    enable_vnc: bool = Field(default=False, validation_alias="ENABLE_VNC")
    vnc_port: int = Field(default=5900, validation_alias="VNC_PORT", ge=1024, le=65535)
    vnc_password_file: str | None = Field(
        default=None, validation_alias="VNC_PASSWORD_FILE"
    )
    vnc_extra_args: str = Field(default="-shared -loop", validation_alias="VNC_EXTRA_ARGS")

    @field_validator(
        "vnc_password_file",
        "brightnessctl_device",
        "backlight_path",
        "browser_reload_command",
        "daylight_timezone",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("brightness_max")
    @classmethod
    def _validate_brightness_range(cls, value: int, info: FieldValidationInfo) -> int:
        min_value = info.data.get("brightness_min", 0)
        if value <= min_value:
            raise ValueError("BRIGHTNESS_MAX must be greater than BRIGHTNESS_MIN")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(
                f"LOG_LEVEL must be one of {', '.join(sorted(allowed))}, got {value!r}"
            )
        return upper

    @field_validator("daylight_schedule")
    @classmethod
    def _validate_daylight_schedule(cls, value: str) -> str:
        allowed = {"fixed", "sun"}
        normalized = value.lower()
        if normalized not in allowed:
            raise ValueError(
                f"DAYLIGHT_SCHEDULE must be one of {', '.join(sorted(allowed))}, got {value!r}"
            )
        return normalized

    @field_validator("daylight_timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("daylight_light_start", "daylight_dark_start")
    @classmethod
    def _validate_hhmm(cls, value: str) -> str:
        try:
            hour_raw, minute_raw = value.split(":", 1)
            hour = int(hour_raw)
            minute = int(minute_raw)
        except ValueError as exc:
            raise ValueError("must be HH:MM in 24-hour time") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("must be HH:MM in 24-hour time")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("ha_api_base_url", "ha_access_token", "ha_motion_entity_id", mode="before")
    @classmethod
    def _blank_optional_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("gamma_day", "gamma_night", "gamma_idle")
    @classmethod
    def _validate_gamma(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("gamma must be red:green:blue")
        try:
            parsed = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError("gamma values must be numbers") from exc
        if any(part <= 0 or part > 10 for part in parsed):
            raise ValueError("gamma values must be greater than 0 and no more than 10")
        return ":".join(f"{part:g}" for part in parsed)

    @model_validator(mode="after")
    def _validate_cross_field_settings(self) -> "KioskConfig":
        if self.daylight_schedule == "sun" and (
            self.daylight_latitude is None or self.daylight_longitude is None
        ):
            raise ValueError(
                "DAYLIGHT_SCHEDULE=sun requires DAYLIGHT_LATITUDE and DAYLIGHT_LONGITUDE"
            )
        return self

    def as_brightness_bounds(self) -> tuple[int, int]:
        return self.brightness_min, self.brightness_max


def parse_env_lines(lines: Iterable[str]) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if value:
            try:
                tokens = shlex.split(value, posix=True)
                parsed = tokens[0] if tokens else ""
            except ValueError:
                parsed = value
        else:
            parsed = ""
        data[key] = parsed
    return data


def load_config(path: Path | str | None = None) -> KioskConfig:
    env_path = Path(path) if path else Path(os.getenv("PI_KIOSK_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, str] = {}

    if env_path.exists():
        try:
            data.update(parse_env_lines(env_path.read_text(encoding="utf-8").splitlines()))
        except OSError as exc:  # pragma: no cover - filesystem failure
            raise ConfigError(f"Unable to read config file {env_path}: {exc}") from exc

    # Environment variables override file values.
    data.update({k: v for k, v in os.environ.items()})

    try:
        return KioskConfig.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - validation error surfaces to logs
        raise ConfigError(str(exc)) from exc
