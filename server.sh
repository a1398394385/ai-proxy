#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.server.pid"
PROXY_PIDFILE="$SCRIPT_DIR/.proxy.pid"

start_data_browser() {
  local old_pid
  old_pid=$(lsof -ti:18742 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$old_pid" ]; then
    echo "Hermes Data Browser 已经在运行 (PID $old_pid)，访问 http://127.0.0.1:18742"
    echo "$old_pid" > "$PIDFILE"
    return 0
  fi
  rm -f "$PIDFILE"
  cd "$SCRIPT_DIR"
  nohup python server.py > /dev/null 2>&1 &
  # 等待端口就绪，最多 3 秒
  local i=0
  while [ $i -lt 6 ]; do
    sleep 0.5
    local pid
    pid=$(lsof -ti:18742 -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pid" ]; then
      echo "$pid" > "$PIDFILE"
      echo "Hermes Data Browser 已启动 (PID $pid)，访问 http://127.0.0.1:18742"
      return 0
    fi
    i=$((i + 1))
  done
  echo "Hermes Data Browser 启动失败，请查看 server.py 日志"
  return 1
}

stop_data_browser() {
  local pids
  pids=$(lsof -ti:18742 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$pids" ]; then
    for pid in $pids; do
      kill "$pid" 2>/dev/null
    done
    rm -f "$PIDFILE"
    # 等待端口释放
    local i=0
    while [ $i -lt 10 ] && lsof -ti:18742 -sTCP:LISTEN >/dev/null 2>&1; do
      sleep 0.3
      i=$((i + 1))
    done
    echo "Hermes Data Browser 已停止"
    return 0
  fi
  rm -f "$PIDFILE"
  echo "Hermes Data Browser 未运行"
  return 0
}

status_data_browser() {
  local pid
  pid=$(lsof -ti:18742 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$pid" ]; then
    echo "Hermes Data Browser 运行中 PID=$pid"
    return 0
  fi
  echo "Hermes Data Browser 未运行"
  return 0
}

start_proxy() {
  local old_pid
  old_pid=$(lsof -ti:48743 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$old_pid" ]; then
    echo "AI Proxy 已经在运行 (PID $old_pid)，访问 http://127.0.0.1:48743"
    echo "$old_pid" > "$PROXY_PIDFILE"
    return 0
  fi
  rm -f "$PROXY_PIDFILE"
  cd "$SCRIPT_DIR"
  nohup python3 proxy.py > /dev/null 2>&1 &
  # 等待端口就绪，最多 3 秒
  local i=0
  while [ $i -lt 6 ]; do
    sleep 0.5
    local pid
    pid=$(lsof -ti:48743 -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pid" ]; then
      echo "$pid" > "$PROXY_PIDFILE"
      echo "AI Proxy 已启动 (PID $pid)，访问 http://127.0.0.1:48743"
      return 0
    fi
    i=$((i + 1))
  done
  echo "AI Proxy 启动失败，请查看 proxy.log"
  return 1
}

stop_proxy() {
  local pids
  pids=$(lsof -ti:48743 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$pids" ]; then
    for pid in $pids; do
      kill "$pid" 2>/dev/null
    done
    rm -f "$PROXY_PIDFILE"
    # 等待端口释放
    local i=0
    while [ $i -lt 10 ] && lsof -ti:48743 -sTCP:LISTEN >/dev/null 2>&1; do
      sleep 0.3
      i=$((i + 1))
    done
    echo "AI Proxy 已停止"
    return 0
  fi
  rm -f "$PROXY_PIDFILE"
  echo "AI Proxy 未运行"
  return 0
}

status_proxy() {
  local pid
  pid=$(lsof -ti:48743 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$pid" ]; then
    echo "AI Proxy 运行中 PID=$pid"
    return 0
  fi
  echo "AI Proxy 未运行"
  return 0
}

start() {
  start_data_browser
  local db_rc=$?

  start_proxy
  local proxy_rc=$?
  if [ $proxy_rc -ne 0 ]; then
    echo "ERROR: AI Proxy 启动失败"
    if [ $db_rc -eq 0 ]; then
      echo "回退: 停止刚刚启动的 Data Browser"
      stop_data_browser
    fi
    return 1
  fi
}

stop() {
  stop_data_browser
  stop_proxy
}

status() {
  status_data_browser
  status_proxy
}

restart() {
  stop
  sleep 1
  start
}

case "${1:-start}" in
  start)    start ;;
  stop)     stop ;;
  status)   status ;;
  restart)  restart ;;
  *)        echo "用法: $0 {start|stop|status|restart}" ;;
esac
