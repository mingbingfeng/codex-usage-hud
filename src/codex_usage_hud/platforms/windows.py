"""Windows-specific Codex platform helpers."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import threading
import uuid

from .base import BasePlatform
from .cdp_probe import CodexCdpProbe

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

_OBJID_CLIENT = 0xFFFFFFFC
_CHILDID_SELF = 0
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_MULTITHREADED = 0x0
_CLSCTX_INPROC_SERVER = 0x1
_CLSCTX_LOCAL_SERVER = 0x4
_RPC_E_CHANGED_MODE = -2147417850
_S_OK = 0
_S_FALSE = 1
_E_NOINTERFACE = -2147467262
_VT_EMPTY = 0
_VT_I4 = 3
_VT_BSTR = 8
_VT_DISPATCH = 9
_VT_BOOL = 11
_ROLE_SYSTEM_STATICTEXT = 0x29
_ROLE_SYSTEM_TEXT = 0x2A
_ROLE_SYSTEM_PUSHBUTTON = 0x2B
_TEXT_ROLES = {_ROLE_SYSTEM_STATICTEXT, _ROLE_SYSTEM_TEXT}
_TITLE_ROLES = _TEXT_ROLES | {_ROLE_SYSTEM_PUSHBUTTON}
_IGNORED_TITLES = {
    "account",
    "back",
    "chat",
    "close",
    "codex",
    "conversation",
    "conversations",
    "help",
    "history",
    "maximize",
    "minimize",
    "new chat",
    "search",
    "settings",
    "sidebar",
    "对话",
    "对话操作",
    "插件",
    "技能",
    "搜索",
    "设置",
    "新对话",
    "自动化",
    "暂无聊天",
}
_MAX_ACCESSIBLE_NODES = 1600
_MAX_EVENT_ACCESSIBLE_NODES = 80
_MAX_UIA_TITLE_NODES = 900
_MAX_UIA_EVENT_TITLE_NODES = 120
_EVENT_OBJECT_FOCUS = 0x8005
_EVENT_OBJECT_SELECTION = 0x8006
_EVENT_OBJECT_SELECTIONADD = 0x8007
_EVENT_OBJECT_SELECTIONWITHIN = 0x8009
_EVENT_OBJECT_NAMECHANGE = 0x800C
_HOOK_EVENTS = (
    _EVENT_OBJECT_FOCUS,
    _EVENT_OBJECT_SELECTION,
    _EVENT_OBJECT_SELECTIONADD,
    _EVENT_OBJECT_SELECTIONWITHIN,
    _EVENT_OBJECT_NAMECHANGE,
)
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_PM_REMOVE = 0x0001
_QS_ALLINPUT = 0x04FF
_MWMO_INPUTAVAILABLE = 0x0004
_MWMO_ALERTABLE = 0x0002
_WH_MOUSE_LL = 14
_WM_LBUTTONUP = 0x0202
_TREE_SCOPE_ELEMENT = 0x1
_TREE_SCOPE_DESCENDANTS = 0x4
_TREE_SCOPE_SUBTREE = _TREE_SCOPE_ELEMENT | _TREE_SCOPE_DESCENDANTS
_UIA_AUTOMATION_FOCUS_CHANGED_EVENT_ID = 20005
_UIA_INVOKE_INVOKED_EVENT_ID = 20009
_UIA_SELECTION_ITEM_ELEMENT_SELECTED_EVENT_ID = 20012
_UIA_SELECTION_INVALIDATED_EVENT_ID = 20013
_UIA_TITLE_EVENTS = (
    _UIA_SELECTION_ITEM_ELEMENT_SELECTED_EVENT_ID,
    _UIA_SELECTION_INVALIDATED_EVENT_ID,
    _UIA_INVOKE_INVOKED_EVENT_ID,
    _UIA_AUTOMATION_FOCUS_CHANGED_EVENT_ID,
)
_UIA_BUTTON_CONTROL_TYPE_ID = 50000
_UIA_IMAGE_CONTROL_TYPE_ID = 50006
_UIA_LIST_ITEM_CONTROL_TYPE_ID = 50007
_UIA_MENU_ITEM_CONTROL_TYPE_ID = 50011
_UIA_TAB_ITEM_CONTROL_TYPE_ID = 50019
_UIA_TEXT_CONTROL_TYPE_ID = 50020
_UIA_TREE_ITEM_CONTROL_TYPE_ID = 50024
_UIA_CUSTOM_CONTROL_TYPE_ID = 50025
_UIA_GROUP_CONTROL_TYPE_ID = 50026
_UIA_DATA_ITEM_CONTROL_TYPE_ID = 50029
_UIA_SELECTIONITEM_IS_SELECTED_PROPERTY_ID = 30079
_TITLE_CONTROL_TYPES = {
    _UIA_BUTTON_CONTROL_TYPE_ID,
    _UIA_LIST_ITEM_CONTROL_TYPE_ID,
    _UIA_MENU_ITEM_CONTROL_TYPE_ID,
    _UIA_TAB_ITEM_CONTROL_TYPE_ID,
    _UIA_TEXT_CONTROL_TYPE_ID,
    _UIA_TREE_ITEM_CONTROL_TYPE_ID,
    _UIA_CUSTOM_CONTROL_TYPE_ID,
    _UIA_GROUP_CONTROL_TYPE_ID,
    _UIA_DATA_ITEM_CONTROL_TYPE_ID,
}
_TITLE_CONTAINER_CONTROL_TYPES = {
    _UIA_LIST_ITEM_CONTROL_TYPE_ID,
    _UIA_TREE_ITEM_CONTROL_TYPE_ID,
    _UIA_DATA_ITEM_CONTROL_TYPE_ID,
}
_SIDEBAR_MAX_WIDTH = 340
_MAIN_TITLE_MIN_LEFT_OFFSET = 220
MOUSE_HOOK_ENV = "CODEX_USAGE_HUD_MOUSE_HOOK"


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
        ("boolVal", ctypes.c_short),
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


class _MsllHookStruct(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
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


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _plausible_conversation_title(value: str) -> bool:
    text = _clean_title(value)
    if len(text) < 3:
        return False
    lower = text.lower()
    if lower in _IGNORED_TITLES:
        return False
    return True


@dataclass(frozen=True)
class _UiaTitleNode:
    name: str
    control_type: int
    selected: bool
    offscreen: bool
    rect: tuple[int, int, int, int] | None


def _same_guid(left: _GUID, right: _GUID) -> bool:
    return bytes(ctypes.string_at(ctypes.byref(left), ctypes.sizeof(_GUID))) == bytes(
        ctypes.string_at(ctypes.byref(right), ctypes.sizeof(_GUID))
    )


_IID_IUNKNOWN = _GUID.from_string("{00000000-0000-0000-c000-000000000046}")
_IID_IUIAUTOMATION_EVENT_HANDLER = _GUID.from_string(
    "{146c3c17-f12e-4e22-8c27-f894b9b79c69}"
)

_UiaQueryInterfaceProc = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.POINTER(_GUID),
    ctypes.POINTER(ctypes.c_void_p),
)
_UiaAddRefProc = _WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaReleaseProc = _WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_UiaHandleEventProc = _WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
)


class _UiaAutomationEventHandlerVTable(ctypes.Structure):
    _fields_ = [
        ("QueryInterface", _UiaQueryInterfaceProc),
        ("AddRef", _UiaAddRefProc),
        ("Release", _UiaReleaseProc),
        ("HandleAutomationEvent", _UiaHandleEventProc),
    ]


class _UiaAutomationEventHandlerObject(ctypes.Structure):
    _fields_ = [
        ("lpVtbl", ctypes.POINTER(_UiaAutomationEventHandlerVTable)),
    ]


class _UiaAutomationEventHandler:
    """Tiny Python COM object implementing IUIAutomationEventHandler."""

    def __init__(self, callback: Callable[[int, int], None]) -> None:
        self.callback = callback
        self._ref_count = 1
        self._query_interface = _UiaQueryInterfaceProc(self._query_interface_impl)
        self._add_ref = _UiaAddRefProc(self._add_ref_impl)
        self._release = _UiaReleaseProc(self._release_impl)
        self._handle_event = _UiaHandleEventProc(self._handle_event_impl)
        self._vtable = _UiaAutomationEventHandlerVTable(
            self._query_interface,
            self._add_ref,
            self._release,
            self._handle_event,
        )
        self._object = _UiaAutomationEventHandlerObject()
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
            _IID_IUIAUTOMATION_EVENT_HANDLER,
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

    def _handle_event_impl(self, _this: int, sender: int, event_id: int) -> int:
        try:
            self.callback(int(sender or 0), int(event_id))
        except Exception:
            pass
        return _S_OK


class _UiaTitleProbe:
    """Minimal IUIAutomation wrapper for Codex conversation title events."""

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
        self._oleaut32.VariantClear.argtypes = [ctypes.POINTER(_Variant)]
        self._oleaut32.VariantClear.restype = ctypes.c_long

    def conversation_title(self, hwnd: int) -> str | None:
        automation = self._automation_for_thread()
        if not automation:
            return None
        root = self._element_from_handle(automation, hwnd)
        if not root:
            return None
        walker = self._raw_view_walker(automation) or self._control_view_walker(automation)
        if not walker:
            self._release(root)
            return None
        try:
            return self._title_from_subtree(
                root,
                walker,
                _MAX_UIA_TITLE_NODES,
                window_rect=self._window_rect(hwnd),
                main_title_only=True,
            )
        finally:
            self._release(walker)
            self._release(root)

    def title_from_event_element(self, element: int) -> str | None:
        if not element:
            return None
        automation = self._automation_for_thread()
        if not automation:
            return None
        walker = self._raw_view_walker(automation) or self._control_view_walker(automation)
        if not walker:
            return None
        try:
            return self.title_from_element(element, walker)
        finally:
            self._release(walker)

    def title_from_point(self, x: int, y: int) -> str | None:
        automation = self._automation_for_thread()
        if not automation:
            return None
        element = self._element_from_point(automation, x, y)
        if not element:
            return None
        walker = self._raw_view_walker(automation) or self._control_view_walker(automation)
        if not walker:
            self._release(element)
            return None
        try:
            return self.title_from_element(element, walker)
        finally:
            self._release(walker)
            self._release(element)

    def title_from_element(self, element: int, walker: int) -> str | None:
        container, release_container = self._nearest_title_container(element, walker)
        if container:
            try:
                title = self._title_from_subtree(
                    container,
                    walker,
                    _MAX_UIA_EVENT_TITLE_NODES,
                )
                if title:
                    return title
            finally:
                if release_container:
                    self._release(container)

        title = self._title_from_subtree(
            element,
            walker,
            _MAX_UIA_EVENT_TITLE_NODES,
        )
        if title:
            return title
        return None

    def _nearest_title_container(
        self,
        element: int,
        walker: int,
    ) -> tuple[int, bool]:
        current = element
        release_current = False
        for _ in range(8):
            node = self._node(current)
            if node is not None and node.control_type in _TITLE_CONTAINER_CONTROL_TYPES:
                return current, release_current

            parent = self._walker_parent(walker, current)
            if release_current:
                self._release(current)
            if not parent:
                return 0, False
            current = parent
            release_current = True

        if release_current:
            self._release(current)
        return 0, False

    def add_automation_event_handler(
        self,
        automation: int,
        event_id: int,
        element: int,
        handler: int,
    ) -> bool:
        func = self._method(
            automation,
            32,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        hr = int(
            func(
                automation,
                int(event_id),
                ctypes.c_void_p(element),
                _TREE_SCOPE_SUBTREE,
                None,
                ctypes.c_void_p(handler),
            )
        )
        return hr >= 0

    def remove_automation_event_handler(
        self,
        automation: int,
        event_id: int,
        element: int,
        handler: int,
    ) -> None:
        func = self._method(
            automation,
            33,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        try:
            func(
                automation,
                int(event_id),
                ctypes.c_void_p(element),
                ctypes.c_void_p(handler),
            )
        except Exception:
            pass

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
        hr = int(self._ole32.CoInitializeEx(None, _COINIT_MULTITHREADED))
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

    def _element_from_point(self, automation: int, x: int, y: int) -> int:
        func = self._method(
            automation,
            7,
            ctypes.c_long,
            wintypes.POINT,
            ctypes.POINTER(ctypes.c_void_p),
        )
        element = ctypes.c_void_p()
        hr = int(func(automation, wintypes.POINT(int(x), int(y)), ctypes.byref(element)))
        if hr < 0 or not element.value:
            return 0
        return int(element.value)

    def _focused_element(self, automation: int) -> int:
        func = self._method(
            automation,
            8,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
        )
        element = ctypes.c_void_p()
        hr = int(func(automation, ctypes.byref(element)))
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

    def _title_from_subtree(
        self,
        root: int,
        walker: int,
        max_nodes: int,
        *,
        window_rect: tuple[int, int, int, int] | None = None,
        main_title_only: bool = False,
    ) -> str | None:
        best: tuple[int, str] | None = None
        stack: list[tuple[int, bool, int]] = [(root, False, 0)]
        visited = 0
        while stack and visited < max_nodes:
            ptr, release_after, depth = stack.pop()
            visited += 1
            try:
                node = self._node(ptr)
                if node is not None:
                    score = self._score_title_node(
                        node,
                        depth,
                        window_rect,
                        main_title_only=main_title_only,
                    )
                    if score > 0:
                        if best is None or score > best[0]:
                            best = (score, node.name)
                children = self._children(walker, ptr, max_nodes - visited)
                for child in reversed(children):
                    stack.append((child, True, depth + 1))
            finally:
                if release_after:
                    self._release(ptr)
        return best[1] if best is not None else None

    def _node(self, element: int) -> _UiaTitleNode | None:
        try:
            return _UiaTitleNode(
                name=_clean_title(self._element_bstr(element, 23)),
                control_type=self._element_int(element, 21),
                selected=self._element_bool_property(
                    element,
                    _UIA_SELECTIONITEM_IS_SELECTED_PROPERTY_ID,
                ),
                offscreen=self._element_bool(element, 38),
                rect=self._element_rect(element),
            )
        except Exception:
            return None

    @staticmethod
    def _score_title_node(
        node: _UiaTitleNode,
        depth: int,
        window_rect: tuple[int, int, int, int] | None = None,
        *,
        main_title_only: bool = False,
    ) -> int:
        if node.offscreen or not _plausible_conversation_title(node.name):
            return 0
        score = max(1, 400 - (depth * 30))
        main_title_score = 0
        if (
            window_rect is not None
            and node.rect is not None
            and node.control_type == _UIA_TEXT_CONTROL_TYPE_ID
        ):
            left, top, right, bottom = node.rect
            win_left, win_top, win_right, _win_bottom = window_rect
            if (
                depth <= 20
                and left >= win_left + _MAIN_TITLE_MIN_LEFT_OFFSET
                and win_top + 35 <= top <= win_top + 145
                and bottom <= win_top + 185
                and right <= win_right - 40
            ):
                main_title_score = 15000
        if main_title_only and main_title_score <= 0:
            return 0
        score += main_title_score
        if node.selected:
            score += 10000
        if node.control_type in {
            _UIA_LIST_ITEM_CONTROL_TYPE_ID,
            _UIA_TREE_ITEM_CONTROL_TYPE_ID,
            _UIA_DATA_ITEM_CONTROL_TYPE_ID,
        }:
            score += 2500
        elif node.control_type in _TITLE_CONTROL_TYPES:
            score += 900
        if node.control_type == _UIA_TEXT_CONTROL_TYPE_ID:
            score += min(600, len(node.name) * 8)
        if node.control_type == _UIA_IMAGE_CONTROL_TYPE_ID:
            score -= 1200
        return score

    @staticmethod
    def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = wintypes.RECT()
        try:
            if not ctypes.windll.user32.GetWindowRect(
                wintypes.HWND(hwnd),
                ctypes.byref(rect),
            ):
                return None
        except Exception:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _children(self, walker: int, element: int, remaining: int) -> list[int]:
        if remaining <= 0:
            return []
        first_child = self._walker_first_child(walker, element)
        if not first_child:
            return []
        children: list[int] = []
        child = first_child
        while child and len(children) < remaining:
            children.append(child)
            child = self._walker_next_sibling(walker, child)
        return children

    def _walker_parent(self, walker: int, element: int) -> int:
        func = self._method(
            walker,
            3,
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        parent = ctypes.c_void_p()
        hr = int(func(walker, element, ctypes.byref(parent)))
        if hr < 0 or not parent.value:
            return 0
        return int(parent.value)

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

    def _element_rect(self, element: int) -> tuple[int, int, int, int] | None:
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
        if rect.left == rect.right or rect.top == rect.bottom:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _element_bool_property(self, element: int, property_id: int) -> bool:
        func = self._method(
            element,
            10,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.POINTER(_Variant),
        )
        value = _Variant()
        hr = int(func(element, int(property_id), ctypes.byref(value)))
        try:
            if hr < 0:
                return False
            if value.vt == _VT_BOOL:
                return bool(value.boolVal)
            if value.vt == _VT_I4:
                return bool(value.lVal)
            return False
        finally:
            self._oleaut32.VariantClear(ctypes.byref(value))

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

    def _release(self, ptr: int) -> None:
        if not ptr:
            return
        try:
            func = self._method(ptr, 2, ctypes.c_ulong)
            func(ptr)
        except Exception:
            pass

    @staticmethod
    def _method(ptr: int, index: int, restype: object, *argtypes: object) -> object:
        vtable = ctypes.cast(
            ptr,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        return _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


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
        self._oleacc.AccessibleObjectFromEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_long,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(_Variant),
        ]
        self._oleacc.AccessibleObjectFromEvent.restype = ctypes.c_long
        self._oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
        self._oleaut32.SysFreeString.restype = None
        self._oleaut32.VariantClear.argtypes = [ctypes.POINTER(_Variant)]
        self._oleaut32.VariantClear.restype = ctypes.c_long

    def conversation_title(self, hwnd: int) -> str | None:
        if not hwnd or not self._init_com_for_thread():
            return None

        accessible = ctypes.c_void_p()
        try:
            hr = self._oleacc.AccessibleObjectFromWindow(
                ctypes.c_void_p(hwnd),
                _OBJID_CLIENT,
                ctypes.byref(self._iid_iaccessible),
                ctypes.byref(accessible),
            )
        except OSError:
            return None
        if not _succeeded(hr) or not accessible.value:
            return None

        try:
            return self._title_from_tree(accessible.value)
        finally:
            self._release(accessible.value)

    def title_from_event(self, hwnd: int, object_id: int, child_id: int) -> str | None:
        if not hwnd or not self._init_com_for_thread():
            return None

        accessible = ctypes.c_void_p()
        child = _Variant()
        try:
            hr = self._oleacc.AccessibleObjectFromEvent(
                ctypes.c_void_p(hwnd),
                int(object_id),
                int(child_id),
                ctypes.byref(accessible),
                ctypes.byref(child),
            )
        except OSError:
            return None
        if not _succeeded(hr) or not accessible.value:
            return None

        target = int(accessible.value)
        target_child = _CHILDID_SELF
        extra_release = 0
        try:
            if child.vt == _VT_I4:
                target_child = int(child.lVal)
            elif child.vt == _VT_DISPATCH and child.pdispVal:
                queried = self._query_iaccessible(int(child.pdispVal))
                if queried:
                    extra_release = queried
                    target = queried
            return self._title_from_event_node(target, target_child)
        finally:
            if extra_release:
                self._release(extra_release)
            self._oleaut32.VariantClear(ctypes.byref(child))
            self._release(accessible.value)

    def _init_com_for_thread(self) -> bool:
        if getattr(self._local, "com_ready", False):
            return True
        try:
            hr = int(self._ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED))
        except OSError as exc:
            hr = int(getattr(exc, "winerror", 0) or getattr(exc, "hresult", 0) or -1)
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

    def _title_from_event_node(self, root: int, child_id: int) -> str | None:
        best_text = ""
        visited = 0
        stack: list[tuple[int, int, bool]] = [(root, child_id, False)]

        while stack and visited < _MAX_EVENT_ACCESSIBLE_NODES:
            ptr, current_child_id, release_after = stack.pop()
            visited += 1
            try:
                name = _clean_title(self._acc_name(ptr, current_child_id))
                role = self._acc_role(ptr, current_child_id)
                if role in _TITLE_ROLES and _plausible_conversation_title(name):
                    if len(name) >= len(best_text):
                        best_text = name
                children = self._children(ptr, current_child_id)
                for child_ptr, nested_child_id in reversed(children):
                    stack.append((child_ptr, nested_child_id, child_ptr != ptr))
            finally:
                if release_after:
                    self._release(ptr)

        return best_text or None

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
        return _WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


class WindowsPlatform(BasePlatform):
    """Codex platform implementation for Windows."""

    def __init__(self) -> None:
        self._last_observed_title = ""
        self._last_observed_session_id = ""
        self._cdp_probe: CodexCdpProbe | None = None
        self._uia_title_probe: _UiaTitleProbe | None = None
        self._title_probe: _MsaaTitleProbe | None = None
        self._native_active_title_suspended = False
        try:
            self._cdp_probe = CodexCdpProbe()
        except Exception:
            self._cdp_probe = None
        try:
            self._uia_title_probe = _UiaTitleProbe()
        except Exception:
            self._uia_title_probe = None
        try:
            self._title_probe = _MsaaTitleProbe()
        except Exception:
            self._title_probe = None

    def refresh_cdp_probe(self) -> None:
        try:
            self._cdp_probe = CodexCdpProbe()
        except Exception:
            self._cdp_probe = None

    def get_codex_data_dir(self) -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Codex"
        return Path.home() / ".codex"

    def detect_active_session(self, sessions_root: Path) -> Path | None:
        return self._detect_latest_jsonl_by_mtime(sessions_root)

    def supports_active_title_polling(self) -> bool:
        return (
            self._cdp_probe is not None
            or (
                not self._native_active_title_is_suspended()
                and (
                    self._uia_title_probe is not None
                    or self._title_probe is not None
                )
            )
        )

    def supports_active_title_events(self) -> bool:
        return (
            not self._native_active_title_is_suspended()
            and (self._uia_title_probe is not None or self._title_probe is not None)
        )

    def suspend_native_active_title(self, suspended: bool = True) -> None:
        self._native_active_title_suspended = bool(suspended)

    def resume_native_active_title(self) -> None:
        self.suspend_native_active_title(False)

    def _native_active_title_is_suspended(self) -> bool:
        return bool(getattr(self, "_native_active_title_suspended", False))

    def get_active_conversation_ref(self) -> tuple[str, str] | None:
        if self._cdp_probe is None:
            return None
        snapshot = self._cdp_probe.snapshot()
        if snapshot is None:
            return None
        session_id = snapshot.session_id.strip()
        title = snapshot.title.strip()
        if not session_id and not title:
            return None
        if title:
            self._last_observed_title = title
        if session_id:
            self._last_observed_session_id = session_id
        return session_id, title

    def get_active_app_error(self) -> str:
        if self._cdp_probe is None:
            return ""
        snapshot = self._cdp_probe.snapshot()
        if snapshot is None:
            return ""
        return str(getattr(snapshot, "app_error", "") or "").strip()

    def get_active_conversation_title(self) -> str | None:
        ref = self.get_active_conversation_ref()
        if ref is not None and ref[1]:
            return ref[1]
        if self._native_active_title_is_suspended():
            return None
        if self._uia_title_probe is None and self._title_probe is None:
            return None
        hwnd = self._find_codex_window()
        if hwnd is None:
            return None
        if self._uia_title_probe is not None:
            title = self._uia_title_probe.conversation_title(hwnd)
            if title:
                self._last_observed_title = title
                return title
        if self._title_probe is not None:
            title = self._title_probe.conversation_title(hwnd)
            if title:
                self._last_observed_title = title
                return title
        return None

    def watch_active_conversation_title(
        self,
        stop_event: threading.Event,
        on_title: Callable[[str], None],
    ) -> bool:
        if self._native_active_title_is_suspended():
            return False
        if self._uia_title_probe is not None:
            watcher = _UiaTitleWatcher(self, self._uia_title_probe, on_title)
            if watcher.run(stop_event):
                return True
        if self._title_probe is not None:
            watcher = _WinEventTitleWatcher(self, self._title_probe, on_title)
            return watcher.run(stop_event)
        return False

    def build_active_title_command(self, poll_ms: int) -> list[str] | None:
        del poll_ms
        return None

    def _find_codex_window(self) -> int | None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        enum_proc_type = _WINFUNCTYPE(
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
        user32.GetForegroundWindow.restype = wintypes.HWND
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
            if int(hwnd) == int(user32.GetForegroundWindow() or 0):
                score += 10000
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


class _UiaTitleWatcher:
    """Listen for structured UIA selection/invoke events from Codex's sidebar."""

    _mouse_callback_type = _WINFUNCTYPE(
        wintypes.LPARAM,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def __init__(
        self,
        platform: WindowsPlatform,
        probe: _UiaTitleProbe,
        on_title: Callable[[str], None],
    ) -> None:
        self.platform = platform
        self.probe = probe
        self.on_title = on_title
        self._last_emitted = ""
        self._handler: _UiaAutomationEventHandler | None = None
        self._registered_events: list[int] = []
        self._click_points: deque[tuple[int, int]] = deque(maxlen=8)
        self._mouse_callback = None
        self._mouse_hook = 0
        self._mouse_hook_enabled = _env_flag(MOUSE_HOOK_ENV, default=False)
        self._hwnd = 0

    def run(self, stop_event: threading.Event) -> bool:
        hwnd = self.platform._find_codex_window()
        if hwnd is None:
            return False
        self._hwnd = int(hwnd)
        automation = self.probe._automation_for_thread()
        if not automation:
            return False
        root = self.probe._element_from_handle(automation, hwnd)
        if not root:
            return False

        self._emit(self.probe.conversation_title(hwnd))
        self._handler = _UiaAutomationEventHandler(self._handle_event)
        handler_ptr = int(self._handler.ptr.value or 0)
        for event_id in _UIA_TITLE_EVENTS:
            if self.probe.add_automation_event_handler(
                automation,
                event_id,
                root,
                handler_ptr,
            ):
                self._registered_events.append(event_id)

        user32 = ctypes.windll.user32
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            self._mouse_callback_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.CallNextHookEx.restype = wintypes.LPARAM
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
        if self._mouse_hook_enabled:
            self._mouse_callback = self._mouse_callback_type(self._handle_mouse_event)
            self._mouse_hook = int(
                user32.SetWindowsHookExW(_WH_MOUSE_LL, self._mouse_callback, 0, 0) or 0
            )
        if not self._registered_events and not self._mouse_hook:
            self._handler = None
            self.probe._release(root)
            return False

        try:
            msg = wintypes.MSG()
            while not stop_event.is_set():
                user32.MsgWaitForMultipleObjectsEx(
                    0,
                    None,
                    250,
                    _QS_ALLINPUT,
                    _MWMO_INPUTAVAILABLE | _MWMO_ALERTABLE,
                )
                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, _PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                self._drain_click_points()
        finally:
            if self._mouse_hook:
                try:
                    user32.UnhookWindowsHookEx(ctypes.c_void_p(self._mouse_hook))
                except Exception:
                    pass
                self._mouse_hook = 0
            self._mouse_callback = None
            for event_id in self._registered_events:
                self.probe.remove_automation_event_handler(
                    automation,
                    event_id,
                    root,
                    handler_ptr,
                )
            self._registered_events.clear()
            self._handler = None
            self.probe._release(root)
        return True

    def _handle_mouse_event(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code >= 0 and int(w_param) == _WM_LBUTTONUP and l_param:
            try:
                data = ctypes.cast(
                    l_param,
                    ctypes.POINTER(_MsllHookStruct),
                ).contents
                self._click_points.append((int(data.pt.x), int(data.pt.y)))
            except Exception:
                pass
        return int(
            ctypes.windll.user32.CallNextHookEx(
                ctypes.c_void_p(self._mouse_hook),
                int(n_code),
                w_param,
                l_param,
            )
        )

    def _drain_click_points(self) -> None:
        while self._click_points:
            x, y = self._click_points.popleft()
            if not self._point_inside_codex_sidebar(x, y):
                continue
            self._emit(self.probe.title_from_point(x, y))

    def _point_inside_codex_sidebar(self, x: int, y: int) -> bool:
        hwnd = self._hwnd
        if not hwnd:
            return False
        rect = wintypes.RECT()
        try:
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return False
        except Exception:
            return False
        sidebar_right = min(int(rect.right), int(rect.left) + _SIDEBAR_MAX_WIDTH)
        return (
            int(rect.left) <= x <= sidebar_right
            and int(rect.top) + 35 <= y <= int(rect.bottom)
        )

    def _handle_event(self, sender: int, _event_id: int) -> None:
        title = self.probe.title_from_event_element(sender)
        if not title:
            hwnd = self.platform._find_codex_window()
            if hwnd is not None:
                title = self.probe.conversation_title(hwnd)
        self._emit(title)

    def _emit(self, title: str | None) -> None:
        text = _clean_title(title)
        if not _plausible_conversation_title(text):
            return
        if text == self._last_emitted:
            return
        self._last_emitted = text
        self.platform._last_observed_title = text
        self.on_title(text)


class _WinEventTitleWatcher:
    """Listen for Codex accessibility focus/selection events and emit thread titles."""

    _callback_type = _WINFUNCTYPE(
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
        platform: WindowsPlatform,
        probe: _MsaaTitleProbe,
        on_title: Callable[[str], None],
    ) -> None:
        self.platform = platform
        self.probe = probe
        self.on_title = on_title
        self._hooks: list[int] = []
        self._callback = None
        self._last_emitted = ""

    def run(self, stop_event: threading.Event) -> bool:
        self._emit(self.platform.get_active_conversation_title())
        user32 = ctypes.windll.user32
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            self._callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = ctypes.c_void_p
        user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
        user32.UnhookWinEvent.restype = wintypes.BOOL
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
        self._callback = self._callback_type(self._handle_event)
        for event_id in _HOOK_EVENTS:
            hook = user32.SetWinEventHook(
                event_id,
                event_id,
                0,
                self._callback,
                0,
                0,
                _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
            )
            if hook:
                self._hooks.append(int(hook))
        if not self._hooks:
            self._callback = None
            return False

        try:
            msg = wintypes.MSG()
            while not stop_event.is_set():
                user32.MsgWaitForMultipleObjectsEx(
                    0,
                    None,
                    250,
                    _QS_ALLINPUT,
                    _MWMO_INPUTAVAILABLE | _MWMO_ALERTABLE,
                )
                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, _PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hook in self._hooks:
                try:
                    user32.UnhookWinEvent(ctypes.c_void_p(hook))
                except Exception:
                    pass
            self._hooks.clear()
            self._callback = None
        return True

    def _handle_event(
        self,
        _hook: int,
        event_id: int,
        hwnd: int,
        object_id: int,
        child_id: int,
        _thread_id: int,
        _event_time: int,
    ) -> None:
        if not hwnd or not self._is_codex_hwnd(hwnd):
            return
        title = self.probe.title_from_event(hwnd, object_id, child_id)
        if not title and event_id in {
            _EVENT_OBJECT_SELECTION,
            _EVENT_OBJECT_SELECTIONADD,
            _EVENT_OBJECT_SELECTIONWITHIN,
            _EVENT_OBJECT_NAMECHANGE,
        }:
            title = self.platform.get_active_conversation_title()
        self._emit(title)

    def _emit(self, title: str | None) -> None:
        text = _clean_title(title)
        if not _plausible_conversation_title(text):
            return
        if text == self._last_emitted:
            return
        self._last_emitted = text
        self.on_title(text)

    def _is_codex_hwnd(self, hwnd: int) -> bool:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        root = int(user32.GetAncestor(hwnd, 2) or hwnd)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        process = self.platform._process_name(kernel32, int(pid.value or 0)).lower()
        return process == "codex.exe" or process.startswith("codex")
