#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv-gst"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8000}"
SERVICE_NAME="web_server_${WEB_PORT}"
PID_FILE="$SCRIPT_DIR/${SERVICE_NAME}.pid"
LOG_FILE="$SCRIPT_DIR/${SERVICE_NAME}.log"
PACKAGES_DIR="$SCRIPT_DIR/packages"

DEFAULT_CONFIG="$SCRIPT_DIR/configs/config.json"
LEGACY_CONFIG="$SCRIPT_DIR/config.json"
CONFIG_PATH="$DEFAULT_CONFIG"
if [ ! -f "$CONFIG_PATH" ]; then
  if [ -f "$LEGACY_CONFIG" ]; then
    CONFIG_PATH="$LEGACY_CONFIG"
  else
    echo "config file not found: $DEFAULT_CONFIG"
    exit 1
  fi
fi

MONITOR_CPU_LIMIT="${MONITOR_CPU_LIMIT:-600}"
MONITOR_INTERVAL="${MONITOR_INTERVAL:-10}"
FORCE_SETUP="${FORCE_SETUP:-}"

port_owner_pids() {
  local port="$1"
  ss -ltnp 2>/dev/null | awk -v port=":$port" '
    $4 ~ port"$" {
      if (match($0, /pid=[0-9]+/)) {
        pid = substr($0, RSTART + 4, RLENGTH - 4)
        print pid
      }
    }
  ' | sort -u
}

describe_pid() {
  local pid="$1"
  if [ ! -d "/proc/$pid" ]; then
    return 1
  fi
  local cmdline
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  local cwd
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  echo "pid=$pid cwd=$cwd cmd=$cmdline"
}

stop_pid() {
  local pid="$1"
  if [ ! -d "/proc/$pid" ]; then
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    if [ ! -d "/proc/$pid" ]; then
      return 0
    fi
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
}

stop_same_project_port_owners() {
  local port="$1"
  local pid
  while read -r pid; do
    [ -z "$pid" ] && continue
    if [ ! -d "/proc/$pid" ]; then
      continue
    fi
    local cmdline
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    local cwd
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    if [[ "$cmdline" == *"$SCRIPT_DIR"* ]] || [[ "$cwd" == "$SCRIPT_DIR"* ]] || [[ "$cmdline" == *"web.server"* ]]; then
      echo "[service] stop existing same-project process on port $port: $(describe_pid "$pid")"
      stop_pid "$pid"
    fi
  done < <(port_owner_pids "$port")
}

ensure_port_available() {
  local port="$1"
  local owners
  owners="$(port_owner_pids "$port" || true)"
  if [ -z "$owners" ]; then
    return 0
  fi
  stop_same_project_port_owners "$port"
  owners="$(port_owner_pids "$port" || true)"
  if [ -z "$owners" ]; then
    return 0
  fi
  echo "[service] port $port is already in use by:"
  local pid
  while read -r pid; do
    [ -z "$pid" ] && continue
    describe_pid "$pid" || true
  done <<< "$owners"
  return 1
}

wait_for_server_ready() {
  local pid="$1"
  local port="$2"
  for _ in $(seq 1 30); do
    if [ ! -d "/proc/$pid" ]; then
      return 1
    fi
    local owners
    owners="$(port_owner_pids "$port" || true)"
    if echo "$owners" | grep -qx "$pid"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_apt() {
  local desc="$1"
  shift
  if ! "$@"; then
    echo "[setup] apt failed: $desc"
    exit 1
  fi
}

start_cpu_monitor() {
  local pid="$1"
  (
    while ps -p "$pid" >/dev/null 2>&1; do
      local cpu_raw
      cpu_raw="$(ps -p "$pid" -o %cpu= 2>/dev/null | awk '{print int($1)}')"
      if [ -n "$cpu_raw" ] && [ "$cpu_raw" -gt "$MONITOR_CPU_LIMIT" ]; then
        echo "[monitor] pid=$pid cpu=${cpu_raw}% limit=${MONITOR_CPU_LIMIT}%"
        kill "$pid" 2>/dev/null || true
        sleep 5
        if ps -p "$pid" >/dev/null 2>&1; then
          kill -9 "$pid" 2>/dev/null || true
        fi
        echo "[monitor] process killed, check log: $LOG_FILE"
        break
      fi
      sleep "$MONITOR_INTERVAL"
    done
  ) &
}

if [ -x /usr/bin/python3 ]; then
  PYTHON_SYS="/usr/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_SYS="$(command -v python3)"
else
  echo "python3 not found"
  exit 1
fi

PYTHON_VER="$("$PYTHON_SYS" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VER" in
  3.8|3.9|3.10|3.11|3.12) ;;
  *)
    echo "unsupported python version: $PYTHON_VER"
    exit 1
    ;;
esac

