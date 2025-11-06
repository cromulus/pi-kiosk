#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must run with sudo/root privileges." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/pi-kiosk"
CONFIG_DIR="/etc/pi-kiosk"
KIOSK_USER="kiosk"
SYSTEMD_DIR="/etc/systemd/system"

APT_PACKAGES=(
  python3
  python3-venv
  python3-pip
  python3-dev
  git
  i2c-tools
  chromium-browser
  xserver-xorg
  x11-xserver-utils
  xinit
  matchbox-window-manager
  unclutter
  brightnessctl
)

echo "Installing APT dependencies..."
apt-get update
apt-get install -y "${APT_PACKAGES[@]}"

if command -v raspi-config >/dev/null 2>&1; then
  echo "Enabling I²C bus..."
  raspi-config nonint do_i2c 0 || true
fi

if ! id -u "${KIOSK_USER}" >/dev/null 2>&1; then
  echo "Creating user ${KIOSK_USER}..."
  useradd -m -s /bin/bash "${KIOSK_USER}"
fi

echo "Configuring tty1 autologin for ${KIOSK_USER}..."
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat <<EOF >/etc/systemd/system/getty@tty1.service.d/autologin.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF

install -d -o "${KIOSK_USER}" -g "${KIOSK_USER}" "${APP_DIR}"
install -m 644 "${REPO_ROOT}/src/kiosk_sensors.py" "${APP_DIR}/kiosk_sensors.py"

echo "Setting up Python virtual environment..."
if [[ ! -d "${APP_DIR}/venv" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${REPO_ROOT}/requirements.txt"

echo "Deploying launcher scripts..."
install -m 755 "${REPO_ROOT}/src/launch_kiosk.sh" /usr/local/bin/launch_kiosk.sh
install -m 755 "${REPO_ROOT}/src/kiosk_xsession.sh" /usr/local/bin/kiosk_xsession.sh

echo "Preparing configuration..."
install -d "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_DIR}/kiosk.env" ]]; then
  cp "${REPO_ROOT}/config/kiosk.env.sample" "${CONFIG_DIR}/kiosk.env"
  chown "${KIOSK_USER}:${KIOSK_USER}" "${CONFIG_DIR}/kiosk.env"
  echo "Template config copied to ${CONFIG_DIR}/kiosk.env. Update it before rebooting."
fi

echo "Installing systemd services..."
install -m 644 "${REPO_ROOT}/services/kiosk-browser.service" \
  "${SYSTEMD_DIR}/kiosk-browser@.service"
install -m 644 "${REPO_ROOT}/services/kiosk-sensors.service" \
  "${SYSTEMD_DIR}/kiosk-sensors.service"

systemctl daemon-reload
systemctl enable --now "kiosk-browser@${KIOSK_USER}.service"
systemctl enable --now kiosk-sensors.service

echo "Setup complete. Review ${CONFIG_DIR}/kiosk.env and reboot when ready."
