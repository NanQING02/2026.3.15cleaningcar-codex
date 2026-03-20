#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8000}"
SYSTEMD_SERVICE_NAME="cleaningcar-web.service"
UNIT_TARGET="/etc/systemd/system/$SYSTEMD_SERVICE_NAME"
TEMPLATE_PATH="$SCRIPT_DIR/systemd/cleaningcar-web.service"
RUN_USER="${RUN_USER:-${SUDO_USER:-$(id -un)}}"
RUN_GROUP="${RUN_GROUP:-$(id -gn "$RUN_USER")}"

resolve_config_path() {
  local config_override="${CONFIG_PATH:-}"
  local default_config="$SCRIPT_DIR/configs/config.json"
  local legacy_config="$SCRIPT_DIR/config.json"
  if [ -n "$config_override" ]; then
    if [ -f "$config_override" ]; then
      printf '%s\n' "$config_override"
      return 0
    fi
    echo "config file not found: $config_override" >&2
    return 1
  fi
  if [ -f "$default_config" ]; then
    printf '%s\n' "$default_config"
    return 0
  fi
  if [ -f "$legacy_config" ]; then
    printf '%s\n' "$legacy_config"
    return 0
  fi
  echo "config file not found: $default_config" >&2
  return 1
}

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

if [ ! -x "$SCRIPT_DIR/venv-gst/bin/python" ]; then
  echo "[systemd] missing runtime venv, please run ./install_runtime_venv.sh first"
  exit 1
fi

if [ ! -f "$TEMPLATE_PATH" ]; then
  echo "[systemd] template not found: $TEMPLATE_PATH"
  exit 1
fi

CONFIG_PATH="$(resolve_config_path)"
SUDO_PREFIX="$(ensure_sudo_prefix)"
TMP_FILE="$(mktemp)"
cleanup_tmp() {
  rm -f "$TMP_FILE"
}
trap cleanup_tmp EXIT

echo "[systemd] stop manual web process before enabling service"
"$SCRIPT_DIR/start_web_server.sh" stop || true

sed \
  -e "s|__PROJECT_DIR__|$SCRIPT_DIR|g" \
  -e "s|__WEB_HOST__|$WEB_HOST|g" \
  -e "s|__WEB_PORT__|$WEB_PORT|g" \
  -e "s|__CONFIG_PATH__|$CONFIG_PATH|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  -e "s|__RUN_GROUP__|$RUN_GROUP|g" \
  "$TEMPLATE_PATH" > "$TMP_FILE"

if [ -n "$SUDO_PREFIX" ]; then
  "$SUDO_PREFIX" install -m 0644 "$TMP_FILE" "$UNIT_TARGET"
  "$SUDO_PREFIX" systemctl daemon-reload
  "$SUDO_PREFIX" systemctl enable --now "$SYSTEMD_SERVICE_NAME"
  "$SUDO_PREFIX" systemctl status "$SYSTEMD_SERVICE_NAME" --no-pager || true
else
  install -m 0644 "$TMP_FILE" "$UNIT_TARGET"
  systemctl daemon-reload
  systemctl enable --now "$SYSTEMD_SERVICE_NAME"
  systemctl status "$SYSTEMD_SERVICE_NAME" --no-pager || true
fi

echo "[systemd] installed and enabled: $SYSTEMD_SERVICE_NAME"
