#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.server.pid"
PROXY_PIDFILE="$SCRIPT_DIR/.proxy.pid"

start_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 已经在运行 (PID $PID)，访问 http://127.0.0.1:18742"
      return
    fi
    rm -f "$PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python server.py > /dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo "Hermes Data Browser 已启动 (PID $!)，访问 http://127.0.0.1:18742"
}

stop_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "Hermes Data Browser 已停止 (PID $PID)"
      return
    fi
    rm -f "$PIDFILE"
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Hermes Data Browser 已停止 (端口 18742)" || echo "停止失败"
    return
  fi
  echo "Hermes Data Browser 未运行"
}

status_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 运行中 PID=$PID"
      return
    fi
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Hermes Data Browser 运行中 PID=$PID2 (孤儿进程)"
    return
  fi
  echo "Hermes Data Browser 未运行"
}

start_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 已经在运行 (PID $PID)，访问 http://127.0.0.1:48743"
      return
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python3 proxy.py > /dev/null 2>&1 &
  echo $! > "$PROXY_PIDFILE"
  echo "Codex Proxy 已启动 (PID $!)，访问 http://127.0.0.1:48743"
}

stop_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PROXY_PIDFILE"
      echo "Codex Proxy 已停止 (PID $PID)"
      return
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Codex Proxy 已停止 (端口 48743)" || echo "停止失败"
    return
  fi
  echo "Codex Proxy 未运行"
}

status_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 运行中 PID=$PID"
      return
    fi
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Codex Proxy 运行中 PID=$PID2 (孤儿进程)"
    return
  fi
  echo "Codex Proxy 未运行"
}

start() {
  start_data_browser
  start_proxy
}

stop() {
  stop_data_browser
  stop_proxy
}

status() {
  status_data_browser
  status_proxy
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *)      echo "用法: $0 {start|stop|status}" ;;
esac
