#!/usr/bin/env bash
set -euo pipefail

: "${KIOSK_URL:=}"
if [[ -z "${KIOSK_URL}" ]]; then
  echo "KIOSK_URL is unset. Did launch_kiosk.sh run first?" >&2
  exit 1
fi

export DISPLAY="${DISPLAY:-:0}"

CHROMIUM_COLOR_SCHEME="${CHROMIUM_COLOR_SCHEME:-auto}"
PI_KIOSK_CONFIG="${PI_KIOSK_CONFIG:-/etc/pi-kiosk/kiosk.env}"
PI_KIOSK_PYTHON="${PI_KIOSK_PYTHON:-/opt/pi-kiosk/venv/bin/python3}"

if [[ -n "${CHROMIUM_BIN:-}" ]]; then
  CANDIDATES=("${CHROMIUM_BIN}")
else
  CANDIDATES=(
    /usr/bin/chromium-browser
    /usr/bin/chromium
    /snap/bin/chromium
  )
fi

CHROMIUM_BIN=""
for candidate in "${CANDIDATES[@]}"; do
  if [[ -x "${candidate}" ]]; then
    CHROMIUM_BIN="${candidate}"
    break
  fi
done

if [[ -z "${CHROMIUM_BIN}" ]]; then
  echo "Chromium executable not found. Checked: ${CANDIDATES[*]}" >&2
  exit 1
fi

DEFAULT_FLAGS=(
  --kiosk
  --disable-infobars
  --noerrdialogs
  --disable-session-crashed-bubble
  --check-for-update-interval=604800
  --disable-pinch
  --disable-translate
  --overscroll-history-navigation=0
  --disable-features=TranslateUI
)

EXTRA_FLAGS=()
if [[ -n "${CHROMIUM_FLAGS:-}" ]]; then
  # shellcheck disable=SC2206 # intentional word-splitting of user-provided flags
  EXTRA_FLAGS=(${CHROMIUM_FLAGS})
fi

is_dark_mode_now() {
  if [[ -x "${PI_KIOSK_PYTHON}" ]]; then
    "${PI_KIOSK_PYTHON}" -m pi_kiosk.daylight is-dark --config "${PI_KIOSK_CONFIG}"
    return
  fi
  return 1
}

seconds_until_next_theme_transition() {
  if [[ -x "${PI_KIOSK_PYTHON}" ]]; then
    "${PI_KIOSK_PYTHON}" -m pi_kiosk.daylight seconds-until-transition --config "${PI_KIOSK_CONFIG}" \
      || printf '3600'
    return
  fi
  printf '3600'
}

schedule_theme_restart() {
  local parent_pid parent_start_time sleep_seconds
  parent_pid="$$"
  parent_start_time="$(awk '{print $22}' "/proc/${parent_pid}/stat" 2>/dev/null || true)"
  sleep_seconds="$(seconds_until_next_theme_transition)"

  (
    sleep "${sleep_seconds}"
    if [[ -n "${parent_start_time}" ]] && [[ -r "/proc/${parent_pid}/stat" ]]; then
      if [[ "$(awk '{print $22}' "/proc/${parent_pid}/stat" 2>/dev/null || true)" == "${parent_start_time}" ]]; then
        kill -TERM "${parent_pid}" 2>/dev/null || true
      fi
    fi
  ) &
}

case "${CHROMIUM_COLOR_SCHEME,,}" in
  auto|light)
    ;;
  dark)
    EXTRA_FLAGS+=(--force-dark-mode)
    ;;
  schedule)
    if is_dark_mode_now; then
      EXTRA_FLAGS+=(--force-dark-mode)
    fi
    schedule_theme_restart
    ;;
  *)
    echo "Invalid CHROMIUM_COLOR_SCHEME=${CHROMIUM_COLOR_SCHEME}; expected auto, light, dark, or schedule." >&2
    exit 1
    ;;
esac

# Disable blanking + DPMS inside the X session to prevent screen burn-in.
xset s off || true
xset -dpms || true
xset s noblank || true

if command -v matchbox-window-manager >/dev/null 2>&1; then
  matchbox-window-manager -use_titlebar no &
fi

if command -v unclutter >/dev/null 2>&1; then
  # Hide the pointer without grabbing it so touch input still works.
  unclutter -idle 0 -root -reset &
fi

exec "${CHROMIUM_BIN}" "${DEFAULT_FLAGS[@]}" "${EXTRA_FLAGS[@]}" "${KIOSK_URL}"
