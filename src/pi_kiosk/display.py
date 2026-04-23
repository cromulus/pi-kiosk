from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .config import KioskConfig


@dataclass(slots=True)
class DisplayState:
    screen_on: bool = True
    brightness: Optional[int] = None


class ScreenController:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._state = DisplayState()
        self._warned_brightness = False
        self._last_brightnessctl_warning: Optional[float] = None
        self._brightnessctl_max_loaded = False
        self._brightnessctl_max: Optional[int] = None

        self._brightnessctl_bin = config.brightnessctl_bin
        self._brightnessctl_device = config.brightnessctl_device
        self._backlight_path = config.backlight_path

    @property
    def state(self) -> DisplayState:
        return self._state

    def brightness_from_lux(self, lux: Optional[float]) -> int:
        if lux is None:
            self._logger.debug("Lux unavailable, using default brightness %s", self._config.default_brightness)
            return self._config.default_brightness

        min_b, max_b = self._config.as_brightness_bounds()
        ratio = min(1.0, lux / max(1.0, self._config.brightness_lux_max))
        value = int(min_b + (max_b - min_b) * ratio)
        return max(min_b, min(max_b, value))

    def wake_screen(self) -> None:
        if self._state.screen_on:
            return
        self._logger.info("Waking screen")
        self._run_display_cmd(["xset", "dpms", "force", "on"])
        self._state.screen_on = True

    def sleep_screen(self) -> None:
        if not self._state.screen_on:
            return
        self._logger.info("Blanking screen")
        self._run_display_cmd(["xset", "dpms", "force", "off"])
        self._state.screen_on = False

    def set_brightness(self, target: int) -> None:
        min_b, max_b = self._config.as_brightness_bounds()
        target = int(max(min_b, min(max_b, target)))

        if self._state.brightness == target:
            return

        if self._use_brightnessctl(target):
            self._logger.debug("Brightness set via brightnessctl: %s", target)
        elif self._write_backlight_file(target):
            self._logger.debug("Brightness set via backlight file: %s", target)
        else:
            if not self._warned_brightness:
                self._logger.warning(
                    "No brightness control method available. "
                    "Set BRIGHTNESSCTL_BIN or BACKLIGHT_PATH."
                )
                self._warned_brightness = True
            return

        self._state.brightness = target

    def _use_brightnessctl(self, target: int) -> bool:
        if not self._brightnessctl_bin or not os.path.exists(self._brightnessctl_bin):
            return False

        args = [self._brightnessctl_bin]
        if self._brightnessctl_device:
            args.append(f"--device={self._brightnessctl_device}")
        actual_target = self._brightnessctl_target(target)
        args.extend(["set", str(actual_target)])
        if self._run(args):
            return True
        self._warn_brightnessctl_failure(args)
        return False

    def _brightnessctl_target(self, target: int) -> int:
        device_max = self._get_brightnessctl_max()
        if not device_max:
            return target

        logical_max = max(1, self._config.brightness_max)
        scaled = round((target / logical_max) * device_max)
        if target > 0:
            scaled = max(1, scaled)
        return max(0, min(device_max, scaled))

    def _get_brightnessctl_max(self) -> Optional[int]:
        if self._brightnessctl_max_loaded:
            return self._brightnessctl_max

        self._brightnessctl_max_loaded = True
        if not self._brightnessctl_bin or not os.path.exists(self._brightnessctl_bin):
            return None

        args = [self._brightnessctl_bin]
        if self._brightnessctl_device:
            args.append(f"--device={self._brightnessctl_device}")
        args.append("max")

        try:
            result = subprocess.run(
                args,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except FileNotFoundError:
            return None

        if result.returncode != 0:
            return None
        try:
            max_value = int(result.stdout.strip())
        except ValueError:
            return None
        if max_value <= 0:
            return None
        self._brightnessctl_max = max_value
        return max_value

    def _warn_brightnessctl_failure(self, args: list[str]) -> None:
        now = time.monotonic()
        if (
            self._last_brightnessctl_warning is not None
            and now - self._last_brightnessctl_warning < 60
        ):
            return
        self._last_brightnessctl_warning = now
        self._logger.warning(
            "brightnessctl command failed. Args=%s (bin=%s)",
            args,
            self._brightnessctl_bin,
        )

    def _write_backlight_file(self, target: int) -> bool:
        if not self._backlight_path:
            return False
        try:
            with open(self._backlight_path, "w", encoding="ascii") as handle:
                handle.write(str(target))
            return True
        except OSError as exc:  # pragma: no cover - hardware specific
            self._logger.warning("Failed writing %s: %s", self._backlight_path, exc)
            return False

    def _run_display_cmd(self, args: list[str]) -> None:
        env = os.environ.copy()
        env.setdefault("DISPLAY", os.getenv("DISPLAY", ":0"))
        xauthority = os.getenv("XAUTHORITY")
        if xauthority:
            env["XAUTHORITY"] = xauthority
        if not self._run(args, env=env):
            self._logger.warning("Failed to run display command: %s", args)

    def _run(self, args: list[str], env: Optional[dict[str, str]] = None) -> bool:
        try:
            result = subprocess.run(
                args,
                check=False,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0
        except FileNotFoundError as exc:
            self._logger.debug("Command missing: %s (%s)", args[0], exc)
            return False
        except Exception as exc:  # pragma: no cover - best effort
            self._logger.warning("Command %s failed: %s", args, exc)
            return False
