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

if /I "%ACTIVE_VENV_PATH%"=="%PROJECT_VENV%" if exist "%PROJECT_VENV%\Scripts\activate.bat" call "%PROJECT_VENV%\Scripts\activate.bat" >nul 2>nul

color 0A
echo.
echo 安装成功！您现在可以在系统的任何终端直接输入 [codex-hud] 或 [codex-hud --once] 畅快使用！
echo.
color 07

popd
endlocal
exit /b 0

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
