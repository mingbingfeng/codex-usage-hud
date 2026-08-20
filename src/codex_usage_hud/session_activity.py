"""Windows session-lock gating for the Renderer HUD runtime."""

from __future__ import annotations

import ctypes
import sys
import threading
from collections.abc import Callable


_DESKTOP_SWITCHDESKTOP = 0x0100
_UOI_NAME = 2


def windows_session_locked() -> bool:
    """Return whether the interactive Windows desktop is unavailable.

    ``OpenInputDesktop`` is available without a GUI framework and fails while
    Windows is displaying the secure lock screen.  Non-Windows runtimes are
    always considered interactive so Renderer HUD behavior stays unchanged.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        close_desktop = user32.CloseDesktop
        close_desktop.argtypes = (ctypes.c_void_p,)
        close_desktop.restype = ctypes.c_bool
        open_input_desktop = user32.OpenInputDesktop
        open_input_desktop.argtypes = (
            ctypes.c_uint32,
            ctypes.c_bool,
            ctypes.c_uint32,
        )
        open_input_desktop.restype = ctypes.c_void_p
        desktop = open_input_desktop(0, False, _DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        try:
            get_user_object_information = user32.GetUserObjectInformationW
            get_user_object_information.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
            )
            get_user_object_information.restype = ctypes.c_bool
            required = ctypes.c_uint32(0)
            get_user_object_information(
                desktop, _UOI_NAME, None, 0, ctypes.byref(required)
            )
            buffer = ctypes.create_unicode_buffer(max(2, required.value + 1))
            if not get_user_object_information(
                desktop,
                _UOI_NAME,
                buffer,
                ctypes.sizeof(buffer),
                ctypes.byref(required),
            ):
                return False
            return buffer.value.casefold() == "winlogon"
        finally:
            close_desktop(desktop)
    except Exception:
        # A detection failure must not turn a normal desktop session into a
        # permanent HUD outage.
        return False


class RendererActivityGate:
    """Coordinates lock/unlock transitions with the Renderer event loop."""

    def __init__(self) -> None:
        self._suspended = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()

    def suspend(self) -> bool:
        if self._suspended.is_set():
            return False
        self._suspended.set()
        self._resumed.clear()
        return True

    def resume(self) -> bool:
        if not self._suspended.is_set():
            return False
        self._suspended.clear()
        self._resumed.set()
        return True

    def is_suspended(self) -> bool:
        return self._suspended.is_set()

    def wait_until_resumed(self, timeout_seconds: float = 30.0) -> bool:
        return self._resumed.wait(max(0.05, float(timeout_seconds)))


class WindowsSessionLockMonitor:
    """Observe lock transitions with conservative polling outside the renderer.

    Windows delivers lock notifications only to a GUI message window.  The HUD
    has no native window in Renderer mode, so this monitor uses the input
    desktop availability probe once per second.  It never touches Codex or
    CDP; callbacks only transition the local activity gate.
    """

    def __init__(
        self,
        *,
        on_lock: Callable[[], None],
        on_unlock: Callable[[], None],
        locked_probe: Callable[[], bool] = windows_session_locked,
        poll_seconds: float = 1.0,
    ) -> None:
        self._on_lock = on_lock
        self._on_unlock = on_unlock
        self._locked_probe = locked_probe
        self._poll_seconds = max(0.2, float(poll_seconds))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._locked: bool | None = None

    def start(self) -> None:
        if not sys.platform.startswith("win") or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="codex-hud-session-lock-monitor",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self._poll_seconds + 0.2)
        self._thread = None

    def poll_once(self) -> None:
        locked = bool(self._locked_probe())
        previous = self._locked
        self._locked = locked
        if previous is None:
            if locked:
                self._on_lock()
            return
        if locked == previous:
            return
        if locked:
            self._on_lock()
        else:
            self._on_unlock()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop_event.wait(self._poll_seconds)


__all__ = [
    "RendererActivityGate",
    "WindowsSessionLockMonitor",
    "windows_session_locked",
]
