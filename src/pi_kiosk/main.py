from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from .config import ConfigError, KioskConfig, load_config
from .display import ScreenController
from .logging_utils import setup_logging
from .sensors import SensorSuite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pi Kiosk sensor controller")
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to kiosk.env style configuration file "
        "(defaults to $PI_KIOSK_CONFIG or /etc/pi-kiosk/kiosk.env)",
    )
    return parser


def run(argv: Optional[list[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)

    logger = setup_logging(config.log_level, config.log_json)
    logger.info("Starting Pi Kiosk controller (log_json=%s)", config.log_json)

    sensors = SensorSuite(config, logger.getChild("sensors"))
    screen = ScreenController(config, logger.getChild("display"))
    logger.info("Initial sensor health: %s", sensors.health_snapshot())

    running = True

    def _shutdown(signum: int, _frame) -> None:
        nonlocal running
        logger.info("Received signal %s, shutting down", signum)
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    last_motion_ts = time.monotonic()
    last_health_log = 0.0

    while running:
        readings = sensors.read()
        now = time.monotonic()

        distance_capable = config.enable_distance_sensor and sensors.distance_supported

        if readings.distance_mm is not None:
            logger.debug("Distance reading: %.2f mm", readings.distance_mm)
            if readings.distance_mm <= config.distance_threshold_mm:
                last_motion_ts = now
                screen.wake_screen()

        if distance_capable and (now - last_motion_ts) > config.inactivity_timeout_sec:
            screen.sleep_screen()

        brightness = screen.brightness_from_lux(readings.ambient_lux)
        screen.set_brightness(brightness)

        if readings.ambient_lux is not None:
            logger.debug("Ambient lux: %.2f -> brightness %s", readings.ambient_lux, brightness)

        if now - last_health_log >= 60:
            last_health_log = now
            logger.info("Sensor health: %s", sensors.health_snapshot())

        time.sleep(config.poll_interval_sec)

    logger.info("Pi Kiosk controller exiting")


if __name__ == "__main__":
    run()
