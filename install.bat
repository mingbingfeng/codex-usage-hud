@echo off
setlocal EnableExtensions

chcp 65001 >nul

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
pushd "%PROJECT_ROOT%" >nul
if errorlevel 1 goto pushd_failed

set "PROJECT_VENV=%PROJECT_ROOT%\.venv"

if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" goto use_active_venv
if exist "%PROJECT_VENV%\Scripts\python.exe" goto use_project_venv
goto detect_system_python

:use_active_venv
set "PYTHON_CALL=%VIRTUAL_ENV%\Scripts\python.exe"
set "ACTIVE_VENV_PATH=%VIRTUAL_ENV%"
goto have_python

:use_project_venv
set "PYTHON_CALL=%PROJECT_VENV%\Scripts\python.exe"
set "ACTIVE_VENV_PATH=%PROJECT_VENV%"
goto have_python

:detect_system_python
for /f "delims=" %%I in ('where python 2^>nul') do (
    echo %%~fI | find /I "WindowsApps" >nul
    if errorlevel 1 (
        set "PYTHON_CALL=%%~fI"
        goto have_python
    )
)

where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CALL=py"
    set "PYTHON_ARGS=-3"
    goto have_python
)
goto no_python

:have_python
if not defined PYTHON_CALL goto no_python

if not defined ACTIVE_VENV_PATH goto create_project_venv
goto check_pip

:create_project_venv
echo [1/3] 正在创建项目虚拟环境 .venv ...
call "%PYTHON_CALL%" %PYTHON_ARGS% -m venv "%PROJECT_VENV%"
if errorlevel 1 goto venv_failed
set "PYTHON_CALL=%PROJECT_VENV%\Scripts\python.exe"
set "PYTHON_ARGS="
set "ACTIVE_VENV_PATH=%PROJECT_VENV%"
goto check_pip

:check_pip
echo [2/3] 正在检查 pip ...
call "%PYTHON_CALL%" %PYTHON_ARGS% -m pip --version >nul 2>nul
if errorlevel 1 goto ensurepip
goto install_editable

:ensurepip
echo [3/3] pip 不可用，正在启用 ensurepip ...
call "%PYTHON_CALL%" %PYTHON_ARGS% -m ensurepip --upgrade
if errorlevel 1 goto ensurepip_failed

:install_editable
echo [3/3] 正在执行 editable 安装 ...
call "%PYTHON_CALL%" %PYTHON_ARGS% -m pip install -e "%PROJECT_ROOT%"
if errorlevel 1 goto install_failed

call :ensure_command_path

if /I "%ACTIVE_VENV_PATH%"=="%PROJECT_VENV%" if exist "%PROJECT_VENV%\Scripts\activate.bat" call "%PROJECT_VENV%\Scripts\activate.bat" >nul 2>nul

call :offer_startup

color 0A
echo.
echo 安装成功！您现在可以在系统的任何终端直接输入 [codex-hud] 或 [codex-hud --once] 畅快使用！
echo 守护模式可手动运行: codex-hud --daemon
echo 如果当前终端仍无法识别 codex-hud，请新开一个 PowerShell 窗口。
echo.
color 07

call :offer_build_exe
if errorlevel 1 goto build_exe_failed

popd
endlocal
exit /b 0

:ensure_command_path
set "CODEX_HUD_SCRIPTS=%ACTIVE_VENV_PATH%\Scripts"
if not exist "%CODEX_HUD_SCRIPTS%\codex-hud.exe" (
    echo [WARN] 未找到 codex-hud.exe，跳过 PATH 注册: %CODEX_HUD_SCRIPTS%
    exit /b 0
)
set "CODEX_HUD_PATH_TO_ADD=%CODEX_HUD_SCRIPTS%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$target=$env:CODEX_HUD_PATH_TO_ADD; $current=[Environment]::GetEnvironmentVariable('Path','User'); if ([string]::IsNullOrWhiteSpace($current)) { [Environment]::SetEnvironmentVariable('Path',$target,'User'); exit 0 }; $targetNorm=$target.TrimEnd('\'); $exists=($current -split ';' | Where-Object { $_.TrimEnd('\').Equals($targetNorm,[StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1); if (-not $exists) { [Environment]::SetEnvironmentVariable('Path', $current.TrimEnd(';') + ';' + $target, 'User') }"
if errorlevel 1 (
    echo [WARN] 写入用户 PATH 失败；可手动运行: setx PATH "%%PATH%%;%CODEX_HUD_SCRIPTS%"
    exit /b 0
)
echo 已确保命令目录在用户 PATH: %CODEX_HUD_SCRIPTS%
exit /b 0

