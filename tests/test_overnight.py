import logging
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from pi_kiosk.config import KioskConfig
from pi_kiosk.daylight import is_sleep_time
from pi_kiosk.display import ScreenController
from pi_kiosk.main import run
from pi_kiosk.presence import HomeAssistantMotion, InputActivity
from pi_kiosk.sensors import SensorReadings


def config(**overrides):
    return KioskConfig(**{
        "HA_BASE_URL": "http://homeassistant.local:8123/kiosk",
        "DAYLIGHT_TIMEZONE": "America/New_York",
        "SLEEP_SCHEDULE": True,
        **overrides,
    })


class OvernightTests(unittest.TestCase):
    def test_midnight_window_boundaries_and_dst(self):
        for day in ("2026-10-09", "2026-11-01"):
            for clock, expected in (("22:59:59", False), ("23:00:00", True),
                                    ("00:00:00", True), ("05:59:59", True),
                                    ("06:00:00", False)):
                with self.subTest(day=day, clock=clock):
                    now = datetime.fromisoformat(f"{day}T{clock}").replace(
                        tzinfo=ZoneInfo("America/New_York"))
                    self.assertEqual(is_sleep_time(config(), now), expected)
                    self.assertFalse(is_sleep_time(config(SLEEP_SCHEDULE=False), now))

    def test_invalid_times_and_equal_window(self):
        for overrides in ({"SLEEP_START": "25:00"}, {"SLEEP_END": "23:00"}):
            with self.assertRaises(ValidationError):
                config(**overrides)

    def test_backlight_zero_bypasses_awake_minimum_only_when_requested(self):
        screen = ScreenController(config(BRIGHTNESS_MIN=1), logging.getLogger("test"))
        with patch.object(screen, "_use_brightnessctl", return_value=True):
            screen.set_brightness(0)
            self.assertEqual(screen.state.brightness, 1)
            screen.set_brightness(0, allow_off=True)
            self.assertEqual(screen.state.brightness, 0)
            screen.set_brightness(1)
            self.assertEqual(screen.state.brightness, 1)

    def test_controller_sleep_wake_and_morning(self):
        for sleep_mode, motion in ((True, False), (True, True), (False, False)):
            with self.subTest(sleep_mode=sleep_mode, motion=motion):
                screen = MagicMock()
                screen.brightness_from_lux.return_value = 1
                sensors = MagicMock()
                sensors.read.return_value = SensorReadings()
                input_monitor = MagicMock()
                input_monitor.poll.return_value = InputActivity()
                with patch("pi_kiosk.main.load_config", return_value=config(ENABLE_IDLE_SCREENSAVER=True)), \
                     patch("pi_kiosk.main.SensorSuite", return_value=sensors), \
                     patch("pi_kiosk.main.ScreenController", return_value=screen), \
                     patch("pi_kiosk.main.InputActivityMonitor", return_value=input_monitor), \
                     patch("pi_kiosk.main.HomeAssistantMotion") as ha, \
                     patch("pi_kiosk.main.is_sleep_time", return_value=sleep_mode), \
                     patch("pi_kiosk.main.time.monotonic", side_effect=[1000, 1001]), \
                     patch("pi_kiosk.main.time.sleep", side_effect=StopIteration), \
                     patch("pi_kiosk.main.signal.signal"):
                    ha.return_value.poll.return_value = motion
                    with self.assertRaises(StopIteration):
                        run([])
                if sleep_mode and not motion:
                    screen.sleep_screen.assert_called_once()
                    screen.set_brightness.assert_called_with(0, fade_seconds=0.0, allow_off=True)
                    screen.wake_screen.assert_not_called()
                else:
                    screen.wake_screen.assert_called_once()
                    screen.sleep_screen.assert_not_called()
                    screen.set_screensaver.assert_called_with(False)

    def test_failed_motion_poll_does_not_keep_stale_presence(self):
        motion = HomeAssistantMotion(config(HA_API_BASE_URL="http://ha.local:8123",
            HA_ACCESS_TOKEN="test", HA_MOTION_ENTITY_ID="binary_sensor.motion"), logging.getLogger("test"))
        motion._state = True
        with patch("pi_kiosk.presence.urlopen", side_effect=OSError("offline")) as request:
            self.assertIsNone(motion.poll(1000))
            self.assertEqual(request.call_args.kwargs["timeout"], 2.0)
            self.assertIsNone(motion.poll(1000.1))


if __name__ == "__main__":
    unittest.main()
