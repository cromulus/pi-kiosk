#!/usr/bin/env bash
set -euo pipefail

if ! command -v startx >/dev/null 2>&1; then
  echo "startx is required but missing" >&2
  exit 1
fi

KIOSK_XSESSION="${KIOSK_XSESSION:-/usr/local/bin/kiosk_xsession.sh}"
if [[ ! -x "${KIOSK_XSESSION}" ]]; then
  echo "Expected executable at ${KIOSK_XSESSION}" >&2
  exit 1
fi

: "${HA_BASE_URL:?Set HA_BASE_URL in /etc/pi-kiosk/kiosk.env}"

HA_EXTRA_QUERY="${HA_EXTRA_QUERY:-}"
HA_LONG_LIVED_TOKEN="${HA_LONG_LIVED_TOKEN:-}"
KIOSK_VT="${KIOSK_VT:-7}"

urlencode() {
  local string="${1}"
  local length="${#string}"
  local i char
  for ((i = 0; i < length; i++)); do
    char="${string:i:1}"
    case "${char}" in
      [a-zA-Z0-9.~_-]) printf '%s' "${char}" ;;
      *) printf '%%%02X' "'${char}" ;;
    esac
  done
}

append_query() {
  local base="${1}"
  local addition="${2}"
  if [[ -z "${addition}" ]]; then
    printf '%s' "${base}"
    return
  fi
  local sep="?"
  [[ "${base}" == *"?"* ]] && sep="&"
  printf '%s%s%s' "${base}" "${sep}" "${addition}"
}

build_url() {
  local url="${HA_BASE_URL}"
  if [[ -n "${HA_LONG_LIVED_TOKEN}" ]]; then
    url="$(append_query "${url}" "authToken=$(urlencode "${HA_LONG_LIVED_TOKEN}")")"
  fi
  if [[ -n "${HA_EXTRA_QUERY}" ]]; then
    url="$(append_query "${url}" "${HA_EXTRA_QUERY}")"
  fi
  printf '%s' "${url}"
}

FINAL_URL="$(build_url)"
export KIOSK_URL="${FINAL_URL}"

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}" || true

if [[ -n "${XAUTHORITY:-}" && ! -f "${XAUTHORITY}" ]]; then
  touch "${XAUTHORITY}"
  chmod 600 "${XAUTHORITY}" || true
fi

echo "Starting kiosk on ${DISPLAY} (URL: ${KIOSK_URL})"
exec startx "${KIOSK_XSESSION}" -- "${DISPLAY}" "vt${KIOSK_VT}" -nolisten tcp
