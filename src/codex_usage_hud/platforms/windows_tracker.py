"""Windows Codex window tracking and UI Automation landmark probing.

This module intentionally uses only the Python standard library.  The Win32,
DWM, COM, and UI Automation calls are bound with ``ctypes`` so the HUD can dock
to the live Codex window without pywinauto, pyautogui, or comtypes.
"""

from __future__ import annotations

import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DockTarget = Literal["title", "input"]


STATUS_VISIBLE = "visible"
STATUS_NOT_FOUND = "not_found"
STATUS_HIDDEN = "hidden"
STATUS_MINIMIZED = "minimized"
STATUS_CLOAKED = "cloaked"
STATUS_UNSUPPORTED = "unsupported"

_COINIT_APARTMENTTHREADED = 0x2
_CLSCTX_INPROC_SERVER = 0x1
_CLSCTX_LOCAL_SERVER = 0x4
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_UIA_EDIT_CONTROL_TYPE_ID = 50004
_UIA_TITLE_BAR_CONTROL_TYPE_ID = 50037

_MAX_UIA_NODES = 900
_HWND_REVERIFY_SECONDS = 2.0
_WINDOW_SNAPSHOT_CACHE_SECONDS = 0.02
_UIA_REFRESH_SECONDS = 0.75
_UIA_SLOW_SECONDS = 0.05
_MIN_CODEX_WINDOW_WIDTH = 300
_MIN_CODEX_WINDOW_HEIGHT = 200

_TITLE_BAR_HEIGHT = 45
_TITLE_SAFE_LEFT_RATIO = 0.155
_TITLE_SAFE_RIGHT_RATIO = 0.14
_TITLE_SAFE_LEFT_MIN = 154
_TITLE_SAFE_RIGHT_MIN = 172
_TITLE_SAFE_MIN_WIDTH = 320

_INPUT_BOTTOM_MARGIN = 36
_INPUT_FALLBACK_HEIGHT = 56
_INPUT_SAFE_LEFT_RATIO = 0.30
_INPUT_SAFE_RIGHT_RATIO = 0.28
_INPUT_SAFE_LEFT_MIN = 298
_INPUT_SAFE_RIGHT_MIN = 345
_INPUT_SAFE_MIN_WIDTH = 260

_LOGGER_NAME = "codex_usage_hud.windows_tracker"
_LOG_ENV_PATH = "CODEX_USAGE_HUD_WINDOW_LOG"
_LOG_ENV_LEVEL = "CODEX_USAGE_HUD_WINDOW_LOG_LEVEL"
_logger = logging.getLogger(_LOGGER_NAME)
_logger.addHandler(logging.NullHandler())
_logging_configured = False


def window_tracker_log_path() -> Path:
    """Return the diagnostics log path used by the Windows tracker."""
    explicit = os.environ.get(_LOG_ENV_PATH)
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "codex-usage-hud" / "window_tracker.log"


def configure_window_tracker_logging() -> Path | None:
    """Configure lightweight rolling diagnostics for slow Windows tracking paths."""
    global _logging_configured
    if _logging_configured:
        return window_tracker_log_path()
    path = window_tracker_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        return None
    level_name = os.environ.get(_LOG_ENV_LEVEL, "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    _logger.handlers = [
        item for item in _logger.handlers if not isinstance(item, logging.NullHandler)
    ]
    _logger.addHandler(handler)
    _logger.setLevel(level)
    _logger.propagate = False
    _logging_configured = True
    return path


