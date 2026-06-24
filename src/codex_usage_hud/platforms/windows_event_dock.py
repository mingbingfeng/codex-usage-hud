"""Event-driven Windows docking helpers for the Codex HUD.

The classes in this module intentionally stay on the project's existing
standard-library ctypes path.  They provide three pieces that the Qt HUD can
compose:

* owned-window binding through GWLP_HWNDPARENT, without WS_EX_TOPMOST
* SetWinEventHook based window movement/visibility notifications
* UI Automation BoundingRectangle property-change notifications
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import ctypes
import logging
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any
import uuid

from .windows_tracker import (
    CodexWindowTracker,
    PhysicalRect,
    STATUS_CLOAKED,
    STATUS_HIDDEN,
    STATUS_MINIMIZED,
    STATUS_VISIBLE,
    _UiaProbe,
)

if not sys.platform.startswith("win"):  # pragma: no cover - Windows-only module.
    raise ImportError("windows_event_dock is only available on Windows")


_logger = logging.getLogger("codex_usage_hud.windows_event_dock")
_logger.addHandler(logging.NullHandler())

_GWLP_HWNDPARENT = -8
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008

_HWND_TOP = 0
_HWND_NOTOPMOST = -2

_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200
_SWP_ASYNCWINDOWPOS = 0x4000

_GA_ROOT = 2
_OBJID_WINDOW = 0
_OBJID_CLIENT = -4

_EVENT_SYSTEM_FOREGROUND = 0x0003
_EVENT_SYSTEM_MINIMIZESTART = 0x0016
_EVENT_SYSTEM_MINIMIZEEND = 0x0017
_EVENT_OBJECT_CREATE = 0x8000
_EVENT_OBJECT_DESTROY = 0x8001
_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_HIDE = 0x8003
_EVENT_OBJECT_REORDER = 0x8004
_EVENT_OBJECT_LOCATIONCHANGE = 0x800B
_EVENT_OBJECT_NAMECHANGE = 0x800C

_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002

_PM_REMOVE = 0x0001
_QS_ALLINPUT = 0x04FF
_MWMO_INPUTAVAILABLE = 0x0004
_MWMO_ALERTABLE = 0x0002
_WM_NULL = 0x0000

_TREE_SCOPE_ELEMENT = 0x1
_TREE_SCOPE_DESCENDANTS = 0x4
_TREE_SCOPE_SUBTREE = _TREE_SCOPE_ELEMENT | _TREE_SCOPE_DESCENDANTS
_UIA_BOUNDING_RECTANGLE_PROPERTY_ID = 30001
_UIA_NAME_PROPERTY_ID = 30005
_UIA_IS_OFFSCREEN_PROPERTY_ID = 30022

_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1
_E_NOINTERFACE = -2147467262


def _event_name(event_id: int) -> str:
    return {
        _EVENT_SYSTEM_FOREGROUND: "foreground",
        _EVENT_SYSTEM_MINIMIZESTART: "minimize-start",
        _EVENT_SYSTEM_MINIMIZEEND: "minimize-end",
        _EVENT_OBJECT_CREATE: "create",
        _EVENT_OBJECT_DESTROY: "destroy",
        _EVENT_OBJECT_SHOW: "show",
        _EVENT_OBJECT_HIDE: "hide",
        _EVENT_OBJECT_REORDER: "reorder",
        _EVENT_OBJECT_LOCATIONCHANGE: "location",
        _EVENT_OBJECT_NAMECHANGE: "name",
    }.get(int(event_id), f"event-{int(event_id)}")


@dataclass(frozen=True)
class EventDockSnapshot:
    """Last known event-driven Codex docking state."""

    status: str
    hwnd: int = 0
    window_rect: PhysicalRect | None = None
    reason: str = ""

    @property
    def visible(self) -> bool:
        return self.status == STATUS_VISIBLE and self.hwnd != 0


class NativeHudWindowManager:
    """Win32 owner and geometry operations for HUD top-level windows."""

    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._long_type = ctypes.c_ssize_t
        self._get_window_long = getattr(self.user32, "GetWindowLongPtrW", None)
        self._set_window_long = getattr(self.user32, "SetWindowLongPtrW", None)
        if self._get_window_long is None or self._set_window_long is None:
            self._get_window_long = self.user32.GetWindowLongW
            self._set_window_long = self.user32.SetWindowLongW
            self._long_type = ctypes.c_long
        self._configure_api()

    def _configure_api(self) -> None:
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get_window_long.restype = self._long_type
        self._set_window_long.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            self._long_type,
        ]
        self._set_window_long.restype = self._long_type

    def valid_window(self, hwnd: int) -> bool:
        return bool(hwnd and self.user32.IsWindow(wintypes.HWND(int(hwnd))))

    def native_root(self, hwnd: int) -> int:
        """Return the top-level native HWND for toolkit-owned child HWNDs."""
        hwnd = int(hwnd or 0)
        if not hwnd:
            return 0
        try:
            root = int(self.user32.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT) or 0)
        except Exception:
            root = 0
        return root or hwnd

    def owner_for(self, hwnd: int) -> int:
        hwnd = self.native_root(hwnd)
        if not self.valid_window(hwnd):
            return 0
        try:
            return int(self._get_window_long(wintypes.HWND(hwnd), _GWLP_HWNDPARENT) or 0)
        except Exception:
            return 0

    def bind_owner(self, hud_hwnd: int, owner_hwnd: int) -> bool:
        """Make ``hud_hwnd`` an owned top-level window of ``owner_hwnd``."""
        raw_hud_hwnd = int(hud_hwnd or 0)
        raw_owner_hwnd = int(owner_hwnd or 0)
        hud_hwnd = self.native_root(raw_hud_hwnd)
        owner_hwnd = self.native_root(raw_owner_hwnd)
        if not self.valid_window(hud_hwnd) or not self.valid_window(owner_hwnd):
            return False
        if hud_hwnd == owner_hwnd:
            return False

        current_owner = self.owner_for(hud_hwnd)
        if current_owner != owner_hwnd:
            ctypes.set_last_error(0)
            previous = int(
                self._set_window_long(
                    wintypes.HWND(hud_hwnd),
                    _GWLP_HWNDPARENT,
                    self._long_type(owner_hwnd),
                )
                or 0
            )
            error = ctypes.get_last_error()
            if previous == 0 and error:
                _logger.debug(
                    "owner_bind_failed hud=%s owner=%s error=%s",
                    hud_hwnd,
                    owner_hwnd,
                    error,
                )
                return False
            _logger.info(
                "owner_bound raw_hud=%s hud=%s raw_owner=%s owner=%s",
                raw_hud_hwnd,
                hud_hwnd,
                raw_owner_hwnd,
                owner_hwnd,
            )

        self.clear_topmost(hud_hwnd)
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
        return bool(
            self.user32.SetWindowPos(
                wintypes.HWND(hud_hwnd),
                wintypes.HWND(_HWND_TOP),
                0,
                0,
                0,
                0,
                flags,
            )
        )

    def unbind_owner(self, hud_hwnd: int) -> bool:
        hud_hwnd = self.native_root(hud_hwnd)
        if not self.valid_window(hud_hwnd):
            return False
        ctypes.set_last_error(0)
        self._set_window_long(
            wintypes.HWND(hud_hwnd),
            _GWLP_HWNDPARENT,
            self._long_type(0),
        )
        return ctypes.get_last_error() == 0

    def clear_topmost(self, hwnd: int) -> bool:
        hwnd = self.native_root(hwnd)
        if not self.valid_window(hwnd):
            return False
        try:
            exstyle = int(self._get_window_long(wintypes.HWND(hwnd), _GWL_EXSTYLE) or 0)
        except Exception:
            exstyle = 0
        if not (exstyle & _WS_EX_TOPMOST):
            return True
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
        return bool(
            self.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(_HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                flags,
            )
        )

    def set_window_pos(self, hwnd: int, x: int, y: int, width: int, height: int) -> bool:
        hwnd = self.native_root(hwnd)
        if not self.valid_window(hwnd):
            return False
        flags = _SWP_NOACTIVATE | _SWP_NOOWNERZORDER | _SWP_ASYNCWINDOWPOS
        return bool(
            self.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(_HWND_TOP),
                int(x),
                int(y),
                max(1, int(width)),
                max(1, int(height)),
                flags,
            )
        )


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "_GUID":
        parsed = uuid.UUID(value)
        fields = parsed.fields
        node = int(fields[5]).to_bytes(6, "big")
        data4 = (ctypes.c_ubyte * 8)(fields[3], fields[4], *node)
        return cls(fields[0], fields[1], fields[2], data4)


def _same_guid(left: _GUID, right: _GUID) -> bool:
    return bytes(ctypes.string_at(ctypes.byref(left), ctypes.sizeof(_GUID))) == bytes(
        ctypes.string_at(ctypes.byref(right), ctypes.sizeof(_GUID))
    )


class _VariantValue(ctypes.Union):
    _fields_ = [
        ("lVal", ctypes.c_long),
        ("boolVal", ctypes.c_short),
        ("dblVal", ctypes.c_double),
        ("bstrVal", ctypes.c_void_p),
        ("parray", ctypes.c_void_p),
        ("pdispVal", ctypes.c_void_p),
    ]


class _Variant(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("value", _VariantValue),
    ]


_IID_IUNKNOWN = _GUID.from_string("{00000000-0000-0000-c000-000000000046}")
_IID_IUIAUTOMATION_PROPERTY_CHANGED_EVENT_HANDLER = _GUID.from_string(
    "{40cd37d4-c756-4b0c-8c6f-bddfeeb13b50}"
)

_UiaQueryInterfaceProc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(ctypes.c_void_p),
)
_UiaAddRefProc = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaReleaseProc = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaHandlePropertyChangedProc = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    _Variant,
)


class _UiaPropertyChangedEventHandlerVTable(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _UiaQueryInterfaceProc),
        ("AddRef", _UiaAddRefProc),
        ("Release", _UiaReleaseProc),
        ("HandlePropertyChangedEvent", _UiaHandlePropertyChangedProc),
    ]


class _UiaPropertyChangedEventHandlerObject(ctypes.Structure):
    _fields_ = [
        ("lpVtbl", ctypes.POINTER(_UiaPropertyChangedEventHandlerVTable)),
    ]


class _UiaPropertyChangedEventHandler:
    """Tiny COM object implementing IUIAutomationPropertyChangedEventHandler."""

    def __init__(self, callback: Callable[[int, int], None]) -> None:
        self.callback = callback
        self._ref_count = 1
        self._query_interface = _UiaQueryInterfaceProc(self._query_interface_impl)
        self._add_ref = _UiaAddRefProc(self._add_ref_impl)
        self._release = _UiaReleaseProc(self._release_impl)
        self._handle_event = _UiaHandlePropertyChangedProc(self._handle_event_impl)
        self._vtable = _UiaPropertyChangedEventHandlerVTable(
            self._query_interface,
            self._add_ref,
            self._release,
            self._handle_event,
        )
        self._object = _UiaPropertyChangedEventHandlerObject()
        self._object.lpVtbl = ctypes.pointer(self._vtable)
        self.ptr = ctypes.cast(ctypes.pointer(self._object), ctypes.c_void_p)

    def _query_interface_impl(
        self,
        this: int,
        riid: ctypes.POINTER(_GUID),
        out: ctypes.POINTER(ctypes.c_void_p),
    ) -> int:
        if not out:
            return _E_NOINTERFACE
        requested = riid.contents
        if _same_guid(requested, _IID_IUNKNOWN) or _same_guid(
            requested,
            _IID_IUIAUTOMATION_PROPERTY_CHANGED_EVENT_HANDLER,
        ):
            out[0] = ctypes.c_void_p(this)
            self._add_ref_impl(this)
            return _S_OK
        out[0] = ctypes.c_void_p()
        return _E_NOINTERFACE

    def _add_ref_impl(self, _this: int) -> int:
        self._ref_count += 1
        return self._ref_count

    def _release_impl(self, _this: int) -> int:
        self._ref_count = max(1, self._ref_count - 1)
        return self._ref_count

    def _handle_event_impl(
        self,
        _this: int,
        sender: int,
        property_id: int,
        _new_value: _Variant,
    ) -> int:
        try:
            self.callback(int(sender or 0), int(property_id))
        except Exception:
            _logger.debug("uia_property_callback_failed", exc_info=True)
        return _S_OK


class _WinMessagePumpMixin:
    """Small user32 message-pump helper shared by hook threads."""

    _user32: Any
    _thread_id: int

    def _configure_message_api(self) -> None:
        user32 = self._user32
        user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostThreadMessageW.restype = wintypes.BOOL

    def _pump_messages(self, stop_event: threading.Event) -> None:
        msg = wintypes.MSG()
        while not stop_event.is_set():
            self._user32.MsgWaitForMultipleObjectsEx(
                0,
                None,
                250,
                _QS_ALLINPUT,
                _MWMO_INPUTAVAILABLE | _MWMO_ALERTABLE,
            )
            while self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, _PM_REMOVE):
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))

    def _wake_message_loop(self) -> None:
        thread_id = int(getattr(self, "_thread_id", 0) or 0)
        if not thread_id:
            return
        try:
            self._user32.PostThreadMessageW(
                wintypes.DWORD(thread_id),
                _WM_NULL,
                0,
                0,
            )
        except Exception:
            return


class _WinEventHookThread(_WinMessagePumpMixin):
    _callback_type = ctypes.WINFUNCTYPE(
        None,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.HWND,
        ctypes.c_long,
        ctypes.c_long,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    def __init__(
        self,
        tracker: CodexWindowTracker,
        on_event: Callable[[str], None],
        on_hwnd: Callable[[int], None],
    ) -> None:
        self.tracker = tracker
        self.on_event = on_event
        self.on_hwnd = on_hwnd
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._hooks: list[int] = []
        self._callback = None
        self._target_hwnd = 0
        self._target_pid = 0
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = 0
        self._configure_api()

    def _configure_api(self) -> None:
        self._configure_message_api()
        self._user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            self._callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._user32.SetWinEventHook.restype = ctypes.c_void_p
        self._user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        self._user32.UnhookWinEvent.restype = wintypes.BOOL
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="codex-hud-win-events",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_message_loop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def update_target(self, hwnd: int) -> None:
        hwnd = int(hwnd or 0)
        if hwnd == self._target_hwnd:
            return
        self._target_hwnd = hwnd
        self._target_pid = self._pid_for_hwnd(hwnd)

    def _run(self) -> None:
        self._thread_id = int(self._kernel32.GetCurrentThreadId() or 0)
        self._callback = self._callback_type(self._handle_event)
        event_ids = (
            _EVENT_SYSTEM_FOREGROUND,
            _EVENT_SYSTEM_MINIMIZESTART,
            _EVENT_SYSTEM_MINIMIZEEND,
            _EVENT_OBJECT_CREATE,
            _EVENT_OBJECT_DESTROY,
            _EVENT_OBJECT_SHOW,
            _EVENT_OBJECT_HIDE,
            _EVENT_OBJECT_REORDER,
            _EVENT_OBJECT_LOCATIONCHANGE,
            _EVENT_OBJECT_NAMECHANGE,
        )
        try:
            for event_id in event_ids:
                hook = int(
                    self._user32.SetWinEventHook(
                        event_id,
                        event_id,
                        0,
                        self._callback,
                        0,
                        0,
                        _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
                    )
                    or 0
                )
                if hook:
                    self._hooks.append(hook)
            if not self._hooks:
                self._callback = None
                self.on_event("win-event-hook-failed")
                return
            self.on_event("win-event-hook-started")
            self._pump_messages(self._stop_event)
        finally:
            for hook in self._hooks:
                try:
                    self._user32.UnhookWinEvent(ctypes.c_void_p(hook))
                except Exception:
                    pass
            self._hooks.clear()
            self._callback = None
            self._thread_id = 0

    def _handle_event(
        self,
        _hook: int,
        event_id: int,
        hwnd: int,
        object_id: int,
        _child_id: int,
        _thread_id: int,
        _event_time: int,
    ) -> None:
        hwnd = int(hwnd or 0)
        event_id = int(event_id)
        object_id = int(object_id)
        root = self._root_for_hwnd(hwnd)
        if event_id == _EVENT_SYSTEM_FOREGROUND:
            if self._is_codex_root(root):
                self.update_target(root)
                self.on_hwnd(root)
                self.on_event("foreground")
            return

        target = int(self._target_hwnd or 0)
        if not target:
            if root and self._is_codex_root(root):
                self.update_target(root)
                self.on_hwnd(root)
                self.on_event(_event_name(event_id))
            return
        if root != target and hwnd != target:
            return
        if object_id not in {_OBJID_WINDOW, _OBJID_CLIENT}:
            return
        self.on_event(_event_name(event_id))

    def _root_for_hwnd(self, hwnd: int) -> int:
        if not hwnd:
            return 0
        try:
            root = int(self._user32.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT) or 0)
        except Exception:
            root = 0
        return root or int(hwnd)

    def _pid_for_hwnd(self, hwnd: int) -> int:
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        try:
            self._user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        except Exception:
            return 0
        return int(pid.value or 0)

    def _is_codex_root(self, hwnd: int) -> bool:
        if not hwnd:
            return False
        if int(hwnd) == int(self._target_hwnd or 0):
            return True
        pid = self._pid_for_hwnd(hwnd)
        if pid and pid == self._target_pid:
            return True
        try:
            candidate = self.tracker._candidate_from_hwnd(int(hwnd), verify_codex=True)
        except Exception:
            candidate = None
        return candidate is not None


class _UiaBoundingRectangleThread(_WinMessagePumpMixin):
    def __init__(
        self,
        hwnd: int,
        on_event: Callable[[str], None],
        properties: Iterable[int] | None = None,
    ) -> None:
        self.hwnd = int(hwnd or 0)
        self.on_event = on_event
        self.properties = tuple(
            int(item)
            for item in (
                properties
                or (
                    _UIA_BOUNDING_RECTANGLE_PROPERTY_ID,
                    _UIA_NAME_PROPERTY_ID,
                    _UIA_IS_OFFSCREEN_PROPERTY_ID,
                )
            )
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = 0
        self._handler: _UiaPropertyChangedEventHandler | None = None
        self._configure_api()

    def _configure_api(self) -> None:
        self._configure_message_api()
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        # COM is initialized by _UiaProbe on this worker thread.  Keeping that
        # path single-owned avoids an imbalanced CoInitializeEx/CoUninitialize
        # pair when _UiaProbe caches automation in thread-local storage.

    def start(self) -> bool:
        if not self.hwnd:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="codex-hud-uia-bounds",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_message_loop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self) -> None:
        self._thread_id = int(self._kernel32.GetCurrentThreadId() or 0)
        probe = _UiaProbe()
        automation = 0
        root = 0
        handler_ptr = 0
        try:
            automation = probe._automation_for_thread()
            if not automation:
                self.on_event("uia-automation-unavailable")
                return
            root = probe._element_from_handle(automation, self.hwnd)
            if not root:
                self.on_event("uia-root-unavailable")
                return
            self._handler = _UiaPropertyChangedEventHandler(self._handle_property_changed)
            handler_ptr = int(self._handler.ptr.value or 0)
            if not self._add_property_handler(probe, automation, root, handler_ptr):
                self.on_event("uia-property-hook-failed")
                return
            self.on_event("uia-property-hook-started")
            self._pump_messages(self._stop_event)
        finally:
            if automation and root and handler_ptr:
                self._remove_property_handler(probe, automation, root, handler_ptr)
            if root:
                probe._release(root)
            self._handler = None
            self._thread_id = 0

    def _add_property_handler(
        self,
        probe: _UiaProbe,
        automation: int,
        root: int,
        handler_ptr: int,
    ) -> bool:
        props = (ctypes.c_int * len(self.properties))(*self.properties)
        func = probe._method(
            automation,
            34,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
        )
        try:
            hr = int(
                func(
                    automation,
                    ctypes.c_void_p(root),
                    _TREE_SCOPE_SUBTREE,
                    ctypes.c_void_p(),
                    ctypes.c_void_p(handler_ptr),
                    props,
                    len(self.properties),
                )
            )
        except Exception:
            _logger.debug("uia_add_property_handler_failed", exc_info=True)
            return False
        return hr >= 0

    def _remove_property_handler(
        self,
        probe: _UiaProbe,
        automation: int,
        root: int,
        handler_ptr: int,
    ) -> None:
        func = probe._method(
            automation,
            36,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        try:
            func(
                automation,
                ctypes.c_void_p(root),
                ctypes.c_void_p(handler_ptr),
            )
        except Exception:
            return

    def _handle_property_changed(self, _sender: int, property_id: int) -> None:
        if int(property_id) == _UIA_BOUNDING_RECTANGLE_PROPERTY_ID:
            self.on_event("uia-bounding-rectangle")
            return
        if int(property_id) == _UIA_NAME_PROPERTY_ID:
            self.on_event("uia-name")
            return
        if int(property_id) == _UIA_IS_OFFSCREEN_PROPERTY_ID:
            self.on_event("uia-offscreen")
            return
        self.on_event(f"uia-property-{int(property_id)}")


class WindowsEventDockBridge:
    """Coordinate native event hooks, UIA property hooks, and HUD HWND ownership."""

    def __init__(
        self,
        *,
        on_event: Callable[[str], None],
        hud_hwnds: Callable[[], set[int]],
        tracker: CodexWindowTracker | None = None,
        enable_uia_events: bool | None = None,
        enable_owner_binding: bool | None = None,
    ) -> None:
        self.on_event = on_event
        self.hud_hwnds = hud_hwnds
        self.tracker = tracker or CodexWindowTracker(enable_uia=False)
        self.window_manager = NativeHudWindowManager()
        self._enable_uia_events = (
            event_dock_uia_enabled_from_env(default=False)
            if enable_uia_events is None
            else bool(enable_uia_events)
        )
        self._enable_owner_binding = (
            event_dock_owner_binding_enabled_from_env(default=True)
            if enable_owner_binding is None
            else bool(enable_owner_binding)
        )
        self._lock = threading.RLock()
        self._started = False
        self._owner_hwnd = 0
        self._cached_hud_hwnds: set[int] = set()
        self._snapshot = EventDockSnapshot(status=STATUS_HIDDEN, reason="not-started")
        self._win_events = _WinEventHookThread(
            self.tracker,
            self._handle_native_event,
            self._handle_hwnd_changed,
        )
        self._uia_thread: _UiaBoundingRectangleThread | None = None

    @property
    def active(self) -> bool:
        return self._started

    @property
    def owner_binding_active(self) -> bool:
        return self._started and self._enable_owner_binding

    @property
    def snapshot(self) -> EventDockSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> bool:
        with self._lock:
            if self._started:
                return True
            self._started = True
            self._cached_hud_hwnds = self._safe_hud_hwnds_from_provider()
        self.refresh_snapshot("start")
        self._win_events.start()
        snapshot = self.snapshot
        if snapshot.hwnd and self._enable_uia_events:
            self._restart_uia(snapshot.hwnd)
        return True

    def stop(self) -> None:
        with self._lock:
            self._started = False
        self._win_events.stop()
        self._stop_uia()
        with self._lock:
            hud_hwnds = set(self._cached_hud_hwnds)
            self._cached_hud_hwnds.clear()
        if self._enable_owner_binding:
            for hwnd in hud_hwnds:
                self.window_manager.unbind_owner(hwnd)

    def bind_to_owner(self, owner_hwnd: int, hud_hwnds: Iterable[int] | None = None) -> None:
        owner_hwnd = int(owner_hwnd or 0)
        if not owner_hwnd:
            return
        if hud_hwnds is not None:
            with self._lock:
                self._cached_hud_hwnds = set(int(item) for item in hud_hwnds if int(item or 0))
        with self._lock:
            self._owner_hwnd = owner_hwnd
        self._win_events.update_target(owner_hwnd)
        if self._enable_uia_events:
            self._restart_uia(owner_hwnd)
        self._bind_cached_to_owner(owner_hwnd)

    def _bind_cached_to_owner(self, owner_hwnd: int) -> None:
        if not self._enable_owner_binding:
            return
        with self._lock:
            hud_hwnds = set(self._cached_hud_hwnds)
        for hud_hwnd in hud_hwnds:
            self.window_manager.bind_owner(hud_hwnd, owner_hwnd)

    def set_hud_geometry(
        self,
        hwnd: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        return self.window_manager.set_window_pos(hwnd, x, y, width, height)

    def refresh_snapshot(self, reason: str) -> EventDockSnapshot:
        try:
            base = self.tracker.get_window_snapshot()
        except Exception as exc:
            _logger.debug("event_dock_snapshot_failed reason=%s", reason, exc_info=True)
            snapshot = EventDockSnapshot(status=STATUS_HIDDEN, reason=str(exc))
        else:
            snapshot = EventDockSnapshot(
                status=base.status,
                hwnd=int(base.hwnd or 0),
                window_rect=base.window_rect,
                reason=reason if not base.reason else base.reason,
            )
        with self._lock:
            previous_hwnd = self._snapshot.hwnd
            self._snapshot = snapshot
        if snapshot.hwnd and snapshot.hwnd != previous_hwnd:
            self._handle_hwnd_changed(snapshot.hwnd)
        if snapshot.visible and self._enable_owner_binding:
            self._bind_cached_to_owner(snapshot.hwnd)
        return snapshot

    def _handle_native_event(self, reason: str) -> None:
        snapshot = self.refresh_snapshot(reason)
        if snapshot.status in {STATUS_MINIMIZED, STATUS_HIDDEN, STATUS_CLOAKED}:
            self.on_event(reason)
            return
        if snapshot.visible:
            self.on_event(reason)

    def _handle_hwnd_changed(self, hwnd: int) -> None:
        hwnd = int(hwnd or 0)
        if not hwnd:
            return
        with self._lock:
            if self._owner_hwnd == hwnd:
                pass
            self._owner_hwnd = hwnd
        self._win_events.update_target(hwnd)
        if self._enable_uia_events:
            self._restart_uia(hwnd)

    def _restart_uia(self, hwnd: int) -> None:
        if not hwnd:
            return
        current = self._uia_thread
        if current is not None and current.hwnd == int(hwnd):
            return
        self._stop_uia()
        try:
            self._uia_thread = _UiaBoundingRectangleThread(
                int(hwnd),
                self._handle_native_event,
            )
            self._uia_thread.start()
        except Exception:
            _logger.debug("uia_thread_start_failed hwnd=%s", hwnd, exc_info=True)
            self._uia_thread = None

    def _stop_uia(self) -> None:
        current = self._uia_thread
        self._uia_thread = None
        if current is not None:
            current.stop()

    def _safe_hud_hwnds_from_provider(self) -> set[int]:
        try:
            return set(int(item) for item in self.hud_hwnds() if int(item or 0))
        except Exception:
            return set()


def event_dock_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get("CODEX_USAGE_HUD_EVENT_DOCK")
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def event_dock_uia_enabled_from_env(default: bool = False) -> bool:
    value = os.environ.get("CODEX_USAGE_HUD_EVENT_DOCK_UIA")
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def event_dock_owner_binding_enabled_from_env(default: bool = True) -> bool:
    value = os.environ.get("CODEX_USAGE_HUD_EVENT_DOCK_OWNER")
    if value is None:
        return bool(default)
    return value.strip().lower() not in {"0", "false", "no", "off", ""}
