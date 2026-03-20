#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv-gst"
SYSTEMD_SERVICE_NAME="cleaningcar-web.service"
UNIT_TARGET="/etc/systemd/system/$SYSTEMD_SERVICE_NAME"

ensure_sudo_prefix() {
  if [ "$(id -u)" -eq 0 ]; then
    printf '%s\n' ""
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    printf '%s\n' "sudo"
    return 0
  fi
  echo "sudo not found and current user is not root" >&2
  exit 1
}

echo "[uninstall] stop manual web service if running"
"$SCRIPT_DIR/start_web_server.sh" stop || true

if [ -f "$UNIT_TARGET" ]; then
  echo "[uninstall] remove systemd service: $SYSTEMD_SERVICE_NAME"
  SUDO_PREFIX="$(ensure_sudo_prefix)"
  if [ -n "$SUDO_PREFIX" ]; then
    "$SUDO_PREFIX" systemctl disable --now "$SYSTEMD_SERVICE_NAME" 2>/dev/null || true
    "$SUDO_PREFIX" rm -f "$UNIT_TARGET"
    "$SUDO_PREFIX" systemctl daemon-reload
    "$SUDO_PREFIX" systemctl reset-failed "$SYSTEMD_SERVICE_NAME" 2>/dev/null || true
  else
    systemctl disable --now "$SYSTEMD_SERVICE_NAME" 2>/dev/null || true
    rm -f "$UNIT_TARGET"
    systemctl daemon-reload
    systemctl reset-failed "$SYSTEMD_SERVICE_NAME" 2>/dev/null || true
  fi
fi

if [ -d "$VENV_DIR" ]; then
  echo "[uninstall] remove runtime venv: $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

rm -f "$SCRIPT_DIR"/web_server_*.pid

echo "[uninstall] runtime environment removed"
