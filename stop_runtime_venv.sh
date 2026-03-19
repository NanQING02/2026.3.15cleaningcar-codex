#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_PORT="${WEB_PORT:-8000}"
PID_FILE="$SCRIPT_DIR/web_server_${WEB_PORT}.pid"

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

if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "${PID:-}" ]; then
    echo "[service] stop pid from pid file: $PID"
    stop_pid "$PID"
  fi
  rm -f "$PID_FILE"
fi

OWNERS="$(port_owner_pids "$WEB_PORT" || true)"
if [ -z "$OWNERS" ]; then
  echo "[service] no listener on port $WEB_PORT"
  exit 0
fi

while read -r pid; do
  [ -z "$pid" ] && continue
  if [ ! -d "/proc/$pid" ]; then
    continue
  fi
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
  if [[ "$cmdline" == *"$SCRIPT_DIR"* ]] || [[ "$cwd" == "$SCRIPT_DIR"* ]] || [[ "$cmdline" == *"web.server"* ]]; then
    echo "[service] stop port owner: $(describe_pid "$pid")"
    stop_pid "$pid"
  else
    echo "[service] port $WEB_PORT is occupied by unrelated process, not stopping automatically:"
    describe_pid "$pid" || true
  fi
done <<< "$OWNERS"

sleep 1
REMAINING="$(port_owner_pids "$WEB_PORT" || true)"
if [ -n "$REMAINING" ]; then
  echo "[service] port $WEB_PORT still occupied"
  exit 1
fi

echo "[service] port $WEB_PORT released"
