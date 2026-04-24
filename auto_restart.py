#!/usr/bin/env python3
"""
Hermes Data Browser 自动重启工具
用法: python3 auto_restart.py
"""
import subprocess
import time
import os
import signal

def restart_server():
    """重启 Hermes Data Browser 服务器"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pid_file = "/tmp/hdb_server.pid"
    log_file = "/tmp/hdb_server.log"
    
    # 1. 杀掉旧进程
    try:
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(0.5)
    except Exception:
        pass
    
    # 2. 强制杀掉所有 server.py 实例
    subprocess.run(["pkill", "-f", "python3.*server.py"], capture_output=True)
    time.sleep(1)
    
    # 3. 等待端口释放
    for _ in range(5):
        result = subprocess.run(["lsof", "-ti:18742"], capture_output=True)
        if result.returncode != 0 or not result.stdout.strip():
            break
        pids = result.stdout.strip().split()
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass
        time.sleep(0.5)
    
    # 4. 清理缓存
    cache_dir = os.path.join(script_dir, "__pycache__")
    if os.path.exists(cache_dir):
        import shutil
        shutil.rmtree(cache_dir)
    
    # 5. 启动新服务器
    os.chdir(script_dir)
    with open(log_file, "a") as log:
        log.write(f"\n--- Restart at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    
    process = subprocess.Popen(
        ["python3", "server.py"],
        stdout=open(log_file, "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True
    )
    
    with open(pid_file, "w") as f:
        f.write(str(process.pid))
    
    # 6. 等待服务器启动
    time.sleep(1.5)
    
    # 7. 验证
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:18742/api/token_stats?period=week", timeout=3) as resp:
            if resp.status == 200:
                print(f"✅ 服务器重启成功 (PID: {process.pid})")
                return True
    except Exception as e:
        print(f"❌ 服务器启动失败: {e}")
        return False
    
    return False

if __name__ == "__main__":
    restart_server()
