"""Command-line interface for codex-usage-hud."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Mapping

from . import __version__
from .config import (
    DEFAULT_BUDGET_THRESHOLDS,
    DEFAULT_DAILY_BUDGET_USD,
    DEFAULT_WEEKLY_BUDGET_USD,
    UserConfig,
    UserConfigStore,
    effective_display_mode,
    fetch_model_prices,
    normalize_display_mode,
    parse_thresholds as parse_config_thresholds,
    time_parts,
)
from .core import (
    CostEstimator,
    JsonlSessionParser,
    ParsedSession,
    SseRequestStateMachine,
    UsageCalculator,
    UsageSummary,
)
from .daemon import (
    CodexDaemonManager,
    DEFAULT_DAEMON_POLL_MS,
    MAX_DAEMON_POLL_MS,
    ProcessListenerError,
    WindowsProcessListener,
    configure_daemon_logging,
    hide_console_window,
)
from .platforms import (
    ActiveSessionTracker,
    CodexWindowTracker,
    SessionPathResolver,
    get_current_platform,
)
from .platforms.base import BasePlatform
from .platforms.cdp_probe import cdp_port_from_env
from .settings_bridge import SettingsBridgeServer
from .ui import TokenHudWindow
from .ui.renderer_hud import RendererHudClient, wait_for_renderer
from .updater import (
    check_for_update,
    download_update_asset,
    format_update_info,
    launch_installer,
)

DEFAULT_POLL_MS = 500
DEFAULT_SQLITE_LOG = "logs_2.sqlite"
DEFAULT_STATE_DB = "state_5.sqlite"
DEFAULT_SESSION_INDEX = "session_index.jsonl"
DEFAULT_BUDGET_THRESHOLDS_TEXT = ",".join(f"{item:g}" for item in DEFAULT_BUDGET_THRESHOLDS)
DEFAULT_ACTIVE_SESSION_POLL_MS = 500
DEFAULT_AUTO_SWITCH_IDLE_SECONDS = 30.0
DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS = 2.0
RENDERER_IDLE_POLL_MS = 1500
HUD_LOCK_FILENAME = "codex_usage_hud.pid"
HUD_MUTEX_NAME = "Local\\codex_usage_hud_single_instance"
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
DAEMON_RESTART_REQUESTED = 10
RENDERER_HUD_UNAVAILABLE = 20
HUD_SWITCH_TO_TK = 30
HUD_SWITCH_TO_RENDERER = 31
HUD_SWITCH_TO_RENDERER_RESTART_CODEX = 32
RENDERER_CDP_TIMEOUT_SECONDS = 1.0
DAEMON_RENDERER_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_INITIAL_TIMEOUT_SECONDS = 2.0
DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS = 5.0
DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS = 15.0
RENDERER_UPDATE_FAILURE_LIMIT = 6
AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT = 3
RENDERER_DIAGNOSTIC_FILENAME = "renderer_fallback.log"
CODEX_APP_PATH_ENV = "CODEX_USAGE_HUD_CODEX_APP"
CODEX_APP_ID_ENV = "CODEX_USAGE_HUD_CODEX_APP_ID"
CODEX_APP_DEFAULT_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
DAEMON_STARTUP_WAIT = "wait"
DAEMON_STARTUP_RENDERER = "renderer"
DAEMON_STARTUP_TK = "tk"
DAEMON_STARTUP_CANCEL = "cancel"
_LOGGER = logging.getLogger("codex_usage_hud.cli")
_cli_daemon_logging_attached = False


class HudAlreadyRunningError(RuntimeError):
    """Raised when another HUD instance owns the local runtime lock."""


@dataclass(frozen=True)
class DaemonStartupDecision:
    """How daemon startup should continue when Codex is not already visible."""

    mode: str
    launch_codex: bool = False


def configure_stdout() -> None:
    """Prefer UTF-8 console output when the interpreter supports it."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _eprint(message: str) -> None:
    """Print to stderr when a console stream exists."""
    try:
        stream = sys.stderr
        if stream is not None and hasattr(stream, "write"):
            print(message, file=stream)
    except Exception:
        pass


def _attach_cli_logger_to_daemon_log() -> None:
    """Mirror CLI daemon lifecycle logs into the daemon log file."""
    global _cli_daemon_logging_attached
    if _cli_daemon_logging_attached:
        return
    daemon_logger = logging.getLogger("codex_usage_hud.daemon")
    handlers = [
        item
        for item in daemon_logger.handlers
        if not isinstance(item, logging.NullHandler)
    ]
    if not handlers:
        return
    for handler in handlers:
        if handler not in _LOGGER.handlers:
            _LOGGER.addHandler(handler)
    _LOGGER.setLevel(daemon_logger.level or logging.INFO)
    _LOGGER.propagate = False
    _cli_daemon_logging_attached = True


def _unique_strings(items: Sequence[str]) -> list[str]:
    """Return non-empty strings with original order preserved."""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _codex_app_shell_targets() -> list[str]:
    """Return shell targets that can open the Codex desktop app normally."""
    targets: list[str] = []
    configured_id = os.environ.get(CODEX_APP_ID_ENV, "").strip()
    if configured_id:
        if configured_id.lower().startswith("shell:"):
            targets.append(configured_id)
        else:
            targets.append(f"shell:AppsFolder\\{configured_id}")
    targets.append(f"shell:AppsFolder\\{CODEX_APP_DEFAULT_ID}")

    configured_path = os.environ.get(CODEX_APP_PATH_ENV, "").strip()
    if configured_path:
        targets.append(configured_path)

    start_menu_roots = [
        os.environ.get("APPDATA", ""),
        os.environ.get("PROGRAMDATA", ""),
    ]
    for root in start_menu_roots:
        if not root:
            continue
        base = Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        targets.extend(
            str(base / name)
            for name in (
                "Codex.lnk",
                "OpenAI Codex.lnk",
                Path("OpenAI") / "Codex.lnk",
            )
        )
    return _unique_strings(targets)


def _codex_app_executable_candidates() -> list[Path]:
    """Return executable candidates that can accept Chromium/Electron flags."""
    candidates: list[Path] = []
    configured = os.environ.get(CODEX_APP_PATH_ENV, "").strip()
    if configured and not configured.lower().startswith("shell:"):
        path = Path(configured).expanduser()
        if path.suffix.lower() == ".exe":
            candidates.append(path)

    for root_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_name)
        if not root:
            continue
        base = Path(root)
        candidates.extend(
            [
                base / "Programs" / "Codex" / "Codex.exe",
                base / "Programs" / "codex" / "Codex.exe",
                base / "Programs" / "OpenAI Codex" / "Codex.exe",
                base / "Codex" / "Codex.exe",
                base / "OpenAI Codex" / "Codex.exe",
            ]
        )

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        windows_apps = Path(program_files) / "WindowsApps"
        try:
            candidates.extend(
                windows_apps.glob("OpenAI.Codex_*__2p2nqsd0c76g0/app/Codex.exe")
            )
        except OSError:
            pass
    for install_location in _codex_appx_install_locations():
        candidates.append(install_location / "app" / "Codex.exe")

    existing = [path for path in candidates if path.exists()]
    return [Path(item) for item in _unique_strings(str(path) for path in existing)]


