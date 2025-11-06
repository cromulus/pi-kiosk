#!/usr/bin/env bash
set -euo pipefail

: "${KIOSK_URL:=}"
if [[ -z "${KIOSK_URL}" ]]; then
  echo "KIOSK_URL is unset. Did launch_kiosk.sh run first?" >&2
  exit 1
fi

export DISPLAY="${DISPLAY:-:0}"

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
  --disable-translate
  --overscroll-history-navigation=0
  --disable-features=TranslateUI
)

EXTRA_FLAGS=()
if [[ -n "${CHROMIUM_FLAGS:-}" ]]; then
  # shellcheck disable=SC2206 # intentional word-splitting of user-provided flags
  EXTRA_FLAGS=(${CHROMIUM_FLAGS})
fi

# Disable blanking + DPMS inside the X session to prevent screen burn-in.
xset s off || true
xset -dpms || true
xset s noblank || true

if command -v matchbox-window-manager >/dev/null 2>&1; then
  matchbox-window-manager -use_titlebar no &
fi

if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 1 -root &
fi

exec "${CHROMIUM_BIN}" "${DEFAULT_FLAGS[@]}" "${EXTRA_FLAGS[@]}" "${KIOSK_URL}"
