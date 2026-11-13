from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

from .config import ConfigError, KioskConfig, load_config
from .daylight import current_daylight_window, is_dark, is_sleep_time, seconds_until_transition
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
    screen.preflight()
    logger.info("Initial sensor health: %s", sensors.health_snapshot())
    logger.info("Initial input health: %s", input_activity.health_snapshot())
    logger.info("Initial display health: %s", screen.health_snapshot())
    if ha_motion.enabled:
        logger.info("Home Assistant motion presence enabled: %s", config.ha_motion_entity_id)
    elif config.ha_api_base_url or config.ha_access_token or config.ha_motion_entity_id:
        logger.warning(
            "Home Assistant motion presence is not fully configured; API URL, token, and entity ID are all required"
        )

    if config.enable_idle_screensaver and not (
        config.enable_distance_sensor or ha_motion.enabled or input_activity.healthy
    ):
        logger.warning(
            "Idle screensaver is enabled but no presence source is currently available"
        )

    daylight = current_daylight_window(config)
    logger.info(
        "Initial daylight mode: dark=%s light_start=%s dark_start=%s next_transition_sec=%s",
        is_dark(config),
        daylight.light_start.isoformat(),
        daylight.dark_start.isoformat(),
        seconds_until_transition(config),
    )

    running = True

    def _shutdown(signum: int, _frame) -> None:
        nonlocal running
        logger.info("Received signal %s, shutting down", signum)
        running = False

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    last_presence_ts = time.monotonic()
    if config.enable_idle_screensaver or config.sleep_schedule:
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

            input_state = input_activity.poll()
            if input_state:
                presence_detected = True
            if input_state.pull_to_refresh:
                screen.reload_browser()

            if presence_detected:
                last_presence_ts = now

            presence_active = (now - last_presence_ts) <= config.presence_hold_sec
            dark_mode = is_dark(config)
            sleep_mode = is_sleep_time(config)
            sleep_idle = sleep_mode and not presence_active
            scheduled_brightness = screen.brightness_from_lux(
                readings.ambient_lux,
                dark_mode=dark_mode,
            )

            if sleep_idle:
                screen.set_screensaver(True)
                target_brightness = 0
                fade_seconds = 0.0
                gamma_profile = config.gamma_idle
            elif config.enable_idle_screensaver and not presence_active and not config.sleep_schedule:
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
            screen.set_brightness(target_brightness, fade_seconds=fade_seconds, allow_off=sleep_idle)

            if sleep_idle:
                screen.sleep_screen()
            elif config.sleep_schedule or presence_detected:
                screen.wake_screen()

            if (
                distance_capable
                and not config.sleep_schedule
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
                    "Controller health: sensors=%s input=%s display=%s presence_active=%s ha_motion_enabled=%s ha_motion=%s dark_mode=%s next_transition_sec=%s",
                    sensors.health_snapshot(),
                    input_activity.health_snapshot(),
                    screen.health_snapshot(),
                    presence_active,
                    ha_motion.enabled,
                    ha_motion_state,
                    dark_mode,
                    seconds_until_transition(config),
                )

            time.sleep(config.poll_interval_sec)
    finally:
        input_activity.close()

    logger.info("Pi Kiosk controller exiting")


if __name__ == "__main__":
    run()
