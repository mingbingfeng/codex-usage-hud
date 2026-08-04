"""Cross-process PID-file and native mutex ownership for one HUD instance."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
import signal
import sys
import time

from .runtime_paths import HUD_LOCK_FILENAME, hud_lock_path


HUD_MUTEX_NAME = "Local\\codex_usage_hud_single_instance"
ERROR_ALREADY_EXISTS = 183
STILL_ACTIVE = 259
_LOGGER = logging.getLogger(__name__)


class HudAlreadyRunningError(RuntimeError):
    """Raised when another HUD instance owns the local runtime lock."""


def read_pid(path: Path) -> int | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def process_exists(pid: int) -> bool:
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


def terminate_process(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def stop_recorded_instance(
    path: Path | None = None,
    *,
    before_stop: Callable[[], object] | None = None,
    process_probe: Callable[[int], bool] = process_exists,
    terminate: Callable[[int], bool] = terminate_process,
    sleep: Callable[[float], None] = time.sleep,
    wait_attempts: int = 20,
) -> str:
    """Stop the process in a HUD PID file without importing higher layers."""
    lock_path = Path(path) if path is not None else hud_lock_path()
    pid = read_pid(lock_path)
    if before_stop is not None:
        try:
            before_stop()
        except Exception:
            _LOGGER.debug("instance_before_stop_failed", exc_info=True)
    if pid is None:
        try:
            lock_path.unlink()
        except OSError:
            pass
        return "No running codex-usage-hud instance was recorded."
    if not process_probe(pid):
        try:
            lock_path.unlink()
        except OSError:
            pass
        return f"Removed stale codex-usage-hud lock for PID {pid}."
    if not terminate(pid):
        return f"Unable to stop codex-usage-hud PID {pid}."
    for _ in range(max(0, int(wait_attempts))):
        if not process_probe(pid):
            try:
                lock_path.unlink()
            except OSError:
                pass
            return f"Stopped codex-usage-hud PID {pid}."
        sleep(0.1)
    return f"Sent stop signal to codex-usage-hud PID {pid}."


class HudInstanceLock:
    """Own a PID file and, on Windows, a named kernel mutex."""

    def __init__(
        self,
        path: Path | None = None,
        mutex_name: str | None = None,
        *,
        pid_provider: Callable[[], int] = os.getpid,
        process_probe: Callable[[int], bool] = process_exists,
    ) -> None:
        self.path = Path(path) if path is not None else hud_lock_path()
        self.mutex_name = mutex_name or HUD_MUTEX_NAME
        self._pid_provider = pid_provider
        self._process_probe = process_probe
        self._owned = False
        self._mutex_handle: int | None = None

    @property
    def owned(self) -> bool:
        return self._owned

    def acquire(self) -> None:
        if self._owned:
            return
        self._mutex_handle = self._acquire_native_mutex()
        current_pid = int(self._pid_provider())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existing_pid = read_pid(self.path)
            if existing_pid is not None and self._process_probe(existing_pid):
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
                raise HudAlreadyRunningError(
                    "codex-usage-hud lock already exists. "
                    "Run `python -m codex_usage_hud --stop` to clear it."
                ) from exc
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(current_pid))
            except Exception:
                try:
                    self.path.unlink()
                except OSError:
                    pass
                raise
        except Exception:
            self._release_native_mutex()
            raise
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
        if read_pid(self.path) == int(self._pid_provider()):
            try:
                self.path.unlink()
            except OSError:
                pass
        self._owned = False
        self._release_native_mutex()

    def __enter__(self) -> HudInstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.release()


__all__ = [
    "ERROR_ALREADY_EXISTS",
    "HUD_LOCK_FILENAME",
    "HUD_MUTEX_NAME",
    "STILL_ACTIVE",
    "HudAlreadyRunningError",
    "HudInstanceLock",
    "process_exists",
    "read_pid",
    "stop_recorded_instance",
    "terminate_process",
]
