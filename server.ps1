# Windows 启动脚本 - 管理 Hermes Data Browser 和 AI Proxy
# 用法: .\server.ps1 {start|stop|status|restart}

param(
    [Parameter(Position=0)]
    [ValidateSet("start", "stop", "status", "restart")]
    [string]$Action = "start"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ScriptDir ".server.pid"
$ProxyPidFile = Join-Path $ScriptDir ".proxy.pid"

# ─── 辅助函数：通过端口查找进程 ───
function Find-ProcessByPort($Port) {
    $connections = netstat -ano | Select-String ":$Port\s+.*LISTENING"
    if ($connections) {
        foreach ($conn in $connections) {
            $parts = $conn -split '\s+'
            $pid = $parts[-1]
            if ($pid -match '^\d+$') {
                return [int]$pid
            }
        }
    }
    return $null
}

# ─── Data Browser 管理 ───
function Start-DataBrowser {
    $oldPid = Find-ProcessByPort 18742
    if ($oldPid) {
        Write-Host "Hermes Data Browser 已经在运行 (PID $oldPid)，访问 http://127.0.0.1:18742"
        $oldPid | Out-File -FilePath $PidFile -Encoding ascii
        return $true
    }
    
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    
    Push-Location $ScriptDir
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList "server.py" `
            -WindowStyle Hidden -PassThru
        
        # 等待端口就绪，最多 3 秒
        $ready = $false
        for ($i = 0; $i -lt 6; $i++) {
            Start-Sleep -Milliseconds 500
            $pid = Find-ProcessByPort 18742
            if ($pid) {
                $pid | Out-File -FilePath $PidFile -Encoding ascii
                Write-Host "Hermes Data Browser 已启动 (PID $pid)，访问 http://127.0.0.1:18742"
                $ready = $true
                break
            }
        }
        
        if (-not $ready) {
            Write-Host "Hermes Data Browser 启动失败，请查看 server.py 日志" -ForegroundColor Red
            return $false
        }
        return $true
    }
    finally {
        Pop-Location
    }
}

function Stop-DataBrowser {
    $pid = Find-ProcessByPort 18742
    if ($pid) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
        
        # 等待端口释放
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Milliseconds 300
            if (-not (Find-ProcessByPort 18742)) { break }
        }
        Write-Host "Hermes Data Browser 已停止"
        return $true
    }
    
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force }
    Write-Host "Hermes Data Browser 未运行"
    return $true
}

function Get-DataBrowserStatus {
    $pid = Find-ProcessByPort 18742
    if ($pid) {
        Write-Host "Hermes Data Browser 运行中 PID=$pid"
        return $true
    }
    Write-Host "Hermes Data Browser 未运行"
    return $false
}

# ─── AI Proxy 管理 ───
function Start-Proxy {
    $oldPid = Find-ProcessByPort 48743
    if ($oldPid) {
        Write-Host "AI Proxy 已经在运行 (PID $oldPid)，访问 http://127.0.0.1:48743"
        $oldPid | Out-File -FilePath $ProxyPidFile -Encoding ascii
        return $true
    }
    
    if (Test-Path $ProxyPidFile) { Remove-Item $ProxyPidFile -Force }
    
    Push-Location $ScriptDir
    try {
        $proc = Start-Process -FilePath "python" -ArgumentList "proxy.py" `
            -WindowStyle Hidden -PassThru
        
        # 等待端口就绪，最多 3 秒
        $ready = $false
        for ($i = 0; $i -lt 6; $i++) {
            Start-Sleep -Milliseconds 500
            $pid = Find-ProcessByPort 48743
            if ($pid) {
                $pid | Out-File -FilePath $ProxyPidFile -Encoding ascii
                Write-Host "AI Proxy 已启动 (PID $pid)，访问 http://127.0.0.1:48743"
                $ready = $true
                break
            }
        }
        
        if (-not $ready) {
            Write-Host "AI Proxy 启动失败，请查看 proxy.log" -ForegroundColor Red
            return $false
        }
        return $true
    }
    finally {
        Pop-Location
    }
}

function Stop-Proxy {
    $pid = Find-ProcessByPort 48743
    if ($pid) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        if (Test-Path $ProxyPidFile) { Remove-Item $ProxyPidFile -Force }
        
        # 等待端口释放
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Milliseconds 300
            if (-not (Find-ProcessByPort 48743)) { break }
        }
        Write-Host "AI Proxy 已停止"
        return $true
    }
    
    if (Test-Path $ProxyPidFile) { Remove-Item $ProxyPidFile -Force }
    Write-Host "AI Proxy 未运行"
    return $true
}

function Get-ProxyStatus {
    $pid = Find-ProcessByPort 48743
    if ($pid) {
        Write-Host "AI Proxy 运行中 PID=$pid"
        return $true
    }
    Write-Host "AI Proxy 未运行"
    return $false
}

# ─── 主命令 ───
function Invoke-Start {
    $dbOk = Start-DataBrowser
    $proxyOk = Start-Proxy
    
    if (-not $proxyOk) {
        Write-Host "ERROR: AI Proxy 启动失败" -ForegroundColor Red
        if ($dbOk) {
            Write-Host "回退: 停止刚刚启动的 Data Browser"
            Stop-DataBrowser
        }
        exit 1
    }
}

function Invoke-Stop {
    Stop-DataBrowser
    Stop-Proxy
}

function Invoke-Status {
    Get-DataBrowserStatus
    Get-ProxyStatus
}

function Invoke-Restart {
    Invoke-Stop
    Start-Sleep -Seconds 1
    Invoke-Start
}

# 执行命令
switch ($Action) {
    "start"   { Invoke-Start }
    "stop"    { Invoke-Stop }
    "status"  { Invoke-Status }
    "restart" { Invoke-Restart }
}
