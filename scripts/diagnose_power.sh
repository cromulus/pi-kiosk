#!/usr/bin/env bash
# Read-only evidence capture. Run on pidisplay, preferably with sudo for logs.
set -uo pipefail

section() { printf '\n## %s\n' "$1"; }
check() {
  if command -v "$1" >/dev/null 2>&1; then
    "$@" || printf '[Check failed or data unavailable]\n'
  else
    printf '[Unavailable command: %s]\n' "$1"
  fi
}

KIOSK_ACCOUNT="${1:-kiosk}"
if [[ $# -eq 0 && -r /etc/pi-kiosk/kiosk.env ]]; then
  # Parse only the account name; do not execute or print the credential file.
  KIOSK_ACCOUNT="$(python3 - <<'PY'
import shlex
from pathlib import Path
user = "kiosk"
for line in Path('/etc/pi-kiosk/kiosk.env').read_text().splitlines():
    key, sep, value = line.partition('=')
    if sep and key.strip() == 'KIOSK_USER':
        tokens = shlex.split(value, comments=True)
        user = tokens[0] if tokens else 'kiosk'
print(user)
PY
  )"
fi

section 'Host and uptime'
check hostname
check date --iso-8601=seconds
check uptime
if [[ -r /proc/device-tree/model ]]; then
  tr -d '\0' </proc/device-tree/model
  printf '\n'
fi
check last -x -n 12

section 'Power and temperature since this boot'
check vcgencmd get_throttled
check vcgencmd measure_temp
section 'EEPROM configuration (read-only)'
check rpi-eeprom-config

section 'Kiosk service startup and restart state'
for name in browser sensors vnc; do
  check systemctl show "kiosk-${name}@${KIOSK_ACCOUNT}.service" \
    -p UnitFileState -p ActiveState -p SubState -p Result -p NRestarts \
    -p ExecMainStatus -p ActiveEnterTimestamp -p Restart
done
section 'Failed units and scheduled timers'
check systemctl --failed --no-pager
check systemctl list-timers --all --no-pager
section 'System watchdog configuration'
check systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec

for boot in 0 -1; do
  section "Kernel power/crash/storage messages, boot ${boot}"
  check journalctl -k -b "$boot" --no-pager -n 80 \
    -g 'voltage|throttl|thermal|watchdog|panic|oom|Out of memory|I/O error|EXT4-fs error'
  section "Power button and shutdown messages, boot ${boot}"
  check journalctl -b "$boot" --no-pager -n 50 \
    -u systemd-logind -u systemd-poweroff -u systemd-reboot
done

section 'Display state (screen off does not mean host off)'
if command -v getent >/dev/null 2>&1; then
  KIOSK_HOME="$(getent passwd "$KIOSK_ACCOUNT" | cut -d: -f6)"
  if [[ -n "$KIOSK_HOME" ]]; then
    export DISPLAY=:0 XAUTHORITY="${KIOSK_HOME}/.Xauthority"
    check xset q
    check xrandr --current
  fi
fi
printf '\nNo settings were changed. Missing previous-boot logs do not prove a clean shutdown.\n'
