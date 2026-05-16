from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Iterable

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    FieldValidationInfo,
    ValidationError,
    field_validator,
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

    @field_validator("vnc_password_file", mode="before")
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