def _codex_appx_install_locations() -> list[Path]:
    """Return MSIX install locations for the Codex desktop app."""
    if not sys.platform.startswith("win"):
        return []
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "Get-AppxPackage -Name OpenAI.Codex | "
                    "Select-Object -ExpandProperty InstallLocation"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        Path(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _shell_execute_open(
    target: str | Path,
    *,
    verb: str = "open",
    parameters: str = "",
    working_dir: str | Path | None = None,
) -> bool:
    """Open a Windows shell target without requiring a console."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        result = shell32.ShellExecuteW(
            None,
            verb,
            str(target),
            parameters or None,
            str(working_dir) if working_dir else None,
            1,
        )
        return int(result or 0) > 32
    except Exception as exc:
        _LOGGER.info(
            "codex_app_shell_execute_failed verb=%s target=%s error=%s",
            verb,
            target,
            exc,
        )
        return False


def _shell_execute_open_with_elevation_fallback(
    target: str | Path,
    *,
    parameters: str = "",
    working_dir: str | Path | None = None,
) -> bool:
    """Open a target normally, then ask Windows for elevation if needed."""
    if _shell_execute_open(target, parameters=parameters, working_dir=working_dir):
        return True
    if _shell_execute_open(
        target,
        verb="runas",
        parameters=parameters,
        working_dir=working_dir,
    ):
        _LOGGER.info("codex_app_launch_elevated target=%s", target)
        return True
    return False


def _codex_app_debugger_parameters(port: int) -> str:
    """Return Chromium flags required for HUD CDP websocket access."""
    port = int(port)
    return (
        f"--remote-debugging-port={port} "
        f"--remote-allow-origins=http://127.0.0.1:{port}"
    )


def launch_codex_app(*, debugger: bool = False) -> bool:
    """Best-effort launch of Codex App, optionally with local CDP enabled."""
    if debugger:
        port = cdp_port_from_env()
        parameters = _codex_app_debugger_parameters(port)
        for executable in _codex_app_executable_candidates():
            if _shell_execute_open_with_elevation_fallback(
                executable,
                parameters=parameters,
                working_dir=executable.parent,
            ):
                _LOGGER.info(
                    "codex_app_launched mode=debugger target=%s port=%s",
                    executable,
                    port,
                )
                return True
        for target in _codex_app_shell_targets():
            if _shell_execute_open(target, parameters=parameters):
                _LOGGER.info(
                    "codex_app_launched mode=debugger target=%s port=%s",
                    target,
                    port,
                )
                return True
        _LOGGER.info("codex_app_debugger_launch_unavailable port=%s", port)
        return False

    for target in _codex_app_shell_targets():
        if _shell_execute_open(target):
            _LOGGER.info("codex_app_launched mode=normal target=%s", target)
            return True
    for executable in _codex_app_executable_candidates():
        if _shell_execute_open_with_elevation_fallback(
            executable,
            working_dir=executable.parent,
        ):
            _LOGGER.info("codex_app_launched mode=normal target=%s", executable)
            return True
    _LOGGER.info("codex_app_launch_unavailable")
    return False


def _clone_args_with_renderer_preference(
    args: argparse.Namespace,
    prefer_renderer: bool,
) -> argparse.Namespace:
    cloned = argparse.Namespace(**vars(args))
    cloned.renderer_hud = bool(prefer_renderer)
    return cloned


def _runtime_display_mode(value: object) -> str:
    return effective_display_mode(value)


def _stop_codex_processes(*, timeout_seconds: float = 8.0) -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        listener = WindowsProcessListener(exclude_pid=os.getpid())
        snapshot = listener.snapshot()
    except ProcessListenerError as exc:
        _LOGGER.info("codex_app_stop_unavailable error=%s", exc)
        return False
    if not snapshot.found:
        return True

    pending = {int(pid) for pid in snapshot.pids if int(pid) > 0}
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while pending and time.monotonic() < deadline:
        for pid in list(pending):
            if not _process_exists(pid):
                pending.discard(pid)
                continue
            _terminate_process(pid)
        time.sleep(0.1)
        pending = {pid for pid in pending if _process_exists(pid)}
        if not pending:
            return True
        try:
            refreshed = listener.snapshot()
        except ProcessListenerError:
            refreshed = None
        if refreshed is not None:
            pending.update(int(pid) for pid in refreshed.pids if int(pid) > 0)
    remaining = [pid for pid in sorted(pending) if _process_exists(pid)]
    if remaining:
        _LOGGER.info("codex_app_stop_incomplete pids=%s", ",".join(map(str, remaining)))
        return False
    return True


def _restart_codex_for_renderer() -> bool:
    if sys.platform.startswith("win") and not _stop_codex_processes():
        return False
    return launch_codex_app(debugger=True)


def _prompt_missing_codex_startup() -> str:
    """Ask the user how to continue when daemon startup finds no Codex app."""
    if not sys.platform.startswith("win"):
        return DAEMON_STARTUP_WAIT
    message = (
        "未检测到 Codex App。\n\n"
        "请选择本次启动方式：\n\n"
        "是：启动 Codex App（调试/CDP 模式），并将 HUD 注入到 Codex 界面里。\n"
        "否：启动 Codex App（普通模式），同时打开独立 Tk HUD 窗口。\n"
        "取消：退出 HUD。\n\n"
        "Renderer 注入需要 Codex 暴露本地调试端口；Tk 模式可作为独立窗口使用。"
        "\n\n如 Windows 阻止直接启动，HUD 会请求一次权限确认。"
    )
    title = "Codex App 未启动"
    try:
        import ctypes

        MB_YESNOCANCEL = 0x00000003
        MB_ICONINFORMATION = 0x00000040
        MB_SETFOREGROUND = 0x00010000
        MB_TOPMOST = 0x00040000
        IDYES = 6
        IDNO = 7
        IDCANCEL = 2
        result = ctypes.windll.user32.MessageBoxW(
            None,
            message,
            title,
            MB_YESNOCANCEL | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
        )
        if int(result or 0) == IDYES:
            return DAEMON_STARTUP_RENDERER
        if int(result or 0) == IDNO:
            return DAEMON_STARTUP_TK
        if int(result or 0) == IDCANCEL:
            return DAEMON_STARTUP_CANCEL
    except Exception as exc:
        _LOGGER.info("daemon_startup_prompt_failed error=%s", exc)
    return DAEMON_STARTUP_WAIT


def _daemon_startup_decision(
    args: argparse.Namespace,
    manager: CodexDaemonManager,
) -> DaemonStartupDecision:
    """Resolve startup behavior before the daemon enters the invisible wait loop."""
    if getattr(args, "no_startup_prompt", False):
        return DaemonStartupDecision(DAEMON_STARTUP_WAIT)
    snapshot = manager.snapshot()
    if snapshot.found:
        return DaemonStartupDecision(DAEMON_STARTUP_WAIT)
    mode = _prompt_missing_codex_startup()
    return DaemonStartupDecision(
        mode,
        launch_codex=mode in {DAEMON_STARTUP_RENDERER, DAEMON_STARTUP_TK},
    )


def hud_runtime_dir() -> Path:
    """Return the per-user directory for lightweight HUD runtime files."""
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(
            os.environ.get("XDG_RUNTIME_DIR")
            or os.environ.get("XDG_STATE_HOME")
            or Path.home() / ".local" / "state"
        )
    return base / "codex-usage-hud"


def hud_lock_path() -> Path:
    """Return the pid-file lock path used by the interactive HUD."""
    return hud_runtime_dir() / HUD_LOCK_FILENAME


def renderer_diagnostic_path() -> Path:
    """Return the renderer fallback diagnostics path."""
    return hud_runtime_dir() / RENDERER_DIAGNOSTIC_FILENAME


def _append_renderer_diagnostic(stage: str, **fields: object) -> None:
    """Persist one renderer fallback diagnostic record for postmortems."""
    path = renderer_diagnostic_path()
    record = {
        "time": datetime.now().astimezone().isoformat(),
        "stage": stage,
    }
    for key, value in fields.items():
        if value not in {"", None}:
            record[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def _renderer_update_failure_limit(display_mode: str, last_error: str) -> int:
    """Return how many consecutive renderer update failures we tolerate."""
    if (
        normalize_display_mode(display_mode) == "auto"
        and "timed out" in str(last_error or "").lower()
    ):
        return AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT
    return RENDERER_UPDATE_FAILURE_LIMIT


def _renderer_refresh_delay_seconds(
    context: "RuntimeContext",
    snapshot: ParsedSession,
    elapsed_seconds: float,
    *,
    force_fast: bool = False,
) -> float:
    """Return the next renderer loop delay with slower idle refreshes."""
    fast_seconds = max(0.1, context.poll_ms / 1000.0)
    request_status = str(getattr(snapshot.request, "status", "") or "")
    target_seconds = fast_seconds
    if not force_fast and request_status != "running":
        target_seconds = max(fast_seconds, RENDERER_IDLE_POLL_MS / 1000.0)
    return max(0.1, target_seconds - max(0.0, elapsed_seconds))


def _wait_for_visible_codex_window(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
) -> tuple[bool, str, str, int]:
    """Wait briefly for a visible Codex top-level window before renderer injection."""
    if not sys.platform.startswith("win"):
        return True, "unsupported", "", 0
    try:
        tracker = CodexWindowTracker(enable_uia=False)
    except Exception:
        return True, "tracker-error", "", 0
    if not getattr(tracker, "enabled", False):
        return True, "disabled", "", 0

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        snapshot = tracker.get_window_snapshot()
        if str(getattr(snapshot, "status", "")) == "visible":
            return True, str(snapshot.status), str(snapshot.reason or ""), int(
                snapshot.hwnd or 0
            )
        if time.monotonic() >= deadline:
            return False, str(snapshot.status), str(snapshot.reason or ""), int(
                snapshot.hwnd or 0
            )
        time.sleep(max(0.01, float(poll_seconds)))


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(
                    wintypes.HANDLE(handle),
                    ctypes.byref(exit_code),
                ):
                    return False
                return int(exit_code.value or 0) == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_process(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


class HudInstanceLock:
    """Cross-process lock to keep invisible duplicate HUDs from piling up."""

    def __init__(self, path: Path | None = None, mutex_name: str | None = None) -> None:
        self.path = path or hud_lock_path()
        self.mutex_name = mutex_name or HUD_MUTEX_NAME
        self._owned = False
        self._mutex_handle: int | None = None

    def acquire(self) -> None:
        self._mutex_handle = self._acquire_native_mutex()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_pid = _read_pid(self.path)
        if existing_pid is not None and _process_exists(existing_pid):
            self._release_native_mutex()
            raise HudAlreadyRunningError(
                f"codex-usage-hud is already running as PID {existing_pid}. "
                "Run `python -m codex_usage_hud --stop` to close it."
            )
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            self._release_native_mutex()
            raise HudAlreadyRunningError(
                "codex-usage-hud lock already exists. "
                "Run `python -m codex_usage_hud --stop` to clear it."
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self._owned = True

    def _acquire_native_mutex(self) -> int | None:
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [
                wintypes.LPVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.CreateMutexW(None, True, self.mutex_name)
            error = ctypes.get_last_error()
            if not handle:
                raise OSError(error, "CreateMutexW failed")
            if error == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
                raise HudAlreadyRunningError(
                    "codex-usage-hud is already running "
                    f"(mutex {self.mutex_name!r} exists). "
                    "Run `python -m codex_usage_hud --stop` to close it."
                )
            return int(handle)
        except HudAlreadyRunningError:
            raise
        except Exception:
            return None

    def _release_native_mutex(self) -> None:
        if not self._mutex_handle or not sys.platform.startswith("win"):
            self._mutex_handle = None
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = wintypes.HANDLE(self._mutex_handle)
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        self._mutex_handle = None

    def release(self) -> None:
        if not self._owned:
            self._release_native_mutex()
            return
        if _read_pid(self.path) == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self._owned = False
        self._release_native_mutex()

    def __enter__(self) -> "HudInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.release()


def stop_running_hud(path: Path | None = None) -> str:
    """Stop the HUD instance recorded in the local pid-file lock."""
    lock_path = path or hud_lock_path()
    pid = _read_pid(lock_path)
    if pid is None:
        try:
            lock_path.unlink()
        except OSError:
            pass
        return "No running codex-usage-hud instance was recorded."
    if not _process_exists(pid):
        try:
            lock_path.unlink()
        except OSError:
            pass
        return f"Removed stale codex-usage-hud lock for PID {pid}."
    if not _terminate_process(pid):
        return f"Unable to stop codex-usage-hud PID {pid}."
    for _ in range(20):
        if not _process_exists(pid):
            try:
                lock_path.unlink()
            except OSError:
                pass
            return f"Stopped codex-usage-hud PID {pid}."
        import time

        time.sleep(0.1)
    return f"Sent stop signal to codex-usage-hud PID {pid}."


def _format_money(value: float | None) -> str:
    return f"${float(value or 0.0):,.6f}"


def _format_tokens(value: int | None) -> str:
    amount = int(value or 0)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}k"
    return f"{amount}"


def _current_task_tokens(snapshot: ParsedSession) -> int:
    request = snapshot.request
    if request.total_tokens:
        return int(request.total_tokens)
    if request.input_tokens is not None or request.output_tokens is not None:
        return int(request.input_tokens or 0) + int(request.output_tokens or 0)
    if snapshot.estimate.total_tokens:
        return int(snapshot.estimate.total_tokens)
    return int(snapshot.confirmed.last_total or 0)


def _current_session_cost(snapshot: ParsedSession) -> float:
    confirmed_cost = float(snapshot.confirmed.cumulative_cost_usd or 0.0)
    pending_cost = 0.0
    if snapshot.request.status == "running" and snapshot.request.cost_usd is not None:
        pending_cost = float(snapshot.request.cost_usd)
    return round(confirmed_cost + pending_cost, 6)


def _merge_usage(target: UsageSummary, addition: UsageSummary) -> None:
    target.tokens += addition.tokens
    target.input_tokens += addition.input_tokens
    target.cached_tokens += addition.cached_tokens
    target.output_tokens += addition.output_tokens
    target.reasoning_tokens += addition.reasoning_tokens
    target.cost_usd = round(target.cost_usd + addition.cost_usd, 6)


def usage_before_today_in_week(
    week_total: UsageSummary,
    today_total: UsageSummary,
    day_start: datetime,
    week_start: datetime,
) -> UsageSummary:
    """Return the part of the weekly window that happened before this daily window."""
    if day_start <= week_start:
        return UsageSummary()
    return UsageSummary(
        tokens=max(0, week_total.tokens - today_total.tokens),
        input_tokens=max(0, week_total.input_tokens - today_total.input_tokens),
        cached_tokens=max(0, week_total.cached_tokens - today_total.cached_tokens),
        output_tokens=max(0, week_total.output_tokens - today_total.output_tokens),
        reasoning_tokens=max(0, week_total.reasoning_tokens - today_total.reasoning_tokens),
        cost_usd=round(max(0.0, week_total.cost_usd - today_total.cost_usd), 6),
    )


@dataclass
class _UsageCacheEntry:
    mtime: float | None
    file_size: int | None
    day_start: datetime
    week_start: datetime
    summary_day: UsageSummary
    summary_week: UsageSummary


class UsageSummaryCache:
    """Cache rolling day/week usage summaries per JSONL session file."""

    def __init__(
        self,
        parser: JsonlSessionParser,
        *,
        min_rescan_seconds: float = DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS,
    ) -> None:
        self._parser = parser
        self._min_rescan_seconds = max(0.0, float(min_rescan_seconds))
        self._entries: dict[Path, _UsageCacheEntry] = {}
        self._last_scan_key: tuple[Path, datetime, datetime] | None = None
        self._last_scan_at = 0.0
        self._last_day_total = UsageSummary()
        self._last_week_total = UsageSummary()

    def summarize(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> tuple[UsageSummary, UsageSummary]:
        now = time.monotonic()
        scan_key = (sessions_root, day_start, week_start)
        if (
            self._last_scan_key == scan_key
            and now - self._last_scan_at < self._min_rescan_seconds
        ):
            return replace(self._last_day_total), replace(self._last_week_total)

        day_total = UsageSummary()
        week_total = UsageSummary()

        if not sessions_root.exists():
            self._last_scan_key = scan_key
            self._last_scan_at = now
            self._last_day_total = day_total
            self._last_week_total = week_total
            return day_total, week_total

        seen_paths: set[Path] = set()
        for path in sessions_root.rglob("*.jsonl"):
            seen_paths.add(path)
            summary_day, summary_week = self._summaries_for_file(
                path, day_start, week_start
            )
            _merge_usage(day_total, summary_day)
            _merge_usage(week_total, summary_week)

        for cached_path in list(self._entries):
            if cached_path not in seen_paths:
                del self._entries[cached_path]

        self._last_scan_key = scan_key
        self._last_scan_at = now
        self._last_day_total = replace(day_total)
        self._last_week_total = replace(week_total)
        return day_total, week_total

    def _summaries_for_file(
        self,
        path: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> tuple[UsageSummary, UsageSummary]:
        try:
            stat = path.stat()
        except OSError:
            return UsageSummary(), UsageSummary()

        entry = self._entries.get(path)
        if (
            entry is not None
            and entry.mtime == stat.st_mtime
            and entry.file_size == stat.st_size
            and entry.day_start == day_start
            and entry.week_start == week_start
        ):
            return entry.summary_day, entry.summary_week

        try:
            records = self._parser.load_records_lenient(path)
        except OSError:
            return UsageSummary(), UsageSummary()

        events = self._parser.usage_events(records)
        summary_day = self._parser.summarize_usage_events(events, day_start)
        summary_week = self._parser.summarize_usage_events(events, week_start)
        self._entries[path] = _UsageCacheEntry(
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            day_start=day_start,
            week_start=week_start,
            summary_day=summary_day,
            summary_week=summary_week,
        )
        return summary_day, summary_week


@dataclass
class RuntimeContext:
    platform: BasePlatform
    sessions_root: Path
    session_file: Path | None
    sqlite_log_path: Path | None
    state_db_path: Path
    session_index_path: Path
    poll_ms: int
    daily_budget_usd: float
    weekly_budget_usd: float
    budget_thresholds: list[float]
    user_config: UserConfig
    settings_store: UserConfigStore
    settings_mtime: float | None
    parser: JsonlSessionParser
    sse_tracker: SseRequestStateMachine | None
    active_session_tracker: ActiveSessionTracker | None
    session_resolver: SessionPathResolver
    usage_cache: UsageSummaryCache

    def close(self) -> None:
        """Release any background helpers created for the runtime context."""
        if self.active_session_tracker is not None:
            self.active_session_tracker.close()

    def reload_user_config(self) -> None:
        """Reload user config and reset cost caches when pricing changes."""
        mtime = self.settings_store.mtime()
        if mtime == self.settings_mtime:
            return
        next_config = self.settings_store.load()
        prices_changed = next_config.price_table() != self.user_config.price_table()
        self.user_config = next_config
        self.settings_mtime = mtime
        self.daily_budget_usd = max(0.0, float(next_config.daily_budget_usd))
        self.weekly_budget_usd = max(0.0, float(next_config.weekly_budget_usd))
        self.budget_thresholds = list(next_config.budget_thresholds)
        if prices_changed:
            estimator = _cost_estimator_from_config(next_config)
            self.parser.cost_estimator = estimator
            if self.sse_tracker is not None:
                self.sse_tracker.cost_estimator = estimator
            self.usage_cache = UsageSummaryCache(self.parser)
            _configure_ui_cost_estimators(estimator)


def _candidate_data_dirs(platform: BasePlatform | None = None) -> list[Path]:
    platform = platform or get_current_platform()
    candidates = [platform.get_codex_data_dir(), Path.home() / ".codex"]
    ordered: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def _discover_path(
    platform: BasePlatform,
    explicit_path: str | None,
    relative_name: str,
) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()

    for root in _candidate_data_dirs(platform):
        candidate = root / relative_name
        if candidate.exists():
            return candidate
    return _candidate_data_dirs(platform)[0] / relative_name


def _discover_sessions_root(platform: BasePlatform, explicit_root: str | None) -> Path:
    if explicit_root:
        return Path(explicit_root).expanduser()

    for root in _candidate_data_dirs(platform):
        candidate = root / "sessions"
        if candidate.exists():
            return candidate
    return _candidate_data_dirs(platform)[0] / "sessions"


def parse_thresholds(value: object) -> list[float]:
    """Parse comma-separated budget warning thresholds."""
    return parse_config_thresholds(value)


def _cost_estimator_from_config(config: UserConfig) -> CostEstimator:
    return CostEstimator(UsageCalculator(config.price_table()))


def _configure_ui_cost_estimators(estimator: CostEstimator) -> None:
    try:
        from .ui import renderer_hud, tk_hud

        renderer_hud.set_cost_estimator(estimator)
        tk_hud.set_cost_estimator(estimator)
    except Exception:
        return


def _apply_cli_config_overrides(
    config: UserConfig,
    args: argparse.Namespace,
) -> UserConfig:
    patch: dict[str, object] = {}
    if getattr(args, "daily_budget_usd", None) is not None:
        patch["daily_budget_usd"] = max(0.0, float(args.daily_budget_usd))
    if getattr(args, "weekly_budget_usd", None) is not None:
        patch["weekly_budget_usd"] = max(0.0, float(args.weekly_budget_usd))
    if getattr(args, "budget_thresholds", None) is not None:
        patch["budget_thresholds"] = parse_thresholds(args.budget_thresholds)
    if getattr(args, "hud_mode", None):
        patch["display_mode"] = normalize_display_mode(args.hud_mode)
    if not patch:
        return config
    return replace(config, **patch)


def _config_from_settings_payload(
    current: UserConfig,
    payload: object,
) -> UserConfig:
    merged = current.to_dict()
    if isinstance(payload, Mapping):
        merged.update(dict(payload))
    return UserConfig.from_dict(merged)


def _save_renderer_user_config(context: RuntimeContext, config: UserConfig) -> None:
    context.settings_store.save(config)
    context.settings_mtime = None
    context.reload_user_config()


def _renderer_settings_status(
    message: str,
    *,
    kind: str = "",
    restart_visible: bool = False,
    switch_mode: str = "",
    restart_codex: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "message": message,
        "kind": kind,
        "restartVisible": restart_visible,
    }
    if switch_mode:
        payload["switchMode"] = switch_mode
    if restart_codex:
        payload["restartCodex"] = True
    return payload


def _handle_renderer_settings_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    restart_requested: Event,
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    try:
        if action == "save":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            _save_renderer_user_config(context, config)
            next_runtime_mode = _runtime_display_mode(config.display_mode)
            return _renderer_settings_status(
                (
                    "已保存到本地配置；预算和价格会自动刷新。"
                    if next_runtime_mode == "renderer"
                    else "已保存到本地配置；当前会话仍保持 Renderer，Tk 方案会在下次切换或重启后生效。"
                ),
            )
        if action == "applyDisplayMode":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            _save_renderer_user_config(context, config)
            next_runtime_mode = _runtime_display_mode(config.display_mode)
            if next_runtime_mode == "tk":
                return _renderer_settings_status(
                    "正在切换到 Tk 独立窗口。",
                    switch_mode="tk",
                )
            return _renderer_settings_status(
                "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。",
            )
        if action == "fetchPrices":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            prices = fetch_model_prices(config.pricing_url)
            config = config.with_price_updates(prices, pricing_url=config.pricing_url)
            _save_renderer_user_config(context, config)
            return _renderer_settings_status(
                f"已拉取并保存 {len(prices)} 个模型价格。",
            )
        if action == "restart":
            restart_requested.set()
            return _renderer_settings_status(
                "已请求重启 HUD；daemon 模式会自动恢复。",
            )
        if action == "checkUpdate":
            info = check_for_update(current_version=__version__)
            if info.error:
                return _renderer_settings_status(
                    f"检查更新失败：{info.error}",
                    kind="error",
                )
            if info.available:
                return _renderer_settings_status(
                    f"发现新版本 {info.latest_version}，安装包：{info.asset_name}",
                )
            return _renderer_settings_status(
                f"当前已是最新版本（{info.current_version}）。",
            )
        if action == "installUpdate":
            info = check_for_update(current_version=__version__)
            if info.error:
                return _renderer_settings_status(
                    f"检查更新失败：{info.error}",
                    kind="error",
                )
            if not info.available:
                return _renderer_settings_status(
                    f"当前已是最新版本（{info.current_version}）。",
                )
            installer = download_update_asset(info)
            launch_installer(installer)
            restart_requested.set()
            return _renderer_settings_status(
                f"已启动 {info.asset_name}，安装器会先关闭当前 HUD。",
            )
        return _renderer_settings_status(
            f"无法处理未知设置命令：{action or 'empty'}",
            kind="error",
        )
    except Exception as exc:
        return _renderer_settings_status(
            f"设置命令执行失败：{exc}",
            kind="error",
        )


def current_budget_windows(
    config: UserConfig | None = None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return daily and weekly budget windows using user reset settings."""
    config = config or UserConfig.defaults()
    now = now or datetime.now().astimezone()
    day_hour, day_minute = time_parts(config.daily_reset_time)
    week_hour, week_minute = time_parts(config.weekly_reset_time)
    day_start = datetime.combine(
        now.date(), datetime_time(hour=day_hour, minute=day_minute), tzinfo=now.tzinfo
    )
    if now < day_start:
        day_start -= timedelta(days=1)

    days_since_thursday = (now.weekday() - int(config.weekly_reset_weekday)) % 7
    week_date = now.date() - timedelta(days=days_since_thursday)
    week_start = datetime.combine(
        week_date, datetime_time(hour=week_hour, minute=week_minute), tzinfo=now.tzinfo
    )
    if now < week_start:
        week_start -= timedelta(days=7)
    return day_start, week_start


def budget_warnings(
    day_cost: float,
    week_cost: float,
    daily_limit_usd: float,
    weekly_limit_usd: float,
    thresholds: Sequence[float],
) -> list[str]:
    """Build original-style budget threshold warnings."""
    messages: list[str] = []
    for label, used, limit in [
        ("日", day_cost, daily_limit_usd),
        ("周", week_cost, weekly_limit_usd),
    ]:
        if limit <= 0:
            continue
        ratio = used / limit
        crossed = [item for item in thresholds if ratio >= item]
        if not crossed:
            continue
        percent = int(crossed[-1] * 100)
        messages.append(
            f"{label}额度已用 {used:.2f}/{limit:.0f} USD ({ratio:.0%})，超过 {percent}% 阈值"
        )
    return messages


def build_runtime_context(args: argparse.Namespace) -> RuntimeContext:
    platform = get_current_platform()
    settings_store = UserConfigStore()
    user_config = _apply_cli_config_overrides(settings_store.load(), args)
    estimator = _cost_estimator_from_config(user_config)
    _configure_ui_cost_estimators(estimator)
    parser = JsonlSessionParser(estimate_enabled=True, cost_estimator=estimator)
    sessions_root = _discover_sessions_root(platform, args.sessions_root)
    sqlite_log_path = _discover_path(platform, args.sse_db, DEFAULT_SQLITE_LOG)
    state_db_path = _discover_path(platform, args.state_db, DEFAULT_STATE_DB)
    session_index_path = _discover_path(platform, None, DEFAULT_SESSION_INDEX)
    active_session_tracker = ActiveSessionTracker(
        platform=platform,
        state_db=state_db_path,
        sessions_root=sessions_root,
        session_index_path=session_index_path,
        poll_ms=args.active_session_poll_ms,
        enabled=(
            not args.no_follow_active_session
            and not args.session_id
            and not args.session_file
        ),
    )
    active_session_tracker.start()
    session_resolver = SessionPathResolver(
        platform=platform,
        sessions_root=sessions_root,
        session_id=args.session_id,
        session_file=Path(args.session_file).expanduser() if args.session_file else None,
        active_session_tracker=active_session_tracker,
        auto_switch_idle_seconds=args.auto_switch_idle_seconds,
    )
    sse_tracker = (
        None
        if args.no_sse
        else SseRequestStateMachine(db_path=sqlite_log_path, cost_estimator=estimator)
    )
    return RuntimeContext(
        platform=platform,
        sessions_root=sessions_root,
        session_file=Path(args.session_file).expanduser() if args.session_file else None,
        sqlite_log_path=sqlite_log_path,
        state_db_path=state_db_path,
        session_index_path=session_index_path,
        poll_ms=max(100, int(args.poll_ms)),
        daily_budget_usd=max(0.0, float(user_config.daily_budget_usd)),
        weekly_budget_usd=max(0.0, float(user_config.weekly_budget_usd)),
        budget_thresholds=list(user_config.budget_thresholds),
        user_config=user_config,
        settings_store=settings_store,
        settings_mtime=settings_store.mtime(),
        parser=parser,
        sse_tracker=sse_tracker,
        active_session_tracker=active_session_tracker,
        session_resolver=session_resolver,
        usage_cache=UsageSummaryCache(parser),
    )


def build_snapshot(context: RuntimeContext) -> ParsedSession:
    context.reload_user_config()
    session_path, selection_source = context.session_resolver.resolve()

    if session_path is None:
        if context.session_resolver.session_id:
            snapshot = ParsedSession(
                status="missing",
                error=(
                    f"Session id not found under {context.sessions_root}: "
                    f"{context.session_resolver.session_id}"
                ),
            )
        elif context.session_resolver.session_file is not None:
            snapshot = ParsedSession(
                status="missing",
                error=f"Session file not found: {context.session_resolver.session_file}",
            )
        elif context.sessions_root.exists():
            snapshot = ParsedSession(
                status="waiting",
                error=f"No local Codex session JSONL found under {context.sessions_root}",
            )
        else:
            snapshot = ParsedSession(
                status="missing",
                error=f"Sessions directory not found: {context.sessions_root}",
            )
    else:
        snapshot = context.parser.parse_file(
            session_path,
            sse_tracker=context.sse_tracker,
        )
    snapshot.selection_source = selection_source
    if context.active_session_tracker is not None and session_path is not None:
        snapshot.session_title = context.active_session_tracker.title_for_session(
            session_path,
            snapshot.session_id,
        )

    day_start, week_start = current_budget_windows(context.user_config)
    today_total, week_total = context.usage_cache.summarize(
        context.sessions_root,
        day_start,
        week_start,
    )
    week_adjustment_usd = max(0.0, float(context.user_config.weekly_adjustment_usd))
    snapshot.today_tokens = today_total.tokens
    snapshot.today_cost_usd = today_total.cost_usd
    snapshot.week_tokens = week_total.tokens
    snapshot.week_cost_usd = round(week_total.cost_usd + week_adjustment_usd, 6)
    prior_week_total = usage_before_today_in_week(
        week_total,
        today_total,
        day_start,
        week_start,
    )
    snapshot.week_before_today_tokens = prior_week_total.tokens
    snapshot.week_before_today_cost_usd = prior_week_total.cost_usd
    snapshot.week_adjustment_usd = week_adjustment_usd
    snapshot.daily_limit_usd = context.daily_budget_usd
    snapshot.weekly_limit_usd = context.weekly_budget_usd
    snapshot.day_start = day_start
    snapshot.week_start = week_start
    snapshot.budget_warnings = budget_warnings(
        today_total.cost_usd,
        snapshot.week_cost_usd,
        context.daily_budget_usd,
        context.weekly_budget_usd,
        context.budget_thresholds,
    )
    snapshot.budget_error = "" if context.sessions_root.exists() else snapshot.error
    return snapshot


def snapshot_to_text(snapshot: ParsedSession, compact: bool = False) -> str:
    """Render a ParsedSession as CLI-friendly text."""
    model_name = snapshot.request.model or "n/a"
    task_tokens = _current_task_tokens(snapshot)
    session_cost = _current_session_cost(snapshot)

    if compact:
        return (
            f"session={snapshot.session_id} status={snapshot.status} source={snapshot.selection_source} model={model_name} "
            f"task_tokens={_format_tokens(task_tokens)} session_cost={_format_money(session_cost)} "
            f"today={_format_tokens(snapshot.today_tokens)}/{_format_money(snapshot.today_cost_usd)} "
            f"week={_format_tokens(snapshot.week_tokens)}/{_format_money(snapshot.week_cost_usd)}"
        )

    lines = [
        f"Session: {snapshot.session_id}",
        f"Status: {snapshot.status}",
        f"Source: {snapshot.selection_source}",
        f"Model: {model_name}",
        f"Current Task: {_format_tokens(task_tokens)} tokens",
        f"Current Session Cost: {_format_money(session_cost)}",
        (
            "Today: "
            f"{_format_tokens(snapshot.today_tokens)} tokens | "
            f"{_format_money(snapshot.today_cost_usd)} / "
            f"{_format_money(snapshot.daily_limit_usd)}"
        ),
        (
            "This Week: "
            f"{_format_tokens(snapshot.week_tokens)} tokens | "
            f"{_format_money(snapshot.week_cost_usd)} / "
            f"{_format_money(snapshot.weekly_limit_usd)}"
        ),
        (
            "This Week Breakdown: "
            f"before today reset {_format_money(snapshot.week_before_today_cost_usd)} + "
            f"today {_format_money(snapshot.today_cost_usd)}"
        ),
        f"Activity: {snapshot.activity.kind} | {snapshot.activity.detail or 'n/a'}",
        f"Path: {snapshot.session_path or 'n/a'}",
    ]
    if snapshot.week_adjustment_usd > 0:
        lines.append(
            "This Week Manual Adjustment: "
            f"{_format_money(snapshot.week_adjustment_usd)}"
        )
    if snapshot.budget_warnings:
        lines.append("Budget Warnings: " + " | ".join(snapshot.budget_warnings))
    if snapshot.error:
        lines.append(f"Error: {snapshot.error}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI parser."""
    parser = argparse.ArgumentParser(
        prog="codex-hud",
        description="Local-first Codex usage HUD from local JSONL and SQLite logs.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print the installed codex-usage-hud version and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print the current local snapshot and exit without opening the HUD.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the currently running HUD instance recorded by the local pid lock.",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check GitHub Releases for a newer Windows installer and exit.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Download and launch the latest Windows installer when one is available.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help=(
            "Run as a hidden Windows daemon: wait for Codex, show the HUD, "
            "and exit when Codex closes."
        ),
    )
    parser.add_argument(
        "--no-startup-prompt",
        action="store_true",
        help=(
            "In daemon mode, skip the Codex-not-running prompt and wait silently. "
            "Intended for login startup entries."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Use compact output mode for CLI snapshots and the Tkinter HUD.",
    )
    parser.set_defaults(renderer_hud=None)
    parser.add_argument(
        "--renderer-hud",
        dest="renderer_hud",
        action="store_true",
        help=(
            "Prefer the renderer-injected HUD when Codex exposes a local CDP "
            "target, falling back to the Tk HUD otherwise. Enabled by default."
        ),
    )
    parser.add_argument(
        "--tk-hud",
        "--no-renderer-hud",
        dest="renderer_hud",
        action="store_false",
        help="Force the legacy Tk HUD and skip renderer injection.",
    )
    parser.add_argument(
        "--hud-mode",
        choices=["auto", "renderer", "tk"],
        help=(
            "Override the configured HUD display mode for this run. "
            "auto and renderer both try renderer injection first; tk skips injection."
        ),
    )
    parser.add_argument(
        "--session-file",
        help="Optional exact session JSONL file to monitor.",
    )
    parser.add_argument(
        "--session-id",
        help="Optional session id to pin instead of following the active Codex conversation.",
    )
    parser.add_argument(
        "--sessions-root",
        help="Optional override for the root directory containing Codex session JSONL files.",
    )
    parser.add_argument(
        "--sse-db",
        help="Optional override for the Codex SQLite OTel log database path.",
    )
    parser.add_argument(
        "--state-db",
        help="Optional override for the Codex state SQLite path used for active-session mapping.",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        default=DEFAULT_POLL_MS,
        help=f"HUD refresh interval in milliseconds. Default: {DEFAULT_POLL_MS}.",
    )
    parser.add_argument(
        "--active-session-poll-ms",
        type=int,
        default=DEFAULT_ACTIVE_SESSION_POLL_MS,
        help=(
            "Polling interval for tracking the currently selected Codex conversation. "
            f"Default: {DEFAULT_ACTIVE_SESSION_POLL_MS}."
        ),
    )
    parser.add_argument(
        "--daemon-poll-ms",
        type=int,
        default=DEFAULT_DAEMON_POLL_MS,
        help=(
            "Windows daemon process polling interval in milliseconds. "
            f"Default: {DEFAULT_DAEMON_POLL_MS}; values above {MAX_DAEMON_POLL_MS} are clamped."
        ),
    )
    parser.add_argument(
        "--auto-switch-idle-seconds",
        type=float,
        default=DEFAULT_AUTO_SWITCH_IDLE_SECONDS,
        help=(
            "When no conversation is selected explicitly, only switch to a newer "
            "mtime-based session after the current session has been idle this many "
            f"seconds. Default: {DEFAULT_AUTO_SWITCH_IDLE_SECONDS:g}."
        ),
    )
    parser.add_argument(
        "--no-follow-active-session",
        action="store_true",
        help="Disable best-effort tracking of the currently selected Codex conversation.",
    )
    parser.add_argument(
        "--no-sse",
        action="store_true",
        help="Disable SQLite SSE tracking and use JSONL-only fallback parsing.",
    )
    parser.add_argument(
        "--daily-budget-usd",
        type=float,
        default=None,
        help=(
            "Daily reminder budget in USD. "
            f"Configured default: {DEFAULT_DAILY_BUDGET_USD:g}."
        ),
    )
    parser.add_argument(
        "--weekly-budget-usd",
        type=float,
        default=None,
        help=(
            "Weekly reminder budget in USD. "
            f"Configured default: {DEFAULT_WEEKLY_BUDGET_USD:g}."
        ),
    )
    parser.add_argument(
        "--budget-thresholds",
        default=None,
        help=(
            "Comma-separated budget warning thresholds. "
            f"Configured default: {DEFAULT_BUDGET_THRESHOLDS_TEXT}."
        ),
    )
    return parser


def run_update_check() -> int:
    """Print update status from GitHub Releases."""
    info = check_for_update(current_version=__version__)
    print(format_update_info(info))
    return 1 if info.error else 0


def run_update_install() -> int:
    """Download and launch the latest Windows installer when available."""
    info = check_for_update(current_version=__version__)
    if info.error:
        print(format_update_info(info), file=sys.stderr)
        return 1
    if not info.available:
        print(format_update_info(info))
        return 0
    try:
        installer = download_update_asset(info)
        launch_installer(installer)
    except Exception as exc:
        print(f"Update install failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Launched {info.asset_name}. "
        "The installer will stop the running HUD before replacing files."
    )
    return 0


def run_once_snapshot(args: argparse.Namespace) -> int:
    """Print one local usage snapshot and exit."""
    context = build_runtime_context(args)
    try:
        if context.active_session_tracker is not None:
            context.active_session_tracker.wait_for_title()
        print(snapshot_to_text(build_snapshot(context), compact=args.compact))
        return 0
    finally:
        context.close()


def run_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
) -> int:
    """Run one HUD session, preferring renderer injection with Tk fallback."""
    session_args = _clone_args_with_renderer_preference(
        args,
        getattr(args, "renderer_hud", False),
    )
    launched_codex_for_renderer = False
    while True:
        if getattr(session_args, "renderer_hud", False):
            renderer_exit = run_renderer_hud_session(
                session_args,
                lock_already_held=lock_already_held,
                daemon_manager=daemon_manager,
                launched_codex=launched_codex_for_renderer,
            )
            launched_codex_for_renderer = False
            if renderer_exit == HUD_SWITCH_TO_TK:
                session_args = _clone_args_with_renderer_preference(session_args, False)
                continue
            if renderer_exit != RENDERER_HUD_UNAVAILABLE:
                return renderer_exit
            _LOGGER.info("renderer_hud_unavailable falling_back=tk")
            session_args = _clone_args_with_renderer_preference(session_args, False)
            continue

        tk_exit = run_tk_hud_session(
            session_args,
            lock_already_held=lock_already_held,
            hide_until_attached=hide_until_attached,
            daemon_manager=daemon_manager,
        )
        if tk_exit == HUD_SWITCH_TO_RENDERER:
            session_args = _clone_args_with_renderer_preference(session_args, True)
            continue
        if tk_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
            if not _restart_codex_for_renderer():
                _eprint("codex-usage-hud: unable to restart Codex App in debugger mode.")
                session_args = _clone_args_with_renderer_preference(session_args, False)
                continue
            session_args = _clone_args_with_renderer_preference(session_args, True)
            launched_codex_for_renderer = True
            continue
        return tk_exit


def run_renderer_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    launched_codex: bool = False,
) -> int:
    """Run the in-renderer HUD over CDP, or report that it is unavailable."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            display_mode = normalize_display_mode(
                getattr(args, "hud_mode", None) or context.user_config.display_mode
            )
            client = RendererHudClient(
                timeout_seconds=(
                    DAEMON_RENDERER_CDP_TIMEOUT_SECONDS
                    if daemon_manager is not None
                    else RENDERER_CDP_TIMEOUT_SECONDS
                )
            )
            restart_requested = Event()
            bridge = SettingsBridgeServer(
                context.settings_store,
                restart_callback=restart_requested.set,
            )
            bridge_url = bridge.start()

            def snapshot_or_error() -> ParsedSession:
                try:
                    return build_snapshot(context)
                except Exception as exc:
                    return ParsedSession(status="error", error=str(exc))

            try:
                wait_for_window = daemon_manager is not None or launched_codex
                if wait_for_window:
                    (
                        window_ready,
                        window_status,
                        window_reason,
                        window_hwnd,
                    ) = _wait_for_visible_codex_window(
                        timeout_seconds=DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS
                    )
                    if not window_ready:
                        _LOGGER.info(
                            "renderer_hud_window_not_ready status=%s hwnd=%s reason=%s",
                            window_status,
                            window_hwnd,
                            window_reason,
                        )
                        _append_renderer_diagnostic(
                            "window_not_ready",
                            status=window_status,
                            reason=window_reason,
                            hwnd=window_hwnd,
                            display_mode=display_mode,
                            daemon_mode=True,
                            window_ready_timeout_seconds=(
                                DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS
                            ),
                        )
                        return RENDERER_HUD_UNAVAILABLE

                initial_timeout = (
                    DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS
                    if wait_for_window
                    else RENDERER_INITIAL_TIMEOUT_SECONDS
                )
                if not wait_for_renderer(
                    client,
                    snapshot_or_error,
                    timeout_seconds=initial_timeout,
                ):
                    _LOGGER.info(
                        "renderer_hud_initial_connect_failed status=%s error=%s",
                        client.last_status,
                        client.last_error,
                    )
                    _append_renderer_diagnostic(
                        "initial_connect_failed",
                        status=client.last_status,
                        error=client.last_error,
                        display_mode=display_mode,
                        daemon_mode=daemon_manager is not None,
                        initial_timeout_seconds=initial_timeout,
                        cdp_timeout_seconds=getattr(client, "timeout_seconds", None),
                    )
                    return RENDERER_HUD_UNAVAILABLE

                failures = 0
                runtime_failure_reported = False
                settings_command_status: dict[str, object] = {}
                next_daemon_check_at = 0.0
                while True:
                    started = time.monotonic()
                    if (
                        daemon_manager is not None
                        and started >= next_daemon_check_at
                    ):
                        try:
                            if not daemon_manager.codex_is_running():
                                _LOGGER.info("daemon_codex_exited")
                                return DAEMON_RESTART_REQUESTED
                            next_daemon_check_at = (
                                started + daemon_manager.poll_seconds
                            )
                        except ProcessListenerError as exc:
                            _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                            return RENDERER_HUD_UNAVAILABLE
                    command = client.take_settings_command()
                    force_fast_refresh = bool(command or settings_command_status)
                    if command:
                        settings_command_status = _handle_renderer_settings_command(
                            command,
                            context,
                            restart_requested,
                        )
                    mode_switch = str(settings_command_status.get("switchMode") or "").strip()
                    if mode_switch == "tk":
                        _LOGGER.info("renderer_hud_switch_requested mode=tk")
                        return HUD_SWITCH_TO_TK
                    if restart_requested.is_set():
                        _LOGGER.info("renderer_hud_restart_requested")
                        return (
                            DAEMON_RESTART_REQUESTED
                            if daemon_manager is not None
                            else 0
                        )
                    context.reload_user_config()
                    snapshot = snapshot_or_error()
                    if client.update(
                        snapshot,
                        settings=context.user_config,
                        active_display_mode="renderer",
                        settings_path=context.settings_store.path,
                        settings_bridge_url=bridge_url,
                        settings_command_status=settings_command_status,
                    ):
                        settings_command_status = {}
                        failures = 0
                        runtime_failure_reported = False
                    else:
                        failures += 1
                        _LOGGER.info(
                            "renderer_hud_update_failed failures=%s status=%s error=%s",
                            failures,
                            client.last_status,
                            client.last_error,
                        )
                        failure_limit = _renderer_update_failure_limit(
                            display_mode,
                            client.last_error,
                        )
                        if failures >= failure_limit:
                            if not runtime_failure_reported:
                                _append_renderer_diagnostic(
                                    "runtime_update_failed_retrying",
                                    failures=failures,
                                    failure_limit=failure_limit,
                                    status=client.last_status,
                                    error=client.last_error,
                                    display_mode=display_mode,
                                    daemon_mode=daemon_manager is not None,
                                    cdp_timeout_seconds=getattr(
                                        client, "timeout_seconds", None
                                    ),
                                )
                                runtime_failure_reported = True
                    elapsed = time.monotonic() - started
                    delay = _renderer_refresh_delay_seconds(
                        context,
                        snapshot,
                        elapsed,
                        force_fast=force_fast_refresh,
                    )
                    if failures >= _renderer_update_failure_limit(
                        display_mode,
                        client.last_error,
                    ):
                        delay = max(delay, min(5.0, failures * 0.5))
                    time.sleep(delay)
            except KeyboardInterrupt:
                return 130
            finally:
                client.close()
                bridge.close()
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def run_tk_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
) -> int:
    """Run one Tk HUD session with optional daemon process supervision."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            try:
                try:
                    window = TokenHudWindow(
                        compact=args.compact,
                        hide_until_attached=hide_until_attached,
                        tombstone_follow_ms=(
                            100 if daemon_manager is not None else 500
                        ),
                        user_settings_store=getattr(context, "settings_store", None),
                    )
                except Exception as exc:
                    _eprint(f"codex-usage-hud: unable to open Tkinter HUD: {exc}")
                    return 1

                def refresh() -> None:
                    if window.should_refresh_snapshot():
                        try:
                            context.reload_user_config()
                            snapshot = build_snapshot(context)
                        except Exception as exc:
                            snapshot = ParsedSession(status="error", error=str(exc))
                        window.update_display(snapshot)
                    try:
                        window.root.after(window.refresh_delay_ms(context.poll_ms), refresh)
                    except Exception:
                        return

                def daemon_watchdog() -> None:
                    if daemon_manager is None:
                        return
                    try:
                        if not daemon_manager.codex_is_running():
                            _LOGGER.info("daemon_codex_exited")
                            window.close("daemon_codex_exited")
                            return
                    except ProcessListenerError as exc:
                        _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                        return
                    try:
                        window.root.after(daemon_manager.poll_ms, daemon_watchdog)
                    except Exception:
                        return

                refresh()
                if daemon_manager is not None:
                    window.root.after(daemon_manager.poll_ms, daemon_watchdog)
                window.run()
                if daemon_manager is not None and window.exit_reason == "daemon_codex_exited":
                    return DAEMON_RESTART_REQUESTED
                if getattr(window, "mode_switch_request", "") == "renderer":
                    if getattr(window, "restart_codex_for_renderer", False):
                        return HUD_SWITCH_TO_RENDERER_RESTART_CODEX
                    return HUD_SWITCH_TO_RENDERER
                return 0
            finally:
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def run_daemon(args: argparse.Namespace) -> int:
    """Run the hidden Windows daemon manager, falling back when unsupported."""
    configure_daemon_logging()
    _attach_cli_logger_to_daemon_log()
    hide_console_window()
    manager = CodexDaemonManager(poll_ms=args.daemon_poll_ms)
    preferred_renderer = bool(getattr(args, "renderer_hud", False))
    try:
        with HudInstanceLock():
            try:
                startup = _daemon_startup_decision(args, manager)
            except KeyboardInterrupt:
                return 130
            except ProcessListenerError as exc:
                _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                return run_hud_session(
                    args,
                    lock_already_held=True,
                    hide_until_attached=False,
                )

            if startup.mode == DAEMON_STARTUP_CANCEL:
                _LOGGER.info("daemon_startup_cancelled")
                return 0
            if startup.mode == DAEMON_STARTUP_TK:
                if startup.launch_codex:
                    launch_codex_app(debugger=False)
                _LOGGER.info("daemon_startup_tk_selected")
                preferred_renderer = False
                return run_hud_session(
                    _clone_args_with_renderer_preference(args, False),
                    lock_already_held=True,
                    hide_until_attached=False,
                    daemon_manager=manager,
                )
            if startup.mode == DAEMON_STARTUP_RENDERER and startup.launch_codex:
                launch_codex_app(debugger=True)
                _LOGGER.info("daemon_startup_renderer_selected")
            if startup.mode == DAEMON_STARTUP_RENDERER:
                preferred_renderer = True
            force_renderer_retry = startup.mode == DAEMON_STARTUP_RENDERER

            while True:
                try:
                    manager.wait_for_codex()
                except KeyboardInterrupt:
                    return 130
                except ProcessListenerError as exc:
                    _LOGGER.exception("daemon_listener_failed fallback=%s", exc)
                    return run_hud_session(
                        args,
                        lock_already_held=True,
                        hide_until_attached=False,
                    )
                if force_renderer_retry:
                    exit_code = run_renderer_hud_session(
                        _clone_args_with_renderer_preference(args, True),
                        lock_already_held=True,
                        daemon_manager=manager,
                    )
                else:
                    exit_code = run_hud_session(
                        _clone_args_with_renderer_preference(args, preferred_renderer),
                        lock_already_held=True,
                        hide_until_attached=True,
                        daemon_manager=manager,
                    )
                if exit_code == HUD_SWITCH_TO_TK:
                    preferred_renderer = False
                    force_renderer_retry = False
                    continue
                if exit_code == DAEMON_RESTART_REQUESTED:
                    _LOGGER.info("daemon_restarting_wait_for_codex")
                    continue
                if force_renderer_retry and exit_code == RENDERER_HUD_UNAVAILABLE:
                    _LOGGER.info("daemon_renderer_unavailable_retrying")
                    time.sleep(manager.poll_seconds)
                    continue
                return exit_code
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entry point."""
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check_update:
        return run_update_check()
    if args.update:
        return run_update_install()
    if args.renderer_hud is None:
        configured_mode = normalize_display_mode(
            args.hud_mode or UserConfigStore().load().display_mode
        )
        args.renderer_hud = configured_mode != "tk"
    if args.stop:
        print(stop_running_hud())
        return 0
    if args.daemon and args.once:
        parser.error("--daemon cannot be combined with --once")

    if args.once:
        return run_once_snapshot(args)

    if args.daemon:
        return run_daemon(args)

    return run_hud_session(args)


if __name__ == "__main__":
    raise SystemExit(main())
