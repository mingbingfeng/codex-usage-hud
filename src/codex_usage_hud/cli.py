"""Command-line interface for codex-usage-hud."""

from __future__ import annotations

import argparse
import copy
from contextlib import nullcontext
import importlib.metadata as importlib_metadata
import importlib.util
import json
import logging
import os
import queue
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from . import __version__
from .background_usage_runtime import (
    BACKGROUND_USAGE_DATABASE_FILENAME,
    BackgroundUsageRuntime,
)
from .config import (
    DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED,
    DEFAULT_BUDGET_THRESHOLDS,
    DEFAULT_DAILY_BUDGET_USD,
    DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    DEFAULT_WEEKLY_BUDGET_USD,
    UserConfig,
    UserConfigStore,
    dismiss_warning_for_today,
    effective_display_mode,
    fetch_model_prices,
    normalize_display_mode,
    normalize_work_overlay_max_items,
    parse_thresholds as parse_config_thresholds,
    time_parts,
    write_json_object,
)
from .core import (
    BaseEstimate,
    CostEstimator,
    JsonlSessionParser,
    JsonlTailState,
    ParsedSession,
    PreSendEstimator,
    ReadingActivity,
    RequestRound,
    SseRequestStateMachine,
    UsageCalculator,
    UsageSummary,
    WorkStatusItem,
    detect_reading_activity,
    extract_session_thread_identity,
    message_text,
)
from .core.connection_health import ConnectionHealth
from .core.rest_reminder import RestReminderPresenter
from .core.runtime_events import RuntimeEvent, RuntimeEventBus
from .core.background_usage import BACKGROUND_USAGE_KIND, background_feature_label
from .core.runtime_errors import RuntimeErrorRegistry
from .core.deleted_usage import (
    DeletedUsageEvent,
    DeletedUsageLedger,
    DeletedUsageLedgerError,
)
from .core.session_cleanup import (
    SessionCleanupError,
    SessionCleanupItem,
    SessionCleanupManager,
)
from .daemon import (
    CodexDaemonManager,
    DEFAULT_DAEMON_POLL_MS,
    MAX_DAEMON_POLL_MS,
    ProcessListenerError,
    WindowsProcessListener,
    configure_daemon_logging,
    hide_console_window,
    is_codex_client_process,
)
from .platforms import (
    ActiveSessionTracker,
    CodexWindowTracker,
    CdpSessionSwitchBackend,
    SessionPathResolver,
    SessionSwitchController,
    SessionSwitchResult,
    WindowsSearchSessionSwitchBackend,
    get_current_platform,
    is_new_session_source,
    is_pending_session_source,
)
from .platforms.base import BasePlatform
from .platforms.cdp_probe import (
    CDP_PORT_ENV,
    DEFAULT_CDP_PORT,
    cdp_version_info,
    cdp_port_from_env,
    list_targets,
    pick_page_target,
)
from .platforms.codex_theme import CodexThemeProbe
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec
from .provider_registry import ProviderRegistry, discover_provider_registry
from .settings_bridge import SettingsBridgeServer
from .ui.renderer_hud import (
    RendererHudClient,
    payload_from_snapshot,
    remove_renderer_hud_from_pages,
    session_switch_payload_from_snapshot,
    wait_for_renderer,
)
from .updater import (
    AutoUpdateManager,
    check_for_update,
    download_update_asset,
    format_update_info,
    launch_installer,
)

DEFAULT_POLL_MS = 500
WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS = 5.0
WORK_OVERLAY_CDP_SWITCH_TIMEOUT_SECONDS = 3.0
WORK_OVERLAY_WINDOW_PREPARE_TIMEOUT_SECONDS = 0.8
WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS = 0.8
WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS = 0.08
WORK_OVERLAY_SWITCH_COMPLETED_HOLD_SECONDS = 1.4
WORK_OVERLAY_CURRENT_SESSION_REFOCUS_DELAY_SECONDS = (
    WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS
)
DEFAULT_SQLITE_LOG = "logs_2.sqlite"
DEFAULT_STATE_DB = "state_5.sqlite"
DEFAULT_SESSION_INDEX = "session_index.jsonl"
DEFAULT_BUDGET_THRESHOLDS_TEXT = ",".join(f"{item:g}" for item in DEFAULT_BUDGET_THRESHOLDS)
DEFAULT_ACTIVE_SESSION_POLL_MS = 500
DEFAULT_AUTO_SWITCH_IDLE_SECONDS = 30.0
NATIVE_SEARCH_SESSION_SWITCH_ENV = "CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH"
DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS = 2.0
USAGE_INSIGHTS_TOP_SESSION_LIMIT = 10
RENDERER_IDLE_POLL_MS = 1500
RENDERER_FILE_WATCHER_FALLBACK_SECONDS = 5.0
RENDERER_FILE_EVENT_DEBOUNCE_SECONDS = 0.75
RENDERER_EVENT_IDLE_WAIT_SECONDS = 30.0
RENDERER_ACTIVE_WORK_RESCAN_SECONDS = 5.0
RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS = 1.2
HUD_LOCK_FILENAME = "codex_usage_hud.pid"
HUD_MUTEX_NAME = "Local\\codex_usage_hud_single_instance"
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
DAEMON_RESTART_REQUESTED = 10
RENDERER_HUD_UNAVAILABLE = 20
HUD_SWITCH_TO_RENDERER = 31
HUD_SWITCH_TO_RENDERER_RESTART_CODEX = 32
HUD_AUTO_RESTART_CODEX = 33
RENDERER_CDP_TIMEOUT_SECONDS = 0.35
BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS = (0.15, 0.35, 0.75)
DAEMON_RENDERER_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_INITIAL_TIMEOUT_SECONDS = 0.75
RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS = 2.0
DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS = 10.0
# A just-restarted Codex needs a materially longer CDP readiness window than
# an already-running instance. Keep this bounded so a failed restart still
# returns control instead of relaunching indefinitely.
RENDERER_RESTART_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_RESTART_INITIAL_TIMEOUT_SECONDS = 30.0
DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS = 15.0
RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS = 0.35
RENDERER_STARTUP_STEP_MIN_VISIBLE_SECONDS = 0.45
RENDERER_SLOW_OPERATION_LOG_MS = 250.0
RENDERER_COLD_SESSION_PREVIEW_BYTES = 256 * 1024
RENDERER_SESSION_SNAPSHOT_CACHE_SIZE = 3
RENDERER_UPDATE_FAILURE_LIMIT = 6
AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT = 3
RENDERER_DIAGNOSTIC_FILENAME = "renderer_fallback.log"
RENDERER_CDP_STATE_FILENAME = "renderer_cdp_state.json"
RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS = 0.25
CRASH_DIAGNOSTIC_FILENAME = "crash.log"
CRASH_DIAGNOSTICS_ENV = "CODEX_USAGE_HUD_CRASH_DIAGNOSTICS"
RUNTIME_DEBUG_ENV = "CODEX_USAGE_HUD_DEBUG"
FORCE_DESKTOP_OVERLAY_MISSING_ENV = "CODEX_USAGE_HUD_FORCE_DESKTOP_OVERLAY_MISSING"
CODEX_APP_PATH_ENV = "CODEX_USAGE_HUD_CODEX_APP"
CODEX_APP_ID_ENV = "CODEX_USAGE_HUD_CODEX_APP_ID"
CODEX_APP_DEFAULT_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
DAEMON_STARTUP_WAIT = "wait"
DAEMON_STARTUP_RENDERER = "renderer"
DAEMON_STARTUP_CANCEL = "cancel"
RENDERER_STARTUP_LAUNCH = "launch"
RENDERER_STARTUP_ATTACH = "attach"
RENDERER_STARTUP_RESTART_REQUIRED = "restart-required"
RENDERER_STARTUP_ATTACH_LAUNCHED = "attach-launched"
RENDERER_STARTUP_ATTACH_OBSERVED = "attach-observed"
RENDERER_STARTUP_RELAUNCH_OBSERVED = "relaunch-observed"
_REMOTE_DEBUGGING_PORT_PATTERN = re.compile(
    r"(?:^|\s)--remote-debugging-port(?:=|\s+)(\d{1,5})(?=\s|$)"
)
LOADING_FEEDBACK_STALE_SECONDS = 20.0
ACTIVE_WORK_ITEM_LIMIT = DEFAULT_WORK_OVERLAY_MAX_ITEMS
ACTIVE_WORK_CANDIDATE_LIMIT = 16
ACTIVE_WORK_STALE_SECONDS = 4 * 60 * 60
ACTIVE_WORK_MODEL_STARTUP_STALE_SECONDS = 90.0
FINAL_ANSWER_COMPLETION_GRACE_SECONDS = 1.0
VISIBLE_APP_ERROR_HOLD_SECONDS = 60.0
WORK_OVERLAY_STALE_SECONDS = 20.0
WORK_OVERLAY_KEEPALIVE_SECONDS = 15.0
WORK_OVERLAY_ALPHA = 0.88
# Let the work bubble fade far enough on hover to keep content beneath it readable.
WORK_OVERLAY_HOVER_ALPHA = 0.22
WORK_OVERLAY_HEADER_TITLE_LIMIT = 28
WORK_OVERLAY_RESTART_BACKOFF_SECONDS = 60.0
WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS = 35.0
WORK_OVERLAY_HELPER_MAX_USER_OBJECTS = 2_000
WORK_OVERLAY_TOP_OFFSET = 56
WORK_OVERLAY_MARGIN = 16
WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT = 160
WORK_OVERLAY_RESTART_ACTION = "restartCodex"
WORK_OVERLAY_SYSTEM_ACTION_READY = "systemActionReady"
WORK_OVERLAY_RESTART_ACTION_ID = "restart-codex-for-renderer"
WORK_OVERLAY_SYSTEM_ACTION_READY_TIMEOUT_SECONDS = 2.0
DELETED_SESSION_USAGE_FILENAME = "deleted-session-usage.json"
DESKTOP_OVERLAY_PACKAGE = "PySide6"
DESKTOP_OVERLAY_PIP_SPEC = "PySide6>=6.8"
_LOGGER = logging.getLogger("codex_usage_hud.cli")
_cli_daemon_logging_attached = False
_CRASH_DIAGNOSTIC_FILE: Any | None = None
_DESKTOP_OVERLAY_INSTALL_PROCESS: subprocess.Popen[Any] | None = None
_FORCE_DESKTOP_OVERLAY_MISSING = False


def _work_overlay_helper_qt() -> Any:
    from .ui.work_overlay_qt import run_work_overlay_helper_qt

    return run_work_overlay_helper_qt


def _work_overlay_max_items_for_screen_height(screen_height: int) -> int:
    available_height = max(
        1,
        int(screen_height) - WORK_OVERLAY_TOP_OFFSET - (WORK_OVERLAY_MARGIN * 2),
    )
    return max(1, available_height // WORK_OVERLAY_ESTIMATED_ITEM_HEIGHT)


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _force_desktop_overlay_missing() -> bool:
    return bool(_FORCE_DESKTOP_OVERLAY_MISSING)


def _set_force_desktop_overlay_missing(enabled: bool) -> None:
    global _FORCE_DESKTOP_OVERLAY_MISSING
    _FORCE_DESKTOP_OVERLAY_MISSING = bool(enabled)


def _init_force_desktop_overlay_missing_from_env() -> None:
    _set_force_desktop_overlay_missing(
        _env_flag_enabled(FORCE_DESKTOP_OVERLAY_MISSING_ENV)
    )


def _pyside6_runtime_available(*, honor_force: bool = True) -> bool:
    if honor_force and _force_desktop_overlay_missing():
        return False
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec(DESKTOP_OVERLAY_PACKAGE) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _pyside6_version() -> str:
    try:
        return importlib_metadata.version(DESKTOP_OVERLAY_PACKAGE)
    except importlib_metadata.PackageNotFoundError:
        return ""
    except Exception:
        return ""


def _desktop_overlay_install_running() -> bool:
    global _DESKTOP_OVERLAY_INSTALL_PROCESS
    process = _DESKTOP_OVERLAY_INSTALL_PROCESS
    if process is None:
        return False
    if process.poll() is None:
        return True
    _DESKTOP_OVERLAY_INSTALL_PROCESS = None
    # Install finished: if the package is now present, stop simulating missing.
    if _pyside6_runtime_available(honor_force=False):
        _set_force_desktop_overlay_missing(False)
    return False


def _desktop_overlay_can_install() -> bool:
    return bool(sys.executable) and not bool(getattr(sys, "frozen", False))


def _desktop_overlay_dependency_status() -> dict[str, object]:
    real_installed = _pyside6_runtime_available(honor_force=False)
    # Forced-missing simulation reports not installed until install/enable clears it.
    installed = real_installed and not _force_desktop_overlay_missing()
    version = _pyside6_version() if real_installed else ""
    can_install = _desktop_overlay_can_install()
    installing = _desktop_overlay_install_running()
    requires_restart = bool(getattr(sys, "frozen", False)) and not installed
    install_command = f"{Path(sys.executable).name} -m pip install \"{DESKTOP_OVERLAY_PIP_SPEC}\""
    return {
        "package": DESKTOP_OVERLAY_PACKAGE,
        "installed": installed,
        "version": version if installed else "",
        "canInstall": can_install,
        "installing": installing,
        "requiresRestart": requires_restart,
        "canEnableNow": not requires_restart,
        "installCommand": install_command,
        "forcedMissing": _force_desktop_overlay_missing(),
        "realInstalled": real_installed,
    }


def _start_desktop_overlay_install() -> bool:
    global _DESKTOP_OVERLAY_INSTALL_PROCESS
    if _desktop_overlay_install_running():
        return True
    if not _desktop_overlay_can_install():
        return False
    # Simulated missing + real package present: clear force immediately so the next
    # status poll shows installed without a redundant pip install.
    if _force_desktop_overlay_missing() and _pyside6_runtime_available(honor_force=False):
        _set_force_desktop_overlay_missing(False)
        return True
    try:
        _DESKTOP_OVERLAY_INSTALL_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", DESKTOP_OVERLAY_PIP_SPEC],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        _DESKTOP_OVERLAY_INSTALL_PROCESS = None
        return False
    return True


class HudAlreadyRunningError(RuntimeError):
    """Raised when another HUD instance owns the local runtime lock."""


@dataclass(frozen=True)
class DaemonStartupDecision:
    """How daemon startup should continue when Codex is not already visible."""

    mode: str
    launch_codex: bool = False


@dataclass(frozen=True)
class RendererStartupPlan:
    """One evidence-backed action for the renderer startup state machine."""

    scenario: str
    port: int | None = None
    port_source: str = ""
    reason: str = ""


@dataclass(frozen=True)
class _CodexDesktopProcess:
    pid: int
    name: str
    executable_path: str
    command_line: str


@dataclass(frozen=True)
class _RendererCdpPortCandidate:
    port: int
    source: str
    pid: int | None = None


class HudLoadingFeedback:
    """Small topmost startup/loading card for renderer launch and recovery."""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        enabled: bool,
    ) -> None:
        self.title = str(title or "")
        self.message = str(message or "")
        self.enabled = bool(enabled)
        self._process: subprocess.Popen[str] | None = None
        self._state_path: Path | None = None
        self._restart_request_path: Path | None = None
        self._restart_visible = False
        self._closed = False

    def start(self) -> "HudLoadingFeedback":
        if not self.enabled or self._process is not None:
            return self
        state_path = hud_runtime_dir() / f"loading-{os.getpid()}-{int(time.time() * 1000)}.json"
        self._state_path = state_path
        self._restart_request_path = _loading_feedback_restart_path(state_path)
        self._write_state(close=False)
        try:
            self._process = subprocess.Popen(
                _loading_helper_command(state_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._process = None
            try:
                state_path.unlink()
            except OSError:
                pass
        return self

    def update(
        self,
        *,
        title: str | None = None,
        message: str | None = None,
    ) -> None:
        if not self.enabled or self._closed:
            return
        self.title = self.title if title is None else str(title)
        self.message = self.message if message is None else str(message)
        self._write_state(close=False)

    def offer_codex_restart(
        self,
        *,
        title: str,
        message: str,
    ) -> bool:
        """Keep the launch card open until the user explicitly requests restart."""
        if not self.enabled or self._closed:
            return False
        self._restart_visible = True
        self.title = str(title)
        self.message = str(message)
        self._write_state(close=False)
        return self._process is not None

    def take_codex_restart_request(self) -> bool:
        """Consume the restart click written by the lightweight launch card."""
        path = self._restart_request_path
        if path is None or self._closed:
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        try:
            path.unlink()
        except OSError:
            pass
        return isinstance(payload, Mapping) and payload.get("action") == "restart_codex"

    def wait_for_codex_restart_request(self) -> bool:
        """Wait without automatic restart while the user finishes current work."""
        if not self.enabled or self._closed:
            return False
        process = self._process
        path = self._restart_request_path
        if process is None or path is None:
            return False
        wake = Event()
        requested = False
        requested_lock = threading.Lock()

        def consume_request() -> None:
            nonlocal requested
            if not self.take_codex_restart_request():
                return
            with requested_lock:
                requested = True
            wake.set()

        watcher = FileChangeWatcher(
            lambda _reasons, _paths: consume_request(),
            fallback_poll_seconds=WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS,
        )
        try:
            watcher.update([FileWatchSpec.file(path, "loading-feedback-restart")])
            consume_request()

            def wait_for_helper_exit() -> None:
                try:
                    process.wait()
                except Exception:
                    pass
                wake.set()

            threading.Thread(
                target=wait_for_helper_exit,
                name="codex-hud-loading-action-exit",
                daemon=True,
            ).start()
            wake.wait()
            consume_request()
        finally:
            watcher.close()
        with requested_lock:
            return requested

    def close(self) -> None:
        if not self.enabled or self._closed:
            return
        self._closed = True
        self._write_state(close=True)
        process = self._process
        if process is not None:
            try:
                process.wait(timeout=1.5)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        if self._state_path is not None:
            try:
                self._state_path.unlink()
            except OSError:
                pass
        if self._restart_request_path is not None:
            try:
                self._restart_request_path.unlink()
            except OSError:
                pass

    def _write_state(self, *, close: bool) -> None:
        if self._state_path is None:
            return
        try:
            write_json_object(
                self._state_path,
                {
                    "ownerPid": os.getpid(),
                    "title": self.title,
                    "message": self.message,
                    "restartVisible": self._restart_visible,
                    "updatedAt": time.time(),
                    "close": bool(close),
                },
            )
        except OSError:
            return


def _loading_feedback_enabled(args: argparse.Namespace | None = None) -> bool:
    # ``--no-startup-prompt`` used to suppress a modal startup choice.  The
    # renderer-only flow no longer has that choice: this lightweight status
    # card is the only place where an already-running, non-CDP Codex asks for
    # an explicit restart.
    del args
    return sys.platform.startswith("win") or sys.platform == "darwin"


def _create_loading_feedback(
    args: argparse.Namespace | None,
    *,
    title: str,
    message: str,
) -> HudLoadingFeedback:
    return HudLoadingFeedback(
        title=title,
        message=message,
        enabled=_loading_feedback_enabled(args),
    )


def _loading_helper_command(state_path: Path) -> list[str]:
    state_arg = str(state_path)
    if getattr(sys, "frozen", False):
        return [
            str(Path(sys.executable)),
            "--loading-feedback-helper",
            "--loading-feedback-state-file",
            state_arg,
        ]

    helper_python = Path(sys.executable)
    if helper_python.name.lower() == "python.exe":
        candidate = helper_python.with_name("pythonw.exe")
        if candidate.exists():
            helper_python = candidate
    return [
        str(helper_python),
        "-m",
        "codex_usage_hud",
        "--loading-feedback-helper",
        "--loading-feedback-state-file",
        state_arg,
    ]


def _loading_feedback_restart_path(state_path: Path) -> Path:
    """Return the one-shot user-action file owned by a launch feedback card."""
    return state_path.with_name(f"{state_path.stem}-restart.json")


def _loading_feedback_top_right_geometry(
    *,
    screen_width: int,
    screen_height: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Place the helper where the in-renderer startup bubble normally sits."""
    right = 18
    top = 72
    x = max(0, int(screen_width) - int(width) - right)
    y = max(0, min(top, int(screen_height) - int(height)))
    return x, y


def _loading_feedback_owner_pid(path: Path) -> int | None:
    match = re.match(r"loading-(\d+)-\d+\.json$", path.name, re.IGNORECASE)
    if not match:
        return None
    try:
        pid = int(match.group(1))
    except ValueError:
        return None
    return pid if pid > 0 else None


def cleanup_stale_loading_feedback_files() -> None:
    runtime = hud_runtime_dir()
    try:
        files = list(runtime.glob("loading-*.json"))
    except OSError:
        return
    now = time.time()
    for path in files:
        owner_pid = _loading_feedback_owner_pid(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        stale = (now - float(mtime)) > LOADING_FEEDBACK_STALE_SECONDS
        owner_alive = _process_exists(owner_pid) if owner_pid is not None else False
        # A live owner is authoritative. In particular, the restart card is a
        # deliberate user-wait state and must not disappear after the generic
        # startup staleness window.
        if owner_pid is not None and owner_alive:
            continue
        if owner_pid is None and not stale:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        try:
            _loading_feedback_restart_path(path).unlink()
        except OSError:
            pass
        try:
            _work_overlay_command_path(path).unlink()
        except OSError:
            continue


def run_loading_feedback_helper(state_file: str | Path) -> int:
    state_arg = str(state_file or "").strip()
    if not state_arg:
        return 1
    path = Path(state_arg).expanduser()
    try:
        import tkinter as tk
    except Exception:
        return 0

    def read_state() -> dict[str, object] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#081018")
    root.withdraw()

    shell = tk.Frame(
        root,
        bg="#10161D",
        highlightthickness=1,
        highlightbackground="#263241",
        padx=14,
        pady=13,
    )
    shell.pack(fill="both", expand=True)

    title_var = tk.StringVar(value="")
    message_var = tk.StringVar(value="")

    tk.Label(
        shell,
        textvariable=title_var,
        anchor="center",
        justify="center",
        bg="#10161D",
        fg="#F3D27A",
        font=("Microsoft YaHei UI", 11, "bold"),
        pady=2,
    ).pack(fill="x")
    tk.Label(
        shell,
        textvariable=message_var,
        anchor="center",
        justify="center",
        bg="#10161D",
        fg="#8492A6",
        font=("Microsoft YaHei UI", 9),
        wraplength=196,
    ).pack(fill="x")

    track = tk.Canvas(
        shell,
        width=196,
        height=6,
        bg="#10161D",
        highlightthickness=0,
        bd=0,
    )
    track.pack(fill="x", pady=(10, 0))
    track.create_rectangle(0, 0, 196, 6, fill="#1A2430", outline="")
    indicator = track.create_rectangle(0, 0, 58, 6, fill="#F3D27A", outline="")
    accent = track.create_rectangle(0, 0, 30, 6, fill="#FFE7A0", outline="")

    restart_path = _loading_feedback_restart_path(path)
    restart_button = tk.Button(
        shell,
        text="重启 Codex",
        anchor="center",
        bg="#F3D27A",
        fg="#10161D",
        activebackground="#FFE7A0",
        activeforeground="#10161D",
        relief="flat",
        bd=0,
        padx=10,
        pady=5,
        font=("Microsoft YaHei UI", 9, "bold"),
    )

    def request_restart() -> None:
        try:
            write_json_object(
                restart_path,
                {"action": "restart_codex", "requestedAt": time.time()},
            )
        except OSError:
            return
        restart_button.configure(state="disabled", text="正在重启…")

    restart_button.configure(command=request_restart)

    root.update_idletasks()
    width = max(228, int(root.winfo_reqwidth()))
    height = max(118, int(root.winfo_reqheight()))
    screen_width = max(1, int(root.winfo_screenwidth()))
    screen_height = max(1, int(root.winfo_screenheight()))
    x, y = _loading_feedback_top_right_geometry(
        screen_width=screen_width,
        screen_height=screen_height,
        width=width,
        height=height,
    )
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.deiconify()

    position = 0
    direction = 1
    last_signature = ("", "", False, False)
    owner_pid = _loading_feedback_owner_pid(path)

    def animate_bar() -> None:
        nonlocal position, direction
        if not root.winfo_exists():
            return
        position += 7 * direction
        if position >= 138:
            position = 138
            direction = -1
        elif position <= 0:
            position = 0
            direction = 1
        track.coords(indicator, position, 0, position + 58, 6)
        track.coords(accent, position + 12, 0, position + 42, 6)
        root.after(34, animate_bar)

    def poll_state() -> None:
        nonlocal last_signature
        if not root.winfo_exists():
            return
        state = read_state()
        if state is None:
            root.destroy()
            return
        title = str(state.get("title") or "")
        message = str(state.get("message") or "")
        should_close = bool(state.get("close"))
        restart_visible = bool(state.get("restartVisible"))
        updated_at = float(state.get("updatedAt") or 0.0)
        file_stale = updated_at > 0 and (time.time() - updated_at) > LOADING_FEEDBACK_STALE_SECONDS
        owner_alive = owner_pid is not None and _process_exists(owner_pid)
        if owner_pid is not None and not owner_alive:
            root.destroy()
            return
        if file_stale and not owner_alive:
            root.destroy()
            return
        signature = (title, message, should_close, restart_visible)
        if signature != last_signature:
            last_signature = signature
            title_var.set(title)
            message_var.set(message)
            if restart_visible:
                restart_button.pack(fill="x", pady=(10, 0))
            else:
                restart_button.pack_forget()
            root.update_idletasks()
            width = max(228, int(root.winfo_reqwidth()))
            height = max(118, int(root.winfo_reqheight()))
            x, y = _loading_feedback_top_right_geometry(
                screen_width=screen_width,
                screen_height=screen_height,
                width=width,
                height=height,
            )
            root.geometry(f"{width}x{height}+{x}+{y}")
        if should_close:
            root.destroy()
            return
        root.after(80, poll_state)

    animate_bar()
    poll_state()
    root.mainloop()
    return 0


def _work_overlay_command(state_path: Path) -> list[str]:
    state_arg = str(state_path)
    if getattr(sys, "frozen", False):
        return [
            str(Path(sys.executable)),
            "--work-overlay-helper",
            "--work-overlay-state-file",
            state_arg,
        ]

    helper_python = Path(sys.executable)
    if sys.platform.startswith("win") and helper_python.name.lower() == "python.exe":
        candidate = helper_python.with_name("pythonw.exe")
        if candidate.exists():
            helper_python = candidate
    return [
        str(helper_python),
        "-m",
        "codex_usage_hud",
        "--work-overlay-helper",
        "--work-overlay-state-file",
        state_arg,
    ]










def _work_overlay_owner_pid(path: Path) -> int | None:
    match = re.match(r"work-overlay-(\d+)-\d+\.json$", path.name, re.IGNORECASE)
    if not match:
        return None
    try:
        pid = int(match.group(1))
    except ValueError:
        return None
    return pid if pid > 0 else None


def cleanup_stale_work_overlay_files() -> None:
    runtime = hud_runtime_dir()
    try:
        files = list(runtime.glob("work-overlay-*.json"))
    except OSError:
        return
    now = time.time()
    for path in files:
        owner_pid = _work_overlay_owner_pid(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        stale = (now - float(mtime)) > WORK_OVERLAY_STALE_SECONDS
        owner_alive = _process_exists(owner_pid) if owner_pid is not None else False
        if owner_pid is not None and owner_alive and not stale:
            continue
        if owner_pid is None and not stale:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _work_overlay_command_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-commands.jsonl")


def _work_overlay_heartbeat_path(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.stem}-heartbeat")


def _windows_user_object_count(process: subprocess.Popen[Any]) -> int | None:
    """Return native USER object usage for a Windows child process when available."""
    if os.name != "nt":
        return None
    handle = getattr(process, "_handle", None)
    if not handle:
        return None
    try:
        import ctypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_gui_resources = user32.GetGuiResources
        get_gui_resources.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        get_gui_resources.restype = ctypes.c_uint
        return int(get_gui_resources(ctypes.c_void_p(handle), 1))
    except Exception:
        return None


def _work_overlay_transition_audit_path() -> Path:
    return hud_runtime_dir() / "work-overlay-transitions.jsonl"


def _overlay_payload_status(item: Mapping[str, object]) -> str:
    return str(item.get("status") or "").strip()


def _overlay_payload_pending_accounting(item: Mapping[str, object]) -> bool:
    return bool(item.get("pendingAccounting"))


def _overlay_payload_kind(item: Mapping[str, object]) -> str:
    return "completed" if _overlay_payload_status(item) == "recent" else "card"


def _overlay_payload_transition_name(
    old_item: Mapping[str, object],
    new_item: Mapping[str, object],
) -> str:
    old_kind = _overlay_payload_kind(old_item)
    new_kind = _overlay_payload_kind(new_item)
    if old_kind == "card" and new_kind == "completed":
        return "card_to_completed"
    if old_kind == "completed" and new_kind == "card":
        return "completed_to_card"
    if (
        old_kind == "completed"
        and new_kind == "completed"
        and _overlay_payload_pending_accounting(old_item)
        and not _overlay_payload_pending_accounting(new_item)
    ):
        return "accounting_finalized"
    return "status_changed"


def work_item_to_overlay_dict(item: WorkStatusItem) -> dict[str, object]:
    return {
        "kind": item.kind,
        "eventId": item.event_id,
        "id": item.id,
        "title": item.title,
        "sessionId": item.session_id,
        "targetTitle": item.target_title,
        "roundIndex": item.round_index,
        "modelName": item.model_name,
        "status": item.status,
        "statusLabel": item.status_label,
        "detail": item.detail,
        "statusText": item.status_text,
        "lastText": item.last_text,
        "elapsedText": item.elapsed_text,
        "progress": item.progress,
        "tokensText": item.tokens_text,
        "costText": item.cost_text,
        "cacheHitText": item.cache_hit_text,
        "workdirName": item.workdir_name,
        "source": item.source,
        "workdir": item.workdir,
        "modelProvider": item.model_provider,
        "clientKind": item.client_kind,
        "sessionStartedAt": _iso_or_empty(item.session_started_at),
        "taskStartedAt": _iso_or_empty(item.task_started_at),
        "startedAt": _iso_or_empty(item.started_at),
        "updatedAt": _iso_or_empty(item.updated_at),
        "current": item.current,
        "pendingAccounting": item.pending_accounting,
    }


def background_usage_to_work_item(
    summary: Mapping[str, object],
) -> WorkStatusItem | None:
    """Project one typed audit summary into the existing desktop helper payload."""
    event_id = str(summary.get("eventId") or "").strip()
    if not event_id:
        return None
    models_value = summary.get("models")
    models = (
        [str(value).strip() for value in models_value if str(value).strip()]
        if isinstance(models_value, Sequence) and not isinstance(models_value, str)
        else []
    )
    model_name = " + ".join(models[:2])
    if len(models) > 2:
        model_name = f"{model_name} +{len(models) - 2}"
    request_count = max(0, int(summary.get("requestCount") or 0))
    total_tokens = max(0, int(summary.get("totalTokens") or 0))
    cost_value = summary.get("estimatedCostUsd")
    try:
        estimated_cost = float(cost_value) if cost_value is not None else None
    except (TypeError, ValueError):
        estimated_cost = None
    updated_at: datetime | None = None
    updated_text = str(summary.get("lastSeenAt") or "").strip()
    if updated_text:
        try:
            updated_at = datetime.fromisoformat(updated_text.replace("Z", "+00:00"))
        except ValueError:
            updated_at = None
    feature_label = background_feature_label(
        summary.get("featureKey"),
        summary.get("featureLabel"),
    )
    endpoint = str(summary.get("endpoint") or "/responses").strip()
    return WorkStatusItem(
        id=event_id,
        event_id=event_id,
        kind=BACKGROUND_USAGE_KIND,
        title=feature_label,
        status=BACKGROUND_USAGE_KIND,
        status_label=f"Codex App 后台任务：{feature_label}",
        detail=f"{request_count} 次 API 请求",
        model_name=model_name,
        status_text=f"{request_count} 次请求",
        last_text=endpoint,
        progress=f"{request_count} 次 API 请求",
        tokens_text=_format_tokens(total_tokens),
        cost_text=(
            f"估算 {_format_cost_compact(estimated_cost)}"
            if estimated_cost is not None
            else "估算不可用"
        ),
        workdir_name="查看后台用量记录",
        source=BACKGROUND_USAGE_KIND,
        workdir=str(summary.get("cwd") or "").strip(),
        model_provider=str(summary.get("provider") or "unknown").strip() or "unknown",
        client_kind="app",
        updated_at=updated_at,
    )


def _background_usage_work_items(context: object) -> list[WorkStatusItem]:
    runtime = getattr(context, "background_usage_runtime", None)
    pending_today = getattr(runtime, "pending_today", None)
    if not callable(pending_today):
        return []
    try:
        summaries = pending_today()
    except Exception as exc:
        _LOGGER.debug("background_usage_overlay_query_failed error=%s", exc)
        return []
    items: list[WorkStatusItem] = []
    for summary in summaries:
        if not isinstance(summary, Mapping):
            continue
        item = background_usage_to_work_item(summary)
        if item is not None:
            items.append(item)
    return items


def _background_usage_notification_for_session(
    context: object,
    session_id: object,
) -> dict[str, object]:
    runtime = getattr(context, "background_usage_runtime", None)
    notification_for_session = getattr(runtime, "notification_for_session", None)
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id or not callable(notification_for_session):
        return {}
    try:
        raw = notification_for_session(normalized_session_id)
    except Exception as exc:
        _LOGGER.debug(
            "background_usage_notification_query_failed session_id=%s error=%s",
            normalized_session_id,
            exc,
        )
        return {}
    if not isinstance(raw, Mapping):
        return {}
    try:
        count = max(0, int(raw.get("count") or 0))
    except (TypeError, ValueError, OverflowError):
        return {}
    event_id = str(raw.get("eventId") or "").strip()
    if count <= 0 or not event_id:
        return {}
    range_key = str(raw.get("range") or "today").strip().lower()
    if range_key not in {"today", "7d", "30d", "all"}:
        range_key = "today"
    return {"count": count, "eventId": event_id, "range": range_key}


def _work_overlay_items_with_background_usage(
    context: object,
    session_items: Sequence[WorkStatusItem],
) -> list[WorkStatusItem]:
    return [*session_items, *_background_usage_work_items(context)]


def _normalized_overlay_match_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def _work_overlay_command_matches_item(
    command: Mapping[str, object],
    item: Mapping[str, object],
) -> bool:
    command_session = str(command.get("sessionId") or "").strip()
    item_sessions = {
        str(item.get("sessionId") or "").strip(),
        str(item.get("id") or "").strip(),
    }
    if command_session and command_session in item_sessions:
        return True

    command_title = _normalized_overlay_match_text(
        command.get("targetTitle") or command.get("title")
    )
    if not command_title:
        return False
    item_titles = {
        _normalized_overlay_match_text(item.get("targetTitle")),
        _normalized_overlay_match_text(item.get("title")),
    }
    if command_title not in item_titles:
        return False

    command_workdir = _normalized_overlay_match_text(command.get("workdir"))
    item_workdir = _normalized_overlay_match_text(item.get("workdir"))
    return not command_workdir or not item_workdir or command_workdir == item_workdir


class DesktopWorkOverlay:
    """Optional PySide6 primary-screen desktop overlay for work bubbles."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        item_limit: int = DEFAULT_WORK_OVERLAY_MAX_ITEMS,
    ) -> None:
        self.item_limit = normalize_work_overlay_max_items(item_limit)
        self.enabled = bool(enabled) and self.item_limit > 0
        self._state_path = (
            hud_runtime_dir() / f"work-overlay-{os.getpid()}-{int(time.time() * 1000)}.json"
        )
        self._command_path = _work_overlay_command_path(self._state_path)
        self._transition_audit_path = _work_overlay_transition_audit_path()
        self._command_offset = 0
        self._deferred_commands: deque[dict[str, object]] = deque()
        self._process: subprocess.Popen[str] | None = None
        self._closed = False
        self._available: bool | None = None
        self._unavailable_reason = ""
        self._unavailable_reported = False
        self._restart_blocked_until = 0.0
        self._last_helper_exit_code: int | None = None
        self._helper_started_at = 0.0
        self._last_helper_heartbeat_at = 0.0
        self._last_payload_items: list[dict[str, object]] | None = None
        self._last_theme_payload: dict[str, object] = {}
        self._system_action: dict[str, object] | None = None
        self._rest_reminder: dict[str, object] = {}
        self._system_action_unavailable_reason = ""
        self._last_state_signature: str | None = None
        self._last_state_write_at = 0.0
        # A HUD launch cannot have an in-progress task.  The first snapshot can
        # still contain the last task read from Codex's persisted session files,
        # so never publish that snapshot as a desktop bubble.  The next real
        # update (session/file event) is allowed to create bubbles normally.
        self._suppress_initial_items = True
        self._switch_completed_command: dict[str, object] | None = None
        self._switch_completed_until = 0.0
        self._theme_probe = CodexThemeProbe(
            timeout_seconds=0.08,
            # The renderer binding pushes theme changes.  This cache is only
            # a fallback for the desktop work overlay, so do not re-run the
            # expensive DOM probe on every session refresh.
            cache_seconds=60.0,
            failure_cooldown_seconds=5.0,
        )

    def configure(
        self,
        *,
        enabled: bool | None = None,
        item_limit: int | None = None,
    ) -> None:
        next_enabled = self.enabled if enabled is None else bool(enabled)
        if item_limit is not None:
            self.item_limit = normalize_work_overlay_max_items(item_limit)
        self.enabled = next_enabled and self.item_limit > 0
        if (
            not self.enabled
            and self._system_action is None
            and not self._rest_reminder
            and not self._closed
        ):
            self._stop_runtime(permanent=False)

    def update(self, items: Sequence[WorkStatusItem]) -> None:
        if self._closed:
            return
        if not self.enabled and self._system_action is None and not self._rest_reminder:
            self._stop_runtime(permanent=False)
            return
        if not self.enabled:
            return
        if not self._runtime_available():
            self._stop_runtime(permanent=False)
            self._report_unavailable_once(self._unavailable_reason)
            return
        self._ensure_helper_healthy(time.monotonic())
        if self._suppress_initial_items:
            self._suppress_initial_items = False
            payload_items = [
                work_item_to_overlay_dict(item)
                for item in items
                if item.kind == BACKGROUND_USAGE_KIND
            ]
        else:
            payload_items = [work_item_to_overlay_dict(item) for item in items]
        payload_items = self._apply_switch_completed_override(payload_items)
        theme_payload = self._theme_payload()
        next_signature = self._state_signature(
            payload_items,
            theme=theme_payload,
            close=False,
        )
        if next_signature != self._last_state_signature:
            self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        if self._process is None and time.monotonic() >= self._restart_blocked_until:
            self._start()

    def update_rest_reminder(self, payload: Mapping[str, object] | None) -> bool:
        """Publish one non-session rest bubble, independently of session limits."""
        if self._closed:
            return False
        next_payload = (
            dict(payload)
            if isinstance(payload, Mapping) and bool(payload.get("bubbleVisible"))
            else {}
        )
        if next_payload == self._rest_reminder:
            if not next_payload:
                return False
            if not self._runtime_available():
                self._report_unavailable_once(self._unavailable_reason)
                return False
            self._ensure_helper_healthy(time.monotonic())
            if self._process is None:
                self._restart_blocked_until = 0.0
            process = self._process
            if process is not None and process.poll() is not None:
                self._last_helper_exit_code = int(process.returncode or 0)
                self._process = None
            if self._process is None and time.monotonic() >= self._restart_blocked_until:
                self._start()
            return self._process is not None
        self._rest_reminder = next_payload
        if not self._rest_reminder and not self.enabled and self._system_action is None:
            self._stop_runtime(permanent=False)
            return False
        if not self._runtime_available():
            self._report_unavailable_once(self._unavailable_reason)
            return False
        self._ensure_helper_healthy(time.monotonic())
        if self._process is None:
            self._restart_blocked_until = 0.0
        payload_items = list(self._last_payload_items or [])
        theme_payload = self._last_theme_payload or self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._process = None
        if self._process is None and time.monotonic() >= self._restart_blocked_until:
            self._start()
        return self._process is not None

    def offer_codex_restart(self, *, title: str, message: str) -> bool:
        """Show a persistent system action independently of session-bubble settings."""
        if self._closed:
            return False
        self._system_action_unavailable_reason = ""
        self._system_action = {
            "id": WORK_OVERLAY_RESTART_ACTION_ID,
            "action": WORK_OVERLAY_RESTART_ACTION,
            "title": str(title or "需要重启 Codex"),
            "message": str(message or ""),
            "label": "重启 Codex",
            "persistent": True,
        }
        if not self._runtime_available():
            self._system_action_unavailable_reason = self._unavailable_reason
            self._report_unavailable_once(self._unavailable_reason)
            self._system_action = None
            return False
        self._ensure_helper_healthy(time.monotonic())
        payload_items = list(self._last_payload_items or [])
        theme_payload = self._theme_payload()
        self._write_state(payload_items, theme=theme_payload, close=False)
        self._last_payload_items = [dict(item) for item in payload_items]
        self._last_theme_payload = dict(theme_payload)
        process = self._process
        if process is not None and process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._process = None
        if self._process is None:
            self._start()
        if self._process is None:
            self._system_action_unavailable_reason = (
                self._unavailable_reason or "unable to start PySide6 desktop overlay helper"
            )
            self._system_action = None
            return False
        ready = self._wait_for_system_action_command(
            {WORK_OVERLAY_SYSTEM_ACTION_READY},
            timeout_seconds=WORK_OVERLAY_SYSTEM_ACTION_READY_TIMEOUT_SECONDS,
        )
        if ready is None:
            if not self._system_action_unavailable_reason:
                self._system_action_unavailable_reason = (
                    "PySide6 desktop overlay helper did not acknowledge the restart action"
                )
            self._stop_runtime(permanent=False)
            return False
        return True

    def wait_for_codex_restart_request(self) -> bool:
        """Wait on file/process events until the system action is clicked or fails."""
        if self._closed or self._system_action is None:
            return False
        command = self._wait_for_system_action_command(
            {WORK_OVERLAY_RESTART_ACTION},
            timeout_seconds=None,
        )
        if command is None:
            if not self._system_action_unavailable_reason:
                self._system_action_unavailable_reason = (
                    "PySide6 desktop overlay helper exited before restart was requested"
                )
            return False
        return True

    @property
    def system_action_unavailable_reason(self) -> str:
        return self._system_action_unavailable_reason

    def _wait_for_system_action_command(
        self,
        accepted_actions: set[str],
        *,
        timeout_seconds: float | None,
    ) -> dict[str, object] | None:
        process = self._process
        if process is None:
            return None
        wake = Event()
        matched: list[dict[str, object]] = []
        result_lock = threading.Lock()
        drain_lock = threading.Lock()
        expected_action_id = str(
            (self._system_action or {}).get("id") or ""
        ).strip()

        def drain_commands() -> None:
            with drain_lock:
                commands = list(self._deferred_commands)
                self._deferred_commands.clear()
                commands.extend(self.take_commands())
                for command in commands:
                    action = str(command.get("action") or "").strip()
                    if action == "runtimeError":
                        self._system_action_unavailable_reason = str(
                            command.get("message") or "PySide6 desktop overlay helper error"
                        )
                        wake.set()
                        continue
                    if action not in accepted_actions:
                        self._deferred_commands.append(dict(command))
                        continue
                    action_id = str(command.get("actionId") or "").strip()
                    if expected_action_id and action_id != expected_action_id:
                        continue
                    with result_lock:
                        if not matched:
                            matched.append(dict(command))
                    wake.set()

        watcher = FileChangeWatcher(
            lambda _reasons, _paths: drain_commands(),
            fallback_poll_seconds=WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS,
        )
        try:
            watcher.update(
                [FileWatchSpec.file(self._command_path, "work-overlay-system-action")]
            )
            drain_commands()

            def wait_for_helper_exit() -> None:
                try:
                    process.wait()
                except Exception:
                    pass
                wake.set()

            threading.Thread(
                target=wait_for_helper_exit,
                name="codex-hud-overlay-action-exit",
                daemon=True,
            ).start()
            wake.wait(timeout_seconds)
            drain_commands()
        finally:
            watcher.close()
        with result_lock:
            return dict(matched[0]) if matched else None

    def keep_alive(self) -> None:
        """Refresh the helper state file while renderer snapshots are unchanged."""
        if self._closed or (not self.enabled and not self._rest_reminder):
            return
        if not self._last_payload_items and not self._rest_reminder:
            return
        now = time.monotonic()
        self._ensure_helper_healthy(now)
        if now - self._last_state_write_at < WORK_OVERLAY_KEEPALIVE_SECONDS:
            return
        self._write_state(
            self._last_payload_items,
            theme=self._last_theme_payload,
            close=False,
        )
        if self._process is None and now >= self._restart_blocked_until:
            self._start()

    def mark_switch_completed(self, command: Mapping[str, object]) -> bool:
        """Tell the helper that a clicked bubble has become the active session."""
        if self._closed or not self.enabled or not self._last_payload_items:
            return False
        match_index = next(
            (
                index
                for index, item in enumerate(self._last_payload_items)
                if _work_overlay_command_matches_item(command, item)
            ),
            -1,
        )
        if match_index < 0:
            return False
        self._switch_completed_command = dict(command)
        self._switch_completed_until = (
            time.monotonic() + WORK_OVERLAY_SWITCH_COMPLETED_HOLD_SECONDS
        )
        payload_items: list[dict[str, object]] = []
        for index, item in enumerate(self._last_payload_items):
            next_item = dict(item)
            next_item["current"] = index == match_index
            payload_items.append(next_item)
        self._last_payload_items = payload_items
        self._write_state(
            payload_items,
            theme=self._last_theme_payload,
            close=False,
        )
        return True

    def _apply_switch_completed_override(
        self,
        payload_items: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        items = [dict(item) for item in payload_items]
        command = self._switch_completed_command
        if command is None:
            return items
        if time.monotonic() > self._switch_completed_until:
            self._switch_completed_command = None
            self._switch_completed_until = 0.0
            return items
        match_index = next(
            (
                index
                for index, item in enumerate(items)
                if _work_overlay_command_matches_item(command, item)
            ),
            -1,
        )
        if match_index < 0:
            return items
        for index, item in enumerate(items):
            item["current"] = index == match_index
        return items

    def next_keep_alive_seconds(self) -> float | None:
        """Return when the helper state file needs its next refresh."""
        if (
            self._closed
            or (not self.enabled and not self._rest_reminder)
            or (not self._last_payload_items and not self._rest_reminder)
        ):
            return None
        elapsed = time.monotonic() - self._last_state_write_at
        return max(0.1, WORK_OVERLAY_KEEPALIVE_SECONDS - max(0.0, elapsed))

    def close(self) -> None:
        if self._closed:
            return
        self._stop_runtime(permanent=True)

    def take_commands(self) -> list[dict[str, object]]:
        if self._closed:
            return []
        try:
            stat = self._command_path.stat()
        except OSError:
            self._command_offset = 0
            return []
        if stat.st_size <= 0:
            self._command_offset = 0
            return []
        if stat.st_size < self._command_offset:
            self._command_offset = 0
        elif stat.st_size == self._command_offset:
            return []

        commands: list[dict[str, object]] = []
        try:
            with self._command_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._command_offset)
                for line in handle:
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        commands.append(payload)
                self._command_offset = handle.tell()
        except OSError:
            return []
        return commands

    @property
    def command_path(self) -> Path:
        return self._command_path

    def _stop_runtime(self, *, permanent: bool) -> None:
        if permanent:
            self._closed = True
        process = self._process
        self._process = None
        try:
            state_exists = self._state_path.exists()
        except OSError:
            state_exists = False
        if process is not None or state_exists:
            self._write_state([], close=True)
        if process is not None:
            try:
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        try:
            self._state_path.unlink()
        except OSError:
            pass
        try:
            self._command_path.unlink()
        except OSError:
            pass
        try:
            _work_overlay_heartbeat_path(self._state_path).unlink()
        except OSError:
            pass
        self._command_offset = 0
        self._deferred_commands.clear()
        self._last_payload_items = None
        self._last_theme_payload = {}
        self._system_action = None
        self._rest_reminder = {}
        self._last_state_signature = None
        self._last_state_write_at = 0.0
        self._switch_completed_command = None
        self._switch_completed_until = 0.0
        self._helper_started_at = 0.0
        self._last_helper_heartbeat_at = 0.0

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                _work_overlay_command(self._state_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            started_at = time.time()
            self._helper_started_at = started_at
            self._last_helper_heartbeat_at = started_at
        except Exception:
            self._process = None
            self._restart_blocked_until = (
                time.monotonic() + WORK_OVERLAY_RESTART_BACKOFF_SECONDS
            )
            self._report_unavailable_once("unable to start PySide6 desktop overlay helper")

    def _ensure_helper_healthy(self, now: float) -> None:
        """Restart a live-but-stuck helper before it can retain stale bubbles."""
        process = self._process
        if process is None:
            return
        if process.poll() is not None:
            self._last_helper_exit_code = int(process.returncode or 0)
            self._process = None
            self._restart_blocked_until = (
                0.0
                if self._last_helper_exit_code == 0
                else now + WORK_OVERLAY_RESTART_BACKOFF_SECONDS
            )
            if self._last_helper_exit_code != 0:
                self._report_unavailable_once(
                    f"PySide6 desktop overlay helper exited with code {self._last_helper_exit_code}"
                )
            return
        user_objects = _windows_user_object_count(process)
        if (
            user_objects is not None
            and user_objects >= WORK_OVERLAY_HELPER_MAX_USER_OBJECTS
        ):
            self._restart_unresponsive_helper(
                process,
                now,
                reason=f"user_objects={user_objects}",
            )
            return
        self._refresh_helper_heartbeat()
        heartbeat_at = max(self._helper_started_at, self._last_helper_heartbeat_at)
        if heartbeat_at <= 0.0:
            return
        heartbeat_now = time.time()
        if heartbeat_now - heartbeat_at < WORK_OVERLAY_HELPER_HEARTBEAT_TIMEOUT_SECONDS:
            return
        _LOGGER.warning(
            "work_overlay_helper_unresponsive age_seconds=%.1f",
            heartbeat_now - heartbeat_at,
        )
        self._restart_unresponsive_helper(
            process,
            now,
            reason=f"heartbeat_age_seconds={heartbeat_now - heartbeat_at:.1f}",
        )

    def _restart_unresponsive_helper(
        self,
        process: subprocess.Popen[str],
        now: float,
        *,
        reason: str,
    ) -> None:
        _LOGGER.warning("work_overlay_helper_restart reason=%s", reason)
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        self._process = None
        self._helper_started_at = 0.0
        self._last_helper_heartbeat_at = 0.0
        self._restart_blocked_until = now
        self._start()

    def _refresh_helper_heartbeat(self) -> None:
        try:
            heartbeat_at = _work_overlay_heartbeat_path(self._state_path).stat().st_mtime
        except OSError:
            return
        self._last_helper_heartbeat_at = max(
            self._last_helper_heartbeat_at,
            float(heartbeat_at),
        )

    def reset_runtime_availability(self) -> bool:
        self._available = None
        self._unavailable_reason = ""
        self._unavailable_reported = False
        self._restart_blocked_until = 0.0
        return self._runtime_available()

    def _runtime_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            available = _pyside6_runtime_available()
        except (ImportError, AttributeError, ValueError) as exc:
            available = False
            self._unavailable_reason = str(exc)
        if not available and not self._unavailable_reason:
            self._unavailable_reason = (
                "PySide6 is not installed; install codex-usage-hud[desktop-overlay] "
                "to enable desktop work bubbles."
            )
        self._available = available
        return available

    def _report_unavailable_once(self, reason: str) -> None:
        if self._unavailable_reported:
            return
        self._unavailable_reported = True
        message = str(reason or "PySide6 desktop overlay is unavailable")
        _LOGGER.warning("work_overlay_unavailable reason=%s", message)
        try:
            _append_renderer_diagnostic("work_overlay_unavailable", reason=message)
        except Exception:
            return

    def _append_transition_audit(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        close: bool,
    ) -> None:
        if close or self._last_payload_items is None:
            return
        old_by_id = {
            str(item.get("id") or item.get("sessionId") or "").strip(): item
            for item in self._last_payload_items
            if str(item.get("id") or item.get("sessionId") or "").strip()
        }
        if not old_by_id:
            return
        now = datetime.now().astimezone().isoformat()
        events: list[dict[str, object]] = []
        for item in items:
            item_id = str(item.get("id") or item.get("sessionId") or "").strip()
            if not item_id or item_id not in old_by_id:
                continue
            old_item = old_by_id[item_id]
            old_status = _overlay_payload_status(old_item)
            new_status = _overlay_payload_status(item)
            old_pending = _overlay_payload_pending_accounting(old_item)
            new_pending = _overlay_payload_pending_accounting(item)
            old_kind = _overlay_payload_kind(old_item)
            new_kind = _overlay_payload_kind(item)
            if (
                old_status == new_status
                and old_pending == new_pending
                and old_kind == new_kind
            ):
                continue
            events.append(
                {
                    "time": now,
                    "ownerPid": os.getpid(),
                    "stateFile": str(self._state_path),
                    "id": item_id,
                    "sessionId": str(item.get("sessionId") or old_item.get("sessionId") or ""),
                    "title": str(
                        item.get("targetTitle")
                        or item.get("title")
                        or old_item.get("targetTitle")
                        or old_item.get("title")
                        or ""
                    ),
                    "transition": _overlay_payload_transition_name(old_item, item),
                    "oldKind": old_kind,
                    "newKind": new_kind,
                    "oldStatus": old_status,
                    "newStatus": new_status,
                    "oldPendingAccounting": old_pending,
                    "newPendingAccounting": new_pending,
                    "oldUpdatedAt": str(old_item.get("updatedAt") or ""),
                    "newUpdatedAt": str(item.get("updatedAt") or ""),
                }
            )
        if not events:
            return
        try:
            self._transition_audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._transition_audit_path.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
        except OSError:
            return

    def _write_state(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        theme: Mapping[str, object] | None = None,
        close: bool,
    ) -> None:
        try:
            payload_items = list(items)
            payload_signature = self._state_signature(
                payload_items,
                theme=theme,
                close=close,
            )
            write_json_object(
                self._state_path,
                {
                    "ownerPid": os.getpid(),
                    "itemLimit": int(self.item_limit),
                    "commandPath": str(self._command_path),
                    "items": payload_items,
                    "systemAction": (
                        dict(self._system_action or {}) if not close else {}
                    ),
                    "restReminder": (
                        dict(self._rest_reminder or {}) if not close else {}
                    ),
                    "theme": dict(theme or {}),
                    "updatedAt": time.time(),
                    "close": bool(close),
                },
            )
            self._append_transition_audit(payload_items, close=close)
            self._last_state_signature = payload_signature
            self._last_state_write_at = time.monotonic()
        except OSError:
            return

    def _state_signature(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        theme: Mapping[str, object] | None = None,
        close: bool,
    ) -> str:
        return json.dumps(
            {
                "itemLimit": int(self.item_limit),
                "commandPath": str(self._command_path),
                "items": list(items),
                "systemAction": (
                    dict(self._system_action or {}) if not close else {}
                ),
                "restReminder": (
                    dict(self._rest_reminder or {}) if not close else {}
                ),
                "theme": dict(theme or {}),
                "close": bool(close),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _theme_payload(self) -> dict[str, object]:
        snapshot = self._theme_probe.snapshot()
        if snapshot.source not in {"cdp", "persisted"}:
            return {}
        return snapshot.hud_tokens.to_dict()


def _wait_for_renderer_restart_request(
    args: argparse.Namespace,
    work_overlay: DesktopWorkOverlay,
    loading_feedback: HudLoadingFeedback | None,
) -> bool:
    title = "需要重启 Codex"
    message = "当前 Codex 未开启 HUD 所需的 CDP。保存好当前工作后，点击重启继续。"
    if work_overlay.offer_codex_restart(title=title, message=message):
        if loading_feedback is not None:
            loading_feedback.close()
            loading_feedback = None
        if work_overlay.wait_for_codex_restart_request():
            _LOGGER.info("renderer_restart_requested_by_user surface=work-overlay")
            return True
        fallback_reason = work_overlay.system_action_unavailable_reason
    else:
        fallback_reason = work_overlay.system_action_unavailable_reason

    fallback_reason = str(
        fallback_reason or "PySide6 desktop restart action unavailable"
    )
    _append_renderer_diagnostic(
        "renderer_restart_overlay_fallback",
        reason=fallback_reason,
    )
    work_overlay.close()
    card = loading_feedback
    if card is None:
        card = _create_loading_feedback(
            args,
            title=title,
            message="",
        ).start()
    offered = card.offer_codex_restart(title=title, message=message)
    if not offered:
        card.close()
        return False
    if not card.wait_for_codex_restart_request():
        card.close()
        return False
    card.close()
    _LOGGER.info("renderer_restart_requested_by_user surface=loading-card")
    return True


class _WorkOverlayCommandPump:
    """Drain work-overlay click commands when the helper command file changes."""

    def __init__(
        self,
        work_overlay: DesktopWorkOverlay,
        session_controller: SessionSwitchController,
        *,
        poll_ms: int | None = None,
        command_event: Event | None = None,
        runtime_events: RuntimeEventBus | None = None,
        runtime_errors: RuntimeErrorRegistry | None = None,
        background_command_callback: Callable[[dict[str, object]], bool] | None = None,
        rest_reminder_command_callback: Callable[[dict[str, object]], bool] | None = None,
    ) -> None:
        self._work_overlay = work_overlay
        self._session_controller = session_controller
        del poll_ms
        self._command_event = command_event
        self._runtime_events = runtime_events
        self._runtime_errors = runtime_errors
        self._background_command_callback = background_command_callback
        self._rest_reminder_command_callback = rest_reminder_command_callback
        self._stop_event = Event()
        self._lock = threading.Lock()
        self._watcher: FileChangeWatcher | None = None

    def start(self) -> bool:
        with self._lock:
            if self._watcher is not None:
                return True
            self._stop_event.clear()
        try:
            self.drain_once()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_initial_drain_failed error=%s", exc)
        try:
            command_path = self._command_path()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_path_unavailable error=%s", exc)
            return True
        with self._lock:
            if self._watcher is not None:
                return True
            watcher = FileChangeWatcher(
                self._on_command_file_changed,
                fallback_poll_seconds=WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS,
            )
            self._watcher = watcher
        try:
            watcher.update(
                [
                    FileWatchSpec.file(
                        command_path,
                        "work-overlay-command",
                    )
                ]
            )
            return True
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_watcher_start_failed error=%s", exc)
            with self._lock:
                if self._watcher is watcher:
                    self._watcher = None
            try:
                watcher.close()
            except Exception:
                pass
            return False

    def close(self, timeout_seconds: float = 0.5) -> None:
        del timeout_seconds
        self._stop_event.set()
        with self._lock:
            watcher = self._watcher
            self._watcher = None
        if watcher is not None:
            watcher.close()

    def drain_once(self) -> int:
        handled = _handle_work_overlay_commands(
            self._work_overlay,
            self._session_controller,
            prepare_window=True,
            runtime_events=self._runtime_events,
            runtime_errors=self._runtime_errors,
            background_command_callback=self._background_command_callback,
            rest_reminder_command_callback=self._rest_reminder_command_callback,
        )
        if handled and self._command_event is not None:
            self._command_event.set()
        return handled

    def _command_path(self) -> Path:
        command_path = getattr(self._work_overlay, "command_path", None)
        if command_path is not None:
            return Path(command_path)
        return Path(getattr(self._work_overlay, "_command_path"))

    def _on_command_file_changed(self, reasons: set[str], paths: set[Path]) -> None:
        del reasons, paths
        if self._stop_event.is_set():
            return
        try:
            self.drain_once()
        except Exception as exc:
            _LOGGER.debug("work_overlay_command_pump_failed error=%s", exc)


def run_work_overlay_helper(state_file: str | Path) -> int:
    state_arg = str(state_file or "").strip()
    if not state_arg:
        return 1
    try:
        return _work_overlay_helper_qt()(
            state_arg,
            process_exists=_process_exists,
            owner_pid_from_path=_work_overlay_owner_pid,
            item_limit=ACTIVE_WORK_ITEM_LIMIT,
            stale_seconds=WORK_OVERLAY_STALE_SECONDS,
            overlay_alpha=WORK_OVERLAY_ALPHA,
            hover_alpha=WORK_OVERLAY_HOVER_ALPHA,
            header_title_limit=WORK_OVERLAY_HEADER_TITLE_LIMIT,
        )
    except RuntimeError as exc:
        _eprint(str(exc))
        return 1


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
    for logger_name in ("codex_usage_hud.cli", "codex_usage_hud.file_watcher"):
        logger = logging.getLogger(logger_name)
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)
        logger.setLevel(daemon_logger.level or logging.INFO)
        logger.propagate = False
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

    # ``CodexRelocated`` is the user-approved canonical desktop copy.  Keep it
    # ahead of AppX and legacy candidates so HUD recovery and normal launches
    # do not silently start the old WindowsApps installation.
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        relocated_app = Path(local_appdata) / "Programs" / "CodexRelocated" / "app"
        candidates.extend(
            [relocated_app / "ChatGPT.exe", relocated_app / "Codex.exe"]
        )

    # Codex Desktop 26.707+ renamed the Electron GUI executable to
    # ``ChatGPT.exe`` (the ``Codex.exe`` next to it now launches the Rust
    # ``app-server`` backend, not the visible window).  Prefer the new name so
    # we hand ``--remote-debugging-port`` to the real GUI process, but keep the
    # legacy ``Codex.exe`` paths as a fallback for older installs.
    for root_name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(root_name)
        if not root:
            continue
        base = Path(root)
        candidates.extend(
            [
                base / "Programs" / "Codex" / "ChatGPT.exe",
                base / "Programs" / "codex" / "ChatGPT.exe",
                base / "Programs" / "OpenAI Codex" / "ChatGPT.exe",
                base / "Codex" / "ChatGPT.exe",
                base / "OpenAI Codex" / "ChatGPT.exe",
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
                windows_apps.glob(
                    "OpenAI.Codex_*__2p2nqsd0c76g0/app/ChatGPT.exe"
                )
            )
            candidates.extend(
                windows_apps.glob("OpenAI.Codex_*__2p2nqsd0c76g0/app/Codex.exe")
            )
        except OSError:
            pass
    for install_location in _codex_appx_install_locations():
        candidates.append(install_location / "app" / "ChatGPT.exe")
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


def _codex_app_debugger_args(port: int) -> list[str]:
    return _codex_app_debugger_parameters(port).split()


def _macos_codex_app_target() -> str:
    configured = os.environ.get(CODEX_APP_PATH_ENV, "").strip()
    return configured or "Codex"


def _macos_codex_app_name() -> str:
    target = _macos_codex_app_target()
    name = Path(target).stem if target.endswith(".app") or "/" in target else target
    return name or "Codex"


def _launch_macos_codex_app(*, debugger: bool = False) -> bool:
    target = _macos_codex_app_target()
    command = ["open"]
    if target.endswith(".app") or "/" in target:
        command.append(target)
    else:
        command.extend(["-a", target])
    if debugger:
        command.extend(["--args", *_codex_app_debugger_args(cdp_port_from_env())])
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        _LOGGER.info("codex_app_macos_launch_failed target=%s error=%s", target, exc)
        return False
    _LOGGER.info(
        "codex_app_launched mode=%s target=%s",
        "debugger" if debugger else "normal",
        target,
    )
    if debugger:
        _remember_requested_renderer_cdp_port(cdp_port_from_env())
    return True


def _stop_macos_codex_app(*, timeout_seconds: float = 8.0) -> bool:
    app_name = _macos_codex_app_name()
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to quit'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except Exception as exc:
        _LOGGER.info("codex_app_macos_quit_failed app=%s error=%s", app_name, exc)
        return False
    deadline = time.monotonic() + max(0.5, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["pgrep", "-x", app_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
        except Exception:
            return True
        if result.returncode != 0:
            return True
        time.sleep(0.1)
    _LOGGER.info("codex_app_macos_quit_timeout app=%s", app_name)
    return False


def launch_codex_app(*, debugger: bool = False) -> bool:
    """Best-effort launch of Codex App, optionally with local CDP enabled."""
    if sys.platform == "darwin":
        return _launch_macos_codex_app(debugger=debugger)

    if debugger:
        port = cdp_port_from_env()
        parameters = _codex_app_debugger_parameters(port)
        for executable in _codex_app_executable_candidates():
            if _shell_execute_open_with_elevation_fallback(
                executable,
                parameters=parameters,
                working_dir=executable.parent,
            ):
                _remember_requested_renderer_cdp_port(port)
                _LOGGER.info(
                    "codex_app_launched mode=debugger target=%s port=%s",
                    executable,
                    port,
                )
                return True
        for target in _codex_app_shell_targets():
            if _shell_execute_open(target, parameters=parameters):
                _remember_requested_renderer_cdp_port(port)
                _LOGGER.info(
                    "codex_app_launched mode=debugger target=%s port=%s",
                    target,
                    port,
                )
                return True
        _LOGGER.info("codex_app_debugger_launch_unavailable port=%s", port)
        return False

    for executable in _codex_app_executable_candidates():
        if _shell_execute_open_with_elevation_fallback(
            executable,
            working_dir=executable.parent,
        ):
            _LOGGER.info("codex_app_launched mode=normal target=%s", executable)
            return True
    for target in _codex_app_shell_targets():
        if _shell_execute_open(target):
            _LOGGER.info("codex_app_launched mode=normal target=%s", target)
            return True
    _LOGGER.info("codex_app_launch_unavailable")
    return False


def _clone_args_with_renderer_preference(
    args: argparse.Namespace,
    prefer_renderer: bool,
) -> argparse.Namespace:
    del prefer_renderer
    cloned = argparse.Namespace(**vars(args))
    cloned.renderer_hud = True
    cloned.hud_mode = "renderer"
    cloned.runtime_hud_mode = "renderer"
    cloned.standalone_hud_mode = None
    return cloned


def _clone_args_with_display_mode(
    args: argparse.Namespace,
    mode: str,
) -> argparse.Namespace:
    del mode
    cloned = argparse.Namespace(**vars(args))
    cloned.hud_mode = "renderer"
    cloned.runtime_hud_mode = "renderer"
    cloned.standalone_hud_mode = None
    cloned.renderer_hud = True
    return cloned


def _runtime_display_mode(value: object) -> str:
    return effective_display_mode(value)


def _initial_runtime_display_mode(args: argparse.Namespace) -> str:
    del args
    return "renderer"


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
    if sys.platform == "darwin" and not _stop_macos_codex_app():
        return False
    try:
        port = _select_launch_renderer_cdp_port(require_fresh=True)
    except (OSError, RuntimeError) as exc:
        _append_renderer_diagnostic(
            "renderer_cdp_launch_failed",
            reason=str(exc),
            source="restart",
        )
        return False
    _append_renderer_diagnostic(
        "renderer_restart_requested_by_user",
        action_id=WORK_OVERLAY_RESTART_ACTION_ID,
        port=port,
    )
    return launch_codex_app(debugger=True)


def _daemon_startup_decision(
    args: argparse.Namespace,
    manager: CodexDaemonManager,
) -> DaemonStartupDecision:
    """Map daemon startup onto the renderer-only three-scenario contract."""
    del args
    snapshot = manager.snapshot()
    if snapshot.found:
        return DaemonStartupDecision(DAEMON_STARTUP_WAIT)
    return DaemonStartupDecision(
        DAEMON_STARTUP_RENDERER,
        launch_codex=True,
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


def renderer_cdp_state_path() -> Path:
    """Return the per-user renderer CDP runtime state path."""
    return hud_runtime_dir() / RENDERER_CDP_STATE_FILENAME


def crash_diagnostic_path() -> Path:
    """Return the fatal-crash diagnostics path."""
    return hud_runtime_dir() / CRASH_DIAGNOSTIC_FILENAME


def _enable_crash_diagnostics() -> Path | None:
    """Enable faulthandler so native ctypes crashes leave a Python stack."""
    global _CRASH_DIAGNOSTIC_FILE
    setting = os.environ.get(CRASH_DIAGNOSTICS_ENV, "").strip().lower()
    if setting in {"0", "false", "no", "off"}:
        return None
    if not sys.platform.startswith("win"):
        return None
    if _CRASH_DIAGNOSTIC_FILE is not None:
        return crash_diagnostic_path()
    try:
        import faulthandler

        path = crash_diagnostic_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8", buffering=1)
        handle.write(
            "\n--- codex-usage-hud crash diagnostics enabled "
            f"pid={os.getpid()} time={datetime.now().astimezone().isoformat()} ---\n"
        )
        faulthandler.enable(file=handle, all_threads=True)
        _CRASH_DIAGNOSTIC_FILE = handle
        return path
    except Exception:
        return None


def _append_renderer_diagnostic(stage: str, **fields: object) -> None:
    """Persist one renderer fallback diagnostic record for postmortems."""
    path = renderer_diagnostic_path()
    record = {
        "time": datetime.now().astimezone().isoformat(),
        "stage": stage,
    }
    for key, value in fields.items():
        if value is not None and value != "":
            record[key] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def _append_runtime_error_diagnostic(action: str, event: object) -> None:
    """Persist runtime errors outside DEBUG HUD so normal mode is diagnosable."""
    to_payload = getattr(event, "to_payload", None)
    payload = to_payload() if callable(to_payload) else {}
    _append_renderer_diagnostic(
        f"runtime_error_{action}",
        source=str(getattr(event, "source", "") or ""),
        severity=str(getattr(event, "severity", "") or ""),
        code=str(getattr(event, "code", "") or ""),
        message=str(getattr(event, "message", "") or ""),
        context=payload.get("context") if isinstance(payload, Mapping) else None,
        count=payload.get("count") if isinstance(payload, Mapping) else None,
        firstSeenAt=payload.get("firstSeenAt") if isinstance(payload, Mapping) else None,
        lastSeenAt=payload.get("lastSeenAt") if isinstance(payload, Mapping) else None,
    )


def _ensure_runtime_error_diagnostics(context: object) -> None:
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return
    if getattr(registry, "diagnostic_callback", None) is None:
        registry.diagnostic_callback = _append_runtime_error_diagnostic


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


def _renderer_initial_failure_can_be_fixed_by_restart(last_error: str) -> bool:
    """Return whether an initial CDP failure likely means Codex lacks debug mode."""
    text = str(last_error or "").lower()
    if not text:
        return False
    if "timed out" in text or "timeout" in text:
        return False
    if "10013" in text or "access" in text or "permission" in text:
        return False
    return any(
        marker in text
        for marker in (
            "connection refused",
            "actively refused",
            "target has no websocket",
            "no page target",
            "no websocket",
            "connection reset",
            "winerror 10061",
        )
    )


def _renderer_initial_failure_should_recover_cdp_port(last_error: str) -> bool:
    """Return whether a fresh CDP port is a better first recovery than prompting."""
    text = str(last_error or "").lower()
    if not text:
        return False
    return any(
        marker in text
        for marker in (
            "timed out",
            "timeout",
            "connection refused",
            "actively refused",
            "connection reset",
            "winerror 10061",
            "winerror 10013",
            "访问权限不允许",
        )
    )


def _valid_renderer_cdp_port(value: object) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _remote_debugging_ports_from_command_line(command_line: object) -> tuple[int, ...]:
    """Extract bounded Chromium remote-debugging ports from one command line."""
    text = str(command_line or "")
    ports: list[int] = []
    for match in _REMOTE_DEBUGGING_PORT_PATTERN.finditer(text):
        port = _valid_renderer_cdp_port(match.group(1))
        if port is not None and port not in ports:
            ports.append(port)
    return tuple(ports)


def _windows_running_codex_processes() -> list[_CodexDesktopProcess]:
    """Return exact Windows Codex/App rows or raise when they cannot be audited."""

    if not sys.platform.startswith("win"):
        return []
    script = (
        "$items = @(Get-CimInstance Win32_Process "
        "-Filter \"Name='ChatGPT.exe' OR Name='Codex.exe'\" | "
        "Select-Object ProcessId,Name,ExecutablePath,CommandLine); "
        "ConvertTo-Json -InputObject $items -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=3.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Windows Codex process query failed") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(
            f"Windows Codex process query returned {result.returncode}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Windows Codex process query returned invalid JSON") from exc
    rows = payload if isinstance(payload, list) else [payload]
    processes: list[_CodexDesktopProcess] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("Name") or "").strip()
        executable_path = str(row.get("ExecutablePath") or "").strip()
        if Path(name).stem.casefold() not in {"chatgpt", "codex"}:
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue
        processes.append(
            _CodexDesktopProcess(
                pid=pid,
                name=name,
                executable_path=executable_path,
                command_line=str(row.get("CommandLine") or ""),
            )
        )
    return processes


def _windows_running_codex_desktop_processes() -> list[_CodexDesktopProcess]:
    try:
        processes = _windows_running_codex_processes()
    except RuntimeError as exc:
        _LOGGER.info("renderer_cdp_process_query_failed platform=windows error=%s", exc)
        return []
    return [
        process
        for process in processes
        if is_codex_client_process(process.name, process.executable_path)
    ]


def _is_macos_codex_desktop_command(executable: str, command_line: str) -> bool:
    normalized_executable = str(executable or "").replace("\\", "/").casefold()
    normalized_command = str(command_line or "").replace("\\", "/").casefold()
    if "/codex.app/contents/macos/" in normalized_executable:
        return True
    configured = os.environ.get(CODEX_APP_PATH_ENV, "").strip()
    if configured:
        configured_path = configured.replace("\\", "/").rstrip("/").casefold()
        if configured_path and configured_path in normalized_command:
            return "/contents/macos/" in normalized_executable
    return False


def _macos_executable_from_command_line(command_line: object) -> str:
    """Recover a macOS app executable even when ``ps`` leaves spaces unquoted."""
    text = str(command_line or "").strip()
    if not text:
        return ""
    try:
        args = shlex.split(text, posix=True)
    except ValueError:
        args = []
    executable = args[0] if args else ""
    if "/contents/macos/" in executable.replace("\\", "/").casefold():
        return executable
    match = re.match(
        r"^(.+?\.app/Contents/MacOS/[^\s]+)(?:\s|$)",
        text.replace("\\", "/"),
        flags=re.IGNORECASE,
    )
    return match.group(1).strip('"\'') if match is not None else executable


def _macos_running_codex_desktop_processes() -> list[_CodexDesktopProcess]:
    if sys.platform != "darwin":
        return []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _LOGGER.info("renderer_cdp_process_query_failed platform=macos error=%s", exc)
        return []
    if result.returncode != 0:
        _LOGGER.info(
            "renderer_cdp_process_query_failed platform=macos code=%s",
            result.returncode,
        )
        return []
    processes: list[_CodexDesktopProcess] = []
    for line in result.stdout.splitlines():
        row = line.strip()
        if not row:
            continue
        pid_text, separator, command_line = row.partition(" ")
        if not separator:
            continue
        try:
            pid = int(pid_text)
        except (ValueError, TypeError):
            continue
        executable = _macos_executable_from_command_line(command_line)
        if pid <= 0 or not _is_macos_codex_desktop_command(executable, command_line):
            continue
        processes.append(
            _CodexDesktopProcess(
                pid=pid,
                name=Path(executable).name,
                executable_path=executable,
                command_line=command_line,
            )
        )
    return processes


def _running_codex_desktop_processes() -> list[_CodexDesktopProcess]:
    if sys.platform.startswith("win"):
        return _windows_running_codex_desktop_processes()
    if sys.platform == "darwin":
        return _macos_running_codex_desktop_processes()
    return []


def _audited_running_codex_desktop_processes() -> list[_CodexDesktopProcess]:
    if sys.platform.startswith("win"):
        return [
            process
            for process in _windows_running_codex_processes()
            if is_codex_client_process(process.name, process.executable_path)
        ]
    if sys.platform == "darwin":
        processes = _macos_running_codex_desktop_processes()
        if not processes:
            raise RuntimeError("Codex Desktop process could not be verified")
        return processes
    raise RuntimeError(f"Codex Desktop process audit is unsupported on {sys.platform}")


def _running_standalone_codex_cli_pids() -> tuple[int, ...]:
    """Return standalone Codex CLI PIDs, failing closed when audit is unavailable."""

    if sys.platform.startswith("win"):
        rows = _windows_running_codex_processes()
        return tuple(
            sorted(
                {
                    process.pid
                    for process in rows
                    if Path(process.name).stem.casefold() == "codex"
                    and not is_codex_client_process(
                        process.name,
                        process.executable_path,
                    )
                }
            )
        )
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,comm=,command="],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("macOS Codex process query failed") from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"macOS Codex process query returned {result.returncode}"
            )
        pids: set[int] = set()
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except (TypeError, ValueError):
                continue
            executable = parts[1]
            command_line = parts[2] if len(parts) > 2 else executable
            if Path(executable).name.casefold() != "codex":
                continue
            if _is_macos_codex_desktop_command(executable, command_line):
                continue
            if pid > 0 and pid != os.getpid():
                pids.add(pid)
        return tuple(sorted(pids))
    raise RuntimeError(f"Codex process audit is unsupported on {sys.platform}")






def _append_renderer_cdp_candidate(
    candidates: list[_RendererCdpPortCandidate],
    port: object,
    source: str,
    *,
    pid: int | None = None,
) -> None:
    value = _valid_renderer_cdp_port(port)
    if value is None or any(item.port == value for item in candidates):
        return
    candidates.append(
        _RendererCdpPortCandidate(port=value, source=str(source or ""), pid=pid)
    )


def _renderer_cdp_port_candidates() -> list[_RendererCdpPortCandidate]:
    candidates: list[_RendererCdpPortCandidate] = []
    for process in _running_codex_desktop_processes():
        for port in _remote_debugging_ports_from_command_line(process.command_line):
            _append_renderer_cdp_candidate(
                candidates,
                port,
                "desktop-process",
                pid=process.pid,
            )
    _append_renderer_cdp_candidate(
        candidates,
        _explicit_renderer_cdp_port_from_env(),
        "environment",
    )
    _append_renderer_cdp_candidate(
        candidates,
        _read_persisted_renderer_cdp_state_port("lastRequestedPort"),
        "requested",
    )
    _append_renderer_cdp_candidate(
        candidates,
        _read_persisted_renderer_cdp_port(),
        "successful",
    )
    _append_renderer_cdp_candidate(candidates, DEFAULT_CDP_PORT, "default")
    return candidates


def _validate_renderer_cdp_candidate(
    candidate: _RendererCdpPortCandidate,
) -> tuple[bool, str]:
    if not _localhost_cdp_port_is_listening(candidate.port):
        return False, "not-listening"
    try:
        cdp_version_info(
            candidate.port,
            RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS,
        )
        targets = list_targets(
            candidate.port,
            RENDERER_CDP_DISCOVERY_TIMEOUT_SECONDS,
        )
        pick_page_target(targets)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _find_existing_renderer_cdp_candidate() -> _RendererCdpPortCandidate | None:
    for candidate in _renderer_cdp_port_candidates():
        valid, reason = _validate_renderer_cdp_candidate(candidate)
        if valid:
            if candidate.source == "desktop-process":
                _append_renderer_diagnostic(
                    "renderer_cdp_process_port_discovered",
                    platform=sys.platform,
                    pid=candidate.pid,
                    port=candidate.port,
                )
            return candidate
        _append_renderer_diagnostic(
            "renderer_cdp_candidate_rejected",
            port=candidate.port,
            source=candidate.source,
            reason=reason,
        )
    return None


def _explicit_renderer_cdp_port_from_env() -> int | None:
    raw = os.environ.get(CDP_PORT_ENV, "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        return None
    return port if 0 < port < 65536 else None


def _read_persisted_renderer_cdp_port() -> int | None:
    return _read_persisted_renderer_cdp_state_port("lastSuccessfulPort")


def _read_persisted_renderer_cdp_state_port(key: str) -> int | None:
    try:
        data = json.loads(renderer_cdp_state_path().read_text(encoding="utf-8"))
    except (OSError, RuntimeError, json.JSONDecodeError):
        return None
    try:
        port = int(data.get(key))
    except (AttributeError, TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def _localhost_cdp_port_is_listening(port: int | None) -> bool:
    try:
        value = int(port or 0)
        with socket.create_connection(("127.0.0.1", value), timeout=0.2):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _remember_renderer_cdp_port(
    port: int | None,
    *,
    requested: bool = False,
    successful: bool = False,
) -> None:
    if port is None:
        return
    try:
        value = int(port)
    except (TypeError, ValueError):
        return
    if value <= 0 or value >= 65536:
        return
    try:
        path = renderer_cdp_state_path()
    except (OSError, RuntimeError):
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
        payload = dict(existing) if isinstance(existing, Mapping) else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    try:
        if requested:
            payload["lastRequestedPort"] = value
        if successful:
            payload["lastSuccessfulPort"] = value
        payload["updatedAt"] = datetime.now().astimezone().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return


def _remember_requested_renderer_cdp_port(port: int | None) -> None:
    _remember_renderer_cdp_port(port, requested=True)


def _remember_successful_renderer_cdp_port(port: int | None) -> None:
    _remember_renderer_cdp_port(port, requested=True, successful=True)


def _select_initial_renderer_cdp_port() -> int:
    """Select the configured launch preference without probing the runtime."""
    explicit = _explicit_renderer_cdp_port_from_env()
    if explicit is not None:
        return explicit

    persisted = _read_persisted_renderer_cdp_port()
    requested = _read_persisted_renderer_cdp_state_port("lastRequestedPort")
    # A cold-start attach may finish after the HUD process times out. Reuse its
    # port only when it is now alive; an old failed request must not displace a
    # known successful endpoint or create another restart loop.
    selected = (
        requested
        if requested is not None and _localhost_cdp_port_is_listening(requested)
        else persisted or DEFAULT_CDP_PORT
    )
    os.environ[CDP_PORT_ENV] = str(selected)
    _LOGGER.info(
        "renderer_cdp_port_selected fixed=%s source=%s",
        selected,
        "requested" if selected == requested else "persisted" if persisted else "default",
    )
    return selected


def _select_launch_renderer_cdp_port(*, require_fresh: bool = False) -> int:
    """Select a port that Codex can bind immediately for a single CDP launch."""
    preferred: list[int] = []
    for candidate in (
        _explicit_renderer_cdp_port_from_env(),
        _read_persisted_renderer_cdp_state_port("lastRequestedPort"),
        _read_persisted_renderer_cdp_port(),
        DEFAULT_CDP_PORT,
    ):
        value = _valid_renderer_cdp_port(candidate)
        if value is not None and value not in preferred:
            preferred.append(value)
    if not require_fresh:
        for port in preferred:
            if _localhost_cdp_port_available(port):
                os.environ[CDP_PORT_ENV] = str(port)
                _LOGGER.info(
                    "renderer_cdp_launch_port_selected port=%s source=preferred",
                    port,
                )
                return port
    port = _allocate_fresh_renderer_cdp_port()
    os.environ[CDP_PORT_ENV] = str(port)
    _LOGGER.info("renderer_cdp_launch_port_selected port=%s source=fresh", port)
    return port


def _observed_renderer_startup_plan() -> RendererStartupPlan:
    """Classify a Desktop family observed after the daemon saw full absence."""
    try:
        processes = _audited_running_codex_desktop_processes()
    except RuntimeError as exc:
        return RendererStartupPlan(
            scenario=RENDERER_STARTUP_RESTART_REQUIRED,
            reason=f"observed-codex-process-audit-failed: {exc}",
        )
    if not processes:
        return RendererStartupPlan(
            scenario=RENDERER_STARTUP_RESTART_REQUIRED,
            reason="observed-codex-process-not-found",
        )

    declared_ports = sorted(
        {
            port
            for process in processes
            for port in _remote_debugging_ports_from_command_line(
                process.command_line
            )
        }
    )
    if len(declared_ports) > 1:
        return RendererStartupPlan(
            scenario=RENDERER_STARTUP_RESTART_REQUIRED,
            reason="observed-codex-has-conflicting-cdp-ports",
        )
    if not declared_ports:
        return RendererStartupPlan(
            scenario=RENDERER_STARTUP_RELAUNCH_OBSERVED,
            reason="observed-codex-has-no-declared-cdp-port",
        )

    port = declared_ports[0]
    os.environ[CDP_PORT_ENV] = str(port)
    return RendererStartupPlan(
        scenario=RENDERER_STARTUP_ATTACH_OBSERVED,
        port=port,
        port_source="observed-desktop-process",
    )


def _renderer_startup_plan(
    *,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
) -> RendererStartupPlan:
    if launched_codex:
        port = _select_initial_renderer_cdp_port()
        plan = RendererStartupPlan(
            scenario=RENDERER_STARTUP_ATTACH_LAUNCHED,
            port=port,
            port_source="requested-launch",
        )
    elif observed_codex_launch:
        plan = _observed_renderer_startup_plan()
    elif _codex_processes_running():
        existing = _find_existing_renderer_cdp_candidate()
        if existing is None:
            plan = RendererStartupPlan(
                scenario=RENDERER_STARTUP_RESTART_REQUIRED,
                reason="running-codex-has-no-verified-cdp-target",
            )
        else:
            os.environ[CDP_PORT_ENV] = str(existing.port)
            plan = RendererStartupPlan(
                scenario=RENDERER_STARTUP_ATTACH,
                port=existing.port,
                port_source=existing.source,
            )
    else:
        port = _select_launch_renderer_cdp_port()
        plan = RendererStartupPlan(
            scenario=RENDERER_STARTUP_LAUNCH,
            port=port,
            port_source="launch",
        )
    _append_renderer_diagnostic(
        "renderer_startup_classified",
        scenario=plan.scenario,
        port=plan.port,
        source=plan.port_source,
        reason=plan.reason,
    )
    return plan


def _refresh_renderer_cdp_dependents(context: object) -> None:
    platform = getattr(context, "platform", None)
    refresh = getattr(platform, "refresh_cdp_probe", None)
    if callable(refresh):
        try:
            refresh()
        except Exception as exc:
            _LOGGER.info("renderer_cdp_probe_refresh_failed error=%s", exc)


def _localhost_cdp_bind_targets() -> list[tuple[int, str]]:
    targets = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_ipv6:
        targets.append((socket.AF_INET6, "::1"))
    return targets


def _localhost_cdp_port_available(port: int) -> bool:
    sockets: list[socket.socket] = []
    try:
        for family, host in _localhost_cdp_bind_targets():
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_EXCLUSIVEADDRUSE,
                        1,
                    )
                sock.bind((host, int(port)))
            except OSError:
                sock.close()
                raise
            sockets.append(sock)
    except OSError:
        return False
    finally:
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass
    return True


def _allocate_fresh_renderer_cdp_port() -> int:
    """Pick a currently unused localhost TCP port for Codex CDP."""
    current = cdp_port_from_env()
    for _attempt in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port != current and _localhost_cdp_port_available(port):
            return port
    raise RuntimeError("unable to allocate a fresh local CDP port")


def _assign_fresh_renderer_cdp_port() -> int:
    old_port = cdp_port_from_env()
    new_port = _allocate_fresh_renderer_cdp_port()
    os.environ[CDP_PORT_ENV] = str(new_port)
    _LOGGER.info("renderer_cdp_port_reassigned old=%s new=%s", old_port, new_port)
    return new_port


def _json_signature(value: Mapping[str, object] | None) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        return str(sorted(value.items()))


def _path_stat_signature(path: Path | None) -> tuple[str, int, int]:
    if path is None:
        return "", 0, 0
    key = _session_path_key(path)
    try:
        stat = path.stat()
    except OSError:
        return key, 0, 0
    mtime_ns = int(
        getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
    )
    return key, mtime_ns, int(stat.st_size)


def _renderer_runtime_signature(
    context: "RuntimeContext",
    *,
    update_state: Mapping[str, object] | None = None,
    settings_command_status: Mapping[str, object] | None = None,
) -> tuple[object, ...]:
    """Cheap invalidation key for renderer refreshes.

    This is a transitional event-driven gate: when the current session file,
    settings file, budget window, update state, and pending command state are
    unchanged, the renderer loop can skip JSONL parsing and CDP payload pushes.
    """
    try:
        session_path, selection_source = context.session_resolver.resolve()
    except Exception as exc:
        session_path = None
        selection_source = f"resolve-error:{type(exc).__name__}"
    try:
        settings_mtime = context.settings_store.mtime()
    except Exception:
        settings_mtime = None
    try:
        day_start, week_start = current_budget_windows(context.user_config)
        day_key = day_start.isoformat()
        week_key = week_start.isoformat()
    except Exception:
        day_key = ""
        week_key = ""
    return (
        _path_stat_signature(session_path),
        str(selection_source or ""),
        settings_mtime,
        day_key,
        week_key,
        _json_signature(update_state),
        _json_signature(settings_command_status),
    )


def _renderer_budget_window_keys(context: "RuntimeContext") -> tuple[str, str]:
    """Return normalized (day, week) budget-window keys for change detection."""
    try:
        day_start, week_start = current_budget_windows(context.user_config)
        return day_start.isoformat(), week_start.isoformat()
    except Exception:
        return "", ""


def _renderer_budget_signature(context: "RuntimeContext") -> tuple[object, ...]:
    day_key, week_key = _renderer_budget_window_keys(context)
    return (
        _session_path_key(getattr(context, "sessions_root", None)),
        day_key,
        week_key,
    )


def _paths_only_current_session(paths: set[Path], session_path: Path | None) -> bool:
    if not paths or session_path is None:
        return False
    current_key = _session_path_key(session_path)
    if not current_key:
        return False
    return all(_session_path_key(path) == current_key for path in paths)


def _renderer_budget_refresh_paths(
    file_change_paths: Iterable[Path],
) -> tuple[Path, ...]:
    paths = tuple(dict.fromkeys(Path(path) for path in file_change_paths))
    if not paths:
        return ()
    if any(path.suffix.lower() != ".jsonl" for path in paths):
        return ()
    return tuple(sorted(paths, key=_session_path_key))


def _renderer_should_refresh_budget_aggregate(
    *,
    latest_snapshot: ParsedSession | None,
    latest_budget_signature: tuple[object, ...] | None,
    budget_signature: tuple[object, ...],
    file_change_reasons: set[str],
    file_change_paths: set[Path],
) -> bool:
    if latest_snapshot is None:
        return True
    if budget_signature != latest_budget_signature:
        return True
    if "sessions-root" not in file_change_reasons:
        return False
    return not bool(_renderer_budget_refresh_paths(file_change_paths))


def _renderer_should_refresh_active_work_items(
    *,
    latest_snapshot: ParsedSession | None,
    latest_active_work_refresh_at: float,
    now_monotonic: float,
    active_work_refresh_pending: bool,
    file_change_reasons: set[str],
    file_change_paths: set[Path],
) -> bool:
    if latest_snapshot is None or active_work_refresh_pending:
        return True
    if "session" in file_change_reasons:
        return True
    if "sessions-root" in file_change_reasons and any(
        path.suffix.lower() == ".jsonl" for path in file_change_paths
    ):
        # A background CLI session is only covered by the recursive tree watch.
        # Its terminal event must rebuild the bubble list, not wait for another
        # renderer/session event to make the completion badge visible.
        return True
    return (
        now_monotonic - latest_active_work_refresh_at
        >= RENDERER_ACTIVE_WORK_RESCAN_SECONDS
    )


def _renderer_snapshot_selection_is_stale(
    snapshot: ParsedSession,
    tracker: object | None,
) -> bool:
    snapshot_seq = int(getattr(snapshot, "selection_seq", 0) or 0)
    current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
    return bool(snapshot_seq and current_seq and snapshot_seq != current_seq)


def _renderer_active_session_observation_should_refresh(
    *,
    changed: bool,
    selection_seq: object,
    tracker: object | None,
) -> bool:
    """Retry the current selection until the renderer applies its sequence."""
    if changed:
        return True
    try:
        incoming_seq = int(selection_seq or 0)
        current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
    except (TypeError, ValueError):
        return False
    return bool(incoming_seq > 0 and incoming_seq == current_seq)


def _renderer_should_use_visible_first_active_session(
    *,
    active_session_requested: bool,
    latest_snapshot: ParsedSession | None,
    has_command: bool,
    has_settings_command_status: bool,
    update_phase: str,
) -> bool:
    """Keep coalesced filesystem writes off the selected-session click path."""
    return bool(
        active_session_requested
        and latest_snapshot is not None
        and not has_command
        and not has_settings_command_status
        and update_phase != "downloading"
    )


def _renderer_deferred_active_work_refresh_due(
    *,
    pending: bool,
    not_before: float,
    now_monotonic: float,
) -> bool:
    return bool(pending and now_monotonic >= not_before)


def _renderer_file_watch_specs(
    context: "RuntimeContext",
    session_path: Path | None,
) -> list[FileWatchSpec]:
    specs: list[FileWatchSpec] = []
    settings_path = getattr(getattr(context, "settings_store", None), "path", None)
    if settings_path is not None:
        specs.append(FileWatchSpec.file(Path(settings_path), "settings"))
    session_index_path = getattr(context, "session_index_path", None)
    if session_index_path is not None:
        specs.append(FileWatchSpec.file(Path(session_index_path), "session-map"))
    state_db_path = getattr(context, "state_db_path", None)
    if state_db_path is not None:
        specs.append(FileWatchSpec.file(Path(state_db_path), "session-map"))
    sessions_root = getattr(context, "sessions_root", None)
    if sessions_root is not None and not _renderer_skip_recursive_session_tree_watch():
        root = Path(sessions_root)
        specs.append(FileWatchSpec.tree(root, "sessions-root", suffixes=(".jsonl",)))
        if root.name == "sessions":
            specs.append(
                FileWatchSpec.tree(
                    root.parent / "archived_sessions",
                    "sessions-root",
                    suffixes=(".jsonl",),
                )
            )
    if session_path is not None:
        specs.append(FileWatchSpec.file(Path(session_path), "session"))
    return specs


def _renderer_skip_recursive_session_tree_watch() -> bool:
    """Avoid high-cost polling for recursive session trees on macOS kqueue."""
    return sys.platform == "darwin"


class _RendererFileEventSource:
    """Coalesce filesystem invalidations for the renderer loop."""

    _OVERFLOW_REASON = "file_watcher.overflow"

    def __init__(
        self,
        context: "RuntimeContext",
        wake_event: Event,
        *,
        debounce_seconds: float = RENDERER_FILE_EVENT_DEBOUNCE_SECONDS,
    ) -> None:
        self._context = context
        _ensure_runtime_error_diagnostics(context)
        self._wake_event = wake_event
        self._debounce_seconds = max(0.0, float(debounce_seconds))
        self._lock = threading.Lock()
        self._reasons: set[str] = set()
        self._paths: set[Path] = set()
        self._session_path: Path | None = None
        self._timer: threading.Timer | None = None
        self._closed = False
        self._watcher = FileChangeWatcher(
            self._on_change,
            fallback_poll_seconds=RENDERER_FILE_WATCHER_FALLBACK_SECONDS,
        )
        self.update_session_path(None)

    @property
    def event_driven(self) -> bool:
        return self._watcher.event_driven

    def update_session_path(self, session_path: Path | None) -> None:
        if self._same_path(self._session_path, session_path):
            return
        self._session_path = Path(session_path) if session_path is not None else None
        specs = _renderer_file_watch_specs(self._context, self._session_path)
        self._watcher.update(specs)
        self._record_degraded_state(specs)

    def take_reasons(self) -> set[str]:
        reasons, _paths = self.take_changes()
        return reasons

    def take_changes(self) -> tuple[set[str], set[Path]]:
        with self._lock:
            reasons = set(self._reasons)
            paths = set(self._paths)
            self._reasons.clear()
            self._paths.clear()
        return reasons, paths

    def close(self) -> None:
        with self._lock:
            self._closed = True
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()
        self._watcher.close()

    def _on_change(self, reasons: set[str], paths: set[Path]) -> None:
        reasons = set(reasons)
        overflow = self._OVERFLOW_REASON in reasons
        if overflow:
            reasons.discard(self._OVERFLOW_REASON)
            self._record_overflow(reasons, paths)
        with self._lock:
            if self._closed:
                return
            self._reasons.update(reasons)
            self._paths.update(paths)
            if self._should_wake_immediately(reasons):
                wake_now = True
            elif self._debounce_seconds <= 0:
                wake_now = True
            elif self._timer is None:
                self._timer = threading.Timer(
                    self._debounce_seconds,
                    self._flush_debounced_change,
                )
                self._timer.daemon = True
                self._timer.start()
                wake_now = False
            else:
                wake_now = False
        if wake_now:
            self._publish_runtime_events(reasons, paths)
            self._wake_event.set()

    @staticmethod
    def _should_wake_immediately(reasons: set[str]) -> bool:
        # The renderer can publish a canonical UUID before Codex commits its
        # state-db row.  A session-map event makes that exact mapping available
        # and must not inherit the general filesystem debounce.
        return "session" in reasons or "session-map" in reasons

    def _flush_debounced_change(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or not self._reasons:
                return
            reasons = set(self._reasons)
            paths = set(self._paths)
        self._publish_runtime_events(reasons, paths)
        self._wake_event.set()

    def _publish_runtime_events(self, reasons: set[str], paths: set[Path]) -> None:
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if not callable(publish):
            return
        context = {
            "reasons": sorted(reasons),
            "paths": sorted(_session_path_key(path) for path in paths),
        }
        session = None
        if paths:
            session = _session_path_key(sorted(paths, key=_session_path_key)[0])
        if reasons.intersection({"session", "sessions-root"}):
            publish(
                "session_file_changed",
                source="file_watcher",
                session=session,
                context=context,
            )
        if "settings" in reasons:
            publish(
                "settings_changed",
                source="file_watcher",
                context=context,
            )

    def _record_overflow(self, reasons: set[str], paths: set[Path]) -> None:
        registry = getattr(self._context, "runtime_errors", None)
        if registry is None:
            return
        registry.record(
            source="file_watcher",
            severity="warning",
            code="overflow",
            message="Windows file watcher overflowed; reconciled watched paths.",
            context={
                "reasons": sorted(reasons),
                "paths": sorted(_session_path_key(path) for path in paths),
            },
        )

    def _record_degraded_state(self, specs: list[FileWatchSpec]) -> None:
        registry = getattr(self._context, "runtime_errors", None)
        if registry is None:
            return
        if specs and not self._watcher.event_driven:
            cause = str(
                getattr(self._watcher, "polling_cause", "") or "native_unavailable"
            )
            registry.record(
                source="file_watcher",
                severity="warning",
                code="degraded",
                message="Renderer file watcher is using polling fallback.",
                context={
                    "mode": "polling",
                    "cause": cause,
                    "reasons": sorted({spec.reason for spec in specs}),
                    "specs": len(specs),
                    "fallbackPollSeconds": RENDERER_FILE_WATCHER_FALLBACK_SECONDS,
                },
            )
            return
        registry.resolve(source="file_watcher", code="degraded")

    @staticmethod
    def _same_path(left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return left is None and right is None
        return _session_path_key(left) == _session_path_key(right)


def _invalidate_active_session_mapping_cache(context: "RuntimeContext") -> None:
    tracker = getattr(context, "active_session_tracker", None)
    invalidate = getattr(tracker, "invalidate_mapping_cache", None)
    if callable(invalidate):
        invalidate()


def _renderer_event_idle_wait_enabled(
    file_events: _RendererFileEventSource | None,
    snapshot: ParsedSession,
    update_state: Mapping[str, object],
    delay: float,
    *,
    force_fast: bool,
) -> bool:
    del snapshot, delay
    if file_events is None or not file_events.event_driven or force_fast:
        return False
    update_phase = str(update_state.get("phase") or "")
    return update_phase not in {"checking", "downloading"}


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


def _codex_processes_running() -> bool:
    if sys.platform.startswith("win"):
        try:
            listener = WindowsProcessListener(exclude_pid=os.getpid())
            return bool(listener.snapshot().found)
        except ProcessListenerError:
            return False
    if sys.platform == "darwin":
        return bool(_macos_running_codex_desktop_processes())
    return False


def _codex_processes_exited() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        listener = WindowsProcessListener(exclude_pid=os.getpid())
        return not bool(listener.snapshot().found)
    except ProcessListenerError:
        return False


def _activate_running_codex_app() -> bool:
    """Ask Codex to foreground its existing instance via normal app activation."""
    if not _codex_processes_running():
        return False
    return launch_codex_app(debugger=False)


def _prepare_codex_window_for_renderer(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
) -> tuple[bool, str, str, int]:
    """Best-effort restore/focus of Codex before attempting renderer injection."""
    if not sys.platform.startswith("win"):
        return True, "unsupported", "", 0
    try:
        tracker = CodexWindowTracker(enable_uia=False)
    except Exception:
        return True, "tracker-error", "", 0
    if not getattr(tracker, "enabled", False):
        return True, "disabled", "", 0

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    launch_attempted = False
    activation_attempted = False
    last_status = "not_found"
    last_reason = ""
    last_hwnd = 0
    while True:
        snapshot = tracker.get_window_snapshot()
        last_status = str(getattr(snapshot, "status", "") or "")
        last_reason = str(getattr(snapshot, "reason", "") or "")
        last_hwnd = int(getattr(snapshot, "hwnd", 0) or 0)
        is_active = False
        if last_hwnd:
            try:
                is_active = bool(tracker.is_active(last_hwnd))
            except Exception:
                is_active = False

        if last_status == "visible" and is_active:
            return True, last_status, last_reason, last_hwnd

        if (
            not activation_attempted
            and _codex_processes_running()
            and (last_status != "visible" or not is_active)
        ):
            activation_attempted = True
            activated = _activate_running_codex_app()
            _LOGGER.info(
                "renderer_codex_shell_activation_requested activated=%s status=%s hwnd=%s reason=%s",
                activated,
                last_status,
                last_hwnd,
                last_reason,
            )
            if activated:
                time.sleep(max(0.05, float(poll_seconds)))
                continue

        try:
            activated_hwnd = int(tracker.activate_main_window() or 0)
        except Exception:
            activated_hwnd = 0
        if activated_hwnd:
            snapshot = tracker.get_window_snapshot()
            last_status = str(getattr(snapshot, "status", "") or "")
            last_reason = str(getattr(snapshot, "reason", "") or "")
            last_hwnd = int(getattr(snapshot, "hwnd", 0) or activated_hwnd)
            activated_is_active = False
            if last_hwnd and last_status == "visible":
                try:
                    activated_is_active = bool(tracker.is_active(last_hwnd))
                except Exception:
                    activated_is_active = False
            if last_status == "visible" and activated_is_active:
                return True, last_status, last_reason, last_hwnd

        if (
            launch_if_missing
            and not launch_attempted
            and last_status in {"not_found", "hidden", "cloaked"}
        ):
            launch_attempted = True
            if _codex_processes_running() and last_status == "not_found":
                # Do not restart an existing Codex process as a side effect of
                # HUD startup. The connection-recovery path asks the user for
                # confirmation before any restart.
                launched = False
                action = "await_restart_confirmation"
            else:
                _select_launch_renderer_cdp_port()
                launched = launch_codex_app(debugger=True)
                action = "launch_debugger"
            _LOGGER.info(
                "renderer_codex_window_restore_requested action=%s launched=%s status=%s hwnd=%s reason=%s",
                action,
                launched,
                last_status,
                last_hwnd,
                last_reason,
            )

        if time.monotonic() >= deadline:
            return False, last_status, last_reason, last_hwnd
        time.sleep(max(0.01, float(poll_seconds)))


def _prepare_codex_window_for_standalone(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
) -> tuple[bool, str, str, int]:
    """Deprecated compatibility alias for renderer window preparation."""
    return _prepare_codex_window_for_renderer(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
    )


def _build_session_switch_controller(
    platform: BasePlatform,
    *,
    prefer_native_search: bool,
    cdp_port: int | None = None,
) -> SessionSwitchController:
    cdp = CdpSessionSwitchBackend(
        timeout_seconds=WORK_OVERLAY_CDP_SWITCH_TIMEOUT_SECONDS,
        port=cdp_port,
    )
    native_setting = os.environ.get(NATIVE_SEARCH_SESSION_SWITCH_ENV, "").strip().lower()
    native_enabled = native_setting not in {"0", "false", "no", "off"}
    backends: list[object] = [cdp]
    if native_enabled:
        native = WindowsSearchSessionSwitchBackend(platform)
        backends = [native, cdp] if prefer_native_search else [cdp, native]
    return SessionSwitchController(backends)


def _prepare_codex_window_for_work_overlay_switch() -> tuple[bool, str, str, int]:
    if sys.platform == "darwin":
        activated = launch_codex_app(debugger=False)
        return (
            bool(activated),
            "activated" if activated else "launch-failed",
            "" if activated else "macOS open failed",
            0,
        )
    return _prepare_codex_window_for_standalone(
        timeout_seconds=WORK_OVERLAY_WINDOW_PREPARE_TIMEOUT_SECONDS,
        poll_seconds=0.08,
        launch_if_missing=True,
    )


def _refocus_codex_window_after_work_overlay_switch() -> tuple[bool, str, str, int]:
    time.sleep(WORK_OVERLAY_SWITCH_REFOCUS_DELAY_SECONDS)
    if sys.platform == "darwin":
        activated = launch_codex_app(debugger=False)
        return (
            bool(activated),
            "activated" if activated else "launch-failed",
            "" if activated else "macOS open failed",
            0,
        )
    return _prepare_codex_window_for_standalone(
        timeout_seconds=WORK_OVERLAY_SWITCH_REFOCUS_TIMEOUT_SECONDS,
        poll_seconds=0.08,
        launch_if_missing=True,
    )


def _refocus_codex_window_after_current_session_click() -> tuple[bool, str, str, int]:
    return _refocus_codex_window_after_work_overlay_switch()


def _handle_work_overlay_command(
    command: Mapping[str, object],
    session_controller: SessionSwitchController,
    *,
    prepare_window: bool = True,
    activation_meta: dict[str, object] | None = None,
    backend_names: tuple[str, ...] | None = None,
) -> SessionSwitchResult | None:
    action = str(command.get("action") or "").strip()
    if action != "activateSession":
        return None
    if str(command.get("clientKind") or "").strip().lower() == "cli":
        _LOGGER.info("work_overlay_command_ignored reason=cli_session")
        return None
    is_current = bool(command.get("current"))
    session_id = str(command.get("sessionId") or "").strip()
    target_title = str(command.get("targetTitle") or command.get("title") or "").strip()
    if not session_id and not target_title:
        _LOGGER.info("work_overlay_command_ignored reason=missing_target")
        return None

    def activate_session() -> SessionSwitchResult:
        workdir = str(command.get("workdir") or "").strip()
        if backend_names is None:
            return session_controller.activate_session(
                session_id=session_id,
                title=target_title,
                workdir=workdir,
            )
        return session_controller.activate_session(
            session_id=session_id,
            title=target_title,
            workdir=workdir,
            backend_names=backend_names,
        )

    # CDP can reach a live Codex renderer without first foregrounding the
    # desktop window.  Defer the expensive window preparation until transport
    # or backend failure, then retry once as the bounded recovery path.
    result = activate_session()
    window_prepared = False
    if (
        prepare_window
        and not result.ok
        and result.status in {"cdp-error", "backend-error", "no-backend"}
    ):
        window_ready, window_status, window_reason, window_hwnd = (
            _prepare_codex_window_for_work_overlay_switch()
        )
        window_prepared = True
        if not window_ready:
            _LOGGER.info(
                "work_overlay_command_window_prepare_best_effort_failed status=%s hwnd=%s reason=%s",
                window_status,
                window_hwnd,
                window_reason,
            )
        result = activate_session()
    if activation_meta is not None:
        activation_meta["windowPrepared"] = window_prepared
    _LOGGER.info(
        "work_overlay_command_processed ok=%s status=%s backend=%s requested_session=%s active_session=%s matched_by=%s message=%s",
        result.ok,
        result.status,
        result.backend or "-",
        result.requested_session_id or "-",
        result.active_session_id or "-",
        result.matched_by or "-",
        result.message or "-",
    )
    if prepare_window and (is_current or result.ok or result.status == "already-active"):
        window_ready, window_status, window_reason, window_hwnd = (
            _refocus_codex_window_after_current_session_click()
        )
        _LOGGER.info(
            "work_overlay_command_session_refocus ok=%s status=%s hwnd=%s reason=%s",
            window_ready,
            window_status,
            window_hwnd,
            window_reason or "-",
        )
    return result


def _handle_work_overlay_commands(
    work_overlay: DesktopWorkOverlay,
    session_controller: SessionSwitchController,
    *,
    prepare_window: bool = True,
    runtime_events: RuntimeEventBus | None = None,
    runtime_errors: RuntimeErrorRegistry | None = None,
    background_command_callback: Callable[[dict[str, object]], bool] | None = None,
    rest_reminder_command_callback: Callable[[dict[str, object]], bool] | None = None,
) -> int:
    take_commands = getattr(work_overlay, "take_commands", None)
    if not callable(take_commands):
        return 0
    handled = 0
    for command in take_commands():
        if _handle_work_overlay_runtime_error_command(
            command,
            runtime_events,
            runtime_errors,
        ):
            handled += 1
            continue
        action = str(command.get("action") or "").strip()
        if action in {
            "restReminderAck",
            "restReminderPostpone",
            "restReminderStart",
            "restReminderFinish",
        }:
            callback_handled = rest_reminder_command_callback is not None
            callback_ok = False
            if rest_reminder_command_callback is not None:
                callback_ok = bool(rest_reminder_command_callback(dict(command)))
            _publish_work_overlay_command_event(
                runtime_events,
                command,
                None,
                activation_context={
                    "handled": callback_handled,
                    "ok": callback_ok,
                    "restReminder": True,
                },
            )
            handled += 1
            continue
        if action in {"dismissBackgroundUsage", "openBackgroundUsage"}:
            callback_handled = background_command_callback is not None
            callback_ok = False
            if background_command_callback is not None:
                callback_ok = bool(background_command_callback(dict(command)))
            activation_meta: dict[str, object] = {
                "handled": callback_handled,
                "ok": callback_ok,
            }
            if action == "openBackgroundUsage":
                activation_meta["backgroundCommandQueued"] = callback_ok
                if callback_ok and prepare_window:
                    try:
                        window_ready, window_status, window_reason, window_hwnd = (
                            _refocus_codex_window_after_work_overlay_switch()
                        )
                    except Exception as exc:
                        window_ready = False
                        window_status = "refocus-error"
                        window_reason = str(exc)
                        window_hwnd = 0
                    activation_meta.update(
                        {
                            "windowRefocused": window_ready,
                            "windowStatus": window_status,
                            "windowReason": window_reason,
                            "windowHwnd": window_hwnd,
                        }
                    )
                    _LOGGER.info(
                        "work_overlay_background_usage_refocus ok=%s status=%s hwnd=%s reason=%s",
                        window_ready,
                        window_status,
                        window_hwnd,
                        window_reason or "-",
                    )
            _publish_work_overlay_command_event(
                runtime_events,
                command,
                None,
                activation_context=activation_meta,
            )
            handled += 1
            continue
        activation_meta: dict[str, object] = {}
        result = _handle_work_overlay_command(
            command,
            session_controller,
            prepare_window=prepare_window,
            activation_meta=activation_meta,
        )
        _publish_work_overlay_command_event(
            runtime_events,
            command,
            result,
            activation_context=activation_meta,
        )
        if result is not None and result.ok:
            _publish_work_overlay_active_session_changed(
                runtime_events,
                command,
                result,
                activation_context=activation_meta,
            )
        if result is not None and (
            bool(command.get("current")) or result.ok or result.status == "already-active"
        ):
            mark_completed = getattr(work_overlay, "mark_switch_completed", None)
            if callable(mark_completed):
                mark_completed(command)
        handled += 1
    return handled


def _handle_work_overlay_runtime_error_command(
    command: Mapping[str, object],
    runtime_events: RuntimeEventBus | None,
    runtime_errors: RuntimeErrorRegistry | None = None,
) -> bool:
    action = str(command.get("action") or "").strip()
    if action != "runtimeError":
        return False
    context_value = command.get("context")
    context = dict(context_value) if isinstance(context_value, Mapping) else {}
    severity = str(command.get("severity") or "error").strip() or "error"
    code = str(command.get("code") or "helper_error").strip() or "helper_error"
    message = str(command.get("message") or "Desktop work overlay helper error.").strip()
    source = str(command.get("source") or "work_overlay_helper").strip()
    if runtime_errors is not None:
        if runtime_errors.event_bus is None and runtime_events is not None:
            runtime_errors.event_bus = runtime_events
        runtime_errors.record(
            source=source,
            severity=severity,
            code=code,
            message=message,
            context=context,
        )
        return True
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return True
    publish(
        "runtime_error",
        source=source,
        session=str(command.get("sessionId") or "") or None,
        context=context,
        error={
            "source": source,
            "severity": severity,
            "code": code,
            "message": message,
            "context": context,
        },
    )
    return True


def _publish_work_overlay_command_event(
    runtime_events: RuntimeEventBus | None,
    command: Mapping[str, object],
    result: SessionSwitchResult | None,
    *,
    activation_context: Mapping[str, object] | None = None,
) -> None:
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return
    context = _work_overlay_activation_context(command, result)
    context.update(dict(activation_context or {}))
    publish(
        "overlay_command_received",
        source="work_overlay",
        session=str(command.get("sessionId") or "") or None,
        context=context,
    )


def _work_overlay_activation_context(
    command: Mapping[str, object],
    result: SessionSwitchResult | None,
) -> dict[str, object]:
    """Project one structured local activation result for runtime events."""
    context: dict[str, object] = {
        "action": str(command.get("action") or ""),
        "sessionId": str(command.get("sessionId") or ""),
        "requestedSessionId": str(command.get("sessionId") or ""),
        "activeSessionId": str(getattr(result, "active_session_id", "") or ""),
        "requestedTitle": str(
            command.get("targetTitle") or command.get("title") or ""
        ),
        "activeTitle": str(getattr(result, "active_title", "") or ""),
        "current": bool(command.get("current")),
        "handled": result is not None,
        "ok": bool(getattr(result, "ok", False)) if result is not None else False,
        "backend": str(getattr(result, "backend", "") or "") if result is not None else "",
        "status": str(getattr(result, "status", "") or "") if result is not None else "",
        "matchedBy": str(getattr(result, "matched_by", "") or "") if result is not None else "",
        "message": str(getattr(result, "message", "") or "") if result is not None else "",
    }
    requested_at = command.get("requestedAt")
    try:
        requested_timestamp = float(requested_at)
    except (TypeError, ValueError):
        requested_timestamp = 0.0
    if requested_timestamp > 0:
        context["latencyMs"] = round(
            max(0.0, (time.time() - requested_timestamp) * 1000.0),
            1,
        )
    return context


def _publish_work_overlay_active_session_changed(
    runtime_events: RuntimeEventBus | None,
    command: Mapping[str, object],
    result: SessionSwitchResult,
    *,
    activation_context: Mapping[str, object] | None = None,
) -> None:
    publish = getattr(runtime_events, "publish", None)
    if not callable(publish):
        return
    context = _work_overlay_activation_context(command, result)
    context.update(dict(activation_context or {}))
    publish(
        "active_session_changed",
        source="work_overlay",
        session=(
            str(result.active_session_id or "").strip()
            or str(command.get("sessionId") or "").strip()
            or None
        ),
        context={"reason": "overlay_session_activation", **context},
    )


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
    # The desktop bubbles are a child of this process, but renderer injection
    # lives in Codex.  Always remove that DOM first so --stop cannot leave a
    # renderer-only half of the HUD visible after the process is gone.
    try:
        remove_renderer_hud_from_pages(port=_read_persisted_renderer_cdp_port())
    except Exception:
        _LOGGER.debug("renderer_hud_shutdown_cleanup_failed", exc_info=True)
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


def _format_cost_compact(value: float | None) -> str:
    amount = float(value or 0.0)
    if amount <= 0:
        return "$0"
    if amount >= 1:
        return f"${amount:.2f}"
    if amount >= 0.01:
        return f"${amount:.2f}"
    return f"${amount:.3f}".rstrip("0").rstrip(".")


def _current_task_tokens(snapshot: ParsedSession) -> int:
    tokens, _cost = _current_task_usage(snapshot)
    return tokens


def _current_task_cost(snapshot: ParsedSession) -> float | None:
    _tokens, cost = _current_task_usage(snapshot)
    return cost


def _current_task_cache_hit_text(snapshot: ParsedSession) -> str:
    rows = _current_task_rounds(snapshot)
    if rows:
        input_total = 0
        cached_total = 0
        for item in rows:
            input_amount = int(item.input_tokens or 0)
            if input_amount <= 0:
                continue
            cached_amount = max(0, min(int(item.cached_tokens or 0), input_amount))
            input_total += input_amount
            cached_total += cached_amount
        if input_total > 0:
            return f"{round((cached_total / max(1, input_total)) * 100):.0f}%"

    request = snapshot.request
    input_tokens = request.input_tokens
    cached_tokens = request.cached_tokens
    if input_tokens is None or int(input_tokens or 0) <= 0:
        input_tokens = snapshot.confirmed.last_input
        cached_tokens = snapshot.confirmed.last_cached
    input_amount = int(input_tokens or 0)
    if input_amount <= 0:
        return "--"
    cached_amount = max(0, min(int(cached_tokens or 0), input_amount))
    return f"{round((cached_amount / max(1, input_amount)) * 100):.0f}%"


def _request_round_from_snapshot(snapshot: ParsedSession) -> RequestRound | None:
    request = snapshot.request
    if not (
        request.total_tokens
        or request.input_tokens is not None
        or request.output_tokens is not None
        or request.cost_usd is not None
    ):
        return None
    total_tokens = request.total_tokens
    if not total_tokens:
        total_tokens = int(request.input_tokens or 0) + int(request.output_tokens or 0)
    return RequestRound(
        index=max(1, int(request.round_index or 1)),
        status=request.status,
        model=request.model,
        input_tokens=request.input_tokens,
        cached_tokens=request.cached_tokens,
        cache_write_tokens=request.cache_write_tokens,
        output_tokens=request.output_tokens,
        reasoning_tokens=request.reasoning_tokens,
        total_tokens=total_tokens,
        estimated=request.estimated,
        cost_usd=request.cost_usd,
        started_at=request.started_at,
        completed_at=request.completed_at,
    )


def _current_task_rounds(snapshot: ParsedSession) -> list[RequestRound]:
    if snapshot.request_history:
        return list(snapshot.request_history)
    current = _request_round_from_snapshot(snapshot)
    return [current] if current is not None else []


def _current_task_round_index(snapshot: ParsedSession) -> int:
    round_index = max(0, int(snapshot.request.round_index or 0))
    rows = _current_task_rounds(snapshot)
    if rows:
        round_index = max(round_index, int(rows[-1].index or 0))
    return round_index


def _current_task_model_name(snapshot: ParsedSession) -> str:
    model_name = str(snapshot.request.model or "").strip()
    if model_name:
        return model_name
    for item in reversed(_current_task_rounds(snapshot)):
        model_name = str(item.model or "").strip()
        if model_name:
            return model_name
    return ""


def _current_task_usage(snapshot: ParsedSession) -> tuple[int, float | None]:
    rows = _current_task_rounds(snapshot)
    if not rows:
        if snapshot.estimate.total_tokens:
            return int(snapshot.estimate.total_tokens), None
        return int(snapshot.confirmed.last_total or 0), None

    total_tokens = 0
    cost = 0.0
    has_cost = False
    for index, item in enumerate(rows):
        item_total = item.total_tokens
        if not item_total:
            item_total = int(item.input_tokens or 0) + int(item.output_tokens or 0)
        total_tokens += int(item_total or 0)
        if item.cost_usd is not None:
            cost += float(item.cost_usd)
            has_cost = True
        elif index == len(rows) - 1 and snapshot.request.cost_usd is not None:
            cost += float(snapshot.request.cost_usd)
            has_cost = True
    return total_tokens, (round(cost, 6) if has_cost else None)


def _workdir_leaf(value: object) -> str:
    text = str(value or "").strip().rstrip("\\/")
    if not text:
        return ""
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    return parts[-1] if parts else text


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
    target.cache_write_tokens += addition.cache_write_tokens
    target.output_tokens += addition.output_tokens
    target.reasoning_tokens += addition.reasoning_tokens
    target.cost_usd = round(target.cost_usd + addition.cost_usd, 6)


def _replace_usage(
    total: UsageSummary,
    old: UsageSummary,
    new: UsageSummary,
) -> UsageSummary:
    return UsageSummary(
        tokens=max(0, total.tokens - old.tokens + new.tokens),
        input_tokens=max(0, total.input_tokens - old.input_tokens + new.input_tokens),
        cached_tokens=max(0, total.cached_tokens - old.cached_tokens + new.cached_tokens),
        cache_write_tokens=max(
            0,
            total.cache_write_tokens
            - old.cache_write_tokens
            + new.cache_write_tokens,
        ),
        output_tokens=max(0, total.output_tokens - old.output_tokens + new.output_tokens),
        reasoning_tokens=max(
            0,
            total.reasoning_tokens - old.reasoning_tokens + new.reasoning_tokens,
        ),
        cost_usd=round(max(0.0, total.cost_usd - old.cost_usd + new.cost_usd), 6),
    )


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
        cache_write_tokens=max(
            0,
            week_total.cache_write_tokens - today_total.cache_write_tokens,
        ),
        output_tokens=max(0, week_total.output_tokens - today_total.output_tokens),
        reasoning_tokens=max(0, week_total.reasoning_tokens - today_total.reasoning_tokens),
        cost_usd=round(max(0.0, week_total.cost_usd - today_total.cost_usd), 6),
    )


@dataclass
class _UsageInsightAggregate:
    summary: UsageSummary = field(default_factory=UsageSummary)
    priced_event_count: int = 0
    total_event_count: int = 0
    latest_event_at: datetime | None = None


@dataclass
class _UsageCacheEntry:
    mtime: float | None
    file_size: int | None
    day_start: datetime
    week_start: datetime
    month_start: datetime
    model_provider: str
    summary_day: UsageSummary
    summary_week: UsageSummary
    summary_month: UsageSummary
    summary_all: UsageSummary = field(default_factory=UsageSummary)
    session_id: str = ""
    parent_session_id: str = ""
    session_key: str = ""
    session_title: str = ""
    workdir_name: str = ""
    archived: bool = False
    can_activate: bool = False
    models_day: dict[str, _UsageInsightAggregate] = field(default_factory=dict)
    models_week: dict[str, _UsageInsightAggregate] = field(default_factory=dict)
    models_month: dict[str, _UsageInsightAggregate] = field(default_factory=dict)
    day_priced_event_count: int = 0
    day_total_event_count: int = 0
    week_priced_event_count: int = 0
    week_total_event_count: int = 0
    month_priced_event_count: int = 0
    month_total_event_count: int = 0
    day_latest_event_at: datetime | None = None
    week_latest_event_at: datetime | None = None
    month_latest_event_at: datetime | None = None


@dataclass
class _UsageInsightSessionAggregate:
    session_id: str
    session_key: str
    title: str
    provider: str
    workdir_name: str
    archived: bool
    can_activate: bool
    models: dict[tuple[str, str], _UsageInsightAggregate] = field(default_factory=dict)
    usage: _UsageInsightAggregate = field(default_factory=_UsageInsightAggregate)


class UsageSummaryCache:
    """Cache rolling day/week usage summaries per JSONL session file."""

    def __init__(
        self,
        parser: JsonlSessionParser,
        *,
        min_rescan_seconds: float = DEFAULT_USAGE_SUMMARY_RESCAN_SECONDS,
        deleted_usage_ledger: DeletedUsageLedger | None = None,
    ) -> None:
        self._parser = parser
        self._min_rescan_seconds = max(0.0, float(min_rescan_seconds))
        self._deleted_usage_ledger = deleted_usage_ledger
        self._entries: dict[Path, _UsageCacheEntry] = {}
        self._deleted_entries: list[_UsageCacheEntry] = []
        self._last_scan_key: tuple[tuple[Path, ...], datetime, datetime] | None = None
        self._last_scan_at = 0.0
        self._last_day_total = UsageSummary()
        self._last_week_total = UsageSummary()
        self._insights_revision = 0
        self._insights_generated_at: datetime | None = None

    @staticmethod
    def _cache_path(path: Path) -> Path:
        expanded = Path(path).expanduser()
        try:
            return expanded.resolve(strict=False)
        except OSError:
            return expanded.absolute()

    @staticmethod
    def _scan_roots(sessions_root: Path) -> tuple[Path, ...]:
        roots = [sessions_root]
        if sessions_root.name == "sessions":
            roots.append(sessions_root.parent / "archived_sessions")
        ordered: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if root in seen:
                continue
            seen.add(root)
            ordered.append(root)
        return tuple(ordered)

    def _touch_insights(self) -> None:
        self._insights_revision += 1
        self._insights_generated_at = datetime.now().astimezone()

    def prepare_deleted_session_usage(self, item: SessionCleanupItem) -> str:
        if self._deleted_usage_ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        return self._deleted_usage_ledger.prepare(
            session_id=item._session_id,
            family_session_ids=(item._session_id, *item._descendant_ids),
            title=item.title,
            workdir_name=item.workdir_name,
            rollout_paths=item._rollout_paths,
            parser=self._parser,
        )

    def commit_deleted_session_usage(self, receipt: object) -> None:
        if self._deleted_usage_ledger is None:
            raise DeletedUsageLedgerError(
                "Deleted-session usage ledger is not configured."
            )
        self._deleted_usage_ledger.commit(str(receipt or ""))
        self._touch_insights()

    def discard_deleted_session_usage(self, receipt: object) -> None:
        if self._deleted_usage_ledger is not None:
            self._deleted_usage_ledger.discard(str(receipt or ""))

    def _deleted_usage_entries(
        self,
        day_start: datetime,
        week_start: datetime,
        live_session_ids: set[str],
    ) -> list[_UsageCacheEntry]:
        ledger = self._deleted_usage_ledger
        if ledger is None:
            return []
        try:
            sessions = ledger.sessions()
        except DeletedUsageLedgerError as exc:
            _LOGGER.warning("deleted_session_usage_load_failed error=%s", exc)
            return []
        month_start = day_start - timedelta(days=29)
        entries: list[_UsageCacheEntry] = []
        for session in sessions:
            if live_session_ids.intersection(session.family_session_ids):
                continue
            providers: dict[str, list[DeletedUsageEvent]] = {}
            for event in session.events:
                providers.setdefault(event.provider, []).append(event)
            for provider, events in providers.items():
                summary_day = self._parser.summarize_usage_events(events, day_start)
                summary_week = self._parser.summarize_usage_events(events, week_start)
                summary_month = self._parser.summarize_usage_events(events, month_start)
                (
                    models_day,
                    day_priced_event_count,
                    day_total_event_count,
                    day_latest_event_at,
                ) = self._model_insights_for_window(events, day_start)
                (
                    models_week,
                    week_priced_event_count,
                    week_total_event_count,
                    week_latest_event_at,
                ) = self._model_insights_for_window(events, week_start)
                (
                    models_month,
                    month_priced_event_count,
                    month_total_event_count,
                    month_latest_event_at,
                ) = self._model_insights_for_window(events, month_start)
                entries.append(
                    _UsageCacheEntry(
                        mtime=None,
                        file_size=None,
                        day_start=day_start,
                        week_start=week_start,
                        month_start=month_start,
                        model_provider=provider,
                        summary_day=summary_day,
                        summary_week=summary_week,
                        summary_month=summary_month,
                        session_id=session.session_id,
                        session_key=(
                            "deleted-session-"
                            + uuid.uuid5(uuid.NAMESPACE_URL, session.session_id).hex[:16]
                        ),
                        session_title=session.title,
                        workdir_name=session.workdir_name,
                        archived=True,
                        can_activate=False,
                        models_day=models_day,
                        models_week=models_week,
                        models_month=models_month,
                        day_priced_event_count=day_priced_event_count,
                        day_total_event_count=day_total_event_count,
                        week_priced_event_count=week_priced_event_count,
                        week_total_event_count=week_total_event_count,
                        month_priced_event_count=month_priced_event_count,
                        month_total_event_count=month_total_event_count,
                        day_latest_event_at=day_latest_event_at,
                        week_latest_event_at=week_latest_event_at,
                        month_latest_event_at=month_latest_event_at,
                    )
                )
        return entries

    def _session_meta_payload(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        payload: Mapping[str, Any] = {}
        payload_reader = getattr(self._parser, "session_meta_payload", None)
        if callable(payload_reader):
            raw_payload = payload_reader(records)
            if isinstance(raw_payload, Mapping):
                payload = raw_payload
        if not payload:
            for record in records:
                if record.get("type") != "session_meta":
                    continue
                raw_payload = record.get("payload")
                if isinstance(raw_payload, Mapping):
                    payload = raw_payload
                    break
        return payload

    @staticmethod
    def _canonical_session_id(value: object) -> str:
        candidate = str(value or "").strip()
        try:
            canonical = str(uuid.UUID(candidate))
        except (ValueError, AttributeError, TypeError):
            return ""
        return canonical if candidate.casefold() == canonical else ""

    @classmethod
    def _delegated_source_session_id(
        cls,
        records: Sequence[Mapping[str, Any]],
    ) -> str:
        """Recover the parent ID from legacy desktop subagent prompts."""
        for record in records:
            if record.get("type") != "response_item":
                continue
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                continue
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            text = message_text(payload)
            if "<codex_delegation" not in text:
                continue
            match = re.search(
                r"<source_thread_id>\s*([^<]+?)\s*</source_thread_id>",
                text,
                re.IGNORECASE,
            )
            if match:
                parent_id = cls._canonical_session_id(match.group(1))
                if parent_id:
                    return parent_id
        return ""

    def _is_archived_path(
        self,
        path: Path,
        scan_roots: Sequence[Path],
    ) -> bool:
        resolved = self._cache_path(path)
        for root in scan_roots:
            if root.name.casefold() != "archived_sessions":
                continue
            try:
                resolved.relative_to(self._cache_path(root))
            except ValueError:
                continue
            return True
        return False

    @staticmethod
    def _event_value(event: object, name: str, default: object = None) -> object:
        if isinstance(event, Mapping):
            return event.get(name, default)
        return getattr(event, name, default)

    @staticmethod
    def _window_event_time(
        event: object,
        start_at: datetime,
    ) -> datetime | None:
        timestamp = UsageSummaryCache._event_value(event, "timestamp")
        if not isinstance(timestamp, datetime):
            return None
        if start_at.tzinfo is None:
            event_time = (
                timestamp.astimezone().replace(tzinfo=None)
                if timestamp.tzinfo is not None
                else timestamp
            )
        elif timestamp.tzinfo is None:
            event_time = timestamp.replace(tzinfo=start_at.tzinfo)
        else:
            event_time = timestamp.astimezone(start_at.tzinfo)
        return event_time if event_time >= start_at else None

    @staticmethod
    def _nonnegative_event_int(event: object, name: str) -> int:
        try:
            return max(0, int(UsageSummaryCache._event_value(event, name, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _summary_for_usage_event(cls, event: object) -> tuple[UsageSummary, bool]:
        input_tokens = cls._nonnegative_event_int(event, "input_tokens")
        cached_tokens = min(
            input_tokens,
            cls._nonnegative_event_int(event, "cached_tokens"),
        )
        raw_cost = cls._event_value(event, "cost_usd")
        try:
            cost = None if raw_cost is None else max(0.0, float(raw_cost))
        except (TypeError, ValueError):
            cost = None
        return (
            UsageSummary(
                tokens=cls._nonnegative_event_int(event, "total_tokens"),
                input_tokens=input_tokens,
                cached_tokens=cached_tokens,
                cache_write_tokens=min(
                    input_tokens - cached_tokens,
                    cls._nonnegative_event_int(event, "cache_write_tokens"),
                ),
                output_tokens=cls._nonnegative_event_int(event, "output_tokens"),
                reasoning_tokens=cls._nonnegative_event_int(event, "reasoning_tokens"),
                cost_usd=round(cost or 0.0, 6),
            ),
            cost is not None,
        )

    @staticmethod
    def _merge_insight_aggregate(
        target: _UsageInsightAggregate,
        summary: UsageSummary,
        *,
        priced_event_count: int,
        total_event_count: int,
        latest_event_at: datetime | None,
    ) -> None:
        _merge_usage(target.summary, summary)
        target.priced_event_count += max(0, int(priced_event_count))
        target.total_event_count += max(0, int(total_event_count))
        if latest_event_at is not None and (
            target.latest_event_at is None or latest_event_at > target.latest_event_at
        ):
            target.latest_event_at = latest_event_at

    @classmethod
    def _model_insights_for_window(
        cls,
        events: Sequence[object],
        start_at: datetime,
    ) -> tuple[dict[str, _UsageInsightAggregate], int, int, datetime | None]:
        models: dict[str, _UsageInsightAggregate] = {}
        priced_event_count = 0
        total_event_count = 0
        latest_event_at: datetime | None = None
        for event in events:
            event_time = cls._window_event_time(event, start_at)
            if event_time is None:
                continue
            model = str(cls._event_value(event, "model", "") or "").strip() or "unknown"
            summary, priced = cls._summary_for_usage_event(event)
            aggregate = models.setdefault(model, _UsageInsightAggregate())
            cls._merge_insight_aggregate(
                aggregate,
                summary,
                priced_event_count=int(priced),
                total_event_count=1,
                latest_event_at=event_time,
            )
            priced_event_count += int(priced)
            total_event_count += 1
            if latest_event_at is None or event_time > latest_event_at:
                latest_event_at = event_time
        return models, priced_event_count, total_event_count, latest_event_at

    @staticmethod
    def _insight_aggregate_payload(
        aggregate: _UsageInsightAggregate,
    ) -> dict[str, object]:
        summary = aggregate.summary
        input_tokens = max(0, int(summary.input_tokens or 0))
        cached_tokens = min(input_tokens, max(0, int(summary.cached_tokens or 0)))
        total_event_count = max(0, int(aggregate.total_event_count))
        priced_event_count = min(
            total_event_count,
            max(0, int(aggregate.priced_event_count)),
        )
        cost_usd: float | None = round(
            max(0.0, float(summary.cost_usd or 0.0)),
            6,
        )
        if total_event_count > 0 and priced_event_count == 0:
            cost_usd = None
        return {
            "tokens": max(0, int(summary.tokens or 0)),
            "inputTokens": input_tokens,
            "cachedTokens": cached_tokens,
            "cacheWriteTokens": max(0, int(summary.cache_write_tokens or 0)),
            "outputTokens": max(0, int(summary.output_tokens or 0)),
            "reasoningTokens": max(0, int(summary.reasoning_tokens or 0)),
            "costUsd": cost_usd,
            "cacheRatio": (
                round(cached_tokens / input_tokens, 6)
                if input_tokens > 0
                else None
            ),
            "costCoverage": {
                "pricedEventCount": priced_event_count,
                "totalEventCount": total_event_count,
                "hasCompleteCost": priced_event_count == total_event_count,
            },
            "latestEventAt": (
                aggregate.latest_event_at.isoformat(timespec="seconds")
                if aggregate.latest_event_at is not None
                else ""
            ),
        }

    def _window_insights(
        self,
        entries: Sequence[_UsageCacheEntry],
        *,
        window: str,
        start_at: datetime,
        limit: int,
    ) -> dict[str, object]:
        total = _UsageInsightAggregate()
        provider_totals: dict[str, _UsageInsightAggregate] = {}
        model_totals: dict[tuple[str, str], _UsageInsightAggregate] = {}
        session_entries = {
            entry.session_id: entry
            for entry in entries
            if entry.session_id
        }
        session_groups: dict[str, _UsageInsightSessionAggregate] = {}

        def root_session(
            entry: _UsageCacheEntry,
        ) -> tuple[str, _UsageCacheEntry | None]:
            if not entry.session_id:
                return f"file:{entry.session_key}", entry
            current = entry
            chain = [entry.session_id]
            while current.parent_session_id:
                parent_id = current.parent_session_id
                if parent_id in chain:
                    cycle = chain[chain.index(parent_id) :]
                    root_id = min(cycle)
                    return root_id, session_entries.get(root_id)
                chain.append(parent_id)
                parent = session_entries.get(parent_id)
                if parent is None:
                    return parent_id, None
                current = parent
            return current.session_id, current

        for entry in entries:
            if window == "day":
                summary = entry.summary_day
                models = entry.models_day
                priced_event_count = entry.day_priced_event_count
                total_event_count = entry.day_total_event_count
                latest_event_at = entry.day_latest_event_at
            elif window == "week":
                summary = entry.summary_week
                models = entry.models_week
                priced_event_count = entry.week_priced_event_count
                total_event_count = entry.week_total_event_count
                latest_event_at = entry.week_latest_event_at
            else:
                summary = entry.summary_month
                models = entry.models_month
                priced_event_count = entry.month_priced_event_count
                total_event_count = entry.month_total_event_count
                latest_event_at = entry.month_latest_event_at
            aggregate = _UsageInsightAggregate(
                summary=replace(summary),
                priced_event_count=priced_event_count,
                total_event_count=total_event_count,
                latest_event_at=latest_event_at,
            )
            self._merge_insight_aggregate(
                total,
                aggregate.summary,
                priced_event_count=aggregate.priced_event_count,
                total_event_count=aggregate.total_event_count,
                latest_event_at=aggregate.latest_event_at,
            )
            provider_total = provider_totals.setdefault(
                entry.model_provider,
                _UsageInsightAggregate(),
            )
            self._merge_insight_aggregate(
                provider_total,
                aggregate.summary,
                priced_event_count=aggregate.priced_event_count,
                total_event_count=aggregate.total_event_count,
                latest_event_at=aggregate.latest_event_at,
            )
            for model, model_aggregate in models.items():
                target = model_totals.setdefault(
                    (entry.model_provider, model),
                    _UsageInsightAggregate(),
                )
                self._merge_insight_aggregate(
                    target,
                    model_aggregate.summary,
                    priced_event_count=model_aggregate.priced_event_count,
                    total_event_count=model_aggregate.total_event_count,
                    latest_event_at=model_aggregate.latest_event_at,
                )
            if aggregate.total_event_count or aggregate.summary.tokens:
                root_id, root_entry = root_session(entry)
                metadata_entry = root_entry or entry
                group = session_groups.get(root_id)
                if group is None:
                    group = _UsageInsightSessionAggregate(
                        session_id=(root_id if entry.session_id else ""),
                        session_key=metadata_entry.session_key,
                        title=metadata_entry.session_title,
                        provider=metadata_entry.model_provider,
                        workdir_name=(
                            metadata_entry.workdir_name or entry.workdir_name
                        ),
                        archived=bool(metadata_entry.archived),
                        can_activate=bool(
                            root_entry is not None and root_entry.can_activate
                        ),
                    )
                    session_groups[root_id] = group
                for model, model_aggregate in models.items():
                    model_key = (entry.model_provider, model)
                    target_model = group.models.setdefault(
                        model_key,
                        _UsageInsightAggregate(),
                    )
                    self._merge_insight_aggregate(
                        target_model,
                        model_aggregate.summary,
                        priced_event_count=model_aggregate.priced_event_count,
                        total_event_count=model_aggregate.total_event_count,
                        latest_event_at=model_aggregate.latest_event_at,
                    )
                self._merge_insight_aggregate(
                    group.usage,
                    aggregate.summary,
                    priced_event_count=aggregate.priced_event_count,
                    total_event_count=aggregate.total_event_count,
                    latest_event_at=aggregate.latest_event_at,
                )

        sessions = [
            {
                "sessionId": group.session_id,
                "sessionKey": group.session_key,
                "title": group.title,
                "provider": group.provider,
                "workdirName": group.workdir_name,
                "archived": group.archived,
                "canActivate": group.can_activate,
                "models": [
                    {
                        "model": model,
                        "provider": provider,
                        **self._insight_aggregate_payload(model_aggregate),
                    }
                    for (provider, model), model_aggregate in sorted(
                        group.models.items(),
                        key=lambda item: (
                            -int(item[1].summary.tokens or 0),
                            str(item[0][1]).casefold(),
                            str(item[0][0]).casefold(),
                        ),
                    )
                ],
                **self._insight_aggregate_payload(group.usage),
            }
            for group in session_groups.values()
        ]

        def rank_key(item: Mapping[str, object]) -> tuple[int, int, str]:
            return (
                -int(item.get("tokens") or 0),
                -int(item.get("inputTokens") or 0),
                str(
                    item.get("sessionKey")
                    or item.get("model")
                    or item.get("provider")
                    or ""
                ).casefold(),
            )

        model_rows = [
            {
                "model": model,
                "provider": provider,
                **self._insight_aggregate_payload(aggregate),
            }
            for (provider, model), aggregate in model_totals.items()
        ]
        provider_rows = [
            {
                "provider": provider,
                **self._insight_aggregate_payload(aggregate),
            }
            for provider, aggregate in provider_totals.items()
        ]
        sessions.sort(key=rank_key)
        cost_ranked_sessions = [
            item
            for item in sessions
            if item.get("costUsd") is not None
        ]
        cost_ranked_sessions.sort(
            key=lambda item: (
                -float(item.get("costUsd") or 0.0),
                -int(item.get("tokens") or 0),
                str(item.get("sessionKey") or "").casefold(),
            )
        )
        model_rows.sort(key=rank_key)
        provider_rows.sort(key=rank_key)
        totals_payload = self._insight_aggregate_payload(total)
        totals_payload["sessionCount"] = len(sessions)
        return {
            "startAt": start_at.isoformat(timespec="seconds"),
            "totals": totals_payload,
            "costCoverage": dict(totals_payload["costCoverage"]),
            "sessions": sessions[:limit],
            "topSessionsByUsage": sessions[:USAGE_INSIGHTS_TOP_SESSION_LIMIT],
            "topSessionsByCost": cost_ranked_sessions[
                :USAGE_INSIGHTS_TOP_SESSION_LIMIT
            ],
            "models": model_rows[:limit],
            "providers": provider_rows[:limit],
        }

    def insights(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        included_providers: Iterable[str] | None = None,
        limit: int = 8,
    ) -> dict[str, object]:
        """Project already-cached usage contributions without filesystem work."""
        sessions_root = self._cache_path(sessions_root)
        scan_roots = self._scan_roots(sessions_root)
        scan_key = (scan_roots, day_start, week_start)
        month_start = day_start - timedelta(days=29)
        ready = self._last_scan_key == scan_key
        providers = None
        if included_providers is not None:
            providers = {
                str(provider or "").strip().lower()
                for provider in included_providers
                if str(provider or "").strip()
            }
        entries = [
            entry
            for path, entry in self._entries.items()
            if ready
            and entry.day_start == day_start
            and entry.week_start == week_start
            and entry.month_start == month_start
            and self._path_under_scan_roots(path, scan_roots)
            and (providers is None or entry.model_provider in providers)
        ]
        deleted_entries = list(self._deleted_entries) if ready else []
        if providers is not None:
            deleted_entries = [
                entry
                for entry in deleted_entries
                if entry.model_provider in providers
            ]
        entries.extend(deleted_entries)
        row_limit = max(1, min(100, int(limit)))
        return {
            "ready": ready,
            "revision": int(self._insights_revision),
            "generatedAt": (
                self._insights_generated_at.isoformat(timespec="seconds")
                if self._insights_generated_at is not None
                else ""
            ),
            "providerScope": sorted(providers) if providers is not None else None,
            "today": self._window_insights(
                entries,
                window="day",
                start_at=day_start,
                limit=row_limit,
            ),
            "week": self._window_insights(
                entries,
                window="week",
                start_at=week_start,
                limit=row_limit,
            ),
            "month": self._window_insights(
                entries,
                window="month",
                start_at=month_start,
                limit=row_limit,
            ),
        }

    def summarize(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        allow_stale: bool = False,
        force_rescan: bool = False,
        refresh_paths: Iterable[Path] = (),
        included_providers: Iterable[str] | None = None,
    ) -> tuple[UsageSummary, UsageSummary]:
        now = time.monotonic()
        sessions_root = self._cache_path(sessions_root)
        scan_roots = self._scan_roots(sessions_root)
        scan_key = (scan_roots, day_start, week_start)
        refresh_path_tuple = tuple(
            dict.fromkeys(self._cache_path(path) for path in refresh_paths)
        )
        if not force_rescan and self._last_scan_key == scan_key and refresh_path_tuple:
            self._refresh_paths(
                refresh_path_tuple,
                scan_roots,
                day_start,
                week_start,
            )
            self._last_scan_at = now
        if allow_stale and self._last_scan_key == scan_key:
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )
        if (
            not force_rescan
            and self._last_scan_key == scan_key
            and now - self._last_scan_at < self._min_rescan_seconds
        ):
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )

        day_total = UsageSummary()
        week_total = UsageSummary()
        previous_scan_key = self._last_scan_key
        revision_before_scan = self._insights_revision
        previous_deleted_entries = list(self._deleted_entries)

        existing_roots = [root for root in scan_roots if root.exists()]
        if not existing_roots:
            had_entries = bool(self._entries)
            self._entries.clear()
            deleted_entries = self._deleted_usage_entries(day_start, week_start, set())
            self._deleted_entries = deleted_entries
            if deleted_entries != previous_deleted_entries:
                self._touch_insights()
            for entry in deleted_entries:
                _merge_usage(day_total, entry.summary_day)
                _merge_usage(week_total, entry.summary_week)
            self._last_scan_key = scan_key
            self._last_scan_at = now
            self._last_day_total = day_total
            self._last_week_total = week_total
            if had_entries or previous_scan_key != scan_key:
                self._touch_insights()
            return self._totals_for_providers(
                scan_roots,
                day_start,
                week_start,
                included_providers,
            )

        seen_paths: set[Path] = set()
        for root in existing_roots:
            archived = root.name.casefold() == "archived_sessions"
            for path in root.rglob("*.jsonl"):
                path = self._cache_path(path)
                seen_paths.add(path)
                summary_day, summary_week, _summary_month = self._summaries_for_file(
                    path,
                    day_start,
                    week_start,
                    archived=archived,
                )
                _merge_usage(day_total, summary_day)
                _merge_usage(week_total, summary_week)

        for cached_path in list(self._entries):
            if cached_path not in seen_paths:
                del self._entries[cached_path]
                self._touch_insights()

        live_session_ids = {
            entry.session_id for entry in self._entries.values() if entry.session_id
        }
        self._deleted_entries = self._deleted_usage_entries(
            day_start,
            week_start,
            live_session_ids,
        )
        if self._deleted_entries != previous_deleted_entries:
            self._touch_insights()
        for entry in self._deleted_entries:
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)

        self._last_scan_key = scan_key
        self._last_scan_at = now
        self._last_day_total = replace(day_total)
        self._last_week_total = replace(week_total)
        if previous_scan_key != scan_key and revision_before_scan == self._insights_revision:
            self._touch_insights()
        return self._totals_for_providers(
            scan_roots,
            day_start,
            week_start,
            included_providers,
        )

    def _totals_for_providers(
        self,
        scan_roots: Sequence[Path],
        day_start: datetime,
        week_start: datetime,
        included_providers: Iterable[str] | None,
    ) -> tuple[UsageSummary, UsageSummary]:
        if included_providers is None:
            return replace(self._last_day_total), replace(self._last_week_total)
        providers = {
            str(provider or "").strip().lower()
            for provider in included_providers
            if str(provider or "").strip()
        }
        day_total = UsageSummary()
        week_total = UsageSummary()
        for path, entry in self._entries.items():
            if entry.day_start != day_start or entry.week_start != week_start:
                continue
            if entry.model_provider not in providers:
                continue
            if not self._path_under_scan_roots(path, scan_roots):
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)
        for entry in self._deleted_entries:
            if entry.model_provider not in providers:
                continue
            _merge_usage(day_total, entry.summary_day)
            _merge_usage(week_total, entry.summary_week)
        return day_total, week_total

    def family_lifetime_usage(
        self,
        session_id: str,
        *,
        included_providers: Iterable[str] | None = None,
    ) -> UsageSummary:
        """Sum lifetime usage for a root session and its subagent children."""
        root_id = self._canonical_session_id(session_id)
        total = UsageSummary()
        if not root_id:
            return total
        providers = None
        if included_providers is not None:
            providers = {
                str(provider or "").strip().lower()
                for provider in included_providers
                if str(provider or "").strip()
            }
        session_entries = {
            entry.session_id: entry
            for entry in list(self._entries.values()) + list(self._deleted_entries)
            if entry.session_id
        }

        def root_of(entry: _UsageCacheEntry) -> str:
            if not entry.session_id:
                return ""
            current = entry
            chain = [entry.session_id]
            while current.parent_session_id:
                parent_id = current.parent_session_id
                if parent_id in chain:
                    return min(chain[chain.index(parent_id) :])
                chain.append(parent_id)
                parent = session_entries.get(parent_id)
                if parent is None:
                    return parent_id
                current = parent
            return current.session_id

        for entry in list(self._entries.values()) + list(self._deleted_entries):
            if providers is not None and entry.model_provider not in providers:
                continue
            member_id = entry.session_id or ""
            if member_id != root_id and root_of(entry) != root_id:
                continue
            summary = entry.summary_all
            if summary.tokens <= 0 and float(summary.cost_usd or 0.0) <= 0.0:
                # Older cache entries or deleted ledger rows without lifetime.
                summary = entry.summary_month
            _merge_usage(total, summary)
        return total

    def _path_under_scan_roots(self, path: Path, scan_roots: Sequence[Path]) -> bool:
        resolved = self._cache_path(path)
        for root in scan_roots:
            root_resolved = self._cache_path(root)
            try:
                resolved.relative_to(root_resolved)
            except ValueError:
                continue
            return True
        return False

    def _entry_for_window(
        self,
        path: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> _UsageCacheEntry | None:
        entry = self._entries.get(path)
        if (
            entry is not None
            and entry.day_start == day_start
            and entry.week_start == week_start
            and entry.month_start == day_start - timedelta(days=29)
        ):
            return entry
        return None

    def _refresh_paths(
        self,
        paths: Sequence[Path],
        scan_roots: Sequence[Path],
        day_start: datetime,
        week_start: datetime,
    ) -> None:
        day_total = replace(self._last_day_total)
        week_total = replace(self._last_week_total)
        empty = UsageSummary()

        for path in paths:
            if not self._path_under_scan_roots(path, scan_roots):
                continue
            old_entry = self._entry_for_window(path, day_start, week_start)
            old_day = old_entry.summary_day if old_entry is not None else empty
            old_week = old_entry.summary_week if old_entry is not None else empty

            if path.exists():
                new_day, new_week, _new_month = self._summaries_for_file(
                    path,
                    day_start,
                    week_start,
                    force=True,
                    archived=self._is_archived_path(path, scan_roots),
                )
            else:
                if self._entries.pop(path, None) is not None:
                    self._touch_insights()
                new_day = empty
                new_week = empty

            day_total = _replace_usage(day_total, old_day, new_day)
            week_total = _replace_usage(week_total, old_week, new_week)

        self._last_day_total = day_total
        self._last_week_total = week_total

    def _summaries_for_file(
        self,
        path: Path,
        day_start: datetime,
        week_start: datetime,
        *,
        force: bool = False,
        archived: bool | None = None,
    ) -> tuple[UsageSummary, UsageSummary, UsageSummary]:
        try:
            stat = path.stat()
        except OSError:
            if self._entries.pop(path, None) is not None:
                self._touch_insights()
            return UsageSummary(), UsageSummary(), UsageSummary()

        entry = self._entries.get(path)
        if (
            not force
            and entry is not None
            and entry.mtime == stat.st_mtime
            and entry.file_size == stat.st_size
            and entry.day_start == day_start
            and entry.week_start == week_start
            and entry.month_start == day_start - timedelta(days=29)
            and (archived is None or entry.archived == archived)
        ):
            return entry.summary_day, entry.summary_week, entry.summary_month

        try:
            records = self._parser.load_records_lenient(path)
        except OSError:
            if self._entries.pop(path, None) is not None:
                self._touch_insights()
            return UsageSummary(), UsageSummary(), UsageSummary()

        events = self._parser.usage_events(records)
        provider_reader = getattr(self._parser, "session_model_provider", None)
        model_provider = (
            str(provider_reader(records) or "").strip().lower()
            if callable(provider_reader)
            else "unknown"
        ) or "unknown"
        summary_day = self._parser.summarize_usage_events(events, day_start)
        summary_week = self._parser.summarize_usage_events(events, week_start)
        month_start = day_start - timedelta(days=29)
        summary_month = self._parser.summarize_usage_events(events, month_start)
        lifetime_start = day_start - timedelta(days=36500)
        summary_all = self._parser.summarize_usage_events(events, lifetime_start)
        (
            models_day,
            day_priced_event_count,
            day_total_event_count,
            day_latest_event_at,
        ) = self._model_insights_for_window(events, day_start)
        (
            models_week,
            week_priced_event_count,
            week_total_event_count,
            week_latest_event_at,
        ) = self._model_insights_for_window(events, week_start)
        (
            models_month,
            month_priced_event_count,
            month_total_event_count,
            month_latest_event_at,
        ) = self._model_insights_for_window(events, month_start)
        session_meta = self._session_meta_payload(records)
        session_id = self._canonical_session_id(session_meta.get("id"))
        session_title = " ".join(
            str(
                session_meta.get("title")
                or session_meta.get("session_title")
                or session_meta.get("name")
                or ""
            ).split()
        )
        _thread_source, raw_parent_id, _agent_nickname, is_subagent = (
            extract_session_thread_identity(session_meta)
        )
        if is_subagent and not raw_parent_id:
            raw_parent_id = self._delegated_source_session_id(records)
        parent_session_id = (
            self._canonical_session_id(raw_parent_id) if is_subagent else ""
        )
        if parent_session_id == session_id:
            parent_session_id = ""
        is_archived = bool(archived) if archived is not None else (
            "archived_sessions" in {part.casefold() for part in path.parts}
        )
        self._entries[path] = _UsageCacheEntry(
            mtime=stat.st_mtime,
            file_size=stat.st_size,
            day_start=day_start,
            week_start=week_start,
            month_start=month_start,
            model_provider=model_provider,
            summary_day=summary_day,
            summary_week=summary_week,
            summary_month=summary_month,
            summary_all=summary_all,
            session_id=session_id,
            parent_session_id=parent_session_id,
            session_key=path.stem,
            session_title=session_title,
            workdir_name=_workdir_leaf(session_meta.get("cwd")),
            archived=is_archived,
            can_activate=bool(session_id) and not is_archived,
            models_day=models_day,
            models_week=models_week,
            models_month=models_month,
            day_priced_event_count=day_priced_event_count,
            day_total_event_count=day_total_event_count,
            week_priced_event_count=week_priced_event_count,
            week_total_event_count=week_total_event_count,
            month_priced_event_count=month_priced_event_count,
            month_total_event_count=month_total_event_count,
            day_latest_event_at=day_latest_event_at,
            week_latest_event_at=week_latest_event_at,
            month_latest_event_at=month_latest_event_at,
        )
        self._touch_insights()
        return summary_day, summary_week, summary_month


class _UsageInsightsWorker:
    """Run explicit usage refreshes without blocking the renderer loop."""

    def __init__(self, context: object) -> None:
        self._context = context
        self._lock = threading.Lock()
        self._wake = Event()
        self._closed = Event()
        self._request_id = ""
        self._worker = threading.Thread(
            target=self._run,
            name="codex-usage-hud-insights",
            daemon=True,
        )
        self._worker.start()

    def request_refresh(self, *, request_id: str = "") -> bool:
        if self._closed.is_set():
            return False
        with self._lock:
            self._request_id = str(request_id or "")
        current = dict(
            getattr(self._context, "usage_insights_payload", {}) or {}
        )
        current.update(
            {
                "state": "loading",
                "error": "",
                "requestId": str(request_id or ""),
            }
        )
        setattr(self._context, "usage_insights_payload", current)
        self._publish(current)
        self._wake.set()
        return True

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)

    def _run(self) -> None:
        while not self._closed.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._closed.is_set():
                break
            with self._lock:
                request_id = self._request_id
                self._request_id = ""
            try:
                context = self._context
                day_start, week_start = current_budget_windows(
                    getattr(context, "user_config", UserConfig.defaults())
                )
                usage_cache = getattr(context, "usage_cache")
                usage_cache.summarize(
                    Path(getattr(context, "sessions_root")),
                    day_start,
                    week_start,
                    force_rescan=True,
                    included_providers=_effective_provider_scope(context),
                )
                payload = _build_usage_insights_payload(context)
                payload["state"] = "ready" if payload.get("ready") else "idle"
                payload["requestId"] = request_id
            except Exception as exc:
                _LOGGER.exception("usage_insights_refresh_failed")
                payload = {
                    "state": "failed",
                    "ready": False,
                    "requestId": request_id,
                    "error": str(exc) or type(exc).__name__,
                }
            setattr(self._context, "usage_insights_payload", payload)
            self._publish(payload)

    def _publish(self, payload: Mapping[str, object]) -> None:
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "usage_insights_changed",
                source="usage_insights",
                context={
                    "requestId": str(payload.get("requestId") or ""),
                    "revision": int(payload.get("revision") or 0),
                    "state": str(payload.get("state") or ""),
                },
            )




class _SessionCleanupWorker:
    """Serialize explicit session inventory and official delete commands."""

    _ACTIONS = {
        "sessionCleanupScan",
        "sessionCleanupPreview",
        "sessionCleanupExecute",
        "sessionCleanupCancel",
    }

    def __init__(self, context: object, manager: SessionCleanupManager) -> None:
        self._context = context
        self.manager = manager
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._closed = Event()
        self._worker = threading.Thread(
            target=self._run,
            name="codex-usage-hud-session-cleanup",
            daemon=True,
        )
        self._worker.start()

    def enqueue(self, command: Mapping[str, object]) -> dict[str, object]:
        action = str(command.get("action") or "").strip()
        if action not in self._ACTIONS:
            raise SessionCleanupError("unsupported session-cleanup command")
        if self._closed.is_set():
            raise SessionCleanupError("session cleanup worker is closed")
        request_id = str(command.get("requestId") or "").strip() or uuid.uuid4().hex
        payload = dict(command)
        payload["requestId"] = request_id
        if action == "sessionCleanupCancel":
            self._publish(self.manager.cancel(request_id=request_id))
        else:
            self._publish(
                self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="scanning" if action == "sessionCleanupScan" else "accepted",
                    progress=0,
                )
            )
            self._queue.put_nowait(payload)
        return {"status": "accepted", "requestId": request_id, "action": action}

    def close(self, timeout_seconds: float = 2.0) -> bool:
        if self._closed.is_set():
            return not self._worker.is_alive()
        self._closed.set()
        self._queue.put_nowait(None)
        if self._worker is not threading.current_thread() and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, float(timeout_seconds)))
        return not self._worker.is_alive()

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                return
            action = str(command.get("action") or "")
            request_id = str(command.get("requestId") or "")
            try:
                if action == "sessionCleanupScan":
                    previous_publisher = getattr(
                        self.manager, "progress_publisher", None
                    )
                    self.manager.progress_publisher = self._publish
                    try:
                        snapshot = self.manager.scan(request_id=request_id)
                    finally:
                        self.manager.progress_publisher = previous_publisher
                elif action == "sessionCleanupPreview":
                    item_ids = _cleanup_string_list(
                        command.get("itemIds") or command.get("sessionIds")
                    )
                    snapshot = self.manager.preview(
                        item_ids,
                        str(command.get("inventoryRevision") or ""),
                        request_id=request_id,
                    )
                else:
                    item_ids = _cleanup_string_list(
                        command.get("itemIds") or command.get("sessionIds")
                    )
                    snapshot = self.manager.execute(
                        item_ids,
                        str(command.get("inventoryRevision") or ""),
                        str(command.get("confirmationToken") or ""),
                        request_id=request_id,
                    )
                    operation = snapshot.get("operation")
                    if (
                        isinstance(operation, Mapping)
                        and int(operation.get("deletedCount") or 0) > 0
                    ):
                        self._refresh_usage_after_delete(request_id)
            except Exception as exc:
                snapshot = self.manager.mark_operation(
                    request_id=request_id,
                    action=action,
                    state="failed",
                    progress=100,
                    error=str(exc) or type(exc).__name__,
                )
            self._publish(snapshot)

    def _refresh_usage_after_delete(self, request_id: str) -> None:
        try:
            day_start, week_start = current_budget_windows(
                getattr(self._context, "user_config", UserConfig.defaults())
            )
            usage_cache = getattr(self._context, "usage_cache")
            usage_cache.summarize(
                Path(getattr(self._context, "sessions_root")),
                day_start,
                week_start,
                force_rescan=True,
                included_providers=_effective_provider_scope(self._context),
            )
            payload = _refresh_usage_insights_payload(self._context)
            event_bus = getattr(self._context, "runtime_events", None)
            publish = getattr(event_bus, "publish", None)
            if callable(publish):
                publish(
                    "usage_insights_changed",
                    source="session_cleanup",
                    context={
                        "requestId": request_id,
                        "revision": int(payload.get("revision") or 0),
                        "state": str(payload.get("state") or ""),
                    },
                )
        except Exception as exc:
            _LOGGER.exception("deleted_session_usage_refresh_failed error=%s", exc)

    def _publish(self, payload: Mapping[str, object]) -> None:
        snapshot = dict(payload)
        setattr(self._context, "session_cleanup_payload", snapshot)
        event_bus = getattr(self._context, "runtime_events", None)
        publish = getattr(event_bus, "publish", None)
        if not callable(publish):
            return
        operation = snapshot.get("operation")
        values = operation if isinstance(operation, Mapping) else {}
        publish(
            "session_cleanup_changed",
            source="session_cleanup",
            context={
                "requestId": str(values.get("requestId") or ""),
                "action": str(values.get("action") or ""),
                "state": str(values.get("state") or ""),
                "revision": str(snapshot.get("revision") or ""),
            },
        )


def _cleanup_string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _session_path_key(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _active_work_scan_roots(sessions_root: Path) -> tuple[Path, ...]:
    return UsageSummaryCache._scan_roots(sessions_root)


def _recent_session_files(
    sessions_root: Path,
    *,
    current_path: Path | None = None,
    limit: int = ACTIVE_WORK_CANDIDATE_LIMIT,
) -> list[Path]:
    paths: dict[str, tuple[Path, float]] = {}
    if current_path is not None:
        try:
            paths[_session_path_key(current_path)] = (
                current_path,
                current_path.stat().st_mtime,
            )
        except OSError:
            paths[_session_path_key(current_path)] = (current_path, 0.0)
    for root in _active_work_scan_roots(sessions_root):
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*.jsonl")
            for path in iterator:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                paths[_session_path_key(path)] = (path, mtime)
        except OSError:
            continue
    ordered = sorted(paths.values(), key=lambda item: item[1], reverse=True)
    return [path for path, _mtime in ordered[: max(1, int(limit))]]


def _work_activity_label(value: str) -> str:
    labels = {
        "idle": "空闲",
        "user": "用户输入",
        "agent": "助手消息",
        "tool call": "调用工具",
        "tool output": "工具返回",
        "assistant": "助手输出",
        "confirmed": "Token确认",
    }
    return labels.get(value, value)


def _tool_invocation_parts(detail: str) -> tuple[str, str]:
    text = " ".join(str(detail or "").split())
    name, _space, args = text.partition(" ")
    return name, args


def _tool_display_name(name: str) -> str:
    value = str(name or "").strip()
    if not value:
        return "工具"
    value = value.rsplit(".", 1)[-1]
    return value.replace("_", " ")


def _extract_tool_file_target(text: str) -> str:
    patterns = [
        r"(?:Update|Add|Delete) File:\s*([^\r\n]+)",
        r'"(?:file|path)"\s*:\s*"([^"]+)"',
        r"'(?:file|path)'\s*:\s*'([^']+)'",
        r"([A-Za-z]:\\[^\s\"']+\.[A-Za-z0-9_]+)",
        r"([\w./\\-]+\.(?:py|ts|tsx|js|jsx|json|md|css|html|yaml|yml|toml|txt))",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        target = match.group(1).strip().strip("`'\".,")
        if target:
            normalized = target.replace("/", "\\")
            return _compact_work_text(normalized, 48)
    return ""


def _tool_status_text(detail: str) -> str:
    name, args = _tool_invocation_parts(detail)
    lower_name = name.lower()
    target = _extract_tool_file_target(args)
    if "apply_patch" in lower_name or "edit" in lower_name:
        return f"正在编辑 {target}" if target else "正在编辑文件"
    if "shell" in lower_name:
        command = ""
        try:
            parsed_args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            parsed_args = {}
        if isinstance(parsed_args, Mapping):
            command = str(parsed_args.get("command") or "").strip()
        if command:
            file_target = _extract_tool_file_target(command)
            if file_target and re.search(r"\b(apply_patch|Set-Content|Add-Content)\b", command):
                return f"正在编辑 {file_target}"
            return f"正在运行 {_compact_work_text(command, 52)}"
        return "正在运行命令"
    if "view_image" in lower_name or "screenshot" in lower_name:
        return "正在查看界面"
    if "puppeteer" in lower_name:
        return "正在操作浏览器"
    if "read" in lower_name or "open" in lower_name:
        return f"正在读取 {target}" if target else "正在读取内容"
    if target:
        return f"正在处理 {target}"
    return f"正在调用 {_tool_display_name(name)}"


def _work_status_text(
    snapshot: ParsedSession,
    status_value: str,
    status_label: str,
) -> str:
    activity = snapshot.activity
    if status_value == "recent":
        return "已完成"
    if status_value == "error":
        return _compact_work_text(
            snapshot.request.error or snapshot.error or status_label,
            80,
        )
    if status_value == "waiting_user":
        return "等待用户输入"
    if activity.kind == "tool call":
        return _tool_status_text(activity.detail)
    if activity.kind == "tool output":
        return "正在读取工具结果"
    if status_value == "tool":
        return _tool_status_text(activity.detail)
    if status_value in {"running", "active"}:
        if activity.kind in {"agent", "assistant"}:
            return "正在输出"
        model_name = _current_task_model_name(snapshot)
        return f"{model_name} 正在思考" if model_name else "正在思考"
    return status_label


def _elapsed_compact(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return ""
    if started_at.tzinfo is None:
        current = now.replace(tzinfo=None) if now is not None else datetime.now()
    else:
        current = (now or datetime.now().astimezone()).astimezone(started_at.tzinfo)
    seconds = max(0, int((current - started_at).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def is_subagent_session(snapshot: ParsedSession | object) -> bool:
    """True when a parsed session is a Codex multi-agent subagent thread."""
    flag = getattr(snapshot, "is_subagent", False)
    if flag:
        return True
    thread_source = str(getattr(snapshot, "thread_source", "") or "").strip().lower()
    if thread_source == "subagent":
        return True
    parent_thread_id = str(getattr(snapshot, "parent_thread_id", "") or "").strip()
    return bool(parent_thread_id)


def is_independent_desktop_delegation(snapshot: ParsedSession | object) -> bool:
    """True when Desktop promoted a subagent into its own visible thread."""
    if not is_subagent_session(snapshot):
        return False
    if str(getattr(snapshot, "client_kind", "") or "").strip().lower() != "app":
        return False
    if str(getattr(snapshot, "parent_thread_id", "") or "").strip():
        return False
    if str(getattr(snapshot, "agent_nickname", "") or "").strip():
        return False
    # Internal collaboration agents carry a structural parent or agent identity.
    # Desktop handoff threads currently retain only thread_source=subagent; their
    # latest task prompt changes over time, so prompt text is not an identity key.
    return True


def _hide_from_work_overlay(snapshot: ParsedSession | object) -> bool:
    return is_subagent_session(snapshot) and not is_independent_desktop_delegation(snapshot)


def _work_status_from_snapshot(
    snapshot: ParsedSession,
    *,
    now: datetime,
) -> tuple[str, str, bool] | None:
    if snapshot.task_aborted_at is not None:
        return None
    activity_detail = snapshot.activity.detail.lower()
    request_status = snapshot.request.status
    if request_status == "error" or snapshot.request.error:
        return "error", "出错", False
    if snapshot.task_completed_at is not None:
        return "recent", "刚完成", False
    if snapshot.final_answer_at is not None and (
        _datetime_age_seconds(snapshot.final_answer_at, now)
        >= FINAL_ANSWER_COMPLETION_GRACE_SECONDS
    ):
        return "recent", "刚完成", True
    if request_status == "running":
        return "running", "运行中", False
    if snapshot.activity.kind == "tool call" and activity_detail.startswith(
        "request_user_input"
    ):
        return "waiting_user", "等待用户", False
    if snapshot.activity.kind == "tool call":
        return "tool", "工具执行", False
    if snapshot.slow.current_gap_active:
        return "active", "处理中", False
    return None


def _work_item_model_startup_timed_out(
    snapshot: ParsedSession,
    *,
    now: datetime,
) -> bool:
    """Whether a CLI task never progressed past its initial user message."""
    if (
        snapshot.task_completed_at is not None
        or snapshot.task_aborted_at is not None
        or snapshot.final_answer_at is not None
        or snapshot.request.status != "running"
        or snapshot.activity.kind != "user"
    ):
        return False
    updated_at = (
        snapshot.request.updated_at
        or snapshot.activity.timestamp
        or snapshot.last_event_time
        or snapshot.refreshed_at
    )
    return bool(
        updated_at is not None
        and _datetime_age_seconds(updated_at, now)
        > ACTIVE_WORK_MODEL_STARTUP_STALE_SECONDS
    )


def _work_item_from_snapshot(
    snapshot: ParsedSession,
    *,
    current: bool,
    title: str = "",
    source: str = "",
    now: datetime | None = None,
) -> WorkStatusItem | None:
    current_time = now or datetime.now().astimezone()
    status = _work_status_from_snapshot(snapshot, now=current_time)
    if status is None:
        return None
    status_value, status_label, pending_accounting = status
    if _work_item_model_startup_timed_out(snapshot, now=current_time):
        # A task_started/user_message pair can be left behind when a CLI resume
        # exits before model work begins. It has no terminal event, but must not
        # keep an active bubble (and its live elapsed clock) for four hours.
        return None

    updated_at = (
        snapshot.request.updated_at
        or snapshot.activity.timestamp
        or snapshot.last_event_time
        or snapshot.refreshed_at
    )
    if updated_at is not None:
        current_for_age = (
            current_time.astimezone(updated_at.tzinfo)
            if updated_at.tzinfo is not None
            else current_time.replace(tzinfo=None)
        )
        age_seconds = (current_for_age - updated_at).total_seconds()
        if status_value != "recent" and age_seconds > ACTIVE_WORK_STALE_SECONDS:
            return None

    display_title = (
        title.strip()
        or snapshot.session_title.strip()
        or str(getattr(snapshot, "agent_nickname", "") or "").strip()
        or str(snapshot.session_id or "").strip()
        or "Codex 工作"
    )
    detail = (
        snapshot.request.error
        or snapshot.activity.detail
        or snapshot.error
        or "等待更多活动日志"
    )
    if snapshot.activity.kind:
        detail = f"{_work_activity_label(snapshot.activity.kind)}：{detail}"
    round_index = _current_task_round_index(snapshot)
    model_name = _current_task_model_name(snapshot)
    status_text = _work_status_text(snapshot, status_value, status_label)
    last_text = snapshot.last_output.detail.strip()
    started_at = snapshot.task_started_at or snapshot.request.started_at
    elapsed_reference = (
        snapshot.task_completed_at
        if status_value == "recent" and snapshot.task_completed_at is not None
        else current_time
    )
    elapsed = _elapsed_compact(started_at, now=elapsed_reference)
    elapsed_text = f"已处理 {elapsed}" if elapsed else ""
    tokens = _current_task_tokens(snapshot)
    progress_parts = []
    if tokens:
        progress_parts.append(f"{_format_tokens(tokens)} tokens")
    if source or snapshot.selection_source:
        progress_parts.append(source or snapshot.selection_source)
    progress = " | ".join(progress_parts)
    session_id = str(snapshot.session_id or "").strip()
    item_id = session_id or _session_path_key(snapshot.session_path) or display_title
    return WorkStatusItem(
        id=str(item_id),
        title=_compact_work_text(display_title, 56),
        session_id=session_id,
        target_title=display_title.strip(),
        round_index=round_index,
        model_name=model_name,
        status=status_value,
        status_label=status_label,
        detail=_compact_work_text(detail, 120),
        status_text=_compact_work_text(status_text, 80),
        last_text=_compact_work_text(last_text, 180),
        elapsed_text=elapsed_text,
        progress=progress,
        tokens_text=_format_tokens(tokens),
        cost_text=_format_cost_compact(_current_task_cost(snapshot)),
        cache_hit_text=_current_task_cache_hit_text(snapshot),
        source=source or snapshot.selection_source,
        workdir=str(snapshot.cwd or "").strip(),
        workdir_name=_compact_work_text(_workdir_leaf(snapshot.cwd), 32),
        model_provider=snapshot.model_provider,
        client_kind=snapshot.client_kind,
        # The overlay treats a promoted Desktop delegation as a normal visible
        # session. Keep the internal-subagent marker only for folded agents so
        # both visible-item caches can retain promoted work across refreshes.
        is_subagent=_hide_from_work_overlay(snapshot),
        agent_nickname=str(getattr(snapshot, "agent_nickname", "") or "").strip(),
        parent_thread_id=str(getattr(snapshot, "parent_thread_id", "") or "").strip(),
        session_started_at=snapshot.session_started_at,
        task_started_at=snapshot.task_started_at,
        started_at=started_at,
        updated_at=updated_at,
        current=current,
        pending_accounting=pending_accounting,
    )


def _compact_work_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _primary_screen_height() -> int:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            return max(1, int(user32.GetSystemMetrics(1)))
        except Exception:
            pass
    return 1080


def _work_overlay_screen_max_items(screen_height: int | None = None) -> int:
    height = _primary_screen_height() if screen_height is None else int(screen_height)
    return _work_overlay_max_items_for_screen_height(height)


def _work_overlay_item_limit_for_context(context: object) -> int:
    config = getattr(context, "user_config", None)
    configured = normalize_work_overlay_max_items(
        getattr(config, "work_overlay_max_items", ACTIVE_WORK_ITEM_LIMIT),
        ACTIVE_WORK_ITEM_LIMIT,
    )
    if configured <= 0:
        return 0
    return min(configured, _work_overlay_screen_max_items())


def _work_overlay_runtime_task_key(item: WorkStatusItem) -> str:
    item_id = str(item.id or item.session_id or "").strip()
    started_at = _iso_or_empty(item.task_started_at or item.started_at)
    if not item_id and not started_at:
        return ""
    return json.dumps(
        {
            "id": item_id,
            "taskStartedAt": started_at,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _work_overlay_seen_task_keys(context: object) -> set[str]:
    seen = getattr(context, "_work_overlay_seen_task_keys", None)
    if isinstance(seen, set):
        return seen
    seen = set()
    try:
        setattr(context, "_work_overlay_seen_task_keys", seen)
    except Exception:
        pass
    return seen


def _work_overlay_visible_item_cache(context: object) -> dict[str, WorkStatusItem]:
    cache = getattr(context, "_work_overlay_visible_item_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(context, "_work_overlay_visible_item_cache", cache)
    except Exception:
        pass
    return cache


def _work_overlay_published_item_cache(context: object) -> dict[str, WorkStatusItem]:
    cache = getattr(context, "_work_overlay_published_item_cache", None)
    if isinstance(cache, dict):
        return cache
    cache = {}
    try:
        setattr(context, "_work_overlay_published_item_cache", cache)
    except Exception:
        pass
    return cache


def _work_overlay_terminal_item_tasks(context: object) -> dict[str, str]:
    terminal = getattr(context, "_work_overlay_terminal_item_tasks", None)
    if isinstance(terminal, dict):
        return terminal
    terminal = {}
    try:
        setattr(context, "_work_overlay_terminal_item_tasks", terminal)
    except Exception:
        pass
    return terminal


def _work_overlay_item_sort_key(item: WorkStatusItem) -> tuple[float, float]:
    session_timestamp = item.session_started_at or item.started_at or item.updated_at
    task_timestamp = item.started_at or item.updated_at or item.session_started_at
    session_seconds = session_timestamp.timestamp() if session_timestamp is not None else 0.0
    task_seconds = task_timestamp.timestamp() if task_timestamp is not None else 0.0
    return session_seconds, task_seconds


def _work_overlay_item_updated_seconds(item: WorkStatusItem) -> float:
    updated_at = (
        item.updated_at
        or item.started_at
        or item.task_started_at
        or item.session_started_at
    )
    return updated_at.timestamp() if updated_at is not None else 0.0


def _refresh_visible_current_work_item(
    context: object,
    items: Sequence[WorkStatusItem],
    snapshot: ParsedSession,
) -> list[WorkStatusItem]:
    """Apply current-session state without waiting for the recent-work scan."""
    if _hide_from_work_overlay(snapshot):
        return list(items)
    session_id = str(snapshot.session_id or "").strip()
    if not session_id:
        return list(items)
    existing_index = next(
        (
            index
            for index, item in enumerate(items)
            if str(item.session_id or item.id).strip() == session_id
        ),
        None,
    )
    if existing_index is None:
        return list(items)
    refreshed = _work_item_from_snapshot(
        snapshot,
        current=True,
        title=snapshot.session_title,
        source=snapshot.selection_source,
    )
    if refreshed is None:
        if snapshot.task_aborted_at is None:
            return list(items)
        task_key = _iso_or_empty(snapshot.task_started_at or snapshot.request.started_at)
        if task_key:
            _work_overlay_terminal_item_tasks(context)[session_id] = task_key
        return [item for index, item in enumerate(items) if index != existing_index]
    updated = list(items)
    updated[existing_index] = refreshed
    return updated


def _stabilize_published_work_overlay_items(
    context: object,
    items: Sequence[WorkStatusItem],
) -> list[WorkStatusItem]:
    item_limit = _work_overlay_item_limit_for_context(context)
    cache = _work_overlay_published_item_cache(context)
    terminal = _work_overlay_terminal_item_tasks(context)
    if item_limit <= 0:
        cache.clear()
        return []

    now = datetime.now().astimezone()
    merged = {str(item.id): item for item in items if str(item.id or "").strip()}
    for item_id, item in list(merged.items()):
        cached_item = cache.get(item_id)
        if (
            cached_item is not None
            and _work_overlay_item_updated_seconds(item)
            < _work_overlay_item_updated_seconds(cached_item)
        ):
            item = replace(cached_item, current=item.current)
            merged[item_id] = item
        if cached_item is not None and cached_item.session_started_at is not None:
            stable_session_start = cached_item.session_started_at
            if item.session_started_at is not None:
                stable_session_start = min(
                    stable_session_start,
                    item.session_started_at,
                )
            if item.session_started_at != stable_session_start:
                item = replace(item, session_started_at=stable_session_start)
                merged[item_id] = item
        terminal_task = terminal.get(item_id)
        item_task = _iso_or_empty(item.task_started_at or item.started_at)
        if terminal_task and terminal_task == item_task:
            merged.pop(item_id, None)
        elif terminal_task:
            terminal.pop(item_id, None)

    provider_scope = _effective_notification_provider_scope(context, None)
    for item_id, item in list(merged.items()):
        if bool(getattr(item, "is_subagent", False)) and not item.current:
            merged.pop(item_id, None)
    for item_id, cached_item in list(cache.items()):
        if item_id in merged:
            continue
        if bool(getattr(cached_item, "is_subagent", False)):
            continue
        cached_task = _iso_or_empty(cached_item.task_started_at or cached_item.started_at)
        if terminal.get(item_id) == cached_task:
            continue
        if provider_scope is not None and cached_item.model_provider not in provider_scope:
            continue
        updated_at = (
            cached_item.updated_at
            or cached_item.started_at
            or cached_item.task_started_at
            or cached_item.session_started_at
        )
        if cached_item.status != "recent" and (
            updated_at is None
            or _datetime_age_seconds(updated_at, now) > ACTIVE_WORK_STALE_SECONDS
        ):
            continue
        merged[item_id] = replace(cached_item, current=False)

    stable = sorted(merged.values(), key=_work_overlay_item_sort_key, reverse=True)[
        :item_limit
    ]
    cache.clear()
    cache.update(
        {
            str(item.id): replace(item, current=False)
            for item in stable
            if str(item.id or "").strip()
        }
    )
    return stable


def _select_runtime_work_overlay_items(
    context: object,
    items: Sequence[WorkStatusItem],
    *,
    item_limit: int,
) -> list[WorkStatusItem]:
    seen_task_keys = _work_overlay_seen_task_keys(context)
    previously_seen_task_keys = set(seen_task_keys)
    now = datetime.now().astimezone()
    visible: list[WorkStatusItem] = []
    for item in items:
        if len(visible) >= item_limit:
            break
        task_key = _work_overlay_runtime_task_key(item)
        if item.status == "recent":
            updated_at = item.updated_at or item.started_at or item.task_started_at
            fresh = (
                updated_at is not None
                and _datetime_age_seconds(updated_at, now) <= WORK_OVERLAY_STALE_SECONDS
            )
            current_startup_summary = (
                item.current
                and not previously_seen_task_keys
                and item.tokens_text in {"", "0"}
                and item.cost_text in {"", "$0"}
            )
            if (
                current_startup_summary
                or (not item.current and fresh)
                or (task_key and task_key in previously_seen_task_keys)
            ):
                visible.append(item)
                if task_key:
                    seen_task_keys.add(task_key)
            continue
        visible.append(item)
        if task_key:
            seen_task_keys.add(task_key)
    return visible


def _effective_provider_scope(
    context: "RuntimeContext | object",
    snapshot: ParsedSession | None = None,
) -> frozenset[str] | None:
    """Resolve the provider scope used for usage, budgets, and adjustments."""
    if snapshot is not None and snapshot.client_kind == "app":
        observed_provider = str(snapshot.model_provider or "").strip().lower()
        if observed_provider and observed_provider != "unknown":
            setattr(context, "app_provider", observed_provider)
    app_provider = str(getattr(context, "app_provider", "") or "").strip().lower()
    config = getattr(context, "user_config", None)
    resolver = getattr(config, "effective_provider_scope", None)
    if callable(resolver):
        return resolver(app_provider)
    return None


def _background_usage_insights_summary(
    context: object,
    *,
    range_key: str,
) -> dict[str, object]:
    runtime = getattr(context, "background_usage_runtime", None)
    query = getattr(runtime, "query", None)
    if not callable(query):
        return {
            "available": False,
            "requestCount": 0,
            "totalTokens": 0,
            "estimatedCostUsd": None,
            "costComplete": False,
            "pendingCount": 0,
        }
    try:
        raw = query(
            range_key=range_key,
            feature="",
            model="",
            event_id="",
        )
        summary = raw.get("summary") if isinstance(raw, Mapping) else None
        values = dict(summary) if isinstance(summary, Mapping) else {}
        pending_count = 0
        if range_key == "today":
            pending_today = getattr(runtime, "pending_today", None)
            if callable(pending_today):
                pending_count = len(pending_today())
        return {
            "available": True,
            "requestCount": max(0, int(values.get("requestCount") or 0)),
            "totalTokens": max(0, int(values.get("totalTokens") or 0)),
            "estimatedCostUsd": values.get("estimatedCostUsd"),
            "costComplete": bool(values.get("costComplete", False)),
            "pendingCount": max(0, int(pending_count)),
            "range": range_key,
            "separateFromSessionTotals": True,
        }
    except Exception as exc:
        _LOGGER.debug(
            "usage_insights_background_summary_failed range=%s error=%s",
            range_key,
            exc,
        )
        return {
            "available": False,
            "requestCount": 0,
            "totalTokens": 0,
            "estimatedCostUsd": None,
            "costComplete": False,
            "pendingCount": 0,
        }


def _usage_insights_session_title(context: object, session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return ""
    tracker = getattr(context, "active_session_tracker", None)
    for method_name in ("title_from_thread_id", "title_from_session_index_id"):
        resolver = getattr(tracker, method_name, None)
        if not callable(resolver):
            continue
        try:
            title = " ".join(str(resolver(normalized) or "").split())
        except Exception:
            continue
        if title:
            return title
    return f"会话 {normalized[:8]}"


def _build_usage_insights_payload(context: object) -> dict[str, object]:
    day_start, week_start = current_budget_windows(
        getattr(context, "user_config", UserConfig.defaults())
    )
    usage_cache = getattr(context, "usage_cache")
    payload = dict(
        usage_cache.insights(
            Path(getattr(context, "sessions_root")),
            day_start,
            week_start,
            included_providers=_effective_provider_scope(context),
        )
    )
    background_by_window = {
        "today": _background_usage_insights_summary(
            context,
            range_key="today",
        ),
        "week": _background_usage_insights_summary(
            context,
            range_key="7d",
        ),
        "month": _background_usage_insights_summary(
            context,
            range_key="30d",
        ),
    }
    title_cache: dict[str, str] = {}
    for window_name in ("today", "week", "month"):
        raw_window = payload.get(window_name)
        window = dict(raw_window) if isinstance(raw_window, Mapping) else {}
        totals = dict(window.get("totals") or {})
        coverage = dict(totals.get("costCoverage") or {})
        def project_sessions(value: object) -> list[dict[str, object]]:
            sessions: list[dict[str, object]] = []
            if not isinstance(value, list):
                return sessions
            for raw_session in value:
                if not isinstance(raw_session, Mapping):
                    continue
                session = dict(raw_session)
                session_id = str(session.get("sessionId") or "").strip()
                can_activate = bool(session.get("canActivate")) and bool(session_id)
                title = " ".join(str(session.get("title") or "").split())
                if session_id and not title:
                    title = title_cache.get(session_id, "")
                if session_id and not title:
                    title = _usage_insights_session_title(context, session_id)
                if session_id and title:
                    title_cache[session_id] = title
                session.update(
                    {
                        "id": session_id,
                        "title": title or "未命名会话",
                        "actionable": can_activate,
                    }
                )
                sessions.append(session)
            return sessions

        sessions = project_sessions(window.get("sessions"))
        top_sessions_by_usage = project_sessions(window.get("topSessionsByUsage"))
        top_sessions_by_cost = project_sessions(window.get("topSessionsByCost"))
        totals["sessionCount"] = max(
            len(sessions),
            int(totals.get("sessionCount") or 0),
        )
        window.update(
            {
                "totals": totals,
                "costCoverage": coverage,
                "sessions": sessions,
                "topSessionsByUsage": top_sessions_by_usage,
                "topSessionsByCost": top_sessions_by_cost,
                "background": background_by_window[window_name],
            }
        )
        payload[window_name] = window
    payload.update(
        {
            "state": "ready" if bool(payload.get("ready")) else "idle",
            "error": "",
            "backgroundSeparate": True,
        }
    )
    return payload


def _refresh_usage_insights_payload(context: object) -> dict[str, object]:
    try:
        payload = _build_usage_insights_payload(context)
    except Exception as exc:
        _LOGGER.debug("usage_insights_projection_failed error=%s", exc)
        payload = {
            "state": "failed",
            "ready": False,
            "error": str(exc) or type(exc).__name__,
        }
    setattr(context, "usage_insights_payload", payload)
    return payload


def _apply_family_session_usage(
    context: object,
    snapshot: ParsedSession,
    provider_scope: Iterable[str] | None,
) -> None:
    """Attach root+subagent lifetime usage so top bar matches insights top10."""
    session_id = str(getattr(snapshot, "session_id", "") or "").strip()
    parent_id = str(getattr(snapshot, "parent_thread_id", "") or "").strip()
    root_id = parent_id if bool(getattr(snapshot, "is_subagent", False)) and parent_id else session_id
    if not root_id or root_id in {"", "n/a"}:
        snapshot.family_tokens = int(snapshot.confirmed.cumulative_total or 0)
        snapshot.family_cost_usd = snapshot.confirmed.cumulative_cost_usd
        snapshot.family_member_count = 1 if snapshot.family_tokens else 0
        return
    usage_cache = getattr(context, "usage_cache", None)
    lookup = getattr(usage_cache, "family_lifetime_usage", None)
    if not callable(lookup):
        snapshot.family_tokens = int(snapshot.confirmed.cumulative_total or 0)
        snapshot.family_cost_usd = snapshot.confirmed.cumulative_cost_usd
        snapshot.family_member_count = 1 if snapshot.family_tokens else 0
        return
    try:
        family = lookup(root_id, included_providers=provider_scope)
    except Exception as exc:
        _LOGGER.debug("family_session_usage_failed session=%s error=%s", root_id, exc)
        family = None
    if family is None or (
        int(getattr(family, "tokens", 0) or 0) <= 0
        and float(getattr(family, "cost_usd", 0.0) or 0.0) <= 0.0
    ):
        # Cache may still be warming; fall back to the currently parsed thread.
        snapshot.family_tokens = int(snapshot.confirmed.cumulative_total or 0)
        snapshot.family_cost_usd = snapshot.confirmed.cumulative_cost_usd
        snapshot.family_member_count = 1 if snapshot.family_tokens else 0
        return
    snapshot.family_tokens = int(getattr(family, "tokens", 0) or 0)
    snapshot.family_cost_usd = round(float(getattr(family, "cost_usd", 0.0) or 0.0), 6)
    # Member count is approximate: root plus any live children under this root.
    member_ids = {root_id}
    for entry in list(getattr(usage_cache, "_entries", {}).values()):
        sid = str(getattr(entry, "session_id", "") or "")
        parent = str(getattr(entry, "parent_session_id", "") or "")
        if sid == root_id or parent == root_id:
            if sid:
                member_ids.add(sid)
    snapshot.family_member_count = len(member_ids)


def _effective_notification_provider_scope(
    context: "RuntimeContext | object",
    snapshot: ParsedSession | None = None,
) -> frozenset[str] | None:
    """Resolve providers that may produce active-work notification bubbles."""
    included = _effective_provider_scope(context, snapshot)
    app_provider = str(getattr(context, "app_provider", "") or "").strip().lower()
    config = getattr(context, "user_config", None)
    resolver = getattr(config, "effective_notification_provider_scope", None)
    if callable(resolver):
        return resolver(app_provider)
    return included


def _provider_registry_payload(context: object) -> dict[str, object]:
    registry = getattr(context, "provider_registry", None)
    entries = getattr(registry, "entries", {})
    if not isinstance(entries, Mapping):
        return {}
    return {
        provider: {
            "profiles": list(getattr(entry, "profile_names", ())),
            "historicalOnly": bool(getattr(entry, "historical_only", False)),
        }
        for provider, entry in entries.items()
    }


def active_work_items_for_snapshot(
    context: "RuntimeContext",
    snapshot: ParsedSession,
    session_path: Path | None,
) -> list[WorkStatusItem]:
    """Build primary-screen work bubble items from recently active Codex sessions."""
    item_limit = _work_overlay_item_limit_for_context(context)
    if item_limit <= 0:
        _work_overlay_visible_item_cache(context).clear()
        return []
    now = datetime.now().astimezone()
    items: dict[str, WorkStatusItem] = {}
    visible_item_cache = _work_overlay_visible_item_cache(context)
    terminal_item_tasks = _work_overlay_terminal_item_tasks(context)
    terminal_item_ids: dict[str, str] = {}
    expired_startup_item_ids: set[str] = set()
    current_key = _session_path_key(session_path)
    # Internal collaboration agents stay folded into their parent. Desktop can
    # promote a delegation to an independent visible thread; those do bubble.
    if not _hide_from_work_overlay(snapshot):
        current_item = _work_item_from_snapshot(
            snapshot,
            current=True,
            title=snapshot.session_title,
            source=snapshot.selection_source,
            now=now,
        )
        if current_item is not None:
            items[str(current_item.id)] = current_item
        elif snapshot.task_aborted_at is not None and snapshot.session_id:
            terminal_item_ids[str(snapshot.session_id)] = _iso_or_empty(
                snapshot.task_started_at
            )
        elif _work_item_model_startup_timed_out(snapshot, now=now) and snapshot.session_id:
            expired_startup_item_ids.add(str(snapshot.session_id))

    for path in _recent_session_files(
        context.sessions_root,
        current_path=session_path,
        limit=ACTIVE_WORK_CANDIDATE_LIMIT,
    ):
        if _session_path_key(path) == current_key:
            continue
        try:
            parsed = context.parser.parse_file(path)
        except Exception:
            continue
        if _hide_from_work_overlay(parsed):
            continue
        title = ""
        if context.active_session_tracker is not None:
            title = context.active_session_tracker.title_for_session(
                path,
                parsed.session_id,
            )
        item = _work_item_from_snapshot(
            parsed,
            current=False,
            title=title,
            source="activity",
            now=now,
        )
        if item is not None:
            items[str(item.id)] = item
        elif parsed.task_aborted_at is not None and parsed.session_id:
            terminal_item_ids[str(parsed.session_id)] = _iso_or_empty(
                parsed.task_started_at
            )
        elif _work_item_model_startup_timed_out(parsed, now=now) and parsed.session_id:
            expired_startup_item_ids.add(str(parsed.session_id))

    terminal_item_tasks.update(terminal_item_ids)
    for item_id in terminal_item_ids:
        visible_item_cache.pop(item_id, None)
    for item_id, cached_item in list(visible_item_cache.items()):
        if item_id in items:
            continue
        if item_id in expired_startup_item_ids:
            visible_item_cache.pop(item_id, None)
            continue
        if bool(getattr(cached_item, "is_subagent", False)):
            visible_item_cache.pop(item_id, None)
            continue
        updated_at = (
            cached_item.updated_at
            or cached_item.started_at
            or cached_item.task_started_at
            or cached_item.session_started_at
        )
        if cached_item.status != "recent" and (
            updated_at is None
            or _datetime_age_seconds(updated_at, now) > ACTIVE_WORK_STALE_SECONDS
        ):
            visible_item_cache.pop(item_id, None)
            continue
        items[item_id] = replace(cached_item, current=False)

    ordered = sorted(items.values(), key=_work_overlay_item_sort_key, reverse=True)
    provider_scope = _effective_notification_provider_scope(context, snapshot)
    if provider_scope is not None:
        ordered = [item for item in ordered if item.model_provider in provider_scope]
    selected = _select_runtime_work_overlay_items(
        context,
        ordered,
        item_limit=item_limit,
    )
    selected_ids = {str(item.id) for item in selected if str(item.id or "").strip()}
    retained_ids = {
        item_id
        for item_id, cached_item in visible_item_cache.items()
        if item_id in items
        and item_id not in terminal_item_ids
        and _work_overlay_runtime_task_key(items[item_id])
        == _work_overlay_runtime_task_key(cached_item)
    }
    visible_ids = selected_ids | retained_ids
    selected = [
        item
        for item in ordered
        if str(item.id or "").strip() in visible_ids
    ][:item_limit]
    visible_item_cache.clear()
    visible_item_cache.update(
        {
            str(item.id): replace(item, current=False)
            for item in selected
            if str(item.id or "").strip()
        }
    )
    return selected














class _RendererActiveWorkPump:
    """Build recent-work items off the latency-critical renderer loop."""

    def __init__(self, context: "RuntimeContext", wake_event: Event) -> None:
        self._context = context
        self._wake_event = wake_event
        self._lock = threading.Lock()
        self._closed = False
        self._pending: tuple[ParsedSession, Path | None, int] | None = None
        self._latest: tuple[int, list[WorkStatusItem]] | None = None
        self._worker: threading.Thread | None = None

    def request(self, snapshot: ParsedSession, session_path: Path | None) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._pending = (
                copy.copy(snapshot),
                session_path,
                int(snapshot.selection_seq or 0),
            )
            if self._worker is not None and self._worker.is_alive():
                return True
            self._worker = threading.Thread(
                target=self._run,
                name="codex-usage-hud-renderer-active-work",
                daemon=True,
            )
            self._worker.start()
        return True

    def take_latest(self) -> tuple[int, list[WorkStatusItem]] | None:
        with self._lock:
            latest = self._latest
            self._latest = None
            return latest

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending = None
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=0.2)

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    self._worker = None
                    return
                request = self._pending
                self._pending = None
            if request is None:
                with self._lock:
                    self._worker = None
                return
            snapshot, session_path, selection_seq = request
            try:
                items = active_work_items_for_snapshot(
                    self._context,
                    snapshot,
                    session_path,
                )
            except Exception as exc:
                _LOGGER.info(
                    "renderer_active_work_refresh_failed error=%s",
                    f"{type(exc).__name__}: {exc}",
                )
                items = []
            with self._lock:
                if self._closed:
                    self._worker = None
                    return
                if self._pending is not None:
                    continue
                self._latest = (selection_seq, items)
                self._worker = None
            self._wake_event.set()
            return


def _visible_app_error_task_key(snapshot: ParsedSession) -> str:
    started_at = snapshot.task_started_at or snapshot.request.started_at
    return json.dumps(
        {
            "session": snapshot.session_id,
            "path": str(snapshot.session_path or ""),
            "startedAt": _iso_or_empty(started_at),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _datetime_age_seconds(value: datetime, now: datetime) -> float:
    try:
        return (now - value).total_seconds()
    except TypeError:
        return (now.replace(tzinfo=None) - value.replace(tzinfo=None)).total_seconds()


@dataclass
class _VisibleAppErrorCache:
    message: str = ""
    task_key: str = ""
    updated_at: datetime | None = None

    def clear(self) -> None:
        self.message = ""
        self.task_key = ""
        self.updated_at = None

    def resolve(self, snapshot: ParsedSession, visible_message: str) -> str:
        message = " ".join(str(visible_message or "").split())
        now = snapshot.refreshed_at or datetime.now().astimezone()
        task_key = _visible_app_error_task_key(snapshot)
        if message:
            self.message = message
            self.task_key = task_key
            self.updated_at = now
            return message
        if not self.message or not self.updated_at:
            return ""
        if self.task_key != task_key:
            self.clear()
            return ""
        if _datetime_age_seconds(self.updated_at, now) <= VISIBLE_APP_ERROR_HOLD_SECONDS:
            return self.message
        self.clear()
        return ""






def _session_cleanup_current_ids(context: object) -> tuple[str, ...]:
    values: list[str] = []
    values.append(str(getattr(context, "session_management_current_session_id", "") or ""))
    resolver = getattr(context, "session_resolver", None)
    values.append(str(getattr(resolver, "session_id", "") or ""))
    tracker = getattr(context, "active_session_tracker", None)
    values.append(str(getattr(tracker, "latest_session_id", "") or ""))
    return tuple(values)


def _session_cleanup_active_ids(context: object) -> tuple[str, ...]:
    values = getattr(context, "session_management_active_session_ids", set())
    return tuple(str(value) for value in values)


def _build_usage_summary_cache(parser: JsonlSessionParser) -> UsageSummaryCache:
    return UsageSummaryCache(
        parser,
        deleted_usage_ledger=DeletedUsageLedger(
            hud_runtime_dir() / DELETED_SESSION_USAGE_FILENAME
        ),
    )


def _prepare_session_cleanup_usage(
    context: object, item: SessionCleanupItem
) -> object:
    try:
        return getattr(context, "usage_cache").prepare_deleted_session_usage(item)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _commit_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").commit_deleted_session_usage(receipt)
    except DeletedUsageLedgerError as exc:
        raise SessionCleanupError(str(exc)) from exc


def _discard_session_cleanup_usage(context: object, receipt: object) -> None:
    try:
        getattr(context, "usage_cache").discard_deleted_session_usage(receipt)
    except DeletedUsageLedgerError:
        pass


def _build_session_cleanup_manager(context: object) -> SessionCleanupManager:
    return SessionCleanupManager(
        state_db_path=Path(getattr(context, "state_db_path")),
        sessions_root=Path(getattr(context, "sessions_root")),
        session_index_path=Path(getattr(context, "session_index_path")),
        current_session_ids=lambda: _session_cleanup_current_ids(context),
        active_session_ids=lambda: _session_cleanup_active_ids(context),
        usage_snapshot_prepare=lambda item: _prepare_session_cleanup_usage(
            context, item
        ),
        usage_snapshot_commit=lambda receipt: _commit_session_cleanup_usage(
            context, receipt
        ),
        usage_snapshot_discard=lambda receipt: _discard_session_cleanup_usage(
            context, receipt
        ),
    )


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
    app_provider: str = ""
    provider_registry: ProviderRegistry | None = None
    pre_send_estimator: PreSendEstimator | None = None
    runtime_events: RuntimeEventBus = field(default_factory=RuntimeEventBus)
    runtime_errors: RuntimeErrorRegistry = field(default_factory=RuntimeErrorRegistry)
    visible_app_error_cache: _VisibleAppErrorCache = field(
        default_factory=_VisibleAppErrorCache
    )
    current_session_tail_state: JsonlTailState | None = None
    session_snapshot_cache: "SessionSnapshotCache | None" = None
    renderer_mode: bool = True
    background_usage_runtime: BackgroundUsageRuntime | None = None
    usage_insights_payload: dict[str, object] = field(default_factory=dict)
    usage_insights_worker: _UsageInsightsWorker | None = None
    session_cleanup_manager: SessionCleanupManager | None = None
    session_cleanup_worker: _SessionCleanupWorker | None = None
    session_cleanup_payload: dict[str, object] = field(default_factory=dict)
    session_management_current_session_id: str = ""
    session_management_active_session_ids: set[str] = field(default_factory=set)
    rest_reminder: RestReminderPresenter | None = None

    def __post_init__(self) -> None:
        if self.runtime_errors.event_bus is None:
            self.runtime_errors.event_bus = self.runtime_events
        if self.rest_reminder is None:
            settings_path = getattr(self.settings_store, "path", None)
            self.rest_reminder = RestReminderPresenter(state_path=settings_path)
            self.rest_reminder.configure(
                self.user_config,
                force_reset=True,
                restore_persisted=True,
            )
        _ensure_runtime_error_diagnostics(self)
        if self.session_snapshot_cache is None:
            self.session_snapshot_cache = SessionSnapshotCache(
                self.parser,
                event_bus=self.runtime_events,
                sse_tracker=self.sse_tracker,
            )
        if self.renderer_mode:
            self.usage_insights_payload = _refresh_usage_insights_payload(self)
            if self.usage_insights_worker is None:
                self.usage_insights_worker = _UsageInsightsWorker(self)
            if self.session_cleanup_manager is None:
                self.session_cleanup_manager = _build_session_cleanup_manager(self)
            self.session_cleanup_payload = self.session_cleanup_manager.snapshot()
            if self.session_cleanup_worker is None:
                self.session_cleanup_worker = _SessionCleanupWorker(
                    self,
                    self.session_cleanup_manager,
                )

    def close(self) -> None:
        """Release any background helpers created for the runtime context."""
        _stop_active_session_tracker(self)
        if self.session_cleanup_worker is not None:
            self.session_cleanup_worker.close()
            self.session_cleanup_worker = None
        if self.usage_insights_worker is not None:
            self.usage_insights_worker.close()
            self.usage_insights_worker = None
        if self.background_usage_runtime is not None:
            self.background_usage_runtime.close()
            self.background_usage_runtime = None
        if self.session_snapshot_cache is not None:
            self.session_snapshot_cache.close()
        if self.pre_send_estimator is not None:
            self.pre_send_estimator.close()

    def reload_user_config(self) -> None:
        """Reload user config and reset cost caches when pricing changes."""
        mtime = self.settings_store.mtime()
        if mtime == self.settings_mtime:
            return
        next_config = self.settings_store.load()
        _apply_user_config_to_runtime_context(self, next_config, mtime=mtime)


def _suspend_native_active_title(context: "RuntimeContext") -> None:
    try:
        context.platform.suspend_native_active_title(True)
    except Exception:
        return


def _stop_active_session_tracker(context: "RuntimeContext") -> None:
    tracker = getattr(context, "active_session_tracker", None)
    resolver = getattr(context, "session_resolver", None)
    if resolver is not None and hasattr(resolver, "active_session_tracker"):
        try:
            resolver.active_session_tracker = None
        except Exception:
            pass
    if tracker is None:
        return
    try:
        tracker.close()
    finally:
        context.active_session_tracker = None


def _snapshot_session_key(snapshot: ParsedSession | None) -> str:
    if snapshot is None:
        return ""
    return _session_path_key(snapshot.session_path) or str(snapshot.session_id or "")


@dataclass
class _SessionSnapshotCacheEntry:
    """A fully parsed JSONL state retained for a recently selected session."""

    state: JsonlTailState
    snapshot: ParsedSession
    file_size: int
    mtime: float
    accessed_at: float


def _clone_cached_session_snapshot(snapshot: ParsedSession) -> ParsedSession:
    """Clone mutable runtime fields while sharing parsed history read-only."""
    cloned = copy.copy(snapshot)
    cloned.request = copy.copy(snapshot.request)
    cloned.budget_warnings = list(snapshot.budget_warnings)
    cloned.active_work_items = list(snapshot.active_work_items)
    cloned.follow_timing = dict(snapshot.follow_timing)
    return cloned


class SessionSnapshotCache:
    """Keep cold session parsing off the renderer refresh path.

    A selected, uncached session gets a bounded tail preview immediately.  One
    daemon worker then builds the complete incremental state and publishes an
    event, so renderer refreshes remain event-driven and never synchronously
    decode a multi-megabyte historical JSONL file during a sidebar click.
    """

    def __init__(
        self,
        parser: JsonlSessionParser,
        *,
        event_bus: RuntimeEventBus | None = None,
        sse_tracker: SseRequestStateMachine | None = None,
        max_entries: int = RENDERER_SESSION_SNAPSHOT_CACHE_SIZE,
        preview_bytes: int = RENDERER_COLD_SESSION_PREVIEW_BYTES,
    ) -> None:
        self._parser = parser
        self._event_bus = event_bus
        self._sse_tracker = sse_tracker
        self._max_entries = max(1, int(max_entries))
        self._preview_bytes = max(1, int(preview_bytes))
        self._entries: dict[Path, _SessionSnapshotCacheEntry] = {}
        self._pending: deque[tuple[Path, str]] = deque()
        self._queued: set[Path] = set()
        self._lock = threading.Lock()
        self._wake = Event()
        self._closed = Event()
        self._worker = threading.Thread(
            target=self._run,
            name="codex-usage-hud-session-cache",
            daemon=True,
        )
        self._worker.start()

    @staticmethod
    def _cache_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=False)
        except OSError:
            return path.expanduser().absolute()

    def snapshot_for(self, path: Path, *, session_id: str = "") -> ParsedSession:
        """Return a cached complete snapshot or a bounded cold-session preview."""
        key = self._cache_path(path)
        try:
            stat = key.stat()
        except OSError:
            return self._parser.parse_file_tail_preview(
                key,
                session_id=session_id or None,
                max_bytes=self._preview_bytes,
            )
        preserve_previous_cost = False
        previous_cost: float | None = None
        with self._lock:
            entry = self._entries.get(key)
            if (
                entry is not None
                and entry.file_size == int(stat.st_size)
                and entry.mtime == stat.st_mtime
            ):
                entry.accessed_at = time.monotonic()
                return _clone_cached_session_snapshot(entry.snapshot)
            if entry is not None and int(stat.st_size) > entry.file_size:
                current_file_id = self._parser._file_id(key, stat)
                if entry.state.file_id == current_file_id:
                    preserve_previous_cost = True
                    previous_cost = entry.snapshot.confirmed.cumulative_cost_usd
            self._enqueue_locked(key, session_id)
        preview = self._parser.parse_file_tail_preview(
            key,
            session_id=session_id or None,
            max_bytes=self._preview_bytes,
        )
        if preserve_previous_cost:
            preview.confirmed.cumulative_cost_usd = previous_cost
        return preview

    def _enqueue_locked(self, path: Path, session_id: str) -> None:
        if path in self._queued or self._closed.is_set():
            return
        self._queued.add(path)
        self._pending.append((path, session_id))
        self._wake.set()

    def _run(self) -> None:
        while not self._closed.is_set():
            self._wake.wait()
            self._wake.clear()
            while not self._closed.is_set():
                with self._lock:
                    if not self._pending:
                        break
                    path, session_id = self._pending.popleft()
                try:
                    state = None
                    with self._lock:
                        previous = self._entries.get(path)
                        if previous is not None:
                            state = previous.state
                    snapshot, state = self._parser.parse_file_incremental(
                        path,
                        state,
                        session_id=session_id or None,
                        sse_tracker=self._sse_tracker,
                    )
                    stat = path.stat()
                except OSError as exc:
                    _LOGGER.info("renderer_session_cache_hydrate_failed path=%s error=%s", path, exc)
                except Exception:
                    _LOGGER.exception("renderer_session_cache_hydrate_failed path=%s", path)
                else:
                    with self._lock:
                        self._entries[path] = _SessionSnapshotCacheEntry(
                            state=state,
                            snapshot=snapshot,
                            file_size=int(stat.st_size),
                            mtime=stat.st_mtime,
                            accessed_at=time.monotonic(),
                        )
                        self._trim_locked()
                    self._publish_hydrated(path)
                finally:
                    with self._lock:
                        self._queued.discard(path)

    def _trim_locked(self) -> None:
        while len(self._entries) > self._max_entries:
            oldest = min(self._entries, key=lambda key: self._entries[key].accessed_at)
            del self._entries[oldest]

    def _publish_hydrated(self, path: Path) -> None:
        publish = getattr(self._event_bus, "publish", None)
        if callable(publish):
            publish(
                "session_snapshot_hydrated",
                source="session_snapshot_cache",
                session=_session_path_key(path),
            )

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        if self._worker.is_alive():
            self._worker.join(timeout=0.2)


def _active_session_switch_pending(context: "RuntimeContext", snapshot: ParsedSession | None) -> bool:
    resolver = getattr(context, "session_resolver", None)
    if resolver is None:
        return False
    try:
        session_path, selection_source = resolver.resolve()
    except Exception:
        return False
    del selection_source
    current_key = _session_path_key(session_path) or str(getattr(resolver, "session_id", "") or "")
    return bool(current_key and current_key != _snapshot_session_key(snapshot))


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
        from .ui import renderer_hud

        renderer_hud.set_cost_estimator(estimator)
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


def _changed_user_config_keys(
    previous: UserConfig,
    current: UserConfig,
) -> set[str]:
    previous_payload = previous.to_dict()
    current_payload = current.to_dict()
    return {
        key
        for key in previous_payload.keys() | current_payload.keys()
        if previous_payload.get(key) != current_payload.get(key)
    }


def _apply_user_config_to_runtime_context(
    context: RuntimeContext | object,
    next_config: UserConfig,
    *,
    mtime: float | None,
) -> None:
    previous_config = getattr(context, "user_config", UserConfig.defaults())
    prices_changed = next_config.price_table() != previous_config.price_table()
    setattr(context, "user_config", next_config)
    setattr(context, "settings_mtime", mtime)
    setattr(context, "daily_budget_usd", max(0.0, float(next_config.daily_budget_usd)))
    setattr(context, "weekly_budget_usd", max(0.0, float(next_config.weekly_budget_usd)))
    setattr(
        context,
        "weekly_adjustment_usd",
        max(0.0, float(next_config.weekly_adjustment_usd)),
    )
    setattr(context, "budget_thresholds", list(next_config.budget_thresholds))
    sessions_root = getattr(context, "sessions_root", None)
    if isinstance(sessions_root, Path):
        registry = discover_provider_registry(
            user_config=next_config,
            sessions_root=sessions_root,
        )
        next_config = next_config.migrate_legacy_provider_settings(
            registry.providers(), app_provider=registry.app_provider
        )
        setattr(context, "user_config", next_config)
        registry = discover_provider_registry(
            user_config=next_config,
            sessions_root=sessions_root,
        )
        setattr(context, "provider_registry", registry)
        if not str(getattr(context, "app_provider", "") or "").strip():
            setattr(context, "app_provider", registry.app_provider)
    if prices_changed:
        estimator = _cost_estimator_from_config(next_config)
        parser = getattr(context, "parser", None)
        if parser is not None:
            parser.cost_estimator = estimator
        sse_tracker = getattr(context, "sse_tracker", None)
        if sse_tracker is not None:
            sse_tracker.cost_estimator = estimator
        if parser is not None:
            setattr(context, "usage_cache", _build_usage_summary_cache(parser))
        _configure_ui_cost_estimators(estimator)
    background_usage_runtime = getattr(context, "background_usage_runtime", None)
    reconfigure_background_usage = getattr(background_usage_runtime, "reconfigure", None)
    if callable(reconfigure_background_usage):
        reconfigure_background_usage(
            provider=str(getattr(context, "app_provider", "") or ""),
            price_table=next_config.price_table(),
        )
    rest_reminder = getattr(context, "rest_reminder", None)
    if rest_reminder is not None:
        rest_reminder.configure(next_config)


def _partial_domains_for_changed_user_config(
    changed_keys: set[str],
) -> set[str] | None:
    ui_keys = {"display_mode"}
    overlay_keys = {"work_overlay_max_items"}
    rest_keys = {
        "rest_reminder_enabled",
        "rest_reminder_interval_minutes",
        "rest_reminder_break_minutes",
        "rest_reminder_postpone_minutes",
        "rest_reminder_idle_reset_minutes",
        "rest_reminder_work_start_time",
        "rest_reminder_work_end_time",
        "rest_reminder_lunch_enabled",
        "rest_reminder_lunch_start_time",
        "rest_reminder_lunch_end_time",
    }
    pricing_keys = {"pricing_url", "model_prices"}
    budget_keys = {
        "daily_budget_usd",
        "weekly_budget_usd",
        "budget_thresholds",
        "weekly_adjustment_usd",
    }
    safe_keys = ui_keys | overlay_keys | rest_keys | pricing_keys | budget_keys
    if changed_keys and not changed_keys.issubset(safe_keys):
        return None
    domains = {"settings"}
    if changed_keys & overlay_keys:
        domains.add("overlay")
    if changed_keys & pricing_keys:
        domains.add("currentSession")
    if changed_keys & budget_keys:
        domains.update({"currentSession", "budget"})
    return domains


def _partial_domains_for_settings_command(
    command: Mapping[str, Any],
    *,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> set[str] | None:
    action = str(command.get("action") or "").strip()
    if action in SESSION_CLEANUP_COMMANDS:
        return {"settings", "sessionCleanup"}
    if action == "usageInsightsRefresh":
        return {"settings", "usageInsights"}
    if action == "openUsageInsightsSession":
        return {"settings"}
    if action == "openBackgroundUsageFromInsights":
        return {"backgroundUsage"}
    if action in {
        "restReminderAck",
        "restReminderPostpone",
        "restReminderStart",
        "restReminderFinish",
        "restReminderTestNotification",
    }:
        return {"settings"}
    if action == "save":
        changed_keys = _changed_user_config_keys(previous_config, current_config)
        return _partial_domains_for_changed_user_config(changed_keys)
    if action == "applyDisplayMode":
        return {"settings"}
    if action == "fetchPrices":
        return {"currentSession", "settings"}
    if action in {
        "openBackgroundUsage",
        "backgroundUsageQuery",
        "backgroundUsageDetail",
    }:
        return {"backgroundUsage"}
    return None


def _refresh_latest_snapshot_for_partial_settings_command(
    command: Mapping[str, Any],
    *,
    snapshot: ParsedSession,
    context: RuntimeContext,
    previous_config: UserConfig,
    current_config: UserConfig,
) -> None:
    action = str(command.get("action") or "").strip()
    changed_keys = _changed_user_config_keys(previous_config, current_config)
    pricing_keys = {"pricing_url", "model_prices"}
    budget_keys = {
        "daily_budget_usd",
        "weekly_budget_usd",
        "budget_thresholds",
        "weekly_adjustment_usd",
    }
    if action == "fetchPrices" or (
        action == "save" and changed_keys and changed_keys.issubset(pricing_keys)
    ):
        snapshot.estimate_base = _apply_pre_send_pricing(
            context,
            snapshot,
            snapshot.estimate_base,
        )
    if action == "save" and changed_keys & budget_keys:
        raw_week_cost_usd = max(
            0.0,
            float(snapshot.week_cost_usd) - float(snapshot.week_adjustment_usd or 0.0),
        )
        week_adjustment_usd = max(0.0, float(current_config.weekly_adjustment_usd))
        snapshot.week_adjustment_usd = week_adjustment_usd
        snapshot.week_cost_usd = round(raw_week_cost_usd + week_adjustment_usd, 6)
        snapshot.daily_limit_usd = max(0.0, float(current_config.daily_budget_usd))
        snapshot.weekly_limit_usd = max(0.0, float(current_config.weekly_budget_usd))
        snapshot.budget_warnings = budget_warnings(
            snapshot.today_cost_usd,
            snapshot.week_cost_usd,
            snapshot.daily_limit_usd,
            snapshot.weekly_limit_usd,
            list(current_config.budget_thresholds),
        )


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


def _background_usage_response_status(
    kind: str,
    request_id: str,
    *,
    payload: object = None,
    event_id: str = "",
    error: str = "",
) -> dict[str, object]:
    """Build one correlated local background-usage RPC response."""
    status = _renderer_settings_status("")
    response: dict[str, object] = {
        "kind": kind,
        "requestId": request_id,
        "payload": payload,
        "error": error,
    }
    if event_id:
        response["eventId"] = event_id
    status["backgroundUsageResponse"] = response
    if kind == "open":
        status["backgroundUsageOpenEventId"] = event_id
    return status


def _background_usage_response_retry_delay_seconds(
    attempt: int,
) -> float | None:
    """Return the bounded delay before retrying an undelivered local RPC response."""
    index = int(attempt) - 1
    if index < 0 or index >= len(BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS):
        return None
    return BACKGROUND_USAGE_RESPONSE_RETRY_DELAYS_SECONDS[index]


def _has_pending_background_usage_response(status: Mapping[str, object]) -> bool:
    response = status.get("backgroundUsageResponse")
    if not isinstance(response, Mapping):
        return False
    return bool(
        str(response.get("requestId") or "").strip()
        and str(response.get("kind") or "").strip()
        in {"query", "detail", "open"}
    )


def _background_usage_query_payload_with_preview(
    runtime: object,
    *,
    range_key: str,
    feature: str,
    model: str,
    event_id: str,
) -> dict[str, object]:
    query = getattr(runtime, "query", None)
    if not callable(query):
        raise RuntimeError("用量总览当前不可用。")
    raw_payload = query(
        range_key=range_key,
        feature=feature,
        model=model,
        event_id=event_id,
    )
    if not isinstance(raw_payload, Mapping):
        raise RuntimeError("后台用量查询返回了无效数据。")
    payload = dict(raw_payload)
    selected_event_id = str(payload.get("selectedEventId") or "").strip()
    selected_detail: dict[str, object] | None = None
    detail = getattr(runtime, "detail", None)
    if selected_event_id and callable(detail):
        try:
            raw_detail = detail(selected_event_id)
        except Exception as exc:
            _LOGGER.debug(
                "background_usage_preview_failed event_id=%s error=%s",
                selected_event_id,
                exc,
            )
        else:
            if isinstance(raw_detail, Mapping):
                selected_detail = dict(raw_detail)
                prompt = str(selected_detail.pop("prompt", "") or "")
                selected_detail["hasPrompt"] = bool(prompt)
    payload["selectedDetail"] = selected_detail
    return payload


SESSION_CLEANUP_COMMANDS = {
    "sessionCleanupScan",
    "sessionCleanupPreview",
    "sessionCleanupExecute",
    "sessionCleanupCancel",
}








def _handle_renderer_session_cleanup_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    if action not in SESSION_CLEANUP_COMMANDS:
        return _renderer_settings_status(
            f"无法处理未知会话清理命令：{action or 'empty'}",
            kind="error",
        )
    worker = getattr(context, "session_cleanup_worker", None)
    enqueue = getattr(worker, "enqueue", None)
    if not callable(enqueue):
        return _renderer_settings_status("会话永久删除当前不可用。", kind="error")
    try:
        accepted = enqueue(command)
    except SessionCleanupError as exc:
        return _renderer_settings_status(str(exc), kind="error")
    request_id = str(accepted.get("requestId") or command.get("requestId") or "")
    labels = {
        "sessionCleanupScan": "会话清单扫描已开始。",
        "sessionCleanupPreview": "正在生成永久删除确认。",
        "sessionCleanupExecute": "永久删除请求已进入本地事务门禁。",
        "sessionCleanupCancel": "已取消会话删除确认。",
    }
    status = _renderer_settings_status(labels.get(action, "会话清理命令已提交。"))
    status["sessionCleanupRequestId"] = request_id
    status["sessionCleanupAction"] = action
    return status


def _usage_insights_actionable_session_ids(context: object) -> set[str]:
    payload = getattr(context, "usage_insights_payload", {})
    if not isinstance(payload, Mapping):
        return set()
    result: set[str] = set()
    for window_name in ("today", "week", "month"):
        window = payload.get(window_name)
        if not isinstance(window, Mapping):
            continue
        for collection_name in (
            "sessions",
            "topSessionsByUsage",
            "topSessionsByCost",
        ):
            sessions = window.get(collection_name)
            if not isinstance(sessions, list):
                continue
            for item in sessions:
                if not isinstance(item, Mapping) or not bool(
                    item.get("actionable", item.get("canActivate", False))
                ):
                    continue
                session_id = str(
                    item.get("id") or item.get("sessionId") or ""
                ).strip()
                try:
                    canonical = str(uuid.UUID(session_id))
                except (ValueError, AttributeError, TypeError):
                    continue
                if canonical == session_id.casefold():
                    result.add(canonical)
    return result


def _handle_renderer_usage_insights_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    *,
    session_controller: SessionSwitchController | None,
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    request_id = str(command.get("requestId") or "")
    if action == "usageInsightsRefresh":
        worker = getattr(context, "usage_insights_worker", None)
        request_refresh = getattr(worker, "request_refresh", None)
        if not callable(request_refresh) or not request_refresh(request_id=request_id):
            return _renderer_settings_status(
                "用量洞察刷新器当前不可用。",
                kind="error",
            )
        status = _renderer_settings_status("用量洞察刷新已开始。")
        status["usageInsightsRequestId"] = request_id
        return status
    if action != "openUsageInsightsSession":
        return _renderer_settings_status(
            f"无法处理未知用量洞察命令：{action or 'empty'}",
            kind="error",
        )
    session_id = str(command.get("sessionId") or "").strip().casefold()
    if session_id not in _usage_insights_actionable_session_ids(context):
        return _renderer_settings_status(
            "该会话已归档、标识不完整或不在当前洞察结果中，未执行跳转。",
            kind="error",
        )
    if session_controller is None:
        return _renderer_settings_status(
            "当前 Renderer 会话切换器不可用。",
            kind="error",
        )
    result = _handle_work_overlay_command(
        {
            "action": "activateSession",
            "sessionId": session_id,
            "targetTitle": str(command.get("targetTitle") or "").strip(),
            "workdir": str(command.get("workdir") or "").strip(),
        },
        session_controller,
        prepare_window=True,
        backend_names=("cdp",),
    )
    if result is None or not (result.ok or result.status == "already-active"):
        return _renderer_settings_status(
            result.message if result is not None and result.message else "无法打开该会话。",
            kind="error",
        )
    return _renderer_settings_status("已切换到所选会话。")


def _handle_renderer_settings_command(
    command: Mapping[str, Any],
    context: RuntimeContext,
    restart_requested: Event,
    exit_requested: Event,
    update_manager: AutoUpdateManager | None = None,
    work_overlay: DesktopWorkOverlay | None = None,
    session_controller: SessionSwitchController | None = None,
) -> dict[str, object]:
    action = str(command.get("action") or "").strip()
    background_usage_request_id = str(
        command.get("requestId") or command.get("id") or ""
    ).strip()
    try:
        if action in SESSION_CLEANUP_COMMANDS:
            return _handle_renderer_session_cleanup_command(command, context)
        if action in {"usageInsightsRefresh", "openUsageInsightsSession"}:
            return _handle_renderer_usage_insights_command(
                command,
                context,
                session_controller=session_controller,
            )
        if action == "backgroundUsageQuery":
            runtime = getattr(context, "background_usage_runtime", None)
            raw_filters = command.get("filters")
            filters = raw_filters if isinstance(raw_filters, Mapping) else {}
            payload = _background_usage_query_payload_with_preview(
                runtime,
                range_key=str(filters.get("range") or "today"),
                feature=str(filters.get("feature") or ""),
                model=str(filters.get("model") or ""),
                event_id=str(filters.get("eventId") or ""),
            )
            return _background_usage_response_status(
                "query",
                background_usage_request_id,
                payload=payload,
            )
        if action == "backgroundUsageDetail":
            runtime = getattr(context, "background_usage_runtime", None)
            detail = getattr(runtime, "detail", None)
            event_id = str(command.get("eventId") or "").strip()
            if not callable(detail):
                return _background_usage_response_status(
                    "detail",
                    background_usage_request_id,
                    event_id=event_id,
                    error="用量总览当前不可用。",
                )
            if command.get("markViewed") is True:
                confirm = getattr(runtime, "confirm", None)
                if callable(confirm):
                    confirm(event_id)
            payload = detail(event_id) if event_id else None
            return _background_usage_response_status(
                "detail",
                background_usage_request_id,
                payload=payload,
                event_id=event_id,
                error="" if payload is not None else "后台用量事件不存在。",
            )
        if action in {"openBackgroundUsage", "openBackgroundUsageFromInsights"}:
            event_id = str(command.get("eventId") or "").strip()
            runtime = getattr(context, "background_usage_runtime", None)
            # Opening the overview with an auto-located event counts as viewing it.
            # Confirm before query so the returned list/preview already show unread=false
            # and the bottom-right notification badge can clear.
            if event_id:
                confirm = getattr(runtime, "confirm", None)
                if callable(confirm):
                    confirm(event_id)
            range_key = "today"
            range_for_event = getattr(runtime, "range_for_event", None)
            if event_id and callable(range_for_event):
                candidate = str(range_for_event(event_id) or "today").strip().lower()
                if candidate in {"today", "7d", "30d", "all"}:
                    range_key = candidate
            payload = _background_usage_query_payload_with_preview(
                runtime,
                range_key=range_key,
                feature="",
                model="",
                event_id=event_id,
            )
            return _background_usage_response_status(
                "open",
                background_usage_request_id,
                payload=payload,
                event_id=event_id,
            )
        if action == "save":
            settings_payload = command.get("settings")
            config = _config_from_settings_payload(
                context.settings_store.load(),
                settings_payload,
            )
            _save_renderer_user_config(context, config)
            if str(command.get("section") or "") == "restReminder":
                presenter = getattr(context, "rest_reminder", None)
                started_at_ms = (
                    settings_payload.get("rest_reminder_timer_started_at_ms")
                    if isinstance(settings_payload, Mapping)
                    else None
                )
                if presenter is not None and started_at_ms is not None:
                    presenter.adjust_cycle_started_at_ms(started_at_ms)
                    status = _renderer_settings_status(
                        "提醒设置已保存，已按指定时间校正本轮计时。"
                    )
                else:
                    status = _renderer_settings_status(
                        "提醒设置已保存；休息结束后会自动开始下一轮。"
                    )
                status["restReminderSaved"] = True
                status["restReminderSaveRequestId"] = str(command.get("id") or "")
                return status
            return _renderer_settings_status("设置已保存，相关显示会自动刷新。")
        if action == "restReminderAck":
            presenter = getattr(context, "rest_reminder", None)
            if presenter is not None:
                presenter.acknowledge()
            return _renderer_settings_status("休息提醒状态已更新。")
        if action == "restReminderStart":
            presenter = getattr(context, "rest_reminder", None)
            started = bool(presenter.start_rest()) if presenter is not None else False
            return _renderer_settings_status(
                "已开始休息计时。" if started else "当前状态不能开始休息。",
                kind="" if started else "error",
            )
        if action == "restReminderFinish":
            presenter = getattr(context, "rest_reminder", None)
            finished = bool(presenter.finish_rest()) if presenter is not None else False
            return _renderer_settings_status(
                "本次休息已结束，新一轮专注计时已开始。"
                if finished
                else "当前没有正在进行的休息。",
                kind="" if finished else "error",
            )
        if action == "restReminderPostpone":
            presenter = getattr(context, "rest_reminder", None)
            postponed = bool(presenter.postpone()) if presenter is not None else False
            return _renderer_settings_status(
                "已安排稍后提醒。" if postponed else "这次提醒已经延后过了。",
                kind="" if postponed else "error",
            )
        if action == "restReminderTestNotification":
            presenter = getattr(context, "rest_reminder", None)
            result = presenter.test_notification() if presenter is not None else {
                "status": "failed",
                "error": "提醒服务未启动",
            }
            sent = str(result.get("status") or "") == "sent"
            preview = bool(result.get("preview"))
            if preview:
                if sent:
                    return _renderer_settings_status(
                        "已发送系统通知，并弹出实际休息提醒预览。关闭预览不会改变当前计时。"
                    )
                return _renderer_settings_status(
                    f"已弹出实际休息提醒预览；系统通知失败：{result.get('error') or '未知错误'}",
                    kind="error",
                )
            return _renderer_settings_status(
                "系统通知测试已发送。" if sent else f"系统通知发送失败：{result.get('error') or '未知错误'}",
                kind="" if sent else "error",
            )
        if action == "applyDisplayMode":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            _save_renderer_user_config(context, config)
            return _renderer_settings_status(
                "Renderer 方案已保存；当前会话已处于内嵌显示，无需重启。",
            )
        if action == "fetchPrices":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            provider = str(command.get("provider") or "").strip().lower()
            provider_url = (
                config.provider_settings.get(provider).pricing_url
                if provider and provider in config.provider_settings
                else config.pricing_url
            )
            prices = fetch_model_prices(provider_url)
            config = config.with_price_updates(
                prices, pricing_url=provider_url, provider=provider or None
            )
            _save_renderer_user_config(context, config)
            return _renderer_settings_status(
                f"已拉取并保存 {len(prices)} 个模型价格。",
            )
        if action == "restart":
            restart_requested.set()
            return _renderer_settings_status(
                "已请求重启 HUD；daemon 模式会自动恢复。",
            )
        if action == "exit":
            exit_requested.set()
            return _renderer_settings_status(
                "已请求退出 HUD；后台守护进程也会一并停止。",
            )
        if action == "checkUpdate":
            if update_manager is not None:
                state = update_manager.request_check(auto_download=False)
                return _renderer_settings_status(
                    state.message or "正在检查更新...",
                    kind="error" if state.error else "",
                )
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
            if update_manager is not None:
                state = update_manager.request_install()
                return _renderer_settings_status(
                    state.message or state.title or "正在准备安装更新...",
                    kind="error" if state.error else "",
                )
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
        if action == "installDesktopOverlay":
            status = _desktop_overlay_dependency_status()
            if bool(status.get("installed")):
                version = str(status.get("version") or "").strip()
                return _renderer_settings_status(
                    f"气泡组件已可用{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。",
                )
            if bool(status.get("installing")):
                return _renderer_settings_status(
                    "气泡组件正在安装；完成后点击“启用气泡”。",
                )
            if not bool(status.get("canInstall")):
                return _renderer_settings_status(
                    "当前运行环境不能在线安装气泡组件；请安装带会话进度气泡的版本后重启 HUD。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            # Simulated missing with real package: clear force and surface detected install.
            if bool(status.get("forcedMissing")) and bool(status.get("realInstalled")):
                _set_force_desktop_overlay_missing(False)
                version = _pyside6_version()
                return _renderer_settings_status(
                    f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。",
                )
            if _start_desktop_overlay_install():
                refreshed = _desktop_overlay_dependency_status()
                if bool(refreshed.get("installed")):
                    version = str(refreshed.get("version") or "").strip()
                    return _renderer_settings_status(
                        f"已检测到本机已安装气泡组件{f'（PySide6 {version}）' if version else ''}；可直接启用会话进度气泡。",
                    )
                return _renderer_settings_status(
                    "已开始安装气泡组件；完成后点击“启用气泡”。",
                )
            return _renderer_settings_status(
                "无法启动 PySide6 安装；请在终端运行 pip install PySide6>=6.8。",
                kind="error",
            )
        if action == "enableDesktopOverlay":
            # Clear simulation first so enable can re-detect a real install.
            if _force_desktop_overlay_missing() and _pyside6_runtime_available(honor_force=False):
                _set_force_desktop_overlay_missing(False)
            status = _desktop_overlay_dependency_status()
            if not bool(status.get("installed")):
                return _renderer_settings_status(
                    "还没检测到气泡组件；安装完成后再点一次“启用气泡”。",
                    kind="error",
                    restart_visible=bool(status.get("requiresRestart")),
                )
            config = context.settings_store.load()
            if normalize_work_overlay_max_items(config.work_overlay_max_items) <= 0:
                config = replace(
                    config,
                    work_overlay_max_items=min(
                        DEFAULT_WORK_OVERLAY_MAX_ITEMS,
                        _work_overlay_screen_max_items(),
                    ),
                )
                _save_renderer_user_config(context, config)
            elif hasattr(context, "reload_user_config"):
                context.reload_user_config()
            if work_overlay is not None:
                work_overlay.reset_runtime_availability()
            version = str(status.get("version") or "").strip()
            return _renderer_settings_status(
                f"会话进度气泡已启用{f'（PySide6 {version}）' if version else ''}。",
            )
        if action == "updateAction":
            if update_manager is None:
                return _renderer_settings_status(
                    "当前会话未启用自动更新控制器。",
                    kind="error",
                )
            state = update_manager.handle_click()
            return _renderer_settings_status(
                state.message or state.title or "更新操作已提交。",
                kind="error" if state.error else "",
            )
        if action == "dismissWarningsToday":
            settings_path = getattr(getattr(context, "settings_store", None), "path", None)
            if settings_path is None:
                return _renderer_settings_status(
                    "无法保存预警关闭状态：配置路径不可用。",
                    kind="error",
                )
            dismiss_warning_for_today(settings_path)
            return _renderer_settings_status(
                "今天不再显示预算预警。",
            )
        return _renderer_settings_status(
            f"无法处理未知设置命令：{action or 'empty'}",
            kind="error",
        )
    except Exception as exc:
        response_kind = {
            "backgroundUsageQuery": "query",
            "backgroundUsageDetail": "detail",
            "openBackgroundUsage": "open",
            "openBackgroundUsageFromInsights": "open",
        }.get(action)
        if response_kind:
            return _background_usage_response_status(
                response_kind,
                background_usage_request_id,
                event_id=str(command.get("eventId") or "").strip(),
                error=f"用量总览读取失败：{exc}",
            )
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
    provider_registry = discover_provider_registry(
        user_config=user_config,
        sessions_root=sessions_root,
    )
    user_config = user_config.migrate_legacy_provider_settings(
        provider_registry.providers(), app_provider=provider_registry.app_provider
    )
    provider_registry = discover_provider_registry(
        user_config=user_config,
        sessions_root=sessions_root,
    )
    sqlite_log_path = _discover_path(platform, args.sse_db, DEFAULT_SQLITE_LOG)
    state_db_path = _discover_path(platform, args.state_db, DEFAULT_STATE_DB)
    session_index_path = _discover_path(platform, None, DEFAULT_SESSION_INDEX)
    runtime_display_mode = _runtime_display_mode(
        getattr(args, "runtime_hud_mode", None)
        or getattr(args, "hud_mode", None)
        or user_config.display_mode
    )
    renderer_active_session_bridge = runtime_display_mode == "renderer"
    if renderer_active_session_bridge:
        try:
            platform.suspend_native_active_title(True)
        except Exception:
            pass
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
        start_background_watcher=not renderer_active_session_bridge,
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
    pre_send_estimator = None
    if DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED:
        pre_send_estimator = PreSendEstimator(
            project_roots=[str(sessions_root.parent)],
        )
        pre_send_estimator.start()
    context = RuntimeContext(
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
        usage_cache=_build_usage_summary_cache(parser),
        app_provider=provider_registry.app_provider,
        provider_registry=provider_registry,
        pre_send_estimator=pre_send_estimator,
        runtime_errors=RuntimeErrorRegistry(),
        renderer_mode=renderer_active_session_bridge,
    )
    if renderer_active_session_bridge and sqlite_log_path.is_file():
        try:
            context.background_usage_runtime = BackgroundUsageRuntime(
                logs_path=sqlite_log_path,
                state_path=state_db_path,
                database_path=(
                    hud_runtime_dir() / BACKGROUND_USAGE_DATABASE_FILENAME
                ),
                provider=provider_registry.app_provider,
                price_table=user_config.price_table(),
                event_bus=context.runtime_events,
                runtime_errors=context.runtime_errors,
            ).start()
        except Exception as exc:
            context.runtime_errors.record(
                source="background_usage",
                code="startup_failed",
                message="Background usage audit could not start; the renderer HUD remains available.",
                severity="warning",
                context={"errorType": type(exc).__name__},
            )
    return context


def _visible_app_error(platform: BasePlatform) -> str:
    try:
        text = platform.get_active_app_error()
    except Exception:
        return ""
    return " ".join(str(text or "").split())


def _apply_visible_app_error(snapshot: ParsedSession, message: str) -> None:
    if not message:
        return
    snapshot.request.status = "error"
    snapshot.request.error = message
    snapshot.request.source = "app"
    snapshot.request.updated_at = snapshot.refreshed_at
    if snapshot.request.started_at is None:
        snapshot.request.started_at = snapshot.task_started_at


def _runtime_debug_enabled() -> bool:
    value = os.environ.get(RUNTIME_DEBUG_ENV)
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized not in {"", "0", "false", "no", "off"}


def _record_active_session_runtime_error(
    context: RuntimeContext,
    selection_source: str,
    session_path: Path | None,
) -> None:
    _ensure_runtime_error_diagnostics(context)
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return
    source = str(selection_source or "")
    pending_codes = {
        "awaiting-canonical-id": "awaiting_canonical_id",
        "awaiting-persistence": "awaiting_persistence",
        "awaiting-exact-mapping": "awaiting_exact_mapping",
        "ambiguous-persisted-identity": "ambiguous_persisted_identity",
        "renderer-channel-unavailable": "renderer_channel_unavailable",
    }
    tracker = getattr(context, "active_session_tracker", None)
    follow_reason = str(getattr(tracker, "follow_reason", "") or "")
    if is_pending_session_source(source):
        code = pending_codes.get(follow_reason, "pending_mapping")
        registry.record(
            source="active_session",
            severity=(
                "error"
                if follow_reason
                in {"ambiguous-persisted-identity", "renderer-channel-unavailable"}
                else "warning"
            ),
            code=code,
            message="Renderer active session is waiting for exact local reconciliation.",
            context={
                "selectionSource": source,
                "followReason": follow_reason,
                "selectionSeq": int(getattr(tracker, "selection_seq", 0) or 0),
                "threadId": str(getattr(tracker, "latest_session_id", "") or ""),
                "title": str(getattr(tracker, "latest_title", "") or ""),
            },
        )
        return
    if source.startswith("renderer-unmatched"):
        registry.record(
            source="active_session",
            severity="error",
            code="unmatched_thread",
            message="Renderer active session could not be mapped to a local JSONL session.",
            context={
                "selectionSource": source,
                "sessionPath": str(session_path or ""),
                "threadId": str(getattr(tracker, "latest_session_id", "") or ""),
                "title": str(getattr(tracker, "latest_title", "") or ""),
                "trackerSource": str(getattr(tracker, "latest_source", "") or ""),
            },
        )
        return
    if source.startswith("renderer:") or is_new_session_source(source):
        registry.resolve(source="active_session", code="unmatched_thread")
        registry.resolve(source="active_session", code="pending_mapping")
        for code in pending_codes.values():
            registry.resolve(source="active_session", code=code)


def _record_cdp_update_failure(
    context: RuntimeContext,
    client: RendererHudClient,
    *,
    failures: int,
) -> None:
    _ensure_runtime_error_diagnostics(context)
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return
    registry.record(
        source="cdp",
        severity="error",
        code="update_failed",
        message="Renderer HUD payload update failed.",
        context={
            "failures": int(failures),
            "status": str(getattr(client, "last_status", "") or ""),
            "error": str(getattr(client, "last_error", "") or ""),
            "timeoutSeconds": float(getattr(client, "timeout_seconds", 0.0) or 0.0),
            "metrics": dict(getattr(client, "last_update_metrics", {}) or {}),
        },
    )


def _resolve_cdp_update_failure(context: RuntimeContext) -> None:
    registry = getattr(context, "runtime_errors", None)
    if registry is not None:
        registry.resolve(source="cdp", code="update_failed")


def _runtime_errors_payload_for_context(context: RuntimeContext) -> list[dict[str, object]]:
    registry = getattr(context, "runtime_errors", None)
    if registry is None:
        return []
    payload = getattr(registry, "to_payload", None)
    return payload() if callable(payload) else []


def build_snapshot(
    context: RuntimeContext,
    *,
    refresh_budget_aggregate: bool | None = None,
    refresh_budget_paths: Iterable[Path] = (),
    refresh_active_work_items: bool = True,
    refresh_current_session_usage: bool = True,
    reuse_budget_from: ParsedSession | None = None,
    refresh_visible_app_error: bool = True,
) -> ParsedSession:
    build_started_at_ms = int(time.time() * 1000)
    context.reload_user_config()
    session_path, selection_source = context.session_resolver.resolve()
    session_resolved_at_ms = int(time.time() * 1000)
    _record_active_session_runtime_error(context, selection_source, session_path)

    if session_path is None:
        if is_new_session_source(selection_source):
            snapshot = ParsedSession(status="waiting")
        elif is_pending_session_source(selection_source):
            snapshot = ParsedSession(status="waiting")
        elif str(selection_source or "").startswith("renderer-waiting"):
            snapshot = ParsedSession(status="waiting")
        elif context.session_resolver.session_id:
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
        cache = getattr(context, "session_snapshot_cache", None)
        snapshot_for = getattr(cache, "snapshot_for", None)
        if callable(snapshot_for):
            snapshot = snapshot_for(
                session_path,
                session_id=str(getattr(context.session_resolver, "session_id", "") or ""),
            )
        else:
            tail_state = getattr(context, "current_session_tail_state", None)
            snapshot, tail_state = context.parser.parse_file_incremental(
                session_path,
                tail_state,
                sse_tracker=context.sse_tracker,
            )
            context.current_session_tail_state = tail_state
    session_parsed_at_ms = int(time.time() * 1000)
    snapshot.selection_source = selection_source
    if context.active_session_tracker is not None:
        tracker = context.active_session_tracker
        snapshot.renderer_session_id = str(
            getattr(tracker, "renderer_session_id", "") or ""
        )
        snapshot.selection_seq = int(getattr(tracker, "selection_seq", 0) or 0)
        snapshot.selection_observed_at_ms = int(
            getattr(tracker, "selection_observed_at_ms", 0) or 0
        )
        snapshot.follow_state = str(getattr(tracker, "follow_state", "") or "")
        snapshot.follow_reason = str(getattr(tracker, "follow_reason", "") or "")
        stuck_since = int(getattr(tracker, "follow_stuck_since_ms", 0) or 0)
        stuck_elapsed = int(getattr(tracker, "follow_stuck_elapsed_ms", 0) or 0)
        snapshot.follow_timing = {
            "observedAt": int(getattr(tracker, "selection_observed_at_ms", 0) or 0),
            "receivedAt": int(getattr(tracker, "selection_received_at_ms", 0) or 0),
            "resolvedAt": int(getattr(tracker, "selection_resolved_at_ms", 0) or 0),
            "stuckSince": stuck_since,
            "stuckElapsedMs": stuck_elapsed,
        }
        if session_path is not None:
            snapshot.session_title = tracker.title_for_session(
                session_path,
                snapshot.session_id,
            )
    tracker_enriched_at_ms = int(time.time() * 1000)
    app_error = context.visible_app_error_cache.resolve(
        snapshot,
        _visible_app_error(context.platform) if refresh_visible_app_error else "",
    )
    _apply_visible_app_error(snapshot, app_error)
    app_error_checked_at_ms = int(time.time() * 1000)

    if reuse_budget_from is not None:
        for field_name in (
            "today_tokens",
            "today_cost_usd",
            "week_tokens",
            "week_cost_usd",
            "week_before_today_tokens",
            "week_before_today_cost_usd",
            "week_adjustment_usd",
            "family_tokens",
            "family_cost_usd",
            "family_member_count",
            "daily_limit_usd",
            "weekly_limit_usd",
            "day_start",
            "week_start",
            "budget_error",
        ):
            setattr(snapshot, field_name, getattr(reuse_budget_from, field_name))
        snapshot.budget_warnings = list(reuse_budget_from.budget_warnings)
        if int(getattr(snapshot, "family_tokens", 0) or 0) <= 0:
            provider_scope = _effective_provider_scope(context, snapshot)
            _apply_family_session_usage(context, snapshot, provider_scope)
    else:
        day_start, week_start = current_budget_windows(context.user_config)
        refresh_budget_paths = tuple(Path(path) for path in refresh_budget_paths)
        if (
            refresh_budget_aggregate is False
            and not refresh_budget_paths
            and session_path is not None
            and refresh_current_session_usage
        ):
            refresh_budget_paths = (session_path,)
        provider_scope = _effective_provider_scope(context, snapshot)
        today_total, week_total = context.usage_cache.summarize(
            context.sessions_root,
            day_start,
            week_start,
            allow_stale=refresh_budget_aggregate is False,
            force_rescan=refresh_budget_aggregate is True,
            refresh_paths=refresh_budget_paths,
            included_providers=provider_scope,
        )
        week_adjustment_usd = context.user_config.weekly_adjustment_for_scope(
            provider_scope
        )
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
        _refresh_usage_insights_payload(context)
        _apply_family_session_usage(context, snapshot, provider_scope)
    usage_summarized_at_ms = int(time.time() * 1000)
    if refresh_active_work_items:
        snapshot.active_work_items = active_work_items_for_snapshot(
            context,
            snapshot,
            session_path,
        )
    _apply_pre_send_and_activity(context, snapshot)
    snapshot.follow_timing = {
        **dict(snapshot.follow_timing or {}),
        "buildStartedAt": build_started_at_ms,
        "sessionResolvedAt": session_resolved_at_ms,
        "sessionParsedAt": session_parsed_at_ms,
        "trackerEnrichedAt": tracker_enriched_at_ms,
        "appErrorCheckedAt": app_error_checked_at_ms,
        "usageSummarizedAt": usage_summarized_at_ms,
        "runtimeEnrichedAt": int(time.time() * 1000),
    }
    _update_session_cleanup_activity(context, snapshot)
    return snapshot


def _update_session_cleanup_activity(
    context: RuntimeContext,
    snapshot: ParsedSession,
) -> None:
    session_id = str(snapshot.session_id or "").strip()
    try:
        canonical = str(uuid.UUID(session_id))
    except (AttributeError, TypeError, ValueError):
        canonical = ""
    context.session_management_current_session_id = (
        canonical if canonical == session_id.casefold() else ""
    )
    active: set[str] = set()
    if context.session_management_current_session_id and (
        snapshot.request.status == "running"
        or snapshot.slow.current_gap_active
        or snapshot.activity.kind in {"tool call", "agent", "assistant"}
    ):
        active.add(context.session_management_current_session_id)
    for item in snapshot.active_work_items:
        value = str(item.session_id or "").strip()
        try:
            canonical = str(uuid.UUID(value))
        except (AttributeError, TypeError, ValueError):
            continue
        if canonical == value.casefold() and str(item.status or "") not in {"recent"}:
            active.add(canonical)
    context.session_management_active_session_ids = active


def _apply_pre_send_and_activity(
    context: RuntimeContext,
    snapshot: ParsedSession,
) -> None:
    """Attach the static pre-send base estimate and live reading-activity light.

    The base estimate (C context files + D MCP schema + F padding) is produced
    off-thread by ``PreSendEstimator``; the session-history term (B) comes for
    free from the API-confirmed input tokens so it stays exact and needs no
    re-tokenization. Reading activity (E) is derived from the just-parsed
    snapshot at zero extra I/O.
    """
    if not DEFAULT_COMPOSER_TIKTOKEN_BADGE_ENABLED:
        snapshot.estimate_base = BaseEstimate()
        snapshot.reading_activity = ReadingActivity()
        return

    estimator = getattr(context, "pre_send_estimator", None)
    if estimator is not None:
        # Keep the context-file scan anchored to the active session's cwd.
        if snapshot.cwd:
            estimator.set_project_roots([snapshot.cwd])
        base = estimator.latest()
        confirmed = snapshot.confirmed
        if confirmed.last_input > 0:
            # 已有真实请求：`last_input` 就是上一次实际发送的完整上下文（历史 +
            # 系统提示 + 工具 + 协议开销都已包含在内），直接用它作为"会话上下文"，
            # 不再另加 C/D/F，否则会重复计算。缓存拆分也用实测的命中/未命中量。
            base = base.with_confirmed_context(
                cached_tokens=confirmed.last_cached,
                uncached_tokens=max(0, confirmed.last_input - confirmed.last_cached),
            )
        else:
            # 会话首条消息：无真实 token_count，用冷启动估算（历史累加=0，仅 C/D/F）。
            base = base.with_session_history(int(confirmed.cumulative_input or 0))
        snapshot.estimate_base = _apply_pre_send_pricing(context, snapshot, base)
    snapshot.reading_activity = detect_reading_activity(snapshot)


def _apply_pre_send_pricing(
    context: RuntimeContext,
    snapshot: ParsedSession,
    base: "BaseEstimate",
) -> "BaseEstimate":
    """Attach per-token input/cached prices and the real cache-hit rate.

    Prices come from the user-configured cost estimator (so custom price tables
    apply). The cache-hit rate is the ratio measured on the most recent real
    request (``last_cached / last_input``); it is 0 on the very first turn.
    """
    model = snapshot.request.model or ""
    estimator = context.parser.cost_estimator
    million = 1_000_000
    # input 单价：100 万个全未命中 input token 的成本 / 100 万。
    input_cost = estimator.calculate(model, million, 0, 0, 0)
    # cached 单价：100 万个全命中 input token 的成本 / 100 万。
    cached_cost = estimator.calculate(model, million, million, 0, 0)
    if input_cost is None or cached_cost is None:
        return base
    confirmed = snapshot.confirmed
    cache_rate = 0.0
    if confirmed.last_input > 0:
        cache_rate = min(1.0, max(0.0, confirmed.last_cached / confirmed.last_input))
    return base.with_pricing(
        input_price_per_token=input_cost / million,
        cached_price_per_token=cached_cost / million,
        cache_hit_rate=cache_rate,
        model_name=model,
    )


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
            "Explicitly run the default persistent daemon: wait for Codex, "
            "show the HUD, and keep watching after Codex closes."
        ),
    )
    parser.add_argument(
        "--no-startup-prompt",
        action="store_true",
        help=(
            "Deprecated compatibility flag. Renderer startup no longer uses a "
            "modal startup choice."
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Use compact output mode for CLI snapshots.",
    )
    parser.set_defaults(renderer_hud=None)
    parser.add_argument(
        "--renderer-hud",
        dest="renderer_hud",
        action="store_true",
        help=(
            "Use the renderer-injected HUD when Codex exposes a local CDP target. "
            "Enabled by default."
        ),
    )
    parser.add_argument(
        "--hud-mode",
        choices=["renderer"],
        help="Override the configured HUD display mode for this run.",
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
        "--legacy-active-session-diagnostics",
        action="store_true",
        help=argparse.SUPPRESS,
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
    parser.add_argument(
        "--loading-feedback-helper",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--loading-feedback-state-file",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--work-overlay-helper",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--work-overlay-state-file",
        default="",
        help=argparse.SUPPRESS,
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
    hide_until_attached: bool = True,
    daemon_manager: CodexDaemonManager | None = None,
    loading_feedback: HudLoadingFeedback | None = None,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
) -> int:
    """Run one renderer-injected HUD session."""
    del hide_until_attached
    session_args = _clone_args_with_display_mode(args, "renderer")
    renderer_exit = run_renderer_hud_session(
        session_args,
        lock_already_held=lock_already_held,
        daemon_manager=daemon_manager,
        launched_codex=launched_codex,
        observed_codex_launch=observed_codex_launch,
        loading_feedback=loading_feedback,
    )
    if renderer_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
        if not _restart_codex_for_renderer():
            return RENDERER_HUD_UNAVAILABLE
        return run_renderer_hud_session(
            _clone_args_with_renderer_preference(args, True),
            lock_already_held=lock_already_held,
            daemon_manager=daemon_manager,
            launched_codex=True,
            observed_codex_launch=False,
            loading_feedback=loading_feedback,
        )
    if renderer_exit == HUD_SWITCH_TO_RENDERER:
        _LOGGER.info("renderer_hud_legacy_switch_ignored code=%s", renderer_exit)
        return RENDERER_HUD_UNAVAILABLE
    return renderer_exit


def run_renderer_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    launched_codex: bool = False,
    observed_codex_launch: bool = False,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Run the in-renderer HUD over CDP, or report that it is unavailable."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            local_loading = loading_feedback
            try:
                startup_plan = _renderer_startup_plan(
                    launched_codex=launched_codex,
                    observed_codex_launch=observed_codex_launch,
                )
            except (OSError, RuntimeError) as exc:
                if local_loading is not None:
                    local_loading.close()
                _append_renderer_diagnostic(
                    "renderer_cdp_launch_failed",
                    reason=str(exc),
                    source="startup-classification",
                )
                return RENDERER_HUD_UNAVAILABLE
            if startup_plan.scenario == RENDERER_STARTUP_RELAUNCH_OBSERVED:
                if local_loading is not None:
                    local_loading.update(
                        title="正在切换到 Renderer HUD",
                        message="检测到普通 Codex 启动，正在改用调试/CDP 模式…",
                    )
                _append_renderer_diagnostic(
                    "renderer_observed_plain_launch_takeover",
                    reason=startup_plan.reason,
                )
                return HUD_AUTO_RESTART_CODEX
            codex_was_running = startup_plan.scenario in {
                RENDERER_STARTUP_ATTACH,
                RENDERER_STARTUP_RESTART_REQUIRED,
            }
            if startup_plan.scenario == RENDERER_STARTUP_LAUNCH:
                if local_loading is not None:
                    local_loading.update(
                        title="正在启动 Renderer HUD",
                        message="正在以调试/CDP 模式启动 Codex App...",
                    )
                if not launch_codex_app(debugger=True):
                    if local_loading is not None:
                        local_loading.close()
                    _append_renderer_diagnostic(
                        "renderer_cdp_launch_failed",
                        port=startup_plan.port,
                        source=startup_plan.port_source,
                    )
                    return RENDERER_HUD_UNAVAILABLE
                launched_codex = True
            context = build_runtime_context(args)
            display_mode = normalize_display_mode(
                getattr(args, "hud_mode", None) or context.user_config.display_mode
            )
            work_overlay = DesktopWorkOverlay(
                item_limit=_work_overlay_item_limit_for_context(context),
            )
            if startup_plan.scenario == RENDERER_STARTUP_RESTART_REQUIRED:
                try:
                    requested = _wait_for_renderer_restart_request(
                        args,
                        work_overlay,
                        local_loading,
                    )
                    return (
                        HUD_SWITCH_TO_RENDERER_RESTART_CODEX
                        if requested
                        else RENDERER_HUD_UNAVAILABLE
                    )
                except KeyboardInterrupt:
                    if local_loading is not None:
                        local_loading.close()
                    return 130
                finally:
                    work_overlay.close()
                    context.close()
            cold_start_attach = bool(
                launched_codex
                or startup_plan.scenario == RENDERER_STARTUP_ATTACH_OBSERVED
            )
            renderer_cdp_timeout = (
                RENDERER_RESTART_CDP_TIMEOUT_SECONDS
                if cold_start_attach
                else (
                    DAEMON_RENDERER_CDP_TIMEOUT_SECONDS
                    if daemon_manager is not None and not codex_was_running
                    else RENDERER_CDP_TIMEOUT_SECONDS
                )
            )
            client = RendererHudClient(
                port=startup_plan.port,
                timeout_seconds=renderer_cdp_timeout,
            )
            update_manager = AutoUpdateManager(current_version=__version__)
            restart_requested = Event()
            exit_requested = Event()
            command_refresh_requested = Event()
            active_session_refresh_requested = Event()
            command_pump: _WorkOverlayCommandPump | None = None
            runtime_event_unsubscribe = None
            bridge_commands: deque[dict[str, object]] = deque()
            bridge_command_lock = threading.Lock()

            def request_active_session_refresh() -> None:
                active_session_refresh_requested.set()
                command_refresh_requested.set()

            pre_send_estimator = getattr(context, "pre_send_estimator", None)
            if pre_send_estimator is not None:
                pre_send_estimator.update_callback = (
                    lambda _estimate: request_active_session_refresh()
                )

            runtime_event_bus = getattr(context, "runtime_events", None)
            runtime_event_subscribe = getattr(runtime_event_bus, "subscribe", None)
            runtime_event_publish = getattr(runtime_event_bus, "publish", None)
            runtime_event_drain = getattr(runtime_event_bus, "drain", None)
            if callable(runtime_event_subscribe):

                def wake_for_runtime_event(event: object) -> None:
                    event_type = str(getattr(event, "type", "") or "")
                    if event_type in {
                        "overlay_command_received",
                        "session_file_changed",
                        "settings_command_received",
                        "settings_changed",
                        "budget_window_changed",
                        "renderer_layout_changed",
                        "renderer_theme_changed",
                        "session_snapshot_hydrated",
                        "background_usage_changed",
                        "usage_insights_changed",
                        "session_cleanup_changed",
                    }:
                        command_refresh_requested.set()
                    elif event_type == "active_session_changed":
                        request_active_session_refresh()

                runtime_event_unsubscribe = runtime_event_subscribe(
                    wake_for_runtime_event
                )

            def publish_active_session_changed(reason: str) -> None:
                if callable(runtime_event_publish):
                    runtime_event_publish(
                        "active_session_changed",
                        source="active_session",
                        context={"reason": reason},
                    )
                    return
                request_active_session_refresh()

            # Shared with the renderer loop; created early so CDP binding callbacks
            # can update the connection light before the first tick runs.
            connection_health = ConnectionHealth()
            connection_health.note_success("ok")
            # Filled once the loop defines push_connection_health_light.
            connection_health_pushers: dict[str, Callable[[], bool] | None] = {
                "push": None
            }

            def request_connection_health_light() -> None:
                pusher = connection_health_pushers.get("push")
                if callable(pusher):
                    try:
                        pusher()
                        return
                    except Exception:
                        pass
                command_refresh_requested.set()

            def publish_settings_command_received(command: dict[str, object]) -> None:
                if callable(runtime_event_publish):
                    runtime_event_publish(
                        "settings_command_received",
                        source="settings_bridge",
                        context={
                            "action": str(command.get("action") or ""),
                            "id": str(command.get("id") or ""),
                            "command": dict(command),
                        },
                    )
                    return
                command_refresh_requested.set()

            tracker_change_callback = getattr(
                getattr(context, "active_session_tracker", None),
                "set_change_callback",
                None,
            )
            if callable(tracker_change_callback):
                tracker_change_callback(
                    lambda: publish_active_session_changed("tracker_callback")
                )

            def enqueue_renderer_command(command: dict[str, object]) -> None:
                with bridge_command_lock:
                    bridge_commands.append(dict(command))
                publish_settings_command_received(dict(command))

            def handle_background_usage_overlay_command(
                command: dict[str, object],
            ) -> bool:
                runtime = getattr(context, "background_usage_runtime", None)
                action = str(command.get("action") or "").strip()
                event_id = str(command.get("eventId") or "").strip()
                if runtime is None or not event_id:
                    return False
                if action == "dismissBackgroundUsage":
                    confirm = getattr(runtime, "confirm", None)
                    return bool(callable(confirm) and confirm(event_id))
                if action == "openBackgroundUsage":
                    request_id = f"background-overlay-{uuid.uuid4().hex}"
                    enqueue_renderer_command(
                        {
                            "id": request_id,
                            "requestId": request_id,
                            "action": "openBackgroundUsage",
                            "eventId": event_id,
                        }
                    )
                    return True
                return False

            def observe_renderer_active_session(payload: dict[str, object]) -> None:
                tracker = getattr(context, "active_session_tracker", None)
                if bool(
                    payload.get("channelUnavailable")
                    or payload.get("channel_unavailable")
                ):
                    marker = getattr(tracker, "mark_renderer_channel_unavailable", None)
                    if callable(marker):
                        marker(str(payload.get("reason") or ""))
                    connection_health.note_channel_unavailable(
                        str(payload.get("reason") or "channel-unavailable")
                    )
                    request_connection_health_light()
                    return
                observer = getattr(tracker, "observe_conversation_ref", None)
                if not callable(observer):
                    return
                observer_kwargs = {
                    "session_id": str(
                        payload.get("sessionId") or payload.get("session_id") or ""
                    ),
                    "title": str(payload.get("title") or ""),
                    "source": "renderer",
                }
                renderer_session_id = str(
                    payload.get("rendererSessionId")
                    or payload.get("renderer_session_id")
                    or ""
                )
                if renderer_session_id:
                    observer_kwargs["renderer_session_id"] = renderer_session_id
                selection_seq = payload.get("selectionSeq") or payload.get(
                    "selection_seq"
                )
                if selection_seq:
                    observer_kwargs["selection_seq"] = selection_seq
                observed_at_ms = payload.get("observedAt") or payload.get(
                    "observed_at_ms"
                )
                if observed_at_ms:
                    observer_kwargs["observed_at_ms"] = observed_at_ms
                if bool(payload.get("newSession") or payload.get("new_session")):
                    observer_kwargs["new_session"] = True
                if bool(
                    payload.get("pendingSession") or payload.get("pending_session")
                ):
                    observer_kwargs["pending_session"] = True
                changed = observer(**observer_kwargs)
                if not bool(payload.get("newSession") or payload.get("new_session")):
                    if not connection_health.channel_available:
                        connection_health.note_channel_restored()
                        request_connection_health_light()
                if _renderer_active_session_observation_should_refresh(
                    changed=bool(changed),
                    selection_seq=selection_seq,
                    tracker=tracker,
                ):
                    publish_active_session_changed("renderer_bridge")

            def observe_renderer_attachments(payload: dict[str, object]) -> None:
                estimator = getattr(context, "pre_send_estimator", None)
                if estimator is None:
                    return
                estimator.set_attachments(payload)
                # 附件变化通常伴随 token 变化，唤醒一次刷新让浮窗尽快重绘。
                request_active_session_refresh()

            # 页面 CSP 拦截了到本地桥的 fetch，因此优先用 CDP binding 接收附件，
            # HTTP 桥作兜底（非渲染模式或旧版页面仍可用）。
            def observe_renderer_layout(payload: dict[str, object]) -> None:
                if callable(runtime_event_publish):
                    runtime_event_publish(
                        "renderer_layout_changed",
                        source="renderer_layout",
                        context={
                            "reason": str(payload.get("reason") or ""),
                            "panel": str(payload.get("panel") or ""),
                            "layout": payload.get("layout"),
                            "observedAt": payload.get("observedAt"),
                        },
                    )
                    return
                # Runtime bus disabled — still wake the loop so the next tick
                # sees any settings/state side effects triggered by the drag.
                command_refresh_requested.set()

            def observe_renderer_theme(payload: dict[str, object]) -> None:
                if callable(runtime_event_publish):
                    runtime_event_publish(
                        "renderer_theme_changed",
                        source="renderer_theme",
                        context={"theme": dict(payload)},
                    )
                    return
                command_refresh_requested.set()

            def configure_renderer_client(renderer_client: object) -> None:
                """Attach every CDP binding to a newly created renderer client.

                Fresh-port recovery replaces the client instance after Codex has
                restarted.  The bindings are instance-owned, so omitting this
                step silently loses the renderer session authority and leaves
                the HUD in its pending/new-session state indefinitely.
                """
                bindings = (
                    ("set_active_session_callback", observe_renderer_active_session),
                    ("set_settings_command_callback", enqueue_renderer_command),
                    ("set_attachments_callback", observe_renderer_attachments),
                    ("set_layout_callback", observe_renderer_layout),
                    ("set_theme_callback", observe_renderer_theme),
                )
                for setter_name, callback in bindings:
                    setter = getattr(renderer_client, setter_name, None)
                    if callable(setter):
                        setter(callback)

            configure_renderer_client(client)

            def take_renderer_bridge_command() -> dict[str, object] | None:
                with bridge_command_lock:
                    if not bridge_commands:
                        return None
                    return bridge_commands.popleft()

            background_usage_runtime = getattr(
                context,
                "background_usage_runtime",
                None,
            )
            bridge = SettingsBridgeServer(
                context.settings_store,
                restart_callback=restart_requested.set,
                command_callback=enqueue_renderer_command,
                active_session_callback=observe_renderer_active_session,
                attachments_callback=observe_renderer_attachments,
                background_usage_query_callback=(
                    lambda filters: background_usage_runtime.query(**filters)
                    if background_usage_runtime is not None
                    else None
                ),
                background_usage_detail_callback=(
                    background_usage_runtime.detail
                    if background_usage_runtime is not None
                    else None
                ),
                background_usage_confirm_callback=(
                    background_usage_runtime.confirm
                    if background_usage_runtime is not None
                    else None
                ),
            )
            bridge_url = bridge.start()
            background_usage_bridge_url = (
                bridge.background_usage_url
                if background_usage_runtime is not None
                else ""
            )

            def renderer_startup_payload(
                *,
                step: str,
                detail: str,
                progress: int,
            ) -> dict[str, object]:
                return {
                    "payloadDomains": {
                        "startup": {
                            "step": step,
                            "title": "正在启动 Codex HUD",
                            "detail": detail,
                            "progress": max(0, min(100, int(progress))),
                        }
                    }
                }

            def update_renderer_startup(
                *,
                step: str,
                detail: str,
                progress: int,
            ) -> bool:
                show_startup = getattr(client, "show_startup", None)
                if not callable(show_startup):
                    return False
                return bool(
                    show_startup(
                        renderer_startup_payload(
                            step=step,
                            detail=detail,
                            progress=progress,
                        )
                    )
                )

            def bootstrap_renderer_active_session(
                *,
                step: str,
                detail: str,
                progress: int,
            ) -> bool:
                bootstrap = getattr(client, "bootstrap_active_session", None)
                if not callable(bootstrap):
                    return False
                command_refresh_requested.clear()
                startup_payload = renderer_startup_payload(
                    step=step,
                    detail=detail,
                    progress=progress,
                )
                try:
                    bootstrapped = bool(bootstrap(startup_payload=startup_payload))
                except TypeError:
                    # Compatibility for third-party/testing clients that still
                    # expose the old no-argument bootstrap method.
                    bootstrapped = bool(bootstrap())
                metrics = dict(
                    getattr(client, "last_bootstrap_metrics", {}) or {}
                )
                _LOGGER.info(
                    "renderer_active_session_bootstrap ok=%s step=%s progress=%s total_ms=%s failure_stage=%s",
                    bootstrapped,
                    step,
                    progress,
                    metrics.get("totalMs", "-"),
                    metrics.get("failureStage", ""),
                )
                if bootstrapped:
                    command_refresh_requested.wait(
                        RENDERER_ACTIVE_SESSION_BOOTSTRAP_WAIT_SECONDS
                    )
                return bootstrapped

            def renderer_startup_progress(stage: str) -> None:
                stages = {
                    "reading_session": (
                        "第 3 步，共 4 步",
                        "正在识别当前打开的会话…",
                        62,
                    ),
                    "showing_hud": (
                        "第 4 步，共 4 步",
                        "会话信息已就绪，正在显示用量与预算…",
                        88,
                    ),
                }
                step, detail, progress = stages.get(
                    str(stage or ""),
                    ("正在启动", "正在准备 HUD…", 40),
                )
                update_renderer_startup(
                    step=step,
                    detail=detail,
                    progress=progress,
                )

            def snapshot_or_error(
                *,
                refresh_budget_aggregate: bool | None = None,
                refresh_budget_paths: Iterable[Path] = (),
                refresh_active_work_items: bool = True,
                refresh_current_session_usage: bool = True,
                reuse_budget_from: ParsedSession | None = None,
                refresh_visible_app_error: bool = True,
            ) -> ParsedSession:
                try:
                    if refresh_budget_aggregate is None and refresh_active_work_items:
                        return build_snapshot(context)
                    if refresh_budget_aggregate is None:
                        return build_snapshot(
                            context,
                            refresh_active_work_items=False,
                        )
                    snapshot_kwargs: dict[str, object] = {
                        "refresh_budget_aggregate": refresh_budget_aggregate,
                        "refresh_budget_paths": refresh_budget_paths,
                    }
                    if reuse_budget_from is not None:
                        snapshot_kwargs["reuse_budget_from"] = reuse_budget_from
                    if not refresh_visible_app_error:
                        snapshot_kwargs["refresh_visible_app_error"] = False
                    if not refresh_current_session_usage:
                        snapshot_kwargs["refresh_current_session_usage"] = False
                    if refresh_active_work_items:
                        return build_snapshot(context, **snapshot_kwargs)
                    return build_snapshot(
                        context,
                        refresh_active_work_items=False,
                        **snapshot_kwargs,
                    )
                except Exception as exc:
                    return ParsedSession(status="error", error=str(exc))

            try:
                # A missing app is launched once through the fixed-port
                # renderer launcher.  An already-running app is never
                # restarted or reconfigured by the HUD.
                # The daemon can observe an already running Codex process, but
                # that does not mean this invocation launched it. Fresh
                # externally observed launches are also allowed a bounded
                # first-window/CDP readiness wait; ordinary existing instances
                # still take the bounded single strict attach path below.
                wait_for_window = cold_start_attach or (
                    sys.platform.startswith("win") and not codex_was_running
                )
                # Startup classification is the only owner of a CDP launch.
                # Window preparation may activate/focus the selected process,
                # but it must never race the classified launch with another.
                launch_if_missing = False
                if local_loading is not None:
                    local_loading.update(
                        title=(
                            "正在切换到 Renderer HUD"
                            if cold_start_attach
                            else "正在启动 Renderer HUD"
                        ),
                        message="正在拉起 Codex 主窗口并切到前台，确保 Renderer 注入目标正确...",
                    )
                (
                    window_prepared,
                    window_status,
                    window_reason,
                    window_hwnd,
                ) = _prepare_codex_window_for_renderer(
                    timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
                    launch_if_missing=launch_if_missing,
                )
                if not window_prepared:
                    _LOGGER.info(
                        "renderer_hud_window_prepare_best_effort_failed status=%s hwnd=%s reason=%s",
                        window_status,
                        window_hwnd,
                        window_reason,
                    )
                if wait_for_window:
                    if local_loading is not None:
                        local_loading.update(
                            title=(
                                "正在切换到 Renderer HUD"
                                if cold_start_attach
                                else "正在启动 Renderer HUD"
                            ),
                            message="正在等待 Codex 主窗口和调试端口准备完成...",
                        )
                    (
                        window_ready,
                        window_status,
                        window_reason,
                        window_hwnd,
                    ) = _wait_for_visible_codex_window(
                        timeout_seconds=DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS
                    )
                    if not window_ready:
                        if local_loading is not None:
                            local_loading.close()
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
                    RENDERER_RESTART_INITIAL_TIMEOUT_SECONDS
                    if cold_start_attach
                    else (
                        DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS
                        if wait_for_window
                        else RENDERER_INITIAL_TIMEOUT_SECONDS
                    )
                )
                if local_loading is not None:
                    local_loading.update(
                        title=(
                            "正在切换到 Renderer HUD"
                            if cold_start_attach
                            else "正在启动 Renderer HUD"
                        ),
                        message="正在把 HUD 注入 Codex 界面，通常只需 1 到 3 秒...",
                    )
                # Install the renderer controller and paint stage 1 before
                # waiting for the active-session binding.  Binding readiness
                # may lag script installation briefly on a cold Codex launch;
                # tying the first visible bubble to that binding caused the
                # startup panel to be skipped intermittently.
                renderer_startup_visible = update_renderer_startup(
                    step="第 1 步，共 4 步",
                    detail="已连接 Codex，正在准备 HUD…",
                    progress=18,
                )
                if renderer_startup_visible and local_loading is not None:
                    # The native pre-CDP card occupies the same top-right slot.
                    # Close it as soon as the in-renderer panel can take over.
                    local_loading.close()
                    local_loading = None
                initial_bootstrapped = False
                if renderer_startup_visible:
                    initial_bootstrapped = bootstrap_renderer_active_session(
                        step="第 1 步，共 4 步",
                        detail="已连接 Codex，正在准备 HUD…",
                        progress=18,
                    )
                if renderer_startup_visible or initial_bootstrapped:
                    # The synchronous bootstrap can complete in a single frame.
                    # Keep the first two stages legible rather than jumping
                    # straight to session discovery on fast machines.
                    if getattr(client, "enabled", False) is True:
                        time.sleep(RENDERER_STARTUP_STEP_MIN_VISIBLE_SECONDS)
                    update_renderer_startup(
                        step="第 2 步，共 4 步",
                        detail="正在建立安全的 HUD 通道…",
                        progress=35,
                    )
                initial_wait_kwargs: dict[str, object] = {
                    "timeout_seconds": initial_timeout,
                }
                if renderer_startup_visible or initial_bootstrapped:
                    initial_wait_kwargs["progress_callback"] = renderer_startup_progress
                skip_redundant_existing_attach = bool(
                    codex_was_running
                    and not launched_codex
                    and not renderer_startup_visible
                    and local_loading is not None
                )
                renderer_attached = False
                if not skip_redundant_existing_attach:
                    renderer_attached = wait_for_renderer(
                        client,
                        snapshot_or_error,
                        **initial_wait_kwargs,
                    )
                if not renderer_attached:
                    original_error = client.last_error
                    restart_can_help = bool(
                        startup_plan.scenario
                        in {
                            RENDERER_STARTUP_ATTACH,
                            RENDERER_STARTUP_ATTACH_OBSERVED,
                        }
                        and (
                            _renderer_initial_failure_should_recover_cdp_port(
                                original_error
                            )
                            or _renderer_initial_failure_can_be_fixed_by_restart(
                                original_error
                            )
                        )
                    )
                    if restart_can_help:
                        current_port = _valid_renderer_cdp_port(
                            getattr(client, "port", startup_plan.port)
                        )
                        if current_port is not None:
                            target_still_valid, _reason = _validate_renderer_cdp_candidate(
                                _RendererCdpPortCandidate(
                                    port=current_port,
                                    source=startup_plan.port_source or "startup-plan",
                                )
                            )
                            restart_can_help = not target_still_valid
                    if restart_can_help:
                        _append_renderer_diagnostic(
                            "initial_connect_restart_waiting_for_user",
                            status=client.last_status,
                            error=original_error,
                            old_port=getattr(client, "port", None),
                            display_mode=display_mode,
                            daemon_mode=daemon_manager is not None,
                        )
                        if _wait_for_renderer_restart_request(
                            args,
                            work_overlay,
                            local_loading,
                        ):
                            return HUD_SWITCH_TO_RENDERER_RESTART_CODEX
                        _LOGGER.info("renderer_cdp_port_restart_card_unavailable")
                    if local_loading is not None:
                        local_loading.close()
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
                else:
                    _remember_successful_renderer_cdp_port(
                        getattr(client, "port", None)
                    )

                session_controller = _build_session_switch_controller(
                    getattr(context, "platform", get_current_platform()),
                    prefer_native_search=False,
                    cdp_port=getattr(client, "port", None),
                )

                def handle_rest_reminder_overlay_command(
                    command: dict[str, object],
                ) -> bool:
                    presenter = getattr(context, "rest_reminder", None)
                    if presenter is None:
                        return False
                    action = str(command.get("action") or "").strip()
                    if action == "restReminderAck":
                        presenter.acknowledge()
                        ok = True
                    elif action == "restReminderPostpone":
                        ok = bool(presenter.postpone())
                    elif action == "restReminderStart":
                        ok = bool(presenter.start_rest())
                    elif action == "restReminderFinish":
                        ok = bool(presenter.finish_rest())
                    else:
                        return False
                    work_overlay.update_rest_reminder(
                        presenter.desktop_bubble_payload()
                    )
                    return ok

                command_pump = _WorkOverlayCommandPump(
                    work_overlay,
                    session_controller,
                    command_event=command_refresh_requested,
                    runtime_events=getattr(context, "runtime_events", None),
                    runtime_errors=getattr(context, "runtime_errors", None),
                    background_command_callback=(
                        handle_background_usage_overlay_command
                    ),
                    rest_reminder_command_callback=(
                        handle_rest_reminder_overlay_command
                    ),
                )
                file_events = _RendererFileEventSource(
                    context,
                    command_refresh_requested,
                )
                active_work_pump = _RendererActiveWorkPump(
                    context,
                    command_refresh_requested,
                )
                if local_loading is not None:
                    local_loading.close()
                command_pump.start()

                @dataclass
                class _RendererEventRefreshRequest:
                    """Refresh intent requested by typed runtime event handlers."""

                    snapshot: bool = False
                    force_fast: bool = False
                    active_session: bool = False
                    diagnostics: bool = False
                    background_usage: bool = False
                    domains: set[str] = field(default_factory=set)
                    theme_payload: dict[str, object] | None = None

                    def request_snapshot(self, *, force_fast: bool = False) -> None:
                        self.snapshot = True
                        self.force_fast = self.force_fast or force_fast

                    def request_active_session(self) -> None:
                        self.active_session = True
                        self.request_snapshot(force_fast=True)

                    def request_diagnostics(self) -> None:
                        self.diagnostics = True
                        self.force_fast = True
                        self.domains.add("diagnostics")

                    def request_background_usage(self) -> None:
                        self.background_usage = True
                        self.force_fast = True

                    def request_domains(
                        self,
                        *domain_names: str,
                        force_fast: bool = False,
                    ) -> None:
                        self.force_fast = self.force_fast or force_fast
                        for name in domain_names:
                            key = str(name or "").strip()
                            if key:
                                self.domains.add(key)

                @dataclass
                class _RendererTickInputs:
                    """Immutable snapshot of wakeup reasons sampled at tick start."""

                    started: float
                    update_state: dict[str, object]
                    bridge_wakeup: bool
                    active_session_wakeup: bool
                    file_change_reasons: set[str]
                    file_change_paths: set[Path]
                    command: dict[str, object] | None
                    budget_window_keys: tuple[str, str]
                    runtime_events: list[object]
                    event_refresh_request: _RendererEventRefreshRequest

                    @property
                    def file_refresh_requested(self) -> bool:
                        return bool(self.file_change_reasons)

                @dataclass
                class _RendererLoopState:
                    """State carried across renderer ticks."""

                    failures: int = 0
                    settings_command_status: dict[str, object] = field(default_factory=dict)
                    next_daemon_check_at: float = 0.0
                    latest_snapshot: ParsedSession | None = None
                    latest_budget_signature: tuple[object, ...] | None = None
                    latest_budget_window_keys: tuple[str, str] | None = None
                    latest_update_state_signature: tuple[object, ...] | None = None
                    latest_update_state: dict[str, object] | None = None
                    latest_active_work_refresh_at: float = 0.0
                    active_work_refresh_pending: bool = False
                    active_work_refresh_not_before: float = 0.0
                    background_usage_response_retry_attempts: int = 0
                    background_usage_response_retry_not_before: float = 0.0
                    soft_reinstall_pending: bool = False
                    activity_wake_pending: str = ""

                loop_state = _RendererLoopState()

                def reset_background_usage_response_retry() -> None:
                    loop_state.background_usage_response_retry_attempts = 0
                    loop_state.background_usage_response_retry_not_before = 0.0

                def schedule_background_usage_response_retry() -> None:
                    if not _has_pending_background_usage_response(
                        loop_state.settings_command_status
                    ):
                        return
                    next_attempt = (
                        loop_state.background_usage_response_retry_attempts + 1
                    )
                    delay = _background_usage_response_retry_delay_seconds(
                        next_attempt
                    )
                    if delay is None:
                        reset_background_usage_response_retry()
                        return
                    loop_state.background_usage_response_retry_attempts = next_attempt
                    loop_state.background_usage_response_retry_not_before = (
                        time.monotonic() + delay
                    )

                def current_event_session() -> str | None:
                    snapshot = loop_state.latest_snapshot
                    session_path = getattr(snapshot, "session_path", None)
                    if session_path is None:
                        return None
                    return _session_path_key(session_path)

                def make_internal_runtime_event(
                    event_type: str,
                    *,
                    source: str,
                    context: Mapping[str, object] | None = None,
                    session: str | None = None,
                    error: Mapping[str, object] | None = None,
                ) -> RuntimeEvent:
                    clock = getattr(runtime_event_bus, "clock", None)
                    try:
                        timestamp = float(clock() if callable(clock) else time.time())
                    except Exception:
                        timestamp = time.time()
                    return RuntimeEvent(
                        type=event_type,
                        source=source,
                        timestamp=timestamp,
                        session=session,
                        context=dict(context or {}),
                        error=dict(error) if error is not None else None,
                    )

                def handle_session_file_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_snapshot()

                def handle_settings_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_snapshot(force_fast=True)

                def handle_settings_command_received(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    action = ""
                    context = getattr(event, "context", None)
                    if isinstance(context, Mapping):
                        action = str(context.get("action") or "").strip()
                    if action in {"checkUpdate", "installUpdate", "updateAction"}:
                        request.request_domains("settings", force_fast=True)
                        return
                    if action in {
                        "restReminderAck",
                        "restReminderPostpone",
                        "restReminderStart",
                        "restReminderFinish",
                        "restReminderTestNotification",
                    }:
                        request.request_domains("settings", force_fast=True)
                        return
                    if action in {"installDesktopOverlay", "enableDesktopOverlay"}:
                        request.request_domains("settings", "overlay", force_fast=True)
                        return
                    if action in SESSION_CLEANUP_COMMANDS:
                        request.request_domains(
                            "settings", "sessionCleanup", force_fast=True
                        )
                        return
                    if action == "usageInsightsRefresh":
                        request.request_domains(
                            "settings", "usageInsights", force_fast=True
                        )
                        return
                    if action == "openUsageInsightsSession":
                        request.request_domains("settings", force_fast=True)
                        return
                    if action == "openBackgroundUsageFromInsights":
                        request.request_background_usage()
                        request.request_domains("backgroundUsage", force_fast=True)
                        return
                    if action == "dismissWarningsToday":
                        request.request_domains(
                            "currentSession",
                            "settings",
                            force_fast=True,
                        )
                        return
                    request.request_snapshot(force_fast=True)

                def handle_overlay_command_received(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    context_value = getattr(event, "context", None)
                    action = (
                        str(context_value.get("action") or "").strip()
                        if isinstance(context_value, Mapping)
                        else ""
                    )
                    if action in {
                        "dismissBackgroundUsage",
                        "openBackgroundUsage",
                    }:
                        request.request_background_usage()
                        return
                    if action in {
                        "restReminderAck",
                        "restReminderPostpone",
                        "restReminderStart",
                        "restReminderFinish",
                    }:
                        request.request_domains("settings", force_fast=True)
                        return
                    request.request_snapshot(force_fast=True)

                def handle_background_usage_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    _refresh_usage_insights_payload(context)
                    request.request_background_usage()
                    request.request_domains(
                        "backgroundUsage",
                        "usageInsights",
                        force_fast=True,
                    )

                def handle_usage_insights_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_domains(
                        "settings",
                        "usageInsights",
                        force_fast=True,
                    )

                def handle_session_cleanup_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_domains(
                        "settings",
                        "sessionCleanup",
                        force_fast=True,
                    )

                def handle_runtime_error(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_diagnostics()

                def handle_active_session_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_active_session()

                def handle_session_snapshot_hydrated(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_snapshot(force_fast=True)

                def handle_budget_window_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_snapshot(force_fast=True)

                def handle_renderer_layout_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event, request
                    # Layout changes are already handled in the renderer. They
                    # wake the loop so overlay keepalive/commands are not
                    # starved, but do not invalidate the Python snapshot.
                    return

                def handle_update_state_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_domains("settings", force_fast=True)

                def handle_rest_reminder_due(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_domains("settings", force_fast=True)

                def handle_renderer_theme_changed(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    context = getattr(event, "context", None)
                    theme = context.get("theme") if isinstance(context, Mapping) else None
                    if isinstance(theme, Mapping) and theme:
                        request.theme_payload = dict(theme)
                        request.request_domains("settings", force_fast=True)

                def handle_active_work_refresh_requested(
                    event: object,
                    request: _RendererEventRefreshRequest,
                ) -> None:
                    del event
                    request.request_snapshot(force_fast=True)

                runtime_event_handlers = {
                    "active_session_changed": handle_active_session_changed,
                    "active_work_refresh_requested": handle_active_work_refresh_requested,
                    "budget_window_changed": handle_budget_window_changed,
                    "overlay_command_received": handle_overlay_command_received,
                    "renderer_layout_changed": handle_renderer_layout_changed,
                    "runtime_error": handle_runtime_error,
                    "session_file_changed": handle_session_file_changed,
                    "session_snapshot_hydrated": handle_session_snapshot_hydrated,
                    "settings_changed": handle_settings_changed,
                    "settings_command_received": handle_settings_command_received,
                    "update_state_changed": handle_update_state_changed,
                    "rest_reminder_due": handle_rest_reminder_due,
                    "renderer_theme_changed": handle_renderer_theme_changed,
                    "background_usage_changed": handle_background_usage_changed,
                    "usage_insights_changed": handle_usage_insights_changed,
                    "session_cleanup_changed": handle_session_cleanup_changed,
                }

                def refresh_request_for_events(
                    events: list[object],
                ) -> _RendererEventRefreshRequest:
                    request = _RendererEventRefreshRequest()
                    for event in events:
                        event_type = str(getattr(event, "type", "") or "")
                        handler = runtime_event_handlers.get(event_type)
                        if handler is None:
                            continue
                        handler(event, request)
                    return request

                def sample_tick_inputs() -> _RendererTickInputs:
                    started_at = time.monotonic()
                    active_work_result = active_work_pump.take_latest()
                    if active_work_result is not None:
                        result_seq, result_items = active_work_result
                        tracker = getattr(context, "active_session_tracker", None)
                        current_seq = int(getattr(tracker, "selection_seq", 0) or 0)
                        latest_seq = int(
                            getattr(loop_state.latest_snapshot, "selection_seq", 0)
                            or 0
                        )
                        if (
                            loop_state.latest_snapshot is not None
                            and result_seq == current_seq
                            and result_seq == latest_seq
                        ):
                            stable_items = _stabilize_published_work_overlay_items(
                                context,
                                result_items,
                            )
                            loop_state.latest_snapshot.active_work_items = list(
                                stable_items
                            )
                            work_overlay.update(
                                _work_overlay_items_with_background_usage(
                                    context,
                                    stable_items,
                                )
                            )
                        else:
                            _LOGGER.info(
                                "renderer_active_work_discarded result_seq=%s current_seq=%s latest_seq=%s",
                                result_seq,
                                current_seq,
                                latest_seq,
                            )
                    update_state_value = update_manager.tick().to_dict()
                    update_state_signature = _json_signature(update_state_value)
                    rest_reminder = getattr(context, "rest_reminder", None)
                    rest_reminder_payload = None
                    if rest_reminder is not None:
                        try:
                            rest_reminder_payload = rest_reminder.tick()
                        except Exception:
                            _LOGGER.debug("rest_reminder_tick_failed", exc_info=True)
                            rest_reminder_payload = None
                        try:
                            work_overlay.update_rest_reminder(
                                rest_reminder.desktop_bubble_payload()
                            )
                        except Exception:
                            _LOGGER.debug(
                                "rest_reminder_desktop_bubble_update_failed",
                                exc_info=True,
                            )
                    if rest_reminder_payload is not None:
                        # Renderer fallback toast needs a settings-domain push.
                        if callable(runtime_event_publish):
                            runtime_event_publish(
                                "rest_reminder_due",
                                source="rest_reminder",
                                session=current_event_session(),
                                context=dict(rest_reminder_payload),
                            )
                    if loop_state.latest_update_state_signature is None:
                        loop_state.latest_update_state_signature = update_state_signature
                        loop_state.latest_update_state = dict(update_state_value)
                    elif update_state_signature != loop_state.latest_update_state_signature:
                        previous_update_state = dict(loop_state.latest_update_state or {})
                        if callable(runtime_event_publish):
                            runtime_event_publish(
                                "update_state_changed",
                                source="update_manager",
                                session=current_event_session(),
                                context={
                                    "previous": previous_update_state,
                                    "current": dict(update_state_value),
                                },
                            )
                        loop_state.latest_update_state_signature = update_state_signature
                        loop_state.latest_update_state = dict(update_state_value)
                    current_window_keys = _renderer_budget_window_keys(context)
                    if (
                        loop_state.latest_budget_window_keys is not None
                        and current_window_keys != loop_state.latest_budget_window_keys
                        and callable(runtime_event_publish)
                    ):
                        previous_day, previous_week = loop_state.latest_budget_window_keys
                        current_day, current_week = current_window_keys
                        runtime_event_publish(
                            "budget_window_changed",
                            source="budget_window",
                            session=current_event_session(),
                            context={
                                "previousDay": previous_day,
                                "previousWeek": previous_week,
                                "currentDay": current_day,
                                "currentWeek": current_week,
                            },
                        )
                    loop_state.latest_budget_window_keys = current_window_keys
                    bridge_wake = command_refresh_requested.is_set()
                    if bridge_wake:
                        command_refresh_requested.clear()
                    active_session_wake = active_session_refresh_requested.is_set()
                    if active_session_wake:
                        active_session_refresh_requested.clear()
                    reasons, paths = file_events.take_changes()
                    session_map_changed = "session-map" in reasons
                    if session_map_changed:
                        _invalidate_active_session_mapping_cache(context)
                        rematerialize = getattr(
                            getattr(context, "active_session_tracker", None),
                            "rematerialize_renderer_mapping",
                            None,
                        )
                        if callable(rematerialize):
                            try:
                                if rematerialize(force=True):
                                    loop_state.activity_wake_pending = ""
                                    command_refresh_requested.set()
                            except Exception as exc:
                                _LOGGER.debug(
                                    "renderer_mapping_rematerialize_failed error=%s",
                                    exc,
                                )
                    pending_command = take_renderer_bridge_command()
                    active_work_refresh_due = _renderer_deferred_active_work_refresh_due(
                        pending=loop_state.active_work_refresh_pending,
                        not_before=loop_state.active_work_refresh_not_before,
                        now_monotonic=time.monotonic(),
                    )
                    if active_work_refresh_due and callable(runtime_event_publish):
                        runtime_event_publish(
                            "active_work_refresh_requested",
                            source="renderer_loop",
                            session=current_event_session(),
                            context={"reason": "pending_after_active_session_refresh"},
                        )
                    events = runtime_event_drain() if callable(runtime_event_drain) else []
                    local_events = list(events)
                    event_types = {
                        str(getattr(event, "type", "") or "") for event in local_events
                    }
                    paths_payload = sorted(_session_path_key(path) for path in paths)
                    if session_map_changed:
                        # A renderer id can arrive before Codex commits its
                        # exact state-db row.  Re-run only the active-session
                        # selection after this mapping event; do not title-map
                        # or scan the session tree.
                        local_events.append(
                            make_internal_runtime_event(
                                "active_session_changed",
                                source="session_map",
                                session=current_event_session(),
                                context={
                                    "reason": "exact_renderer_mapping_available",
                                    "paths": paths_payload,
                                },
                            )
                        )
                        # Pure new-session has no UUID yet; force a DOM re-report
                        # so a just-persisted sidebar identity can clear the latch.
                        loop_state.activity_wake_pending = "session-map"
                    if (
                        reasons.intersection({"session", "sessions-root"})
                        and "session_file_changed" not in event_types
                    ):
                        session = (
                            _session_path_key(sorted(paths, key=_session_path_key)[0])
                            if paths
                            else current_event_session()
                        )
                        local_events.append(
                            make_internal_runtime_event(
                                "session_file_changed",
                                source="file_watcher",
                                session=session,
                                context={
                                    "reasons": sorted(reasons),
                                    "paths": paths_payload,
                                },
                            )
                        )
                        # Session JSONL activity while follow is stuck should force
                        # a DOM re-report (wake only — never path fallback).
                        if not loop_state.activity_wake_pending:
                            loop_state.activity_wake_pending = "session-file"
                    if "settings" in reasons and "settings_changed" not in event_types:
                        local_events.append(
                            make_internal_runtime_event(
                                "settings_changed",
                                source="file_watcher",
                                session=current_event_session(),
                                context={
                                    "reasons": sorted(reasons),
                                    "paths": paths_payload,
                                },
                            )
                        )
                    if active_session_wake and "active_session_changed" not in event_types:
                        local_events.append(
                            make_internal_runtime_event(
                                "active_session_changed",
                                source="renderer_loop",
                                session=current_event_session(),
                                context={"reason": "active_session_wakeup"},
                            )
                        )
                    event_refresh_request = refresh_request_for_events(local_events)
                    if _has_pending_background_usage_response(
                        loop_state.settings_command_status
                    ) and (
                        loop_state.background_usage_response_retry_attempts > 0
                        and time.monotonic()
                        >= loop_state.background_usage_response_retry_not_before
                    ):
                        event_refresh_request.request_domains(
                            "backgroundUsage",
                            force_fast=True,
                        )
                    return _RendererTickInputs(
                        started=started_at,
                        update_state=update_state_value,
                        bridge_wakeup=bridge_wake,
                        active_session_wakeup=active_session_wake,
                        file_change_reasons=reasons,
                        file_change_paths=paths,
                        command=pending_command,
                        budget_window_keys=current_window_keys,
                        runtime_events=list(local_events),
                        event_refresh_request=event_refresh_request,
                    )

                def apply_settings_command(inputs: _RendererTickInputs) -> None:
                    if not inputs.command:
                        return
                    previous_config = context.user_config
                    if str(inputs.command.get("action") or "").strip() in {
                        "openBackgroundUsage",
                        "openBackgroundUsageFromInsights",
                        "backgroundUsageQuery",
                        "backgroundUsageDetail",
                    }:
                        reset_background_usage_response_retry()
                    loop_state.settings_command_status = _handle_renderer_settings_command(
                        inputs.command,
                        context,
                        restart_requested,
                        exit_requested,
                        update_manager,
                        work_overlay,
                        session_controller,
                    )
                    inputs.update_state = update_manager.status().to_dict()
                    mode_switch = str(
                        loop_state.settings_command_status.get("switchMode") or ""
                    ).strip()
                    if mode_switch and mode_switch != "renderer":
                        _LOGGER.info(
                            "renderer_hud_legacy_switch_ignored mode=%s",
                            mode_switch,
                        )
                        loop_state.settings_command_status = _renderer_settings_status(
                            "Renderer-only 版本不再切换到 Qt/Tk。",
                        )
                    partial_domains = _partial_domains_for_settings_command(
                        inputs.command,
                        previous_config=previous_config,
                        current_config=context.user_config,
                    )
                    if (
                        partial_domains
                        and inputs.event_refresh_request.snapshot
                        and not inputs.file_change_reasons
                        and not inputs.active_session_wakeup
                        and not inputs.event_refresh_request.active_session
                        and not inputs.event_refresh_request.diagnostics
                    ):
                        if loop_state.latest_snapshot is not None:
                            _refresh_latest_snapshot_for_partial_settings_command(
                                inputs.command,
                                snapshot=loop_state.latest_snapshot,
                                context=context,
                                previous_config=previous_config,
                                current_config=context.user_config,
                            )
                        inputs.event_refresh_request.snapshot = False
                        inputs.event_refresh_request.request_domains(
                            *sorted(partial_domains),
                            force_fast=True,
                        )

                def apply_background_usage_change(
                    inputs: _RendererTickInputs,
                ) -> None:
                    if not inputs.event_refresh_request.background_usage:
                        return
                    session_items = (
                        list(loop_state.latest_snapshot.active_work_items)
                        if loop_state.latest_snapshot is not None
                        else []
                    )
                    work_overlay.configure(
                        item_limit=_work_overlay_item_limit_for_context(context),
                    )
                    work_overlay.update(
                        _work_overlay_items_with_background_usage(
                            context,
                            session_items,
                        )
                    )
                    # Background usage activity is a useful wake signal when the
                    # current follow latch is stuck without a UUID yet.
                    if not loop_state.activity_wake_pending:
                        loop_state.activity_wake_pending = "background-usage"

                def apply_partial_settings_file_change(
                    inputs: _RendererTickInputs,
                ) -> None:
                    event_types = {
                        str(getattr(event, "type", "") or "")
                        for event in inputs.runtime_events
                    }
                    if (
                        loop_state.latest_snapshot is None
                        or inputs.command
                        or not inputs.event_refresh_request.snapshot
                        or inputs.active_session_wakeup
                        or inputs.event_refresh_request.active_session
                        or inputs.event_refresh_request.diagnostics
                    ):
                        return
                    if inputs.file_change_reasons and inputs.file_change_reasons != {"settings"}:
                        return
                    if event_types - {"settings_changed"}:
                        return
                    settings_store = getattr(context, "settings_store", None)
                    load = getattr(settings_store, "load", None)
                    mtime_fn = getattr(settings_store, "mtime", None)
                    if not callable(load):
                        return
                    previous_config = context.user_config
                    next_config = load()
                    mtime = mtime_fn() if callable(mtime_fn) else None
                    _apply_user_config_to_runtime_context(
                        context,
                        next_config,
                        mtime=mtime,
                    )
                    changed_keys = _changed_user_config_keys(
                        previous_config,
                        next_config,
                    )
                    partial_domains = _partial_domains_for_changed_user_config(
                        changed_keys,
                    )
                    if partial_domains is None:
                        return
                    _refresh_latest_snapshot_for_partial_settings_command(
                        {"action": "save"},
                        snapshot=loop_state.latest_snapshot,
                        context=context,
                        previous_config=previous_config,
                        current_config=next_config,
                    )
                    inputs.event_refresh_request.snapshot = False
                    inputs.event_refresh_request.request_domains(
                        *sorted(partial_domains),
                        force_fast=True,
                    )

                def compute_force_fast_refresh(inputs: _RendererTickInputs) -> bool:
                    return bool(
                        loop_state.latest_snapshot is None
                        or inputs.event_refresh_request.force_fast
                    )

                def apply_refresh(
                    inputs: _RendererTickInputs, *, force_fast: bool
                ) -> ParsedSession:
                    refresh_started = time.perf_counter()
                    latest = loop_state.latest_snapshot
                    budget_signature = _renderer_budget_signature(context)
                    refresh_budget_aggregate = _renderer_should_refresh_budget_aggregate(
                        latest_snapshot=latest,
                        latest_budget_signature=loop_state.latest_budget_signature,
                        budget_signature=budget_signature,
                        file_change_reasons=inputs.file_change_reasons,
                        file_change_paths=inputs.file_change_paths,
                    )
                    refresh_budget_paths = ()
                    if refresh_budget_aggregate is False:
                        refresh_budget_paths = _renderer_budget_refresh_paths(
                            inputs.file_change_paths
                        )
                    refresh_active_work_items = _renderer_should_refresh_active_work_items(
                        latest_snapshot=latest,
                        latest_active_work_refresh_at=loop_state.latest_active_work_refresh_at,
                        now_monotonic=time.monotonic(),
                        active_work_refresh_pending=(
                            loop_state.active_work_refresh_pending
                            and _renderer_deferred_active_work_refresh_due(
                                pending=True,
                                not_before=loop_state.active_work_refresh_not_before,
                                now_monotonic=time.monotonic(),
                            )
                        ),
                        file_change_reasons=inputs.file_change_reasons,
                        file_change_paths=inputs.file_change_paths,
                    )
                    lightweight_active_session_refresh = (
                        _renderer_should_use_visible_first_active_session(
                            active_session_requested=bool(
                            inputs.active_session_wakeup
                            or inputs.event_refresh_request.active_session
                            ),
                            latest_snapshot=latest,
                            has_command=bool(inputs.command),
                            has_settings_command_status=bool(
                                loop_state.settings_command_status
                            ),
                            update_phase=str(inputs.update_state.get("phase") or ""),
                        )
                    )
                    if lightweight_active_session_refresh:
                        # The selected session is latency-critical. Reuse the
                        # existing budget/work data even when unrelated session
                        # writes were coalesced into this tick. Rebuild them in a
                        # separate event after the visible sessionSwitch payload.
                        refresh_budget_aggregate = False
                        refresh_budget_paths = ()
                        refresh_active_work_items = False
                    elif (
                        loop_state.active_work_refresh_pending
                        and not _renderer_deferred_active_work_refresh_due(
                            pending=True,
                            not_before=loop_state.active_work_refresh_not_before,
                            now_monotonic=time.monotonic(),
                        )
                    ):
                        refresh_active_work_items = False
                    hydrated_session_refresh = any(
                        str(getattr(event, "type", "") or "")
                        == "session_snapshot_hydrated"
                        for event in inputs.runtime_events
                    )
                    del force_fast  # already folded into signature/inputs decisions
                    snapshot_kwargs: dict[str, object] = {
                        "refresh_budget_aggregate": refresh_budget_aggregate,
                        "refresh_budget_paths": refresh_budget_paths,
                        # 会话切换先发送轻量 session payload；结构化工作状态在
                        # 随后的独立事件中重建，避免 16 文件扫描阻塞可见切换。
                        "refresh_active_work_items": bool(
                            refresh_active_work_items and latest is None
                        ),
                    }
                    if lightweight_active_session_refresh:
                        snapshot_kwargs["reuse_budget_from"] = latest
                        snapshot_kwargs["refresh_visible_app_error"] = False
                    if lightweight_active_session_refresh or hydrated_session_refresh:
                        snapshot_kwargs["refresh_current_session_usage"] = False
                    snapshot_started = time.perf_counter()
                    snapshot_started_at_ms = int(time.time() * 1000)
                    fresh = snapshot_or_error(**snapshot_kwargs)
                    snapshot_built_at_ms = int(time.time() * 1000)
                    fresh.follow_timing = {
                        **dict(fresh.follow_timing or {}),
                        "snapshotStartedAt": snapshot_started_at_ms,
                        "snapshotBuiltAt": snapshot_built_at_ms,
                    }
                    snapshot_ms = (time.perf_counter() - snapshot_started) * 1000.0
                    tracker = getattr(context, "active_session_tracker", None)
                    if latest is not None and _renderer_snapshot_selection_is_stale(
                        fresh,
                        tracker,
                    ):
                        _LOGGER.info(
                            "renderer_refresh_discarded reason=stale_selection fresh_seq=%s current_seq=%s",
                            fresh.selection_seq,
                            int(getattr(tracker, "selection_seq", 0) or 0),
                        )
                        return latest
                    background_active_work_refresh = bool(
                        refresh_active_work_items and latest is not None
                    )
                    if latest is not None:
                        fresh.active_work_items = list(latest.active_work_items)
                        if not lightweight_active_session_refresh:
                            fresh.active_work_items = _refresh_visible_current_work_item(
                                context,
                                fresh.active_work_items,
                                fresh,
                            )
                    if background_active_work_refresh:
                        active_work_pump.request(fresh, fresh.session_path)
                        loop_state.latest_active_work_refresh_at = time.monotonic()
                        loop_state.active_work_refresh_pending = False
                        loop_state.active_work_refresh_not_before = 0.0
                    elif refresh_active_work_items:
                        loop_state.latest_active_work_refresh_at = time.monotonic()
                        loop_state.active_work_refresh_pending = False
                        loop_state.active_work_refresh_not_before = 0.0
                    _update_session_cleanup_activity(context, fresh)
                    loop_state.latest_snapshot = fresh
                    loop_state.latest_budget_signature = _renderer_budget_signature(context)
                    fresh.follow_timing["payloadSendStartedAt"] = int(
                        time.time() * 1000
                    )
                    update_started = time.perf_counter()
                    if lightweight_active_session_refresh:
                        update_payload = getattr(client, "update_payload", None)
                        update_ok = bool(
                            callable(update_payload)
                            and update_payload(
                                session_switch_payload_from_snapshot(
                                    fresh,
                                    settings_path=context.settings_store.path,
                                    background_usage_notification=(
                                        _background_usage_notification_for_session(
                                            context,
                                            fresh.session_id,
                                        )
                                    ),
                                    connection_health=connection_health,
                                )
                            )
                        )
                    else:
                        update_ok = client.update(
                            fresh,
                            settings=context.user_config,
                            active_display_mode="renderer",
                            settings_path=context.settings_store.path,
                            settings_bridge_url=bridge_url,
                            background_usage_bridge_url=background_usage_bridge_url,
                            background_usage_revision=(
                                background_usage_runtime.store.revision()
                                if background_usage_runtime is not None
                                else 0
                            ),
                            background_usage_notification=(
                                _background_usage_notification_for_session(
                                    context,
                                    fresh.session_id,
                                )
                            ),
                            rest_reminder=(
                                getattr(context, "rest_reminder", None).renderer_payload()
                                if getattr(context, "rest_reminder", None) is not None
                                else {"visible": False}
                            ),
                            settings_command_status=loop_state.settings_command_status,
                            update_state=inputs.update_state,
                            debug=_runtime_debug_enabled(),
                            runtime_errors=_runtime_errors_payload_for_context(context),
                            work_overlay_selectable_max=_work_overlay_screen_max_items(),
                            desktop_overlay_dependency=_desktop_overlay_dependency_status(),
                            provider_registry=_provider_registry_payload(context),
                            app_provider=str(getattr(context, "app_provider", "") or ""),
                            usage_insights=dict(
                                getattr(context, "usage_insights_payload", {}) or {}
                            ),
                            session_cleanup=dict(
                                getattr(context, "session_cleanup_payload", {}) or {}
                            ),
                            connection_health=connection_health,
                        )
                    update_ms = (time.perf_counter() - update_started) * 1000.0
                    if not lightweight_active_session_refresh:
                        stable_items = _stabilize_published_work_overlay_items(
                            context,
                            fresh.active_work_items,
                        )
                        fresh.active_work_items = list(stable_items)
                        work_overlay.configure(
                            item_limit=_work_overlay_item_limit_for_context(context),
                        )
                        work_overlay.update(
                            _work_overlay_items_with_background_usage(
                                context,
                                stable_items,
                            )
                        )
                        file_events.update_session_path(fresh.session_path)
                    refresh_ms = (time.perf_counter() - refresh_started) * 1000.0
                    update_metrics = dict(
                        getattr(client, "last_update_metrics", {}) or {}
                    )
                    if refresh_ms >= RENDERER_SLOW_OPERATION_LOG_MS:
                        attribution = (
                            "python_snapshot"
                            if snapshot_ms >= update_ms
                            else str(
                                update_metrics.get("attribution")
                                or "hud_or_cdp"
                            )
                        )
                        _LOGGER.info(
                            "renderer_refresh_timing attribution=%s total_ms=%.1f snapshot_ms=%.1f hud_update_ms=%.1f target_ms=%s transport=%s cdp_ms=%s persistent_ms=%s fallback_ms=%s fallback_reason=%s renderer_apply_ms=%s source=%s",
                            attribution,
                            refresh_ms,
                            snapshot_ms,
                            update_ms,
                            update_metrics.get("targetDiscoveryMs", "-"),
                            update_metrics.get("transport", "-"),
                            update_metrics.get("cdpMs", "-"),
                            update_metrics.get("persistentMs", "-"),
                            update_metrics.get("fallbackMs", "-"),
                            update_metrics.get("persistentFallbackReason", "-") or "-",
                            update_metrics.get("rendererApplyMs", "-"),
                            fresh.selection_source,
                        )
                    if update_ok:
                        connection_health.note_success("update-ok")
                        sync_connection_follow(fresh)
                        if lightweight_active_session_refresh:
                            loop_state.active_work_refresh_pending = True
                            loop_state.active_work_refresh_not_before = (
                                time.monotonic()
                                + RENDERER_ACTIVE_WORK_AFTER_SESSION_DELAY_SECONDS
                            )
                            command_refresh_requested.set()
                        if _has_pending_background_usage_response(
                            loop_state.settings_command_status
                        ):
                            reset_background_usage_response_retry()
                        loop_state.settings_command_status = {}
                        loop_state.failures = 0
                        _resolve_cdp_update_failure(context)
                    else:
                        loop_state.failures += 1
                        connection_health.note_failure("update-failed")
                        schedule_background_usage_response_retry()
                        _record_cdp_update_failure(
                            context,
                            client,
                            failures=loop_state.failures,
                        )
                        _LOGGER.info(
                            "renderer_hud_update_failed failures=%s status=%s error=%s",
                            loop_state.failures,
                            client.last_status,
                            client.last_error,
                        )
                    return fresh

                def apply_domain_update(inputs: _RendererTickInputs) -> bool:
                    snapshot = loop_state.latest_snapshot
                    if snapshot is None or not inputs.event_refresh_request.domains:
                        return True
                    payload = payload_from_snapshot(
                        snapshot,
                        settings=context.user_config,
                        active_display_mode="renderer",
                        settings_path=context.settings_store.path,
                        settings_bridge_url=bridge_url,
                        background_usage_bridge_url=background_usage_bridge_url,
                        background_usage_revision=(
                            background_usage_runtime.store.revision()
                            if background_usage_runtime is not None
                            else 0
                        ),
                        background_usage_notification=(
                            _background_usage_notification_for_session(
                                context,
                                snapshot.session_id,
                            )
                        ),
                        rest_reminder=(
                            getattr(context, "rest_reminder", None).renderer_payload()
                            if getattr(context, "rest_reminder", None) is not None
                            else {"visible": False}
                        ),
                        settings_command_status=loop_state.settings_command_status,
                        theme=inputs.event_refresh_request.theme_payload,
                        update_state=inputs.update_state,
                        debug=_runtime_debug_enabled(),
                        runtime_errors=_runtime_errors_payload_for_context(context),
                        work_overlay_selectable_max=_work_overlay_screen_max_items(),
                        desktop_overlay_dependency=_desktop_overlay_dependency_status(),
                        provider_registry=_provider_registry_payload(context),
                        app_provider=str(getattr(context, "app_provider", "") or ""),
                        usage_insights=dict(
                            getattr(context, "usage_insights_payload", {}) or {}
                        ),
                        session_cleanup=dict(
                            getattr(context, "session_cleanup_payload", {}) or {}
                        ),
                        connection_health=connection_health,
                    ).to_domain_json(*sorted(inputs.event_refresh_request.domains))
                    if not payload:
                        return True
                    update_payload = getattr(client, "update_payload", None)
                    if not callable(update_payload):
                        return False
                    if update_payload(payload):
                        if (
                            "backgroundUsage" in inputs.event_refresh_request.domains
                            and (
                                loop_state.settings_command_status.get(
                                    "backgroundUsageOpenEventId"
                                )
                                or loop_state.settings_command_status.get(
                                    "backgroundUsageResponse"
                                )
                            )
                        ):
                            reset_background_usage_response_retry()
                            loop_state.settings_command_status = {}
                        loop_state.failures = 0
                        connection_health.note_success("update-ok")
                        sync_connection_follow(snapshot)
                        _resolve_cdp_update_failure(context)
                        return True
                    loop_state.failures += 1
                    connection_health.note_failure("update-failed")
                    schedule_background_usage_response_retry()
                    _record_cdp_update_failure(
                        context,
                        client,
                        failures=loop_state.failures,
                    )
                    _LOGGER.info(
                        "renderer_hud_domain_update_failed failures=%s status=%s error=%s domains=%s",
                        loop_state.failures,
                        getattr(client, "last_status", ""),
                        getattr(client, "last_error", ""),
                        sorted(inputs.event_refresh_request.domains),
                    )
                    return False

                def _snapshot_follow_elapsed_ms(snapshot: ParsedSession | None) -> int:
                    tracker = getattr(context, "active_session_tracker", None)
                    stuck = getattr(tracker, "follow_stuck_elapsed_ms", None)
                    if stuck is not None:
                        try:
                            value = int(stuck)
                        except (TypeError, ValueError):
                            value = 0
                        if value > 0:
                            return value
                    observed_at_ms = int(
                        getattr(snapshot, "selection_observed_at_ms", 0) or 0
                    )
                    if observed_at_ms <= 0:
                        return 0
                    return max(0, int(time.time() * 1000) - observed_at_ms)

                def sync_connection_follow(snapshot: ParsedSession | None) -> None:
                    tracker = getattr(context, "active_session_tracker", None)
                    follow_state = str(
                        getattr(snapshot, "follow_state", None)
                        or getattr(tracker, "follow_state", "")
                        or ""
                    )
                    follow_reason = str(
                        getattr(snapshot, "follow_reason", None)
                        or getattr(tracker, "follow_reason", "")
                        or ""
                    )
                    connection_health.observe_follow(
                        follow_state=follow_state,
                        follow_reason=follow_reason,
                        follow_stuck_elapsed_ms=_snapshot_follow_elapsed_ms(snapshot),
                    )

                def push_connection_health_light() -> bool:
                    """Push only the connection light without rebuilding session data.

                    Probe/heal can change health between full refreshes. A tiny
                    diagnostics-domain update keeps the bottom light honest without
                    paying for another full snapshot.
                    """
                    health_payload = connection_health.to_payload()
                    domain = {
                        "connectionHealth": health_payload,
                        "debug": _runtime_debug_enabled(),
                        "runtimeErrors": _runtime_errors_payload_for_context(context),
                    }
                    payload = {
                        "connectionHealth": health_payload,
                        "debug": domain["debug"],
                        "runtimeErrors": domain["runtimeErrors"],
                        "payloadDomains": {"diagnostics": dict(domain)},
                    }
                    update_payload = getattr(client, "update_payload", None)
                    if not callable(update_payload):
                        return False
                    try:
                        return bool(update_payload(payload))
                    except Exception as exc:
                        _LOGGER.debug(
                            "connection_health_light_push_failed error=%s",
                            exc,
                        )
                        return False

                connection_health_pushers["push"] = push_connection_health_light

                def maybe_probe_connection(snapshot: ParsedSession | None) -> None:
                    """Conditional CDP liveness probe — skip when recent traffic succeeded."""
                    sync_connection_follow(snapshot)
                    follow_state = str(getattr(snapshot, "follow_state", "") or "")
                    follow_elapsed_ms = _snapshot_follow_elapsed_ms(snapshot)
                    if not connection_health.should_probe(
                        follow_state=follow_state,
                        follow_elapsed_ms=follow_elapsed_ms,
                        update_failures=loop_state.failures,
                    ):
                        return
                    before = (
                        connection_health.state,
                        connection_health.reason,
                        connection_health.channel_available,
                    )
                    probe = getattr(client, "probe_connection", None)
                    ok = bool(callable(probe) and probe())
                    if ok:
                        connection_health.note_success("probe-ok")
                        sync_connection_follow(snapshot)
                        _LOGGER.debug("connection_heartbeat ok")
                    else:
                        connection_health.note_failure("probe-failed")
                        _LOGGER.info(
                            "connection_heartbeat failed status=%s error=%s",
                            getattr(client, "last_status", ""),
                            getattr(client, "last_error", ""),
                        )
                    after = (
                        connection_health.state,
                        connection_health.reason,
                        connection_health.channel_available,
                    )
                    if after != before:
                        push_connection_health_light()

                def maybe_heal_session_follow(snapshot: ParsedSession | None) -> bool:
                    """Session-follow self-heal ladder (no Codex restart / fresh port).

                    L1 re-report active session from DOM.
                    L2 rebind active-session CDP channel then re-report.
                    L3 soft-clear target/script cache so the next update reinstalls.

                    Success requires follow identity to advance — a report that still
                    returns new-session is heal-no-progress, not success.
                    """
                    sync_connection_follow(snapshot)
                    tracker = getattr(context, "active_session_tracker", None)
                    follow_state = str(
                        getattr(snapshot, "follow_state", None)
                        or getattr(tracker, "follow_state", "")
                        or ""
                    )
                    follow_reason = str(
                        getattr(snapshot, "follow_reason", None)
                        or getattr(tracker, "follow_reason", "")
                        or ""
                    )
                    follow_elapsed_ms = _snapshot_follow_elapsed_ms(snapshot)
                    should, heal_reason = connection_health.should_heal(
                        follow_state=follow_state,
                        follow_reason=follow_reason,
                        follow_elapsed_ms=follow_elapsed_ms,
                    )
                    if not should:
                        return False
                    before = (
                        tracker.follow_snapshot()
                        if tracker is not None and hasattr(tracker, "follow_snapshot")
                        else {
                            "followState": follow_state,
                            "followReason": follow_reason,
                            "newSession": follow_state == "new-session",
                        }
                    )
                    connection_health.note_healing(heal_reason)
                    push_connection_health_light()
                    _LOGGER.info(
                        "session_follow_heal start reason=%s follow_state=%s follow_reason=%s elapsed_ms=%s",
                        heal_reason,
                        follow_state,
                        follow_reason,
                        follow_elapsed_ms,
                    )
                    report = getattr(client, "report_active_session", None)
                    rebind = getattr(client, "rebind_active_session_channel", None)
                    rematerialize = getattr(tracker, "rematerialize_renderer_mapping", None)
                    progressed = getattr(
                        type(tracker) if tracker is not None else object,
                        "follow_progressed",
                        None,
                    )

                    def _follow_advanced() -> bool:
                        after = (
                            tracker.follow_snapshot()
                            if tracker is not None and hasattr(tracker, "follow_snapshot")
                            else {}
                        )
                        if callable(progressed):
                            return bool(progressed(before, after))
                        if tracker is not None:
                            return not bool(getattr(tracker, "renderer_new_session", False))
                        return False

                    # L0 — mapping rematerialize for pending-map (known id OR title-only).
                    # DOM re-report cannot invent a state-db row; re-query mapping first.
                    if (
                        heal_reason == "stuck-pending"
                        or follow_reason
                        in {
                            "awaiting-exact-mapping",
                            "awaiting-persistence",
                            "awaiting-canonical-id",
                        }
                    ) and callable(rematerialize):
                        try:
                            if rematerialize(force=True) and _follow_advanced():
                                connection_health.note_heal_success()
                                push_connection_health_light()
                                command_refresh_requested.set()
                                _LOGGER.info(
                                    "session_follow_heal l0_rematerialize ok reason=%s",
                                    heal_reason,
                                )
                                return True
                        except Exception as exc:
                            _LOGGER.info(
                                "session_follow_heal l0_rematerialize_failed reason=%s error=%s",
                                heal_reason,
                                exc,
                            )

                    # L1
                    if callable(report) and report(f"self-heal:{heal_reason}"):
                        if _follow_advanced():
                            connection_health.note_heal_success()
                            push_connection_health_light()
                            command_refresh_requested.set()
                            _LOGGER.info(
                                "session_follow_heal l1_report ok reason=%s",
                                heal_reason,
                            )
                            return True
                        connection_health.note_heal_no_progress("heal-no-progress")
                        push_connection_health_light()
                        _LOGGER.info(
                            "session_follow_heal l1_no_progress reason=%s before=%s after=%s",
                            heal_reason,
                            before,
                            tracker.follow_snapshot()
                            if tracker is not None and hasattr(tracker, "follow_snapshot")
                            else {},
                        )
                    # L2
                    if callable(rebind) and rebind():
                        if callable(report) and report(
                            f"self-heal-rebind:{heal_reason}"
                        ):
                            if _follow_advanced():
                                connection_health.note_heal_success()
                                push_connection_health_light()
                                command_refresh_requested.set()
                                _LOGGER.info(
                                    "session_follow_heal l2_rebind ok reason=%s",
                                    heal_reason,
                                )
                                return True
                            connection_health.note_heal_no_progress("heal-no-progress")
                            push_connection_health_light()
                            _LOGGER.info(
                                "session_follow_heal l2_no_progress reason=%s",
                                heal_reason,
                            )
                    # L3
                    clear_cache = getattr(client, "_clear_target_cache", None)
                    if callable(clear_cache):
                        try:
                            clear_cache(clear_script=True)
                            loop_state.soft_reinstall_pending = True
                            connection_health.note_failure("heal-failed")
                            push_connection_health_light()
                            command_refresh_requested.set()
                            _LOGGER.info(
                                "session_follow_heal l3_soft_reinstall scheduled reason=%s",
                                heal_reason,
                            )
                            return True
                        except Exception as exc:
                            _LOGGER.info(
                                "session_follow_heal l3_failed reason=%s error=%s",
                                heal_reason,
                                exc,
                            )
                    connection_health.note_failure("heal-failed")
                    push_connection_health_light()
                    return False

                def maybe_activity_wake_session_follow(
                    snapshot: ParsedSession | None,
                    *,
                    reason: str,
                ) -> bool:
                    """Force a DOM re-report / mapping rematerialize while follow is stuck."""
                    tracker = getattr(context, "active_session_tracker", None)
                    follow_state = str(
                        getattr(snapshot, "follow_state", None)
                        or getattr(tracker, "follow_state", "")
                        or ""
                    )
                    follow_reason = str(
                        getattr(snapshot, "follow_reason", None)
                        or getattr(tracker, "follow_reason", "")
                        or ""
                    )
                    if follow_state not in {"new-session", "pending"}:
                        return False
                    before = (
                        tracker.follow_snapshot()
                        if tracker is not None and hasattr(tracker, "follow_snapshot")
                        else {"followState": follow_state}
                    )
                    progressed = getattr(
                        type(tracker) if tracker is not None else object,
                        "follow_progressed",
                        None,
                    )

                    def _advanced(after: dict[str, object]) -> bool:
                        if callable(progressed):
                            return bool(progressed(before, after))
                        return False

                    # Known canonical id waiting on mapping: rematerialize first.
                    if follow_reason in {
                        "awaiting-exact-mapping",
                        "awaiting-persistence",
                        "awaiting-canonical-id",
                    } or str(reason).startswith("session-map"):
                        rematerialize = getattr(
                            tracker, "rematerialize_renderer_mapping", None
                        )
                        if callable(rematerialize):
                            try:
                                if rematerialize(force=True):
                                    after = (
                                        tracker.follow_snapshot()
                                        if tracker is not None
                                        and hasattr(tracker, "follow_snapshot")
                                        else {}
                                    )
                                    if _advanced(after) or str(
                                        after.get("followState") or ""
                                    ) == "confirmed":
                                        connection_health.note_heal_success()
                                        push_connection_health_light()
                                        command_refresh_requested.set()
                                        _LOGGER.info(
                                            "session_follow_activity_wake rematerialize ok reason=%s",
                                            reason,
                                        )
                                        return True
                            except Exception as exc:
                                _LOGGER.debug(
                                    "session_follow_activity_wake rematerialize_failed reason=%s error=%s",
                                    reason,
                                    exc,
                                )

                    report = getattr(client, "report_active_session", None)
                    if not callable(report):
                        return False
                    ok = bool(report(f"activity-wake:{reason}"))
                    after = (
                        tracker.follow_snapshot()
                        if tracker is not None and hasattr(tracker, "follow_snapshot")
                        else {}
                    )
                    advanced = _advanced(after)
                    _LOGGER.info(
                        "session_follow_activity_wake reason=%s ok=%s advanced=%s follow_state=%s",
                        reason,
                        ok,
                        advanced,
                        getattr(tracker, "follow_state", follow_state)
                        if tracker is not None
                        else follow_state,
                    )
                    if advanced:
                        connection_health.note_heal_success()
                        push_connection_health_light()
                        command_refresh_requested.set()
                        return True
                    sync_connection_follow(snapshot)
                    if connection_health.state != "ok":
                        push_connection_health_light()
                    return False

                def compute_wait_delay(
                    snapshot: ParsedSession,
                    inputs: _RendererTickInputs,
                    *,
                    force_fast: bool,
                ) -> float:
                    elapsed_wall = time.monotonic() - inputs.started
                    delay_value = _renderer_refresh_delay_seconds(
                        context,
                        snapshot,
                        elapsed_wall,
                        force_fast=force_fast,
                    )
                    if _renderer_event_idle_wait_enabled(
                        file_events,
                        snapshot,
                        inputs.update_state,
                        delay_value,
                        force_fast=force_fast,
                    ):
                        delay_value = max(delay_value, RENDERER_EVENT_IDLE_WAIT_SECONDS)
                    rest_reminder = getattr(context, "rest_reminder", None)
                    if rest_reminder is not None:
                        seconds_until_wake = getattr(
                            rest_reminder,
                            "seconds_until_wake",
                            lambda: None,
                        )()
                        if seconds_until_wake is not None:
                            delay_value = min(
                                delay_value,
                                max(0.05, float(seconds_until_wake)),
                            )
                    next_keep_alive = getattr(
                        work_overlay,
                        "next_keep_alive_seconds",
                        lambda: None,
                    )()
                    if next_keep_alive is not None:
                        delay_value = min(delay_value, max(0.1, float(next_keep_alive)))
                    if daemon_manager is not None:
                        delay_value = min(
                            delay_value,
                            max(0.1, loop_state.next_daemon_check_at - time.monotonic()),
                        )
                    if loop_state.failures >= _renderer_update_failure_limit(
                        display_mode,
                        client.last_error,
                    ):
                        delay_value = max(
                            delay_value, min(5.0, loop_state.failures * 0.5)
                        )
                    if loop_state.active_work_refresh_pending:
                        delay_value = min(
                            delay_value,
                            max(
                                0.05,
                                loop_state.active_work_refresh_not_before
                                - time.monotonic(),
                            ),
                        )
                    if _has_pending_background_usage_response(
                        loop_state.settings_command_status
                    ) and loop_state.background_usage_response_retry_attempts > 0:
                        delay_value = min(
                            delay_value,
                            max(
                                0.05,
                                loop_state.background_usage_response_retry_not_before
                                - time.monotonic(),
                            ),
                        )
                    probe_in = connection_health.seconds_until_probe()
                    if probe_in is not None:
                        delay_value = min(delay_value, max(0.05, float(probe_in)))
                    heal_in = connection_health.seconds_until_heal()
                    if heal_in is not None and heal_in > 0:
                        delay_value = min(delay_value, max(0.05, float(heal_in)))
                    return delay_value

                while True:
                    if (
                        daemon_manager is not None
                        and time.monotonic() >= loop_state.next_daemon_check_at
                    ):
                        try:
                            if not daemon_manager.codex_is_running():
                                _LOGGER.info("daemon_codex_exited")
                                return DAEMON_RESTART_REQUESTED
                            loop_state.next_daemon_check_at = (
                                time.monotonic() + daemon_manager.poll_seconds
                            )
                        except ProcessListenerError as exc:
                            _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                            return RENDERER_HUD_UNAVAILABLE
                    tick = sample_tick_inputs()
                    apply_settings_command(tick)
                    apply_background_usage_change(tick)
                    apply_partial_settings_file_change(tick)
                    if exit_requested.is_set():
                        _LOGGER.info("renderer_hud_exit_requested")
                        return 0
                    if restart_requested.is_set():
                        _LOGGER.info("renderer_hud_restart_requested")
                        return (
                            DAEMON_RESTART_REQUESTED
                            if daemon_manager is not None
                            else 0
                        )
                    force_fast = compute_force_fast_refresh(tick)
                    snapshot_requested = (
                        tick.event_refresh_request.snapshot
                        or loop_state.latest_snapshot is None
                        or loop_state.soft_reinstall_pending
                    )
                    if snapshot_requested:
                        snapshot = apply_refresh(tick, force_fast=force_fast)
                        if loop_state.soft_reinstall_pending and loop_state.failures == 0:
                            # L3 soft reinstall completed once a full update path ran
                            # against a cleared target/script cache.
                            loop_state.soft_reinstall_pending = False
                    else:
                        snapshot = loop_state.latest_snapshot
                        apply_domain_update(tick)
                        keep_alive = getattr(work_overlay, "keep_alive", None)
                        if callable(keep_alive):
                            keep_alive()
                    wake_reason = str(loop_state.activity_wake_pending or "")
                    if wake_reason:
                        loop_state.activity_wake_pending = ""
                        maybe_activity_wake_session_follow(
                            snapshot,
                            reason=wake_reason,
                        )
                    maybe_heal_session_follow(snapshot)
                    maybe_probe_connection(snapshot)
                    delay = compute_wait_delay(snapshot, tick, force_fast=force_fast)
                    command_refresh_requested.wait(delay)
            except KeyboardInterrupt:
                if local_loading is not None:
                    local_loading.close()
                return 130
            finally:
                if exit_requested.is_set():
                    try:
                        remove_renderer_hud_from_pages(port=startup_plan.port)
                    except Exception:
                        _LOGGER.debug(
                            "renderer_hud_exit_cleanup_failed", exc_info=True
                        )
                if callable(runtime_event_unsubscribe):
                    runtime_event_unsubscribe()
                if callable(tracker_change_callback):
                    tracker_change_callback(None)
                if "active_work_pump" in locals():
                    active_work_pump.close()
                client.close()
                bridge.close()
                if command_pump is not None:
                    command_pump.close()
                if "file_events" in locals():
                    file_events.close()
                work_overlay.close()
                update_manager.close()
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def _legacy_hud_session_unavailable(surface: str) -> int:
    _LOGGER.info("legacy_hud_session_unavailable surface=%s renderer_only=true", surface)
    return RENDERER_HUD_UNAVAILABLE


def _run_tk_window_session(
    context: RuntimeContext,
    args: argparse.Namespace,
    *,
    daemon_manager: CodexDaemonManager | None = None,
    existing_window: Any | None = None,
    close_context: bool = True,
    update_manager: AutoUpdateManager | None = None,
) -> int:
    """Deprecated compatibility stub; Tk HUD runtime has been removed."""
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return _legacy_hud_session_unavailable("tk")


def _run_qt_window_session(
    context: RuntimeContext,
    args: argparse.Namespace,
    *,
    daemon_manager: CodexDaemonManager | None = None,
    existing_window: Any | None = None,
    close_context: bool = True,
    update_manager: AutoUpdateManager | None = None,
) -> int:
    """Deprecated compatibility stub; Qt HUD runtime has been removed."""
    del args, daemon_manager, existing_window, update_manager
    if close_context:
        context.close()
    return _legacy_hud_session_unavailable("qt")


def run_qt_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: CodexDaemonManager | None = None,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Deprecated compatibility stub; renderer HUD is the only supported HUD."""
    del args, hide_until_attached, daemon_manager
    if loading_feedback is not None:
        loading_feedback.close()
    del lock_already_held
    return _legacy_hud_session_unavailable("qt")


def run_tk_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = True,
    daemon_manager: CodexDaemonManager | None = None,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Deprecated compatibility stub; renderer HUD is the only supported HUD."""
    del args, hide_until_attached, daemon_manager
    if loading_feedback is not None:
        loading_feedback.close()
    del lock_already_held
    return _legacy_hud_session_unavailable("tk")


def run_daemon(args: argparse.Namespace) -> int:
    """Run the hidden Desktop daemon manager, falling back when unsupported."""
    configure_daemon_logging()
    _attach_cli_logger_to_daemon_log()
    hide_console_window()
    manager = CodexDaemonManager(poll_ms=args.daemon_poll_ms)
    preferred_runtime_mode = _initial_runtime_display_mode(args)
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
                    hide_until_attached=True,
                )

            if startup.mode == DAEMON_STARTUP_CANCEL:
                _LOGGER.info("daemon_startup_cancelled")
                return 0
            startup_loading: HudLoadingFeedback | None = None
            launched_codex_for_renderer = False
            observed_codex_launch = False
            if startup.mode == DAEMON_STARTUP_WAIT:
                # Existing Codex: show the same compact top-right progress card
                # immediately, then turn this exact card into the restart
                # action if the fixed CDP port is unavailable.
                startup_loading = _create_loading_feedback(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在检查 Codex 的 CDP 连接…",
                ).start()
            if startup.mode == DAEMON_STARTUP_RENDERER and startup.launch_codex:
                startup_loading = _create_loading_feedback(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在以调试模式启动 Codex App...",
                ).start()
                try:
                    launch_port = _select_launch_renderer_cdp_port()
                except (OSError, RuntimeError) as exc:
                    startup_loading.close()
                    _append_renderer_diagnostic(
                        "renderer_cdp_launch_failed",
                        reason=str(exc),
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                if not launch_codex_app(debugger=True):
                    startup_loading.close()
                    _append_renderer_diagnostic(
                        "renderer_cdp_launch_failed",
                        port=launch_port,
                        source="daemon-startup",
                    )
                    return RENDERER_HUD_UNAVAILABLE
                # The daemon has just initiated the fixed-port launch.  Pass
                # that fact through to the renderer session so it waits for
                # this one startup instead of treating the process as an old
                # non-CDP Codex instance and offering a second restart.
                launched_codex_for_renderer = True
                _LOGGER.info("daemon_startup_renderer_selected")
            if startup.mode == DAEMON_STARTUP_RENDERER:
                preferred_runtime_mode = "renderer"
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
                        hide_until_attached=True,
                    )
                if force_renderer_retry:
                    exit_code = run_renderer_hud_session(
                        _clone_args_with_renderer_preference(args, True),
                        lock_already_held=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        launched_codex=launched_codex_for_renderer,
                        observed_codex_launch=observed_codex_launch,
                    )
                else:
                    exit_code = run_hud_session(
                        _clone_args_with_display_mode(args, preferred_runtime_mode),
                        lock_already_held=True,
                        hide_until_attached=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                        observed_codex_launch=observed_codex_launch,
                    )
                session_loading = startup_loading
                startup_loading = None
                observed_codex_launch = False
                if exit_code == HUD_AUTO_RESTART_CODEX:
                    startup_loading = session_loading
                    if startup_loading is None:
                        startup_loading = _create_loading_feedback(
                            args,
                            title="正在切换到 Renderer HUD",
                            message="正在以调试/CDP 模式重新启动 Codex App…",
                        ).start()
                    else:
                        startup_loading.update(
                            title="正在切换到 Renderer HUD",
                            message="正在以调试/CDP 模式重新启动 Codex App…",
                        )
                    if not _restart_codex_for_renderer():
                        startup_loading.close()
                        _LOGGER.info("daemon_observed_codex_takeover_failed")
                        return RENDERER_HUD_UNAVAILABLE
                    launched_codex_for_renderer = True
                    force_renderer_retry = True
                    _LOGGER.info("daemon_observed_codex_takeover_started")
                    continue
                if exit_code == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
                    startup_loading = _create_loading_feedback(
                        args,
                        title="正在重启 Codex",
                        message="正在以调试/CDP 模式重启 Codex App，并重新尝试注入 HUD...",
                    ).start()
                    if not _restart_codex_for_renderer():
                        startup_loading.close()
                        _LOGGER.info("daemon_renderer_restart_requested_but_failed")
                        return RENDERER_HUD_UNAVAILABLE
                    launched_codex_for_renderer = True
                    force_renderer_retry = True
                    _LOGGER.info("daemon_renderer_restart_requested")
                    continue
                if exit_code == DAEMON_RESTART_REQUESTED:
                    launched_codex_for_renderer = False
                    observed_codex_launch = True
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
    _enable_crash_diagnostics()
    _init_force_desktop_overlay_missing_from_env()
    if getattr(args, "loading_feedback_helper", False):
        return run_loading_feedback_helper(args.loading_feedback_state_file)
    if getattr(args, "work_overlay_helper", False):
        return run_work_overlay_helper(args.work_overlay_state_file)
    cleanup_stale_loading_feedback_files()
    cleanup_stale_work_overlay_files()
    if args.check_update:
        return run_update_check()
    if args.update:
        return run_update_install()
    if args.renderer_hud is None and not getattr(args, "hud_mode", None):
        normalize_display_mode(UserConfigStore().load().display_mode)
    args.hud_mode = "renderer"
    args.runtime_hud_mode = "renderer"
    args.standalone_hud_mode = None
    args.renderer_hud = True
    if args.stop:
        print(stop_running_hud())
        return 0
    if args.daemon and args.once:
        parser.error("--daemon cannot be combined with --once")

    if args.once:
        return run_once_snapshot(args)

    # Every persistent entry uses the daemon lifecycle. This prevents a normal
    # no-argument launch from looking alive while missing later Codex launches.
    return run_daemon(args)


if __name__ == "__main__":
    raise SystemExit(main())
