"""Unit tests for Windows tracker geometry fallback helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
