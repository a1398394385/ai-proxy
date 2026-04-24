#!/bin/bash
# 自动重启 Hermes Data Browser 服务器

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="/tmp/hdb_server.pid"
LOG_FILE="/tmp/hdb_server.log"

# 杀掉旧进程
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ]; then
        kill "$OLD_PID" 2>/dev/null
        sleep 0.5
    fi
fi

# 也杀掉可能的其他实例
pkill -f "python3.*server.py" 2>/dev/null
sleep 1

# 等待端口释放
for i in {1..5}; do
    if ! lsof -ti:18742 > /dev/null 2>&1; then
        break
    fi
    kill $(lsof -ti:18742) 2>/dev/null
    sleep 0.5
done

# 清理缓存
rm -rf "$SCRIPT_DIR/__pycache__"

# 启动新进程
cd "$SCRIPT_DIR" || exit 1
python3 server.py > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"

# 等待服务器就绪
sleep 1

# 检查是否启动成功
if kill -0 "$NEW_PID" 2>/dev/null; then
    # 测试 HTTP 连接
    if curl -s "http://127.0.0.1:18742/api/token_stats?period=week" > /dev/null 2>&1; then
        echo "Server restarted successfully (PID: $NEW_PID)"
        exit 0
    else
        echo "Server started but HTTP not responding"
        exit 1
    fi
else
    echo "Server failed to start"
    cat "$LOG_FILE" | tail -10
    exit 1
fi
