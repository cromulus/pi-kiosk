#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "This script must run with sudo/root privileges." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/pi-kiosk"
CONFIG_DIR="/etc/pi-kiosk"
CONFIG_FILE="${CONFIG_DIR}/kiosk.env"
KIOSK_USER_DEFAULT="kiosk"
KIOSK_USER="${KIOSK_USER_DEFAULT}"
SYSTEMD_DIR="/etc/systemd/system"
ASSUME_DEFAULTS="${PI_KIOSK_ASSUME_DEFAULTS:-0}"
RESET=0
UNINSTALL=0
VNC_PASSWORD_VALUE=""
INSTALLED=0

APT_PACKAGES=(
  python3
  python3-venv
  python3-pip
  python3-dev
  git
  i2c-tools
  xserver-xorg
  x11-xserver-utils
  xinit
  matchbox-window-manager
  unclutter
  brightnessctl
  x11vnc
)

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install.sh [options]

Options:
  --assume-defaults        Skip prompts and reuse or accept default values.
  --reset                  Completely remove existing installation (including config)
                           and then run a fresh install.
  --uninstall              Remove services, files, and optional config, then exit.
  -h, --help               Show this help text.

Environment:
  PI_KIOSK_ASSUME_DEFAULTS=1   Equivalent to --assume-defaults.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --assume-defaults) ASSUME_DEFAULTS=1 ;;
    --reset) RESET=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ "${UNINSTALL}" == "1" && "${RESET}" == "1" ]]; then
  echo "Choose either --reset or --uninstall, not both." >&2
  exit 1
fi

if [[ -d "${APP_DIR}" ]] || [[ -f "${SYSTEMD_DIR}/kiosk-browser@.service" ]]; then
  INSTALLED=1
fi

if [[ "${INSTALLED}" == "1" && "${RESET}" == "0" && "${UNINSTALL}" == "0" ]]; then
  if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
    RESET=0
    UNINSTALL=0
  else
    echo "Existing Pi Kiosk installation detected."
    while true; do
      read -r -p "Choose action: [U]pdate/[R]eset/[X] Uninstall (default U): " action
      case "${action,,}" in
        ""|u|update) RESET=0; UNINSTALL=0; break ;;
        r|reset) RESET=1; UNINSTALL=0; break ;;
        x|uninstall) UNINSTALL=1; RESET=0; break ;;
        *) echo "Please enter U, R, or X." ;;
      esac
    done
  fi
fi

log_step() {
  printf '\n==> %s\n' "$1"
}

prompt_string() {
  local label="$1" default="$2" value
  if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
    printf '%s' "${default}"
    return
  fi
  read -r -p "${label} [${default}]: " value
  if [[ -z "${value}" ]]; then
    printf '%s' "${default}"
  else
    printf '%s' "${value}"
  fi
}

prompt_bool() {
  local label="$1" default="$2" prompt value
  if [[ "${default}" == "true" ]]; then
    prompt="Y/n"
  else
    prompt="y/N"
  fi
  if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
    printf '%s' "${default}"
    return
  fi
  while true; do
    read -r -p "${label} [${prompt}]: " value
    if [[ -z "${value}" ]]; then
      printf '%s' "${default}"
      return
    fi
    case "${value,,}" in
      y|yes) printf 'true'; return ;;
      n|no) printf 'false'; return ;;
    esac
    echo "Please answer yes or no." >&2
  done
}

prompt_number() {
  local label="$1" default="$2" value
  if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
    printf '%s' "${default}"
    return
  fi
  while true; do
    read -r -p "${label} [${default}]: " value
    value="${value:-$default}"
    if [[ "${value}" =~ ^[0-9]+$ ]]; then
      printf '%s' "${value}"
      return
    fi
    echo "Please enter a number." >&2
  done
}

