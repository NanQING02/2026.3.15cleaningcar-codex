#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv-gst"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8000}"
SERVICE_NAME="web_server_${WEB_PORT}"
PID_FILE="$SCRIPT_DIR/${SERVICE_NAME}.pid"
LOG_FILE="$SCRIPT_DIR/${SERVICE_NAME}.log"
ACTION="${1:-start}"

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

start_cpu_monitor() {
  local pid="$1"
  local cpu_limit="${MONITOR_CPU_LIMIT:-600}"
  local interval="${MONITOR_INTERVAL:-10}"
  (
    while ps -p "$pid" >/dev/null 2>&1; do
      local cpu_raw
      cpu_raw="$(ps -p "$pid" -o %cpu= 2>/dev/null | awk '{print int($1)}')"
      if [ -n "$cpu_raw" ] && [ "$cpu_raw" -gt "$cpu_limit" ]; then
        echo "[monitor] pid=$pid cpu=${cpu_raw}% limit=${cpu_limit}%"
        kill "$pid" 2>/dev/null || true
        sleep 5
        if ps -p "$pid" >/dev/null 2>&1; then
          kill -9 "$pid" 2>/dev/null || true
        fi
        echo "[monitor] process killed, check log: $LOG_FILE"
        break
      fi
      sleep "$interval"
    done
  ) &
}

do_stop() {
  local stopped=0
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "${pid:-}" ]; then
      echo "[service] stop pid from pid file: $pid"
      stop_pid "$pid"
      stopped=1
    fi
    rm -f "$PID_FILE"
  fi

  local owners
  owners="$(port_owner_pids "$WEB_PORT" || true)"
  if [ -z "$owners" ]; then
    if [ "$stopped" -eq 0 ]; then
      echo "[service] no listener on port $WEB_PORT"
    fi
    return 0
  fi

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
      echo "[service] stop port owner: $(describe_pid "$pid")"
      stop_pid "$pid"
      stopped=1
    else
      echo "[service] port $WEB_PORT is occupied by unrelated process, not stopping automatically:"
      describe_pid "$pid" || true
    fi
  done <<< "$owners"

  sleep 1
  local remaining
  remaining="$(port_owner_pids "$WEB_PORT" || true)"
  if [ -n "$remaining" ]; then
    echo "[service] port $WEB_PORT still occupied"
    return 1
  fi
  echo "[service] port $WEB_PORT released"
}

do_status() {
  local owners
  owners="$(port_owner_pids "$WEB_PORT" || true)"
  if [ -z "$owners" ]; then
    echo "[service] stopped"
    return 1
  fi
  echo "[service] listening on port $WEB_PORT"
  local pid
  while read -r pid; do
    [ -z "$pid" ] && continue
    describe_pid "$pid" || true
  done <<< "$owners"
}

do_start() {
  local config_path
  config_path="$(resolve_config_path)"
  local python_bin="$VENV_DIR/bin/python"
  if [ ! -x "$python_bin" ]; then
    echo "[service] missing runtime venv, please run ./install_runtime_venv.sh first"
    exit 1
  fi

  if [ -f "$PID_FILE" ]; then
    local old_pid
    old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && ps -p "$old_pid" >/dev/null 2>&1; then
      echo "[service] stop old pid from pid file: $old_pid"
      stop_pid "$old_pid"
    fi
    rm -f "$PID_FILE"
  fi

  ensure_port_available "$WEB_PORT"

  cd "$SCRIPT_DIR"
  nohup "$python_bin" -m web.server --config "$config_path" --host "$WEB_HOST" --port "$WEB_PORT" >> "$LOG_FILE" 2>&1 &
  local new_pid=$!
  if wait_for_server_ready "$new_pid" "$WEB_PORT"; then
    echo "$new_pid" > "$PID_FILE"
    echo "[service] started pid=$new_pid host=$WEB_HOST port=$WEB_PORT log=$LOG_FILE config=$config_path"
    start_cpu_monitor "$new_pid"
  else
    echo "[service] failed to start, check log: $LOG_FILE"
    if [ -d "/proc/$new_pid" ]; then
      describe_pid "$new_pid" || true
    fi
    tail -n 50 "$LOG_FILE" 2>/dev/null || true
    exit 1
  fi
}

case "$ACTION" in
  start)
    do_start
    ;;
  stop)
    do_stop
    ;;
  restart)
    do_stop || true
    do_start
    ;;
  status)
    do_status
    ;;
  *)
    echo "usage: $0 [start|stop|restart|status]"
    exit 1
    ;;
esac
