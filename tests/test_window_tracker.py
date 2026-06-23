"""Unit tests for Windows tracker geometry fallback helpers."""

from __future__ import annotations

import time
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from codex_usage_hud.platforms import windows_tracker as wt
from codex_usage_hud.platforms.windows_tracker import CodexWindowTracker, PhysicalRect


class CodexWindowTrackerGeometryTests(unittest.TestCase):
    def test_geometry_fallback_builds_title_and_input_landmarks(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)

        landmarks = CodexWindowTracker.geometry_fallback(rect)

        self.assertEqual(landmarks.source, "geometry")
        self.assertEqual(landmarks.title_bar.as_xywh(), (240, 0, 990, 45))
        self.assertEqual(landmarks.input_box.as_xywh(), (538, 648, 347, 56))

    def test_input_dock_coordinates_touch_input_top_edge(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)
        landmarks = CodexWindowTracker.geometry_fallback(rect)

        dock = CodexWindowTracker.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target="input",
            hud_height=32,
        )

        self.assertEqual(dock, (538, 616, 347))

    def test_title_dock_coordinates_stay_inside_title_bar(self) -> None:
        rect = PhysicalRect(left=240, top=0, right=1230, bottom=740)
        landmarks = CodexWindowTracker.geometry_fallback(rect)

        x, y, width = CodexWindowTracker.dock_coordinates_from_landmarks(
            landmarks.title_bar,
            landmarks.input_box,
            target="title",
            hud_height=36,
        )

        self.assertGreaterEqual(x, landmarks.title_bar.left)
        self.assertGreaterEqual(y, landmarks.title_bar.top)
        self.assertLessEqual(y + 36, landmarks.title_bar.bottom)
        self.assertGreaterEqual(width, 320)
        self.assertLessEqual(x + width, landmarks.title_bar.right)

    def test_uia_input_scoring_rejects_mid_window_scroll_content(self) -> None:
        window = PhysicalRect(left=230, top=126, right=1482, bottom=872)
        node = wt._UiNode(
            rect=PhysicalRect(left=652, top=551, right=1389, bottom=573),
            control_type=50004,
            name="message",
            automation_id="",
            class_name="",
            offscreen=False,
        )

        self.assertEqual(wt._UiaProbe._score_input_box(node, window), 0)

    def test_disabled_uia_landmarks_stay_geometry_only(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        rect = PhysicalRect(left=230, top=126, right=1482, bottom=872)
        tracker._landmark_cache = wt._Landmarks(
            title_bar=PhysicalRect(left=632, top=162, right=1302, bottom=208),
            input_box=PhysicalRect(left=650, top=520, right=1320, bottom=576),
            source="uia",
        )
        tracker._landmark_cache_hwnd = 123
        tracker._landmark_cache_window_rect = rect
        tracker._landmark_cache_at = 0.0

        def fail_schedule(_: int, __: PhysicalRect) -> None:
            raise AssertionError("UIA refresh should stay disabled")

        tracker._schedule_uia_refresh = fail_schedule  # type: ignore[method-assign]

        landmarks = tracker._landmarks(123, rect)

        self.assertEqual(landmarks.source, "geometry")
        self.assertEqual(landmarks.input_box.bottom, 836)


class CodexWindowTrackerSelectionTests(unittest.TestCase):
    def test_browser_title_with_codex_is_not_codex_candidate(self) -> None:
        self.assertFalse(
            CodexWindowTracker._looks_like_codex(
                "Codex documentation - Chrome",
                "Chrome_WidgetWin_1",
                "chrome.exe",
            )
        )
        self.assertFalse(
            CodexWindowTracker._is_stable_candidate(
                wt._WindowCandidate(
                    hwnd=101,
                    title="Codex documentation - Chrome",
                    class_name="Chrome_WidgetWin_1",
                    process="chrome.exe",
                    rect=PhysicalRect(left=40, top=20, right=1320, bottom=840),
                    visible=True,
                    minimized=False,
                    cloaked=False,
                )
            )
        )

    def test_cached_hidden_window_does_not_block_visible_codex_window(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        hidden = wt._WindowCandidate(
            hwnd=101,
            title="",
            class_name="Chrome_WidgetWin_0",
            process="Codex.exe",
            rect=PhysicalRect(left=40, top=20, right=980, bottom=720),
            visible=False,
            minimized=False,
            cloaked=False,
        )
        visible = wt._WindowCandidate(
            hwnd=202,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=1360, bottom=860),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._last_hwnd = hidden.hwnd
        tracker._last_hwnd_verified_at = time.monotonic()
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: hidden
            if hwnd == hidden.hwnd
            else visible
            if hwnd == visible.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [hidden.hwnd, visible.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window()

        self.assertEqual(hwnd, visible.hwnd)
        self.assertEqual(tracker._last_hwnd, visible.hwnd)
        self.assertGreater(tracker._last_hwnd_verified_at, 0.0)

    def test_empty_title_popup_does_not_replace_cached_minimized_main_window(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        minimized_main = wt._WindowCandidate(
            hwnd=101,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=None,
            visible=True,
            minimized=True,
            cloaked=False,
        )
        popup = wt._WindowCandidate(
            hwnd=202,
            title="",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=420, bottom=360),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._last_hwnd = minimized_main.hwnd
        tracker._last_hwnd_verified_at = time.monotonic()
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: minimized_main
            if hwnd == minimized_main.hwnd
            else popup
            if hwnd == popup.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [popup.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window()

        self.assertEqual(hwnd, minimized_main.hwnd)
        self.assertEqual(tracker._last_hwnd, minimized_main.hwnd)
        self.assertGreater(tracker._last_hwnd_verified_at, 0.0)

    def test_allow_inactive_finds_minimized_main_window_without_cache(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        minimized_main = wt._WindowCandidate(
            hwnd=101,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=None,
            visible=True,
            minimized=True,
            cloaked=False,
        )
        popup = wt._WindowCandidate(
            hwnd=202,
            title="",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=420, bottom=360),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: minimized_main
            if hwnd == minimized_main.hwnd
            else popup
            if hwnd == popup.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [minimized_main.hwnd, popup.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window(allow_inactive=True)

        self.assertEqual(hwnd, minimized_main.hwnd)

    def test_empty_title_chrome_widgetwin_zero_does_not_win_over_titled_main(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        wrong_surface = wt._WindowCandidate(
            hwnd=101,
            title="",
            class_name="Chrome_WidgetWin_0",
            process="Codex.exe",
            rect=PhysicalRect(left=40, top=20, right=1320, bottom=840),
            visible=True,
            minimized=False,
            cloaked=False,
        )
        main = wt._WindowCandidate(
            hwnd=202,
            title="Codex",
            class_name="Chrome_WidgetWin_1",
            process="Codex.exe",
            rect=PhysicalRect(left=120, top=60, right=1360, bottom=860),
            visible=True,
            minimized=False,
            cloaked=False,
        )

        tracker.user32 = SimpleNamespace(IsWindow=lambda hwnd: True)
        tracker._candidate_from_hwnd = (  # type: ignore[method-assign]
            lambda hwnd, verify_codex=False: wrong_surface
            if hwnd == wrong_surface.hwnd
            else main
            if hwnd == main.hwnd
            else None
        )
        tracker._findwindow_candidates = lambda: [wrong_surface.hwnd, main.hwnd]  # type: ignore[method-assign]
        tracker._enum_window_candidates = lambda: []  # type: ignore[method-assign]

        hwnd = tracker.find_main_window(allow_inactive=True)

        self.assertEqual(hwnd, main.hwnd)

    def test_is_active_keeps_codex_owned_popup_active(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker.enabled = True
        pid_map = {123: 41001, 999: 41001}

        def get_pid(hwnd: int, pid_ptr: object) -> int:
            hwnd_value = int(getattr(hwnd, "value", hwnd) or 0)
            pid_ptr._obj.value = pid_map.get(hwnd_value, 0)  # type: ignore[attr-defined]
            return 1

        tracker.user32 = SimpleNamespace(
            IsIconic=lambda hwnd: False,
            IsWindowVisible=lambda hwnd: True,
            GetForegroundWindow=lambda: 999,
            GetWindowThreadProcessId=get_pid,
        )
        tracker._is_cloaked = lambda hwnd: False  # type: ignore[method-assign]
        tracker._process_name = lambda pid: "Codex.exe"  # type: ignore[method-assign]

        self.assertTrue(tracker.is_active(123, {456}))

    def test_is_active_goes_inactive_for_other_process_foreground(self) -> None:
        tracker = CodexWindowTracker(enable_uia=False)
        tracker.enabled = True
        pid_map = {123: 41001, 999: 52002}

        def get_pid(hwnd: int, pid_ptr: object) -> int:
            hwnd_value = int(getattr(hwnd, "value", hwnd) or 0)
            pid_ptr._obj.value = pid_map.get(hwnd_value, 0)  # type: ignore[attr-defined]
            return 1

        tracker.user32 = SimpleNamespace(
            IsIconic=lambda hwnd: False,
            IsWindowVisible=lambda hwnd: True,
            GetForegroundWindow=lambda: 999,
            GetWindowThreadProcessId=get_pid,
        )
        tracker._is_cloaked = lambda hwnd: False  # type: ignore[method-assign]
        tracker._process_name = lambda pid: "Explorer.exe"  # type: ignore[method-assign]

        self.assertFalse(tracker.is_active(123, {456}))


class CodexWindowTrackerActivationTests(unittest.TestCase):
    @staticmethod
    def _value(raw: object) -> int:
        return int(getattr(raw, "value", raw) or 0)

    def test_activate_window_restores_and_attaches_foreground_threads(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        calls: list[tuple[str, int, int, bool] | tuple[str, int, int] | tuple[str, int]] = []

        def get_window_thread_process_id(hwnd: object, _: object) -> int:
            hwnd_value = self._value(hwnd)
            if hwnd_value == 700:
                return 701
            if hwnd_value == 123:
                return 1230
            return 0

        def attach_thread_input(src: object, dst: object, attach: bool) -> int:
            calls.append(("attach", self._value(src), self._value(dst), bool(attach)))
            return 1

        tracker.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 500)
        tracker.user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 700,
            GetWindowThreadProcessId=get_window_thread_process_id,
            AttachThreadInput=attach_thread_input,
            ShowWindow=lambda hwnd, cmd: calls.append(("show", self._value(hwnd), int(cmd))) or 1,
            BringWindowToTop=lambda hwnd: calls.append(("top", self._value(hwnd))) or 1,
            SetActiveWindow=lambda hwnd: calls.append(("active", self._value(hwnd))) or hwnd,
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", self._value(hwnd))) or 1,
            SetFocus=lambda hwnd: calls.append(("focus", self._value(hwnd))) or hwnd,
        )

        tracker._activate_window(123)

        self.assertEqual(
            calls,
            [
                ("attach", 500, 701, True),
                ("attach", 500, 1230, True),
                ("show", 123, 9),
                ("top", 123),
                ("active", 123),
                ("foreground", 123),
                ("focus", 123),
                ("attach", 500, 1230, False),
                ("attach", 500, 701, False),
            ],
        )

    def test_activate_window_skips_redundant_thread_attach(self) -> None:
        tracker = object.__new__(CodexWindowTracker)
        calls: list[tuple[str, int, int, bool] | tuple[str, int, int] | tuple[str, int]] = []

        tracker.kernel32 = SimpleNamespace(GetCurrentThreadId=lambda: 500)
        tracker.user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 123,
            GetWindowThreadProcessId=lambda hwnd, _: 500,
            AttachThreadInput=lambda src, dst, attach: calls.append(
                ("attach", self._value(src), self._value(dst), bool(attach))
            )
            or 1,
            ShowWindow=lambda hwnd, cmd: calls.append(("show", self._value(hwnd), int(cmd))) or 1,
            BringWindowToTop=lambda hwnd: calls.append(("top", self._value(hwnd))) or 1,
            SetActiveWindow=lambda hwnd: calls.append(("active", self._value(hwnd))) or hwnd,
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", self._value(hwnd))) or 1,
            SetFocus=lambda hwnd: calls.append(("focus", self._value(hwnd))) or hwnd,
        )

        tracker._activate_window(123)

        self.assertEqual(
            calls,
            [
                ("show", 123, 9),
                ("top", 123),
                ("active", 123),
                ("foreground", 123),
                ("focus", 123),
            ],
        )


if __name__ == "__main__":
    unittest.main()
