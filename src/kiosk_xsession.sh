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
  local initial_theme="$1"
  local parent_pid parent_start_time poll_seconds next_transition
  parent_pid="$$"
  parent_start_time="$(awk '{print $22}' "/proc/${parent_pid}/stat" 2>/dev/null || true)"
  poll_seconds="${CHROMIUM_THEME_POLL_SEC:-30}"
  if [[ ! "${poll_seconds}" =~ ^[0-9]+$ ]] || [[ "${poll_seconds}" -lt 5 ]]; then
    poll_seconds=30
  fi
  next_transition="$(seconds_until_next_theme_transition)"
  if [[ ! "${next_transition}" =~ ^[0-9]+$ ]]; then
    next_transition=unknown
  fi
  echo "Monitoring Chromium theme: current=${initial_theme}, next_transition=${next_transition}s, poll=${poll_seconds}s"

  (
    while true; do
      sleep "${poll_seconds}"
      if [[ -z "${parent_start_time}" || ! -r "/proc/${parent_pid}/stat" ]]; then
        break
      fi
      if [[ "$(awk '{print $22}' "/proc/${parent_pid}/stat" 2>/dev/null || true)" != "${parent_start_time}" ]]; then
        break
      fi

      if is_dark_mode_now; then
        current_theme="dark"
      else
        current_theme="light"
      fi
      if [[ "${current_theme}" != "${initial_theme}" ]]; then
        echo "Chromium theme changed ${initial_theme}->${current_theme}; restarting browser"
        kill -TERM "${parent_pid}" 2>/dev/null || true
        break
      fi
    done
  ) &
}

case "${CHROMIUM_COLOR_SCHEME,,}" in
  auto|light)
    echo "Chromium theme mode: light"
    ;;
  dark)
    echo "Chromium theme mode: dark"
    EXTRA_FLAGS+=(--force-dark-mode)
    ;;
  schedule)
    if is_dark_mode_now; then
      echo "Chromium theme mode: scheduled dark"
      EXTRA_FLAGS+=(--force-dark-mode)
      CURRENT_THEME="dark"
    else
      echo "Chromium theme mode: scheduled light"
      CURRENT_THEME="light"
    fi
    schedule_theme_restart "${CURRENT_THEME}"
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
