"""Windows-specific Codex platform helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import uuid

from .base import BasePlatform


_OBJID_CLIENT = 0xFFFFFFFC
_CHILDID_SELF = 0
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1
_VT_I4 = 3
_VT_BSTR = 8
_ROLE_SYSTEM_STATICTEXT = 0x29
_ROLE_SYSTEM_TEXT = 0x2A
_ROLE_SYSTEM_PUSHBUTTON = 0x2B
_TEXT_ROLES = {_ROLE_SYSTEM_STATICTEXT, _ROLE_SYSTEM_TEXT}
_IGNORED_TITLES = {"设置", "对话", "暂无聊天"}
_MAX_ACCESSIBLE_NODES = 1600


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


class _VariantValue(ctypes.Union):
    _fields_ = [
        ("lVal", ctypes.c_long),
        ("bstrVal", ctypes.c_void_p),
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


def _variant_i4(value: int) -> _Variant:
    variant = _Variant()
    variant.vt = _VT_I4
    variant.lVal = int(value)
    return variant


def _succeeded(hr: int) -> bool:
    return int(hr) >= 0


def _clean_title(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return "" if text in _IGNORED_TITLES else text


class _MsaaTitleProbe:
    """Small ctypes/MSAA probe that replaces the old PowerShell UIA sidecar."""

    _iid_iaccessible = _GUID.from_string("{618736e0-3c3d-11cf-810c-00aa00389b71}")

    def __init__(self) -> None:
        self._local = threading.local()
        self._ole32 = ctypes.oledll.ole32
        self._oleacc = ctypes.oledll.oleacc
        self._oleaut32 = ctypes.oledll.oleaut32
        self._oleacc.AccessibleObjectFromWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._oleacc.AccessibleObjectFromWindow.restype = ctypes.c_long
        self._oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut32.SysFreeString.restype = None
        self._oleaut32.VariantClear.argtypes = [ctypes.POINTER(_Variant)]
        self._oleaut32.VariantClear.restype = ctypes.c_long

    def conversation_title(self, hwnd: int) -> str | None:
        if not hwnd or not self._init_com_for_thread():
            return None

        accessible = ctypes.c_void_p()
        hr = self._oleacc.AccessibleObjectFromWindow(
            ctypes.c_void_p(hwnd),
            _OBJID_CLIENT,
            ctypes.byref(self._iid_iaccessible),
            ctypes.byref(accessible),
        )
        if not _succeeded(hr) or not accessible.value:
            return None

        try:
            return self._title_from_tree(accessible.value)
        finally:
            self._release(accessible.value)

    def _init_com_for_thread(self) -> bool:
        if getattr(self._local, "com_ready", False):
            return True
        hr = int(self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
        if hr in {_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE} or hr >= 0:
            self._local.com_ready = True
            return True
        return False

    def _title_from_tree(self, root: int) -> str | None:
        last_text = ""
        visited = 0
        stack: list[tuple[int, int, bool]] = [(root, _CHILDID_SELF, False)]

        while stack and visited < _MAX_ACCESSIBLE_NODES:
            ptr, child_id, release_after = stack.pop()
            visited += 1
            try:
                name = _clean_title(self._acc_name(ptr, child_id))
                role = self._acc_role(ptr, child_id)
                if name == "对话操作":
                    return last_text or None
                if role in _TEXT_ROLES and name:
                    last_text = name

                children = self._children(ptr, child_id)
                for child_ptr, nested_child_id in reversed(children):
                    stack.append((child_ptr, nested_child_id, child_ptr != ptr))
            finally:
                if release_after:
                    self._release(ptr)

        return None

    def _children(self, ptr: int, child_id: int) -> list[tuple[int, int]]:
        if child_id != _CHILDID_SELF:
            return []
        count = self._child_count(ptr)
        if count <= 0:
            return []
        children: list[tuple[int, int]] = []
        for item in range(1, min(count, _MAX_ACCESSIBLE_NODES) + 1):
            child = self._acc_child(ptr, item)
            if child:
                children.append((child, _CHILDID_SELF))
            else:
                children.append((ptr, item))
        return children

    def _child_count(self, ptr: int) -> int:
        func = self._method(ptr, 8, ctypes.c_long, ctypes.POINTER(ctypes.c_long))
        count = ctypes.c_long()
        hr = func(ptr, ctypes.byref(count))
        return int(count.value) if _succeeded(hr) else 0

    def _acc_child(self, ptr: int, child_id: int) -> int:
        func = self._method(
            ptr,
            9,
            ctypes.c_long,
            _Variant,
            ctypes.POINTER(ctypes.c_void_p),
        )
        dispatch = ctypes.c_void_p()
        hr = func(ptr, _variant_i4(child_id), ctypes.byref(dispatch))
        if not _succeeded(hr) or not dispatch.value:
            return 0

        accessible = self._query_iaccessible(dispatch.value)
        self._release(dispatch.value)
        return accessible

    def _query_iaccessible(self, ptr: int) -> int:
        func = self._method(
            ptr,
            0,
            ctypes.c_long,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        accessible = ctypes.c_void_p()
        hr = func(ptr, ctypes.byref(self._iid_iaccessible), ctypes.byref(accessible))
        if not _succeeded(hr) or not accessible.value:
            return 0
        return int(accessible.value)

    def _acc_name(self, ptr: int, child_id: int) -> str:
        func = self._method(
            ptr,
            10,
            ctypes.c_long,
            _Variant,
            ctypes.POINTER(ctypes.c_void_p),
        )
        bstr = ctypes.c_void_p()
        hr = func(ptr, _variant_i4(child_id), ctypes.byref(bstr))
        if not _succeeded(hr) or not bstr.value:
            return ""
        try:
            return ctypes.wstring_at(bstr.value)
        finally:
            self._oleaut32.SysFreeString(bstr)

    def _acc_role(self, ptr: int, child_id: int) -> int:
        func = self._method(
            ptr,
            13,
            ctypes.c_long,
            _Variant,
            ctypes.POINTER(_Variant),
        )
        role = _Variant()
        hr = func(ptr, _variant_i4(child_id), ctypes.byref(role))
        try:
            if not _succeeded(hr):
                return 0
            if role.vt == _VT_I4:
                return int(role.lVal)
            return 0
        finally:
            self._oleaut32.VariantClear(ctypes.byref(role))

    def _release(self, ptr: int) -> None:
        if not ptr:
            return
        try:
            func = self._method(ptr, 2, ctypes.c_ulong)
            func(ptr)
        except Exception:
            pass

    def _method(self, ptr: int, index: int, restype: object, *argtypes: object) -> object:
        vtable = ctypes.cast(
            ptr,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class WindowsPlatform(BasePlatform):
    """Codex platform implementation for Windows."""

    def __init__(self) -> None:
        self._title_probe: _MsaaTitleProbe | None = None
        try:
            self._title_probe = _MsaaTitleProbe()
        except Exception:
            self._title_probe = None

    def get_codex_data_dir(self) -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Codex"
        return Path.home() / ".codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        return self._detect_latest_jsonl_by_mtime(sessions_root)

    def supports_active_title_polling(self) -> bool:
        return self._title_probe is not None

    def get_active_conversation_title(self) -> str | None:
        if self._title_probe is None:
            return None
        hwnd = self._find_codex_window()
        if hwnd is None:
            return None
        return self._title_probe.conversation_title(hwnd)

    def build_active_title_command(self, poll_ms: int) -> list[str] | None:
        del poll_ms
        return None

    def _find_codex_window(self) -> int | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        candidates: list[tuple[int, int, int]] = []

        def callback(hwnd: int, _: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            title = self._window_text(hwnd)
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 300 or height < 200:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self._process_name(kernel32, int(pid.value or 0))
            process_lower = process.lower()
            title_lower = title.lower()
            if process_lower != "codex.exe" and not (
                process_lower.startswith("codex") and title_lower == "codex"
            ):
                return True
            score = 0
            if process_lower == "codex.exe":
                score += 100
            if title_lower == "codex":
                score += 40
            elif "codex" in title_lower:
                score += 25
            candidates.append((score, width * height, int(hwnd)))
            return True

        try:
            user32.EnumWindows(enum_proc_type(callback), 0)
        except Exception:
            return None
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]

    @staticmethod
    def _window_text(hwnd: int) -> str:
        user32 = ctypes.windll.user32
        length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    @staticmethod
    def _process_name(kernel32: object, pid: int) -> str:
        if not pid:
            return ""
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            )
            return Path(buffer.value).name if ok else ""
        finally:
            kernel32.CloseHandle(handle)
