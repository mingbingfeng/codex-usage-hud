"""Command-line interface for codex-usage-hud."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import gc
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Mapping

from . import __version__
from .config import (
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
    CostEstimator,
    JsonlSessionParser,
    ParsedSession,
    RequestRound,
    SseRequestStateMachine,
    UsageCalculator,
    UsageSummary,
    WorkStatusItem,
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
    CdpSessionSwitchBackend,
    SessionPathResolver,
    SessionSwitchController,
    WindowsSearchSessionSwitchBackend,
    get_current_platform,
)
from .platforms.base import BasePlatform
from .platforms.cdp_probe import cdp_port_from_env
from .platforms.codex_theme import CodexThemeProbe
from .settings_bridge import SettingsBridgeServer
from .ui.tk_hud import TokenHudWindow
from .ui.renderer_hud import (
    RendererHudClient,
    remove_renderer_hud_from_pages,
    wait_for_renderer,
)
from .ui.work_overlay_qt import (
    _work_overlay_header_text,
    run_work_overlay_helper_qt,
    work_overlay_max_items_for_screen_height,
)
from .updater import (
    AutoUpdateManager,
    check_for_update,
    download_update_asset,
    format_update_info,
    launch_installer,
)

DEFAULT_POLL_MS = 500
WORK_OVERLAY_COMMAND_POLL_MS = 60
WORK_OVERLAY_CURRENT_SESSION_REFOCUS_DELAY_SECONDS = 0.12
DEFAULT_SQLITE_LOG = "logs_2.sqlite"
DEFAULT_STATE_DB = "state_5.sqlite"
DEFAULT_SESSION_INDEX = "session_index.jsonl"
DEFAULT_BUDGET_THRESHOLDS_TEXT = ",".join(f"{item:g}" for item in DEFAULT_BUDGET_THRESHOLDS)
DEFAULT_ACTIVE_SESSION_POLL_MS = 500
DEFAULT_AUTO_SWITCH_IDLE_SECONDS = 30.0
NATIVE_SEARCH_SESSION_SWITCH_ENV = "CODEX_USAGE_HUD_NATIVE_SEARCH_SWITCH"
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
HUD_SWITCH_TO_QT = 33
RENDERER_CDP_TIMEOUT_SECONDS = 1.0
DAEMON_RENDERER_CDP_TIMEOUT_SECONDS = 1.5
RENDERER_INITIAL_TIMEOUT_SECONDS = 2.0
RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS = 2.0
DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS = 5.0
DAEMON_RENDERER_WINDOW_READY_TIMEOUT_SECONDS = 15.0
RENDERER_UPDATE_FAILURE_LIMIT = 6
AUTO_RENDERER_TIMEOUT_FAILURE_LIMIT = 3
RENDERER_DIAGNOSTIC_FILENAME = "renderer_fallback.log"
CRASH_DIAGNOSTIC_FILENAME = "crash.log"
CRASH_DIAGNOSTICS_ENV = "CODEX_USAGE_HUD_CRASH_DIAGNOSTICS"
CODEX_APP_PATH_ENV = "CODEX_USAGE_HUD_CODEX_APP"
CODEX_APP_ID_ENV = "CODEX_USAGE_HUD_CODEX_APP_ID"
CODEX_APP_DEFAULT_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
DAEMON_STARTUP_WAIT = "wait"
DAEMON_STARTUP_RENDERER = "renderer"
DAEMON_STARTUP_QT = "qt"
DAEMON_STARTUP_TK = "tk"
DAEMON_STARTUP_CANCEL = "cancel"
LOADING_FEEDBACK_STALE_SECONDS = 20.0
ACTIVE_WORK_ITEM_LIMIT = DEFAULT_WORK_OVERLAY_MAX_ITEMS
ACTIVE_WORK_CANDIDATE_LIMIT = 16
ACTIVE_WORK_STALE_SECONDS = 4 * 60 * 60
RECENT_WORK_STARTUP_GRACE_SECONDS = 60.0
VISIBLE_APP_ERROR_HOLD_SECONDS = 60.0
WORK_OVERLAY_STALE_SECONDS = 20.0
WORK_OVERLAY_ALPHA = 0.88
WORK_OVERLAY_HOVER_ALPHA = 0.52
WORK_OVERLAY_HEADER_TITLE_LIMIT = 28
_LOGGER = logging.getLogger("codex_usage_hud.cli")
_cli_daemon_logging_attached = False
_CRASH_DIAGNOSTIC_FILE: Any | None = None
QtHudWindow: Any | None = None


def _qt_hud_window_class() -> Any:
    global QtHudWindow
    if QtHudWindow is None:
        from .ui.qt_hud import QtHudWindow as qt_hud_window_class

        QtHudWindow = qt_hud_window_class
    return QtHudWindow


class HudAlreadyRunningError(RuntimeError):
    """Raised when another HUD instance owns the local runtime lock."""


@dataclass(frozen=True)
class DaemonStartupDecision:
    """How daemon startup should continue when Codex is not already visible."""

    mode: str
    launch_codex: bool = False


class HudLoadingFeedback:
    """Small topmost startup/loading card for manual launches and mode switches."""

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
        self._closed = False

    def start(self) -> "HudLoadingFeedback":
        if not self.enabled or self._process is not None:
            return self
        state_path = hud_runtime_dir() / f"loading-{os.getpid()}-{int(time.time() * 1000)}.json"
        self._state_path = state_path
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
                    "updatedAt": time.time(),
                    "close": bool(close),
                },
            )
        except OSError:
            return


def _loading_feedback_enabled(args: argparse.Namespace | None = None) -> bool:
    if not sys.platform.startswith("win"):
        return False
    if args is not None and getattr(args, "no_startup_prompt", False):
        return False
    return True


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
        if owner_pid is not None and owner_alive and not stale:
            continue
        if owner_pid is None and not stale:
            continue
        try:
            path.unlink()
        except OSError:
            continue
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
        padx=18,
        pady=16,
    )
    shell.pack(fill="both", expand=True)

    title_var = tk.StringVar(value="")
    message_var = tk.StringVar(value="")

    tk.Label(
        shell,
        text="codex-usage-hud",
        anchor="w",
        bg="#10161D",
        fg="#F3D27A",
        font=("Microsoft YaHei UI", 9, "bold"),
    ).pack(fill="x")
    tk.Label(
        shell,
        textvariable=title_var,
        anchor="w",
        justify="left",
        bg="#10161D",
        fg="#F6F9FC",
        font=("Microsoft YaHei UI", 15, "bold"),
        pady=4,
    ).pack(fill="x")
    tk.Label(
        shell,
        textvariable=message_var,
        anchor="w",
        justify="left",
        bg="#10161D",
        fg="#B8C6D8",
        font=("Microsoft YaHei UI", 10),
        wraplength=324,
    ).pack(fill="x")

    track = tk.Canvas(
        shell,
        width=324,
        height=8,
        bg="#10161D",
        highlightthickness=0,
        bd=0,
    )
    track.pack(fill="x", pady=(14, 0))
    track.create_rectangle(0, 1, 324, 7, fill="#1A2430", outline="")
    indicator = track.create_rectangle(0, 1, 92, 7, fill="#F3D27A", outline="")
    accent = track.create_rectangle(0, 1, 48, 7, fill="#FFE7A0", outline="")

    root.update_idletasks()
    width = max(360, int(root.winfo_reqwidth()))
    height = max(132, int(root.winfo_reqheight()))
    screen_width = max(1, int(root.winfo_screenwidth()))
    screen_height = max(1, int(root.winfo_screenheight()))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.deiconify()

    position = 0
    direction = 1
    last_signature = ("", "", False)
    owner_pid = _loading_feedback_owner_pid(path)

    def animate_bar() -> None:
        nonlocal position, direction
        if not root.winfo_exists():
            return
        position += 7 * direction
        if position >= 232:
            position = 232
            direction = -1
        elif position <= 0:
            position = 0
            direction = 1
        track.coords(indicator, position, 1, position + 92, 7)
        track.coords(accent, position + 20, 1, position + 60, 7)
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
        updated_at = float(state.get("updatedAt") or 0.0)
        file_stale = updated_at > 0 and (time.time() - updated_at) > LOADING_FEEDBACK_STALE_SECONDS
        if owner_pid is not None and not _process_exists(owner_pid):
            root.destroy()
            return
        if file_stale:
            root.destroy()
            return
        signature = (title, message, should_close)
        if signature != last_signature:
            last_signature = signature
            title_var.set(title)
            message_var.set(message)
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


def work_item_to_overlay_dict(item: WorkStatusItem) -> dict[str, object]:
    return {
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
        "taskStartedAt": _iso_or_empty(item.task_started_at),
        "startedAt": _iso_or_empty(item.started_at),
        "updatedAt": _iso_or_empty(item.updated_at),
        "current": item.current,
    }


class DesktopWorkOverlay:
    """Primary-screen Tk overlay that stays visible when Codex is minimized."""

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
        self._command_offset = 0
        self._process: subprocess.Popen[str] | None = None
        self._closed = False
        self._theme_probe = CodexThemeProbe(
            timeout_seconds=0.08,
            cache_seconds=0.8,
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
        if not self.enabled and not self._closed:
            self._stop_runtime(permanent=False)

    def update(self, items: Sequence[WorkStatusItem]) -> None:
        if self._closed:
            return
        if not self.enabled:
            self._stop_runtime(permanent=False)
            return
        payload_items = [work_item_to_overlay_dict(item) for item in items]
        theme_payload = self._theme_payload()
        if not payload_items and self._process is None:
            return
        if self._process is not None and self._process.poll() is not None:
            self._process = None
        self._write_state(payload_items, theme=theme_payload, close=False)
        if self._process is None:
            self._start()

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
        self._command_offset = 0

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                _work_overlay_command(self._state_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._process = None

    def _write_state(
        self,
        items: Sequence[Mapping[str, object]],
        *,
        theme: Mapping[str, object] | None = None,
        close: bool,
    ) -> None:
        try:
            write_json_object(
                self._state_path,
                {
                    "ownerPid": os.getpid(),
                    "itemLimit": int(self.item_limit),
                    "commandPath": str(self._command_path),
                    "items": list(items),
                    "theme": dict(theme or {}),
                    "updatedAt": time.time(),
                    "close": bool(close),
                },
            )
        except OSError:
            return

    def _theme_payload(self) -> dict[str, object]:
        snapshot = self._theme_probe.snapshot()
        if snapshot.source not in {"cdp", "persisted"}:
            return {}
        return snapshot.hud_tokens.to_dict()


class _WorkOverlayCommandPump:
    """Drain work-overlay click commands off the UI thread."""

    def __init__(
        self,
        work_overlay: DesktopWorkOverlay,
        session_controller: SessionSwitchController,
        *,
        poll_ms: int = WORK_OVERLAY_COMMAND_POLL_MS,
        command_event: Event | None = None,
    ) -> None:
        self._work_overlay = work_overlay
        self._session_controller = session_controller
        self._poll_seconds = max(0.05, float(poll_ms) / 1000.0)
        self._command_event = command_event
        self._stop_event = Event()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def start(self) -> bool:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return True
            self._stop_event.clear()
            worker = threading.Thread(
                target=self._run,
                name="codex-usage-hud-work-overlay",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return True

    def close(self, timeout_seconds: float = 0.5) -> None:
        self._stop_event.set()
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, float(timeout_seconds)))

    def drain_once(self) -> int:
        handled = _handle_work_overlay_commands(
            self._work_overlay,
            self._session_controller,
            prepare_window=True,
        )
        if handled and self._command_event is not None:
            self._command_event.set()
        return handled

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.drain_once()
            except Exception as exc:
                _LOGGER.debug("work_overlay_command_pump_failed error=%s", exc)
            self._stop_event.wait(self._poll_seconds)


_TkWorkOverlayCommandPump = _WorkOverlayCommandPump


def run_work_overlay_helper(state_file: str | Path) -> int:
    state_arg = str(state_file or "").strip()
    if not state_arg:
        return 1
    try:
        return run_work_overlay_helper_qt(
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


def _clone_args_with_display_mode(
    args: argparse.Namespace,
    mode: str,
) -> argparse.Namespace:
    normalized = effective_display_mode(mode)
    cloned = argparse.Namespace(**vars(args))
    cloned.hud_mode = normalized
    cloned.runtime_hud_mode = normalized
    cloned.standalone_hud_mode = normalized if normalized in {"qt", "tk"} else None
    cloned.renderer_hud = normalized == "renderer"
    return cloned


def _runtime_display_mode(value: object) -> str:
    return effective_display_mode(value)


def _initial_runtime_display_mode(args: argparse.Namespace) -> str:
    explicit_mode = getattr(args, "hud_mode", None)
    if explicit_mode:
        return effective_display_mode(explicit_mode)
    runtime_mode = getattr(args, "runtime_hud_mode", None)
    if runtime_mode:
        return effective_display_mode(runtime_mode)
    standalone_mode = getattr(args, "standalone_hud_mode", None)
    if standalone_mode in {"qt", "tk"}:
        return str(standalone_mode)
    return "renderer" if bool(getattr(args, "renderer_hud", False)) else "tk"


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
        "否：启动 Codex App（普通模式），同时打开独立 Qt HUD 窗口。\n"
        "取消：退出 HUD。\n\n"
        "Renderer 注入需要 Codex 暴露本地调试端口；Qt 模式可作为独立窗口使用，Tk 会保留为最终兜底。"
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
            return DAEMON_STARTUP_QT
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
        launch_codex=mode in {DAEMON_STARTUP_RENDERER, DAEMON_STARTUP_QT, DAEMON_STARTUP_TK},
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


def _codex_processes_running() -> bool:
    if not sys.platform.startswith("win"):
        return False
    try:
        listener = WindowsProcessListener(exclude_pid=os.getpid())
        return bool(listener.snapshot().found)
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
                launched = _restart_codex_for_renderer()
                action = "restart_debugger"
            else:
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
    """Best-effort restore/focus of Codex before opening a standalone HUD."""
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
                "standalone_codex_shell_activation_requested activated=%s status=%s hwnd=%s reason=%s",
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
            launched = launch_codex_app(debugger=False)
            _LOGGER.info(
                "standalone_codex_window_restore_requested launched=%s status=%s hwnd=%s reason=%s",
                launched,
                last_status,
                last_hwnd,
                last_reason,
            )

        if time.monotonic() >= deadline:
            return False, last_status, last_reason, last_hwnd
        time.sleep(max(0.01, float(poll_seconds)))


def _prepare_codex_window_for_tk(
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.25,
    launch_if_missing: bool = False,
) -> tuple[bool, str, str, int]:
    """Compatibility wrapper for older tests and integrations."""
    return _prepare_codex_window_for_standalone(
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        launch_if_missing=launch_if_missing,
    )


def _build_session_switch_controller(
    platform: BasePlatform,
    *,
    prefer_native_search: bool,
) -> SessionSwitchController:
    cdp = CdpSessionSwitchBackend(timeout_seconds=RENDERER_CDP_TIMEOUT_SECONDS)
    native_setting = os.environ.get(NATIVE_SEARCH_SESSION_SWITCH_ENV, "").strip().lower()
    native_enabled = native_setting not in {"0", "false", "no", "off"}
    backends: list[object] = [cdp]
    if native_enabled:
        native = WindowsSearchSessionSwitchBackend(platform)
        backends = [native, cdp] if prefer_native_search else [cdp, native]
    return SessionSwitchController(backends)


def _refocus_codex_window_after_current_session_click() -> tuple[bool, str, str, int]:
    time.sleep(WORK_OVERLAY_CURRENT_SESSION_REFOCUS_DELAY_SECONDS)
    return _prepare_codex_window_for_tk(
        timeout_seconds=min(RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS, 0.75),
        poll_seconds=0.12,
        launch_if_missing=True,
    )


def _handle_work_overlay_command(
    command: Mapping[str, object],
    session_controller: SessionSwitchController,
    *,
    prepare_window: bool = True,
) -> None:
    action = str(command.get("action") or "").strip()
    if action != "activateSession":
        return
    is_current = bool(command.get("current"))
    session_id = str(command.get("sessionId") or "").strip()
    target_title = str(command.get("targetTitle") or command.get("title") or "").strip()
    if not session_id and not target_title:
        _LOGGER.info("work_overlay_command_ignored reason=missing_target")
        return

    if prepare_window:
        window_ready, window_status, window_reason, window_hwnd = _prepare_codex_window_for_tk(
            timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
            launch_if_missing=True,
        )
        if not window_ready:
            _LOGGER.info(
                "work_overlay_command_window_prepare_best_effort_failed status=%s hwnd=%s reason=%s",
                window_status,
                window_hwnd,
                window_reason,
            )

    result = session_controller.activate_session(
        session_id=session_id,
        title=target_title,
        workdir=str(command.get("workdir") or "").strip(),
    )
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
    if prepare_window and (is_current or result.status == "already-active"):
        window_ready, window_status, window_reason, window_hwnd = (
            _refocus_codex_window_after_current_session_click()
        )
        _LOGGER.info(
            "work_overlay_command_current_session_refocus ok=%s status=%s hwnd=%s reason=%s",
            window_ready,
            window_status,
            window_hwnd,
            window_reason or "-",
        )


def _handle_work_overlay_commands(
    work_overlay: DesktopWorkOverlay,
    session_controller: SessionSwitchController,
    *,
    prepare_window: bool = True,
) -> int:
    take_commands = getattr(work_overlay, "take_commands", None)
    if not callable(take_commands):
        return 0
    handled = 0
    for command in take_commands():
        _handle_work_overlay_command(
            command,
            session_controller,
            prepare_window=prepare_window,
        )
        handled += 1
    return handled


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
        self._last_scan_key: tuple[tuple[Path, ...], datetime, datetime] | None = None
        self._last_scan_at = 0.0
        self._last_day_total = UsageSummary()
        self._last_week_total = UsageSummary()

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

    def summarize(
        self,
        sessions_root: Path,
        day_start: datetime,
        week_start: datetime,
    ) -> tuple[UsageSummary, UsageSummary]:
        now = time.monotonic()
        scan_roots = self._scan_roots(sessions_root)
        scan_key = (scan_roots, day_start, week_start)
        if (
            self._last_scan_key == scan_key
            and now - self._last_scan_at < self._min_rescan_seconds
        ):
            return replace(self._last_day_total), replace(self._last_week_total)

        day_total = UsageSummary()
        week_total = UsageSummary()

        existing_roots = [root for root in scan_roots if root.exists()]
        if not existing_roots:
            self._last_scan_key = scan_key
            self._last_scan_at = now
            self._last_day_total = day_total
            self._last_week_total = week_total
            return day_total, week_total

        seen_paths: set[Path] = set()
        for root in existing_roots:
            for path in root.rglob("*.jsonl"):
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


def _work_status_from_snapshot(
    snapshot: ParsedSession,
    *,
    now: datetime,
) -> tuple[str, str] | None:
    if snapshot.task_aborted_at is not None:
        return None
    activity_detail = snapshot.activity.detail.lower()
    request_status = snapshot.request.status
    if request_status == "error" or snapshot.request.error:
        return "error", "出错"
    if snapshot.task_completed_at is not None:
        return "recent", "刚完成"
    if request_status == "running":
        return "running", "运行中"
    if snapshot.activity.kind == "tool call" and activity_detail.startswith(
        "request_user_input"
    ):
        return "waiting_user", "等待用户"
    if snapshot.activity.kind == "tool call":
        return "tool", "工具执行"
    if snapshot.slow.current_gap_active:
        return "active", "处理中"
    return None


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
    status_value, status_label = status

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
        session_started_at=snapshot.session_started_at,
        task_started_at=snapshot.task_started_at,
        started_at=started_at,
        updated_at=updated_at,
        current=current,
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
    return work_overlay_max_items_for_screen_height(height)


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


def _should_show_recent_work_overlay_item_on_first_sight(
    item: WorkStatusItem,
    *,
    now: datetime,
) -> bool:
    if item.current:
        return True
    completed_at = item.updated_at or item.started_at or item.session_started_at
    if completed_at is None:
        return False
    return _datetime_age_seconds(completed_at, now) <= RECENT_WORK_STARTUP_GRACE_SECONDS


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
            should_show = bool(task_key and task_key in previously_seen_task_keys)
            if not should_show:
                should_show = _should_show_recent_work_overlay_item_on_first_sight(
                    item,
                    now=now,
                )
            if should_show:
                visible.append(item)
                if task_key:
                    seen_task_keys.add(task_key)
            continue
        visible.append(item)
        if task_key:
            seen_task_keys.add(task_key)
    return visible


def active_work_items_for_snapshot(
    context: "RuntimeContext",
    snapshot: ParsedSession,
    session_path: Path | None,
) -> list[WorkStatusItem]:
    """Build primary-screen work bubble items from recently active Codex sessions."""
    item_limit = _work_overlay_item_limit_for_context(context)
    if item_limit <= 0:
        return []
    now = datetime.now().astimezone()
    items: dict[str, WorkStatusItem] = {}
    current_key = _session_path_key(session_path)
    current_item = _work_item_from_snapshot(
        snapshot,
        current=True,
        title=snapshot.session_title,
        source=snapshot.selection_source,
        now=now,
    )
    if current_item is not None:
        items[str(current_item.id)] = current_item

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

    def sort_key(item: WorkStatusItem) -> tuple[int, float]:
        session_timestamp = item.session_started_at or item.started_at or item.updated_at
        task_timestamp = item.started_at or item.updated_at or item.session_started_at
        session_seconds = session_timestamp.timestamp() if session_timestamp is not None else 0.0
        task_seconds = task_timestamp.timestamp() if task_timestamp is not None else 0.0
        return (session_seconds, task_seconds)

    ordered = sorted(items.values(), key=sort_key, reverse=True)
    return _select_runtime_work_overlay_items(
        context,
        ordered,
        item_limit=item_limit,
    )


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
    visible_app_error_cache: _VisibleAppErrorCache = field(
        default_factory=_VisibleAppErrorCache
    )

    def close(self) -> None:
        """Release any background helpers created for the runtime context."""
        if self.active_session_tracker is not None:
            self.active_session_tracker.close()
            self.active_session_tracker = None

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


class _TkSnapshotPump:
    """Keep Tk responsive by building snapshots off the Tk main thread."""

    def __init__(self, context: RuntimeContext) -> None:
        self._context = context
        self._lock = threading.Lock()
        self._stop_event = Event()
        self._worker: threading.Thread | None = None
        self._latest_snapshot: ParsedSession | None = None

    def request_refresh(self) -> bool:
        with self._lock:
            if self._stop_event.is_set():
                return False
            if self._worker is not None and self._worker.is_alive():
                return False
            worker = threading.Thread(
                target=self._refresh_worker,
                name="codex-usage-hud-tk-refresh",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return True

    def take_latest(self) -> ParsedSession | None:
        with self._lock:
            snapshot = self._latest_snapshot
            self._latest_snapshot = None
            return snapshot

    def close(self, timeout_seconds: float = 0.5) -> None:
        self._stop_event.set()
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=max(0.0, float(timeout_seconds)))

    def _refresh_worker(self) -> None:
        try:
            self._context.reload_user_config()
            snapshot = build_snapshot(self._context)
        except Exception as exc:
            snapshot = ParsedSession(status="error", error=str(exc))
        with self._lock:
            self._worker = None
            if self._stop_event.is_set():
                return
            self._latest_snapshot = snapshot


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
    exit_requested: Event,
    update_manager: AutoUpdateManager | None = None,
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
                    else (
                        "已保存到本地配置；当前会话仍保持 Renderer，Qt 方案会在下次切换或重启后生效。"
                        if next_runtime_mode == "qt"
                        else "已保存到本地配置；当前会话仍保持 Renderer，Tk 方案会在下次切换或重启后生效。"
                    )
                ),
            )
        if action == "applyDisplayMode":
            config = _config_from_settings_payload(
                context.settings_store.load(),
                command.get("settings"),
            )
            _save_renderer_user_config(context, config)
            next_runtime_mode = _runtime_display_mode(config.display_mode)
            if next_runtime_mode == "qt":
                return _renderer_settings_status(
                    "正在切换到 Qt 独立窗口。",
                    switch_mode="qt",
                )
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
    app_error = context.visible_app_error_cache.resolve(
        snapshot,
        _visible_app_error(context.platform),
    )
    _apply_visible_app_error(snapshot, app_error)

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
    snapshot.active_work_items = active_work_items_for_snapshot(
        context,
        snapshot,
        session_path,
    )
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
        help="Use compact output mode for CLI snapshots and standalone HUDs.",
    )
    parser.set_defaults(renderer_hud=None)
    parser.add_argument(
        "--renderer-hud",
        dest="renderer_hud",
        action="store_true",
        help=(
            "Prefer the renderer-injected HUD when Codex exposes a local CDP "
            "target, falling back to Qt and then Tk otherwise. Enabled by default."
        ),
    )
    parser.add_argument(
        "--qt-hud",
        dest="hud_mode",
        action="store_const",
        const="qt",
        help="Force the Qt standalone HUD and skip renderer injection.",
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
        choices=["auto", "renderer", "qt", "tk"],
        help=(
            "Override the configured HUD display mode for this run. "
            "auto tries renderer, then Qt, then Tk; qt and tk skip renderer injection."
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
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Run one HUD session, preferring renderer, then Qt, with Tk as final fallback."""
    runtime_mode = _initial_runtime_display_mode(args)
    session_args = _clone_args_with_display_mode(args, runtime_mode)
    launched_codex_for_renderer = False

    def switch_to(mode: str, *, title: str, message: str) -> None:
        nonlocal runtime_mode, session_args, loading_feedback
        runtime_mode = effective_display_mode(mode)
        loading_feedback = _create_loading_feedback(
            session_args,
            title=title,
            message=message,
        ).start()
        session_args = _clone_args_with_display_mode(session_args, runtime_mode)

    while True:
        if runtime_mode == "renderer":
            renderer_exit = run_renderer_hud_session(
                session_args,
                lock_already_held=lock_already_held,
                daemon_manager=daemon_manager,
                launched_codex=launched_codex_for_renderer,
                loading_feedback=loading_feedback,
            )
            launched_codex_for_renderer = False
            loading_feedback = None
            if renderer_exit == HUD_SWITCH_TO_QT:
                switch_to(
                    "qt",
                    title="正在切换到 Qt HUD",
                    message="正在关闭内嵌 HUD 并打开独立的 Qt 悬浮窗...",
                )
                continue
            if renderer_exit == HUD_SWITCH_TO_TK:
                switch_to(
                    "tk",
                    title="正在切换到 Tk HUD",
                    message="正在关闭内嵌 HUD 并打开独立的 Tk 悬浮窗...",
                )
                continue
            if renderer_exit != RENDERER_HUD_UNAVAILABLE:
                return renderer_exit
            _LOGGER.info("renderer_hud_unavailable falling_back=qt")
            switch_to(
                "qt",
                title="正在启动 Qt HUD",
                message="Renderer 暂不可用，正在打开独立的 Qt 悬浮窗...",
            )
            continue

        if runtime_mode == "qt":
            qt_exit = run_qt_hud_session(
                session_args,
                lock_already_held=lock_already_held,
                hide_until_attached=hide_until_attached,
                daemon_manager=daemon_manager,
                loading_feedback=loading_feedback,
            )
            loading_feedback = None
            if qt_exit == HUD_SWITCH_TO_TK:
                switch_to(
                    "tk",
                    title="正在切换到 Tk HUD",
                    message="Qt HUD 暂不可用，正在打开最终兜底的 Tk 悬浮窗...",
                )
                continue
            if qt_exit == HUD_SWITCH_TO_RENDERER:
                runtime_mode = "renderer"
                session_args = _clone_args_with_display_mode(session_args, "renderer")
                continue
            if qt_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
                if loading_feedback is None:
                    loading_feedback = _create_loading_feedback(
                        session_args,
                        title="正在切换到 Renderer HUD",
                        message="正在以调试模式重启 Codex App...",
                    ).start()
                else:
                    loading_feedback.update(
                        title="正在切换到 Renderer HUD",
                        message="正在以调试模式重启 Codex App...",
                    )
                if not _restart_codex_for_renderer():
                    loading_feedback.close()
                    _eprint("codex-usage-hud: unable to restart Codex App in debugger mode.")
                    switch_to(
                        "tk",
                        title="正在切换到 Tk HUD",
                        message="Renderer 切换失败，正在打开最终兜底的 Tk 悬浮窗...",
                    )
                    continue
                runtime_mode = "renderer"
                session_args = _clone_args_with_display_mode(session_args, "renderer")
                launched_codex_for_renderer = True
                continue
            return qt_exit

        tk_exit = run_tk_hud_session(
            session_args,
            lock_already_held=lock_already_held,
            hide_until_attached=hide_until_attached,
            daemon_manager=daemon_manager,
            loading_feedback=loading_feedback,
        )
        loading_feedback = None
        if tk_exit == HUD_SWITCH_TO_QT:
            switch_to(
                "qt",
                title="正在切换到 Qt HUD",
                message="正在关闭 Tk HUD 并打开独立的 Qt 悬浮窗...",
            )
            continue
        if tk_exit == HUD_SWITCH_TO_RENDERER:
            runtime_mode = "renderer"
            session_args = _clone_args_with_display_mode(session_args, "renderer")
            continue
        if tk_exit == HUD_SWITCH_TO_RENDERER_RESTART_CODEX:
            if loading_feedback is None:
                loading_feedback = _create_loading_feedback(
                    session_args,
                    title="正在切换到 Renderer HUD",
                    message="正在以调试模式重启 Codex App...",
                ).start()
            else:
                loading_feedback.update(
                    title="正在切换到 Renderer HUD",
                    message="正在以调试模式重启 Codex App...",
                )
            if not _restart_codex_for_renderer():
                loading_feedback.close()
                _eprint("codex-usage-hud: unable to restart Codex App in debugger mode.")
                runtime_mode = "tk"
                session_args = _clone_args_with_display_mode(session_args, "tk")
                continue
            runtime_mode = "renderer"
            session_args = _clone_args_with_display_mode(session_args, "renderer")
            launched_codex_for_renderer = True
            continue
        return tk_exit


