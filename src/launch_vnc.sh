#!/usr/bin/env bash
set -euo pipefail

BIN="${VNC_BIN:-/usr/bin/x11vnc}"
if [[ ! -x "${BIN}" ]]; then
  echo "x11vnc is not installed (expected at ${BIN})" >&2
  exit 1
fi

: "${DISPLAY:=:0}"
PORT="${VNC_PORT:-5900}"
PASSWORD_FILE="${VNC_PASSWORD_FILE:-}"
EXTRA_ARGS="${VNC_EXTRA_ARGS:-}"

ARGS=(
  "${BIN}"
  -display "${DISPLAY}"
  -rfbport "${PORT}"
  -forever
  -noxdamage
  -repeat
)

if [[ -n "${PASSWORD_FILE}" ]]; then
  if [[ -f "${PASSWORD_FILE}" ]]; then
    ARGS+=(-rfbauth "${PASSWORD_FILE}")
  else
    echo "Warning: VNC password file ${PASSWORD_FILE} not found; starting without authentication" >&2
  fi
fi

if [[ -n "${EXTRA_ARGS}" ]]; then
  # Split on whitespace similar to shell invocation.
  read -r -a extra_array <<< "${EXTRA_ARGS}"
  ARGS+=("${extra_array[@]}")
fi

echo "Starting x11vnc on ${DISPLAY} port ${PORT}"
exec "${ARGS[@]}"
