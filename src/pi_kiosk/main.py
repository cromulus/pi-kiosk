from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from .config import ConfigError, KioskConfig, load_config
from .daylight import is_dark
from .display import ScreenController
from .logging_utils import setup_logging
from .presence import HomeAssistantMotion, InputActivityMonitor
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
    ha_motion = HomeAssistantMotion(config, logger.getChild("ha_motion"))
    input_activity = InputActivityMonitor(config, logger.getChild("input"))
    logger.info("Initial sensor health: %s", sensors.health_snapshot())
    if ha_motion.enabled:
        logger.info("Home Assistant motion presence enabled: %s", config.ha_motion_entity_id)

    running = True

    def _shutdown(signum: int, _frame) -> None:
        nonlocal running
        logger.info("Received signal %s, shutting down", signum)
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    last_presence_ts = time.monotonic()
    if config.enable_idle_screensaver:
        last_presence_ts -= config.presence_hold_sec + 1
    last_health_log = 0.0

    try:
        while running:
            readings = sensors.read()
            now = time.monotonic()

            distance_capable = config.enable_distance_sensor and sensors.distance_supported
            presence_detected = False

            if readings.distance_mm is not None:
                logger.debug("Distance reading: %.2f mm", readings.distance_mm)
                if readings.distance_mm <= config.distance_threshold_mm:
                    presence_detected = True

            ha_motion_state = ha_motion.poll(now)
            if ha_motion_state:
                presence_detected = True

            if input_activity.poll():
                presence_detected = True

            if presence_detected:
                last_presence_ts = now
                screen.wake_screen()

            presence_active = (now - last_presence_ts) <= config.presence_hold_sec
            dark_mode = is_dark(config)
            scheduled_brightness = screen.brightness_from_lux(
                readings.ambient_lux,
                dark_mode=dark_mode,
            )

            if config.enable_idle_screensaver and not presence_active:
                screen.set_screensaver(True)
                target_brightness = config.idle_brightness
                fade_seconds = config.brightness_fade_idle_sec
                gamma_profile = config.gamma_idle
            else:
                screen.set_screensaver(False)
                target_brightness = scheduled_brightness
                fade_seconds = config.brightness_fade_wake_sec
                gamma_profile = config.gamma_night if dark_mode else config.gamma_day

            screen.set_gamma_profile(gamma_profile)
            screen.set_brightness(target_brightness, fade_seconds=fade_seconds)

            if (
                distance_capable
                and not config.enable_idle_screensaver
                and (now - last_presence_ts) > config.inactivity_timeout_sec
            ):
                screen.sleep_screen()

            if readings.ambient_lux is not None:
                logger.debug(
                    "Ambient lux: %.2f -> brightness %s",
                    readings.ambient_lux,
                    target_brightness,
                )

            if now - last_health_log >= 60:
                last_health_log = now
                logger.info(
                    "Sensor health: %s presence_active=%s ha_motion=%s",
                    sensors.health_snapshot(),
                    presence_active,
                    ha_motion_state,
                )

            time.sleep(config.poll_interval_sec)
    finally:
        input_activity.close()

    logger.info("Pi Kiosk controller exiting")


if __name__ == "__main__":
    run()