@dataclass(frozen=True)
class PhysicalRect:
    """A physical screen-space rectangle in device pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def is_empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def as_xywh(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    def intersects(self, other: "PhysicalRect") -> bool:
        return (
            self.left < other.right
            and self.right > other.left
            and self.top < other.bottom
            and self.bottom > other.top
        )

    def intersection(self, other: "PhysicalRect") -> "PhysicalRect | None":
        rect = PhysicalRect(
            max(self.left, other.left),
            max(self.top, other.top),
            min(self.right, other.right),
            min(self.bottom, other.bottom),
        )
        return None if rect.is_empty else rect

    @classmethod
    def from_win_rect(cls, rect: wintypes.RECT) -> "PhysicalRect":
        return cls(
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )


def _offset_rect(rect: PhysicalRect, dx: int, dy: int) -> PhysicalRect:
    return PhysicalRect(
        rect.left + dx,
        rect.top + dy,
        rect.right + dx,
        rect.bottom + dy,
    )


@dataclass(frozen=True)
class DockSnapshot:
    """Current Codex window and landmark state for HUD docking."""

    status: str
    hwnd: int = 0
    source: str = "none"
    window_rect: PhysicalRect | None = None
    title_bar: PhysicalRect | None = None
    input_box: PhysicalRect | None = None
    dock: tuple[int, int, int] | None = None
    reason: str = ""

    @property
    def visible(self) -> bool:
        return self.status == STATUS_VISIBLE and self.dock is not None


@dataclass(frozen=True)
class _Landmarks:
    title_bar: PhysicalRect
    input_box: PhysicalRect
    source: str
    nodes: int = 0
    duration_ms: float = 0.0


@dataclass(frozen=True)
class _UiNode:
    rect: PhysicalRect | None
    control_type: int
    name: str
    automation_id: str
    class_name: str
    offscreen: bool

    @property
    def search_text(self) -> str:
        return " ".join([self.name, self.automation_id, self.class_name]).lower()


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


class _UiaProbe:
    """Minimal IUIAutomation wrapper for Edit and TitleBar landmarks."""

    _clsid_cuiautomation = _GUID.from_string("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    _iid_iuiautomation = _GUID.from_string("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")

    def __init__(self) -> None:
        self._local = threading.local()
        self._ole32 = ctypes.windll.ole32
        self._oleaut32 = ctypes.windll.oleaut32
        self._configure_api()

    def _configure_api(self) -> None:
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long
        self._oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut32.SysFreeString.restype = None

    def find_landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks | None:
        automation = self._automation_for_thread()
        if not automation:
            return None

        root = self._element_from_handle(automation, hwnd)
        if not root:
            return None

        walker = self._control_view_walker(automation) or self._raw_view_walker(automation)
        if not walker:
            self._release(root)
            return None

        try:
            return self._scan_tree(root, walker, window_rect)
        finally:
            self._release(walker)
            self._release(root)

    def _automation_for_thread(self) -> int:
        automation = int(getattr(self._local, "automation", 0) or 0)
        if automation:
            return automation
        if not self._init_com_for_thread():
            return 0

        created = ctypes.c_void_p()
        hr = int(
            self._ole32.CoCreateInstance(
                ctypes.byref(self._clsid_cuiautomation),
                None,
                _CLSCTX_INPROC_SERVER | _CLSCTX_LOCAL_SERVER,
                ctypes.byref(self._iid_iuiautomation),
                ctypes.byref(created),
            )
        )
        if hr < 0 or not created.value:
            return 0
        self._local.automation = int(created.value)
        return int(created.value)

    def _init_com_for_thread(self) -> bool:
        if getattr(self._local, "com_ready", False):
            return True
        hr = int(self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
        if hr in {_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE} or hr >= 0:
            self._local.com_ready = True
            return True
        return False

    def _element_from_handle(self, automation: int, hwnd: int) -> int:
        func = self._method(
            automation,
            6,
            ctypes.c_long,
            wintypes.HWND,
            ctypes.POINTER(ctypes.c_void_p),
        )
        element = ctypes.c_void_p()
        hr = int(func(automation, wintypes.HWND(hwnd), ctypes.byref(element)))
        if hr < 0 or not element.value:
            return 0
        return int(element.value)

    def _control_view_walker(self, automation: int) -> int:
        return self._automation_walker(automation, 14)

    def _raw_view_walker(self, automation: int) -> int:
        return self._automation_walker(automation, 16)

    def _automation_walker(self, automation: int, index: int) -> int:
        func = self._method(
            automation,
            index,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )
        walker = ctypes.c_void_p()
        hr = int(func(automation, ctypes.byref(walker)))
        if hr < 0 or not walker.value:
            return 0
        return int(walker.value)

    def _scan_tree(
        self,
        root: int,
        walker: int,
        window_rect: PhysicalRect,
    ) -> _Landmarks | None:
        best_title: tuple[int, PhysicalRect] | None = None
        best_input: tuple[int, PhysicalRect] | None = None
        stack: list[tuple[int, bool]] = [(root, False)]
        visited = 0

        while stack and visited < _MAX_UIA_NODES:
            ptr, release_after = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    title_score = self._score_title_bar(node, window_rect)
                    if title_score > 0 and node.rect is not None:
                        if best_title is None or title_score > best_title[0]:
                            best_title = (title_score, node.rect)

                    input_score = self._score_input_box(node, window_rect)
                    if input_score > 0 and node.rect is not None:
                        if best_input is None or input_score > best_input[0]:
                            best_input = (input_score, node.rect)

                children = self._children(walker, ptr)
                for child in reversed(children):
                    stack.append((child, True))
            finally:
                if release_after:
                    self._release(ptr)

        if best_title is None and best_input is None:
            return None

        fallback = CodexWindowTracker.geometry_fallback(window_rect)
        source = "uia" if best_title is not None and best_input is not None else "uia+geometry"
        return _Landmarks(
            title_bar=best_title[1] if best_title is not None else fallback.title_bar,
            input_box=best_input[1] if best_input is not None else fallback.input_box,
            source=source,
            nodes=visited,
        )

    def _children(self, walker: int, element: int) -> list[int]:
        first_child = self._walker_first_child(walker, element)
        if not first_child:
            return []
        children: list[int] = []
        child = first_child
        while child and len(children) < _MAX_UIA_NODES:
            children.append(child)
            child = self._walker_next_sibling(walker, child)
        return children

    def _walker_first_child(self, walker: int, element: int) -> int:
        func = self._method(
            walker,
            4,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        child = ctypes.c_void_p()
        hr = int(func(walker, element, ctypes.byref(child)))
        if hr < 0 or not child.value:
            return 0
        return int(child.value)

    def _walker_next_sibling(self, walker: int, element: int) -> int:
        func = self._method(
            walker,
            6,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        sibling = ctypes.c_void_p()
        hr = int(func(walker, element, ctypes.byref(sibling)))
        if hr < 0 or not sibling.value:
            return 0
        return int(sibling.value)

    def _node(self, element: int) -> _UiNode | None:
        try:
            rect = self._element_rect(element)
            return _UiNode(
                rect=rect,
                control_type=self._element_int(element, 21),
                name=self._element_bstr(element, 23),
                automation_id=self._element_bstr(element, 29),
                class_name=self._element_bstr(element, 30),
                offscreen=self._element_bool(element, 38),
            )
        except Exception:
            return None

    def _element_rect(self, element: int) -> PhysicalRect | None:
        func = self._method(
            element,
            43,
            ctypes.c_long,
            ctypes.POINTER(wintypes.RECT),
        )
        rect = wintypes.RECT()
        hr = int(func(element, ctypes.byref(rect)))
        if hr < 0:
            return None
        physical = PhysicalRect.from_win_rect(rect)
        return None if physical.is_empty else physical

    def _element_int(self, element: int, index: int) -> int:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(ctypes.c_int))
        value = ctypes.c_int()
        hr = int(func(element, ctypes.byref(value)))
        return int(value.value) if hr >= 0 else 0

    def _element_bool(self, element: int, index: int) -> bool:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(wintypes.BOOL))
        value = wintypes.BOOL()
        hr = int(func(element, ctypes.byref(value)))
        return bool(value.value) if hr >= 0 else False

    def _element_bstr(self, element: int, index: int) -> str:
        func = self._method(element, index, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p))
        bstr = ctypes.c_void_p()
        hr = int(func(element, ctypes.byref(bstr)))
        if hr < 0 or not bstr.value:
            return ""
        try:
            return ctypes.wstring_at(bstr.value)
        finally:
            self._oleaut32.SysFreeString(bstr)

    @staticmethod
    def _score_title_bar(node: _UiNode, window_rect: PhysicalRect) -> int:
        rect = node.rect
        if rect is None or node.offscreen:
            return 0
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return 0
        if clipped.width < max(160, int(window_rect.width * 0.45)):
            return 0
        if clipped.top > window_rect.top + 90 or clipped.height > 120:
            return 0

        score = clipped.width
        if node.control_type == _UIA_TITLE_BAR_CONTROL_TYPE_ID:
            score += 5000
        if "title" in node.search_text or "caption" in node.search_text:
            score += 700
        if "codex" in node.search_text:
            score += 500
        score += max(0, 600 - abs(clipped.top - window_rect.top) * 10)
        return score

    @staticmethod
    def _score_input_box(node: _UiNode, window_rect: PhysicalRect) -> int:
        rect = node.rect
        if rect is None:
            return 0
        clipped = rect.intersection(window_rect)
        if clipped is None:
            return 0
        if clipped.width < max(240, int(window_rect.width * 0.20)) or clipped.height < 18:
            return 0
        if clipped.height > max(180, int(window_rect.height * 0.30)):
            return 0

        text = node.search_text
        cue_words = (
            "prompt",
            "message",
            "chat",
            "composer",
            "input",
            "textarea",
            "ask",
            "type",
            "edit",
            "输入",
            "消息",
            "编辑",
        )
        has_cue = any(word in text for word in cue_words)
        is_edit = node.control_type == _UIA_EDIT_CONTROL_TYPE_ID
        if not is_edit and not has_cue:
            return 0

        center_y = clipped.top + (clipped.height / 2)
        if center_y < window_rect.top + (window_rect.height * 0.70):
            return 0
        score = clipped.width
        if is_edit:
            score += 3000
        if has_cue:
            score += 1200
        if center_y > window_rect.top + (window_rect.height * 0.75):
            score += 3000
        if clipped.bottom > window_rect.bottom - 180:
            score += 1800
        if node.offscreen:
            score -= 2000
        if "search" in text or "find" in text:
            score -= 600
        return max(0, int(score))

    def _release(self, ptr: int) -> None:
        if not ptr:
            return
        try:
            func = self._method(ptr, 2, ctypes.c_ulong)
            func(ptr)
        except Exception:
            return

    @staticmethod
    def _method(ptr: int, index: int, restype: object, *argtypes: object) -> object:
        vtable = ctypes.cast(
            ptr,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class CodexWindowTracker:
    """Track the Codex main window and optional UIA docking landmarks on Windows."""

    def __init__(self, *, blocking_uia: bool = False, enable_uia: bool = True) -> None:
        self.enabled = False
        self.blocking_uia = blocking_uia
        self.enable_uia = enable_uia
        self.log_path = configure_window_tracker_logging()
        self.last_status = STATUS_UNSUPPORTED
        self.last_snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="not Windows")
        self._last_hwnd = 0
        self._last_hwnd_verified_at = 0.0
        self._window_snapshot_cache_at = 0.0
        self._window_snapshot_cache: DockSnapshot | None = None
        self._landmark_cache_at = 0.0
        self._landmark_cache: _Landmarks | None = None
        self._landmark_cache_hwnd = 0
        self._landmark_cache_window_rect: PhysicalRect | None = None
        self._last_uia_attempt_at = 0.0
        self._uia_lock = threading.Lock()
        self._uia_scan_running = False
        self._uia_probe: _UiaProbe | None = None

        if not sys.platform.startswith("win"):
            _logger.info("windows_tracker_unsupported platform=%s", sys.platform)
            return

        try:
            self.user32 = ctypes.windll.user32
            self.kernel32 = ctypes.windll.kernel32
            self.dwmapi = getattr(ctypes.windll, "dwmapi", None)
            self._configure_api()
            if self.enable_uia:
                self._uia_probe = _UiaProbe()
            self.enabled = True
            self.last_status = STATUS_NOT_FOUND
            self.last_snapshot = DockSnapshot(status=STATUS_NOT_FOUND)
            _logger.info(
                "windows_tracker_started log_path=%s uia_refresh_seconds=%.2f "
                "blocking_uia=%s enable_uia=%s",
                self.log_path,
                _UIA_REFRESH_SECONDS,
                self.blocking_uia,
                self.enable_uia,
            )
        except Exception as exc:
            self.last_snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason=str(exc))
            self.last_status = STATUS_UNSUPPORTED
            _logger.exception("windows_tracker_start_failed")

    def set_dpi_aware(self) -> None:
        """Ask Windows for physical pixels so Tk and Win32 coordinates agree."""
        if not self.enabled:
            return
        try:
            self.user32.SetProcessDPIAware()
        except Exception:
            return

    def find_main_window(self) -> int | None:
        """Return the best live Codex top-level HWND, if one exists."""
        if not self.enabled:
            return None

        now = time.monotonic()
        cached = self._candidate_from_hwnd(self._last_hwnd, verify_codex=True)
        if (
            cached is not None
            and self._is_visible_candidate(cached)
            and self._last_hwnd
            and self.user32.IsWindow(wintypes.HWND(self._last_hwnd))
            and now - self._last_hwnd_verified_at <= _HWND_REVERIFY_SECONDS
        ):
            self._last_hwnd_verified_at = now
            return cached.hwnd

        candidates: dict[int, _WindowCandidate] = {}
        if cached is not None:
            candidates[cached.hwnd] = cached
        for hwnd in self._findwindow_candidates():
            candidate = self._candidate_from_hwnd(hwnd, verify_codex=True)
            if candidate is not None:
                candidates[candidate.hwnd] = candidate

        for candidate in self._enum_window_candidates():
            candidates[candidate.hwnd] = candidate
        if not candidates:
            self._last_hwnd = 0
            return None

        # Prefer the real main surface over transient Codex popups so tray
        # menus and other temporary overlays do not steal the dock anchor.
        stable_candidates = [
            candidate
            for candidate in candidates.values()
            if self._is_stable_candidate(candidate)
        ]
        if not stable_candidates:
            if cached is not None and self.user32.IsWindow(wintypes.HWND(cached.hwnd)):
                self._last_hwnd_verified_at = now
                return cached.hwnd
            self._last_hwnd = 0
            return None

        best = sorted(stable_candidates, key=self._score_candidate, reverse=True)[0]
        self._last_hwnd = best.hwnd
        self._last_hwnd_verified_at = now
        _logger.info(
            "codex_hwnd_selected hwnd=%s process=%s title=%r class=%r visible=%s minimized=%s cloaked=%s",
            best.hwnd,
            best.process,
            best.title,
            best.class_name,
            best.visible,
            best.minimized,
            best.cloaked,
        )
        return best.hwnd

    @staticmethod
    def _is_visible_candidate(candidate: "_WindowCandidate") -> bool:
        return candidate.visible and not candidate.minimized and not candidate.cloaked

    @staticmethod
    def _is_stable_candidate(candidate: "_WindowCandidate") -> bool:
        if not CodexWindowTracker._is_visible_candidate(candidate):
            return False
        title = candidate.title.strip().lower()
        if title == "codex":
            return True
        rect = candidate.rect
        if rect is None:
            return False
        return rect.width * rect.height >= 300_000

    def get_window_rect(self) -> tuple[int, int, int, int] | None:
        """Return the live Codex main window rectangle as ``left, top, right, bottom``."""
        snapshot = self.get_window_snapshot()
        if snapshot.window_rect is None:
            return None
        rect = snapshot.window_rect
        return rect.left, rect.top, rect.right, rect.bottom

    def get_window_snapshot(self) -> DockSnapshot:
        """Return current Codex top-level window status without UIA tree scanning."""
        if not self.enabled:
            snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="Windows APIs unavailable")
            self._remember(snapshot)
            return snapshot
        now = time.monotonic()
        if (
            self._window_snapshot_cache is not None
            and now - self._window_snapshot_cache_at <= _WINDOW_SNAPSHOT_CACHE_SECONDS
        ):
            self._remember(self._window_snapshot_cache)
            return self._window_snapshot_cache

        hwnd = self.find_main_window()
        if hwnd is None:
            snapshot = DockSnapshot(status=STATUS_NOT_FOUND, reason="Codex HWND not found")
            self._remember(snapshot)
            self._window_snapshot_cache = snapshot
            self._window_snapshot_cache_at = now
            return snapshot
        snapshot = self._window_visibility_snapshot(hwnd)
        self._remember(snapshot)
        self._window_snapshot_cache = snapshot
        self._window_snapshot_cache_at = now
        return snapshot

    def get_dock_coordinates(
        self,
        target: DockTarget = "input",
        *,
        hud_height: int = 32,
    ) -> tuple[int, int, int] | None:
        """Return ideal ``(x, y, available_width)`` for a HUD dock, or ``None``.

        Inspect ``last_status`` or call ``get_dock_snapshot`` when the caller
        needs the explicit hide reason (minimized, hidden, virtual-desktop
        cloaked, not found, etc.).
        """
        snapshot = self.get_dock_snapshot(target=target, hud_height=hud_height)
        return snapshot.dock

    def get_dock_snapshot(
        self,
        target: DockTarget = "input",
        *,
        hud_height: int = 32,
    ) -> DockSnapshot:
        """Return window, landmark, status, and docking details for the HUD."""
        if not self.enabled:
            snapshot = DockSnapshot(status=STATUS_UNSUPPORTED, reason="Windows APIs unavailable")
            self._remember(snapshot)
            return snapshot

        base = self.get_window_snapshot()
        if base.status != STATUS_VISIBLE or base.window_rect is None:
            return base
        hwnd = base.hwnd

        landmarks = self._landmarks(hwnd, base.window_rect)
        dock = self.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target=target,
            hud_height=hud_height,
        )
        snapshot = DockSnapshot(
            status=STATUS_VISIBLE,
            hwnd=hwnd,
            source=landmarks.source,
            window_rect=base.window_rect,
            title_bar=landmarks.title_bar,
            input_box=landmarks.input_box,
            dock=dock,
        )
        self._remember(snapshot)
        return snapshot

    def is_active(self, hwnd: int, allowed_hwnds: set[int] | None = None) -> bool:
        """Return whether the tracked Codex window is still visibly present."""
        if not self.enabled or not hwnd:
            return True
        try:
            if self.user32.IsIconic(wintypes.HWND(hwnd)) or self._is_cloaked(hwnd):
                return False
            return bool(self.user32.IsWindowVisible(wintypes.HWND(hwnd)))
        except Exception:
            return True

    @classmethod
    def geometry_fallback(cls, window_rect: PhysicalRect) -> _Landmarks:
        """Return deterministic Electron-style title and input fallback landmarks."""
        title_bar = PhysicalRect(
            window_rect.left,
            window_rect.top,
            window_rect.right,
            min(window_rect.bottom, window_rect.top + _TITLE_BAR_HEIGHT),
        )

        left_margin = max(
            _INPUT_SAFE_LEFT_MIN,
            int(round(window_rect.width * _INPUT_SAFE_LEFT_RATIO)),
        )
        right_margin = max(
            _INPUT_SAFE_RIGHT_MIN,
            int(round(window_rect.width * _INPUT_SAFE_RIGHT_RATIO)),
        )
        left_margin = cls._fit_anchor_left(
            window_rect.width,
            left_margin,
            right_margin,
            _INPUT_SAFE_MIN_WIDTH,
        )
        input_left = window_rect.left + left_margin
        input_right = max(input_left + 1, window_rect.right - right_margin)
        input_top = max(
            title_bar.bottom,
            window_rect.bottom - _INPUT_BOTTOM_MARGIN - _INPUT_FALLBACK_HEIGHT,
        )
        input_bottom = min(window_rect.bottom, input_top + _INPUT_FALLBACK_HEIGHT)
        return _Landmarks(
            title_bar=title_bar,
            input_box=PhysicalRect(input_left, input_top, input_right, input_bottom),
            source="geometry",
        )

    @classmethod
    def dock_coordinates_from_landmarks(
        cls,
        title_bar: PhysicalRect,
        input_box: PhysicalRect,
        *,
        target: DockTarget,
        hud_height: int,
    ) -> tuple[int, int, int]:
        """Convert detected landmarks into a concrete HUD ``(x, y, width)``."""
        height = max(1, int(hud_height))
        if target == "title":
            left_margin = max(
                _TITLE_SAFE_LEFT_MIN,
                int(round(title_bar.width * _TITLE_SAFE_LEFT_RATIO)),
            )
            right_margin = max(
                _TITLE_SAFE_RIGHT_MIN,
                int(round(title_bar.width * _TITLE_SAFE_RIGHT_RATIO)),
            )
            left_margin = cls._fit_anchor_left(
                title_bar.width,
                left_margin,
                right_margin,
                _TITLE_SAFE_MIN_WIDTH,
            )
            x = title_bar.left + left_margin
            y = title_bar.top + max(0, (title_bar.height - height) // 2)
            width = max(1, title_bar.width - left_margin - right_margin)
            return x, y, width

        x = input_box.left
        y = max(0, input_box.top - height)
        return x, y, max(1, input_box.width)

    def _configure_api(self) -> None:
        enum_proc_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )
        self._enum_proc_type = enum_proc_type
        self.user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        if self.dwmapi is not None:
            self.dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            self.dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

    def _findwindow_candidates(self) -> list[int]:
        candidates: list[int] = []
        for class_name, title in [
            (None, "Codex"),
            ("Codex", None),
            ("Chrome_WidgetWin_1", None),
            ("Chrome_WidgetWin_0", None),
        ]:
            try:
                hwnd = int(self.user32.FindWindowW(class_name, title) or 0)
            except Exception:
                hwnd = 0
            if hwnd and hwnd not in candidates:
                candidates.append(hwnd)
        return candidates

    def _enum_window_candidates(self) -> list["_WindowCandidate"]:
        candidates: list[_WindowCandidate] = []

        def callback(hwnd: int, _: int) -> bool:
            candidate = self._candidate_from_hwnd(int(hwnd), verify_codex=True)
            if candidate is not None:
                candidates.append(candidate)
            return True

        try:
            self.user32.EnumWindows(self._enum_proc_type(callback), 0)
        except Exception:
            return []
        return candidates

    def _candidate_from_hwnd(
        self,
        hwnd: int,
        *,
        verify_codex: bool = False,
    ) -> "_WindowCandidate | None":
        if not hwnd:
            return None
        try:
            if not self.user32.IsWindow(wintypes.HWND(hwnd)):
                return None
            title = self._window_text(hwnd)
            class_name = self._class_name(hwnd)
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            process = self._process_name(int(pid.value or 0))
            if verify_codex and not self._looks_like_codex(title, class_name, process):
                return None
            rect = self._rect_for_hwnd(hwnd)
            if rect is None and not self.user32.IsIconic(wintypes.HWND(hwnd)):
                return None
            return _WindowCandidate(
                hwnd=hwnd,
                title=title,
                class_name=class_name,
                process=process,
                rect=rect,
                visible=bool(self.user32.IsWindowVisible(wintypes.HWND(hwnd))),
                minimized=bool(self.user32.IsIconic(wintypes.HWND(hwnd))),
                cloaked=self._is_cloaked(hwnd),
            )
        except Exception:
            return None

    def _window_visibility_snapshot(self, hwnd: int) -> DockSnapshot:
        if not self.user32.IsWindow(wintypes.HWND(hwnd)):
            return DockSnapshot(status=STATUS_NOT_FOUND, hwnd=hwnd, reason="HWND is stale")
        rect = self._rect_for_hwnd(hwnd)
        if self.user32.IsIconic(wintypes.HWND(hwnd)):
            return DockSnapshot(
                status=STATUS_MINIMIZED,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is minimized",
            )
        if not self.user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return DockSnapshot(
                status=STATUS_HIDDEN,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is hidden",
            )
        if self._is_cloaked(hwnd):
            return DockSnapshot(
                status=STATUS_CLOAKED,
                hwnd=hwnd,
                window_rect=rect,
                reason="Codex is cloaked by DWM, often because it is on another virtual desktop",
            )
        if rect is None:
            return DockSnapshot(status=STATUS_HIDDEN, hwnd=hwnd, reason="GetWindowRect failed")
        return DockSnapshot(status=STATUS_VISIBLE, hwnd=hwnd, window_rect=rect)

    def _rect_for_hwnd(self, hwnd: int) -> PhysicalRect | None:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        physical = PhysicalRect.from_win_rect(rect)
        if physical.width < _MIN_CODEX_WINDOW_WIDTH or physical.height < _MIN_CODEX_WINDOW_HEIGHT:
            return None
        return physical

    def _landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks:
        if not self.enable_uia:
            return self.geometry_fallback(window_rect)

        now = time.monotonic()
        with self._uia_lock:
            cached = self._landmark_cache
            cached_rect = self._landmark_cache_window_rect
            cached_hwnd = self._landmark_cache_hwnd
            cache_age = now - self._landmark_cache_at

        if cached is not None and cached_rect is not None and cached_hwnd == hwnd:
            if window_rect.width == cached_rect.width and window_rect.height == cached_rect.height:
                if cache_age >= _UIA_REFRESH_SECONDS:
                    self._schedule_uia_refresh(hwnd, window_rect)
                return self._translate_landmarks(cached, cached_rect, window_rect)
            if now - self._last_uia_attempt_at <= _UIA_REFRESH_SECONDS:
                return self.geometry_fallback(window_rect)

        if self.blocking_uia:
            return self._scan_uia_landmarks(hwnd, window_rect)

        self._schedule_uia_refresh(hwnd, window_rect)
        return self.geometry_fallback(window_rect)

    def _schedule_uia_refresh(self, hwnd: int, window_rect: PhysicalRect) -> None:
        if not self.enable_uia or self._uia_probe is None:
            return
        now = time.monotonic()
        with self._uia_lock:
            if self._uia_scan_running:
                return
            if now - self._last_uia_attempt_at < _UIA_REFRESH_SECONDS:
                return
            self._last_uia_attempt_at = now
            self._uia_scan_running = True

        _logger.debug(
            "uia_scan_scheduled hwnd=%s window=(%s,%s,%s,%s)",
            hwnd,
            window_rect.left,
            window_rect.top,
            window_rect.right,
            window_rect.bottom,
        )
        thread = threading.Thread(
            target=self._run_uia_refresh,
            args=(hwnd, window_rect),
            name="codex-hud-uia",
            daemon=True,
        )
        thread.start()

    def _run_uia_refresh(self, hwnd: int, window_rect: PhysicalRect) -> None:
        try:
            self._scan_uia_landmarks(hwnd, window_rect)
        finally:
            with self._uia_lock:
                self._uia_scan_running = False

    def _scan_uia_landmarks(self, hwnd: int, window_rect: PhysicalRect) -> _Landmarks:
        landmarks = None
        with self._uia_lock:
            self._last_uia_attempt_at = time.monotonic()
        if self._uia_probe is not None:
            started = time.perf_counter()
            try:
                landmarks = self._uia_probe.find_landmarks(hwnd, window_rect)
            except Exception as exc:
                _logger.exception("uia_scan_failed hwnd=%s error=%s", hwnd, exc)
                landmarks = None
            duration = time.perf_counter() - started
            if landmarks is not None:
                landmarks = _Landmarks(
                    title_bar=landmarks.title_bar,
                    input_box=landmarks.input_box,
                    source=landmarks.source,
                    nodes=landmarks.nodes,
                    duration_ms=duration * 1000,
                )
            if duration >= _UIA_SLOW_SECONDS:
                _logger.warning(
                    "uia_scan_slow hwnd=%s duration_ms=%.1f nodes=%s source=%s window=(%s,%s,%s,%s)",
                    hwnd,
                    duration * 1000,
                    landmarks.nodes if landmarks is not None else 0,
                    landmarks.source if landmarks is not None else "none",
                    window_rect.left,
                    window_rect.top,
                    window_rect.right,
                    window_rect.bottom,
                )
        if landmarks is None:
            landmarks = self.geometry_fallback(window_rect)
            _logger.info(
                "uia_fallback_geometry hwnd=%s window=(%s,%s,%s,%s)",
                hwnd,
                window_rect.left,
                window_rect.top,
                window_rect.right,
                window_rect.bottom,
            )
        elif landmarks.source != "uia":
            _logger.info(
                "uia_partial_fallback hwnd=%s source=%s nodes=%s duration_ms=%.1f",
                hwnd,
                landmarks.source,
                landmarks.nodes,
                landmarks.duration_ms,
            )

        with self._uia_lock:
            self._landmark_cache_at = time.monotonic()
            self._landmark_cache = landmarks
            self._landmark_cache_hwnd = hwnd
            self._landmark_cache_window_rect = window_rect
        return landmarks

    @staticmethod
    def _translate_landmarks(
        landmarks: _Landmarks,
        from_rect: PhysicalRect,
        to_rect: PhysicalRect,
    ) -> _Landmarks:
        if from_rect.left == to_rect.left and from_rect.top == to_rect.top:
            return landmarks
        dx = to_rect.left - from_rect.left
        dy = to_rect.top - from_rect.top
        return _Landmarks(
            title_bar=_offset_rect(landmarks.title_bar, dx, dy),
            input_box=_offset_rect(landmarks.input_box, dx, dy),
            source=landmarks.source,
            nodes=landmarks.nodes,
            duration_ms=landmarks.duration_ms,
        )

    def _window_text(self, hwnd: int) -> str:
        length = int(self.user32.GetWindowTextLengthW(wintypes.HWND(hwnd)) or 0)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
        return buffer.value

    def _class_name(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
        return buffer.value

    def _process_name(self, pid: int) -> str:
        if not pid:
            return ""
        handle = self.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(
                handle,
                0,
                buffer,
                ctypes.byref(size),
            )
            return Path(buffer.value).name if ok else ""
        finally:
            self.kernel32.CloseHandle(handle)

    def _is_cloaked(self, hwnd: int) -> bool:
        if self.dwmapi is None:
            return False
        value = wintypes.DWORD()
        try:
            hr = int(
                self.dwmapi.DwmGetWindowAttribute(
                    wintypes.HWND(hwnd),
                    _DWMWA_CLOAKED,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
            )
        except Exception:
            return False
        return hr >= 0 and bool(value.value)

    @staticmethod
    def _looks_like_codex(title: str, class_name: str, process: str) -> bool:
        title_lower = title.strip().lower()
        class_lower = class_name.strip().lower()
        process_lower = process.strip().lower()
        if process_lower == "codex.exe" or process_lower.startswith("codex"):
            return True
        if title_lower == "codex" or class_lower == "codex":
            return True
        return "codex" in class_lower and bool(title_lower)

    @staticmethod
    def _score_candidate(candidate: "_WindowCandidate") -> tuple[int, int]:
        title = candidate.title.strip().lower()
        class_name = candidate.class_name.strip().lower()
        process = candidate.process.strip().lower()
        score = 0
        if process == "codex.exe":
            score += 100
        elif process.startswith("codex"):
            score += 70
        if title == "codex":
            score += 45
        if "codex" in class_name:
            score += 20
        if candidate.visible:
            score += 10
        if not candidate.minimized:
            score += 5
        if candidate.cloaked:
            score -= 50
        area = candidate.rect.width * candidate.rect.height if candidate.rect else 0
        return score, area

    @staticmethod
    def _fit_anchor_left(
        window_width: int,
        left_margin: int,
        right_margin: int,
        min_width: int,
    ) -> int:
        if window_width - left_margin - right_margin >= min_width:
            return left_margin
        return max(8, min(left_margin, window_width - right_margin - min_width))

    def _remember(self, snapshot: DockSnapshot) -> None:
        previous = self.last_snapshot
        if previous.status != snapshot.status or previous.hwnd != snapshot.hwnd:
            _logger.info(
                "codex_window_status status=%s hwnd=%s source=%s reason=%r",
                snapshot.status,
                snapshot.hwnd or 0,
                snapshot.source,
                snapshot.reason,
            )
        self.last_snapshot = snapshot
        self.last_status = snapshot.status


@dataclass(frozen=True)
class _WindowCandidate:
    hwnd: int
    title: str
    class_name: str
    process: str
    rect: PhysicalRect | None
    visible: bool
    minimized: bool
    cloaked: bool


__all__ = [
    "CodexWindowTracker",
    "DockSnapshot",
    "PhysicalRect",
    "STATUS_CLOAKED",
    "STATUS_HIDDEN",
    "STATUS_MINIMIZED",
    "STATUS_NOT_FOUND",
    "STATUS_UNSUPPORTED",
    "STATUS_VISIBLE",
    "configure_window_tracker_logging",
    "window_tracker_log_path",
]
