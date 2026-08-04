"""Detached loading/restart feedback helper lifecycle."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from threading import Event

from . import overlay_ipc
from .config import write_json_object
from .platforms.file_watcher import FileChangeWatcher, FileWatchSpec

LOADING_FEEDBACK_STALE_SECONDS = 20.0
WORK_OVERLAY_COMMAND_FALLBACK_POLL_SECONDS = 5.0


def _default_runtime_dir() -> Path:
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "codex-usage-hud"


def _process_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


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
        state_path = _default_runtime_dir() / f"loading-{os.getpid()}-{int(time.time() * 1000)}.json"
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


def _loading_feedback_enabled(args: object | None = None) -> bool:
    # ``--no-startup-prompt`` used to suppress a modal startup choice.  The
    # renderer-only flow no longer has that choice: this lightweight status
    # card is the only place where an already-running, non-CDP Codex asks for
    # an explicit restart.
    del args
    return sys.platform.startswith("win") or sys.platform == "darwin"


def _create_loading_feedback(
    args: object | None,
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


def cleanup_stale_loading_feedback_files(
    *,
    runtime_dir: Callable[[], Path] = _default_runtime_dir,
    process_exists: Callable[[int | None], bool] = _process_exists,
) -> None:
    runtime = runtime_dir()
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
        owner_alive = process_exists(owner_pid) if owner_pid is not None else False
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
            overlay_ipc.command_path(path).unlink()
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


__all__ = ["HudLoadingFeedback", "cleanup_stale_loading_feedback_files", "run_loading_feedback_helper"]