detect_backlight_path() {
  local candidate
  for candidate in /sys/class/backlight/*; do
    [[ -e "${candidate}/brightness" ]] || continue
    echo "${candidate}/brightness"
    return
  done
  echo "/sys/class/backlight/11-0045/brightness"
}

detect_brightnessctl_device() {
  local candidate
  for candidate in /sys/class/backlight/*; do
    [[ -d "${candidate}" ]] || continue
    local name
    name=$(basename "${candidate}")
    echo "backlight/${name}"
    return
  done
  echo "backlight/11-0045"
}

set_env_var() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${CONFIG_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${CONFIG_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >>"${CONFIG_FILE}"
  fi
}

load_existing_config() {
  if [[ -f "${CONFIG_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${CONFIG_FILE}"
    set +a
  fi
}

configure_runtime() {
  log_step "Gathering kiosk preferences"

  local default_user="${KIOSK_USER:-$KIOSK_USER_DEFAULT}"
  local kiosk_user
  kiosk_user=$(prompt_string "Kiosk Linux user" "${default_user}")
  set_env_var KIOSK_USER "\"${kiosk_user}\""
  KIOSK_USER="${kiosk_user}"

  local default_ha_url="${HA_BASE_URL:-https://homeassistant.local:8123/lovelace/kiosk}"
  local ha_url
  ha_url=$(prompt_string "Home Assistant base URL" "${default_ha_url}")
  set_env_var HA_BASE_URL "\"${ha_url}\""

  local default_token="${HA_LONG_LIVED_TOKEN:-}"
  local token
  token=$(prompt_string "HA long-lived token (leave blank to skip)" "${default_token}")
  set_env_var HA_LONG_LIVED_TOKEN "\"${token}\""

  local default_query="${HA_EXTRA_QUERY:-kiosk=true}"
  local query
  query=$(prompt_string "Extra query string (kiosk flags)" "${default_query}")
  set_env_var HA_EXTRA_QUERY "\"${query}\""

  local i2c_present="false"
  [[ -e /dev/i2c-1 ]] && i2c_present="true"
  local dist_default="${ENABLE_DISTANCE_SENSOR:-$i2c_present}"
  [[ -z "${dist_default}" ]] && dist_default="false"
  local light_default="${ENABLE_LIGHT_SENSOR:-$i2c_present}"
  [[ -z "${light_default}" ]] && light_default="false"

  local enable_dist enable_light
  enable_dist=$(prompt_bool "Enable distance sensor (VL53L4CX)" "${dist_default}")
  enable_light=$(prompt_bool "Enable light sensor (VEML7700)" "${light_default}")
  set_env_var ENABLE_DISTANCE_SENSOR "${enable_dist}"
  set_env_var ENABLE_LIGHT_SENSOR "${enable_light}"

  local brightnessctl_present="false"
  if command -v brightnessctl >/dev/null 2>&1; then
    brightnessctl_present="true"
  fi
  local default_use_bctl
  if [[ -n "${BRIGHTNESSCTL_BIN:-}" ]]; then
    default_use_bctl="true"
  elif [[ -n "${BACKLIGHT_PATH:-}" ]]; then
    default_use_bctl="false"
  else
    default_use_bctl="${brightnessctl_present}"
  fi
  [[ -z "${default_use_bctl}" ]] && default_use_bctl="true"

  local use_bctl
  use_bctl=$(prompt_bool "Use brightnessctl for dimming (recommended)" "${default_use_bctl}")
  if [[ "${use_bctl}" == "true" ]]; then
    local device_default
    device_default="${BRIGHTNESSCTL_DEVICE:-$(detect_brightnessctl_device)}"
    set_env_var BRIGHTNESSCTL_BIN "\"/usr/bin/brightnessctl\""
    set_env_var BRIGHTNESSCTL_DEVICE "\"${device_default}\""
    set_env_var BACKLIGHT_PATH "\"\""
  else
    local backlight_default
    backlight_default="${BACKLIGHT_PATH:-$(detect_backlight_path)}"
    local backlight_path
    backlight_path=$(prompt_string "Backlight brightness file" "${backlight_default}")
    set_env_var BACKLIGHT_PATH "\"${backlight_path}\""
    set_env_var BRIGHTNESSCTL_BIN "\"\""
    set_env_var BRIGHTNESSCTL_DEVICE "\"\""
  fi

  local enable_vnc
  local default_vnc="${ENABLE_VNC:-false}"
  enable_vnc=$(prompt_bool "Enable VNC mirror service" "${default_vnc}")
  set_env_var ENABLE_VNC "${enable_vnc}"
  if [[ "${enable_vnc}" == "true" ]]; then
    local vnc_port
    vnc_port=$(prompt_number "VNC TCP port" "${VNC_PORT:-5900}")
    set_env_var VNC_PORT "${vnc_port}"
    local vnc_password_path_default="${VNC_PASSWORD_FILE:-/etc/pi-kiosk/x11vnc.pass}"
    local vnc_password_path
    vnc_password_path=$(prompt_string "VNC password file path" "${vnc_password_path_default}")
    set_env_var VNC_PASSWORD_FILE "\"${vnc_password_path}\""
    VNC_PASSWORD_FILE="${vnc_password_path}"
    if [[ "${ASSUME_DEFAULTS}" == "1" ]]; then
      VNC_PASSWORD_VALUE=""
    else
      local first second
      read -r -s -p "VNC password (leave blank for none): " first
      echo
      if [[ -n "${first}" ]]; then
        read -r -s -p "Confirm password: " second
        echo
        if [[ "${first}" != "${second}" ]]; then
          echo "Passwords do not match; skipping password creation."
          first=""
        fi
      fi
      VNC_PASSWORD_VALUE="${first}"
    fi
    local vnc_args
    vnc_args=$(prompt_string "Extra x11vnc arguments" "${VNC_EXTRA_ARGS:--shared -loop}")
    set_env_var VNC_EXTRA_ARGS "\"${vnc_args}\""
  else
    VNC_PASSWORD_VALUE=""
  fi

  chown "${kiosk_user}:${kiosk_user}" "${CONFIG_FILE}"
}

perform_uninstall() {
  local remove_config="${1:-false}"

  log_step "Stopping kiosk services"
  systemctl disable --now "kiosk-browser@${KIOSK_USER}.service" >/dev/null 2>&1 || true
  systemctl disable --now kiosk-sensors.service >/dev/null 2>&1 || true
  systemctl disable --now kiosk-vnc.service >/dev/null 2>&1 || true

  log_step "Removing deployed files"
  rm -rf "${APP_DIR}"
  rm -f /usr/local/bin/launch_kiosk.sh \
        /usr/local/bin/kiosk_xsession.sh \
        /usr/local/bin/launch_vnc.sh

  rm -f "${SYSTEMD_DIR}/kiosk-browser@.service" \
        "${SYSTEMD_DIR}/kiosk-sensors.service" \
        "${SYSTEMD_DIR}/kiosk-vnc.service"

  rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf 2>/dev/null || true
  systemctl daemon-reload

  if [[ "${remove_config}" == "true" ]]; then
    rm -f "${CONFIG_FILE}"
    rmdir "${CONFIG_DIR}" 2>/dev/null || true
  fi
}

if [[ "${UNINSTALL}" == "1" ]]; then
  load_existing_config
  local_remove_cfg=$(prompt_bool "Remove ${CONFIG_FILE} during uninstall?" "false")
  perform_uninstall "${local_remove_cfg}"
  echo "Pi Kiosk uninstalled."
  exit 0
fi

if [[ "${RESET}" == "1" ]]; then
  load_existing_config
  local_remove_cfg=$(prompt_bool "Remove existing config before reinstall?" "true")
  perform_uninstall "${local_remove_cfg}"
fi

log_step "Preparing configuration file"
install -d "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  cp "${REPO_ROOT}/config/kiosk.env.sample" "${CONFIG_FILE}"
fi

load_existing_config
configure_runtime
load_existing_config

log_step "Installing APT dependencies (this may take a minute)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y "${APT_PACKAGES[@]}" >/tmp/pi-kiosk-apt.log 2>&1 || {
  cat /tmp/pi-kiosk-apt.log
  exit 1
}

log_step "Ensuring Chromium is installed"
CHROMIUM_CANDIDATES=(chromium-browser chromium)
CHROMIUM_INSTALLED=""
for candidate in "${CHROMIUM_CANDIDATES[@]}"; do
  if apt-cache show "${candidate}" >/dev/null 2>&1; then
    if DEBIAN_FRONTEND=noninteractive apt-get install -y "${candidate}" >/tmp/pi-kiosk-chromium.log 2>&1; then
      CHROMIUM_INSTALLED="${candidate}"
      break
    fi
  fi
done
if [[ -z "${CHROMIUM_INSTALLED}" ]]; then
  echo "Failed to install Chromium (checked: ${CHROMIUM_CANDIDATES[*]})." >&2
  [[ -f /tmp/pi-kiosk-chromium.log ]] && cat /tmp/pi-kiosk-chromium.log
  exit 1
fi

if command -v raspi-config >/dev/null 2>&1; then
  log_step "Enabling I²C bus via raspi-config"
  raspi-config nonint do_i2c 0 || true
fi

log_step "Creating/refreshing kiosk user (${KIOSK_USER})"
if ! id -u "${KIOSK_USER}" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "${KIOSK_USER}"
fi
usermod -aG video,input,render "${KIOSK_USER}"

if [[ "${ENABLE_VNC,,}" == "true" && -n "${VNC_PASSWORD_VALUE}" && -n "${VNC_PASSWORD_FILE:-}" ]]; then
  log_step "Storing VNC password"
  install -d "$(dirname "${VNC_PASSWORD_FILE}")"
  chown "${KIOSK_USER}:${KIOSK_USER}" "$(dirname "${VNC_PASSWORD_FILE}")"
  sudo -u "${KIOSK_USER}" x11vnc -storepasswd "${VNC_PASSWORD_VALUE}" "${VNC_PASSWORD_FILE}" >/dev/null 2>&1 || {
    echo "Warning: failed to write VNC password file at ${VNC_PASSWORD_FILE}"
  }
fi

log_step "Configuring tty1 autologin"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat <<EOF >/etc/systemd/system/getty@tty1.service.d/autologin.conf
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${KIOSK_USER} --noclear %I \$TERM
EOF

log_step "Deploying application to ${APP_DIR}"
install -d -o "${KIOSK_USER}" -g "${KIOSK_USER}" "${APP_DIR}"
if [[ ! -d "${APP_DIR}/venv" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${APP_DIR}/venv/bin/pip" install --upgrade pip >/tmp/pi-kiosk-pip.log
"${APP_DIR}/venv/bin/pip" install --upgrade --force-reinstall "${REPO_ROOT}" >>/tmp/pi-kiosk-pip.log

install -m 755 "${REPO_ROOT}/src/launch_kiosk.sh" /usr/local/bin/launch_kiosk.sh
install -m 755 "${REPO_ROOT}/src/kiosk_xsession.sh" /usr/local/bin/kiosk_xsession.sh
install -m 755 "${REPO_ROOT}/src/launch_vnc.sh" /usr/local/bin/launch_vnc.sh

log_step "Installing systemd units"
install -m 644 "${REPO_ROOT}/services/kiosk-browser.service" \
  "${SYSTEMD_DIR}/kiosk-browser@.service"
install -m 644 "${REPO_ROOT}/services/kiosk-sensors.service" \
  "${SYSTEMD_DIR}/kiosk-sensors.service"
install -m 644 "${REPO_ROOT}/services/kiosk-vnc.service" \
  "${SYSTEMD_DIR}/kiosk-vnc.service"

systemctl daemon-reload
systemctl enable --now "kiosk-browser@${KIOSK_USER}.service"
systemctl enable --now kiosk-sensors.service

XWRAPPER="/etc/X11/Xwrapper.config"
cat <<'EOF' >"${XWRAPPER}"
allowed_users=anybody
needs_root_rights=no
EOF

load_existing_config
if [[ "${ENABLE_VNC,,}" == "true" ]]; then
  systemctl enable --now kiosk-vnc.service
else
  systemctl disable --now kiosk-vnc.service >/dev/null 2>&1 || true
fi

printf '\nAll done! You can rerun this script anytime; it will reuse your saved answers.\n'
printf 'Review %s if you need to tweak anything.\n' "${CONFIG_FILE}"
