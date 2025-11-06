#!/usr/bin/env python3
"""
Ambient light + presence manager for the Pi kiosk.

The script is intentionally defensive: it keeps the kiosk operational even if
the I²C bus, one of the sensors, or display control utilities are missing. When
data is unavailable the code falls back to conservative defaults so a `git pull`
followed by the install script always yields a usable kiosk.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("pi-kiosk")


DISTANCE_THRESHOLD_MM = _env_int("DISTANCE_THRESHOLD_MM", 1500)
INACTIVITY_TIMEOUT_SEC = _env_int("INACTIVITY_TIMEOUT_SEC", 90)
BRIGHTNESS_MIN = _env_int("BRIGHTNESS_MIN", 10)
BRIGHTNESS_MAX = _env_int("BRIGHTNESS_MAX", 255)
BRIGHTNESS_LUX_MAX = _env_float("BRIGHTNESS_LUX_MAX", 400.0)
DEFAULT_BRIGHTNESS = _env_int("DEFAULT_BRIGHTNESS", 120)

BRIGHTNESSCTL_BIN = os.getenv("BRIGHTNESSCTL_BIN", "/usr/bin/brightnessctl")
BRIGHTNESSCTL_DEVICE = os.getenv("BRIGHTNESSCTL_DEVICE", "")
BACKLIGHT_PATH = os.getenv("BACKLIGHT_PATH", "")

DISPLAY = os.getenv("DISPLAY", ":0")
XAUTHORITY = os.getenv("XAUTHORITY")


@dataclass
class SensorReadings:
    distance_mm: Optional[float]
    ambient_lux: Optional[float]


class SensorSuite:
    """Wrap the Adafruit sensors but degrade gracefully when unavailable."""

    def __init__(self) -> None:
        self._i2c = None
        self._distance_sensor = None
        self._tof_started = False
        self._light_sensor = None
        self._init_bus()
        self._init_sensors()

    @property
    def distance_supported(self) -> bool:
        return self._distance_sensor is not None

    @property
    def light_supported(self) -> bool:
        return self._light_sensor is not None

    def _init_bus(self) -> None:
        try:
            import board  # type: ignore
            import busio  # type: ignore

            self._i2c = busio.I2C(board.SCL, board.SDA)
            logger.info("I²C bus initialized")
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.warning("I²C unavailable, sensors disabled: %s", exc)
            self._i2c = None

    def _init_sensors(self) -> None:
        if not self._i2c:
            return

        try:
            import adafruit_vl53l4cd  # type: ignore

            self._distance_sensor = adafruit_vl53l4cd.VL53L4CD(self._i2c)
            try:
                self._distance_sensor.inter_measurement = 0
                self._distance_sensor.timing_budget = 200
                self._distance_sensor.start_ranging()
                self._tof_started = True
            except Exception:
                logger.debug("VL53L4CX start_ranging not supported on this rev")
            logger.info("VL53L4CX/VL53L4CD sensor ready")
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.warning("Distance sensor unavailable: %s", exc)
            self._distance_sensor = None

        try:
            import adafruit_veml7700  # type: ignore

            self._light_sensor = adafruit_veml7700.VEML7700(self._i2c)
            logger.info("VEML7700 sensor ready")
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.warning("Ambient light sensor unavailable: %s", exc)
            self._light_sensor = None

    def read(self) -> SensorReadings:
        return SensorReadings(
            distance_mm=self._read_distance(), ambient_lux=self._read_lux()
        )

    def _read_distance(self) -> Optional[float]:
        sensor = self._distance_sensor
        if not sensor:
            return None
        try:  # pragma: no cover - hardware specific
            if hasattr(sensor, "data_ready") and not sensor.data_ready:
                return None
            distance = getattr(sensor, "distance", None)
            if hasattr(sensor, "clear_interrupt"):
                sensor.clear_interrupt()
            if distance is None or distance <= 0:
                return None
            return float(distance)
        except Exception as exc:
            logger.debug("Distance read failed: %s", exc)
            return None

    def _read_lux(self) -> Optional[float]:
        sensor = self._light_sensor
        if not sensor:
            return None
        try:  # pragma: no cover - hardware specific
            lux = getattr(sensor, "lux", None)
            if lux is None:
                return None
            return float(lux)
        except Exception as exc:
            logger.debug("Lux read failed: %s", exc)
            return None


class ScreenController:
    def __init__(self) -> None:
        self._last_brightness = None
        self._screen_on = True
        self._brightnessctl_available = os.path.exists(BRIGHTNESSCTL_BIN)
        self._warned_brightness = False

    def wake_screen(self) -> None:
        if self._screen_on:
            return
        self._run_display_cmd(["xset", "dpms", "force", "on"])
        self._screen_on = True

    def sleep_screen(self) -> None:
        if not self._screen_on:
            return
        self._run_display_cmd(["xset", "dpms", "force", "off"])
        self._screen_on = False

    def set_brightness(self, target: int) -> None:
        target = int(max(BRIGHTNESS_MIN, min(BRIGHTNESS_MAX, target)))
        if self._last_brightness == target:
            return

        if self._brightnessctl_available:
            cmd = [BRIGHTNESSCTL_BIN, "set", str(target)]
            if BRIGHTNESSCTL_DEVICE:
                cmd.insert(1, f"--device={BRIGHTNESSCTL_DEVICE}")
            self._run(cmd)
        elif BACKLIGHT_PATH:
            try:
                with open(BACKLIGHT_PATH, "w", encoding="ascii") as handle:
                    handle.write(str(target))
            except OSError as exc:
                if not self._warned_brightness:
                    logger.warning("Backlight write failed: %s", exc)
                    self._warned_brightness = True
                return
        else:
            if not self._warned_brightness:
                logger.warning("No brightness control utility configured")
                self._warned_brightness = True
            return

        self._last_brightness = target
        logger.debug("Brightness set to %s", target)

    def _run_display_cmd(self, args: list[str]) -> None:
        env = os.environ.copy()
        env["DISPLAY"] = DISPLAY
        if XAUTHORITY:
            env["XAUTHORITY"] = XAUTHORITY
        self._run(args, env=env)

    @staticmethod
    def _run(args: list[str], env: Optional[dict[str, str]] = None) -> None:
        try:
            subprocess.run(args, check=False, env=env)
        except FileNotFoundError as exc:
            logger.warning("Command missing: %s", exc)
        except Exception as exc:  # pragma: no cover - best effort logging
            logger.warning("Command %s failed: %s", args, exc)


def brightness_from_lux(lux: Optional[float]) -> int:
    if lux is None:
        return DEFAULT_BRIGHTNESS
    ratio = min(1.0, lux / max(1.0, BRIGHTNESS_LUX_MAX))
    target = BRIGHTNESS_MIN + (BRIGHTNESS_MAX - BRIGHTNESS_MIN) * ratio
    return int(target)


def main() -> int:
    sensors = SensorSuite()
    screen = ScreenController()

    last_motion_ts = time.monotonic()
    motion_supported = sensors.distance_supported

    logger.info(
        "Sensors: motion=%s ambient=%s",
        "enabled" if motion_supported else "missing",
        "enabled" if sensors.light_supported else "missing",
    )

    while True:
        readings = sensors.read()
        now = time.monotonic()

        if (
            readings.distance_mm is not None
            and readings.distance_mm <= DISTANCE_THRESHOLD_MM
        ):
            last_motion_ts = now
            screen.wake_screen()
        elif motion_supported and (now - last_motion_ts) > INACTIVITY_TIMEOUT_SEC:
            screen.sleep_screen()

        target_brightness = brightness_from_lux(readings.ambient_lux)
        screen.set_brightness(target_brightness)

        time.sleep(0.5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit(0)
