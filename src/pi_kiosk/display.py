from __future__ import annotations

import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .config import KioskConfig


DISPLAY_STATE_REFRESH_SEC = 30.0


@dataclass(slots=True)
class DisplayState:
    screen_on: bool = True
    brightness: Optional[int] = None
    brightness_target: Optional[int] = None
    screensaver_active: bool = False
    screensaver_applied_at: float = 0.0
    gamma_profile: Optional[str] = None
    gamma_applied_at: float = 0.0


class ScreenController:
    def __init__(self, config: KioskConfig, logger: logging.Logger) -> None:
        self._config = config
        self._logger = logger
        self._state = DisplayState()
        self._warned_brightness = False
        self._last_brightnessctl_warning: Optional[float] = None
        self._last_brightness_verify_warning: Optional[float] = None
        self._brightnessctl_max_loaded = False
        self._brightnessctl_max: Optional[int] = None
        self._brightness_backend = "unavailable"

        self._brightnessctl_bin = config.brightnessctl_bin
        self._brightnessctl_device = config.brightnessctl_device
        self._backlight_path = config.backlight_path

    @property
    def state(self) -> DisplayState:
        return self._state

    @property
    def brightness_backend(self) -> str:
        return self._brightness_backend

    def health_snapshot(self) -> dict[str, object]:
        return {
            "brightness_backend": self._brightness_backend,
            "brightnessctl_device": self._brightnessctl_device,
            "backlight_path": self._backlight_path,
            "xrandr_output": self._config.xrandr_output,
        }

    def preflight(self) -> None:
        for command in ("xset", "xrandr"):
            if shutil.which(command) is None:
                self._logger.warning("Display command missing from PATH: %s", command)

        if self._config.enable_blue_light_reduction:
            query_args = ["xrandr", "--query"]
            if not self._run(query_args, env=self._display_env()):
                self._logger.warning(
                    "Unable to query xrandr output %s; gamma changes may fail",
                    self._config.xrandr_output,
                )

        if self._brightnessctl_bin:
            if not os.path.exists(self._brightnessctl_bin):
                self._logger.warning(
                    "Configured brightnessctl binary is missing: %s",
                    self._brightnessctl_bin,
                )
            elif self._get_brightnessctl_max() is not None:
                self._brightness_backend = self._brightnessctl_backend_name()
                self._logger.info(
                    "Brightness backend ready: %s",
                    self._brightness_backend,
                )
                return

        if self._backlight_path:
            path = Path(self._backlight_path)
            if path.exists() and os.access(path, os.W_OK):
                self._brightness_backend = f"backlight:{self._backlight_path}"
                self._logger.info(
                    "Brightness backend ready: %s",
                    self._brightness_backend,
                )
                return
            self._logger.warning("Backlight path is not writable: %s", self._backlight_path)

        self._logger.warning("No usable brightness backend detected during preflight")

    def brightness_from_lux(self, lux: Optional[float], dark_mode: bool = False) -> int:
        if self._config.brightness_schedule:
            if dark_mode:
                self._logger.debug(
                    "Night schedule active, using night brightness %s",
                    self._config.night_brightness,
                )
                return self._config.night_brightness
            if lux is None:
                self._logger.debug(
                    "Lux unavailable, using day brightness %s",
                    self._config.day_brightness,
                )
                return self._config.day_brightness

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

    def reload_browser(self) -> bool:
        if not self._config.browser_reload_command:
            return False
        try:
            args = shlex.split(self._config.browser_reload_command)
        except ValueError as exc:
            self._logger.warning("Invalid browser reload command: %s", exc)
            return False
        if not args:
            return False

        self._logger.info("Reloading browser from pull-to-refresh gesture")
        env = self._display_env()
        if self._run(args, env=env):
            return True
        self._logger.warning("Failed to run browser reload command: %s", args)
        return False

    def sleep_screen(self) -> None:
        if not self._state.screen_on:
            return
        self._logger.info("Blanking screen")
        self._run_display_cmd(["xset", "dpms", "force", "off"])
        self._state.screen_on = False

    def set_brightness(self, target: int, fade_seconds: float = 0.0) -> None:
        min_b, max_b = self._config.as_brightness_bounds()
        target = int(max(min_b, min(max_b, target)))

        if self._state.brightness == target:
            self._state.brightness_target = target
            return

        if (
            fade_seconds > 0
            and self._state.brightness is not None
            and self._state.brightness != target
        ):
            elapsed = self._config.poll_interval_sec
            distance = target - self._state.brightness
            step = max(1, round(abs(distance) * min(1.0, elapsed / fade_seconds)))
            if distance < 0:
                step = -step
            target_now = self._state.brightness + step
            if (step > 0 and target_now > target) or (step < 0 and target_now < target):
                target_now = target
        else:
            target_now = target

        if self._use_brightnessctl(target_now):
            self._brightness_backend = self._brightnessctl_backend_name()
            self._logger.debug("Brightness set via brightnessctl: %s", target)
        elif self._write_backlight_file(target_now):
            self._brightness_backend = f"backlight:{self._backlight_path}"
            self._logger.debug("Brightness set via backlight file: %s", target)
        else:
            self._brightness_backend = "unavailable"
            if not self._warned_brightness:
                self._logger.warning(
                    "No brightness control method available. "
                    "Set BRIGHTNESSCTL_BIN or BACKLIGHT_PATH."
                )
                self._warned_brightness = True
            return

        self._state.brightness = target_now
        self._state.brightness_target = target

    def set_screensaver(self, active: bool) -> None:
        now = time.monotonic()
        refresh_due = (
            active
            and self._state.screensaver_active == active
            and now - self._state.screensaver_applied_at >= DISPLAY_STATE_REFRESH_SEC
        )
        if self._state.screensaver_active == active and not refresh_due:
            return

        commands: list[list[str]]
        if active:
            if refresh_due:
                self._logger.debug("Refreshing idle screensaver")
            else:
                self._logger.info("Activating idle screensaver")
            commands = [
                ["xset", "s", "blank"],
                ["xset", "s", "activate"],
            ]
        else:
            self._logger.info("Deactivating idle screensaver")
            commands = [
                ["xset", "s", "reset"],
                ["xset", "s", "off"],
                ["xset", "-dpms"],
                ["xset", "s", "noblank"],
            ]

        success = True
        for command in commands:
            success = self._run_display_cmd(command) and success
        if success:
            self._state.screensaver_active = active
            self._state.screensaver_applied_at = now

    def set_gamma_profile(self, profile: str) -> None:
        if not self._config.enable_blue_light_reduction:
            profile = self._config.gamma_day
        now = time.monotonic()
        if (
            self._state.gamma_profile == profile
            and now - self._state.gamma_applied_at < DISPLAY_STATE_REFRESH_SEC
        ):
            return

        if self._apply_gamma_profile(profile):
            self._state.gamma_profile = profile
            self._state.gamma_applied_at = now
            return

        fallback = self._config.gamma_day
        if profile != fallback and self._apply_gamma_profile(fallback):
            self._logger.warning(
                "Fell back to gamma profile %s after %s failed",
                fallback,
                profile,
            )
            self._state.gamma_profile = fallback
            self._state.gamma_applied_at = now

    def _apply_gamma_profile(self, profile: str) -> bool:
        return self._run_display_cmd(
            [
                "xrandr",
                "--output",
                self._config.xrandr_output,
                "--gamma",
                profile,
            ]
        )

    def _use_brightnessctl(self, target: int) -> bool:
        if not self._brightnessctl_bin or not os.path.exists(self._brightnessctl_bin):
            return False

        actual_target = self._brightnessctl_target(target)
        args = self._brightnessctl_args("set", str(actual_target))
        if self._run(args):
            self._verify_brightnessctl_target(actual_target)
            return True

        self._brightnessctl_max_loaded = False
        self._brightnessctl_max = None
        actual_target = self._brightnessctl_target(target)
        retry_args = self._brightnessctl_args("set", str(actual_target))
        if retry_args != args and self._run(retry_args):
            self._verify_brightnessctl_target(actual_target)
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

        configured_device = self._brightnessctl_device
        for candidate in self._brightnessctl_device_candidates():
            max_value = self._read_brightnessctl_max(candidate)
            if max_value is None:
                continue
            self._brightnessctl_device = candidate
            self._brightnessctl_max = max_value
            if candidate != configured_device:
                self._logger.info(
                    "Using brightnessctl device %s instead of configured %s",
                    candidate or "<default>",
                    configured_device or "<default>",
                )
            return max_value
        return None

    def _brightnessctl_args(self, *args: str) -> list[str]:
        command = [self._brightnessctl_bin]
        if self._brightnessctl_device:
            command.append(f"--device={self._brightnessctl_device}")
        command.extend(args)
        return command

    def _brightnessctl_backend_name(self) -> str:
        device = self._brightnessctl_device or "default"
        return f"brightnessctl:{device}"

    def _brightnessctl_device_candidates(self) -> list[Optional[str]]:
        candidates: list[Optional[str]] = []

        if self._brightnessctl_device:
            candidates.append(self._brightnessctl_device)

        backlight_root = Path("/sys/class/backlight")
        try:
            backlight_devices = sorted(
                path.name for path in backlight_root.iterdir() if path.is_dir()
            )
        except OSError:
            backlight_devices = []

        for device in backlight_devices:
            if device not in candidates:
                candidates.append(device)

        if None not in candidates:
            candidates.append(None)
        return candidates

    def _read_brightnessctl_max(self, device: Optional[str]) -> Optional[int]:
        args = [self._brightnessctl_bin]
        if device:
            args.append(f"--device={device}")
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
        return max_value

    def _read_brightnessctl_current(self) -> Optional[int]:
        args = self._brightnessctl_args("get")
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
            return int(result.stdout.strip())
        except ValueError:
            return None

    def _verify_brightnessctl_target(self, target: int) -> None:
        actual = self._read_brightnessctl_current()
        if actual is None or abs(actual - target) <= 1:
            return
        now = time.monotonic()
        if (
            self._last_brightness_verify_warning is not None
            and now - self._last_brightness_verify_warning < 60
        ):
            return
        self._last_brightness_verify_warning = now
        self._logger.warning(
            "Brightness verification mismatch: requested=%s actual=%s backend=%s",
            target,
            actual,
            self._brightnessctl_backend_name(),
        )

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
            actual = Path(self._backlight_path).read_text(encoding="ascii").strip()
            if actual != str(target):
                self._logger.warning(
                    "Backlight verification mismatch: requested=%s actual=%s",
                    target,
                    actual,
                )
            return True
        except OSError as exc:  # pragma: no cover - hardware specific
            self._logger.warning("Failed writing %s: %s", self._backlight_path, exc)
            return False

    def _run_display_cmd(self, args: list[str]) -> bool:
        env = self._display_env()
        if not self._run(args, env=env):
            self._logger.warning("Failed to run display command: %s", args)
            return False
        return True

    def _display_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.setdefault("DISPLAY", os.getenv("DISPLAY", ":0"))
        xauthority = os.getenv("XAUTHORITY")
        if xauthority:
            env["XAUTHORITY"] = xauthority
        return env

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
