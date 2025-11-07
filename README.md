# Pi Home Assistant Kiosk

Turn a Raspberry Pi with a connected display into a hands-off Home Assistant kiosk
that automatically wakes when somebody approaches, dims to match ambient light,
and blanks itself to avoid burn-in. A single `git pull` followed by the install
script re-applies everything, so the Pi can stay locked down and reproducible.

## What you get
- Chromium launches in kiosk mode on boot and loads your HA dashboard using a
  dedicated long-lived token.
- Sensor daemon drives screen wake/sleep plus brightness (Adafruit VL53L4CX +
  VEML7700). The new modular controller keeps track of sensor health, retries with
  backoff, and falls back to safe defaults when hardware is missing.
- Systemd units, autologin, and scripts that can be redeployed idempotently via
  `sudo ./scripts/install.sh`.
- Typed configuration backed by Pydantic makes it easy to tweak thresholds while
  catching invalid values early. Switch sensors on/off individually and opt into
  JSON logs for ingestion.
- Optional x11vnc service mirrors the kiosk display so you can check in remotely.

## Hardware quick reference
- **I²C wiring** (Pi GPIO header pins)
  - 3V3 → VIN on both sensors
  - GND → GND
  - SDA → GPIO2 (pin 3)
  - SCL → GPIO3 (pin 5)
- Keep the VL53L4CX at the bottom of the display, angled forward 30–45° so it
  sees people at ~1–3 m. Point the VEML7700 away from the panel so it measures
  room light, not the backlight.
- Enable I²C in firmware (`raspi-config nonint do_i2c 0`) if you have not
  already.

## One-time + repeatable install
Run everything on the Raspberry Pi itself:

```bash
git clone https://github.com/YOURNAME/pi-kiosk.git
cd pi-kiosk
git pull            # future updates are just pull + rerun
sudo ./scripts/install.sh
```

What the script does:
1. Installs all OS dependencies (Chromium, Xorg, matchbox, Python libs, etc.). It
   automatically picks the correct Chromium package name (`chromium` vs
   `chromium-browser`) for your distro.
2. Enables I²C and creates the `kiosk` user with tty1 autologin.
3. Creates a Python venv in `/opt/pi-kiosk/venv` and installs this package via
   `pip install` (reinstalling on every run so `git pull` updates take effect).
4. Copies the shell helpers into `/usr/local/bin`.
5. Drops a config file in `/etc/pi-kiosk/kiosk.env` (only created if missing).
6. Installs + enables `kiosk-browser@kiosk.service` and `kiosk-sensors.service`.

You can rerun the script any time after a `git pull`; it is idempotent and will
simply refresh binaries, the venv, and systemd units.

## Configure Home Assistant access
1. Create a dedicated HA user (e.g., `kiosk`) with the minimum Lovelace access
   you need.
2. Generate a Long-Lived Access Token for that user.
3. Edit `/etc/pi-kiosk/kiosk.env` (created from `config/kiosk.env.sample`):
   - Set `HA_BASE_URL` to the dashboard URL you want to load.
   - Paste the token into `HA_LONG_LIVED_TOKEN`.
   - Optional: add `HA_EXTRA_QUERY="kiosk=true"` if you use the kiosk-mode
     frontend plugin.
   - Adjust the typed knobs (`DISTANCE_THRESHOLD_MM`, `BRIGHTNESS_MIN/MAX`,
     `POLL_INTERVAL_SEC`, etc.). The controller validates these at startup so
     misconfigurations fail fast.
   - Toggle structured logging with `LOG_JSON=true` if you want to ship events to
     something like Loki/Elasticsearch.
   - Disable missing hardware by setting `ENABLE_DISTANCE_SENSOR=false` or
     `ENABLE_LIGHT_SENSOR=false`.
   - Flip `ENABLE_VNC=true` and optionally set `VNC_PASSWORD_FILE` (created via
     `sudo -u kiosk x11vnc -storepasswd /etc/pi-kiosk/x11vnc.pass`) if you want
     remote viewing.
   - Chromium is auto-detected (`chromium-browser`, `chromium`, or snap). Override
     with `CHROMIUM_BIN=/custom/path` if you use a non-standard build.
   - You normally do not need to touch group membership or Xorg permissions—the
     install script already adds the `kiosk` user to `video,input` and configures
     `/etc/X11/Xwrapper.config` with `allowed_users=anybody`.
4. Customize any sensor thresholds or brightness bounds while you are there.
5. Reboot (or `sudo systemctl restart kiosk-browser@kiosk kiosk-sensors`).

## Service overview
- `kiosk-browser@kiosk.service`  
  Launches Xorg + Chromium on VT7 in full kiosk mode. Controlled by
  `/usr/local/bin/launch_kiosk.sh` and `kiosk_xsession.sh`.
- `kiosk-sensors.service`  
  Runs the `kiosk-sensors` console script from the venv, which:
  - Wakes the display when someone enters `DISTANCE_THRESHOLD_MM`.
  - Blanks after `INACTIVITY_TIMEOUT_SEC` (only if the ToF sensor is working).
  - Scales brightness based on ambient lux, falling back to
    `DEFAULT_BRIGHTNESS` if the light sensor is missing.
  - Reports sensor health every minute and emits DEBUG logs per reading when
    enabled.
- `kiosk-vnc.service`  
  (Optional) Runs x11vnc bound to the kiosk X session. Enable it by setting
  `ENABLE_VNC=true` in the config and rerunning the installer.

Both services default to the `kiosk` user and restart automatically if they
crash. Journald captures logs (`journalctl -u kiosk-sensors.service`).

## Resilience notes
- Missing sensors: the Python daemon keeps running, uses the fallback brightness,
  and never blanks the screen based on motion (since it has none). You can also
  flip `ENABLE_DISTANCE_SENSOR/LIGHT` to false to suppress repeated retries.
- Remote viewing: VNC is disabled by default. When enabled, it respects the port
  and password file you configure; leave the password blank only on trusted
  networks.
- Missing brightness utility: you can point `BACKLIGHT_PATH` to
  `/sys/class/backlight/.../brightness` if `brightnessctl` is not viable.
- Chromium token safety: the URL is constructed runtime so the token never lives
  in plaintext files under your home directory; it only exists in
  `/etc/pi-kiosk/kiosk.env` (root-owned). Restrict that file accordingly.

## Development flow
- Modify files locally → `git commit` → `git push`.
- On the Pi: `cd /path/to/pi-kiosk && git pull && sudo ./scripts/install.sh`.
- Restart only the bits you touched:
  - Browser: `sudo systemctl restart kiosk-browser@kiosk`.
  - Sensors: `sudo systemctl restart kiosk-sensors`.

Feel free to extend the Python daemon (e.g., publish metrics to MQTT) or the
install script (e.g., add custom fonts/themes). The install process is the only
thing you need to rerun after changes, so the kiosk stays in sync with source.
