#!/bin/bash
PIDFILE="$(cd "$(dirname "$0")" && pwd)/.server.pid"

start() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 已经在运行 (PID $PID)，访问 http://127.0.0.1:18742"
      return
    fi
    rm -f "$PIDFILE"
  fi
  cd "$(dirname "$0")"
  nohup python server.py > /dev/null 2>&1 &
  echo $! > "$PIDFILE"
  echo "Hermes Data Browser 已启动 (PID $!)，访问 http://127.0.0.1:18742"
}

stop() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "Hermes Data Browser 已停止 (PID $PID)"
      return
    fi
    rm -f "$PIDFILE"
  fi
  # Fallback: find by port
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Hermes Data Browser 已停止 (端口 18742)" || echo "停止失败"
    return
  fi
  echo "Hermes Data Browser 未运行"
}

status() {
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

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  *)      echo "用法: $0 {start|stop|status}" ;;
esac
