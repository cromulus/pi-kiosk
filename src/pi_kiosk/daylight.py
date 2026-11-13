from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]

from .config import ConfigError, KioskConfig, load_config


SECONDS_PER_DAY = 24 * 60 * 60
OFFICIAL_ZENITH_DEGREES = 90.833


@dataclass(frozen=True)
class DaylightWindow:
    light_start: datetime
    dark_start: datetime


def parse_hhmm(value: str) -> int:
    try:
        hour_raw, minute_raw = value.split(":", 1)
        hour = int(hour_raw)
        minute = int(minute_raw)
    except ValueError as exc:
        raise ValueError(f"Expected HH:MM time, got {value!r}") from exc

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Expected HH:MM time, got {value!r}")
    return hour * 3600 + minute * 60


def _configured_timezone(config: KioskConfig):
    if config.daylight_timezone and ZoneInfo is not None:
        return ZoneInfo(config.daylight_timezone)
    return datetime.now().astimezone().tzinfo


def _seconds_to_local_datetime(day: date, seconds: int, tzinfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tzinfo) + timedelta(seconds=seconds)


def _fixed_window(config: KioskConfig, day: date, tzinfo) -> DaylightWindow:
    light_start = _seconds_to_local_datetime(
        day,
        parse_hhmm(config.daylight_light_start),
        tzinfo,
    )
    dark_start = _seconds_to_local_datetime(
        day,
        parse_hhmm(config.daylight_dark_start),
        tzinfo,
    )
    return DaylightWindow(light_start=light_start, dark_start=dark_start)


def _normalize_degrees(value: float) -> float:
    return value % 360.0


def _solar_event_utc_hour(day: date, latitude: float, longitude: float, sunrise: bool) -> Optional[float]:
    day_of_year = day.timetuple().tm_yday
    longitude_hour = longitude / 15.0
    approximate_time = day_of_year + (((6 if sunrise else 18) - longitude_hour) / 24.0)

    mean_anomaly = (0.9856 * approximate_time) - 3.289
    true_longitude = _normalize_degrees(
        mean_anomaly
        + (1.916 * math.sin(math.radians(mean_anomaly)))
        + (0.020 * math.sin(math.radians(2 * mean_anomaly)))
        + 282.634
    )

    right_ascension = math.degrees(
        math.atan(0.91764 * math.tan(math.radians(true_longitude)))
    )
    right_ascension = _normalize_degrees(right_ascension)
    longitude_quadrant = math.floor(true_longitude / 90.0) * 90.0
    ascension_quadrant = math.floor(right_ascension / 90.0) * 90.0
    right_ascension = (right_ascension + longitude_quadrant - ascension_quadrant) / 15.0

    sin_declination = 0.39782 * math.sin(math.radians(true_longitude))
    cos_declination = math.cos(math.asin(sin_declination))

    cos_hour = (
        math.cos(math.radians(OFFICIAL_ZENITH_DEGREES))
        - (sin_declination * math.sin(math.radians(latitude)))
    ) / (cos_declination * math.cos(math.radians(latitude)))

    if cos_hour > 1 or cos_hour < -1:
        return None

    hour_angle = math.degrees(math.acos(cos_hour))
    if sunrise:
        hour_angle = 360.0 - hour_angle
    hour_angle /= 15.0

    local_mean_time = (
        hour_angle
        + right_ascension
        - (0.06571 * approximate_time)
        - 6.622
    )
    return (local_mean_time - longitude_hour) % 24.0


def _solar_event_datetime(
    day: date,
    latitude: float,
    longitude: float,
    tzinfo,
    sunrise: bool,
    offset_minutes: int,
) -> Optional[datetime]:
    utc_hour = _solar_event_utc_hour(day, latitude, longitude, sunrise)
    if utc_hour is None:
        return None

    utc_dt = datetime.combine(day, time.min).replace(tzinfo=ZoneInfo("UTC") if ZoneInfo else None)
    utc_dt = utc_dt + timedelta(hours=utc_hour)
    local_dt = utc_dt.astimezone(tzinfo)

    while local_dt.date() < day:
        local_dt += timedelta(days=1)
    while local_dt.date() > day:
        local_dt -= timedelta(days=1)

    return local_dt + timedelta(minutes=offset_minutes)


def _sun_window(config: KioskConfig, day: date, tzinfo) -> Optional[DaylightWindow]:
    if config.daylight_latitude is None or config.daylight_longitude is None:
        return None

    sunrise = _solar_event_datetime(
        day,
        config.daylight_latitude,
        config.daylight_longitude,
        tzinfo,
        sunrise=True,
        offset_minutes=config.daylight_sunrise_offset_min,
    )
    sunset = _solar_event_datetime(
        day,
        config.daylight_latitude,
        config.daylight_longitude,
        tzinfo,
        sunrise=False,
        offset_minutes=config.daylight_sunset_offset_min,
    )
    if sunrise is None or sunset is None:
        return None
    return DaylightWindow(light_start=sunrise, dark_start=sunset)


def daylight_window(config: KioskConfig, day: date, tzinfo=None) -> DaylightWindow:
    tzinfo = tzinfo or _configured_timezone(config)
    if config.daylight_schedule == "sun":
        sun_window = _sun_window(config, day, tzinfo)
        if sun_window is not None:
            return sun_window
    return _fixed_window(config, day, tzinfo)


def current_daylight_window(config: KioskConfig, now: Optional[datetime] = None) -> DaylightWindow:
    tzinfo = _configured_timezone(config)
    local_now = now.astimezone(tzinfo) if now else datetime.now(tzinfo)
    return daylight_window(config, local_now.date(), tzinfo)


def is_dark(config: KioskConfig, now: Optional[datetime] = None) -> bool:
    tzinfo = _configured_timezone(config)
    now = now.astimezone(tzinfo) if now else datetime.now(tzinfo)
    window = daylight_window(config, now.date(), tzinfo)
    return now < window.light_start or now >= window.dark_start


def is_sleep_time(config: KioskConfig, now: Optional[datetime] = None) -> bool:
    """Fixed local sleep window, independent of sunrise and sunset."""
    if not config.sleep_schedule:
        return False
    tzinfo = _configured_timezone(config)
    local_now = now.astimezone(tzinfo) if now else datetime.now(tzinfo)
    seconds = local_now.hour * 3600 + local_now.minute * 60 + local_now.second
    start, end = parse_hhmm(config.sleep_start), parse_hhmm(config.sleep_end)
    if start > end:
        return seconds >= start or seconds < end
    return start <= seconds < end


def seconds_until_transition(config: KioskConfig, now: Optional[datetime] = None) -> int:
    tzinfo = _configured_timezone(config)
    now = now.astimezone(tzinfo) if now else datetime.now(tzinfo)

    candidates = []
    for day_offset in range(3):
        window = daylight_window(config, now.date() + timedelta(days=day_offset), tzinfo)
        candidates.extend([window.light_start, window.dark_start])

    future = sorted(candidate for candidate in candidates if candidate > now)
    if not future:
        return 3600
    return max(1, int((future[0] - now).total_seconds()) + 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pi Kiosk day/night schedule helper")
    parser.add_argument("command", choices=["is-dark", "seconds-until-transition"])
    parser.add_argument("--config", metavar="PATH", help="Path to kiosk.env")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (ConfigError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "is-dark":
        return 0 if is_dark(config) else 1

    print(seconds_until_transition(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
