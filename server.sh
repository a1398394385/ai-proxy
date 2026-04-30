#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="$SCRIPT_DIR/.server.pid"
PROXY_PIDFILE="$SCRIPT_DIR/.proxy.pid"
PASS_THROUGH_PIDFILE="$SCRIPT_DIR/.pass_through.pid"

start_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 已经在运行 (PID $PID)，访问 http://127.0.0.1:18742"
      return 0
    fi
    rm -f "$PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python server.py > /dev/null 2>&1 &
  BPID=$!
  echo "$BPID" > "$PIDFILE"
  # 等待进程是否启动失败（比如 python 解析错误）
  sleep 0.5
  if ! kill -0 "$BPID" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "Hermes Data Browser 启动失败，请查看 server.py 日志"
    return 1
  fi
  echo "Hermes Data Browser 已启动 (PID $BPID)，访问 http://127.0.0.1:18742"
  return 0
}

stop_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PIDFILE"
      echo "Hermes Data Browser 已停止 (PID $PID)"
      return 0
    fi
    rm -f "$PIDFILE"
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Hermes Data Browser 已停止 (端口 18742)" || echo "停止失败"
    return 0
  fi
  echo "Hermes Data Browser 未运行"
  return 0
}

status_data_browser() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Hermes Data Browser 运行中 PID=$PID"
      return 0
    fi
  fi
  PID2=$(lsof -ti:18742 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Hermes Data Browser 运行中 PID=$PID2 (孤儿进程)"
    return 0
  fi
  echo "Hermes Data Browser 未运行"
  return 0
}

start_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 已经在运行 (PID $PID)，访问 http://127.0.0.1:48743"
      return 0
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python3 proxy.py > /dev/null 2>&1 &
  BPID=$!
  echo "$BPID" > "$PROXY_PIDFILE"
  # 等待进程是否启动失败（比如 proxy_config.yaml 配置错误）
  sleep 0.5
  if ! kill -0 "$BPID" 2>/dev/null; then
    rm -f "$PROXY_PIDFILE"
    echo "Codex Proxy 启动失败，请查看 proxy.log"
    return 1
  fi
  echo "Codex Proxy 已启动 (PID $BPID)，访问 http://127.0.0.1:48743"
  return 0
}

stop_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PROXY_PIDFILE"
      echo "Codex Proxy 已停止 (PID $PID)"
      return 0
    fi
    rm -f "$PROXY_PIDFILE"
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Codex Proxy 已停止 (端口 48743)" || echo "停止失败"
    return 0
  fi
  echo "Codex Proxy 未运行"
  return 0
}

status_proxy() {
  if [ -f "$PROXY_PIDFILE" ]; then
    PID=$(cat "$PROXY_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Codex Proxy 运行中 PID=$PID"
      return 0
    fi
  fi
  PID2=$(lsof -ti:48743 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Codex Proxy 运行中 PID=$PID2 (孤儿进程)"
    return 0
  fi
  echo "Codex Proxy 未运行"
  return 0
}

start_pass_through() {
  if [ -f "$PASS_THROUGH_PIDFILE" ]; then
    PID=$(cat "$PASS_THROUGH_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Pass-Through Proxy 已经在运行 (PID $PID)，访问 http://127.0.0.1:48744"
      return 0
    fi
    rm -f "$PASS_THROUGH_PIDFILE"
  fi
  cd "$SCRIPT_DIR"
  nohup python3 pass_through.py > /dev/null 2>&1 &
  BPID=$!
  echo "$BPID" > "$PASS_THROUGH_PIDFILE"
  sleep 0.5
  if ! kill -0 "$BPID" 2>/dev/null; then
    rm -f "$PASS_THROUGH_PIDFILE"
    echo "Pass-Through Proxy 启动失败"
    return 1
  fi
  echo "Pass-Through Proxy 已启动 (PID $BPID)，访问 http://127.0.0.1:48744"
  return 0
}

stop_pass_through() {
  if [ -f "$PASS_THROUGH_PIDFILE" ]; then
    PID=$(cat "$PASS_THROUGH_PIDFILE")
    if kill "$PID" 2>/dev/null; then
      rm -f "$PASS_THROUGH_PIDFILE"
      echo "Pass-Through Proxy 已停止 (PID $PID)"
      return 0
    fi
    rm -f "$PASS_THROUGH_PIDFILE"
  fi
  PID2=$(lsof -ti:48744 2>/dev/null)
  if [ -n "$PID2" ]; then
    kill "$PID2" && echo "Pass-Through Proxy 已停止 (端口 48744)" || echo "停止失败"
    return 0
  fi
  echo "Pass-Through Proxy 未运行"
  return 0
}

status_pass_through() {
  if [ -f "$PASS_THROUGH_PIDFILE" ]; then
    PID=$(cat "$PASS_THROUGH_PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "Pass-Through Proxy 运行中 PID=$PID"
      return 0
    fi
  fi
  PID2=$(lsof -ti:48744 2>/dev/null)
  if [ -n "$PID2" ]; then
    echo "Pass-Through Proxy 运行中 PID=$PID2 (孤儿进程)"
    return 0
  fi
  echo "Pass-Through Proxy 未运行"
  return 0
}

start() {
  # 记录 Data Browser 是否原本就在运行
  local db_was_running=false
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      db_was_running=true
    fi
  fi

  start_data_browser
  local db_rc=$?

  start_proxy
  local proxy_rc=$?
  if [ $proxy_rc -ne 0 ]; then
    echo "ERROR: Codex Proxy 启动失败"
    if [ "$db_was_running" = false ] && [ $db_rc -eq 0 ]; then
      echo "回退: 停止刚刚启动的 Data Browser"
      stop_data_browser
    fi
    return 1
  fi

  start_pass_through
}

stop() {
  stop_data_browser
  stop_proxy
  stop_pass_through
}

status() {
  status_data_browser
  status_proxy
  status_pass_through
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