:offer_startup
echo.
echo 是否注册开机自启动守护进程？
echo [Y] 启动文件夹（推荐，无需管理员权限）  [N] 暂不注册  [R] 注册表 Run
choice /C YNR /N /M "请选择: "
if errorlevel 3 goto register_run
if errorlevel 2 exit /b 0
if errorlevel 1 goto register_startup
exit /b 0

:resolve_daemon_python
set "DAEMON_PYTHON=%ACTIVE_VENV_PATH%\Scripts\pythonw.exe"
if exist "%DAEMON_PYTHON%" exit /b 0
set "DAEMON_PYTHON=%ACTIVE_VENV_PATH%\Scripts\python.exe"
if exist "%DAEMON_PYTHON%" exit /b 0
set "DAEMON_PYTHON=%PYTHON_CALL%"
exit /b 0

:register_startup
call :resolve_daemon_python
if not defined APPDATA (
    echo [WARN] 未检测到 APPDATA，无法写入启动文件夹；已跳过自启动注册。
    exit /b 0
)
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_SCRIPT=%STARTUP_DIR%\codex-usage-hud-daemon.vbs"
if not exist "%STARTUP_DIR%" mkdir "%STARTUP_DIR%"
if errorlevel 1 (
    echo [WARN] 创建启动文件夹失败；已跳过自启动注册。
    exit /b 0
)
set "CODEX_HUD_DAEMON_PYTHON=%DAEMON_PYTHON%"
set "CODEX_HUD_STARTUP_SCRIPT=%STARTUP_SCRIPT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[string][char]34; $cmd=$q + $env:CODEX_HUD_DAEMON_PYTHON + $q + ' -m codex_usage_hud --daemon'; $escaped=$cmd.Replace($q, $q + $q); $line='Set shell = CreateObject(' + $q + 'WScript.Shell' + $q + '): shell.Run ' + $q + $escaped + $q + ', 0, False'; Set-Content -LiteralPath $env:CODEX_HUD_STARTUP_SCRIPT -Encoding ASCII -Value $line"
if errorlevel 1 (
    echo [WARN] 写入启动脚本失败；已跳过自启动注册。
    exit /b 0
)
echo 已注册启动文件夹自启动: %STARTUP_SCRIPT%
exit /b 0

:register_run
call :resolve_daemon_python
set "CODEX_HUD_DAEMON_PYTHON=%DAEMON_PYTHON%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[string][char]34; $cmd=$q + $env:CODEX_HUD_DAEMON_PYTHON + $q + ' -m codex_usage_hud --daemon'; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'codex-usage-hud' -Value $cmd"
if errorlevel 1 (
    echo [WARN] 写入注册表 Run 失败；已跳过自启动注册。
    exit /b 0
)
echo 已注册注册表自启动: HKCU\Software\Microsoft\Windows\CurrentVersion\Run\codex-usage-hud
exit /b 0

:offer_build_exe
echo.
echo 是否现在构建单文件、无黑框控制台的 codex-hud.exe？
echo [B] Build Single EXE  [S] 跳过
choice /C BS /N /M "请选择: "
if errorlevel 2 exit /b 0
if errorlevel 1 goto build_single_exe
exit /b 0

:build_single_exe
echo.
echo [1/1] 正在通过 tools\build_exe.py 构建 codex-hud.exe ...
call "%PYTHON_CALL%" %PYTHON_ARGS% "%PROJECT_ROOT%\tools\build_exe.py"
if errorlevel 1 exit /b 1
echo [OK] 已生成: %PROJECT_ROOT%\dist\codex-hud.exe
exit /b 0

:build_exe_failed
echo [ERROR] 构建单文件 EXE 失败。
popd
endlocal
exit /b 1

:pushd_failed
echo [ERROR] 无法切换到项目根目录: %PROJECT_ROOT%
exit /b 1

:no_python
echo [ERROR] 未检测到可用的 Python。请先安装 Python 3.10+，并确保 python 或 py 命令可用。
popd
exit /b 1

:venv_failed
echo [ERROR] 创建虚拟环境失败。
popd
exit /b 1

:ensurepip_failed
echo [ERROR] pip 初始化失败，请确认当前 Python 安装包含 ensurepip。
popd
exit /b 1

:install_failed
echo [ERROR] 安装失败。
popd
exit /b 1