def run_renderer_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    launched_codex: bool = False,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Run the in-renderer HUD over CDP, or report that it is unavailable."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            local_loading = loading_feedback or _create_loading_feedback(
                args,
                title=(
                    "正在切换到 Renderer HUD"
                    if launched_codex
                    else "正在启动 Renderer HUD"
                ),
                message=(
                    "正在等待 Codex 界面就绪，并把 HUD 注入到窗口里..."
                    if launched_codex or daemon_manager is not None
                    else "正在连接 Codex 的本地调试目标..."
                ),
            ).start()
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
            update_manager = AutoUpdateManager(current_version=__version__)
            restart_requested = Event()
            exit_requested = Event()
            work_overlay = DesktopWorkOverlay(
                item_limit=_work_overlay_item_limit_for_context(context),
            )
            session_controller = _build_session_switch_controller(
                getattr(context, "platform", get_current_platform()),
                prefer_native_search=False,
            )
            command_refresh_requested = Event()
            command_pump = _WorkOverlayCommandPump(
                work_overlay,
                session_controller,
                command_event=command_refresh_requested,
            )
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
                launch_if_missing = True
                local_loading.update(
                    title=(
                        "正在切换到 Renderer HUD"
                        if launched_codex
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
                    local_loading.update(
                        title=(
                            "正在切换到 Renderer HUD"
                            if launched_codex
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
                    DAEMON_RENDERER_INITIAL_TIMEOUT_SECONDS
                    if wait_for_window
                    else RENDERER_INITIAL_TIMEOUT_SECONDS
                )
                local_loading.update(
                    title=(
                        "正在切换到 Renderer HUD"
                        if launched_codex
                        else "正在启动 Renderer HUD"
                    ),
                    message="正在把 HUD 注入 Codex 界面，通常只需 1 到 3 秒...",
                )
                if not wait_for_renderer(
                    client,
                    snapshot_or_error,
                    timeout_seconds=initial_timeout,
                ):
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

                local_loading.close()
                command_pump.start()
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
                    update_state = update_manager.tick().to_dict()
                    command = client.take_settings_command()
                    force_fast_refresh = bool(
                        command
                        or settings_command_status
                        or update_state.get("phase") == "downloading"
                    )
                    if command_refresh_requested.is_set():
                        command_refresh_requested.clear()
                        force_fast_refresh = True
                    if command:
                        settings_command_status = _handle_renderer_settings_command(
                            command,
                            context,
                            restart_requested,
                            exit_requested,
                            update_manager,
                        )
                        update_state = update_manager.status().to_dict()
                    mode_switch = str(settings_command_status.get("switchMode") or "").strip()
                    if mode_switch == "qt":
                        local_loading.close()
                        _LOGGER.info("renderer_hud_switch_requested mode=qt")
                        return HUD_SWITCH_TO_QT
                    if mode_switch == "tk":
                        local_loading.close()
                        _LOGGER.info("renderer_hud_switch_requested mode=tk")
                        return HUD_SWITCH_TO_TK
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
                    context.reload_user_config()
                    snapshot = snapshot_or_error()
                    work_overlay.configure(
                        item_limit=_work_overlay_item_limit_for_context(context),
                    )
                    work_overlay.update(snapshot.active_work_items)
                    if client.update(
                        snapshot,
                        settings=context.user_config,
                        active_display_mode="renderer",
                        settings_path=context.settings_store.path,
                        settings_bridge_url=bridge_url,
                        settings_command_status=settings_command_status,
                        update_state=update_state,
                        work_overlay_selectable_max=_work_overlay_screen_max_items(),
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
                local_loading.close()
                return 130
            finally:
                client.close()
                bridge.close()
                command_pump.close()
                work_overlay.close()
                update_manager.close()
                context.close()
    except HudAlreadyRunningError as exc:
        _eprint(f"codex-usage-hud: {exc}")
        return 2


def _run_tk_window_session(
    context: RuntimeContext,
    args: argparse.Namespace,
    *,
    daemon_manager: CodexDaemonManager | None = None,
    existing_window: TokenHudWindow | None = None,
    close_context: bool = True,
    update_manager: AutoUpdateManager | None = None,
) -> int:
    snapshot_pump = _TkSnapshotPump(context)
    work_overlay = DesktopWorkOverlay(
        item_limit=_work_overlay_item_limit_for_context(context),
    )
    command_pump: _WorkOverlayCommandPump | None = None
    session_controller = _build_session_switch_controller(
        getattr(context, "platform", get_current_platform()),
        prefer_native_search=True,
    )
    window: TokenHudWindow | None = None
    try:
        try:
            window = existing_window or TokenHudWindow(
                compact=args.compact,
                hide_until_attached=False,
                tombstone_follow_ms=(
                    100 if daemon_manager is not None else 500
                ),
                user_settings_store=getattr(context, "settings_store", None),
                update_manager=update_manager,
            )
        except Exception as exc:
            _eprint(f"codex-usage-hud: unable to open Tkinter HUD: {exc}")
            return 1
        command_pump = _WorkOverlayCommandPump(work_overlay, session_controller)
        command_pump.start()
        latest_snapshot = ParsedSession(status="waiting")

        def refresh() -> None:
            nonlocal latest_snapshot
            defer_background_work = bool(
                getattr(window, "should_defer_background_work", lambda: False)()
            )
            if not defer_background_work:
                overlay_item_limit = _work_overlay_item_limit_for_context(context)
                refresh_snapshot = window.should_refresh_snapshot()
                if refresh_snapshot or overlay_item_limit > 0:
                    snapshot = snapshot_pump.take_latest()
                    if snapshot is not None:
                        latest_snapshot = snapshot
                    snapshot_pump.request_refresh()
                if refresh_snapshot:
                    window.update_display(
                        latest_snapshot,
                        update_state=update_manager.tick() if update_manager is not None else None,
                    )
                work_overlay.configure(
                    item_limit=overlay_item_limit,
                )
                work_overlay.update(latest_snapshot.active_work_items)
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
        mode_switch = str(getattr(window, "mode_switch_request", "") or "")
        if mode_switch:
            context.close()
        if mode_switch == "qt":
            return HUD_SWITCH_TO_QT
        if mode_switch == "renderer":
            if getattr(window, "restart_codex_for_renderer", False):
                return HUD_SWITCH_TO_RENDERER_RESTART_CODEX
            return HUD_SWITCH_TO_RENDERER
        return 0
    finally:
        if command_pump is not None:
            command_pump.close()
        work_overlay.close()
        snapshot_pump.close()
        window = None
        gc.collect()
        if close_context:
            context.close()


def _run_qt_window_session(
    context: RuntimeContext,
    args: argparse.Namespace,
    *,
    daemon_manager: CodexDaemonManager | None = None,
    existing_window: QtHudWindow | None = None,
    close_context: bool = True,
    update_manager: AutoUpdateManager | None = None,
) -> int:
    try:
        from PySide6.QtCore import QTimer
    except Exception as exc:
        _eprint(f"codex-usage-hud: unable to import Qt timer: {exc}")
        if close_context:
            context.close()
        return HUD_SWITCH_TO_TK

    snapshot_pump = _TkSnapshotPump(context)
    work_overlay = DesktopWorkOverlay(
        item_limit=_work_overlay_item_limit_for_context(context),
    )
    command_pump: _WorkOverlayCommandPump | None = None
    refresh_timer: Any | None = None
    daemon_timer: Any | None = None
    session_controller = _build_session_switch_controller(
        getattr(context, "platform", get_current_platform()),
        prefer_native_search=True,
    )
    try:
        try:
            qt_window_class = _qt_hud_window_class()
            window = existing_window or qt_window_class(
                compact=bool(getattr(args, "compact", False)),
                hide_until_attached=False,
                tombstone_follow_ms=(
                    100 if daemon_manager is not None else 500
                ),
                user_settings_store=getattr(context, "settings_store", None),
                update_manager=update_manager,
            )
        except Exception as exc:
            _eprint(f"codex-usage-hud: unable to open Qt HUD; falling back to Tk: {exc}")
            return HUD_SWITCH_TO_TK
        command_pump = _WorkOverlayCommandPump(work_overlay, session_controller)
        command_pump.start()
        latest_snapshot = ParsedSession(status="waiting")
        refresh_timer = QTimer()
        refresh_timer.setSingleShot(True)

        def schedule_refresh(delay_ms: int) -> None:
            try:
                refresh_timer.start(max(80, int(delay_ms)))
            except Exception:
                return

        def refresh() -> None:
            nonlocal latest_snapshot
            defer_background_work = bool(
                getattr(window, "should_defer_background_work", lambda: False)()
            )
            if not defer_background_work:
                overlay_item_limit = _work_overlay_item_limit_for_context(context)
                refresh_snapshot = window.should_refresh_snapshot()
                if refresh_snapshot or overlay_item_limit > 0:
                    snapshot = snapshot_pump.take_latest()
                    if snapshot is not None:
                        latest_snapshot = snapshot
                    snapshot_pump.request_refresh()
                if refresh_snapshot:
                    window.update_display(
                        latest_snapshot,
                        update_state=update_manager.tick() if update_manager is not None else None,
                    )
                work_overlay.configure(
                    item_limit=overlay_item_limit,
                )
                work_overlay.update(latest_snapshot.active_work_items)
            schedule_refresh(window.refresh_delay_ms(context.poll_ms))

        refresh_timer.timeout.connect(refresh)

        if daemon_manager is not None:
            daemon_timer = QTimer()

            def daemon_watchdog() -> None:
                try:
                    if not daemon_manager.codex_is_running():
                        _LOGGER.info("daemon_codex_exited")
                        window.close("daemon_codex_exited")
                        return
                except ProcessListenerError as exc:
                    _LOGGER.exception("daemon_watchdog_failed fallback=%s", exc)
                    return

            daemon_timer.timeout.connect(daemon_watchdog)
            daemon_timer.start(daemon_manager.poll_ms)

        refresh()
        window.run()
        if daemon_manager is not None and window.exit_reason == "daemon_codex_exited":
            return DAEMON_RESTART_REQUESTED
        mode_switch = str(getattr(window, "mode_switch_request", "") or "")
        if mode_switch:
            context.close()
        if mode_switch == "tk":
            return HUD_SWITCH_TO_TK
        if mode_switch == "renderer":
            if getattr(window, "restart_codex_for_renderer", False):
                return HUD_SWITCH_TO_RENDERER_RESTART_CODEX
            return HUD_SWITCH_TO_RENDERER
        return 0
    finally:
        if refresh_timer is not None:
            try:
                refresh_timer.stop()
            except Exception:
                pass
        if daemon_timer is not None:
            try:
                daemon_timer.stop()
            except Exception:
                pass
        if command_pump is not None:
            command_pump.close()
        work_overlay.close()
        snapshot_pump.close()
        if close_context:
            context.close()


def run_qt_hud_session(
    args: argparse.Namespace,
    *,
    lock_already_held: bool = False,
    hide_until_attached: bool = False,
    daemon_manager: CodexDaemonManager | None = None,
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Run one Qt HUD session, falling back to Tk when Qt is unavailable."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            update_manager = AutoUpdateManager(current_version=__version__)
            try:
                remove_renderer_hud_from_pages(
                    timeout_seconds=min(RENDERER_CDP_TIMEOUT_SECONDS, 0.5),
                )
                _prepare_codex_window_for_standalone(
                    timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
                    launch_if_missing=True,
                )
                try:
                    qt_window_class = _qt_hud_window_class()
                    window = qt_window_class(
                        compact=bool(getattr(args, "compact", False)),
                        hide_until_attached=hide_until_attached,
                        tombstone_follow_ms=(
                            100 if daemon_manager is not None else 500
                        ),
                        user_settings_store=getattr(context, "settings_store", None),
                        update_manager=update_manager,
                    )
                except Exception as exc:
                    _LOGGER.info("qt_hud_unavailable fallback=tk error=%s", exc)
                    _eprint(
                        f"codex-usage-hud: unable to open Qt HUD; falling back to Tk: {exc}"
                    )
                    return HUD_SWITCH_TO_TK
                if loading_feedback is not None:
                    loading_feedback.close()
                return _run_qt_window_session(
                    context,
                    args,
                    daemon_manager=daemon_manager,
                    existing_window=window,
                    close_context=False,
                    update_manager=update_manager,
                )
            finally:
                if loading_feedback is not None:
                    loading_feedback.close()
                update_manager.close()
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
    loading_feedback: HudLoadingFeedback | None = None,
) -> int:
    """Run one Tk HUD session with optional daemon process supervision."""
    lock_context = nullcontext() if lock_already_held else HudInstanceLock()
    try:
        with lock_context:
            context = build_runtime_context(args)
            update_manager = AutoUpdateManager(current_version=__version__)
            try:
                remove_renderer_hud_from_pages(
                    timeout_seconds=min(RENDERER_CDP_TIMEOUT_SECONDS, 0.5),
                )
                _prepare_codex_window_for_tk(
                    timeout_seconds=RENDERER_WINDOW_PREPARE_TIMEOUT_SECONDS,
                    launch_if_missing=True,
                )
                window = TokenHudWindow(
                    compact=args.compact,
                    hide_until_attached=hide_until_attached,
                    tombstone_follow_ms=(
                        100 if daemon_manager is not None else 500
                    ),
                    user_settings_store=getattr(context, "settings_store", None),
                    update_manager=update_manager,
                )
                try:
                    window.root.update_idletasks()
                    window.request_root.update_idletasks()
                except Exception:
                    pass
                if loading_feedback is not None:
                    loading_feedback.close()
                return _run_tk_window_session(
                    context,
                    args,
                    daemon_manager=daemon_manager,
                    existing_window=window,
                    close_context=False,
                    update_manager=update_manager,
                )
            finally:
                if loading_feedback is not None:
                    loading_feedback.close()
                update_manager.close()
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
                    hide_until_attached=False,
                )

            if startup.mode == DAEMON_STARTUP_CANCEL:
                _LOGGER.info("daemon_startup_cancelled")
                return 0
            if startup.mode == DAEMON_STARTUP_TK:
                if startup.launch_codex:
                    launch_codex_app(debugger=False)
                _LOGGER.info("daemon_startup_tk_selected")
                return run_hud_session(
                    _clone_args_with_display_mode(args, "tk"),
                    lock_already_held=True,
                    hide_until_attached=False,
                    daemon_manager=manager,
                )
            if startup.mode == DAEMON_STARTUP_QT:
                if startup.launch_codex:
                    launch_codex_app(debugger=False)
                _LOGGER.info("daemon_startup_qt_selected")
                return run_hud_session(
                    _clone_args_with_display_mode(args, "qt"),
                    lock_already_held=True,
                    hide_until_attached=False,
                    daemon_manager=manager,
                )
            startup_loading: HudLoadingFeedback | None = None
            if startup.mode == DAEMON_STARTUP_RENDERER and startup.launch_codex:
                startup_loading = _create_loading_feedback(
                    args,
                    title="正在启动 Renderer HUD",
                    message="正在以调试模式启动 Codex App...",
                ).start()
                launch_codex_app(debugger=True)
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
                        hide_until_attached=False,
                    )
                if force_renderer_retry:
                    exit_code = run_renderer_hud_session(
                        _clone_args_with_renderer_preference(args, True),
                        lock_already_held=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                    )
                else:
                    exit_code = run_hud_session(
                        _clone_args_with_display_mode(args, preferred_runtime_mode),
                        lock_already_held=True,
                        hide_until_attached=True,
                        daemon_manager=manager,
                        loading_feedback=startup_loading,
                    )
                startup_loading = None
                if exit_code == HUD_SWITCH_TO_QT:
                    preferred_runtime_mode = "qt"
                    force_renderer_retry = False
                    continue
                if exit_code == HUD_SWITCH_TO_TK:
                    preferred_runtime_mode = "tk"
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
    _enable_crash_diagnostics()
    parser = build_parser()
    args = parser.parse_args(argv)
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
    if args.renderer_hud is None:
        configured_mode = normalize_display_mode(
            args.hud_mode or UserConfigStore().load().display_mode
        )
        runtime_mode = effective_display_mode(configured_mode)
        args.runtime_hud_mode = runtime_mode
        args.standalone_hud_mode = runtime_mode if runtime_mode in {"qt", "tk"} else None
        args.renderer_hud = runtime_mode == "renderer"
    elif getattr(args, "hud_mode", None):
        runtime_mode = effective_display_mode(args.hud_mode)
        args.runtime_hud_mode = runtime_mode
        args.standalone_hud_mode = runtime_mode if runtime_mode in {"qt", "tk"} else None
        args.renderer_hud = runtime_mode == "renderer"
    else:
        args.runtime_hud_mode = "renderer" if args.renderer_hud else "tk"
        args.standalone_hud_mode = "tk" if not args.renderer_hud else None
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