PYTHON_BIN=""
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ] && [ -z "$FORCE_SETUP" ]; then
  if "$VENV_DIR/bin/python" -V >/dev/null 2>&1; then
    echo "[setup] use existing venv: $VENV_DIR"
    PYTHON_BIN="$VENV_DIR/bin/python"
  else
    echo "[setup] existing venv is invalid, rebuilding: $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
fi

if [ -z "$PYTHON_BIN" ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    APT_PREFIX=""
  else
    APT_PREFIX="sudo"
  fi

  run_apt "apt-get update" $APT_PREFIX apt-get update
  run_apt "install python/opencv/gstreamer packages" \
    $APT_PREFIX apt-get install -y python3-venv python3-pip python3-opencv \
      gstreamer1.0-tools gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav

  CV2_STATUS="$("$PYTHON_SYS" - <<'EOF'
try:
    import cv2
    info = cv2.getBuildInformation()
    lines = [line for line in info.splitlines() if "GStreamer" in line]
    ok = any(("GStreamer:" in line and "YES" in line) for line in lines)
    print("OK" if ok else "NO_GST")
except Exception:
    print("NO_CV2")
EOF
)"
  if [ "$CV2_STATUS" = "NO_CV2" ]; then
    echo "[setup] cv2 not available in system python"
    exit 1
  fi
  if [ "$CV2_STATUS" = "NO_GST" ]; then
    echo "[setup] system cv2 has no GStreamer support"
    exit 1
  fi

  "$PYTHON_SYS" -m venv --system-site-packages "$VENV_DIR"
  PYTHON_BIN="$VENV_DIR/bin/python"
  "$PYTHON_BIN" -m pip install --upgrade pip

  if [ -d "$PACKAGES_DIR" ]; then
    RKN_LIB_SRC="$PACKAGES_DIR/librknnrt.so"
    if [ -f "$RKN_LIB_SRC" ]; then
      $APT_PREFIX cp -f "$RKN_LIB_SRC" /usr/lib/librknnrt.so
      $APT_PREFIX chmod 755 /usr/lib/librknnrt.so || true
      $APT_PREFIX ldconfig || true
    fi

    RGA_SO_SRC="$PACKAGES_DIR/librga.so"
    if [ -f "$RGA_SO_SRC" ]; then
      $APT_PREFIX cp -f "$RGA_SO_SRC" /usr/local/lib/librga.so
      $APT_PREFIX chmod 755 /usr/local/lib/librga.so || true
      $APT_PREFIX ldconfig || true
    fi

    RGA_HDR_SRC="$PACKAGES_DIR/im2d.h"
    if [ -f "$RGA_HDR_SRC" ]; then
      $APT_PREFIX mkdir -p /usr/local/include/rga
      $APT_PREFIX cp -f "$RGA_HDR_SRC" /usr/local/include/rga/im2d.h
    fi

    PY_MAJOR="$(echo "$PYTHON_VER" | cut -d. -f1)"
    PY_MINOR="$(echo "$PYTHON_VER" | cut -d. -f2)"
    PY_TAG="cp${PY_MAJOR}${PY_MINOR}"
    RKNN_WHL=""
    if ls "$PACKAGES_DIR"/rknn_toolkit_lite*"$PY_TAG"*.whl >/dev/null 2>&1; then
      RKNN_WHL="$(ls "$PACKAGES_DIR"/rknn_toolkit_lite*"$PY_TAG"*.whl 2>/dev/null | head -n 1)"
    fi
    if [ -n "$RKNN_WHL" ]; then
      "$PYTHON_BIN" -m pip install "$RKNN_WHL"
    fi
  fi

  if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt"
  fi

  "$PYTHON_BIN" - <<'EOF'
try:
    from rknnlite.api import RKNNLite  # type: ignore
    print("[setup] RKNNLite available.")
except Exception as exc:
    print("[setup] warning: RKNNLite import failed:", repr(exc))
EOF
fi

if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && ps -p "$OLD_PID" >/dev/null 2>&1; then
    kill "$OLD_PID" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! ps -p "$OLD_PID" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if ps -p "$OLD_PID" >/dev/null 2>&1; then
      kill -9 "$OLD_PID" 2>/dev/null || true
    fi
  fi
  rm -f "$PID_FILE"
fi

ensure_port_available "$WEB_PORT"

cd "$SCRIPT_DIR"
nohup "$PYTHON_BIN" -m web.server --config "$CONFIG_PATH" --host "$WEB_HOST" --port "$WEB_PORT" >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
if wait_for_server_ready "$NEW_PID" "$WEB_PORT"; then
  echo "$NEW_PID" > "$PID_FILE"
  echo "[service] started pid=$NEW_PID host=$WEB_HOST port=$WEB_PORT log=$LOG_FILE config=$CONFIG_PATH"
  start_cpu_monitor "$NEW_PID"
else
  echo "[service] failed to start, check log: $LOG_FILE"
  if [ -d "/proc/$NEW_PID" ]; then
    describe_pid "$NEW_PID" || true
  fi
  tail -n 50 "$LOG_FILE" 2>/dev/null || true
  exit 1
fi
