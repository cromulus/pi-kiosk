from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .config import KioskConfig


@dataclass(slots=True)
class SensorReadings:
    distance_mm: Optional[float] = None
    ambient_lux: Optional[float] = None


class _BaseSensor:
    def __init__(self, name: str, logger: logging.Logger, enabled: bool) -> None:
        self._name = name
        self._logger = logger.getChild(name)
        self._enabled = enabled
        self._sensor = None
        self._fail_count = 0
        self._next_attempt = 0.0

    def is_supported(self) -> bool:
        return self._sensor is not None

    def disable(self) -> None:
        if self._enabled:
            self._logger.warning("%s disabled", self._name)
        self._enabled = False
        self._sensor = None

    def _backoff(self) -> None:
        self._fail_count += 1
        delay = min(2 ** self._fail_count, 60)
        self._next_attempt = time.monotonic() + delay
        if self._fail_count in (1, 3, 5):
            self._logger.warning(
                "%s read failed %s times; retrying in %ss",
                self._name,
                self._fail_count,
                delay,
            )

    def _ready(self) -> bool:
        if not self._enabled:
            return False
        now = time.monotonic()
        if now < self._next_attempt:
            return False
        if self._sensor is None:
            self._attempt_init()
        return self._sensor is not None

    def _attempt_init(self) -> None:
        raise NotImplementedError


class DistanceSensor(_BaseSensor):
    def __init__(self, logger: logging.Logger, enabled: bool, i2c) -> None:
        super().__init__("distance", logger, enabled)
        self._i2c = i2c
        if self._enabled:
            self._attempt_init()

    def read(self) -> Optional[float]:
        if not self._ready():
            return None
        try:
            sensor = self._sensor
            if sensor is None:
                return None
            if hasattr(sensor, "data_ready") and not sensor.data_ready:
                return None
            distance = getattr(sensor, "distance", None)
            if hasattr(sensor, "clear_interrupt"):
                sensor.clear_interrupt()
            if not distance or distance <= 0:
                return None
            self._fail_count = 0
            return float(distance)
        except Exception as exc:  # pragma: no cover - hardware specific
            self._logger.debug("Distance read error: %s", exc)
            self._sensor = None
            self._backoff()
            return None

    def _attempt_init(self) -> None:
        if not self._enabled:
            return
        if not self._i2c:
            self._logger.warning("I²C unavailable; distance sensor disabled")
            self.disable()
            return
        try:
            import adafruit_vl53l4cd  # type: ignore

            sensor = adafruit_vl53l4cd.VL53L4CD(self._i2c)
            try:
                sensor.inter_measurement = 0
                sensor.timing_budget = 200
                sensor.start_ranging()
            except Exception:
                self._logger.debug("start_ranging not supported on this sensor")
            self._sensor = sensor
            self._fail_count = 0
            self._logger.info("Distance sensor ready")
        except Exception as exc:  # pragma: no cover - hardware specific
            self._logger.warning("Unable to initialize distance sensor: %s", exc)
            self._sensor = None
            self._backoff()


class LightSensor(_BaseSensor):
    def __init__(self, logger: logging.Logger, enabled: bool, i2c) -> None:
        super().__init__("light", logger, enabled)
        self._i2c = i2c
        if self._enabled:
            self._attempt_init()

    def read(self) -> Optional[float]:
        if not self._ready():
            return None
        try:
            sensor = self._sensor
            if sensor is None:
                return None
            lux = getattr(sensor, "lux", None)
            if lux is None:
                return None
            self._fail_count = 0
            return float(lux)
        except Exception as exc:  # pragma: no cover - hardware specific
            self._logger.debug("Lux read error: %s", exc)
            self._sensor = None
            self._backoff()
            return None

    def _attempt_init(self) -> None:
        if not self._enabled:
            return
        if not self._i2c:
            self._logger.warning("I²C unavailable; light sensor disabled")
            self.disable()
            return
        try:
            import adafruit_veml7700  # type: ignore

            self._sensor = adafruit_veml7700.VEML7700(self._i2c)
            self._fail_count = 0
            self._logger.info("Light sensor ready")
        except Exception as exc:  # pragma: no cover - hardware specific
            self._logger.warning("Unable to initialize light sensor: %s", exc)
            self._sensor = None
            self._backoff()


class SensorSuite:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._logger = logger
        self._config = config
        self._i2c = self._init_i2c_bus()
        self.distance = DistanceSensor(logger, config.enable_distance_sensor, self._i2c)
        self.light = LightSensor(logger, config.enable_light_sensor, self._i2c)

    def _init_i2c_bus(self):
        if not (self._config.enable_distance_sensor or self._config.enable_light_sensor):
            return None
        try:
            import board  # type: ignore
            import busio  # type: ignore

            self._logger.debug("Initializing I²C bus")
            return busio.I2C(board.SCL, board.SDA)
        except Exception as exc:  # pragma: no cover - hardware specific
            self._logger.warning("Unable to initialize I²C bus: %s", exc)
            return None

    def read(self) -> SensorReadings:
        readings = SensorReadings()
        readings.distance_mm = self.distance.read()
        readings.ambient_lux = self.light.read()
        return readings

    @property
    def distance_supported(self) -> bool:
        return self.distance.is_supported()

    @property
    def light_supported(self) -> bool:
        return self.light.is_supported()

    def health_snapshot(self) -> dict[str, bool]:
        return {
            "distance_sensor": self.distance_supported,
            "light_sensor": self.light_supported,
            "i2c": self._i2c is not None,
        }
