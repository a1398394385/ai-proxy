@echo off
setlocal enabledelayedexpansion

:: Windows 启动脚本 - 管理 Hermes Data Browser 和 AI Proxy
:: 用法: server.bat {start|stop|status|restart}

set SCRIPT_DIR=%~dp0
set PIDFILE=%SCRIPT_DIR%.server.pid
set PROXY_PIDFILE=%SCRIPT_DIR%.proxy.pid

if "%1"=="" (
    set ACTION=start
) else (
    set ACTION=%1
)

if "%ACTION%"=="start" goto :do_start
if "%ACTION%"=="stop" goto :do_stop
if "%ACTION%"=="status" goto :do_status
if "%ACTION%"=="restart" goto :do_restart
echo 用法: %~nx0 {start^|stop^|status^|restart}
exit /b 1

:do_start
call :start_data_browser
call :start_proxy
goto :eof

:do_stop
call :stop_data_browser
call :stop_proxy
goto :eof

:do_status
call :status_data_browser
call :status_proxy
goto :eof

:do_restart
call :stop_data_browser
call :stop_proxy
timeout /t 1 /nobreak >nul
call :start_data_browser
call :start_proxy
goto :eof

:: ─── Data Browser 函数 ───

:start_data_browser
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18742.*LISTENING"') do set OLD_PID=%%a
if defined OLD_PID (
    echo Hermes Data Browser 已经在运行 ^(PID !OLD_PID!^)，访问 http://127.0.0.1:18742
    echo !OLD_PID!> "%PIDFILE%"
    goto :eof
)

if exist "%PIDFILE%" del "%PIDFILE%"

cd /d "%SCRIPT_DIR%"
start /b python server.py >nul 2>&1

:: 等待端口就绪，最多 3 秒
set WAIT_COUNT=0
:wait_data_browser
timeout /t 1 /nobreak >nul
set /a WAIT_COUNT+=1
if %WAIT_COUNT% geq 3 (
    echo Hermes Data Browser 启动失败，请查看 server.py 日志
    goto :eof
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18742.*LISTENING"') do set NEW_PID=%%a
if not defined NEW_PID goto :wait_data_browser

echo !NEW_PID!> "%PIDFILE%"
echo Hermes Data Browser 已启动 ^(PID !NEW_PID!^)，访问 http://127.0.0.1:18742
goto :eof

:stop_data_browser
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18742.*LISTENING"') do set KILL_PID=%%a
if defined KILL_PID (
    taskkill /pid !KILL_PID! /f >nul 2>&1
    if exist "%PIDFILE%" del "%PIDFILE%"
    echo Hermes Data Browser 已停止
) else (
    if exist "%PIDFILE%" del "%PIDFILE%"
    echo Hermes Data Browser 未运行
)
goto :eof

:status_data_browser
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":18742.*LISTENING"') do set STATUS_PID=%%a
if defined STATUS_PID (
    echo Hermes Data Browser 运行中 PID=!STATUS_PID!
) else (
    echo Hermes Data Browser 未运行
)
goto :eof

:: ─── AI Proxy 函数 ───

:start_proxy
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":48743.*LISTENING"') do set OLD_PROXY_PID=%%a
if defined OLD_PROXY_PID (
    echo AI Proxy 已经在运行 ^(PID !OLD_PROXY_PID!^)，访问 http://127.0.0.1:48743
    echo !OLD_PROXY_PID!> "%PROXY_PIDFILE%"
    goto :eof
)

if exist "%PROXY_PIDFILE%" del "%PROXY_PIDFILE%"

cd /d "%SCRIPT_DIR%"
start /b python proxy.py >nul 2>&1

:: 等待端口就绪，最多 3 秒
set WAIT_COUNT=0
:wait_proxy
timeout /t 1 /nobreak >nul
set /a WAIT_COUNT+=1
if %WAIT_COUNT% geq 3 (
    echo AI Proxy 启动失败，请查看 proxy.log
    goto :eof
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":48743.*LISTENING"') do set NEW_PROXY_PID=%%a
if not defined NEW_PROXY_PID goto :wait_proxy

echo !NEW_PROXY_PID!> "%PROXY_PIDFILE%"
echo AI Proxy 已启动 ^(PID !NEW_PROXY_PID!^)，访问 http://127.0.0.1:48743
goto :eof

:stop_proxy
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":48743.*LISTENING"') do set KILL_PROXY_PID=%%a
if defined KILL_PROXY_PID (
    taskkill /pid !KILL_PROXY_PID! /f >nul 2>&1
    if exist "%PROXY_PIDFILE%" del "%PROXY_PIDFILE%"
    echo AI Proxy 已停止
) else (
    if exist "%PROXY_PIDFILE%" del "%PROXY_PIDFILE%"
    echo AI Proxy 未运行
)
goto :eof

:status_proxy
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":48743.*LISTENING"') do set STATUS_PROXY_PID=%%a
if defined STATUS_PROXY_PID (
    echo AI Proxy 运行中 PID=!STATUS_PROXY_PID!
) else (
    echo AI Proxy 未运行
)
goto :eof
